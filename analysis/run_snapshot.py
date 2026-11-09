#!/usr/bin/env python3
"""每小时记一笔当时的分数，并补上已经过去的 1 小时 / 4 小时 / 24 小时结果。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.horizons import DROPPED, _weekly_frame, current_signals
from analysis.market import drop_forming, fetch_funding, fetch_ohlcv
from analysis.scoring import score_frame, talib_last
from analysis.store import LATEST, export_frontend, insert_signals, update_outcomes


def latest_fng():
    import urllib.request
    req = urllib.request.Request("https://api.alternative.me/fng/?limit=1", headers={"User-Agent": "crypto-backtest"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return int(json.loads(resp.read().decode())["data"][0]["value"])


def main():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    try:
        fng = latest_fng()
    except Exception as exc:
        print("fng", exc)
        fng = None
    rows = []
    latest = {}
    for coin in ("BTC", "ETH"):
        raw = {}
        # 快照只需要算当前分数。缓存里已经有长历史时，这里只会补最新的几根。
        for tf in ("15m", "1h", "4h", "1d"):
            raw[tf] = drop_forming(fetch_ohlcv(coin, tf), tf)
        scored = {tf: score_frame(df) for tf, df in raw.items()}
        weekly = _weekly_frame(raw["1d"])
        funding = None
        fund_rate = None
        try:
            hist = fetch_funding(coin, days=30)
            if len(hist):
                funding = hist["rate"]
                fund_rate = float(funding.iloc[-1])
        except Exception as exc:
            print("funding", coin, exc)
        try:
            talib_note = talib_last(raw["1h"])
        except Exception as exc:
            talib_note = {"error": str(exc)[:160]}
        signals = current_signals(scored, weekly, funding, fng)
        price = float(raw["1h"]["close"].iloc[-1])
        latest[coin] = {"price": price, "funding": fund_rate, "fng": fng, "horizons": signals}
        for name, item in signals.items():
            rows.append({
                "ts": now,
                "coin": coin,
                "horizon": name,
                "score": item["score"],
                "verdict": item["verdict"],
                "price": price,
                "factors": json.dumps({
                    "talib": talib_note if name == "1小时" else None,
                    "funding": fund_rate,
                    "fng": fng,
                    "dropped": DROPPED,
                }, ensure_ascii=False),
            })
        print(coin, "price", price, "中线", signals.get("中线"), flush=True)
    insert_signals(rows)
    update_outcomes()
    export_frontend()
    LATEST.parent.mkdir(parents=True, exist_ok=True)
    prev = {}
    if LATEST.exists():
        try:
            prev = json.loads(LATEST.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    # 快照不重算整份回测，只刷新当前分数。已有的回测摘要留在 summary.json。
    LATEST.write_text(json.dumps({
        "generated": now.isoformat(),
        "coins": latest,
        "backtest_generated": prev.get("backtest_generated"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("snapshot rows", len(rows), flush=True)


if __name__ == "__main__":
    main()
