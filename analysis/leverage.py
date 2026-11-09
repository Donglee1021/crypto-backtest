"""杠杆和资金管理的历史模拟。只做计算，不建议用哪一档杠杆，也不下单。

模拟的规则是固定的：全仓、每次用当时的全部权益当保证金、日内权益涨到 +20% 就收工、
单笔权益回撤到 -15% 就平掉这一笔。25 倍时，-15% 的权益大约对应 0.6% 的反向价格。
另外再叠上一套在样本内按期望选出的出场规则。费用按 OKX 吃单 0.05% 乘名义价值，
滑点 0.02%，维持保证金按 0.4% 估算强平。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analysis.exits import FEE, R_MULT, SLIP, Spec, _decide, _fill

MMR = 0.004
START = 10_000.0
LEVERAGES = (25, 10, 5, 3)


def _solve_level(entry: float, side: int, qty: float, wallet: float, target_equity: float) -> float:
    """钱包 + 浮动盈亏 = 目标权益。退出手续费在成交时再扣，价格水平按目标权益反解。"""
    coef = side * qty
    if coef == 0:
        return entry
    # wallet + side * qty * (p - entry) = target
    return entry + (target_equity - wallet) / coef


def _liq_price(entry: float, side: int, qty: float, wallet: float) -> tuple[float, float]:
    maint = qty * entry * MMR
    bankrupt = _solve_level(entry, side, qty, wallet, 0.0)
    liquid = _solve_level(entry, side, qty, wallet, maint)
    return liquid, bankrupt


def group_intrabar(index: pd.DatetimeIndex, sub: pd.DataFrame | None) -> list[list[tuple[float, float, float, float]]]:
    groups = [[] for _ in range(len(index))]
    if sub is None or not len(sub):
        return groups
    sub = sub.sort_index()
    ts = sub.index
    pos = 0
    m = len(ts)
    opens = sub["open"].to_numpy(float)
    highs = sub["high"].to_numpy(float)
    lows = sub["low"].to_numpy(float)
    closes = sub["close"].to_numpy(float)
    for i, start in enumerate(index):
        end = start + pd.Timedelta(hours=1)
        while pos < m and ts[pos] < start:
            pos += 1
        q = pos
        bars = []
        while q < m and ts[q] < end:
            bars.append((opens[q], highs[q], lows[q], closes[q]))
            q += 1
        groups[i] = bars
        pos = q
    return groups


def _coverage(groups: list[list]) -> float:
    if not groups:
        return 0.0
    hit = sum(1 for bars in groups if len(bars) > 1)
    return hit / len(groups)


def run_account(ctx: dict, groups: list[list], pos: np.ndarray, spec: Spec, start: int, end: int, leverage: int) -> dict:
    wallet = START
    qty = 0.0
    side = 0
    entry = 0.0
    entry_i = -1
    trade_base = START
    extreme = 0.0
    r_dist = 0.0
    partial_done = False
    day = None
    day_start = START
    halted = False
    equity_path = []
    day_rows = []
    trade_pnls = []
    plus20_days = 0
    minus15_days = 0
    minus15_hits = 0
    liquidations = 0
    gap_worse = 0
    ruined = False
    prev_pos = 0.0

    def marked(price: float) -> float:
        if qty <= 0 or side == 0:
            return wallet
        return wallet + side * qty * (price - entry)

    def close_at(price: float, reason: str, i: int):
        nonlocal wallet, qty, side, partial_done
        if qty <= 0:
            return
        fill = _fill(price, side, True)
        gross = side * qty * (fill - entry)
        fee = qty * fill * FEE
        before = trade_base
        wallet = wallet + gross - fee
        trade_pnls.append(wallet - before)
        qty = 0.0
        side = 0
        partial_done = False
        if reason == "强平":
            pass

    def liquidate(i: int, bankrupt_fill: bool):
        nonlocal wallet, qty, side, partial_done, liquidations, ruined
        liquidations += 1
        if bankrupt_fill or wallet <= 0:
            wallet = 0.0
            ruined = True
        else:
            wallet = max(0.0, qty * entry * MMR)
            if wallet < 1:
                ruined = True
                wallet = 0.0
        trade_pnls.append(wallet - trade_base)
        qty = 0.0
        side = 0
        partial_done = False

    for i in range(start, end):
        stamp = ctx["index"][i]
        this_day = stamp.date()
        if this_day != day:
            if day is not None:
                day_rows.append(day_stat)
            day = this_day
            day_start = marked(ctx["open"][i])
            halted = False
            day_stat = {"plus20": False, "minus15": 0}
        signal = float(pos[i]) if i < len(pos) else 0.0
        fresh = signal != 0.0 and signal != prev_pos
        prev_pos = signal
        if qty <= 0 and fresh and not halted and not ruined and wallet > 1:
            side = 1 if signal > 0 else -1
            entry = _fill(ctx["open"][i], side, False)
            if entry <= 0:
                side = 0
            else:
                trade_base = wallet
                notional = wallet * leverage
                qty = notional / entry
                fee = notional * FEE
                if fee >= wallet:
                    wallet = 0.0
                    qty = 0.0
                    side = 0
                    ruined = True
                else:
                    wallet -= fee
                    entry_i = i
                    extreme = ctx["high"][i] if side > 0 else ctx["low"][i]
                    atr_v = ctx["atr"][i - 1] if i and math.isfinite(ctx["atr"][i - 1]) else ctx["atr"][i]
                    r_dist = R_MULT * atr_v if math.isfinite(atr_v) and atr_v > 0 else entry * 0.01
                    partial_done = False
        bars = groups[i] if groups[i] else [(ctx["open"][i], ctx["high"][i], ctx["low"][i], ctx["close"][i])]
        for bo, bh, bl, bc in bars:
            if qty <= 0 or ruined:
                break
            if side > 0:
                extreme = max(extreme, bh)
            else:
                extreme = min(extreme, bl)
            stop_eq = trade_base * 0.85
            day_eq = day_start * 1.20
            stop_px = _solve_level(entry, side, qty, wallet, stop_eq)
            day_px = _solve_level(entry, side, qty, wallet, day_eq)
            liq_px, bust_px = _liq_price(entry, side, qty, wallet)
            worse_open = (side > 0 and bo <= bust_px) or (side < 0 and bo >= bust_px)
            liq_open = (side > 0 and bo <= liq_px) or (side < 0 and bo >= liq_px)
            stop_open = (side > 0 and bo <= stop_px) or (side < 0 and bo >= stop_px)
            day_open = (side > 0 and bo >= day_px) or (side < 0 and bo <= day_px)
            if worse_open:
                liquidate(i, True)
                gap_worse += 1
                break
            if liq_open:
                liquidate(i, False)
                gap_worse += 1
                break
            if stop_open:
                close_at(bo, "跳空止损", i)
                day_stat["minus15"] += 1
                minus15_hits += 1
                gap_worse += 1
                break
            if day_open and marked(bo) >= day_eq:
                close_at(bo, "日内止盈", i)
                day_stat["plus20"] = True
                halted = True
                plus20_days += 1
                break
            hit_stop = (side > 0 and bl <= stop_px) or (side < 0 and bh >= stop_px)
            hit_liq = (side > 0 and bl <= liq_px) or (side < 0 and bh >= liq_px)
            hit_day = (side > 0 and bh >= day_px) or (side < 0 and bl <= day_px)
            # 同一根里如果又打止损又打止盈，按不利的一边算：止损更近，且假定已经挂着。
            if hit_liq and not hit_stop:
                liquidate(i, (side > 0 and bl <= bust_px) or (side < 0 and bh >= bust_px))
                break
            if hit_stop:
                close_at(stop_px, "账户止损", i)
                day_stat["minus15"] += 1
                minus15_hits += 1
                break
            if hit_day:
                close_at(day_px, "日内止盈", i)
                day_stat["plus20"] = True
                halted = True
                plus20_days += 1
                break
        if qty > 0 and not ruined:
            action, px, _why = _decide(ctx, spec, i, entry_i, side, entry, r_dist, extreme, partial_done, 1.0 if not partial_done else 0.5)
            if action == "partial" and not partial_done and qty > 0:
                frac = float(spec.params.get("frac", 0.5))
                close_qty = qty * frac
                fill = _fill(px, side, True)
                gross = side * close_qty * (fill - entry)
                fee = close_qty * fill * FEE
                wallet = wallet + gross - fee
                qty -= close_qty
                partial_done = True
            elif action == "close" and qty > 0:
                close_at(px, "出场规则", i)
        equity_path.append((stamp, marked(ctx["close"][i])))
        if ruined:
            equity_path[-1] = (stamp, 0.0)
            break
    if day is not None:
        day_rows.append(day_stat)
    equities = np.array([v for _t, v in equity_path], dtype=float) if equity_path else np.array([START])
    peak = np.maximum.accumulate(np.maximum(equities, 1e-9))
    dd = float((equities / peak - 1.0).min()) if len(equities) else 0.0
    days = max(1, len(day_rows))
    minus_days = sum(1 for row in day_rows if row["minus15"] > 0)
    plus_days = sum(1 for row in day_rows if row["plus20"])
    streak = _loss_streak(trade_pnls)
    daily_eq = _daily_last(equity_path)
    mc = monte_carlo(daily_eq)
    curve = _downsample(equity_path, 220)
    return {
        "leverage": leverage,
        "final_equity": round(float(equities[-1] if len(equities) else START), 2),
        "max_drawdown": round(dd, 4),
        "pct_days_plus20": round(plus_days / days, 4),
        "pct_days_minus15": round(minus_days / days, 4),
        "plus20_days": plus_days,
        "minus15_days": minus_days,
        "daily": [{"time": int(pd.Timestamp(ts).timestamp()), "value": round(float(val), 2)} for ts, val in daily_eq.items()],
        "minus15_per_day": round(minus15_hits / days, 3),
        "minus15_when_hit": round(minus15_hits / minus_days, 3) if minus_days else 0.0,
        "minus15_hits": minus15_hits,
        "longest_loss_streak": streak,
        "liquidations": liquidations,
        "gap_through_stop": gap_worse,
        "trades": len(trade_pnls),
        "days": days,
        "ruined": ruined,
        "p_drop_50": mc["p50"],
        "p_drop_90": mc["p90"],
        "median_days_to_50": mc["median_days"],
        "equity": curve,
    }


def _loss_streak(pnls: list[float]) -> int:
    best = cur = 0
    for value in pnls:
        if value < 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _daily_last(path: list[tuple]) -> pd.Series:
    if not path:
        return pd.Series([START])
    series = pd.Series({ts: val for ts, val in path})
    series = series[~series.index.duplicated(keep="last")]
    return series.resample("1D").last().dropna()


def _downsample(path: list[tuple], n: int) -> list[dict]:
    if not path:
        return []
    step = max(1, len(path) // n)
    rows = []
    seen = set()
    for ts, val in list(path[::step]) + [path[-1]]:
        stamp = int(pd.Timestamp(ts).timestamp())
        if stamp in seen:
            continue
        seen.add(stamp)
        rows.append({"time": stamp, "value": round(float(val), 2)})
    return rows


def monte_carlo(daily_equity: pd.Series, paths: int = 2000, seed: int = 11) -> dict:
    if len(daily_equity) < 10:
        return {"p50": None, "p90": None, "median_days": None}
    rets = daily_equity.pct_change().replace([np.inf, -np.inf], np.nan).dropna().to_numpy(float)
    rets = np.clip(rets, -0.999, None)
    if len(rets) < 10:
        return {"p50": None, "p90": None, "median_days": None}
    rng = np.random.default_rng(seed)
    draw = rng.integers(0, len(rets), size=(paths, len(rets)))
    sampled = rets[draw]
    growth = np.cumprod(1.0 + sampled, axis=1)
    p50 = float((growth.min(axis=1) <= 0.5).mean())
    p90 = float((growth.min(axis=1) <= 0.1).mean())
    hit = growth <= 0.5
    days = []
    for row in hit:
        idx = np.flatnonzero(row)
        if len(idx):
            days.append(int(idx[0]) + 1)
    median = int(np.median(days)) if days else None
    return {"p50": round(p50, 4), "p90": round(p90, 4), "median_days": median}
