"""Provider presets and runtime configuration.

Everything is driven by three environment variables (same names go-mirofish uses,
so a .env from that project drops straight in):

    LLM_API_KEY      API key for the provider ("" is fine for ollama / mock)
    LLM_BASE_URL     OpenAI-compatible base URL (ends in /v1 or equivalent)
    LLM_MODEL_NAME   model id

Or pick a preset with WHATIF_PROVIDER=openai|groq|gemini|openrouter|cerebras|ollama|mock
and only supply the key. Explicit LLM_* variables always win over the preset.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProviderPreset:
    key: str
    label: str
    base_url: str
    default_model: str
    key_env: str            # conventional env var people already have for this vendor
    free_tier: bool
    signup: str
    notes: str = ""
    # conservative defaults for free tiers so the swarm doesn't trip rate limits
    concurrency: int = 4
    rpm: int = 0            # 0 = unknown / not enforced


PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        key="openai", label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        key_env="OPENAI_API_KEY", free_tier=False,
        signup="https://platform.openai.com/api-keys",
        notes="Paid. gpt-4o-mini is cheap and fast; gpt-4.1 / o-series for better judgement.",
        concurrency=8,
    ),
    "groq": ProviderPreset(
        key="groq", label="Groq (free tier)",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        key_env="GROQ_API_KEY", free_tier=True,
        signup="https://console.groq.com/keys",
        notes="Free tier is fast but rate-limited (~30 req/min); keep concurrency low.",
        concurrency=3, rpm=28,
    ),
    "gemini": ProviderPreset(
        key="gemini", label="Google Gemini (free tier)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_model="gemini-2.5-flash",
        key_env="GEMINI_API_KEY", free_tier=True,
        signup="https://aistudio.google.com/apikey",
        notes="Free tier via AI Studio; OpenAI-compatible endpoint. Same default go-mirofish ships with.",
        concurrency=3, rpm=10,
    ),
    "openrouter": ProviderPreset(
        key="openrouter", label="OpenRouter (free models)",
        base_url="https://openrouter.ai/api/v1",
        default_model="meta-llama/llama-3.3-70b-instruct:free",
        key_env="OPENROUTER_API_KEY", free_tier=True,
        signup="https://openrouter.ai/keys",
        notes="Any model id ending in ':free' costs nothing; the free list rotates, so check the Models page.",
        concurrency=3, rpm=18,
    ),
    "cerebras": ProviderPreset(
        key="cerebras", label="Cerebras (free tier)",
        base_url="https://api.cerebras.ai/v1",
        default_model="llama-3.3-70b",
        key_env="CEREBRAS_API_KEY", free_tier=True,
        signup="https://cloud.cerebras.ai/",
        notes="Very fast inference, generous free tier.",
        concurrency=3, rpm=28,
    ),
    "ollama": ProviderPreset(
        key="ollama", label="Ollama (local)",
        base_url="http://localhost:11434/v1",
        default_model="llama3.1",
        key_env="", free_tier=True,
        signup="https://ollama.com/download",
        notes="Local models; no key. In Colab you can `!curl -fsSL https://ollama.com/install.sh | sh` on a GPU runtime.",
        concurrency=2,
    ),
    "mock": ProviderPreset(
        key="mock", label="Mock (no network, for testing the UI)",
        base_url="mock://",
        default_model="mock-1",
        key_env="", free_tier=True,
        signup="",
        notes="Deterministic canned agents. Lets you exercise the whole pipeline and GUI with no key at all.",
        concurrency=16,
    ),
}


@dataclass
class Settings:
    provider: str
    api_key: str
    base_url: str
    model: str
    concurrency: int
    rpm: int
    strong_model: str = ""       # optional bigger model for judgement roles (casting, arbiter, report, compare, history)
    exa_api_key: str = ""        # optional: date-cut web search for dossiers (exa.ai)
    serper_api_key: str = ""     # optional: Google via serper.dev (date-range search)
    research_depth: str = "standard"   # off | quick | standard | deep
    critic: bool = True          # plausibility reviewer pass on every period (one extra strong call per period)
    timeout: float = 120.0
    temperature: float = 0.7
    data_dir: str = "data"
    # retrieval
    wiki_lang: str = "en"
    max_wiki_articles: int = 6
    max_chars_per_article: int = 6000
    gdelt_max_records: int = 25
    brief_chars: int = 9000      # how much of the briefing each agent sees per call (free tiers: smaller)
    # Wikimedia requires a descriptive UA with contact info, and blocks generic ones from cloud IPs (Colab!).
    user_agent: str = "WhatIfTimelines/0.1 (https://github.com/dwtrott/whatif-timelines; research prototype) python-httpx"
    extra: dict = field(default_factory=dict)

    @property
    def preset(self) -> ProviderPreset | None:
        return PRESETS.get(self.provider)

    def public(self) -> dict:
        """Safe-to-serve view (no key)."""
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "strong_model": self.strong_model,
            "has_exa": bool(self.exa_api_key), "has_serper": bool(self.serper_api_key),
            "research_depth": self.research_depth, "critic": self.critic,
            "concurrency": self.concurrency,
            "rpm": self.rpm,
            "has_key": bool(self.api_key) or self.provider in ("ollama", "mock"),
            "data_dir": self.data_dir,
        }


def _env(*names: str, default: str = "") -> str:
    for n in names:
        if not n:
            continue
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return default


def load_settings(**overrides) -> Settings:
    provider = overrides.pop("provider", None) or _env("WHATIF_PROVIDER") or ""
    base_url = overrides.pop("base_url", None) or _env("LLM_BASE_URL")
    model = overrides.pop("model", None) or _env("LLM_MODEL_NAME", "LLM_MODEL")
    api_key = overrides.pop("api_key", None) or _env("LLM_API_KEY")

    # Infer the provider from the base URL, or default sensibly.
    if not provider:
        if base_url:
            for p in PRESETS.values():
                if p.base_url and p.base_url.split("//")[-1].split("/")[0] in base_url:
                    provider = p.key
                    break
            provider = provider or "custom"
        elif _env("OPENAI_API_KEY"):
            provider = "openai"
        elif _env("GROQ_API_KEY"):
            provider = "groq"
        elif _env("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            provider = "gemini"
        elif _env("OPENROUTER_API_KEY"):
            provider = "openrouter"
        elif _env("CEREBRAS_API_KEY"):
            provider = "cerebras"
        else:
            provider = "mock"

    preset = PRESETS.get(provider)
    if preset:
        base_url = base_url or preset.base_url
        model = model or preset.default_model
        api_key = api_key or _env(preset.key_env, "GOOGLE_API_KEY" if provider == "gemini" else "")
        concurrency = preset.concurrency
        rpm = preset.rpm
    else:
        concurrency, rpm = 4, 0

    concurrency = int(overrides.pop("concurrency", None) or _env("WHATIF_CONCURRENCY") or concurrency)
    rpm = int(overrides.pop("rpm", None) or _env("WHATIF_RPM") or rpm)

    brief_chars = int(_env("WHATIF_BRIEF_CHARS") or (6000 if preset and preset.free_tier and provider != "mock" else 9000))
    s = Settings(
        brief_chars=brief_chars,
        provider=provider, api_key=api_key, base_url=base_url.rstrip("/") + "/" if base_url and provider != "mock" else base_url,
        model=model, concurrency=concurrency, rpm=rpm,
        timeout=float(_env("LLM_TIMEOUT_SECONDS") or 90),
        data_dir=_env("WHATIF_DATA_DIR") or "data",
    )
    if _env("WHATIF_USER_AGENT"):
        s.user_agent = _env("WHATIF_USER_AGENT")
    s.strong_model = _env("WHATIF_STRONG_MODEL", "LLM_STRONG_MODEL")
    s.exa_api_key = _env("EXA_API_KEY")
    s.serper_api_key = _env("SERPER_API_KEY")
    s.research_depth = _env("WHATIF_RESEARCH_DEPTH") or "standard"
    s.critic = (_env("WHATIF_CRITIC") or "1").lower() not in ("0", "false", "off", "no")
    for k, v in overrides.items():
        if hasattr(s, k) and v is not None:
            setattr(s, k, v)
    return s
