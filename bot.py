#!/usr/bin/env python3
"""HostSpark Telegram Bot CLI launcher."""
import sys
from pathlib import Path

# Ensure src/ is in sys.path for direct execution (e.g. python bot.py)
_SRC_DIR = Path(__file__).resolve().parent / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from hostspark.cli import main

if __name__ == "__main__":
    main()
