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
    background: str = ""      # track record; how they behaved in past crises
    playbook: str = ""        # characteristic moves
    relationships: str = ""   # allies, rivals, dependencies
    red_lines: str = ""       # what they will not accept / will fight over
    user_edited: bool = False
    dossier: dict = field(default_factory=dict)   # evidence-backed profile (see research.py)
    dossier_status: str = ""  # "" | researching | done | failed


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
    notes: str = ""                 # analyst notes / priors injected into every agent + arbiter prompt on this branch
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
    # --- world model (v0.4)
    seed: int = 0                                          # RNG seed for junctures (runs differ by seed)
    run_group: str = ""                                    # Monte-Carlo sibling group id
    causal_map: list[dict] = field(default_factory=list)   # dependence verdicts for parent's post-fork events
    structural: list[dict] = field(default_factory=list)   # scheduled/structural events + actor lifecycle
    junctures: list[dict] = field(default_factory=list)    # rolled junctures: question, p, roll, outcome
    extra_personas: list[Persona] = field(default_factory=list)   # actors who entered during this branch
    retired: list[str] = field(default_factory=list)       # names who left the stage
    world_notes: str = ""                                  # causal-map notes (biggest uncertainties)
    schedule: list[str] = field(default_factory=list)      # period end dates (adaptive: dense after the fork)
    agent_state: dict = field(default_factory=dict)        # name -> {office, capital, credibility, pressure, priorities, grievances}
    world_vars: dict = field(default_factory=dict)         # economy, approval, legislature control, war footing, media climate
    hazards: dict = field(default_factory=dict)            # hazard_id -> {rolls, yes, last_p, question}
    inbox: dict = field(default_factory=dict)              # name -> [private messages waiting]
    horizon_end: str = ""                                  # per-branch horizon override (planner uses shorter ones)
    plan_id: str = ""                                      # intervention plan this branch belongs to
    critic_notes: list[dict] = field(default_factory=list) # per-period critic notes

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
    notes: str = ""               # analyst notes / priors about the actors (used in casting and all branches)
    wiki_titles: list[str] = field(default_factory=list)
    event_titles: list[str] = field(default_factory=list)
    year_articles: list[str] = field(default_factory=list)   # "{year} in the United States" patterns
    user_docs: list[dict] = field(default_factory=list)
    personas: list[Persona] = field(default_factory=list)
    docs: list[dict] = field(default_factory=list)          # retrieved corpus (serialised Doc)
    branches: dict[str, Branch] = field(default_factory=dict)
    baseline_branch_id: str = ""
    provider: dict = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)
    plans: list[dict] = field(default_factory=list)          # intervention plans (planner)

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
            b["extra_personas"] = [Persona(**p) for p in b.get("extra_personas", [])]
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
