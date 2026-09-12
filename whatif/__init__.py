"""WhatIf Timelines — counterfactual forecasting with an LLM agent swarm.

    from whatif.server import run, show
    run(background=True)   # then open the printed URL, or show()
"""
__version__ = "0.1.0"

from .server import run, show, create_app  # noqa: F401
