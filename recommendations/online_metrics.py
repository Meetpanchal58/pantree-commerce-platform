"""Online recommendation metrics from the live store's logs.

Reads recommendation_impressions / recommendation_clicks (see webapp/rec_logging.py)
together with clickstream and transactions from the store's SQLite database and
reports, per surface, source and experiment variant:

* served and seen impressions (seen = at least half the card was on screen),
* click-through rate (clicks / seen) with a 95% Wilson interval,
* add-to-cart within 24 hours and purchase within 7 days of a click.

With the A/B test on (PANTREE_AB_TEST=ranker) it compares control (no ranker)
with treatment (ranker). Impressions from one customer are not independent, so
the difference is bootstrapped by customer rather than by impression.

Run: python -m recommendations.online_metrics [--db webapp/pantree_app.db]
"""
import argparse
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from recommendations.candidates.common import purchase_sql

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path(__file__).with_name("reports") / "online_metrics.md"


def wilson(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    if trials == 0:
        return (0.0, 0.0)
    p = successes / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def load(db_path: Path) -> pd.DataFrame:
    """One row per served impression with click and post-click outcome flags."""
    from webapp import database  # Postgres when DATABASE_URL is set, else the SQLite file

    con = database.connect(db_path)
    try:
        tables = database.table_names(con)
        if "recommendation_impressions" not in tables:
            return pd.DataFrame()
        imps = database.read_frame(con, "SELECT * FROM recommendation_impressions")
        clicks = database.read_frame(con, "SELECT impression_id, clicked_at FROM recommendation_clicks")
        carts = database.read_frame(
            con, "SELECT customer_id, product_id, event_timestamp FROM clickstream WHERE event_type='add_to_cart'"
        ) if "clickstream" in tables else pd.DataFrame(columns=["customer_id", "product_id", "event_timestamp"])
        buys = database.read_frame(
            con, "SELECT customer_id, product_id, order_timestamp FROM transactions WHERE " + purchase_sql()
        ) if "transactions" in tables else pd.DataFrame(columns=["customer_id", "product_id", "order_timestamp"])
    finally:
        con.close()
    if imps.empty:
        return imps
    imps = imps.merge(clicks, on="impression_id", how="left")
    imps["seen"] = imps["seen_at"].notna()
    imps["clicked"] = imps["clicked_at"].notna()
    for col in ("clicked_at",):
        imps[col] = pd.to_datetime(imps[col], errors="coerce")
    carts["event_timestamp"] = pd.to_datetime(carts["event_timestamp"], errors="coerce")
    buys["order_timestamp"] = pd.to_datetime(buys["order_timestamp"], errors="coerce")

    def followed(events, time_col, window):
        clicked = imps[imps["clicked"]][["impression_id", "customer_id", "product_id", "clicked_at"]]
        joined = clicked.merge(events, on=["customer_id", "product_id"])
        ok = joined[(joined[time_col] >= joined["clicked_at"]) & (joined[time_col] <= joined["clicked_at"] + window)]
        return set(ok["impression_id"])

    carted = followed(carts, "event_timestamp", pd.Timedelta(hours=24))
    bought = followed(buys, "order_timestamp", pd.Timedelta(days=7))
    imps["carted_after_click"] = imps["impression_id"].isin(carted)
    imps["bought_after_click"] = imps["impression_id"].isin(bought)
    return imps


def summarise(frame: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    rows = []
    for key, g in frame.groupby(by, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        seen, clicks = int(g["seen"].sum()), int(g["clicked"].sum())
        low, high = wilson(clicks, seen)
        rows.append({
            **dict(zip(by, key)),
            "served": len(g), "seen": seen, "clicks": clicks,
            "ctr": clicks / seen if seen else 0.0, "ctr_low": low, "ctr_high": high,
            "cart_after_click": int(g["carted_after_click"].sum()),
            "purchase_after_click": int(g["bought_after_click"].sum()),
            "purchase_per_1000_seen": 1000 * g["bought_after_click"].sum() / seen if seen else 0.0,
        })
    return pd.DataFrame(rows)


def ab_test(frame: pd.DataFrame, iterations: int = 2000, seed: int = 3) -> dict | None:
    """Treatment minus control CTR, bootstrapped by customer."""
    seen = frame[frame["seen"] & frame["variant"].isin(["control", "treatment"])]
    if seen["variant"].nunique() < 2:
        return None
    per_customer = seen.groupby(["variant", "customer_id"]).agg(seen=("seen", "size"), clicks=("clicked", "sum")).reset_index()
    groups = {v: g[["seen", "clicks"]].to_numpy() for v, g in per_customer.groupby("variant")}
    rng = np.random.default_rng(seed)
    ctr = lambda arr: arr[:, 1].sum() / arr[:, 0].sum()  # noqa: E731
    diffs = []
    for _ in range(iterations):
        t = groups["treatment"][rng.integers(0, len(groups["treatment"]), len(groups["treatment"]))]
        c = groups["control"][rng.integers(0, len(groups["control"]), len(groups["control"]))]
        diffs.append(ctr(t) - ctr(c))
    return {
        "control_ctr": float(ctr(groups["control"])), "treatment_ctr": float(ctr(groups["treatment"])),
        "control_customers": int(len(groups["control"])), "treatment_customers": int(len(groups["treatment"])),
        "diff": float(ctr(groups["treatment"]) - ctr(groups["control"])),
        "ci_low": float(np.quantile(diffs, 0.025)), "ci_high": float(np.quantile(diffs, 0.975)),
    }


def table(df: pd.DataFrame, keys: list[str]) -> list[str]:
    head = "| " + " | ".join(keys) + " | Served | Seen | Clicks | CTR (95% CI) | Cart after click | Purchase after click |"
    lines = [head, "|" + "---|" * (len(keys) + 6)]
    for r in df.sort_values("seen", ascending=False).itertuples(index=False):
        d = r._asdict()
        lines.append("| " + " | ".join(str(d[k]) for k in keys) +
                     f" | {d['served']:,} | {d['seen']:,} | {d['clicks']:,} | "
                     f"{d['ctr']:.1%} ({d['ctr_low']:.1%}–{d['ctr_high']:.1%}) | "
                     f"{d['cart_after_click']:,} | {d['purchase_after_click']:,} |")
    return lines


def main(db_path: Path, report_path: Path = REPORT) -> str:
    frame = load(db_path)
    lines = ["# Online recommendation metrics", "",
             f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} from `{db_path.name}`.", ""]
    if frame.empty:
        lines.append("No recommendation impressions have been logged yet.")
    else:
        lines += [f"{len(frame):,} served, {int(frame['seen'].sum()):,} seen, {int(frame['clicked'].sum()):,} clicks "
                  f"from {frame['customer_id'].nunique():,} customers.", "", "## By surface and variant", ""]
        lines += table(summarise(frame, ["surface", "intent", "variant"]), ["surface", "intent", "variant"])
        lines += ["", "## By source", ""]
        lines += table(summarise(frame, ["source"]), ["source"])
        result = ab_test(frame)
        lines += ["", "## A/B test: ranker (treatment) vs retrieval order (control)", ""]
        if result is None:
            lines.append("Not running. Set `PANTREE_AB_TEST=ranker` to split customers 50/50.")
        else:
            lines += [f"Control CTR {result['control_ctr']:.2%} ({result['control_customers']:,} customers), "
                      f"treatment CTR {result['treatment_ctr']:.2%} ({result['treatment_customers']:,} customers).",
                      f"Difference {result['diff']:+.2%} (95% CI {result['ci_low']:+.2%} to {result['ci_high']:+.2%}, "
                      "bootstrapped by customer). If the interval includes 0, the test needs more traffic."]
    text = "\n".join(lines) + "\n"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text)
    print(text)
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default_db = Path(os.getenv("PANTREE_DB_PATH", ROOT / "webapp" / "pantree_app.db"))
    parser.add_argument("--db", type=Path, default=default_db)
    main(parser.parse_args().db)
