"""`python -m whatif [--port 8000] [--provider groq] [--model ...] [--max-rounds 12]`"""
import argparse
import logging

from .server import run


def main():
    ap = argparse.ArgumentParser(prog="whatif", description="WhatIf Timelines — counterfactual agent-swarm forecasting")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--provider", default=None, help="openai|groq|gemini|openrouter|cerebras|ollama|mock")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--max-rounds", type=int, default=12, help="cap on simulation periods per branch")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run(port=a.port, host=a.host, background=False, open_colab=False, max_rounds=a.max_rounds,
        provider=a.provider, model=a.model, base_url=a.base_url, api_key=a.api_key, data_dir=a.data_dir,
        log_level="debug" if a.verbose else "info")


if __name__ == "__main__":
    main()
