"""Run the installed memoranda bridge with isolated Python, independent of cwd."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from squadra_memories.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
