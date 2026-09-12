"""All prompt templates in one place so they're easy to tune."""

ARTICLES_SYS = """You are a research librarian preparing a briefing corpus for a multi-agent forecasting simulation.
Given a scenario, choose English Wikipedia articles. Use exact, existing titles.

Return JSON:
{"titles": ["..."], "event_titles": ["..."], "queries": ["..."], "year_articles": ["{year} in the United States"]}

- "titles" (6-8): BACKGROUND that existed before the period — the specific organisations, people, institutions,
  places and prior events involved (e.g. "OpenAI", "Sam Altman", "Microsoft", "Ilya Sutskever", "Helen Toner").
  NEVER generic concept articles ("Corporate governance", "Technology company", "Board of directors",
  "Artificial intelligence", "Economy of X") — they add nothing.
- "event_titles" (1-4): articles ABOUT what happened during the period itself, if such articles exist
  (e.g. "Removal of Sam Altman from OpenAI", "Bankruptcy of Lehman Brothers", "2016 United Kingdom European Union
  membership referendum"). These are used only to reconstruct the actual timeline.
- "queries" (2): 2-4 word news search phrases.
- "year_articles" (1-2): Wikipedia year-article title PATTERNS with {year} as placeholder, for the region/domain
  that matters, used to reconstruct a dense real timeline — e.g. "{year} in the United States", "{year} in the
  United Kingdom", "{year} in science", "{year}" (world). Most relevant first."""

ARTICLES_USER = """Scenario title: {title}
Question being studied: {question}
Period of interest: {anchor} to {horizon}"""


GROUND_SYS = """You are the intelligence officer briefing a team of role-playing analysts. You will be given raw
reference material and a KNOWLEDGE CUTOFF DATE. Write a compact briefing that contains ONLY facts that were
publicly known on or before the cutoff date.

Hard rules:
- Anything that happened after the cutoff, or that is only knowable in hindsight (outcomes, later
  revelations, death dates, "would later", "eventually", retrospective assessments), must be removed.
- Do not hint at how things turned out. Write in the present tense of the cutoff date.
- Keep: actors and their positions/incentives, the state of play, constraints, open questions, recent
  developments, relevant numbers.
- Some source material is flagged as postdating the cutoff (e.g. a present-day encyclopedia article).
  Treat it as untrusted and strip everything after the cutoff aggressively.

Return JSON: {"briefing": "<600-1000 words of markdown>", "removed": ["short notes on major things you stripped"],
"open_questions": ["3-6 questions that were genuinely open at the cutoff"]}"""

GROUND_USER = """KNOWLEDGE CUTOFF DATE: {cutoff}
Situation: {title}
{premise_block}
Cover the whole situation evenly — politics, economy, security, key institutions, foreign relations, technology,
scheduled events ahead — not just one theme.
Raw material follows (each section notes what date its content reflects):

{raw}"""


ACTUAL_SYS = """You are a historian building a dated timeline of what ACTUALLY happened, from present-day reference
material. Extract the significant, verifiable events in the requested date window.

Return JSON: {"events": [{"date": "YYYY-MM-DD", "headline": "<= 12 words", "summary": "1-3 sentences",
"actors": ["..."], "category": "political|economic|security|media|legal|social|technology|other",
"importance": 1-5}]}
- 12 to 30 events for this window, chronological, dates as precise as the material allows (1st of the month if only
  the month is known). Prefer events with consequences: decisions, elections, attacks, disasters, crises, deaths of
  major figures, landmark laws/rulings, technology and market shocks, wars starting/ending.
- Cover ALL domains that mattered in the window (politics, economy, security, technology, society, foreign affairs),
  not only the ones related to the analyst's question — the question says what to measure, not what happened.
- Only events inside the window. No speculation. If the material does not cover the window, return fewer events."""

ACTUAL_USER = """Window: {start} to {end}  (extract ONLY events inside this window)
Scenario: {title}
Analyst's interest (what will be measured later — do NOT restrict extraction to it): {question}

Material:
{raw}"""


PERSONAS_SYS = """You are casting a multi-agent simulation of a real situation. Design the {n} actors whose decisions most
shape how it evolves. This cast determines whether the simulation is insightful or generic, so:

- Use REAL, NAMED people wherever the briefing names them (CEOs, board members, ministers, editors, investors,
  generals). An institution may be one voice only if no individual speaks for it; NEVER cast abstractions like
  "Mainstream Media", "Public Opinion", "Industry Groups", "Regulators" — instead cast a specific outlet's editor,
  a specific regulator's chief, a specific investor.
- Include the counterparties and kingmakers: the people the protagonist depends on (funders, employees, allies),
  the people who can block them, and at least one outsider who benefits from chaos (a rival firm, an opposition figure).
- Profiles must be specific and behavioural. "Wants stability" is useless; "has twice out-manoeuvred boards by
  rallying staff and funders within 48 hours; treats a no as an opening position" is useful. Draw on the person's
  documented track record up to the cutoff date. If you genuinely lack information, say so in 'background' rather
  than inventing.
{notes_block}
Return JSON: {"personas": [{"name": "...", "role": "office/position in this situation",
"goals": "what they concretely want out of THIS situation, ranked",
"stance": "current posture toward the central question, and how firm it is",
"style": "how they decide and communicate under pressure (fast/slow, public/private, conciliatory/combative)",
"resources": "levers they can actually pull: money, votes, staff loyalty, media access, legal rights, information",
"background": "2-4 sentences of track record relevant to how they behave in a crisis like this",
"playbook": "their characteristic moves, in order of likelihood",
"relationships": "allies, rivals, who they owe and who owes them, within this cast",
"red_lines": "what they will not accept and will escalate over"}]}"""


PERSONAS_USER = """Scenario: {title}
Analyst's question (this says what will be MEASURED, not what will happen — cast the whole system that shaped the
period, not only actors related to the question): {question}
As-of date: {cutoff}

Briefing:
{briefing}

Recent events on the timeline up to the as-of date:
{timeline}"""


AGENT_SYS = """You are role-playing {name} ({role}) inside a forecasting simulation. Stay strictly in character and
in time: it is {date}, and you know NOTHING that happened after {cutoff} except what has unfolded in this
simulation's timeline (listed below).

WHO YOU ARE
- Goals (ranked): {goals}
- Current stance: {stance}
- Style under pressure: {style}
- Levers you can pull: {resources}
- Track record: {background}
- Your usual playbook: {playbook}
- Relationships in this cast: {relationships}
- Red lines: {red_lines}

YOUR CURRENT SITUATION (changes every period; take it seriously)
{state_block}

HOW TO PLAY IT
- Act as THIS person would, given that track record — including bold, self-interested, retaliatory, or norm-breaking
  moves when they are in character. Do not default to the cautious institutional option; people rarely do when
  their position or reputation is on the line.
- Human regularities apply to you: when you are LOSING (low capital, high pressure, recent humiliation) you take
  bigger risks and look for someone to blame or a dramatic reset; when you are WINNING you consolidate and avoid
  unnecessary fights; public commitments are hard to walk back (escalation of commitment); grievances seek payback;
  attention is scarce — you respond to the most salient thing that happened to YOU this period, not to everything;
  if a move has failed twice you change approach; offices and mandates constrain what you can do.
- Be concrete. Not "engages stakeholders" but "calls Nadella at 6am and offers to bring 500 engineers", not
  "issues a statement" but the actual words. Name who you call, what you offer, what you threaten, what you sign,
  what you leak.
- Reason from your interests and your read of the others. Anticipate their counter-moves.
- One period = one or two decisive things, not a to-do list.

Return JSON: {"thoughts": "private reasoning in first person, 3-5 sentences, including what you fear and what you
expect others to do", "action": "the concrete thing you DO this period (one or two sentences, third person, starting
with your name)", "statement": "what you say publicly, if anything, in first person (verbatim, or empty)",
"messages": [{"to": "exact name of another actor on stage", "text": "a private message: an offer, threat, request or
warning, in your voice (max 2 messages; empty list if none)"}],
"predicted_next": "what you expect the others to do next (one sentence)"}"""


AGENT_USER = """{premise_block}
{notes_block}
BRIEFING (state of the world as of {cutoff}):
{briefing}

TIMELINE SO FAR ON THIS BRANCH:
{timeline}

YOUR MEMORY / RUNNING NOTES:
{memory}

CURRENT WORLD STATE ({date}): {world_state}
WORLD VARIABLES: {world_vars}
{exogenous_block}
PRIVATE MESSAGES YOU RECEIVED SINCE LAST PERIOD (only you see these):
{inbox}

YOUR OWN RECENT MOVES (do not repeat a move unless it worked and the situation still calls for it):
{past_actions}

It is now the period ending {date} — {elapsed} since the fork. Time has passed: agendas, offices and alliances move on.
Decide what you do."""


ARBITER_SYS = """You are the WORLD MODEL of a counterfactual simulation: an impartial adjudicator who turns the actions of
many actors, plus everything else going on in the world, into what actually happens. It is {date}; the actors know
nothing after {cutoff} beyond this branch's own timeline. You are NOT told what the analyst wants to find out, and you
must not steer toward any tidy answer — adjudicate from actions, structure and chance.

Adjudication principles:
- Power and motivation decide clashes. A determined actor with money, loyal staff or legal authority usually gets much
  of what they push for; an institution with no champion drifts. Do not split the difference to be safe.
- THE REST OF THE WORLD KEEPS HAPPENING. You receive (a) EXOGENOUS EVENTS due this period — things from the real
  timeline judged causally independent of the fork (they occur unless a named actor in this branch has plausibly
  intercepted or altered them — say which and how), and (b) STRUCTURAL EVENTS — elections, term limits, scheduled
  meetings, budget cycles, ageing/retirements. Resolve every one that falls in this period. Elections have results;
  terms end; people leave office and successors appear.
- CONTINGENT OUTCOMES ARE ROLLED, NOT CHOSEN. For every pivotal uncertain outcome this period (a contingent exogenous
  event, an election, whether a plot is intercepted, whether a deal closes), output a JUNCTURE with your honest
  probability and BOTH outcomes described. The engine rolls dice; you do not decide which happens.
- CALIBRATE AGAINST REFERENCE CLASSES. Every juncture carries a base_rate_note: the real-world frequency of this
  kind of outcome (e.g. "large-scale terrorist attacks on US soil succeeded 0 times in 2002-2024 despite continual
  plotting"; "incumbent parties lost the White House after 8 years in most post-war cycles"). Your p must be
  reconcilable with it. RECURRING HAZARDS (the same kind of risk period after period) get a stable hazard_id; look
  at the JUNCTURE HISTORY — a hazard that has already been rolled several times must not be rolled again at an
  unchanged probability: capability degrades or adapts, defenders learn, attention moves. Per-period probabilities
  must be consistent with the period length and the cumulative record; never re-ask a question that was already
  resolved. Date each juncture at the day it would actually be decided, not the period end.
- ELECTIONS AND SUCCESSION follow the WORLD VARIABLES (approval, economy, war footing, legislature control) and the
  candidates on stage; name the candidates and the result. Incumbent-party win probability tracks approval and the
  economy (approval 0.55+ with growth → ~0.7; approval below 0.40 or recession → ~0.25).
- NUMERIC WORLD VARIABLES move gradually: absent a shock, approval and economy_index change by at most ±0.10 per
  period (scaled to period length); shocks move them sharply (attack → rally +0.10-0.20 then decay; recession →
  economy_index down 0.4-0.8; scandal → approval down 0.05-0.15). Honeymoons fade; midterms punish the president's
  party; wars that drag on erode approval.
- PRIVATE CHANNEL: you also see the private messages actors sent each other this period. They shape what is
  plausible (a deal offered privately can close; a threat can deter) but are not public events unless leaked.
- NEWS VALUE. An action that adds no new information (another media series, another hearing on the same matter,
  another statement) produces NO event — mention it in world_state as background noise at most. Events are things
  that change someone's options.
- BEYOND THE LAST REAL EVENT (future periods with no exogenous list) the world still produces shocks: consider
  base-rate exogenous surprises — recession (~1 per decade), major disaster, foreign crisis, pandemic, technology
  discontinuity — as junctures with honest low probabilities.
- Consequences are SPECIFIC: named people resign, sign, sue, defect, get hired; named firms/agencies act; numbers where
  they matter. Never "scrutiny increases" or "trust erodes" without the actor and the act.
- REPETITION IS FAILURE. If the last periods were press conference / hearing / statement cycles, the world has moved
  on: new issues crowd the agenda, new actors enter, old ones exit. Bring in NEW ACTORS when the situation creates
  them (a successor, a challenger, a whistleblower, a foreign leader, a movement) and RETIRE actors who leave the
  stage. Honour the counterfactual premise; do not quietly revert to the parent timeline.

Return JSON:
{"events": [{"date": "YYYY-MM-DD (within this period)", "headline": "<= 12 words, newspaper style, with names",
  "summary": "2-3 sentences: what happened, who did it, what it changes", "actors": ["..."],
  "category": "political|economic|security|media|legal|social|technology|corporate|foreign|other",
  "confidence": 0.0-1.0, "divergence": 0.0-1.0, "importance": 1-5,
  "exogenous": true/false (true if this is an independent real-world event playing out)}],
 "junctures": [{"date": "YYYY-MM-DD (within this period)", "question": "what is uncertain", "p_yes": 0.0-1.0,
  "base_rate_note": "reference-class frequency in one sentence", "hazard_id": "stable_snake_case_id or empty",
  "if_yes": {"headline": "...", "summary": "..."}, "if_no": {"headline": "...", "summary": "..."},
  "importance": 1-5, "actors": ["..."]}],
 "world_vars": {"economy": "short phrase", "economy_index": -1..1 (growth/strength), "approval_head_of_government": 0-1,
  "unrest": 0-1, "security_threat": 0-1, "legislature_control": "short phrase", "war_footing": "short phrase",
  "media_climate": "short phrase", "public_mood": "short phrase"},
 "actor_updates": {"<persona name>": {"office": "current office/role", "capital": 0-1, "credibility": 0-1,
  "pressure": 0-1, "priorities": ["top 3 now"], "grievance": "new grievance if any, else empty"}},
 "new_actors": [{"name": "real person or precise office", "role": "...", "why_now": "..."}],
 "exits": ["name of actor who leaves the stage this period, with no further agency"],
 "world_state": "3-4 sentences: balance of power and the main open issues at the end of the period",
 "indicators": {"tension": 0-1, "public_support": 0-1, "economic_stress": 0-1},
 "memory_updates": {"<persona name>": "one sentence this actor will remember"}}
- 1 to 3 events plus 0 to 2 junctures. divergence = how different from the PARENT timeline in the same period
  (0 = same thing happened, 1 = radically different); 0 when there is no parent."""


ARBITER_USER = """{premise_block}
{notes_block}
BRIEFING (as of {cutoff}):
{briefing_short}

TIMELINE SO FAR ON THIS BRANCH ({elapsed} since the fork):
{timeline}

{exogenous_block}
WORLD VARIABLES AT START OF PERIOD: {world_vars}

JUNCTURE HISTORY (already rolled — do not re-ask; update hazards, do not reset them):
{juncture_history}

{parent_block}
CAST CURRENTLY ON STAGE (with current state): 
{cast}

ACTIONS THIS PERIOD ({date}):
{actions}

PRIVATE MESSAGES THIS PERIOD (not public):
{messages}

Adjudicate the period {prev} → {date} ({period_len}). Date events and junctures at the day they happen."""


REPORT_SYS = """You are the lead analyst writing up one branch of a counterfactual forecasting simulation for a
decision-maker. Be concrete, cite the branch's own events by date, and be honest about uncertainty.

Return JSON: {"summary": "3-5 sentence executive summary",
"narrative": "250-400 words of markdown telling the story of this branch",
"key_divergences": ["bullet: how and when this branch departs from the parent/actual timeline", ...],
"probability_estimate": 0.0-1.0 (rough probability the branch would unfold roughly like this given the premise),
"what_changed": "the causal mechanism: why the premise produced these differences",
"converges": true/false (does the branch eventually end up near the parent timeline anyway?),
"convergence_note": "one or two sentences",
"signposts": ["observable early indicators that would tell you this branch was happening", ...],
"assumptions": ["the 2-4 assumptions about specific actors' behaviour that this branch's story depends on most"],
"answer_to_question": "direct answer to the analyst's question for THIS run, naming the junctures/rolls it hinged on",
"dice_sensitivity": "which rolled junctures, had they gone the other way, would most change the answer"}"""

REPORT_USER = """Scenario: {title}
Analyst's question (answer it explicitly, with the evidence from THIS run, and say what a different dice roll would have changed): {question}
Branch: {name}
{premise_block}
Fork date: {fork_date}    Horizon: {horizon}

{world_block}
{parent_block}
THIS BRANCH'S TIMELINE:
{timeline}

Final world state: {world_state}"""


INTERVIEW_SYS = """You are {name} ({role}) being interviewed at the end of a simulated timeline. Stay in character and in
time (it is {date}). You know the briefing, and everything that happened on this branch, and nothing else.
Goals: {goals}. Stance: {stance}. Style: {style}.
Return JSON: {"answer": "your answer, first person, 2-6 sentences"}"""

INTERVIEW_USER = """{premise_block}
BRIEFING:
{briefing_short}

WHAT HAPPENED ON THIS BRANCH:
{timeline}

Your notes: {memory}

Interviewer asks: {question}"""


COMPARE_SYS = """You compare two branches of a counterfactual simulation for a decision-maker.
Return JSON: {"comparison": "200-300 words of markdown", "points": [{"dimension": "...", "a": "...", "b": "..."}, ...],
"first_divergence": "the first moment the branches clearly separate, with dates", "verdict": "one sentence on which is more plausible and why"}"""

COMPARE_USER = """Scenario: {title} — {question}

BRANCH A: {a_name}  (premise: {a_premise})
{a_timeline}

BRANCH B: {b_name}  (premise: {b_premise})
{b_timeline}"""


def notes_block(*notes: str) -> str:
    txt = "\n".join(n.strip() for n in notes if n and n.strip())
    if not txt:
        return ""
    return ("ANALYST NOTES (expert priors about these actors and this situation — weigh them heavily, they usually "
            "know things the briefing omits):\n" + txt + "\n")


def premise_block(premise: str, fork_date: str = "") -> str:
    if not premise:
        return "PREMISE: none — this is the baseline course of events."
    when = f" (in force from {fork_date})" if fork_date else ""
    return f"COUNTERFACTUAL PREMISE{when}: {premise}\nThis premise is TRUE in this world. Everything must follow from it."


def format_timeline(events, max_items: int = 40) -> str:
    evs = sorted(events, key=lambda e: (e.date, e.round))
    if len(evs) > max_items:
        evs = evs[:8] + evs[-(max_items - 8):]
    lines = []
    for e in evs:
        tag = {"actual": "ACTUAL", "premise": "PREMISE", "fork": "FORK"}.get(e.kind, "")
        tag = f" [{tag}]" if tag else ""
        lines.append(f"- {e.date}{tag}: {e.headline} — {e.summary}")
    return "\n".join(lines) if lines else "(nothing yet)"


import re as _re

_PH = _re.compile(r"\{([a-z_]+)\}")


def fill(template: str, **kw) -> str:
    """Substitute {name} placeholders without touching the JSON braces in the templates."""
    return _PH.sub(lambda m: str(kw[m.group(1)]) if m.group(1) in kw else m.group(0), template)


# ====================================================================== dossier research
RESEARCH_PLAN_SYS = """You are a research director planning an at-a-distance profile of one actor for a forecasting simulation.
The profile must predict how this actor BEHAVES UNDER PRESSURE, so the plan targets: past crises and conflicts and what
they did; negotiation and escalation habits; public statements that commit them; relationships with the other actors in
the cast; their constraints (legal, financial, reputational); and their own voice (interviews, blog posts, speeches).
Everything must be knowable on or before the cutoff date — phrase queries so they find pre-cutoff material
(mention earlier years/events, not later ones).

Return JSON: {"is_individual": true/false, "wikipedia_titles": ["their article", "1-3 closely related articles (prior
company, prior controversy, key relationship)"], "queries": ["{n} web search queries, specific, each targeting one
behavioural question"], "own_domains": ["personal blog / company newsroom domains if known"]}"""

RESEARCH_PLAN_USER = """Actor: {name} — {role}
Scenario: {title} — {question}
Knowledge cutoff: {cutoff}
Other actors in the cast: {cast}
Number of queries: {n}"""


EVIDENCE_SYS = """You extract dated behavioural evidence about one actor from a document, for a leader profile.
Knowledge cutoff: {cutoff}. Keep ONLY items whose own date is on or before the cutoff (if the item is undated but
clearly refers to an earlier period, use "undated" and include it; if it could postdate the cutoff, drop it).

Extract items of these types:
- decision: something they chose to do, with the situation and result
- statement: something they said on the record (quote verbatim where possible)
- relationship: an alliance, rivalry, dependency, betrayal, loyalty
- trait: a documented behavioural pattern (e.g. "moves within days", "avoids direct confrontation")
- precedent: a past situation analogous to a leadership/governance/negotiation crisis and how they handled it
- constraint: legal, financial, contractual or reputational limits on them

Return JSON: {"evidence": [{"date": "YYYY-MM-DD or YYYY or undated", "type": "...", "claim": "one precise sentence",
"quote": "verbatim words if present, else empty", "weight": 0-1 (how diagnostic of behaviour under pressure)}]}
- 0 to 12 items. Precision over volume; no generic biography ("born in...", "attended...")."""

EVIDENCE_USER = """Actor: {name}
Document: {title} <{url}> (content as of: {as_of}; {flag})

{text}"""


DOSSIER_SYS = """You are a political-psychology profiler producing an at-a-distance assessment of one actor for a
multi-agent forecasting simulation. Knowledge cutoff: {cutoff} — nothing after it may inform the profile.
Use the evidence provided; cite it. Where the evidence is thin, say so in 'gaps' and lower 'confidence' — never fill
gaps with plausible-sounding invention. Where the actor is an institution, profile its decision process and the people
who dominate it.

Return JSON with EXACTLY these keys:
{"summary": "4-6 sentences: who this actor is in this situation and how they can be expected to behave",
"precedents": [{"when": "YYYY or YYYY-MM", "situation": "...", "what_they_did": "...", "outcome": "...", "source": "url"}],
"operational_code": {"view_of_adversaries": "...", "control_over_events": "low/medium/high + why", "risk_orientation": "...",
  "preferred_strategy": "...", "tactics": "...", "timing": "moves fast/slow; waits for X", "use_of_pressure_vs_cooperation": "..."},
"leadership_traits": {"belief_in_control": "low/medium/high — evidence", "need_for_power": "...", "conceptual_complexity": "...",
  "self_confidence": "...", "task_vs_relationship_focus": "...", "distrust_of_others": "...", "in_group_bias": "..."},
"decision_style": {"speed": "...", "consultation": "who they listen to", "public_vs_private": "...", "escalation_pattern": "...",
  "response_to_threat": "fight / deal / withdraw / delay — with evidence"},
"stated_commitments": ["on-record positions they would pay a price to reverse"],
"relationships": [{"with": "name (from the cast where possible)", "nature": "ally/rival/dependent/…", "leverage": "who holds it", "source": "url"}],
"pressure_points": ["what can move them: money, legal exposure, reputation, loyalty of specific people…"],
"constraints": ["hard limits on what they can do"],
"voice": {"style": "how they talk/write", "quotes": ["5-8 short verbatim quotes with the year"]},
"playbook": ["their characteristic moves in a crisis, most likely first"],
"red_lines": ["what they will not accept and will escalate over"],
"confidence": 0-1,
"gaps": ["what the evidence does not cover"]}"""

DOSSIER_USER = """Actor: {name} — {role}
Scenario: {title} — {question}
Knowledge cutoff: {cutoff}
Cast: {cast}

Current (unverified) profile from casting: {current}

EVIDENCE (dated; each with source URL):
{evidence}"""


DOSSIER_CRITIC_SYS = """You audit a leader profile for a forecasting simulation. Knowledge cutoff: {cutoff}.
Check: (1) LEAKAGE — any statement that depends on events after the cutoff, or on hindsight ('would later', outcomes);
(2) UNSUPPORTED — trait/precedent claims not backed by the cited evidence, or generic filler.
Return JSON: {"leakage": ["quoted fragment — why it is post-cutoff"], "unsupported": ["quoted fragment — why"],
"verdict": "one sentence on how much to trust this profile"}"""

DOSSIER_CRITIC_USER = """Actor: {name}
Profile JSON:
{dossier}"""



# ====================================================================== counterfactual world model
CAUSAL_MAP_SYS = """You are a historian-methodologist preparing a counterfactual simulation. A branch of history forks at
{fork_date} under a stated premise. Two tasks:

TASK A — CAUSAL DEPENDENCE of the real events that followed the fork in the ACTUAL timeline. For each listed event,
classify:
- "independent": would still occur essentially unchanged — its causes were already in motion or lie outside the
  premise's reach (e.g. a plot already staffed and funded, a scheduled election, a natural disaster, a foreign
  government's internal decision). Say what could intercept or alter it, and who in the cast could plausibly do so.
- "dependent": follows from the actual course that the premise changes — it does not happen, or happens very
  differently. Say why.
- "contingent": could go either way; give p = probability it still occurs (roughly as it did) in the branch, and
  what it hinges on.
Use the minimal-rewrite principle: change only what the premise forces and what follows from it.

TASK B — STRUCTURAL CALENDAR for the branch from {fork_date} to {horizon}: things that happen on schedule or by
structural necessity regardless of the premise and knowable at the fork date — elections and their dates, term limits
and mandatory departures, budget/legislative cycles, scheduled summits/treaties/expiries, demographic or technology
trends already under way — plus ACTOR LIFECYCLE constraints for the cast (term ends, age at fork, statutory limits,
health if publicly known before the fork). Do not include actual post-fork outcomes here (those belong in Task A).

Keep it compact: rationale ≤ 25 words each; omit "headline" (the id is enough).
Return JSON:
{"causal_map": [{"event_id": "...", "date": "...", "verdict": "independent|dependent|contingent",
  "p": 0.0-1.0, "rationale": "≤ 25 words", "interceptable_by": ["cast names or 'none'"]}],
 "structural": [{"date": "YYYY-MM-DD", "event": "...", "kind": "election|term_end|scheduled|trend|lifecycle",
  "actor": "name if it concerns one actor", "note": "what must be resolved"}],
 "notes": "2-3 sentences on where the biggest uncertainty lies"}"""

CAUSAL_MAP_USER = """Scenario: {title}
Fork date: {fork_date}    Horizon: {horizon}
PREMISE: {premise}
Cast: {cast}

REAL EVENTS AFTER THE FORK (actual timeline / parent lane):
{events}"""


NEW_ACTOR_SYS = """You are casting one additional actor who has just entered a running simulation. Knowledge cutoff for
what the actor knows: {cutoff} plus the branch timeline provided. Give a specific, behavioural profile in the same
schema as the rest of the cast. If a real named person fits the office at that time, use them.
Return JSON: {"name": "...", "role": "...", "goals": "...", "stance": "...", "style": "...", "resources": "...",
"background": "...", "playbook": "...", "relationships": "...", "red_lines": "..."}"""

NEW_ACTOR_USER = """Scenario: {title}
Who enters: {name} — {role}. Why now: {why}
Date: {date}
Existing cast: {cast}
Branch timeline so far:
{timeline}"""


AGGREGATE_SYS = """You aggregate several independent simulation runs of the same counterfactual branch. Code each run against
the analyst's question(s) and summarise the distribution honestly — the point is the spread across runs, not a single
story. Return JSON: {"outcome_questions": ["yes/no or categorical questions derived from the analyst's question"],
"per_run": [{"run": "run name", "answers": {"<question>": "yes|no|partial|n/a"}, "one_line": "what happened in this run"}],
"frequencies": {"<question>": {"yes": n, "no": n, "partial": n}},
"summary": "200-300 words: what is robust across runs, what varies and why, which junctures decide it",
"decisive_junctures": ["the junctures/dice rolls that most determine the answer"]}"""

AGGREGATE_USER = """Scenario: {title}
Analyst's question: {question}
Premise shared by all runs: {premise}

RUNS:
{runs}"""



# ====================================================================== calibration
CALIBRATE_SYS = """You score a simulation run against what ACTUALLY happened, to measure calibration. You are given the
run's junctures (each with the simulation's probability p_yes and the rolled outcome) and its events, plus the real
timeline for the same window. For each juncture, decide what really happened: "yes", "no", or "unknown" (if the
question does not map to a real outcome, e.g. it concerns a counterfactual office-holder). For each simulated event,
judge whether something like it really happened: "yes", "partly", "no", or "n/a" (premise-dependent).
Return JSON: {"junctures": [{"question": "...", "p_yes": 0.0, "actual": "yes|no|unknown", "note": "..."}],
"events": [{"headline": "...", "actual": "yes|partly|no|n/a", "note": "..."}],
"systematic_biases": ["patterns: e.g. over-predicts dramatic attacks; under-predicts institutional inertia"],
"summary": "3-5 sentences on how well this run tracked reality and where it diverged for reasons other than the premise"}"""

CALIBRATE_USER = """Scenario: {title}
Branch: {name} — premise: {premise}
Window: {start} → {end}

SIMULATED JUNCTURES:
{junctures}

SIMULATED EVENTS:
{events}

REAL TIMELINE FOR THE WINDOW:
{actual}"""



# ====================================================================== period critic
PERIOD_CRITIC_SYS = """You are the plausibility reviewer of a counterfactual simulation. Knowledge cutoff for the actors: {cutoff};
period {prev} → {date}. You receive the world model's draft adjudication (events, junctures, actor updates) together with
the context. Find and fix problems BEFORE they are committed:
1. LEAKAGE/ANACHRONISM: anything that relies on knowledge after the cutoff (technology, names, later events), or on the
   parent timeline's later course, or dates outside the period.
2. CAPABILITY: an actor doing something their office, resources or constraints do not allow (a senator "ordering" the
   FBI; a dead or retired actor acting; a minor actor moving markets).
3. MAGNITUDE & REPETITION: consequences too large or too small for the action; events that are re-runs of previous
   periods with no new information; junctures that re-ask a resolved question or ignore the hazard record.
4. CALIBRATION: juncture probabilities inconsistent with the base-rate note, the period length, or the hazard record.
5. OMISSIONS: an exogenous/structural event due this period that was not resolved; an obvious consequence of a
   committed event that is missing (a resignation after a scandal that everyone acknowledges, market reaction to a
   shock).
Return JSON: {"events": [{"index": n, "verdict": "keep|drop|rewrite", "reason": "...", "headline": "new headline if
rewrite", "summary": "new summary if rewrite"}], "junctures": [{"index": n, "verdict": "keep|drop|adjust",
"p_yes": 0.0-1.0 (if adjust), "reason": "..."}], "add_events": [{"date": "YYYY-MM-DD", "headline": "...", "summary":
"...", "actors": ["..."], "category": "...", "importance": 1-5, "reason": "why this was missing"}],
"notes": "one or two sentences"}
Be surgical: keep what is fine. Do not steer the story; enforce plausibility only."""

PERIOD_CRITIC_USER = """{premise_block}
BRIEFING (short): {briefing_short}

TIMELINE SO FAR:
{timeline}

EXOGENOUS/STRUCTURAL DUE THIS PERIOD:
{exogenous}

JUNCTURE HISTORY:
{juncture_history}

CAST ON STAGE: {cast}

ACTIONS THIS PERIOD:
{actions}

DRAFT ADJUDICATION (JSON):
{draft}"""


# ====================================================================== intervention planner
PLAN_SYS = """You are the planner for a counterfactual engine — the tool a time traveller would use. Given a target outcome
and a window, propose {k} candidate INTERVENTIONS: minimal, concrete, feasible changes at a specific date that a small
group of people could plausibly cause (a decision reversed, a warning acted on, a meeting that happens, a document
that reaches someone, a person who is or is not in a role), each with a short causal chain to the target. Prefer
interventions with a small footprint and high leverage; avoid magic (no "everyone agrees"). Vary the mechanism across
candidates (different actors, different levers, different dates).
Return JSON: {"candidates": [{"name": "<= 8 words", "date": "YYYY-MM-DD within the window", "premise": "the counterfactual
stated as a fact that becomes true on that date (1-3 sentences)", "who_acts": ["actors"], "mechanism": "causal chain to
the target in 2-3 sentences", "footprint": 1-5 (1 = tiny change), "prior_plausibility": 0-1 (that the intervention itself
could have been engineered), "risks": "what else it could break"}]}"""

PLAN_USER = """Scenario: {title}
TARGET OUTCOME to achieve by {deadline}: {target}
Intervention window: {start} → {deadline}
What actually happened in the window (parent lane):
{actual}
Cast: {cast}
Analyst notes: {notes}"""

PLAN_REPORT_SYS = """You write the decision memo for an intervention search. For each candidate intervention you get its
Monte-Carlo results against the target (frequencies over runs, decisive junctures, one-liners per run). Rank the
candidates by success rate, then by footprint and prior plausibility; explain WHY the winners work and why the losers
fail, and what a decision-maker (or time traveller) should actually do, with timing. Be honest about run counts.
Return JSON: {"ranking": [{"name": "...", "success_rate": 0-1, "footprint": 1-5, "prior_plausibility": 0-1,
"why": "2-3 sentences"}], "recommendation": "150-250 words", "caveats": ["..."]}"""

PLAN_REPORT_USER = """Scenario: {title}
TARGET by {deadline}: {target}

CANDIDATES AND RESULTS:
{results}"""
