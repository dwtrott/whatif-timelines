"""Generates WhatIf_Timelines_Colab.ipynb (kept as a script so the notebook is reproducible)."""
import json
from pathlib import Path

cells = []


def md(src):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": src})


def code(src):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": src})


md("""# ⑂ WhatIf Timelines — counterfactual forecasting with an agent swarm

Build a **baseline timeline** from real, date-cut retrieved sources (Wikipedia revisions *as of* the cutoff date, GDELT headlines, your own documents), then click any event and ask **what if…** — a swarm of LLM agents, each knowing only what was knowable at that date, plays the alternate timeline forward while an arbiter "world model" adjudicates each period. Alternate timelines render as lanes forking off the baseline, git-graph style.

Works with an **OpenAI key** or a **free tier** (Cerebras, Groq, Gemini, OpenRouter) — anything OpenAI-compatible.

**Runtime → Run all**, then open the link printed by step 3 (or use the inline view in step 4).""")

md("""## 1 · Get the code
Either point `REPO_URL` at your git repo containing this project, or leave it empty and upload `whatif-timelines.zip` when prompted.""")

code("""REPO_URL = "https://github.com/dwtrott/whatif-timelines.git"   # set to "" to upload the zip instead

import os, sys, subprocess, pathlib, shutil
if REPO_URL:
    if not pathlib.Path("whatif-timelines").exists():
        subprocess.run(["git", "clone", "--depth", "1", REPO_URL, "whatif-timelines"], check=True)
    else:
        subprocess.run(["git", "-C", "whatif-timelines", "pull", "--ff-only"], check=False)
    ROOT = pathlib.Path("whatif-timelines").resolve()
elif pathlib.Path("whatif").is_dir():
    ROOT = pathlib.Path(".").resolve()
elif list(pathlib.Path("whatif-timelines").rglob("pyproject.toml")):
    ROOT = next(pathlib.Path("whatif-timelines").rglob("pyproject.toml")).parent.resolve()
else:
    from google.colab import files
    print("Upload whatif-timelines.zip …")
    up = files.upload()
    name = next(iter(up))
    shutil.unpack_archive(name, "whatif-timelines")
    inner = [p for p in pathlib.Path("whatif-timelines").rglob("pyproject.toml")]
    ROOT = inner[0].parent.resolve() if inner else pathlib.Path("whatif-timelines").resolve()

os.chdir(ROOT); sys.path.insert(0, str(ROOT))
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], check=True)
print("ready in", ROOT)""")

md("""## 2 · Choose a model provider
Put your key in **Colab Secrets** (🔑 icon in the left sidebar) under one of these names and it is picked up automatically:
`OPENAI_API_KEY`, `CEREBRAS_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, plus `EXA_API_KEY` for date-cut actor research. Or paste it below.

| provider | cost | notes |
|---|---|---|
| `openai` | paid, cheap with `gpt-4o-mini` (~$0.05–0.10 per branch) | best quality/robustness |
| `cerebras` | **free tier** | fast, generous daily token budget — best free option |
| `groq` | free tier | fast; tight tokens-per-minute cap → slower with many agents |
| `gemini` | free tier | `gemini-2.5-flash`; low daily request cap on free tier |
| `openrouter` | free models (`…:free`) | rotating list, low daily cap without credit |
| `ollama` | free, local | GPU runtime: `!curl -fsSL https://ollama.com/install.sh \\| sh` then `ollama serve &` |
| `mock` | free, offline | canned agents — just to try the GUI |

You can also change provider/key/model later from the **provider chip** in the GUI's top bar.""")

code("""PROVIDER = "openai"      # openai | cerebras | groq | gemini | openrouter | ollama | mock
API_KEY  = ""            # leave empty to use a Colab secret / env var
MODEL    = ""            # agents' model; empty = preset default (gpt-4o-mini, llama-3.3-70b, gemini-2.5-flash …)
STRONG_MODEL = "gpt-4.1" # judgement roles (casting, arbiter, reports, history). "" = same as MODEL. OpenAI only by default.
MAX_ROUNDS = 12          # simulation periods per branch (cost ≈ rounds × (agents+1) LLM calls)
RESEARCH_DEPTH = "standard"  # actor dossiers: off | quick | standard | deep  (≈ 8-14 LLM calls per actor)
EXA_API_KEY = ""         # optional but recommended: free key at https://exa.ai — true date-cut web search for dossiers

import os
os.environ["WHATIF_RESEARCH_DEPTH"] = RESEARCH_DEPTH
if EXA_API_KEY: os.environ["EXA_API_KEY"] = EXA_API_KEY
os.environ["WHATIF_PROVIDER"] = PROVIDER
if API_KEY: os.environ["LLM_API_KEY"] = API_KEY
if MODEL:   os.environ["LLM_MODEL_NAME"] = MODEL
if STRONG_MODEL and PROVIDER == "openai": os.environ["WHATIF_STRONG_MODEL"] = STRONG_MODEL
# pull keys from Colab secrets if present
try:
    from google.colab import userdata
    for name in ["OPENAI_API_KEY", "CEREBRAS_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "LLM_API_KEY", "EXA_API_KEY", "SERPER_API_KEY"]:
        try:
            v = userdata.get(name)
            if v and not os.environ.get(name): os.environ[name] = v
        except Exception:
            pass
except ImportError:
    pass
print("provider:", PROVIDER)""")

md("""## 3 · Launch the server
The server runs in the background inside this runtime; Colab proxies `localhost:8000` to a private URL for you.""")

code("""from whatif.server import run
url = run(port=8000, background=True, max_rounds=MAX_ROUNDS)
print("\\n➡  Open in a new tab:", url)""")

md("""## 3b · Diagnostics — run this if a scenario comes back empty
Probes Wikipedia (search, as-of revision, present-day text), GDELT and your model with known-good requests, and prints the server's recent log.""")

code("""import requests, json
d = requests.get("http://localhost:8000/api/diag", timeout=120).json()
for k in ["wikipedia_search", "wikipedia_asof", "wikipedia_latest", "gdelt", "llm"]:
    v = d.get(k, {}); print(("OK  " if v.get("ok") else "FAIL"), k, "→", v.get("detail"))
print("user-agent:", d.get("user_agent"))
print("\nbackground tasks:", json.dumps(requests.get("http://localhost:8000/api/debug/tasks").json(), indent=1)[:2500])
# most recent scenario's log
scs = requests.get("http://localhost:8000/api/scenarios").json()
if scs:
    sc = requests.get(f"http://localhost:8000/api/scenarios/{scs[0]['id']}").json()
    print(f"\nScenario '{sc['title']}' status={sc['status']} error={sc['error']!r} docs={len(sc['docs'])} personas={len(sc['personas'])}")
    for l in sc["log"][-25:]: print(f"  [{l['level']}] {l['msg']}")""")

md("""## 4 · (Optional) Show the GUI inline
The proxied page also works embedded in the notebook. A separate tab is roomier.""")

code("""from whatif.server import show
show(port=8000, height=950)""")

md("""## 5 · Or drive it from Python
Everything the GUI does is a REST call. Example: create a scenario, wait, fork a counterfactual, read the report.""")

code("""import requests, time
B = "http://localhost:8000"

sc = requests.post(f"{B}/api/scenarios", json={
    "title": "2008 financial crisis",
    "question": "How does the crisis unfold if September 2008 goes differently?",
    "anchor_date": "2008-03-01", "horizon_date": "2009-06-30", "step_days": 30, "n_agents": 6,
}).json()
sid = sc["id"]
while requests.get(f"{B}/api/scenarios/{sid}").json()["status"] in ("new", "retrieving"):
    time.sleep(3)
sc = requests.get(f"{B}/api/scenarios/{sid}").json()
print(sc["status"], "| agents:", [p["name"] for p in sc["personas"]])
base = sc["branches"][sc["baseline_branch_id"]]
for e in base["events"]: print(" ", e["date"], e["headline"])

# fork from the event closest to 2008-09-14
ev = min(base["events"], key=lambda e: abs((__import__('datetime').date.fromisoformat(e["date"]) - __import__('datetime').date(2008, 9, 14)).days))
r = requests.post(f"{B}/api/scenarios/{sid}/fork", json={"parent_branch_id": base["id"], "fork_event_id": ev["id"],
        "premise": "Lehman Brothers is rescued through a government-backed sale on 14 September 2008."}).json()
bid = r["branch_id"]
while (br := requests.get(f"{B}/api/scenarios/{sid}").json()["branches"][bid])["status"] in ("pending", "retrieving", "running"):
    print(f"  round {br['rounds_done']}/{br['total_rounds']}", end="\\r"); time.sleep(5)
print("\\n", br["status"], "p≈", br["report"].get("probability_estimate"))
print(br["report"].get("summary"))""")

md("""## Notes
* **Knowledge cutoff.** For topics after ~2002 the agents read the Wikipedia article *as it was on the fork date*. Older topics get present-day text passed through an LLM leakage filter — open a lane's *Briefing* section to see what it stripped and judge the leakage risk yourself.
* **Cost.** One branch ≈ `rounds × (agents + 1) + 3` LLM calls. Lower `MAX_ROUNDS`, the number of agents, or `WHATIF_BRIEF_CHARS` for free tiers.
* **Persistence.** Scenarios are JSON files under `data/scenarios/`. Copy that folder to Drive to keep them across runtimes.
* **Public link.** The Colab proxy URL only works for you while signed in. For a shareable link run `!pip install pyngrok` and `from pyngrok import ngrok; print(ngrok.connect(8000))`.""")

nb = {"cells": cells, "metadata": {"colab": {"name": "WhatIf Timelines", "provenance": []},
                                   "kernelspec": {"name": "python3", "display_name": "Python 3"},
                                   "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 5}
out = Path(__file__).parent / "WhatIf_Timelines_Colab.ipynb"
out.write_text(json.dumps(nb, indent=1))
print("wrote", out)
