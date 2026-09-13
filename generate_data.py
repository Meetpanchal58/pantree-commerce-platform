"""Public entry point for the stateful ecommerce data simulator."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "generators"))
from simulator import run

if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser(description="Pantree stateful ecommerce simulator")
    p.add_argument("--mode",choices=["initialize","increment"],default="initialize")
    p.add_argument("--days",type=int,default=None,help="Deprecated compatibility flag; increment mode always catches up through today")
    args=p.parse_args(); run(args.mode,args.days)
