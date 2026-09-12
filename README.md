# ⑂ WhatIf Timelines

Counterfactual forecasting with an LLM **agent swarm**, on a timeline GUI where alternate histories fork off a
baseline like branches in a git graph. MiroFish-inspired (retrieve → cast personas → simulate → report), built to run
in **Google Colab** on `localhost`, with **any OpenAI-compatible model** — an OpenAI key or a free tier
(Cerebras, Groq, Gemini, OpenRouter) or local Ollama.

![overview](docs/overview.png)

## What it does

1. **Retrieve, date-cut.** For your topic the server pulls
   * English Wikipedia article **revisions as they existed on the cutoff date** (MediaWiki revision history) — so
     agents can't peek at the ending;
   * **GDELT** news headlines in the 120 days before the cutoff (2017 onward);
   * the **present-day** article text (used *only* to extract the actual-history baseline);
   * any **seed documents** you paste.
2. **Ground.** A leakage-filter pass rewrites the material into a briefing containing only what was knowable on the
   cutoff date, and lists what it stripped (visible in the GUI, so you can judge leakage risk yourself).
3. **Cast.** The model designs the 2–12 actors whose decisions drive the situation — heads of government, central
   banks, opposition, press, markets, foreign capitals… These are the agents.
4. **Baseline.** Real events between your anchor date and today are extracted onto the grey *Actual history* lane.
   If the horizon is in the future, a *Baseline forecast* lane starts automatically from today.
5. **Fork.** Click any event → *What if…* → new lane. Every period all agents decide **concurrently** (observe →
   private reasoning → action → public statement), then an **arbiter / world model** adjudicates what actually
   happens, scores each event's **divergence from the parent lane** in the same window, updates indicators
   (tension, public support, economic stress) and each agent's memory. Branches can be forked from branches.
6. **Analyse.** Each finished lane gets a report: narrative, mechanism, probability estimate, key divergences,
   signposts, whether it converges back to the parent. **Interview** any agent inside any lane; **compare** two lanes.

## Run in Colab

Open `notebooks/WhatIf_Timelines_Colab.ipynb` in Colab, *Runtime → Run all*, follow the printed link.
Keys are read from Colab Secrets (`OPENAI_API_KEY`, `CEREBRAS_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`,
`OPENROUTER_API_KEY`) or pasted in the notebook or in the GUI's provider dialog.

## Run locally

```bash
pip install -r requirements.txt          # or: pip install -e .
cp .env.example .env                      # set WHATIF_PROVIDER + LLM_API_KEY
python -m whatif --port 8000              # → http://localhost:8000
# or, no key at all, to click around:
python -m whatif --provider mock
```

Env vars use the same names as go-mirofish (`LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL_NAME`) plus
`WHATIF_PROVIDER` for a preset. Everything can also be changed at runtime from the provider chip in the top bar.

| preset | base URL | default model | cost |
|---|---|---|---|
| `openai` | api.openai.com | gpt-4o-mini | paid (≈ $0.05–0.10 per branch with 4o-mini) |
| `cerebras` | api.cerebras.ai | llama-3.3-70b | free tier — best free choice |
| `groq` | api.groq.com/openai | llama-3.3-70b-versatile | free tier (low tokens/min → slower) |
| `gemini` | generativelanguage.googleapis.com/…/openai | gemini-2.5-flash | free tier (low requests/day) |
| `openrouter` | openrouter.ai/api | any `…:free` model | free models, low daily cap |
| `ollama` | localhost:11434 | llama3.1 | local |
| `mock` | — | — | offline canned agents |

Any other OpenAI-compatible endpoint: set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_NAME`.

**Cost model.** One branch = `rounds × (agents + 1) + 3` LLM calls (default cap 12 rounds, 6 agents → ~90 calls).
Knobs: max rounds (per fork or globally), agents per scenario, `WHATIF_CONCURRENCY`, `WHATIF_RPM`,
`WHATIF_BRIEF_CHARS`. Free-tier presets default to concurrency 3 and a shorter briefing.

## API

| | |
|---|---|
| `POST /api/scenarios` | `{title, question, anchor_date, horizon_date, step_days, n_agents, wiki_titles?, user_docs?}` |
| `GET /api/scenarios/{id}` | full state: personas, docs, branches, events, reports, log |
| `POST /api/scenarios/{id}/fork` | `{parent_branch_id, fork_event_id?, fork_date?, premise, name?, max_rounds?, step_days?}` |
| `POST …/branches/{bid}/stop`, `DELETE …/branches/{bid}` | |
| `POST …/branches/{bid}/interview` | `{persona_id, question}` |
| `GET …/compare?a=&b=` | LLM comparison of two lanes |
| `GET /api/events` | SSE stream (log lines, agent actions, LLM calls, progress) |
| `GET/POST /api/config`, `GET /api/models`, `GET /api/ping` | provider management |

Scenarios persist as JSON in `data/scenarios/`.

## Layout

```
whatif/
  config.py     provider presets + settings (env-driven)
  llm.py        OpenAI-compatible async client: concurrency, RPM limiter, retries, JSON repair, mock provider
  retrieval.py  Wikipedia as-of-date revisions, present-day extracts, GDELT, wikitext cleaning
  prompts.py    all prompt templates (librarian, leakage filter, historian, casting, agent, arbiter, report…)
  engine.py     orchestration: prepare scenario, fork, swarm round loop, reports, interviews, comparisons
  models.py     Scenario / Branch / Event / Persona dataclasses
  store.py      JSON persistence
  server.py     FastAPI app, SSE, static GUI, Colab runner
  static/       index.html, app.css, app.js — the timeline GUI (no build step)
notebooks/      Colab notebook (+ generator script)
tests/          smoke test (REST, mock provider) and a headless UI screenshot script
```

## Relationship to MiroFish / go-mirofish

go-mirofish's Go "native" simulation worker is a placeholder — its round loop emits `"twitter round N agent K"`
strings and interviews return `"Agent responds to …"`; only graph-building and reports call an LLM — and it
requires Docker plus a Zep Cloud key. This project keeps MiroFish's idea (seed documents → knowledge → personas →
swarm → report → interview) in ~1,500 lines of Python with no external services, and adds what the counterfactual
use-case needs: date-cut retrieval, an actual-history baseline, forkable branches with divergence scoring, and the
timeline GUI.

## Honest caveats

* Forecasts are model output, not evidence. Treat probabilities as the swarm's *opinion*.
* For topics before ~2002 there is no pre-event Wikipedia revision; the leakage filter is doing the work. Check the
  "stripped" notes in each lane's briefing.
* Free tiers rate-limit hard; the client backs off and honours `Retry-After`, so a branch may simply take longer.
  A retry-after longer than 3 minutes (a daily cap) fails the branch immediately with the provider's message.
* **OpenAI accounts without billing** are capped at ~200 requests/day for gpt-4o-mini — one branch. Adding $5 of
  credit (Tier 1) lifts that to 10,000/day. Check <https://platform.openai.com/settings/organization/limits>.
* GDELT is rate-limited per IP; from shared cloud egress (Colab) it is usually unavailable and is skipped.

## Publish to GitHub (first time)

From `~/project/whatif-timelines` in a terminal on your machine:

```powershell
gh repo create whatif-timelines --private --source=. --remote=origin --push
```

or without the GitHub CLI: create an empty repo named `whatif-timelines` on github.com, then

```powershell
git init -b main
git add .
git commit -m "WhatIf Timelines v0.1"
git remote add origin https://github.com/dwtrott/whatif-timelines.git
git push -u origin main
```

The Colab notebook clones `https://github.com/dwtrott/whatif-timelines.git` by default (a private repo needs the
`https://<user>:<token>@github.com/...` form or a public repo).
