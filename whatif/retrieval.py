"""Date-aware retrieval.

The whole point of a counterfactual branch is that agents only know what was knowable
at the fork date. Two keyless sources give us that honestly:

* Wikipedia revision history — we fetch the revision of each article that existed
  *on the cutoff date* (MediaWiki `rvstart`/`rvdir=older`). For anything after 2001-ish
  that is a real "as of" snapshot. For older topics the earliest revision already knows
  the ending, so we flag it and let the LLM leakage filter strip post-cutoff facts.
* GDELT DOC 2.0 — global news headlines with hard start/end datetimes (coverage from 2017).

Plus the present-day article text, which is what the *actual* baseline timeline is
extracted from, and any documents the user pastes (MiroFish-style seeds).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta

import httpx

from .config import Settings

log = logging.getLogger("whatif.retrieval")

try:  # optional, only needed to clean wikitext of historical revisions
    import mwparserfromhell  # type: ignore
except Exception:  # noqa: BLE001
    mwparserfromhell = None


@dataclass
class Doc:
    source: str                 # wikipedia_asof | wikipedia_latest | gdelt | user
    title: str
    text: str
    url: str = ""
    as_of: str = ""             # ISO timestamp the content reflects
    cutoff_ok: bool = True      # False = content postdates cutoff; leakage filter required
    note: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


_GDELT_LOCK: asyncio.Lock | None = None
_GDELT_LAST = 0.0


class Retriever:
    def __init__(self, settings: Settings):
        self.s = settings
        self.api = f"https://{settings.wiki_lang}.wikipedia.org/w/api.php"
        self._client: httpx.AsyncClient | None = None
        self.errors: list[str] = []      # human-readable failures from the last gather(), surfaced in the GUI

    def _fmt(self, e: Exception) -> str:
        if isinstance(e, httpx.HTTPStatusError):
            return f"HTTP {e.response.status_code} — {e.response.text[:160]}"
        return f"{type(e).__name__}: {e}"

    def _err(self, where: str, e: Exception | str):
        msg = str(e)
        if isinstance(e, httpx.HTTPStatusError):
            body = e.response.text[:160].replace("\n", " ")
            msg = f"HTTP {e.response.status_code} — {body}"
        line = f"{where}: {msg}"[:400]
        log.warning(line)
        if len(self.errors) < 20:
            self.errors.append(line)

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(40, connect=15),
                                             headers={"User-Agent": self.s.user_agent}, follow_redirects=True)
        return self._client

    async def aclose(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------ wikipedia
    async def wiki_search(self, query: str, limit: int = 8) -> list[str]:
        c = await self.client()
        r = await c.get(self.api, params={"action": "query", "list": "search", "srsearch": query,
                                          "srlimit": limit, "format": "json"})
        r.raise_for_status()
        return [h["title"] for h in r.json().get("query", {}).get("search", [])]

    async def wiki_exists(self, title: str) -> str | None:
        """Resolve redirects; return canonical title or None."""
        c = await self.client()
        r = await c.get(self.api, params={"action": "query", "titles": title, "redirects": 1, "format": "json"})
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for pid, p in pages.items():
            if int(pid) > 0:
                return p.get("title")
        return None

    async def wiki_revision_asof(self, title: str, cutoff: date) -> tuple[int | None, str, bool]:
        """(revid, timestamp, cutoff_ok). Picks the last revision at or before cutoff;
        falls back to the earliest revision if the article did not exist yet."""
        c = await self.client()
        ts = f"{cutoff.isoformat()}T23:59:59Z"
        base = {"action": "query", "prop": "revisions", "titles": title, "rvlimit": 1,
                "rvprop": "ids|timestamp", "redirects": 1, "format": "json"}
        r = await c.get(self.api, params={**base, "rvdir": "older", "rvstart": ts})
        r.raise_for_status()
        rev = _first_rev(r.json())
        if rev:
            return rev["revid"], rev["timestamp"], True
        r = await c.get(self.api, params={**base, "rvdir": "newer"})
        r.raise_for_status()
        rev = _first_rev(r.json())
        if rev:
            return rev["revid"], rev["timestamp"], False
        return None, "", False

    async def wiki_revision_text(self, revid: int) -> str:
        c = await self.client()
        r = await c.get(self.api, params={"action": "query", "prop": "revisions", "revids": revid,
                                          "rvprop": "content", "rvslots": "main", "format": "json"})
        r.raise_for_status()
        rev = _first_rev(r.json())
        if not rev:
            return ""
        wikitext = (rev.get("slots", {}).get("main", {}) or {}).get("*") or rev.get("*") or ""
        return clean_wikitext(wikitext)

    async def wiki_latest_text(self, title: str) -> tuple[str, str]:
        """Plain-text extract of the current article. Returns (text, url)."""
        c = await self.client()
        r = await c.get(self.api, params={"action": "query", "prop": "extracts|info", "explaintext": 1,
                                          "exsectionformat": "plain", "inprop": "url", "titles": title,
                                          "redirects": 1, "format": "json"})
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
        for p in pages.values():
            return p.get("extract", "") or "", p.get("fullurl", "")
        return "", ""

    async def wiki_docs(self, titles: list[str], cutoff: date, want_latest: bool = True,
                        event_titles: list[str] | None = None) -> list[Doc]:
        docs: list[Doc] = []
        sem = asyncio.Semaphore(4)

        async def one(title: str, latest_only: bool = False):
            async with sem:
                try:
                    canon = await self.wiki_exists(title)
                    if not canon:
                        log.info("no article for %r", title)
                        return
                    if latest_only:
                        txt, url = await self.wiki_latest_text(canon)
                        docs.append(Doc("wikipedia_latest", canon, txt[: self.s.max_chars_per_article * 3], url,
                                        datetime.utcnow().date().isoformat(), False,
                                        "event article (present-day); used only for the actual-history baseline"))
                        return
                    revid, ts, ok = await self.wiki_revision_asof(canon, cutoff)
                    latest_txt, latest_url = "", ""
                    if ok and revid:
                        txt = await self.wiki_revision_text(revid)
                        txt = txt[: self.s.max_chars_per_article]
                        url = f"https://{self.s.wiki_lang}.wikipedia.org/w/index.php?title={canon.replace(' ', '_')}&oldid={revid}"
                        docs.append(Doc("wikipedia_asof", canon, txt, url, ts, True, "", {"revid": revid}))
                    else:
                        # No revision existed on the cutoff date (topic predates Wikipedia, or article is newer):
                        # pass the present-day text through, flagged, so the leakage filter strips the hindsight.
                        latest_txt, latest_url = await self.wiki_latest_text(canon)
                        docs.append(Doc("wikipedia_asof", canon, latest_txt[: self.s.max_chars_per_article], latest_url,
                                        datetime.utcnow().date().isoformat(), False,
                                        f"no revision existed on {cutoff}; present-day text used — leakage filter applied"))
                    if want_latest:
                        if not latest_txt:
                            latest_txt, latest_url = await self.wiki_latest_text(canon)
                        docs.append(Doc("wikipedia_latest", canon, latest_txt[: self.s.max_chars_per_article * 2], latest_url,
                                        datetime.utcnow().date().isoformat(), False,
                                        "present-day article; used only for the actual-history baseline"))
                except Exception as e:  # noqa: BLE001
                    self._err(f"Wikipedia '{title}'", e)

        # Event articles: present-day only when building the baseline (they postdate the anchor); as-of the cutoff
        # for branches (a fork inside the crisis should know the crisis so far).
        await asyncio.gather(*(one(t) for t in titles),
                             *(one(t, latest_only=want_latest) for t in (event_titles or []) if t not in titles))
        return docs

    async def wiki_year_articles(self, patterns: list[str], years: list[int], max_chars: int = 14000) -> list[Doc]:
        """Present-day year articles ("2003 in the United States") — dense dated facts for the actual timeline only."""
        docs: list[Doc] = []
        sem = asyncio.Semaphore(4)

        async def one(pattern: str, year: int):
            title = pattern.replace("{year}", str(year))
            async with sem:
                try:
                    canon = await self.wiki_exists(title)
                    if not canon:
                        return
                    txt, url = await self.wiki_latest_text(canon)
                    if len(txt) > 500:
                        docs.append(Doc("wikipedia_year", canon, txt[:max_chars], url, datetime.utcnow().date().isoformat(), False,
                                        "year article (present-day); used only for the actual-history baseline", {"year": year}))
                except Exception as e:  # noqa: BLE001
                    log.info("year article failed for %r: %s", title, e)

        await asyncio.gather(*(one(p, y) for p in patterns[:2] for y in years))
        docs.sort(key=lambda d: (d.meta.get("year", 0), d.title))
        return docs

    # ------------------------------------------------------------ gdelt
    async def gdelt(self, query: str, start: date, end: date, max_records: int | None = None) -> list[Doc]:
        """Headlines between start and end (inclusive). GDELT DOC covers 2017-01-01 onward."""
        if end < date(2017, 1, 1):
            return []
        start = max(start, date(2017, 1, 1))
        if start > end:
            return []
        c = await self.client()
        q = query.strip()
        if " " in q and not q.startswith('"'):
            q = f'"{q}"'
        params = {"query": q, "mode": "artlist", "format": "json", "sort": "hybridrel",
                  "maxrecords": max_records or self.s.gdelt_max_records,
                  "startdatetime": start.strftime("%Y%m%d000000"), "enddatetime": end.strftime("%Y%m%d235959")}
        global _GDELT_LOCK, _GDELT_LAST
        if _GDELT_LOCK is None:
            _GDELT_LOCK = asyncio.Lock()
        if getattr(self, "_gdelt_blocked", False):
            return []
        try:
            arts = None
            for attempt in range(2):
                async with _GDELT_LOCK:  # GDELT allows one request per 5 seconds per IP
                    import time as _t
                    gap = 6.0 - (_t.monotonic() - _GDELT_LAST)
                    if gap > 0:
                        await asyncio.sleep(gap)
                    _GDELT_LAST = _t.monotonic()
                    r = await c.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params)
                if r.status_code == 200 and r.text.strip().startswith("{"):
                    arts = r.json().get("articles", [])
                    break
                if r.status_code == 429 and attempt == 0:
                    continue
                self._gdelt_blocked = True
                self._err("GDELT", "rate-limited from this IP (shared cloud egress, e.g. Colab) — headlines skipped; "
                                   "Wikipedia as-of revisions still ground the agents")
                return []
            if arts is None:
                return []
        except Exception as e:  # noqa: BLE001
            self._gdelt_blocked = True
            self._err("GDELT", f"unreachable ({type(e).__name__}) — headlines skipped")
            return []
        docs = []
        for a in arts:
            seen = a.get("seendate", "")  # 20240105T123000Z
            iso = f"{seen[:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8 else ""
            docs.append(Doc("gdelt", a.get("title", "")[:200], "", a.get("url", ""), iso, True, "",
                            {"domain": a.get("domain"), "country": a.get("sourcecountry"), "lang": a.get("language")}))
        return docs

    # ------------------------------------------------------------ bundle
    async def gather(self, topic: str, titles: list[str], cutoff: date, extra_queries: list[str] | None = None,
                     user_docs: list[dict] | None = None, want_latest: bool = True,
                     event_titles: list[str] | None = None) -> list[Doc]:
        docs: list[Doc] = []
        self.errors = []
        self._gdelt_blocked = False
        if not titles:
            self.errors.append("Wikipedia: no article titles to fetch (title suggestion step returned none)")
        wiki_task = self.wiki_docs(titles, cutoff, want_latest=want_latest, event_titles=event_titles)
        queries = [topic] + [q for q in (extra_queries or []) if q and q != topic]
        gdelt_tasks = [self.gdelt(q, cutoff - timedelta(days=120), cutoff) for q in queries[:2]]
        results = await asyncio.gather(wiki_task, *gdelt_tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, Exception):
                self._err("retrieval task", res)
                continue
            docs.extend(res)
        for ud in user_docs or []:
            if (ud.get("text") or "").strip():
                docs.append(Doc("user", ud.get("title") or "Pasted document", ud["text"][: self.s.max_chars_per_article * 2],
                                "", cutoff.isoformat(), False, "user-supplied; leakage filter applied"))
        return docs


async def diagnose(settings: Settings) -> dict:
    """Probe each source with a known-good request; used by /api/diag and the notebook."""
    r = Retriever(settings)
    out: dict = {"user_agent": settings.user_agent}
    try:
        hits = await r.wiki_search("Lehman Brothers", 3)
        out["wikipedia_search"] = {"ok": bool(hits), "detail": hits}
    except Exception as e:  # noqa: BLE001
        out["wikipedia_search"] = {"ok": False, "detail": r._fmt(e)}
    try:
        revid, ts, ok = await r.wiki_revision_asof("Lehman Brothers", date(2008, 3, 1))
        txt = await r.wiki_revision_text(revid) if revid else ""
        out["wikipedia_asof"] = {"ok": bool(revid and txt), "detail": f"revid={revid} ts={ts} chars={len(txt)} cutoff_ok={ok}"}
    except Exception as e:  # noqa: BLE001
        out["wikipedia_asof"] = {"ok": False, "detail": r._fmt(e)}
    try:
        txt, url = await r.wiki_latest_text("Lehman Brothers")
        out["wikipedia_latest"] = {"ok": len(txt) > 1000, "detail": f"chars={len(txt)} url={url}"}
    except Exception as e:  # noqa: BLE001
        out["wikipedia_latest"] = {"ok": False, "detail": r._fmt(e)}
    docs = await r.gdelt("federal reserve", date(2024, 1, 1), date(2024, 1, 31), 5)
    out["gdelt"] = {"ok": bool(docs), "detail": [d.title for d in docs][:3] or (r.errors[-1] if r.errors else "no articles")}
    await r.aclose()
    out["ok"] = all(v.get("ok") for k, v in out.items() if isinstance(v, dict))
    return out


def _first_rev(data: dict) -> dict | None:
    pages = data.get("query", {}).get("pages", {})
    for p in pages.values():
        revs = p.get("revisions") or []
        if revs:
            return revs[0]
    return None


_REF = re.compile(r"<ref[^>]*?/>|<ref[^>]*>.*?</ref>", re.S | re.I)
_HTML = re.compile(r"<[^>]+>")
_MULTI_NL = re.compile(r"\n{3,}")


def clean_wikitext(wt: str) -> str:
    wt = _REF.sub("", wt)
    if mwparserfromhell is not None:
        try:
            code = mwparserfromhell.parse(wt)
            for t in code.filter_templates(recursive=False):
                try:
                    code.remove(t)
                except ValueError:
                    pass
            text = code.strip_code(normalize=True, collapse=True)
        except Exception:  # noqa: BLE001
            text = _fallback_strip(wt)
    else:
        text = _fallback_strip(wt)
    text = _HTML.sub("", text)
    # drop infobox-ish / table residue lines and category noise
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith(("|", "{", "}", "!")) or s.lower().startswith(("category:", "file:", "image:")):
            continue
        lines.append(s)
    text = "\n".join(lines)
    return _MULTI_NL.sub("\n\n", text).strip()


def _fallback_strip(wt: str) -> str:
    wt = re.sub(r"\{\{[^{}]*\}\}", "", wt)
    wt = re.sub(r"\{\{[^{}]*\}\}", "", wt)
    wt = re.sub(r"\{\|.*?\|\}", "", wt, flags=re.S)
    wt = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", wt)
    wt = re.sub(r"\[https?://[^\s\]]+\s*([^\]]*)\]", r"\1", wt)
    wt = re.sub(r"'{2,}", "", wt)
    wt = re.sub(r"^=+\s*(.*?)\s*=+$", r"\1", wt, flags=re.M)
    return wt


def briefing_from_docs(docs: list[Doc], cutoff: date, max_chars: int = 24000) -> tuple[str, list[str]]:
    """Concatenate cutoff-safe material first, then flagged material, into raw briefing text.
    Returns (text, list_of_flags)."""
    parts, flags = [], []
    budget = max_chars
    ordered = [d for d in docs if d.source == "wikipedia_asof"] + \
              [d for d in docs if d.source == "user"] + \
              [d for d in docs if d.source == "gdelt"]
    gdelt_lines = []
    for d in ordered:
        if d.source == "gdelt":
            gdelt_lines.append(f"- [{d.as_of}] {d.title} ({d.meta.get('domain', '')})")
            continue
        if not d.cutoff_ok and d.note:
            flags.append(f"{d.title}: {d.note}")
        chunk = f"### {d.title} (as of {d.as_of[:10] or 'unknown'}; source: {d.source})\n{d.text}\n"
        if len(chunk) > budget:
            chunk = chunk[:budget]
        parts.append(chunk)
        budget -= len(chunk)
        if budget <= 500:
            break
    if gdelt_lines:
        parts.append("### News headlines in the 120 days before the cutoff (GDELT)\n" + "\n".join(gdelt_lines[:60]))
    return "\n".join(parts), flags
