#!/usr/bin/env python3
"""同一段 K 线，Python 打分必须和 js/indicators.js 一致。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.horizons import (  # noqa: E402
    finish_factors,
    fng_unit_long,
    fng_unit_mid,
    funding_unit_mid,
    funding_unit_scalp,
    sr_unit_from_flags,
    weekly_rsi_unit,
)
from analysis.scoring import score_frame  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "parity_btc_1h.json"


def _rows_from_frame(df: pd.DataFrame) -> list:
    rows = []
    for ts, row in df.iterrows():
        rows.append([int(pd.Timestamp(ts).timestamp() * 1000), float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume)])
    return rows


def _synthetic(n: int = 480, seed: int = 7) -> list:
    rng = np.random.default_rng(seed)
    price = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.012, n)))
    rows = []
    start = 1_700_000_000_000
    for i, close in enumerate(price):
        span = abs(float(rng.normal(0, 0.004))) * close + close * 0.0005
        high = close + span * float(rng.random())
        low = close - span * float(rng.random())
        open_ = low + (high - low) * float(rng.random())
        rows.append([start + i * 3_600_000, open_, high, low, float(close), float(80 + rng.random() * 40)])
    return rows


def _compare(rows: list, label: str):
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("time")
    py_scores = [None if pd.isna(x) else int(x) for x in score_frame(df)["score"].tolist()]
    blob = ROOT / "data" / "cache" / f"parity_{label}.json"
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_text(json.dumps(rows), encoding="utf-8")
    out = subprocess.check_output(["node", str(ROOT / "js" / "score_series.js"), str(blob)], text=True)
    js_scores = json.loads(out)
    mismatches = []
    compared = 0
    for i, (left, right) in enumerate(zip(py_scores, js_scores)):
        if left is None and right is None:
            continue
        compared += 1
        if left != right:
            mismatches.append((i, left, right))
    print(f"{label}: compared {compared}, mismatches {len(mismatches)}")
    if mismatches:
        print(mismatches[:8])
        raise SystemExit(f"{label} Python 与 JS 打分不一致")


def _expect_horizon():
    raw = subprocess.check_output(["node", str(ROOT / "js" / "horizon_cases.js")], text=True)
    js_cases = {item["name"]: item for item in json.loads(raw)}
    local = {
        "scalp-hot": finish_factors([
            ("15分钟技术", 0.14, 4 / 10),
            ("当前资金费率", 0.08, funding_unit_scalp(0.0006)),
            ("短线位置", 0.12, 0.7),
        ]),
        "scalp-cold": finish_factors([
            ("15分钟技术", 0.14, -6 / 10),
            ("当前资金费率", 0.08, funding_unit_scalp(-0.0004)),
            ("短线位置", 0.12, -0.85),
        ]),
        "mid-up": finish_factors([
            ("多周期技术", 0.22, 2.5 / 10),
            ("支撑阻力", 0.14, sr_unit_from_flags({
                "broke_down": False, "broke_up": True, "near_support": False, "near_resistance": False,
                "support_dist": -1.2, "resist_dist": 3.4,
            }, "mid")),
            ("资金费率", 0.08, funding_unit_mid(0.0001)),
            ("恐惧贪婪", 0.05, fng_unit_mid(18)),
        ]),
        "mid-down": finish_factors([
            ("多周期技术", 0.22, -4 / 10),
            ("资金费率", 0.08, funding_unit_mid(0.0008)),
            ("恐惧贪婪", 0.05, fng_unit_mid(82)),
        ]),
        "long-up": finish_factors([
            ("周线趋势", 0.26, 6 / 10),
            ("日线趋势", 0.18, 3 / 10),
            ("周线RSI", 0.10, weekly_rsi_unit(62)),
            ("周线EMA200", 0.12, 0.7),
            ("大级别位置", 0.12, 0.25),
            ("恐惧贪婪", 0.05, fng_unit_long(12)),
        ]),
        "long-flat": finish_factors([
            ("周线趋势", 0.26, 1 / 10),
            ("日线趋势", 0.18, -1 / 10),
            ("周线RSI", 0.10, weekly_rsi_unit(48)),
            ("周线EMA200", 0.12, -0.7),
            ("恐惧贪婪", 0.05, fng_unit_long(55)),
        ]),
    }
    for name, py in local.items():
        js = js_cases[name]
        if py["score"] != js["score"] or py["call"] != js["call"] or py["strength"] != js["strength"] or py["conflict"] != js["conflict"]:
            print(name, "python", py, "js", js)
            raise SystemExit("周期综合分和 JS 不一致")
        print(name, py["strength"], py["score"])
    print("horizon parity ok")


def main():
    _compare(_synthetic(), "synthetic")
    if FIXTURE.exists():
        _compare(json.loads(FIXTURE.read_text(encoding="utf-8")), "btc_1h_fixture")
    else:
        cache = ROOT / "data" / "cache" / "BTC_1h.parquet"
        if cache.exists():
            df = pd.read_parquet(cache).sort_values("ts").tail(360)
            rows = _rows_from_frame(df.assign(time=pd.to_datetime(df["ts"], unit="ms", utc=True)).set_index("time"))
            FIXTURE.parent.mkdir(parents=True, exist_ok=True)
            FIXTURE.write_text(json.dumps(rows), encoding="utf-8")
            _compare(rows, "btc_1h_fixture")
            print("wrote", FIXTURE)
    _expect_horizon()
    print("parity ok")


if __name__ == "__main__":
    main()
