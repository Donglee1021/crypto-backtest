#!/usr/bin/env python3
"""出场规则和杠杆模拟。研究用，不下单。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.exits import (  # noqa: E402
    build_context,
    catalog,
    dashboard_entries,
    live_advice,
    metrics_of,
    random_entries,
    rsi_entries,
    simulate,
    walk_forward,
)
from analysis.leverage import LEVERAGES, START, _coverage, group_intrabar, monte_carlo, run_account  # noqa: E402
from analysis.market import drop_forming, fetch_funding, fetch_ohlcv  # noqa: E402
from analysis.scoring import score_frame  # noqa: E402
from analysis.strategies import rsi_reversion  # noqa: E402

OUT = ROOT / "data" / "backtest" / "exits.json"
LEV = ROOT / "data" / "backtest" / "leverage.json"
LIVE = ROOT / "data" / "backtest" / "exit_live.json"


def _pos_dashboard(df: pd.DataFrame) -> np.ndarray:
    scored = score_frame(df)
    raw = pd.to_numeric(scored["score"], errors="coerce").shift(1).to_numpy()
    pos = np.zeros(len(df))
    for i, value in enumerate(raw):
        if value != value:
            continue
        if value >= 3:
            pos[i] = 1
        elif value <= -3:
            pos[i] = -1
    return pos


def _stitch(parts: list[dict]) -> dict:
    equity = START
    curve = []
    daily = []
    plus_days = minus_days = hits = days = 0
    trades = liquidations = gaps = 0
    ruined = False
    streak = 0
    for part in parts:
        scale = equity / START
        for point in part["daily"]:
            daily.append({"time": point["time"], "value": round(point["value"] * scale, 2)})
        equity = part["final_equity"] * scale
        plus_days += part["plus20_days"]
        minus_days += part["minus15_days"]
        hits += part["minus15_hits"]
        days += part["days"]
        trades += part["trades"]
        liquidations += part["liquidations"]
        gaps += part["gap_through_stop"]
        streak = max(streak, part["longest_loss_streak"])
        ruined = ruined or part["ruined"] or equity <= 0
        if equity <= 0:
            break
    values = np.array([point["value"] for point in daily], dtype=float) if daily else np.array([START])
    peak = np.maximum.accumulate(np.maximum(values, 1e-9))
    dd = float((values / peak - 1.0).min())
    series = pd.Series(
        [point["value"] for point in daily],
        index=pd.to_datetime([point["time"] for point in daily], unit="s", utc=True),
    ) if daily else pd.Series([START])
    mc = monte_carlo(series)
    step = max(1, len(daily) // 220)
    plot = daily[::step]
    if daily and plot[-1] is not daily[-1]:
        plot = plot + [daily[-1]]
    days = max(1, days)
    return {
        "final_equity": round(float(equity), 2),
        "max_drawdown": round(dd, 4),
        "pct_days_plus20": round(plus_days / days, 4),
        "pct_days_minus15": round(minus_days / days, 4),
        "minus15_per_day": round(hits / days, 3),
        "minus15_when_hit": round(hits / minus_days, 3) if minus_days else 0.0,
        "longest_loss_streak": streak,
        "liquidations": liquidations,
        "gap_through_stop": gaps,
        "trades": trades,
        "days": days,
        "ruined": ruined,
        "p_drop_50": mc["p50"],
        "p_drop_90": mc["p90"],
        "median_days_to_50": mc["median_days"],
        "equity": plot,
    }


def _intrabar_frames(coin: str):
    five = fifteen = None
    try:
        five = drop_forming(fetch_ohlcv(coin, "5m"), "5m")
    except Exception as exc:
        print("5m failed", coin, exc, flush=True)
    try:
        fifteen = drop_forming(fetch_ohlcv(coin, "15m"), "15m")
    except Exception as exc:
        print("15m failed", coin, exc, flush=True)
    return five, fifteen


def research_coin(coin: str) -> tuple[dict, dict, dict]:
    print(f"exit {coin}", flush=True)
    h1 = drop_forming(fetch_ohlcv(coin, "1h"), "1h")
    h1.attrs["coin"] = coin
    daily = drop_forming(fetch_ohlcv(coin, "1d"), "1d")
    try:
        fund_df = fetch_funding(coin)
        funding = fund_df["rate"] if len(fund_df) else None
    except Exception as exc:
        print("funding", coin, exc, flush=True)
        funding = None
    ctx = build_context(h1, daily, funding)
    sources = {
        "页面规则": dashboard_entries(h1),
        "RSI均值回归": rsi_entries(h1),
        "随机入场": random_entries(len(h1), seed=7 if coin == "BTC" else 11),
    }
    specs = catalog()
    board = []
    scatter = []
    picked_for_lev = {}
    n = len(h1)
    for name, entries in sources.items():
        print(f"  {name} entries {len(entries)}", flush=True)
        folded = walk_forward(ctx, entries, specs)
        baseline = next(spec for spec in specs if spec.id == "baseline")
        edges = [int(n * i / 4) for i in range(5)]
        base_trades = []
        for fold in range(1, 4):
            base_trades.extend(simulate(ctx, entries, baseline, edges[fold], edges[fold + 1]))
        board.append({
            "coin": coin,
            "entry": name,
            "rule": baseline.name,
            "params": baseline.label,
            "role": "对照",
            "oos": metrics_of(base_trades),
            "flag": False,
        })
        oos = folded["oos"]
        flag = bool(oos["win_rate"] is not None and oos["win_rate"] > 0.8 and (oos["expectancy"] or 0) < 0)
        board.append({
            "coin": coin,
            "entry": name,
            "rule": "走步选定",
            "params": "；".join(folded["picked"]),
            "role": "样本外",
            "oos": oos,
            "flag": flag,
            "folds": folded["picked"],
        })
        for point in folded["cloud"]:
            point = dict(point)
            point["coin"] = coin
            point["entry"] = name
            scatter.append(point)
            if point["flag"]:
                board.append({
                    "coin": coin,
                    "entry": name,
                    "rule": point["rule"],
                    "params": point["params"],
                    "role": "高胜率但期望为负",
                    "oos": {
                        "trades": point["trades"],
                        "win_rate": point["win_rate"],
                        "expectancy": point["expectancy"],
                        "payoff": point["payoff"],
                        "avg_win": None,
                        "avg_loss": None,
                        "profit_factor": None,
                        "max_drawdown": None,
                    },
                    "flag": True,
                })
        if name != "随机入场":
            picked_for_lev[name] = folded["chosen_specs"]
    fund_rate = None if funding is None or not len(funding) else float(funding.iloc[-1])
    live = live_advice(h1, fund_rate)
    five, fifteen = _intrabar_frames(coin)
    groups = group_intrabar(h1.index, five)
    fifteen_groups = group_intrabar(h1.index, fifteen)
    for i, bars in enumerate(groups):
        if len(bars) <= 1 and fifteen_groups[i]:
            groups[i] = fifteen_groups[i]
    cover = _coverage(groups)
    sub_name = "5m/15m"
    print(f"  intrabar {sub_name} coverage {cover:.2%}", flush=True)
    level = {
        "页面规则": _pos_dashboard(h1),
        "RSI均值回归": rsi_reversion(h1["close"], 14, 30, 70).to_numpy(),
    }
    lev_rows = {}
    edges = [int(n * i / 4) for i in range(5)]
    for name, fold_specs in picked_for_lev.items():
        print(f"  leverage {name} via {' / '.join(item.label for item in fold_specs)}", flush=True)
        lev_rows[name] = {
            "exit_rule": " / ".join(dict.fromkeys(item.name + " " + item.label for item in fold_specs)),
            "rows": [],
        }
        for leverage in LEVERAGES:
            parts = []
            for fold, spec_fold in enumerate(fold_specs, start=1):
                # 每一段只用它之前的行情按期望选出场，再只在这一段上做账户模拟。
                parts.append(run_account(ctx, groups, level[name], spec_fold, edges[fold], edges[fold + 1], leverage))
            summary = _stitch(parts)
            summary["leverage"] = leverage
            summary["exit_rule"] = " / ".join(dict.fromkeys(item.label for item in fold_specs))
            lev_rows[name]["rows"].append(summary)
            print(f"    {leverage}x final {summary['final_equity']} dd {summary['max_drawdown']} "
                  f"+20% days {summary['pct_days_plus20']} -15% days {summary['pct_days_minus15']}", flush=True)
    exit_coin = {"board": board, "scatter": scatter, "live": live}
    lev_coin = {
        "intrabar": sub_name,
        "intrabar_coverage": round(cover, 3),
        "strategies": lev_rows,
    }
    return exit_coin, lev_coin, {"price": float(h1["close"].iloc[-1]), "funding": fund_rate, "live": live}


def main():
    now = datetime.now(timezone.utc).isoformat()
    exits = {
        "generated": now,
        "note": "出场研究按 1 倍价格收益，买卖各收 0.05% 吃单和 0.02% 滑点，不含杠杆。走步时只在训练段按每笔期望选参数，不按胜率选，也没有为了接近某个胜率去改规则。表上的结论是样本外交易。散点是各参数自己在样本外的胜率和期望，用来看这两者往往相反，不是再从里面挑一次。",
        "coins": {},
        "leaderboard": [],
        "scatter": [],
        "live": {},
    }
    leverage = {
        "generated": now,
        "start_equity": START,
        "note": "这是把固定规则套在历史行情上的账户路径。全仓、每次用当时的全部权益当保证金。日内权益相对当天开盘涨到 +20% 就当天不再交易；单笔权益回到开仓前的 -15% 就平掉这一笔。25 倍时，这条约等于 0.6% 的反向价格，手续费会使它更近一些。维持保证金按名义价值的 0.4% 估计强平。吃单 0.05% 按名义价值扣，滑点 0.02%。数字不是建议使用哪一档杠杆。杠杆会把亏损和费用一起放大，过去没归零不代表以后不会。",
        "coins": {},
    }
    live = {"generated": now, "coins": {}}
    for coin in ("BTC", "ETH"):
        exit_coin, lev_coin, live_coin = research_coin(coin)
        exits["leaderboard"].extend(exit_coin["board"])
        exits["scatter"].extend(exit_coin["scatter"])
        exits["live"][coin] = live_coin["live"]
        exits["coins"][coin] = {"price": live_coin["price"], "funding": live_coin["funding"]}
        leverage["coins"][coin] = lev_coin
        live["coins"][coin] = live_coin
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(exits, ensure_ascii=False), encoding="utf-8")
    LEV.write_text(json.dumps(leverage, ensure_ascii=False), encoding="utf-8")
    LIVE.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT, LEV, flush=True)


if __name__ == "__main__":
    main()
