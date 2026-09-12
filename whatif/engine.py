"""Orchestrator: retrieval -> grounding -> personas -> baseline -> forkable branch simulations.

A branch simulation is a swarm: every persona agent decides concurrently each period, an
arbiter ("world model") adjudicates what actually happens, memories update, repeat.
"""
from __future__ import annotations

import asyncio
import logging
import math
import traceback
from datetime import date, datetime, timedelta
from typing import Any

from . import prompts as P
from .config import Settings
from .llm import LLM
from .models import BRANCH_COLORS, Branch, Event, Persona, Scenario, new_id
from .retrieval import Retriever, briefing_from_docs, Doc
from .store import Store

log = logging.getLogger("whatif.engine")


class EventBus:
    """Fan-out of engine events to SSE subscribers."""

    def __init__(self):
        self.subs: set[asyncio.Queue] = set()
        self.recent: list[dict] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subs.discard(q)

    def publish(self, msg: dict):
        msg.setdefault("ts", datetime.utcnow().isoformat(timespec="seconds"))
        self.seq = getattr(self, "seq", 0) + 1
        msg["seq"] = self.seq
        self.recent.append(msg)
        self.recent = self.recent[-300:]
        for q in list(self.subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass


class Engine:
    def __init__(self, settings: Settings, store: Store, llm: LLM, retriever: Retriever, bus: EventBus,
                 max_rounds: int = 12):
        self.s = settings
        self.store = store
        self.llm = llm
        self.retriever = retriever
        self.bus = bus
        self.max_rounds = max_rounds
        self.tasks: dict[str, asyncio.Task] = {}
        self._doc_cache: dict[tuple, list[Doc]] = {}

    # ------------------------------------------------------------------ logging
    def _log(self, sc: Scenario, msg: str, level: str = "info", **extra):
        entry = {"ts": datetime.utcnow().isoformat(timespec="seconds"), "level": level, "msg": msg, **extra}
        sc.log.append(entry)
        sc.log = sc.log[-400:]
        self.bus.publish({"type": "log", "scenario_id": sc.id, **entry})
        getattr(log, level if level in ("info", "warning", "error") else "info")("[%s] %s", sc.id, msg)

    # ------------------------------------------------------------------ scenario
    def create_scenario(self, payload: dict) -> Scenario:
        anchor = payload["anchor_date"]
        horizon = payload["horizon_date"]
        if horizon <= anchor:
            raise ValueError("horizon_date must be after anchor_date")
        sc = Scenario(
            id=new_id("sc"), title=payload["title"].strip(), question=payload.get("question", "").strip(),
            anchor_date=anchor, horizon_date=horizon, step_days=int(payload.get("step_days") or 7),
            n_agents=max(2, min(12, int(payload.get("n_agents") or 6))),
            created_at=datetime.utcnow().isoformat(timespec="seconds"),
            wiki_titles=[t.strip() for t in payload.get("wiki_titles", []) if t.strip()],
            notes=(payload.get("notes") or "").strip(),
            user_docs=[d for d in payload.get("user_docs", []) if (d.get("text") or "").strip()],
            provider=self.s.public(),
        )
        self.store.save(sc)
        self.tasks[sc.id] = asyncio.create_task(self._prepare(sc.id), name=f"prepare:{sc.id}")
        return sc

    async def _prepare(self, sid: str):
        sc = self.store.get(sid)
        if not sc:
            return
        try:
            sc.status = "retrieving"
            self.store.save(sc)
            today = date.today().isoformat()
            historical = sc.horizon_date <= today
            persona_cutoff = sc.anchor_date if historical else min(max(sc.anchor_date, today), sc.horizon_date)

            # 1. which articles?
            if not sc.wiki_titles:
                self._log(sc, "Asking the model which reference articles matter…")
                # run the model call and the Wikipedia title search concurrently; never let the search block us
                async def _search():
                    if self.llm.is_mock:
                        return []
                    try:
                        return await asyncio.wait_for(self.retriever.wiki_search(sc.title, 6), timeout=12)
                    except Exception as e:  # noqa: BLE001
                        self._log(sc, f"Wikipedia title search failed/slow ({type(e).__name__}: {str(e)[:120]}); continuing with model-suggested titles.", "warning")
                        return []
                t0 = asyncio.get_event_loop().time()
                out, hint = await asyncio.gather(
                    self.llm.json(P.ARTICLES_SYS, P.fill(P.ARTICLES_USER,
                        title=sc.title, question=sc.question, anchor=sc.anchor_date, horizon=sc.horizon_date),
                        kind="articles", ctx={"hint_titles": [sc.title]}),
                    _search())
                self._log(sc, f"Title step done in {asyncio.get_event_loop().time() - t0:.1f}s")
                sc.wiki_titles = list(dict.fromkeys(t for t in out.get("titles", []) if isinstance(t, str)))[: self.s.max_wiki_articles + 2]
                if not sc.wiki_titles:  # model gave nothing usable — fall back to the keyword search
                    sc.wiki_titles = (hint or [sc.title])[: self.s.max_wiki_articles]
                sc.event_titles = [t for t in out.get("event_titles", []) if isinstance(t, str)][:4]
                queries = [q for q in out.get("queries", []) if isinstance(q, str)][:3]
            else:
                queries = []
            self._log(sc, f"Reference set: {', '.join(sc.wiki_titles) or '(none found)'}"
                          + (f" | event articles: {', '.join(sc.event_titles)}" if sc.event_titles else ""))

            # 2. retrieve as-of persona_cutoff + present-day
            self._log(sc, f"Retrieving Wikipedia revisions as of {persona_cutoff} and present-day text; GDELT headlines…")
            docs = await self._docs_for(sc, date.fromisoformat(persona_cutoff), queries, want_latest=True)
            sc.docs = [d.to_dict() for d in docs]
            n_asof = sum(1 for d in docs if d.source == "wikipedia_asof")
            n_gdelt = sum(1 for d in docs if d.source == "gdelt")
            self._log(sc, f"Retrieved {n_asof} historical article revisions, {n_gdelt} headlines, "
                          f"{sum(1 for d in docs if d.source == 'wikipedia_latest')} present-day articles.")
            if not docs and not sc.user_docs:
                self._log(sc, "No documents retrieved — agents will rely on the model's own knowledge (flagged).", "warning")

            # 3. baseline briefing + personas
            self._log(sc, f"Grounding: building a knowledge-cutoff briefing as of {persona_cutoff}…")
            briefing, flags = await self._ground(sc, docs, persona_cutoff, premise="")

            # 4. actual baseline (what really happened between anchor and min(today, horizon))
            actual_end = min(today, sc.horizon_date)
            base = Branch(id=new_id("br"), scenario_id=sc.id, name="Actual history", kind="actual", premise="",
                          fork_date=sc.anchor_date, knowledge_cutoff=actual_end, color="#e5e7eb",
                          created_at=datetime.utcnow().isoformat(timespec="seconds"),
                          step_days=sc.step_days, status="running")
            base.briefing, base.briefing_flags = briefing, flags
            sc.branches[base.id] = base
            sc.baseline_branch_id = base.id
            self.store.save(sc)

            if sc.anchor_date < actual_end:
                self._log(sc, f"Extracting the actual timeline {sc.anchor_date} → {actual_end} from present-day sources…")
                latest = [d for d in docs if d.source in ("wikipedia_latest", "user")]
                latest.sort(key=lambda d: 0 if "event article" in d.note else 1)  # event articles carry the most dated facts
                share = max(4000, 40000 // max(1, len(latest)))
                raw = "\n\n".join(f"### {d.title}\n{d.text[:share]}" for d in latest)
                if not raw.strip():
                    raw = "(no reference material retrieved; use your own knowledge of the period and say so in summaries if unsure)"
                dates = sc.dates_for(sc.anchor_date, min(8, sc.rounds_between(sc.anchor_date, actual_end)))
                out = await self.llm.json(P.ACTUAL_SYS, P.fill(P.ACTUAL_USER, 
                    start=sc.anchor_date, end=actual_end, title=sc.title, question=sc.question, raw=raw),
                    kind="actual_events", ctx={"dates": dates, "topic": sc.title, "seed": sc.id}, max_tokens=3000, strong=True)
                for i, e in enumerate(out.get("events", [])):
                    d = _safe_date(e.get("date"), sc.anchor_date, actual_end)
                    if not d:
                        continue
                    base.events.append(Event(id=new_id("ev"), branch_id=base.id, date=d, headline=str(e.get("headline", ""))[:140],
                                             summary=str(e.get("summary", "")), actors=_strlist(e.get("actors")),
                                             category=str(e.get("category", "")), kind="actual", confidence=1.0,
                                             divergence=0.0, importance=_int(e.get("importance"), 3), round=i))
                base.events.sort(key=lambda e: e.date)
                self._log(sc, f"Actual timeline: {len(base.events)} events.")
            else:
                self._log(sc, "Anchor date is today or later — no actual history to extract; the baseline is a forecast.")
            # anchor marker so the lane always has a first node
            base.events.insert(0, Event(id=new_id("ev"), branch_id=base.id, date=sc.anchor_date, headline="Scenario start",
                                        summary=f"Baseline timeline begins. Knowledge is grounded as of {persona_cutoff}.",
                                        kind="fork", importance=2, round=-1))
            # cast AFTER the actual timeline exists so the casting call sees who actually mattered
            self._log(sc, f"Casting {sc.n_agents} agents…")
            sc.personas = await self._cast(sc, briefing, persona_cutoff, sc.n_agents,
                                           timeline_txt=P.format_timeline(base.events))
            self._log(sc, "Cast: " + "; ".join(p.name for p in sc.personas))
            base.status = "completed"
            base.progress = 1.0
            sc.status = "ready"
            self.store.save(sc)
            self.bus.publish({"type": "scenario_ready", "scenario_id": sc.id})

            # 5. future horizon -> auto baseline forecast from today
            if sc.horizon_date > today:
                fork_from = max(sc.anchor_date, today)
                anchor_ev = _nearest_event(base, fork_from)
                self._log(sc, f"Horizon is in the future — launching a baseline forecast from {fork_from}.")
                self.fork(sc.id, base.id, anchor_ev.id if anchor_ev else None, premise="",
                          name="Baseline forecast", fork_date=fork_from)
        except Exception as e:  # noqa: BLE001
            sc.status = "failed"
            sc.error = f"{type(e).__name__}: {e}"
            self._log(sc, f"Preparation failed: {sc.error}", "error")
            log.error(traceback.format_exc())
            self.store.save(sc)

    async def _cast(self, sc: Scenario, briefing: str, cutoff: str, n: int, timeline_txt: str = "",
                    extra_notes: str = "") -> list[Persona]:
        out = await self.llm.json(
            P.fill(P.PERSONAS_SYS, n=n, notes_block=P.notes_block(sc.notes, extra_notes)),
            P.fill(P.PERSONAS_USER, title=sc.title, question=sc.question, cutoff=cutoff, briefing=briefing[:12000],
                   timeline=timeline_txt or "(none)"),
            kind="personas", ctx={"n": n, "seed": sc.id}, max_tokens=3200, strong=True)
        personas: list[Persona] = []
        for p in out.get("personas", [])[:n]:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            g = lambda k: str(p.get(k, "") or "")  # noqa: E731
            personas.append(Persona(id=new_id("p"), name=g("name"), role=g("role"), goals=g("goals"), stance=g("stance"),
                                    style=g("style"), resources=g("resources"), background=g("background"),
                                    playbook=g("playbook"), relationships=g("relationships"), red_lines=g("red_lines")))
        if len(personas) < 2:
            raise RuntimeError("persona casting returned fewer than 2 agents")
        return personas

    async def recast(self, sid: str, notes: str = "", n: int | None = None) -> list[Persona]:
        """Re-run casting with extra analyst guidance; keeps user-edited personas."""
        sc = self.store.get(sid)
        if not sc or not sc.baseline_branch_id:
            raise KeyError("scenario not ready")
        base = sc.branches[sc.baseline_branch_id]
        keep = [p for p in sc.personas if p.user_edited]
        want = max(2, (n or sc.n_agents) - len(keep))
        kept_names = ", ".join(p.name for p in keep) or "none"
        fresh = await self._cast(sc, base.briefing, base.knowledge_cutoff, want,
                                 timeline_txt=P.format_timeline(base.events),
                                 extra_notes=(notes + f"\nAlready cast (do not duplicate): {kept_names}").strip())
        sc.personas = keep + fresh
        if notes:
            sc.notes = (sc.notes + "\n" + notes).strip()
        self.store.save(sc)
        self._log(sc, "Recast: " + "; ".join(p.name for p in sc.personas))
        return sc.personas

    def update_persona(self, sid: str, pid: str, fields: dict) -> Persona:
        sc = self.store.get(sid)
        if not sc:
            raise KeyError("scenario not found")
        p = next((x for x in sc.personas if x.id == pid), None)
        if pid == "new":
            p = Persona(id=new_id("p"), name=str(fields.get("name") or "New actor"), role="", goals="")
            sc.personas.append(p)
        if not p:
            raise KeyError("persona not found")
        for k in ("name", "role", "goals", "stance", "style", "resources", "background", "playbook", "relationships", "red_lines"):
            if k in fields and fields[k] is not None:
                setattr(p, k, str(fields[k]))
        p.user_edited = True
        self.store.save(sc)
        return p

    def delete_persona(self, sid: str, pid: str) -> bool:
        sc = self.store.get(sid)
        if not sc:
            raise KeyError("scenario not found")
        before = len(sc.personas)
        sc.personas = [x for x in sc.personas if x.id != pid]
        self.store.save(sc)
        return len(sc.personas) < before

    async def retriever_safe(self, fn, *a, **k):
        try:
            return await fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            log.warning("retrieval helper failed: %s", e)
            return None

    async def _docs_for(self, sc: Scenario, cutoff: date, queries: list[str], want_latest: bool) -> list[Doc]:
        key = (sc.id, cutoff.isoformat(), want_latest)
        if key in self._doc_cache:
            return self._doc_cache[key]
        if self.llm.is_mock and self.s.provider == "mock":
            docs = []  # keep the mock fully offline
        else:
            try:
                docs = await self.retriever.gather(sc.title, sc.wiki_titles, cutoff, queries, sc.user_docs,
                                                   want_latest=want_latest, event_titles=sc.event_titles)
            except Exception as e:  # noqa: BLE001
                self._log(sc, f"Retrieval error: {e}", "warning")
                docs = []
            for err in self.retriever.errors[:6]:
                self._log(sc, f"Retrieval: {err}", "warning")
            if not docs:
                self._log(sc, "Retrieval returned nothing. Run Diagnostics (provider chip → Diagnostics, or GET /api/diag) "
                              "to see which source is failing.", "warning")
        self._doc_cache[key] = docs
        return docs

    async def _ground(self, sc: Scenario, docs: list[Doc], cutoff: str, premise: str) -> tuple[str, list[str]]:
        raw, flags = briefing_from_docs(docs, date.fromisoformat(cutoff))
        # present-day text is never handed to agents directly; only through the leakage filter, and only
        # when we have nothing better
        if len(raw) < 1500:
            latest = [d for d in docs if d.source == "wikipedia_latest"]
            if latest:
                raw += "\n\n" + "\n\n".join(f"### {d.title} (PRESENT-DAY TEXT — POSTDATES CUTOFF)\n{d.text[:5000]}" for d in latest[:4])
                flags.append("present-day articles used through the leakage filter (no as-of revision available)")
        if not raw.strip():
            raw = "(No reference material could be retrieved. Rely on well-established public knowledge as of the cutoff.)"
            flags.append("no retrieved material; model knowledge only")
        raw = raw[:34000]
        self._log(sc, f"Leakage filter: {len(raw) // 1000}k chars of source material → briefing (this is the longest single model call, ~30-60s)")
        out = await self.llm.json(P.GROUND_SYS, P.fill(P.GROUND_USER,
            cutoff=cutoff, title=sc.title, question=sc.question, premise_block=P.premise_block(premise), raw=raw),
            kind="ground", ctx={"topic": sc.title, "date": cutoff, "text": raw}, max_tokens=2000, temperature=0.3)
        briefing = out.get("briefing") or out.get("context") or ""
        if out.get("open_questions"):
            briefing += "\n\n**Open questions at the cutoff:** " + "; ".join(map(str, out["open_questions"]))
        if out.get("removed"):
            flags.append("stripped as post-cutoff: " + "; ".join(map(str, out["removed"]))[:600])
        return briefing.strip(), flags

    # ------------------------------------------------------------------ branches
    def fork(self, sid: str, parent_id: str, fork_event_id: str | None, premise: str, name: str = "",
             fork_date: str | None = None, step_days: int | None = None, max_rounds: int | None = None,
             notes: str = "") -> Branch:
        sc = self.store.get(sid)
        if not sc:
            raise KeyError("scenario not found")
        parent = sc.branches.get(parent_id)
        if not parent:
            raise KeyError("parent branch not found")
        ev = next((e for e in parent.events if e.id == fork_event_id), None) if fork_event_id else None
        fdate = fork_date or (ev.date if ev else parent.fork_date)
        if fdate >= sc.horizon_date:
            raise ValueError("fork date must be before the scenario horizon")
        if fdate < (parent.fork_date or sc.anchor_date):
            raise ValueError(f"fork date must be on or after the parent lane's start ({parent.fork_date or sc.anchor_date})")
        premise = (premise or "").strip()
        kind = "forecast"
        name = name.strip() or (premise[:60] + ("…" if len(premise) > 60 else "") if premise else "Forecast")
        idx = len(sc.branches) % len(BRANCH_COLORS)
        days = (date.fromisoformat(sc.horizon_date) - date.fromisoformat(fdate)).days
        cap = max_rounds or self.max_rounds
        step = step_days or sc.step_days
        rounds = max(1, math.ceil(days / step))
        if rounds > cap:
            rounds = cap
            step = max(1, math.ceil(days / cap))
        br = Branch(id=new_id("br"), scenario_id=sc.id, name=name, kind=kind, premise=premise,
                    parent_branch_id=parent.id, fork_event_id=ev.id if ev else None, fork_date=fdate,
                    knowledge_cutoff=fdate, notes=(notes or "").strip(), color=BRANCH_COLORS[idx], status="pending", total_rounds=rounds,
                    step_days=step, created_at=datetime.utcnow().isoformat(timespec="seconds"), depth=parent.depth + 1)
        br.events.append(Event(id=new_id("ev"), branch_id=br.id, date=fdate,
                               headline=("What if: " + premise[:110]) if premise else "Forecast begins",
                               summary=premise or "Agents forecast forward with no counterfactual change.",
                               kind="premise", importance=5, round=-1))
        sc.branches[br.id] = br
        self.store.save(sc)
        self.tasks[br.id] = asyncio.create_task(self._run_branch(sc.id, br.id), name=f"branch:{br.id}")
        return br

    def stop(self, branch_id: str) -> bool:
        t = self.tasks.get(branch_id)
        if t and not t.done():
            t.cancel()
            return True
        return False

    def lineage_events(self, sc: Scenario, br: Branch, until: str | None = None) -> list[Event]:
        """Events visible on a branch: ancestors' events up to each fork point + own events."""
        chain: list[Branch] = []
        cur: Branch | None = br
        while cur:
            chain.append(cur)
            cur = sc.branches.get(cur.parent_branch_id) if cur.parent_branch_id else None
        chain.reverse()
        out: list[Event] = []
        for i, b in enumerate(chain):
            limit = chain[i + 1].fork_date if i + 1 < len(chain) else None
            for e in b.events:
                if limit is not None and e.date > limit:
                    continue
                if until is not None and e.date > until:
                    continue
                out.append(e)
        out.sort(key=lambda e: (e.date, e.round))
        return out

    async def _run_branch(self, sid: str, bid: str):
        sc = self.store.get(sid)
        br = sc.branches.get(bid) if sc else None
        if not sc or not br:
            return
        parent = sc.branches.get(br.parent_branch_id) if br.parent_branch_id else None
        try:
            br.status = "retrieving"
            self.store.save(sc)
            self._log(sc, f"[{br.name}] Retrieving knowledge as of {br.knowledge_cutoff}…", branch_id=br.id)
            docs = await self._docs_for(sc, date.fromisoformat(br.knowledge_cutoff), [], want_latest=False)
            n_ev = sum(1 for d in docs if d.source == "wikipedia_asof" and d.title in sc.event_titles)
            self._log(sc, f"[{br.name}] {sum(1 for d in docs if d.source == 'wikipedia_asof')} article revisions as of "
                          f"{br.knowledge_cutoff}" + (f" (incl. {n_ev} event article{'s' if n_ev != 1 else ''})" if n_ev else ""),
                      branch_id=br.id)
            if not docs:
                # fall back to whatever the scenario has (as-of docs from the persona cutoff)
                docs = [Doc(**d) for d in sc.docs if d.get("source") in ("wikipedia_asof", "user", "gdelt")]
            self._log(sc, f"[{br.name}] Grounding briefing (cutoff {br.knowledge_cutoff})…", branch_id=br.id)
            br.briefing, br.briefing_flags = await self._ground(sc, docs, br.knowledge_cutoff, br.premise)
            br.status = "running"
            self.store.save(sc)
            self.bus.publish({"type": "branch_status", "scenario_id": sc.id, "branch_id": br.id, "status": br.status})

            dates = sc.dates_for(br.fork_date, br.total_rounds, br.step_days)
            dates[-1] = min(dates[-1], sc.horizon_date)
            prev = br.fork_date
            personas = sc.personas
            for r, d in enumerate(dates):
                visible = self.lineage_events(sc, br)
                timeline_txt = P.format_timeline(visible)
                # --- swarm: every agent decides concurrently
                async def step(p: Persona):
                    out = await self.llm.json(
                        P.fill(P.AGENT_SYS, name=p.name, role=p.role, date=d, cutoff=br.knowledge_cutoff, goals=p.goals,
                                           stance=p.stance, style=p.style, resources=p.resources,
                                           background=p.background or "(not documented)", playbook=p.playbook or "(not documented)",
                                           relationships=p.relationships or "(not documented)", red_lines=p.red_lines or "(not documented)"),
                        P.fill(P.AGENT_USER, premise_block=P.premise_block(br.premise, br.fork_date),
                                            notes_block=P.notes_block(sc.notes, br.notes),
                                            cutoff=br.knowledge_cutoff, briefing=br.briefing[:self.s.brief_chars],
                                            timeline=timeline_txt, memory=br.agent_memory.get(p.name, "(none)"),
                                            world_state=br.world_state or "(start of simulation)", date=d),
                        kind="agent_step", ctx={"name": p.name, "date": d, "premise": br.premise, "topic": sc.title,
                                                "seed": br.id}, max_tokens=900)
                    return {"persona_id": p.id, "name": p.name, "role": p.role,
                            "thoughts": str(out.get("thoughts", ""))[:600], "action": str(out.get("action", ""))[:400],
                            "statement": str(out.get("statement", ""))[:400],
                            "predicted_next": str(out.get("predicted_next", ""))[:300]}

                results = await asyncio.gather(*(step(p) for p in personas), return_exceptions=True)
                actions = []
                for p, res in zip(personas, results):
                    if isinstance(res, Exception):
                        self._log(sc, f"[{br.name}] {p.name} failed this round: {res}", "warning", branch_id=br.id)
                        actions.append({"persona_id": p.id, "name": p.name, "role": p.role, "thoughts": "",
                                        "action": f"{p.name} is silent this period.", "statement": "", "predicted_next": ""})
                    else:
                        actions.append(res)
                self.bus.publish({"type": "agent_actions", "scenario_id": sc.id, "branch_id": br.id, "date": d,
                                  "actions": [{"name": a["name"], "action": a["action"]} for a in actions]})

                # --- arbiter
                parent_block = ""
                if parent:
                    pev = [e for e in self.lineage_events(sc, parent) if prev < e.date <= d]
                    parent_block = ("PARENT TIMELINE IN THE SAME PERIOD (for divergence scoring only — the actors do NOT know this):\n"
                                    + (P.format_timeline(pev) if pev else "(no recorded events in this window)") + "\n")
                actions_txt = "\n".join(f"- {a['name']} ({a['role']}): {a['action']}"
                                        + (f" Says: {a['statement']}" if a["statement"] else "") for a in actions)
                out = await self.llm.json(
                    P.fill(P.ARBITER_SYS, date=d, cutoff=br.knowledge_cutoff),
                    P.fill(P.ARBITER_USER, premise_block=P.premise_block(br.premise, br.fork_date),
                                          notes_block=P.notes_block(sc.notes, br.notes), cutoff=br.knowledge_cutoff,
                                          briefing_short=br.briefing[:self.s.brief_chars // 2], timeline=timeline_txt,
                                          parent_block=parent_block, actions=actions_txt, date=d),
                    kind="arbiter", ctx={"date": d, "premise": br.premise, "topic": sc.title, "seed": br.id + d,
                                         "has_parent": bool(parent)}, max_tokens=1800, strong=True)
                new_events = []
                for e in (out.get("events") or [])[:3]:
                    if not isinstance(e, dict):
                        continue
                    ed = _safe_date(e.get("date"), prev, d) or d
                    new_events.append(Event(
                        id=new_id("ev"), branch_id=br.id, date=ed, headline=str(e.get("headline", ""))[:140],
                        summary=str(e.get("summary", "")), actors=_strlist(e.get("actors")),
                        category=str(e.get("category", "")), kind="simulated",
                        confidence=_float(e.get("confidence"), 0.6), divergence=_float(e.get("divergence"), 0.0),
                        importance=_int(e.get("importance"), 3), round=r, agent_actions=actions))
                if not new_events:
                    new_events.append(Event(id=new_id("ev"), branch_id=br.id, date=d, headline="Quiet period",
                                            summary=str(out.get("world_state", "No decisive events.")), kind="simulated",
                                            confidence=0.5, importance=1, round=r, agent_actions=actions))
                br.events.extend(new_events)
                br.world_state = str(out.get("world_state", br.world_state))
                ind = out.get("indicators") or {}
                if isinstance(ind, dict):
                    br.indicators.append({"date": d, **{k: _float(v, 0.5) for k, v in ind.items() if isinstance(k, str)}})
                mem = out.get("memory_updates") or {}
                if isinstance(mem, dict):
                    for name, note in mem.items():
                        if isinstance(note, str) and note.strip():
                            old = br.agent_memory.get(name, "")
                            br.agent_memory[name] = (old + " " + note.strip()).strip()[-1200:]
                # also remember own thoughts
                for a in actions:
                    if a["thoughts"]:
                        old = br.agent_memory.get(a["name"], "")
                        br.agent_memory[a["name"]] = (old + f" [{d}] " + a["thoughts"]).strip()[-1200:]

                br.rounds_done = r + 1
                br.progress = br.rounds_done / max(1, br.total_rounds)
                prev = d
                self.store.save(sc)
                self._log(sc, f"[{br.name}] {d}: " + " | ".join(e.headline for e in new_events), branch_id=br.id)
                self.bus.publish({"type": "branch_progress", "scenario_id": sc.id, "branch_id": br.id,
                                  "rounds_done": br.rounds_done, "total_rounds": br.total_rounds, "date": d})

            # --- report
            self._log(sc, f"[{br.name}] Writing branch report…", branch_id=br.id)
            br.report = await self._report(sc, br, parent)
            br.status = "completed"
            br.progress = 1.0
            self.store.save(sc)
            self._log(sc, f"[{br.name}] Completed. p≈{br.report.get('probability_estimate', '?')}", branch_id=br.id)
            self.bus.publish({"type": "branch_status", "scenario_id": sc.id, "branch_id": br.id, "status": br.status})
        except asyncio.CancelledError:
            br.status = "stopped"
            self.store.save(sc)
            self._log(sc, f"[{br.name}] Stopped by user.", "warning", branch_id=br.id)
            self.bus.publish({"type": "branch_status", "scenario_id": sc.id, "branch_id": br.id, "status": br.status})
        except Exception as e:  # noqa: BLE001
            br.status = "failed"
            br.error = f"{type(e).__name__}: {e}"
            self.store.save(sc)
            self._log(sc, f"[{br.name}] Failed: {br.error}", "error", branch_id=br.id)
            log.error(traceback.format_exc())
            self.bus.publish({"type": "branch_status", "scenario_id": sc.id, "branch_id": br.id, "status": br.status})

    async def _report(self, sc: Scenario, br: Branch, parent: Branch | None) -> dict:
        parent_block = ""
        if parent:
            pev = [e for e in self.lineage_events(sc, parent) if e.date >= br.fork_date]
            parent_block = f"PARENT TIMELINE ({parent.name}) AFTER THE FORK:\n{P.format_timeline(pev)}\n"
        out = await self.llm.json(P.REPORT_SYS, P.fill(P.REPORT_USER, 
            title=sc.title, question=sc.question, name=br.name, premise_block=P.premise_block(br.premise, br.fork_date),
            fork_date=br.fork_date, horizon=sc.horizon_date, parent_block=parent_block,
            timeline=P.format_timeline([e for e in br.events]), world_state=br.world_state),
            kind="report", ctx={"premise": br.premise, "topic": sc.title, "seed": br.id}, max_tokens=2400, temperature=0.4, strong=True)
        out["probability_estimate"] = _float(out.get("probability_estimate"), 0.5)
        return out

    # ------------------------------------------------------------------ interaction
    async def interview(self, sid: str, bid: str, persona_id: str, question: str) -> dict:
        sc = self.store.get(sid)
        br = sc.branches.get(bid) if sc else None
        if not sc or not br:
            raise KeyError("not found")
        p = next((x for x in sc.personas if x.id == persona_id), None)
        if not p:
            raise KeyError("persona not found")
        d = br.events[-1].date if br.events else br.fork_date
        out = await self.llm.json(
            P.fill(P.INTERVIEW_SYS, name=p.name, role=p.role, date=d, goals=p.goals, stance=p.stance, style=p.style),
            P.fill(P.INTERVIEW_USER, premise_block=P.premise_block(br.premise, br.fork_date), briefing_short=br.briefing[:5000],
                                    timeline=P.format_timeline(self.lineage_events(sc, br)),
                                    memory=br.agent_memory.get(p.name, "(none)"), question=question),
            kind="interview", ctx={"name": p.name, "date": d, "premise": br.premise}, max_tokens=600)
        return {"persona": p.name, "answer": str(out.get("answer", ""))}

    async def compare(self, sid: str, a_id: str, b_id: str) -> dict:
        sc = self.store.get(sid)
        if not sc or a_id not in sc.branches or b_id not in sc.branches:
            raise KeyError("not found")
        a, b = sc.branches[a_id], sc.branches[b_id]
        out = await self.llm.json(P.COMPARE_SYS, P.fill(P.COMPARE_USER, 
            title=sc.title, question=sc.question, a_name=a.name, a_premise=a.premise or "(actual/baseline)",
            a_timeline=P.format_timeline(self.lineage_events(sc, a)), b_name=b.name, b_premise=b.premise or "(actual/baseline)",
            b_timeline=P.format_timeline(self.lineage_events(sc, b))),
            kind="compare", ctx={"seed": a_id + b_id, "topic": sc.title}, max_tokens=1800, temperature=0.4, strong=True)
        return out


# ---------------------------------------------------------------------- helpers
def _safe_date(v: Any, lo: str, hi: str) -> str | None:
    if not v:
        return None
    s = str(v).strip()[:10]
    try:
        if len(s) == 4:
            s = s + "-01-01"
        elif len(s) == 7:
            s = s + "-01"
        d = date.fromisoformat(s).isoformat()
    except ValueError:
        return None
    return min(max(d, lo), hi)


def _strlist(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x)[:80] for x in v if x][:6]
    if isinstance(v, str) and v:
        return [v[:80]]
    return []


def _float(v: Any, default: float) -> float:
    try:
        f = float(v)
        if math.isnan(f):
            return default
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return default


def _int(v: Any, default: int) -> int:
    try:
        return max(1, min(5, int(v)))
    except (TypeError, ValueError):
        return default


def _nearest_event(br: Branch, iso: str) -> Event | None:
    cands = [e for e in br.events if e.date <= iso]
    return max(cands, key=lambda e: e.date) if cands else (br.events[0] if br.events else None)
