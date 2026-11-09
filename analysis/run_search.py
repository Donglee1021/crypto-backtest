#!/usr/bin/env python3
"""走步搜索。只读行情，不下单。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.market import drop_forming, fetch_funding, fetch_ohlcv  # noqa: E402
from analysis.search import Config, current_signal, prepare_frames, run_search  # noqa: E402

OUT = ROOT / "data" / "backtest" / "best.json"
LIVE = ROOT / "data" / "backtest" / "best_live.json"


def load_market():
    market = {}
    funding = {}
    for coin in ("BTC", "ETH"):
        market[coin] = {}
        for tf in ("1h", "4h", "1d"):
            market[coin][tf] = drop_forming(fetch_ohlcv(coin, tf), tf)
        try:
            fund = fetch_funding(coin)
            funding[coin] = fund["rate"] if len(fund) else None
        except Exception as exc:
            print("funding", coin, exc, flush=True)
            funding[coin] = None
    return market, funding


def main():
    market, funding = load_market()
    payload = run_search(market, funding)
    payload["generated"] = datetime.now(timezone.utc).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    LIVE.write_text(json.dumps({
        "generated": payload["generated"],
        "top": [{"name": card["name"], "live": card["live"], "plain": card["plain"]} for card in payload["top"]],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT, flush=True)
    print("beats", payload["beats_buy_hold"], "trials", payload["trials"], flush=True)
    for card in payload["top"]:
        print(card["name"], card["score"], card.get("beats_buy_hold"), card.get("choice", {}).get("tf"), flush=True)


def refresh_live():
    if not OUT.exists():
        print("没有最佳方案文件，跳过实时信号", flush=True)
        return
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    market, funding = load_market()
    prepared, _start, _end = prepare_frames(market, funding)
    live_rows = []
    for card in payload.get("top") or []:
        choice = card.get("choice") or {}
        if not choice:
            continue
        cfg = Config(card["family"], card["name"], choice["tf"], choice["mode"], choice["params"], choice.get("plain") or card.get("plain") or "")
        live = current_signal(prepared, cfg)
        card["live"] = live
        live_rows.append({"name": card["name"], "live": live, "plain": card.get("plain")})
    payload["generated_live"] = datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    LIVE.write_text(json.dumps({"generated": payload["generated_live"], "top": live_rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("refreshed live signals", flush=True)


if __name__ == "__main__":
    main()
