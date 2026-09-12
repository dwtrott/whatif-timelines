"""Data model. Everything is a plain dataclass serialised to JSON so scenarios are
inspectable files on disk (data/scenarios/<id>.json)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class Persona:
    id: str
    name: str
    role: str
    goals: str
    stance: str = ""
    style: str = ""
    resources: str = ""


@dataclass
class Event:
    id: str
    branch_id: str
    date: str                  # ISO date
    headline: str
    summary: str
    actors: list[str] = field(default_factory=list)
    category: str = ""
    kind: str = "simulated"    # actual | simulated | fork | premise
    confidence: float = 1.0    # arbiter's confidence (simulated) or 1.0 (actual)
    divergence: float = 0.0    # 0 = same as parent branch, 1 = nothing like it
    importance: int = 3        # 1..5
    round: int = -1
    sources: list[str] = field(default_factory=list)
    agent_actions: list[dict] = field(default_factory=list)   # what each persona did this round


@dataclass
class Branch:
    id: str
    scenario_id: str
    name: str
    kind: str                       # actual | forecast
    premise: str                    # "" for the actual baseline; counterfactual text otherwise
    parent_branch_id: str | None = None
    fork_event_id: str | None = None
    fork_date: str = ""
    knowledge_cutoff: str = ""
    color: str = "#7c9cff"
    status: str = "pending"         # pending | retrieving | running | completed | failed | stopped
    progress: float = 0.0
    rounds_done: int = 0
    total_rounds: int = 0
    step_days: int = 7
    events: list[Event] = field(default_factory=list)
    briefing: str = ""              # cutoff-safe grounding text agents receive
    briefing_flags: list[str] = field(default_factory=list)
    world_state: str = ""
    indicators: list[dict] = field(default_factory=list)   # [{date, tension, public_support, economic_stress}]
    agent_memory: dict[str, str] = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    error: str = ""
    created_at: str = ""
    depth: int = 0

    def events_until(self, iso: str) -> list[Event]:
        return [e for e in self.events if e.date <= iso]


@dataclass
class Scenario:
    id: str
    title: str
    question: str                 # what we're studying, in the user's words
    anchor_date: str              # first date of interest / start of baseline
    horizon_date: str             # last date simulated
    step_days: int = 7
    n_agents: int = 6
    created_at: str = ""
    status: str = "new"           # new | retrieving | ready | failed
    error: str = ""
    wiki_titles: list[str] = field(default_factory=list)
    event_titles: list[str] = field(default_factory=list)
    user_docs: list[dict] = field(default_factory=list)
    personas: list[Persona] = field(default_factory=list)
    docs: list[dict] = field(default_factory=list)          # retrieved corpus (serialised Doc)
    branches: dict[str, Branch] = field(default_factory=dict)
    baseline_branch_id: str = ""
    provider: dict = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)

    # ---------------------------------------------------------------- helpers
    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Scenario":
        d = dict(d)
        d["personas"] = [Persona(**p) for p in d.get("personas", [])]
        branches = {}
        for bid, b in (d.get("branches") or {}).items():
            b = dict(b)
            b["events"] = [Event(**e) for e in b.get("events", [])]
            branches[bid] = Branch(**b)
        d["branches"] = branches
        return cls(**d)

    def rounds_between(self, start_iso: str, end_iso: str | None = None, step_days: int | None = None) -> int:
        step = step_days or self.step_days
        a = date.fromisoformat(start_iso)
        b = date.fromisoformat(end_iso or self.horizon_date)
        return max(1, ((b - a).days // step))

    def dates_for(self, start_iso: str, rounds: int, step_days: int | None = None) -> list[str]:
        step = step_days or self.step_days
        a = date.fromisoformat(start_iso)
        return [(a + timedelta(days=step * (i + 1))).isoformat() for i in range(rounds)]

    def public(self) -> dict:
        """Lighter payload for the UI list."""
        return {
            "id": self.id, "title": self.title, "question": self.question, "anchor_date": self.anchor_date,
            "horizon_date": self.horizon_date, "status": self.status, "created_at": self.created_at,
            "n_branches": len(self.branches), "error": self.error,
        }


BRANCH_COLORS = ["#7c9cff", "#ff8a5b", "#4fd1c5", "#f6c453", "#c084fc", "#f472b6", "#a3e635", "#38bdf8", "#fb7185", "#fbbf24"]
