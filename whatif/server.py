"""FastAPI app: REST + SSE + static GUI. `whatif.server.run()` starts it in the background
(what the Colab notebook uses); `python -m whatif` runs it in the foreground."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import PRESETS, Settings, load_settings
from .engine import Engine, EventBus
from .llm import LLM
from .retrieval import Retriever, diagnose
from .store import Store

log = logging.getLogger("whatif.server")
STATIC = Path(__file__).parent / "static"

EXAMPLES = [
    {"title": "2008 financial crisis", "question": "How does the crisis unfold if key decisions in September 2008 go differently?",
     "anchor_date": "2008-03-01", "horizon_date": "2009-06-30", "step_days": 30, "n_agents": 6,
     "forks": ["Lehman Brothers is rescued with a government-backed sale on 14 September 2008.",
               "Congress passes TARP on the first vote (29 September 2008)."]},
    {"title": "Cuban Missile Crisis", "question": "How does the October 1962 confrontation play out under different choices?",
     "anchor_date": "1962-09-01", "horizon_date": "1963-01-31", "step_days": 2, "n_agents": 6,
     "forks": ["Kennedy orders air strikes on the missile sites instead of a naval quarantine (22 October 1962)."]},
    {"title": "Brexit referendum aftermath", "question": "What happens to UK–EU relations after June 2016 under different leadership choices?",
     "anchor_date": "2016-06-01", "horizon_date": "2017-06-30", "step_days": 14, "n_agents": 7,
     "forks": ["Boris Johnson stands for and wins the Conservative leadership in July 2016."]},
    {"title": "COVID-19 early response (Jan–Jun 2020)", "question": "How different is the first half of 2020 with earlier or later interventions?",
     "anchor_date": "2020-01-01", "horizon_date": "2020-06-30", "step_days": 7, "n_agents": 7,
     "forks": ["The WHO declares a pandemic on 1 February 2020 and most G20 states restrict travel that week."]},
    {"title": "OpenAI board crisis (Nov 2023)", "question": "What happens to OpenAI and the AI industry if the November 2023 board crisis resolves differently?",
     "anchor_date": "2023-10-01", "horizon_date": "2024-03-31", "step_days": 7, "n_agents": 6,
     "forks": ["Sam Altman is not reinstated; the board's decision stands and most staff join Microsoft."]},
]


def create_app(settings: Settings | None = None, max_rounds: int = 12) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="WhatIf Timelines", version="0.1.0")
    state = {"settings": settings}
    bus = EventBus()
    store = Store(settings.data_dir)
    note = lambda msg: bus.publish({"type": "log", "level": "warning", "msg": msg})  # noqa: E731
    llm = LLM(settings, on_call=lambda info: bus.publish({"type": "llm_call", **info}), on_note=note)
    retriever = Retriever(settings)
    engine = Engine(settings, store, llm, retriever, bus, max_rounds=max_rounds)
    app.state.engine = engine

    # ----------------------------------------------------------------- config
    @app.get("/api/config")
    async def get_config():
        return {"settings": engine.s.public(), "today": date.today().isoformat(), "max_rounds": engine.max_rounds,
                "presets": [{"key": p.key, "label": p.label, "base_url": p.base_url, "default_model": p.default_model,
                             "free_tier": p.free_tier, "signup": p.signup, "notes": p.notes, "key_env": p.key_env}
                            for p in PRESETS.values()],
                "stats": {"calls": engine.llm.calls, "tokens_in": engine.llm.tokens_in, "tokens_out": engine.llm.tokens_out}}

    @app.post("/api/config")
    async def set_config(req: Request):
        body = await req.json()
        provider = (body.get("provider") or "").strip() or None
        new = load_settings(provider=provider, api_key=(body.get("api_key") or "").strip() or None,
                            base_url=(body.get("base_url") or "").strip() or None,
                            model=(body.get("model") or "").strip() or None,
                            concurrency=body.get("concurrency"), rpm=body.get("rpm"))
        if provider and not body.get("api_key") and engine.s.provider == provider:
            new.api_key = engine.s.api_key  # keep the existing key when only the model changes
        if body.get("max_rounds"):
            engine.max_rounds = max(1, min(60, int(body["max_rounds"])))
        new.data_dir = engine.s.data_dir
        await engine.llm.aclose()
        engine.s = new
        engine.llm = LLM(new, on_call=lambda info: bus.publish({"type": "llm_call", **info}), on_note=note)
        engine.retriever = Retriever(new)
        state["settings"] = new
        ping = await engine.llm.ping()
        return {"settings": new.public(), "ping": ping, "max_rounds": engine.max_rounds}

    @app.get("/api/ping")
    async def ping():
        return await engine.llm.ping()

    @app.get("/api/models")
    async def models():
        try:
            return {"models": await engine.llm.list_models()}
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"models": [], "error": str(e)[:300]}, status_code=200)

    @app.get("/api/diag")
    async def diag():
        out = await diagnose(engine.s)
        out["llm"] = await engine.llm.ping()
        out["llm"]["provider"] = engine.s.provider
        out["llm"]["model"] = engine.s.model
        out["ok"] = out["ok"] and out["llm"]["ok"]
        return out

    @app.get("/api/examples")
    async def examples():
        return EXAMPLES

    # ----------------------------------------------------------------- scenarios
    @app.get("/api/scenarios")
    async def list_scenarios():
        return [s.public() for s in store.list()]

    @app.post("/api/scenarios")
    async def create_scenario(req: Request):
        body = await req.json()
        for k in ("title", "anchor_date", "horizon_date"):
            if not body.get(k):
                raise HTTPException(400, f"missing {k}")
        try:
            sc = engine.create_scenario(body)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return sc.to_dict()

    @app.get("/api/scenarios/{sid}")
    async def get_scenario(sid: str, full: int = 0):
        sc = store.get(sid)
        if not sc:
            raise HTTPException(404, "scenario not found")
        d = sc.to_dict()
        if not full:
            d["docs"] = [{k: v for k, v in doc.items() if k != "text"} | {"chars": len(doc.get("text") or "")} for doc in d["docs"]]
        return d

    @app.delete("/api/scenarios/{sid}")
    async def delete_scenario(sid: str):
        sc = store.get(sid)
        if sc:
            for bid in sc.branches:
                engine.stop(bid)
        return {"deleted": store.delete(sid)}

    @app.post("/api/scenarios/{sid}/fork")
    async def fork(sid: str, req: Request):
        body = await req.json()
        try:
            br = engine.fork(sid, body.get("parent_branch_id"), body.get("fork_event_id"), body.get("premise", ""),
                             name=body.get("name", ""), fork_date=body.get("fork_date") or None,
                             step_days=int(body["step_days"]) if body.get("step_days") else None,
                             max_rounds=int(body["max_rounds"]) if body.get("max_rounds") else None)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        sc = store.get(sid)
        return {"branch_id": br.id, "scenario": sc.to_dict()}

    @app.post("/api/scenarios/{sid}/branches/{bid}/stop")
    async def stop_branch(sid: str, bid: str):
        return {"stopped": engine.stop(bid)}

    @app.delete("/api/scenarios/{sid}/branches/{bid}")
    async def delete_branch(sid: str, bid: str):
        sc = store.get(sid)
        if not sc or bid not in sc.branches:
            raise HTTPException(404, "not found")
        if bid == sc.baseline_branch_id:
            raise HTTPException(400, "cannot delete the baseline")
        # delete descendants too
        doomed = {bid}
        changed = True
        while changed:
            changed = False
            for b in sc.branches.values():
                if b.parent_branch_id in doomed and b.id not in doomed:
                    doomed.add(b.id)
                    changed = True
        for d in doomed:
            engine.stop(d)
            sc.branches.pop(d, None)
        store.save(sc)
        return {"deleted": sorted(doomed)}

    @app.post("/api/scenarios/{sid}/branches/{bid}/interview")
    async def interview(sid: str, bid: str, req: Request):
        body = await req.json()
        try:
            return await engine.interview(sid, bid, body.get("persona_id", ""), body.get("question", ""))
        except KeyError as e:
            raise HTTPException(404, str(e))

    @app.get("/api/scenarios/{sid}/compare")
    async def compare(sid: str, a: str, b: str):
        try:
            return await engine.compare(sid, a, b)
        except KeyError as e:
            raise HTTPException(404, str(e))

    # ----------------------------------------------------------------- SSE
    @app.get("/api/events")
    async def events(req: Request):
        q = bus.subscribe()

        async def gen():
            try:
                yield "retry: 3000\n\n"
                for m in bus.recent[-30:]:
                    yield f"data: {json.dumps(m)}\n\n"
                while True:
                    if await req.is_disconnected():
                        break
                    try:
                        m = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(m)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                bus.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/health")
    async def health():
        return {"ok": True, "provider": engine.s.provider, "model": engine.s.model}

    # ----------------------------------------------------------------- static
    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


# ---------------------------------------------------------------------- runners
_server_thread: threading.Thread | None = None


def run(port: int = 8000, host: str = "0.0.0.0", background: bool = False, open_colab: bool = True,
        max_rounds: int = 12, log_level: str = "warning", **settings_overrides):
    """Start the server. In Colab call `run(background=True)`; it prints a proxied URL you can open
    in a new tab (or shows the UI inline with `show()`)."""
    import uvicorn

    settings = load_settings(**settings_overrides)
    app = create_app(settings, max_rounds=max_rounds)
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level, loop="asyncio")
    server = uvicorn.Server(config)

    if not background:
        print(f"WhatIf Timelines → http://localhost:{port}   provider={settings.provider} model={settings.model}")
        server.run()
        return None

    global _server_thread
    import urllib.request
    try:  # already running from an earlier cell? just hand back the URL
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
        already = True
    except Exception:  # noqa: BLE001
        already = False

    def _serve():
        asyncio.run(server.serve())

    if not already:
        _server_thread = threading.Thread(target=_serve, daemon=True, name="whatif-server")
        _server_thread.start()
    # wait until it answers
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.25)
    url = f"http://localhost:{port}"
    if open_colab:
        try:
            from google.colab.output import eval_js  # type: ignore
            url = eval_js(f"google.colab.kernel.proxyPort({port})")
        except Exception:  # noqa: BLE001
            pass
    print(f"WhatIf Timelines is {'already ' if already else ''}running.\n  provider={settings.provider}  model={settings.model}\n  open: {url}")
    return url


def show(port: int = 8000, height: int = 900):
    """Render the GUI inline in a Colab cell (the proxy handles the URL)."""
    try:
        from google.colab.output import serve_kernel_port_as_iframe  # type: ignore
        serve_kernel_port_as_iframe(port, height=height)
    except Exception:  # noqa: BLE001
        from IPython.display import IFrame, display  # type: ignore
        display(IFrame(f"http://localhost:{port}", width="100%", height=height))
