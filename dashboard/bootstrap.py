"""Put the repo root and `src` on the path so Streamlit can import this package."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install_path() -> None:
    for entry in (str(ROOT), str(ROOT / "src")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
