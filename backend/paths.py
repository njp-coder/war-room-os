"""Where state lives.

Everything the server writes — the database, the event log, encrypted connection secrets, cloned repos, uploads —
goes in one directory, so a deployment can mount a disk there and survive restarts. Set WARROOM_DATA_DIR to that
mount (for example /data). Unset, it stays beside the demo fixtures, which is what local development expects.
Fixtures that ship with the repo are read from FIXTURES and never written.
"""
from __future__ import annotations

import os
from pathlib import Path

FIXTURES = Path(__file__).parent / "demo" / "data"
DATA = Path(os.environ.get("WARROOM_DATA_DIR") or FIXTURES).expanduser()
DATA.mkdir(parents=True, exist_ok=True)
