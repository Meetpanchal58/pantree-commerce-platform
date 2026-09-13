"""Compatibility entry point. The stateful simulator is now the single source of generation logic."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from simulator import run
if __name__ == "__main__":
    run("initialize")
