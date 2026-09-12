"""Per-actor research: build an evidence-backed, date-cut dossier for each persona.

Why: agents built from short persona paragraphs behave generically; agents built from rich
first-person material behave like the person (Park et al. 2024). The IC's at-a-distance
profiling methods (Hermann's Leadership Trait Analysis, George/Walker's Operational Code)
give the dossier a schema that predicts behaviour under pressure.

Pipeline per persona:
  plan (strong) -> collect (Wikipedia/Wikiquote as-of, web search, Wayback snapshots <= cutoff)
  -> extract dated evidence per document (cheap model) -> synthesize dossier (strong) -> critic (strong)

Sources by preference for web search: Exa (date-cut, full text) > Serper/Google (date range) > DuckDuckGo
(no date filter; pages are fetched from the Wayback snapshot before the cutoff when one exists, else flagged).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Callable

import httpx

from . import prompts as P
from .config import Settings
from .llm import LLM
from .retrieval import Retriever, clean_wikitext

log = logging.getLogger("whatif.research")

try:
    import trafilatura  # type: ignore
except Exception:  # noqa: BLE001
    trafilatura = None


@dataclass
class Source:
    url: str
    title: str
    kind: str                 # wikipedia | wikiquote | web | wayback
    as_of: str = ""           # date the content reflects (snapshot / revision / published)
    cutoff_ok: bool = True    # False = may contain post-cutoff information
    text: str = ""
    note: str = ""

    def public(self) -> dict:
        d = asdict(self)
        d["chars"] = len(self.text)
        d.pop("text", None)
        return d


@dataclass
class Evidence:
    date: str
    type: str                 # decision | statement | relationship | trait | precedent | constraint
    claim: str
    quote: str = ""
    url: str = ""
    weight: float = 0.5


class Researcher:
    def __init__(self, settings: Settings, llm: LLM, retriever: Retriever,
                 log_fn: Callable[[str, str], None] | None = None):
        self.s = settings
        self.llm = llm
        self.retriever = retriever
        self._log = log_fn or (lambda msg, level="info": log.info(msg))
        self._client: httpx.AsyncClient | None = None
        self._wayback_lock = asyncio.Semaphore(3)

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15), follow_redirects=True,
                                             headers={"User-Agent": self.s.user_agent})
        return self._client

    async def aclose(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ public
    async def build_dossier(self, persona: dict, scenario: dict, cutoff: str, cast_names: list[str],
                            depth: str = "standard") -> dict:
        """Returns a dossier dict (see prompts.DOSSIER_SYS for the schema) plus 'sources' and 'evidence'."""
        name = persona["name"]
        cut = date.fromisoformat(cutoff)
        n_queries = {"quick": 3, "standard": 6, "deep": 10}.get(depth, 6)
        max_docs = {"quick": 6, "standard": 12, "deep": 20}.get(depth, 12)

        # 1. plan
        plan = await self.llm.json(P.RESEARCH_PLAN_SYS, P.fill(
            P.RESEARCH_PLAN_USER, name=name, role=persona.get("role", ""), title=scenario["title"],
            question=scenario["question"], cutoff=cutoff, cast=", ".join(c for c in cast_names if c != name),
            n=n_queries), kind="research_plan", ctx={"name": name}, strong=True, max_tokens=900)
        queries = [q for q in plan.get("queries", []) if isinstance(q, str)][:n_queries]
        wiki_titles = [t for t in plan.get("wikipedia_titles", []) if isinstance(t, str)][:4] or [name]
        is_person = bool(plan.get("is_individual", True))
        self._log(f"[dossier] {name}: {len(queries)} queries, wiki: {', '.join(wiki_titles)}")

        # 2. collect
        sources: list[Source] = []
        if self.llm.is_mock:
            sources.append(Source("mock://source", "Mock source", "web", cutoff, True, "x" * 400, "mock"))
            queries, wiki_titles = [], []
        wiki_task = self._wikipedia(wiki_titles, cut) if wiki_titles else _noop()
        quote_task = self._wikiquote(name, cut) if (is_person and not self.llm.is_mock) else _noop()
        web_task = self._web(queries, cut, max_docs)
        for res in await asyncio.gather(wiki_task, quote_task, web_task, return_exceptions=True):
            if isinstance(res, Exception):
                self._log(f"[dossier] {name}: source failed: {res}", "warning")
            elif res:
                sources.extend(res)
        sources = _dedupe(sources)[: max_docs + 6]
        self._log(f"[dossier] {name}: {len(sources)} sources "
                  f"({sum(1 for s in sources if s.cutoff_ok)} as-of, {sum(1 for s in sources if not s.cutoff_ok)} flagged)")

        # 3. extract evidence (cheap model, concurrent)
        async def extract(src: Source) -> list[Evidence]:
            if len(src.text) < 300:
                return []
            out = await self.llm.json(P.fill(P.EVIDENCE_SYS, cutoff=cutoff), P.fill(
                P.EVIDENCE_USER, name=name, title=src.title, url=src.url, as_of=src.as_of or "unknown",
                flag=("CONTENT MAY POSTDATE THE CUTOFF — keep only items whose own date is on/before the cutoff"
                      if not src.cutoff_ok else "content is as of the cutoff or earlier"),
                text=src.text[:9000]), kind="evidence", ctx={"name": name, "date": cutoff}, max_tokens=1400)
            items = []
            for e in out.get("evidence", [])[:14]:
                if not isinstance(e, dict) or not e.get("claim"):
                    continue
                d = str(e.get("date") or "")[:10]
                if d and d > cutoff:
                    continue
                items.append(Evidence(date=d or "undated", type=str(e.get("type", "statement"))[:20],
                                      claim=str(e["claim"])[:400], quote=str(e.get("quote", ""))[:400],
                                      url=src.url, weight=float(e.get("weight", 0.5) or 0.5)))
            return items

        results = await asyncio.gather(*(extract(s) for s in sources), return_exceptions=True)
        evidence: list[Evidence] = []
        for r in results:
            if isinstance(r, list):
                evidence.extend(r)
        evidence.sort(key=lambda e: (e.date == "undated", e.date))
        self._log(f"[dossier] {name}: {len(evidence)} evidence items")

        # 4. synthesize
        ev_txt = "\n".join(f"- [{e.date}] ({e.type}) {e.claim}" + (f' — "{e.quote}"' if e.quote else "") + f" <{e.url}>"
                           for e in evidence[:120])
        if not ev_txt:
            ev_txt = "(no evidence retrieved — say so explicitly in 'gaps' and keep confidence low; do not invent)"
        dossier = await self.llm.json(P.fill(P.DOSSIER_SYS, cutoff=cutoff), P.fill(
            P.DOSSIER_USER, name=name, role=persona.get("role", ""), title=scenario["title"], question=scenario["question"],
            cutoff=cutoff, cast=", ".join(cast_names), evidence=ev_txt[:28000], current=json.dumps(
                {k: persona.get(k, "") for k in ("goals", "stance", "style", "resources", "background", "playbook",
                                                 "relationships", "red_lines")}, ensure_ascii=False)),
            kind="dossier", ctx={"name": name, "date": cutoff}, strong=True, max_tokens=3600, temperature=0.3)

        # 5. critic
        try:
            crit = await self.llm.json(P.fill(P.DOSSIER_CRITIC_SYS, cutoff=cutoff), P.fill(
                P.DOSSIER_CRITIC_USER, name=name, dossier=json.dumps(dossier, ensure_ascii=False)[:14000]),
                kind="dossier_critic", ctx={"name": name}, strong=True, max_tokens=900, temperature=0.2)
            dossier["critic"] = {"leakage": crit.get("leakage", []), "unsupported": crit.get("unsupported", []),
                                 "verdict": crit.get("verdict", "")}
        except Exception as e:  # noqa: BLE001
            dossier["critic"] = {"error": str(e)[:200]}

        dossier["sources"] = [s.public() for s in sources]
        dossier["evidence"] = [asdict(e) for e in evidence[:150]]
        dossier["built_for_cutoff"] = cutoff
        dossier["depth"] = depth
        return dossier

    # ------------------------------------------------------------------ sources
    async def _wikipedia(self, titles: list[str], cut: date) -> list[Source]:
        out: list[Source] = []
        for t in titles:
            try:
                canon = await self.retriever.wiki_exists(t)
                if not canon:
                    continue
                revid, ts, ok = await self.retriever.wiki_revision_asof(canon, cut)
                if ok and revid:
                    txt = await self.retriever.wiki_revision_text(revid)
                    out.append(Source(f"https://en.wikipedia.org/w/index.php?title={canon.replace(' ', '_')}&oldid={revid}",
                                      f"Wikipedia: {canon}", "wikipedia", ts[:10], True, txt[:25000]))
                else:
                    txt, url = await self.retriever.wiki_latest_text(canon)
                    out.append(Source(url, f"Wikipedia: {canon}", "wikipedia", date.today().isoformat(), False,
                                      txt[:20000], "no revision existed on the cutoff; present-day text"))
            except Exception as e:  # noqa: BLE001
                log.info("wikipedia dossier fetch failed for %r: %s", t, e)
        return out

    async def _wikiquote(self, name: str, cut: date) -> list[Source]:
        api = "https://en.wikiquote.org/w/api.php"
        c = await self.client()
        try:
            r = await c.get(api, params={"action": "query", "list": "search", "srsearch": name, "srlimit": 1, "format": "json"})
            hits = r.json().get("query", {}).get("search", [])
            if not hits or hits[0]["title"].lower() != name.lower() and name.lower() not in hits[0]["title"].lower():
                return []
            title = hits[0]["title"]
            ts = f"{cut.isoformat()}T23:59:59Z"
            r = await c.get(api, params={"action": "query", "prop": "revisions", "titles": title, "rvlimit": 1,
                                         "rvprop": "ids|timestamp", "rvdir": "older", "rvstart": ts, "format": "json"})
            pages = r.json().get("query", {}).get("pages", {})
            rev = next((p["revisions"][0] for p in pages.values() if p.get("revisions")), None)
            if not rev:
                return []
            r = await c.get(api, params={"action": "query", "prop": "revisions", "revids": rev["revid"],
                                         "rvprop": "content", "rvslots": "main", "format": "json"})
            pages = r.json().get("query", {}).get("pages", {})
            rv = next((p["revisions"][0] for p in pages.values() if p.get("revisions")), None)
            wt = ((rv or {}).get("slots", {}).get("main", {}) or {}).get("*", "")
            txt = clean_wikitext(wt)
            if len(txt) < 200:
                return []
            return [Source(f"https://en.wikiquote.org/w/index.php?title={title.replace(' ', '_')}&oldid={rev['revid']}",
                           f"Wikiquote: {title}", "wikiquote", rev["timestamp"][:10], True, txt[:15000],
                           "verbatim quotations (as of cutoff)")]
        except Exception as e:  # noqa: BLE001
            log.info("wikiquote failed for %r: %s", name, e)
            return []

    async def _web(self, queries: list[str], cut: date, max_docs: int) -> list[Source]:
        if not queries:
            return []
        if self.s.exa_api_key:
            return await self._exa(queries, cut, max_docs)
        if self.s.serper_api_key:
            hits = await self._serper(queries, cut, max_docs)
        else:
            hits = await self._ddg(queries, max_docs)
        return await self._fetch_hits(hits, cut)

    async def _exa(self, queries: list[str], cut: date, max_docs: int) -> list[Source]:
        c = await self.client()
        per = max(2, max_docs // max(1, len(queries)))
        out: list[Source] = []

        async def one(q: str):
            try:
                r = await c.post("https://api.exa.ai/search", headers={"x-api-key": self.s.exa_api_key},
                                 json={"query": q, "type": "auto", "numResults": per,
                                       "endPublishedDate": f"{cut.isoformat()}T23:59:59.000Z",
                                       "contents": {"text": {"maxCharacters": 6000},
                                                    "highlights": {"query": q, "maxCharacters": 1200}}})
                if r.status_code != 200:
                    self._log(f"[dossier] Exa HTTP {r.status_code}: {r.text[:120]}", "warning")
                    return
                for res in r.json().get("results", []):
                    txt = res.get("text") or " ".join(res.get("highlights") or [])
                    pub = (res.get("publishedDate") or "")[:10]
                    out.append(Source(res.get("url", ""), res.get("title") or res.get("url", ""), "web", pub,
                                      bool(pub) and pub <= cut.isoformat(), txt, f"exa · q: {q}"))
            except Exception as e:  # noqa: BLE001
                self._log(f"[dossier] Exa failed: {e}", "warning")

        await asyncio.gather(*(one(q) for q in queries))
        return out

    async def _serper(self, queries: list[str], cut: date, max_docs: int) -> list[dict]:
        c = await self.client()
        per = max(2, max_docs // max(1, len(queries)))
        hits: list[dict] = []
        for q in queries:
            try:
                r = await c.post("https://google.serper.dev/search", headers={"X-API-KEY": self.s.serper_api_key},
                                 json={"q": q, "num": per, "tbs": f"cdr:1,cd_max:{cut.strftime('%m/%d/%Y')}"})
                for o in r.json().get("organic", [])[:per]:
                    hits.append({"url": o.get("link"), "title": o.get("title"), "snippet": o.get("snippet"), "q": q,
                                 "date": (o.get("date") or "")})
            except Exception as e:  # noqa: BLE001
                self._log(f"[dossier] Serper failed: {e}", "warning")
        return hits

    async def _ddg(self, queries: list[str], max_docs: int) -> list[dict]:
        per = max(2, max_docs // max(1, len(queries)))
        hits: list[dict] = []

        def run(q: str):
            try:
                from ddgs import DDGS  # type: ignore
                return DDGS().text(q, max_results=per)
            except Exception as e:  # noqa: BLE001
                log.info("ddg failed for %r: %s", q, e)
                return []

        for q in queries:
            res = await asyncio.to_thread(run, q)
            for o in res or []:
                hits.append({"url": o.get("href") or o.get("url"), "title": o.get("title"), "snippet": o.get("body"), "q": q})
            await asyncio.sleep(0.8)
        return hits

    async def _fetch_hits(self, hits: list[dict], cut: date) -> list[Source]:
        seen, todo = set(), []
        for h in hits:
            u = (h.get("url") or "").split("#")[0]
            if not u or u in seen or any(b in u for b in ("wikipedia.org", "youtube.com", "twitter.com", "x.com/", "facebook.com", ".pdf")):
                continue
            seen.add(u)
            todo.append(h)
        sem = asyncio.Semaphore(4)

        async def one(h: dict) -> Source | None:
            async with sem:
                txt, as_of, ok, note = await self._fetch_asof(h["url"], cut)
                if not txt:
                    txt = h.get("snippet") or ""
                    note = (note + "; snippet only").strip("; ")
                if len(txt) < 200:
                    return None
                return Source(h["url"], h.get("title") or h["url"], "wayback" if ok else "web", as_of, ok, txt[:8000],
                              f"{note} · q: {h.get('q', '')}")

        out = [s for s in await asyncio.gather(*(one(h) for h in todo)) if s]
        return out

    async def _fetch_asof(self, url: str, cut: date) -> tuple[str, str, bool, str]:
        """Prefer the Wayback snapshot on/before the cutoff; else the live page, flagged."""
        c = await self.client()
        async with self._wayback_lock:
            try:
                r = await c.get("https://web.archive.org/cdx/search/cdx",
                                params={"url": url, "to": cut.strftime("%Y%m%d"), "limit": -1, "fl": "timestamp,statuscode",
                                        "filter": "statuscode:200", "output": "json"}, timeout=25)
                rows = r.json() if r.status_code == 200 and r.text.strip().startswith("[") else []
                snap = rows[-1][0] if len(rows) > 1 else None
            except Exception:  # noqa: BLE001
                snap = None
            if snap:
                try:
                    r = await c.get(f"https://web.archive.org/web/{snap}id_/{url}", timeout=30)
                    if r.status_code == 200:
                        txt = _html_to_text(r.text)
                        if len(txt) > 300:
                            return txt, f"{snap[:4]}-{snap[4:6]}-{snap[6:8]}", True, "wayback snapshot"
                except Exception:  # noqa: BLE001
                    pass
        try:
            r = await c.get(url, timeout=20)
            if r.status_code == 200 and "text/html" in r.headers.get("content-type", "text/html"):
                return _html_to_text(r.text), "", False, "live page (no pre-cutoff snapshot) — leakage filter applies"
        except Exception:  # noqa: BLE001
            pass
        return "", "", False, "unreachable"


# ---------------------------------------------------------------------- helpers
async def _noop():
    return []


def _dedupe(sources: list[Source]) -> list[Source]:
    seen, out = set(), []
    for s in sources:
        k = s.url.rstrip("/")
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    # as-of material first, then by length
    out.sort(key=lambda s: (not s.cutoff_ok, -len(s.text)))
    return out


_TAG = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S | re.I)
_WS = re.compile(r"[ \t]+")


def _html_to_text(html: str) -> str:
    if trafilatura is not None:
        try:
            t = trafilatura.extract(html, include_comments=False, include_tables=False, favor_recall=True)
            if t and len(t) > 200:
                return t
        except Exception:  # noqa: BLE001
            pass
    txt = _TAG.sub(" ", html)
    txt = re.sub(r"&nbsp;|&#160;", " ", txt)
    txt = re.sub(r"&amp;", "&", txt)
    lines = [_WS.sub(" ", ln).strip() for ln in txt.splitlines()]
    return "\n".join(ln for ln in lines if len(ln) > 40)


def dossier_digest(d: dict, max_chars: int = 2600) -> str:
    """Compact text the agent prompt carries."""
    if not d:
        return ""
    parts = []
    if d.get("summary"):
        parts.append(f"Profile: {d['summary']}")
    if d.get("precedents"):
        parts.append("Precedents (what they did before in similar spots):")
        for p in d["precedents"][:5]:
            if isinstance(p, dict):
                parts.append(f"  - {p.get('when', '')}: {p.get('situation', '')} → {p.get('what_they_did', '')} → {p.get('outcome', '')}")
    oc = d.get("operational_code") or {}
    if isinstance(oc, dict) and oc:
        parts.append("Operational code: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in oc.items() if isinstance(v, str))[:700])
    ds = d.get("decision_style")
    if isinstance(ds, dict):
        parts.append("Decision style: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in ds.items() if isinstance(v, str))[:500])
    elif isinstance(ds, str):
        parts.append("Decision style: " + ds[:500])
    if d.get("stated_commitments"):
        parts.append("On-record commitments: " + "; ".join(map(str, d["stated_commitments"][:5]))[:500])
    if d.get("pressure_points"):
        parts.append("Pressure points: " + "; ".join(map(str, d["pressure_points"][:5]))[:400])
    if d.get("voice", {}).get("quotes") if isinstance(d.get("voice"), dict) else False:
        parts.append("In their own words: " + " | ".join(f'"{q}"' for q in d["voice"]["quotes"][:5])[:600])
    txt = "\n".join(parts)
    return txt[:max_chars]
