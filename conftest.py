"""Pytest setup — expose this directory as the importable package `agent`.

The repo is intended to be cloned as `agent/` so that `python -m agent` and
`from agent.X import ...` work as documented. When the directory has any
other name on disk (e.g. `botarena-agent`), the test imports still need to
find the package, so we register a synthetic `agent` package pointing at the
repo root.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

if "agent" not in sys.modules:
    spec = importlib.util.spec_from_loader(
        "agent",
        loader=None,
        is_package=True,
    )
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(_ROOT)]
    sys.modules["agent"] = module
