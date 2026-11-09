"""出场规则研究。按 1 倍价格收益计算，不下单，也不使用杠杆。

参数在走步的训练段里按每笔期望挑选，不按胜率挑选，也不为了凑某个胜率去改规则。
页面上作为结论的数字只来自训练段之后的样本外交易。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from analysis.scoring import atr, kdj, macd, rsi, score_frame, sma
from analysis.strategies import rsi_reversion

FEE = 0.0005
SLIP = 0.0002
R_MULT = 1.5  # 1R = 1.5 倍入场时的 ATR


@dataclass
class Spec:
    id: str
    name: str
    label: str
    kind: str
    params: dict


def _edges(pos: np.ndarray) -> list[tuple[int, int]]:
    out = []
    prev = 0.0
    for i, cur in enumerate(pos):
        cur = float(cur)
        if cur != 0.0 and cur != prev:
            out.append((i, 1 if cur > 0 else -1))
        prev = cur
    return out


def dashboard_entries(df: pd.DataFrame) -> list[tuple[int, int]]:
    scored = score_frame(df)
    raw = pd.to_numeric(scored["score"], errors="coerce").shift(1).to_numpy()
    pos = np.zeros(len(df))
    for i, value in enumerate(raw):
        if math.isnan(value):
            continue
        if value >= 3:
            pos[i] = 1
        elif value <= -3:
            pos[i] = -1
    return _edges(pos)


def rsi_entries(df: pd.DataFrame) -> list[tuple[int, int]]:
    pos = rsi_reversion(df["close"], 14, 30, 70).to_numpy()
    return _edges(pos)


def random_entries(n: int, seed: int = 7) -> list[tuple[int, int]]:
    rng = np.random.default_rng(seed)
    start, stop = 220, max(221, n - 30)
    count = max(40, (stop - start) // 48)
    idx = np.linspace(start, stop, count).astype(int)
    idx = idx + rng.integers(-6, 7, size=len(idx))
    idx = np.clip(idx, start, stop - 1)
    idx = np.unique(idx)
    sides = rng.choice(np.array([-1, 1]), size=len(idx))
    return [(int(i), int(s)) for i, s in zip(idx, sides)]


def _daily_pivots(daily: pd.DataFrame, index: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    h = daily["high"].to_numpy(float)
    l = daily["low"].to_numpy(float)
    c = daily["close"].to_numpy(float)
    pp = (h + l + c) / 3.0
    r1 = 2 * pp - l
    s1 = 2 * pp - h
    r2 = pp + (h - l)
    s2 = pp - (h - l)
    # 当天枢轴要用前一天收完的数据。
    frame = pd.DataFrame({"r1": r1, "s1": s1, "r2": r2, "s2": s2}, index=daily.index + pd.Timedelta(days=1))
    aligned = frame.reindex(index, method="ffill")
    return (
        aligned["r1"].to_numpy(float),
        aligned["s1"].to_numpy(float),
        aligned["r2"].to_numpy(float),
        aligned["s2"].to_numpy(float),
    )


def build_context(df: pd.DataFrame, daily: pd.DataFrame, funding: pd.Series | None) -> dict:
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    volume = df["volume"].to_numpy(float)
    line, _sig, hist = macd(close)
    ks, ds, _js = kdj(high, low, close)
    r1, s1, r2, s2 = _daily_pivots(daily, df.index)
    if funding is None or not len(funding):
        fund = np.zeros(len(df))
        fund_ok = np.zeros(len(df), dtype=bool)
    else:
        aligned = funding.reindex(df.index, method="ffill")
        fund = aligned.to_numpy(float)
        fund_ok = ~np.isnan(fund)
        fund = np.nan_to_num(fund, nan=0.0)
    return {
        "index": df.index,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "vol_sma": sma(volume, 20),
        "atr": atr(high, low, close),
        "rsi": rsi(close),
        "hist": hist,
        "k": ks,
        "d": ds,
        "r1": r1,
        "s1": s1,
        "r2": r2,
        "s2": s2,
        "funding": fund,
        "funding_ok": fund_ok,
    }


def catalog() -> list[Spec]:
    specs = [
        Spec("baseline", "固定止盈止损", "止损 1R，止盈 2R", "baseline", {"sl": 1.0, "tp": 2.0}),
    ]
    for k in (2.0, 3.0, 4.0):
        specs.append(Spec("chandelier", "吊灯止损", f"{k:g} 倍 ATR", "trail", {"k": k}))
    for r_take, frac in ((1.0, 0.5), (1.5, 0.5), (2.0, 0.5)):
        specs.append(Spec("partial_r", "分段止盈", f"先平 {frac:.0%} 于 {r_take:g}R，余下吊灯", "partial", {"r": r_take, "frac": frac}))
    for band in (0.002, 0.004, 0.008):
        specs.append(Spec("pivot", "枢轴止盈", f"靠近对侧枢轴 {band * 100:g}%", "signal", {"band": band}))
    for look in (10, 20):
        specs.append(Spec("rsi_div", "RSI 背离", f"回看 {look} 根", "signal", {"look": look}))
    for bars in (3, 4):
        specs.append(Spec("macd_fade", "MACD 柱缩短", f"连续 {bars} 根缩短", "signal", {"bars": bars}))
    specs.append(Spec("kdj", "KDJ 交叉", "K 反向穿越 D", "signal", {}))
    for thr in (0.0003, 0.0008):
        specs.append(Spec("funding", "资金费率极端", f"费率绝对值超过 {thr * 100:.2f}%", "signal", {"thr": thr}))
    for mult in (2.0, 3.0):
        specs.append(Spec("volume", "放量高潮", f"量能超过 {mult:g} 倍且反向 K 线", "signal", {"mult": mult}))
    for bars in (12, 24, 48):
        specs.append(Spec("time", "时间出场", f"持有 {bars} 根后平仓", "time", {"bars": bars}))
    specs.append(Spec("combo_partial_trail", "分段止盈 + 吊灯", "1.5R 减半，余下 3 倍 ATR 吊灯", "combo", {}))
    specs.append(Spec("combo_exhaust", "动量衰竭合计", "RSI 背离、MACD 缩短或 KDJ 反向交叉", "combo", {}))
    specs.append(Spec("combo_location", "位置或高潮", "靠近枢轴，或放量反向 K 线", "combo", {}))
    specs.append(Spec("combo_trail_time", "吊灯 + 时间", "3 倍 ATR 吊灯，最久 48 根", "combo", {}))
    return specs


def _fill(price: float, side: int, leaving: bool) -> float:
    slip = SLIP if leaving else SLIP
    return price * (1.0 + side * slip) if not leaving else price * (1.0 - side * slip)


def _pnl(side: int, entry: float, exit_px: float, fraction: float) -> float:
    if entry <= 0 or fraction <= 0:
        return 0.0
    gross = side * (exit_px / entry - 1.0) * fraction
    fees = FEE * fraction * 2.0
    return gross - fees


def _near(price: float, level: float, band: float) -> bool:
    if not math.isfinite(level) or level <= 0 or price <= 0:
        return False
    return abs(price - level) / price <= band


def _rsi_div(ctx, i: int, side: int, look: int) -> bool:
    if i <= look:
        return False
    window_h = ctx["high"][i - look:i + 1]
    window_l = ctx["low"][i - look:i + 1]
    window_r = ctx["rsi"][i - look:i + 1]
    if np.isnan(window_r).any():
        return False
    if side > 0:
        now = int(np.argmax(window_h))
        prev = int(np.argmax(window_h[: max(1, now)]))
        if now < 3:
            return False
        return window_h[now] > window_h[prev] and window_r[now] < window_r[prev] and window_r[now] > 55
    now = int(np.argmin(window_l))
    prev = int(np.argmin(window_l[: max(1, now)]))
    if now < 3:
        return False
    return window_l[now] < window_l[prev] and window_r[now] > window_r[prev] and window_r[now] < 45


def _macd_fade(ctx, i: int, side: int, bars: int) -> bool:
    if i < bars:
        return False
    hist = ctx["hist"]
    seq = hist[i - bars + 1:i + 1]
    if np.isnan(seq).any():
        return False
    fading = all(seq[k] < seq[k - 1] for k in range(1, len(seq))) if side > 0 else all(seq[k] > seq[k - 1] for k in range(1, len(seq)))
    if side > 0:
        return fading and seq[-1] > 0
    return fading and seq[-1] < 0


def _kdj_cross(ctx, i: int, side: int) -> bool:
    if i < 1 or math.isnan(ctx["k"][i]) or math.isnan(ctx["d"][i]) or math.isnan(ctx["k"][i - 1]):
        return False
    if side > 0:
        return ctx["k"][i - 1] >= ctx["d"][i - 1] and ctx["k"][i] < ctx["d"][i]
    return ctx["k"][i - 1] <= ctx["d"][i - 1] and ctx["k"][i] > ctx["d"][i]


def _funding_hit(ctx, i: int, side: int, thr: float) -> bool:
    if not ctx["funding_ok"][i]:
        return False
    rate = ctx["funding"][i]
    if side > 0:
        return rate > thr
    return rate < -thr


def _volume_hit(ctx, i: int, side: int, mult: float) -> bool:
    base = ctx["vol_sma"][i]
    atr_v = ctx["atr"][i]
    if not math.isfinite(base) or base <= 0 or not math.isfinite(atr_v):
        return False
    wide = (ctx["high"][i] - ctx["low"][i]) > atr_v
    against = ctx["close"][i] < ctx["open"][i] if side > 0 else ctx["close"][i] > ctx["open"][i]
    return ctx["volume"][i] > mult * base and wide and against


def _pivot_hit(ctx, i: int, side: int, band: float) -> bool:
    price = ctx["close"][i]
    levels = (ctx["r1"][i], ctx["r2"][i]) if side > 0 else (ctx["s1"][i], ctx["s2"][i])
    return any(_near(price, level, band) for level in levels)


def simulate(ctx: dict, entries: list[tuple[int, int]], spec: Spec, start: int = 0, end: int | None = None) -> list[dict]:
    n = len(ctx["close"])
    end = n if end is None else min(end, n)
    entries = [(i, side) for i, side in entries if start <= i < end - 2]
    trades = []
    cursor = start
    for entry_i, side in entries:
        if entry_i < cursor:
            continue
        trade = _one_trade(ctx, entry_i, side, spec, end)
        if trade is None:
            continue
        trades.append(trade)
        cursor = trade["exit_i"] + 1
    return trades


def _one_trade(ctx, entry_i: int, side: int, spec: Spec, end: int) -> dict | None:
    atr0 = ctx["atr"][entry_i - 1] if entry_i else ctx["atr"][entry_i]
    if not math.isfinite(atr0) or atr0 <= 0:
        return None
    entry = _fill(ctx["open"][entry_i], side, leaving=False)
    r_dist = R_MULT * atr0
    extreme = ctx["high"][entry_i] if side > 0 else ctx["low"][entry_i]
    remaining = 1.0
    realized = 0.0
    partial_done = False
    exit_i = end - 1
    reason = "窗口结束"
    for i in range(entry_i, end):
        high = ctx["high"][i]
        low = ctx["low"][i]
        close = ctx["close"][i]
        if side > 0:
            extreme = max(extreme, high)
        else:
            extreme = min(extreme, low)
        action, px, why = _decide(ctx, spec, i, entry_i, side, entry, r_dist, extreme, partial_done, remaining)
        if action == "partial" and not partial_done:
            frac = min(remaining, float(spec.params.get("frac", 0.5)))
            realized += _pnl(side, entry, _fill(px, side, True), frac)
            remaining -= frac
            partial_done = True
            if remaining <= 0.02:
                exit_i = i
                reason = why
                remaining = 0.0
                break
            continue
        if action == "close":
            realized += _pnl(side, entry, _fill(px, side, True), remaining)
            remaining = 0.0
            exit_i = i
            reason = why
            break
    if remaining > 0:
        realized += _pnl(side, entry, _fill(ctx["close"][end - 1], side, True), remaining)
        exit_i = end - 1
        reason = "窗口结束"
    return {
        "entry_i": entry_i,
        "exit_i": exit_i,
        "side": side,
        "pnl": realized,
        "hold": exit_i - entry_i + 1,
        "reason": reason,
    }


def _decide(ctx, spec: Spec, i, entry_i, side, entry, r_dist, extreme, partial_done, remaining):
    high = ctx["high"][i]
    low = ctx["low"][i]
    close = ctx["close"][i]
    atr_v = ctx["atr"][i] if math.isfinite(ctx["atr"][i]) else r_dist / R_MULT
    kind = spec.id

    def stop_hit(stop_px):
        if side > 0 and low <= stop_px:
            return min(ctx["open"][i], stop_px)
        if side < 0 and high >= stop_px:
            return max(ctx["open"][i], stop_px)
        return None

    def target_hit(target):
        if side > 0 and high >= target:
            return max(ctx["open"][i], target)
        if side < 0 and low <= target:
            return min(ctx["open"][i], target)
        return None

    if kind == "baseline":
        stop = entry - side * spec.params["sl"] * r_dist
        target = entry + side * spec.params["tp"] * r_dist
        hit = stop_hit(stop)
        if hit is not None:
            return "close", hit, "固定止损"
        hit = target_hit(target)
        if hit is not None:
            return "close", hit, "固定止盈"
        return "hold", close, ""

    if kind in ("chandelier", "combo_trail_time"):
        k = 3.0 if kind == "combo_trail_time" else spec.params["k"]
        stop = (extreme - k * atr_v) if side > 0 else (extreme + k * atr_v)
        hit = stop_hit(stop)
        if hit is not None:
            return "close", hit, "吊灯止损"
        if kind == "combo_trail_time" and i - entry_i + 1 >= 48:
            return "close", close, "时间出场"
        return "hold", close, ""

    if kind in ("partial_r", "combo_partial_trail"):
        r_take = 1.5 if kind == "combo_partial_trail" else spec.params["r"]
        stop = entry - side * r_dist
        if partial_done:
            stop = entry
        hit = stop_hit(stop)
        if hit is not None:
            return "close", hit, "止损"
        if not partial_done:
            target = entry + side * r_take * r_dist
            hit = target_hit(target)
            if hit is not None:
                return "partial", hit, "分段止盈"
        trail = (extreme - 3 * atr_v) if side > 0 else (extreme + 3 * atr_v)
        hit = stop_hit(trail)
        if hit is not None and partial_done:
            return "close", hit, "余仓吊灯"
        return "hold", close, ""

    if kind == "time":
        if i - entry_i + 1 >= spec.params["bars"]:
            return "close", close, "时间出场"
        return "hold", close, ""

    signal = False
    why = ""
    if kind == "pivot" and _pivot_hit(ctx, i, side, spec.params["band"]):
        signal, why = True, "靠近枢轴"
    elif kind == "rsi_div" and _rsi_div(ctx, i, side, spec.params["look"]):
        signal, why = True, "RSI 背离"
    elif kind == "macd_fade" and _macd_fade(ctx, i, side, spec.params["bars"]):
        signal, why = True, "MACD 柱缩短"
    elif kind == "kdj" and _kdj_cross(ctx, i, side):
        signal, why = True, "KDJ 交叉"
    elif kind == "funding" and _funding_hit(ctx, i, side, spec.params["thr"]):
        signal, why = True, "资金费率极端"
    elif kind == "volume" and _volume_hit(ctx, i, side, spec.params["mult"]):
        signal, why = True, "放量高潮"
    elif kind == "combo_exhaust":
        if _rsi_div(ctx, i, side, 14) or _macd_fade(ctx, i, side, 3) or _kdj_cross(ctx, i, side):
            signal, why = True, "动量衰竭"
    elif kind == "combo_location":
        if _pivot_hit(ctx, i, side, 0.004) or _volume_hit(ctx, i, side, 2.5):
            signal, why = True, "位置或高潮"
    if signal and i > entry_i:
        return "close", close, why
    return "hold", close, ""


def metrics_of(trades: list[dict]) -> dict:
    if not trades:
        return {
            "trades": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
            "payoff": None, "expectancy": None, "profit_factor": None, "max_drawdown": None,
        }
    pnl = np.array([t["pnl"] for t in trades], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    win_rate = float(len(wins) / len(pnl))
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    payoff = (avg_win / abs(avg_loss)) if avg_loss < 0 else None
    expectancy = float(pnl.mean())
    gross_loss = abs(float(losses.sum())) if len(losses) else 0.0
    profit_factor = float(wins.sum() / gross_loss) if gross_loss else None
    equity = np.cumprod(1.0 + pnl)
    peak = np.maximum.accumulate(equity)
    dd = float((equity / peak - 1.0).min()) if len(equity) else 0.0
    return {
        "trades": int(len(pnl)),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 5),
        "avg_loss": round(avg_loss, 5),
        "payoff": None if payoff is None else round(payoff, 3),
        "expectancy": round(expectancy, 5),
        "profit_factor": None if profit_factor is None or not math.isfinite(profit_factor) else round(profit_factor, 3),
        "max_drawdown": round(dd, 4),
    }


def _objective(row: dict) -> float:
    if row["trades"] < 12 or row["expectancy"] is None:
        return -1e9
    return float(row["expectancy"])


def walk_forward(ctx: dict, entries: list[tuple[int, int]], specs: list[Spec]) -> dict:
    n = len(ctx["close"])
    edges = [int(n * i / 4) for i in range(5)]
    chosen = []
    oos_by_spec = {id(spec): [] for spec in specs}
    for fold in range(1, 4):
        train_end = edges[fold]
        test_end = edges[fold + 1]
        best = specs[0]
        best_score = -1e9
        for spec in specs:
            train = simulate(ctx, entries, spec, 0, train_end)
            score = _objective(metrics_of(train))
            if score > best_score:
                best, best_score = spec, score
            test = simulate(ctx, entries, spec, train_end, test_end)
            oos_by_spec[id(spec)].extend(test)
        chosen.append(best)
    # 三条样本外折各自用当时训练段选出的规则，再拼起来，作为这条入场来源的结论。
    stitched = []
    for fold, spec in enumerate(chosen, start=1):
        stitched.extend(simulate(ctx, entries, spec, edges[fold], edges[fold + 1]))
    cloud = []
    for spec in specs:
        stats = metrics_of(oos_by_spec[id(spec)])
        flag = bool(stats["win_rate"] is not None and stats["win_rate"] > 0.8 and (stats["expectancy"] or 0) < 0)
        cloud.append({
            "rule": spec.name,
            "params": spec.label,
            "win_rate": stats["win_rate"],
            "expectancy": stats["expectancy"],
            "payoff": stats["payoff"],
            "trades": stats["trades"],
            "flag": flag,
        })
    names = [spec.name + " · " + spec.label for spec in chosen]
    return {
        "oos": metrics_of(stitched),
        "picked": names,
        "chosen_specs": chosen,
        "cloud": cloud,
    }


def live_advice(df: pd.DataFrame, funding_rate: float | None) -> dict:
    """假设成本就是现价，或者这笔单已经扛过最近 48 根 1 小时。只给研究标签。"""
    if len(df) < 60:
        return {}
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    volume = df["volume"].to_numpy(float)
    atr_v = atr(high, low, close)
    rsi_v = rsi(close)
    _line, _sig, hist = macd(close)
    ks, ds, _js = kdj(high, low, close)
    price = float(close[-1])
    atr_now = float(atr_v[-1]) if math.isfinite(atr_v[-1]) else price * 0.01
    out = {}
    for side, title in ((1, "long"), (-1, "short")):
        extreme = float(np.max(high[-48:])) if side > 0 else float(np.min(low[-48:]))
        trail = extreme - 3 * atr_now if side > 0 else extreme + 3 * atr_now
        flags = []
        breached = close[-1] < trail if side > 0 else close[-1] > trail
        if breached:
            flags.append("现价已经跌破吊灯止损" if side > 0 else "现价已经升破吊灯止损")
        look = 14
        window_h = high[-look:]
        window_l = low[-look:]
        window_r = rsi_v[-look:]
        if np.isfinite(window_r).all():
            if side > 0 and window_h[-1] >= window_h.max() and window_r[-1] < window_r[int(np.argmax(window_h[:-1]))] and window_r[-1] > 55:
                flags.append("价格新高而 RSI 没有新高")
            if side < 0 and window_l[-1] <= window_l.min() and window_r[-1] > window_r[int(np.argmin(window_l[:-1]))] and window_r[-1] < 45:
                flags.append("价格新低而 RSI 没有新低")
        if len(hist) > 4 and np.isfinite(hist[-4:]).all():
            seq = hist[-3:]
            if side > 0 and seq[-1] > 0 and seq[2] < seq[1] < seq[0]:
                flags.append("MACD 红柱在缩短")
            if side < 0 and seq[-1] < 0 and seq[2] > seq[1] > seq[0]:
                flags.append("MACD 绿柱在缩短")
        if np.isfinite(ks[-1]) and np.isfinite(ds[-1]) and np.isfinite(ks[-2]):
            if side > 0 and ks[-2] >= ds[-2] and ks[-1] < ds[-1]:
                flags.append("KDJ 的 K 下穿 D")
            if side < 0 and ks[-2] <= ds[-2] and ks[-1] > ds[-1]:
                flags.append("KDJ 的 K 上穿 D")
        if funding_rate is not None:
            if side > 0 and funding_rate > 0.0005:
                flags.append("资金费率偏高，多头拥挤")
            if side < 0 and funding_rate < -0.0003:
                flags.append("资金费率为负且绝对值偏大，空头拥挤")
        vol_base = sma(volume, 20)[-1]
        if math.isfinite(vol_base) and vol_base > 0 and volume[-1] > 2.5 * vol_base:
            against = close[-1] < open_[-1] if side > 0 else close[-1] > open_[-1]
            if against and (high[-1] - low[-1]) > atr_now:
                flags.append("放量且这根 K 线对着持仓")
        if breached or len(flags) >= 2:
            action = "平仓"
        elif flags:
            action = "减仓"
        else:
            action = "持有"
        reason = "；".join(flags) if flags else "吊灯还在，动量、费率和量能都没有对着这笔仓位"
        account_stop = price * (1.0 - side * 0.15 / 25.0)
        out[title] = {
            "action": action,
            "reason": reason,
            "trail_stop": round(trail, 2 if price >= 100 else 4),
            "account_stop_25x": round(account_stop, 2 if price >= 100 else 4),
            "price": round(price, 2 if price >= 100 else 4),
        }
    return out
