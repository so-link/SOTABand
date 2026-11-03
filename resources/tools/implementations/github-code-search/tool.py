"""SOTABand github-code-search entry point."""

import os
import sys
from pathlib import Path

_tool_dir = Path(os.environ.get("TOOL_DIR") or Path(__file__).resolve().parent)
_root = _tool_dir.resolve().parents[3]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Compatibility with the existing executor's JSON literals in Python parameters.
true, false, null = True, False, None


def execute(**kwargs) -> dict:
    from resources.tools._academic.common import invoke

    params = dict(kwargs)
    params["mode"] = kwargs.get("mode", "search")
    return invoke("github", params)
