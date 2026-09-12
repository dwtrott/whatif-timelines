"""Minimal async client for any OpenAI-compatible chat-completions endpoint.

- httpx only (no vendor SDK), so the same code path serves OpenAI, Groq, Gemini's
  OpenAI shim, OpenRouter, Cerebras, Ollama, LM Studio, vLLM ...
- Global concurrency semaphore + optional requests-per-minute limiter so free tiers
  survive a swarm of agents firing at once.
- Exponential backoff on 429 / 5xx / timeouts.
- `json()` asks for JSON and repairs the common failure modes (code fences, prose
  around the object, trailing commas).
- A deterministic `mock://` provider so the whole pipeline + GUI can be exercised
  with no key and no network.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import random
import re
import time
from collections import deque
from typing import Any, Callable

import httpx

from .config import Settings

log = logging.getLogger("whatif.llm")


class LLMError(RuntimeError):
    pass


class RateLimiter:
    """Sliding-window RPM limiter (no-op when rpm <= 0)."""

    def __init__(self, rpm: int):
        self.rpm = rpm
        self._times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def wait(self):
        if self.rpm <= 0:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._times and now - self._times[0] > 60:
                    self._times.popleft()
                if len(self._times) < self.rpm:
                    self._times.append(now)
                    return
                await asyncio.sleep(max(0.05, 60 - (now - self._times[0]) + 0.05))


class LLM:
    def __init__(self, settings: Settings, on_call: Callable[[dict], None] | None = None,
                 on_note: Callable[[str], None] | None = None):
        self.s = settings
        self.on_note = on_note
        self.sem = asyncio.Semaphore(max(1, settings.concurrency))
        self.limiter = RateLimiter(settings.rpm)
        self.on_call = on_call
        self.calls = 0
        self.inflight: dict[int, dict] = {}
        self.tokens_in = 0
        self.tokens_out = 0
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ plumbing
    @property
    def is_mock(self) -> bool:
        return self.s.provider == "mock" or self.s.base_url.startswith("mock")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.s.api_key:
            h["Authorization"] = f"Bearer {self.s.api_key}"
        if "openrouter" in self.s.base_url:
            h["HTTP-Referer"] = "https://github.com/whatif-timelines"
            h["X-Title"] = "WhatIf Timelines"
        return h

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.s.timeout, connect=20))
        return self._client

    async def aclose(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def list_models(self) -> list[str]:
        if self.is_mock:
            return ["mock-1"]
        c = await self.client()
        r = await c.get(self.s.base_url + "models", headers=self._headers())
        r.raise_for_status()
        data = r.json()
        items = data.get("data", data if isinstance(data, list) else [])
        ids = [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]
        return sorted(ids)

    async def ping(self) -> dict:
        """Cheap connectivity/auth check used by the UI."""
        if self.is_mock:
            return {"ok": True, "detail": "mock provider"}
        try:
            txt = await self.chat([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=5, temperature=0)
            return {"ok": True, "detail": txt.strip()[:40]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": str(e)[:300]}

    # ------------------------------------------------------------------ calls
    async def chat(self, messages: list[dict], *, temperature: float | None = None,
                   max_tokens: int = 1400, json_mode: bool = False, kind: str = "", ctx: dict | None = None,
                   model: str | None = None) -> str:
        if self.is_mock:
            await asyncio.sleep(0.05 + random.random() * 0.1)
            self.calls += 1
            out = mock_response(kind, messages, ctx or {})
            if self.on_call:
                self.on_call({"kind": kind, "model": "mock", "ms": 50, "tokens": 0})
            return out

        payload: dict[str, Any] = {
            "model": model or self.s.model,
            "messages": messages,
            "temperature": self.s.temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and _supports_json_mode(self.s.base_url):
            payload["response_format"] = {"type": "json_object"}

        delay = 2.0
        last_err: Exception | None = None
        key = id(payload)
        # long generations need long read timeouts: ~12 tok/s worst case + slack
        read_timeout = max(self.s.timeout, 40 + max_tokens * 0.09)
        self.inflight[key] = {"kind": kind, "started": time.time(), "stage": "queued (semaphore)", "attempt": 0,
                              "timeout_s": int(read_timeout)}
        try:
            return await self._chat_loop(payload, kind, delay, last_err, key, read_timeout)
        finally:
            self.inflight.pop(key, None)

    async def _chat_loop(self, payload, kind, delay, last_err, key, read_timeout):
        timeouts = 0
        for attempt in range(6):
            self.inflight[key]["attempt"] = attempt + 1
            async with self.sem:
                self.inflight[key]["stage"] = "rate limiter"
                await self.limiter.wait()
                self.inflight[key]["stage"] = "http post"
                t0 = time.monotonic()
                try:
                    c = await self.client()
                    post = asyncio.ensure_future(c.post(self.s.base_url + "chat/completions", headers=self._headers(),
                                                        json=payload, timeout=httpx.Timeout(read_timeout, connect=20)))
                    waited = 0
                    while True:  # watchdog: announce slow calls instead of silently waiting
                        try:
                            r = await asyncio.wait_for(asyncio.shield(post), 20)
                            break
                        except asyncio.TimeoutError:
                            waited += 20
                            if self.on_note:
                                self.on_note(f"LLM call '{kind or 'chat'}' still waiting on {self.s.base_url} after {waited}s "
                                             f"(attempt {attempt + 1})")
                            if waited >= read_timeout:
                                post.cancel()
                                raise httpx.ReadTimeout(f"no response after {waited}s")
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_err = e
                    if isinstance(e, httpx.TimeoutException):
                        timeouts += 1
                        if timeouts >= 2:  # a second full timeout means this request is too big/slow — give up fast
                            raise LLMError(f"LLM '{kind}' timed out twice ({int(read_timeout)}s each) on {self.s.base_url}. "
                                           f"Try a faster model or lower WHATIF_BRIEF_CHARS.") from e
                    log.warning("LLM transport error (%s), retry %d", e, attempt + 1)
                    if self.on_note:
                        self.on_note(f"LLM transport error ({type(e).__name__}); retry {attempt + 1}/6 in {delay:.0f}s")
                    await asyncio.sleep(delay + random.random())
                    delay = min(delay * 2, 40)
                    continue
                ms = int((time.monotonic() - t0) * 1000)

            self.inflight[key]["stage"] = f"got HTTP {r.status_code}"
            if r.status_code == 429 or r.status_code >= 500:
                last_err = LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                retry_after = r.headers.get("retry-after")
                wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else delay
                provider_msg = _provider_message(r.text)
                if wait > 180:
                    # a long retry-after means a daily/tier cap, not a burst — waiting silently helps nobody
                    raise LLMError(f"{self.s.provider} rate limit — retry-after {wait / 60:.0f} min: {provider_msg}. "
                                   f"Check the provider's usage/limits page (OpenAI accounts without billing get ~200 "
                                   f"requests/day), or switch provider in the GUI.")
                log.warning("LLM %s, waiting %.1fs (attempt %d)", r.status_code, wait, attempt + 1)
                if self.on_note:
                    self.on_note(f"LLM HTTP {r.status_code}: {provider_msg[:300]} — waiting {wait:.0f}s, retry {attempt + 1}/6")
                await asyncio.sleep(wait + random.random())
                delay = min(delay * 2, 40)
                continue
            if r.status_code == 400:
                body = r.text
                if "response_format" in payload and "response_format" in body:
                    payload.pop("response_format", None)  # provider doesn't support it; retry plain
                    continue
                if "max_tokens" in payload and "max_completion_tokens" in body:
                    payload["max_completion_tokens"] = payload.pop("max_tokens")  # o-series / gpt-5 style models
                    continue
                if "temperature" in payload and "temperature" in body and "unsupported" in body.lower():
                    payload.pop("temperature", None)
                    continue
            if r.status_code >= 400:
                raise LLMError(f"HTTP {r.status_code} from {self.s.base_url}: {r.text[:500]}")

            data = r.json()
            try:
                content = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as e:
                raise LLMError(f"Unexpected response shape: {str(data)[:300]}") from e
            usage = data.get("usage") or {}
            self.calls += 1
            self.tokens_in += int(usage.get("prompt_tokens") or 0)
            self.tokens_out += int(usage.get("completion_tokens") or 0)
            if self.on_call:
                self.on_call({"kind": kind, "model": payload["model"], "ms": ms,
                              "tokens": int(usage.get("total_tokens") or 0)})
            return content
        raise LLMError(f"LLM call failed after retries: {last_err}")

    async def json(self, system: str, user: str, *, kind: str = "", ctx: dict | None = None,
                   max_tokens: int = 1800, temperature: float | None = None, retries: int = 2,
                   strong: bool = False) -> dict:
        """strong=True routes to settings.strong_model when one is configured."""
        model = (self.s.strong_model or None) if strong else None
        messages = [
            {"role": "system", "content": system + "\n\nRespond with a single valid JSON object and nothing else."},
            {"role": "user", "content": user},
        ]
        last = ""
        for attempt in range(retries + 1):
            last = await self.chat(messages, json_mode=True, kind=kind, ctx=ctx, max_tokens=max_tokens,
                                   temperature=temperature, model=model)
            obj = extract_json(last)
            if obj is not None:
                return obj
            messages.append({"role": "assistant", "content": last})
            messages.append({"role": "user", "content": "That was not valid JSON. Return ONLY the JSON object."})
        raise LLMError(f"Model did not return JSON for {kind or 'request'}: {last[:200]}")


def _provider_message(text: str) -> str:
    """Pull the human message out of an error body if it is JSON; else return the raw text, compacted."""
    try:
        obj = _json.loads(text)
        err = obj.get("error", obj)
        msg = err.get("message") if isinstance(err, dict) else None
        if msg:
            return " ".join(str(msg).split())
    except Exception:  # noqa: BLE001
        pass
    return " ".join(text.split())[:400]


def _supports_json_mode(base_url: str) -> bool:
    # These are known to accept response_format json_object. Others get a plain request
    # (the prompt already demands JSON, and extract_json repairs the rest).
    return any(k in base_url for k in ("api.openai.com", "groq.com", "openrouter.ai", "cerebras.ai",
                                       "generativelanguage.googleapis.com", "deepseek.com", "together"))


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict | None:
    if not text:
        return None
    candidates = [text.strip()]
    m = _FENCE.search(text)
    if m:
        candidates.insert(0, m.group(1).strip())
    # widest {...} span
    i, j = text.find("{"), text.rfind("}")
    if i != -1 and j > i:
        candidates.append(text[i:j + 1])
    for c in candidates:
        for fixer in (lambda s: s, _strip_trailing_commas):
            try:
                obj = _json.loads(fixer(c))
                if isinstance(obj, dict):
                    return obj
            except Exception:  # noqa: BLE001
                continue
    return None


def _strip_trailing_commas(s: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", s)


# ---------------------------------------------------------------------- mock
_ACTORS = ["the government", "the central bank", "opposition leaders", "the press", "markets",
           "allied capitals", "the regulator", "the public", "industry lobby", "the courts"]
_VERBS = ["signals a shift on", "holds emergency talks over", "publicly rejects", "quietly backs",
          "escalates pressure on", "delays a decision on", "announces a review of", "reverses course on"]


def mock_response(kind: str, messages: list[dict], ctx: dict) -> str:
    """Deterministic-ish canned answers keyed on the request kind. Content is derived from
    ctx so branches with different premises visibly diverge."""
    seed = f"{kind}|{ctx.get('seed', '')}|{ctx.get('date', '')}|{ctx.get('name', '')}"
    rng = random.Random(seed)
    premise = ctx.get("premise") or "the baseline course of events"
    date = ctx.get("date", "")
    topic = ctx.get("topic", "the situation")

    def pick(xs, n=1):
        return rng.sample(xs, min(n, len(xs)))

    if kind == "articles":
        return _json.dumps({"titles": ctx.get("hint_titles", [])[:6]})
    if kind == "redact":
        return _json.dumps({"briefing": ctx.get("text", "")[:4000], "removed": []})
    if kind == "actual_events":
        n = 5
        evs = []
        for i, d in enumerate(ctx.get("dates", [])[:n]):
            evs.append({"date": d, "headline": f"{pick(_ACTORS)[0].capitalize()} {pick(_VERBS)[0]} {topic}",
                        "summary": f"Recorded development #{i + 1} in the historical record for {topic}.",
                        "actors": pick(_ACTORS, 2), "importance": rng.choice([2, 3, 4, 5])})
        return _json.dumps({"events": evs})
    if kind == "personas":
        roles = [("Head of Government", "keep coalition intact, project control"),
                 ("Central Bank Governor", "price stability, credibility"),
                 ("Opposition Leader", "force early elections"),
                 ("Investigative Editor", "break the story first"),
                 ("Foreign Minister of a key ally", "avoid contagion, keep alliance aligned"),
                 ("Industry Federation Chair", "protect margins and subsidies"),
                 ("Grassroots Organizer", "mobilize the street"),
                 ("Senior Regulator", "avoid blame, enforce rules")]
        ps = []
        for i, (r, g) in enumerate(roles[: ctx.get("n", 6)]):
            ps.append({"name": f"{r}", "role": r, "goals": g,
                       "stance": rng.choice(["hawkish", "cautious", "opportunistic", "principled"]),
                       "style": rng.choice(["terse", "rhetorical", "technocratic", "combative"]),
                       "resources": rng.choice(["formal authority", "media reach", "money", "votes", "information"])})
        return _json.dumps({"personas": ps})
    if kind == "agent_step":
        name = ctx.get("name", "Agent")
        return _json.dumps({
            "thoughts": f"Given {premise}, I expect {pick(_ACTORS)[0]} to move within days.",
            "action": f"{name} {pick(_VERBS)[0]} {topic}.",
            "statement": f"\"We will not be rushed on {topic}; the facts as of {date} speak for themselves.\"",
            "predicted_next": f"{pick(_ACTORS)[0].capitalize()} responds by {rng.choice(['convening a summit', 'leaking a memo', 'issuing sanctions', 'calling a vote'])}.",
        })
    if kind == "arbiter":
        k = rng.choice([1, 2, 2, 3])
        evs = []
        for _ in range(k):
            evs.append({
                "date": date,
                "headline": f"{pick(_ACTORS)[0].capitalize()} {pick(_VERBS)[0]} {topic}",
                "summary": f"Under the premise that {premise}, agent pressure converges here: {pick(_ACTORS)[0]} acts, "
                           f"{pick(_ACTORS)[0]} reacts. Consequences ripple into the next period.",
                "actors": pick(_ACTORS, 2),
                "category": rng.choice(["political", "economic", "security", "media", "legal"]),
                "confidence": round(rng.uniform(0.35, 0.9), 2),
                "divergence": round(rng.uniform(0.0, 1.0), 2) if ctx.get("has_parent") else 0.0,
                "importance": rng.choice([1, 2, 3, 4, 5]),
            })
        extra = {}
        if rng.random() < 0.5:
            extra["junctures"] = [{"question": f"Does {pick(_ACTORS)[0]} prevail on {topic}?", "p_yes": round(rng.uniform(0.2, 0.8), 2),
                                   "if_yes": {"headline": f"{pick(_ACTORS)[0].capitalize()} prevails", "summary": "mock yes"},
                                   "if_no": {"headline": f"{pick(_ACTORS)[0].capitalize()} is rebuffed", "summary": "mock no"}, "importance": 4, "actors": pick(_ACTORS, 1)}]
        if rng.random() < 0.25:
            extra["new_actors"] = [{"name": f"Successor {rng.randint(1, 99)}", "role": "new minister", "why_now": "vacancy"}]
        if rng.random() < 0.15:
            extra["exits"] = [ctx.get("name", "")]
        return _json.dumps({"events": evs, **extra,
                            "world_state": f"As of {date}: tension {rng.choice(['rising', 'plateauing', 'easing'])}; "
                                           f"the premise ({premise}) continues to shape incentives.",
                            "indicators": {"tension": round(rng.uniform(0.2, 0.95), 2),
                                           "public_support": round(rng.uniform(0.2, 0.8), 2),
                                           "economic_stress": round(rng.uniform(0.1, 0.9), 2)}})
    if kind == "report":
        return _json.dumps({
            "summary": f"On this branch ({premise}), the swarm converged on a {rng.choice(['slower', 'faster', 'more chaotic', 'more contained'])} trajectory than the parent timeline.",
            "key_divergences": [f"{pick(_ACTORS)[0].capitalize()} never {pick(_VERBS)[0]} {topic}",
                                f"Timing of the decisive move shifts by ~{rng.randint(1, 8)} periods"],
            "probability_estimate": round(rng.uniform(0.15, 0.7), 2),
            "what_changed": "Incentives for the pivotal actor changed at the fork; second-order effects followed.",
            "converges": rng.random() < 0.4,
            "convergence_note": "Structural pressures eventually pull outcomes back toward the parent path." if rng.random() < 0.5 else "The branch stays distinct through the horizon.",
        })
    if kind == "interview":
        return _json.dumps({"answer": f"Speaking as {ctx.get('name', 'this actor')} on {date}: {premise} changed my calculus. "
                                      f"I would now prioritize {rng.choice(['de-escalation', 'a show of strength', 'buying time', 'a leak to the press'])}."})
    if kind == "compare":
        return _json.dumps({"comparison": f"Branch A and Branch B differ mainly in how {pick(_ACTORS)[0]} behaves after the fork.",
                            "points": [{"dimension": "Pace", "a": "gradual", "b": "abrupt"},
                                       {"dimension": "Winner", "a": pick(_ACTORS)[0], "b": pick(_ACTORS)[0]},
                                       {"dimension": "End state", "a": "fragile stability", "b": "open crisis"}]})
    if kind == "causal_map":
        ids, dates = ctx.get("ids", []), ctx.get("dates", [])
        cm = []
        for i, (eid, d) in enumerate(zip(ids, dates)):
            v = ["independent", "contingent", "dependent"][i % 3]
            cm.append({"event_id": eid, "date": d, "verdict": v, "p": {"independent": 0.9, "contingent": 0.5, "dependent": 0.1}[v],
                       "rationale": "mock rationale", "interceptable_by": ["the government"] if v != "dependent" else []})
        return _json.dumps({"causal_map": cm, "structural": [{"date": dates[-1] if dates else "2100-01-01", "event": "Scheduled election", "kind": "election", "actor": "", "note": "must be resolved"}],
                            "notes": "mock uncertainty note"})
    if kind == "new_actor":
        return _json.dumps({"name": ctx.get("name", "New Actor"), "role": "successor", "goals": "consolidate", "stance": "cautious", "style": "terse",
                            "resources": "office", "background": "mock", "playbook": "wait, then strike", "relationships": "rival of incumbent", "red_lines": "humiliation"})
    if kind == "aggregate":
        n = ctx.get("n", 1)
        return _json.dumps({"outcome_questions": ["Did the crisis escalate?"], "per_run": [{"run": f"run {i+1}", "answers": {"Did the crisis escalate?": "yes" if i % 2 == 0 else "no"}, "one_line": "mock"} for i in range(n)],
                            "frequencies": {"Did the crisis escalate?": {"yes": (n + 1) // 2, "no": n // 2, "partial": 0}},
                            "summary": "Mock aggregate summary.", "decisive_junctures": ["the election roll"]})
    if kind == "calibrate":
        return _json.dumps({"junctures": [{"question": "mock", "p_yes": 0.4, "actual": "no", "note": "mock"}],
                            "events": [{"headline": "mock", "actual": "partly", "note": "mock"}],
                            "systematic_biases": ["mock bias"], "summary": "mock calibration summary"})
    if kind == "research_plan":
        return _json.dumps({"is_individual": True, "wikipedia_titles": [ctx.get("name", "")], "queries": [], "own_domains": []})
    if kind == "evidence":
        return _json.dumps({"evidence": [{"date": "2019", "type": "precedent", "claim": f"{ctx.get('name')} faced a similar showdown and escalated.", "quote": "We do not blink.", "weight": 0.7}]})
    if kind == "dossier":
        n = ctx.get("name", "Actor")
        return _json.dumps({"summary": f"{n} is a fast-moving operator who escalates when cornered and builds coalitions quickly.",
                            "precedents": [{"when": "2019", "situation": "board dispute", "what_they_did": "rallied staff and funders", "outcome": "prevailed", "source": "mock://"}],
                            "operational_code": {"view_of_adversaries": "obstacles to route around", "control_over_events": "high", "risk_orientation": "risk-seeking when losing", "preferred_strategy": "coalition + speed", "tactics": "leaks, ultimatums", "timing": "moves within days", "use_of_pressure_vs_cooperation": "pressure first"},
                            "leadership_traits": {"belief_in_control": "high", "need_for_power": "high", "conceptual_complexity": "medium", "self_confidence": "high", "task_vs_relationship_focus": "task", "distrust_of_others": "medium", "in_group_bias": "medium"},
                            "decision_style": {"speed": "fast", "consultation": "small inner circle", "public_vs_private": "public when useful", "escalation_pattern": "rapid", "response_to_threat": "fight"},
                            "stated_commitments": ["never accept a demotion"], "relationships": [{"with": "the board", "nature": "rival", "leverage": "staff loyalty", "source": "mock://"}],
                            "pressure_points": ["loyalty of key staff"], "constraints": ["contract terms"], "voice": {"style": "terse, confident", "quotes": ["We do not blink. (2019)"]},
                            "playbook": ["rally staff", "line up a rival home", "public ultimatum"], "red_lines": ["being sidelined"], "confidence": 0.6, "gaps": ["mock data"]})
    if kind == "dossier_critic":
        return _json.dumps({"leakage": [], "unsupported": [], "verdict": "mock"})
    if kind == "ground":
        return _json.dumps({"context": f"Background on {topic} as of {date}: key actors are positioning; the situation is fluid.",
                            "open_questions": ["Who blinks first?", "Does the coalition hold?"]})
    return _json.dumps({"ok": True, "text": "OK"})
