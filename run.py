#!/usr/bin/env python3
"""
DanmakuHime launcher.

A thin shim so the assembled app runs from the repo (or bundle) root with a
single `python3 run.py`. The real entry point and all backend code live in
backend/; this only puts backend/ on the import path and hands off to
backend/main.py's main(). It is NOT part of the integrity-checked backend
package — editing it never trips the startup guards.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

from main import main  # backend/main.py — its __file__ anchors BASE_DIR to backend/

if __name__ == "__main__":
    main()
