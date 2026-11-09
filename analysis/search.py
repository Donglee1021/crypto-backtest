"""事先定好的策略族，走步选出参数，再按样本外的风险调整后表现排序。

目标是样本外夏普和卡玛，并且扣完手续费、滑点和资金费之后每笔期望要为正。
不按样本内收益挑冠军。杠杆只并列展示，不从里面再挑一个当建议。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from analysis.scoring import atr, boll, ema, rsi, sma

FEE = 0.0005
SLIP = 0.0002
BAR_HOURS = {"1h": 1.0, "4h": 4.0, "1d": 24.0}


@dataclass
class Config:
    family: str
    name: str
    tf: str
    mode: str
    params: dict
    plain: str
    key: str = ""

    def __post_init__(self):
        bits = [self.family, self.tf, self.mode] + [f"{k}={v}" for k, v in sorted(self.params.items())]
        self.key = "|".join(bits)


def _wilder(values: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    if len(values) < n:
        return out
    acc = float(np.nansum(values[:n]))
    out[n - 1] = acc / n
    for i in range(n, len(values)):
        if not math.isfinite(values[i]) or not math.isfinite(out[i - 1]):
            continue
        out[i] = (out[i - 1] * (n - 1) + values[i]) / n
    return out


def adx_values(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    m = len(close)
    up = np.zeros(m)
    down = np.zeros(m)
    tr = np.zeros(m)
    tr[0] = high[0] - low[0]
    for i in range(1, m):
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        up[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        down[i] = down_move if down_move > up_move and down_move > 0 else 0.0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr_s = _wilder(tr, n)
    plus = _wilder(up, n)
    minus = _wilder(down, n)
    dx = np.full(m, np.nan)
    for i in range(m):
        if not math.isfinite(atr_s[i]) or atr_s[i] == 0:
            continue
        pdi = 100 * plus[i] / atr_s[i]
        mdi = 100 * minus[i] / atr_s[i]
        denom = pdi + mdi
        if denom == 0:
            continue
        dx[i] = 100 * abs(pdi - mdi) / denom
    return _wilder(dx, n)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    if p <= 0:
        return -8.0
    if p >= 1:
        return 8.0
    a = [2.50662823884, -18.61500062529, 41.39119773534, -25.44106049637]
    b = [-8.47351093090, 23.08336743743, -21.06224101826, 3.13082909833]
    c = [0.3374754822726147, 0.9761690190917186, 0.160797932314249, 0.0276438810333863, 0.0038405729373609, 0.0003951896511919, 0.0000321767881768, 0.0000002888167364, 0.0000003960315187]
    y = p - 0.5
    if abs(y) < 0.42:
        r = y * y
        return y * (((a[3] * r + a[2]) * r + a[1]) * r + a[0]) / ((((b[3] * r + b[2]) * r + b[1]) * r + b[0]) * r + 1)
    r = p if y > 0 else 1 - p
    r = math.log(-math.log(r))
    t = c[0]
    for coef in c[1:]:
        t = t * r + coef
    return t if y > 0 else -t


def expected_max_sharpe(n_trials: int) -> float:
    """零假设下，N 次独立尝试里样本夏普的期望最大值（以「每个观测」为单位，再由调用方处理）。"""
    if n_trials < 2:
        return 0.0
    gamma = 0.5772156649
    z1 = _norm_ppf(1 - 1 / n_trials)
    z2 = _norm_ppf(1 - 1 / (n_trials * math.e))
    return (1 - gamma) * z1 + gamma * z2


def deflated_sharpe(daily_returns: np.ndarray, n_trials: int) -> float | None:
    x = daily_returns[np.isfinite(daily_returns)]
    if len(x) < 30 or np.std(x) == 0:
        return None
    sr = float(np.mean(x) / np.std(x, ddof=1))
    skew = float(pd.Series(x).skew())
    kurt = float(pd.Series(x).kurtosis()) + 3.0  # 皮尔逊峰度，正态是 3
    if not math.isfinite(skew):
        skew = 0.0
    if not math.isfinite(kurt):
        kurt = 3.0
    sr_star = expected_max_sharpe(n_trials) / math.sqrt(len(x) - 1)
    denom = math.sqrt(max(1e-8, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    stat = (sr - sr_star) * math.sqrt(len(x) - 1) / denom
    return round(_norm_cdf(stat), 3)


def _shift(pos: np.ndarray) -> np.ndarray:
    out = np.zeros(len(pos))
    out[1:] = pos[:-1]
    return out


def run_exposure(close: np.ndarray, exposure: np.ndarray, funding: np.ndarray, bar_hours: float, leverage: float = 1.0):
    """exposure 在收盘决定，下一根才持有。leverage 是额外倍数，波动率目标已经写进 exposure 时传 1。"""
    n = len(close)
    px = np.zeros(n)
    px[1:] = close[1:] / close[:-1] - 1.0
    held = _shift(exposure) * leverage
    prev = np.zeros(n)
    prev[1:] = held[:-1]
    turnover = np.abs(held - prev)
    fund = np.nan_to_num(funding, nan=0.0)
    net = held * px - turnover * (FEE + SLIP) - held * fund * (bar_hours / 8.0)
    net = np.nan_to_num(net, nan=0.0)
    # 账户不能穿仓成负数。单根亏损超过本金时，按亏光处理。
    net = np.maximum(net, -0.999)
    return net, held


def _trades(held: np.ndarray, net: np.ndarray) -> list[float]:
    sign = np.sign(held)
    trades = []
    cur = 0.0
    eq = 1.0
    for i in range(len(sign)):
        side = sign[i]
        if side != cur:
            if cur != 0.0:
                trades.append(eq - 1.0)
            eq = 1.0
            cur = side
        if cur != 0.0:
            eq *= 1.0 + net[i]
    if cur != 0.0:
        trades.append(eq - 1.0)
    return trades


def summarize(index: pd.DatetimeIndex, net: np.ndarray, held: np.ndarray) -> dict:
    equity = np.cumprod(1.0 + net)
    series = pd.Series(equity, index=index)
    daily = series.resample("1D").last().dropna()
    dret = daily.pct_change().dropna()
    days = max(1, len(dret))
    if len(dret) > 5 and float(dret.std()) > 1e-8:
        sharpe = float(dret.mean() / dret.std() * math.sqrt(365))
    else:
        sharpe = 0.0
    ann = float(daily.iloc[-1] ** (365 / max(1, (daily.index[-1] - daily.index[0]).days)) - 1) if len(daily) > 2 else 0.0
    peak = np.maximum.accumulate(equity)
    dd = float((equity / np.maximum(peak, 1e-12) - 1).min())
    calmar = ann / abs(dd) if dd < -1e-6 else (99.0 if ann > 0 else 0.0)
    trades = _trades(held, net)
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    win_rate = len(wins) / len(trades) if trades else None
    expectancy = float(np.mean(trades)) if trades else None
    years = max(1e-6, days / 365)
    in_mkt = float(np.mean(np.abs(held) > 0.05))
    return {
        "ann_return": round(ann, 4),
        "max_drawdown": round(dd, 4),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3) if math.isfinite(calmar) else None,
        "win_rate": None if win_rate is None else round(win_rate, 4),
        "expectancy": None if expectancy is None else round(expectancy, 5),
        "trades": len(trades),
        "trades_per_year": round(len(trades) / years, 2),
        "in_market": round(in_mkt, 3),
        "days": int(days),
        "daily_index": daily.index,
        "daily_equity": daily.to_numpy(float),
        "daily_ret": dret,
    }


def donchian_pos(high, low, close, entry_n: int, exit_n: int) -> np.ndarray:
    hh = pd.Series(high).rolling(entry_n).max().shift(1).to_numpy()
    ll = pd.Series(low).rolling(entry_n).min().shift(1).to_numpy()
    xh = pd.Series(high).rolling(exit_n).max().shift(1).to_numpy()
    xl = pd.Series(low).rolling(exit_n).min().shift(1).to_numpy()
    pos = np.zeros(len(close))
    cur = 0
    for i in range(len(close)):
        if not math.isfinite(hh[i]):
            pos[i] = 0
            continue
        if close[i] > hh[i]:
            cur = 1
        elif close[i] < ll[i]:
            cur = -1
        elif cur > 0 and close[i] < xl[i]:
            cur = 0
        elif cur < 0 and close[i] > xh[i]:
            cur = 0
        pos[i] = cur
    return pos


def apply_trail(high, low, close, raw: np.ndarray, atr_v: np.ndarray, k: float) -> np.ndarray:
    if not k:
        return raw
    pos = np.zeros(len(close))
    cur = 0
    extreme = 0.0
    for i in range(len(close)):
        want = raw[i]
        if not math.isfinite(atr_v[i]):
            pos[i] = 0
            cur = 0
            continue
        if cur == 0:
            if want != 0:
                cur = 1 if want > 0 else -1
                extreme = high[i] if cur > 0 else low[i]
        else:
            if cur > 0:
                extreme = max(extreme, high[i])
                if low[i] <= extreme - k * atr_v[i] or want <= 0:
                    cur = 0
            else:
                extreme = min(extreme, low[i])
                if high[i] >= extreme + k * atr_v[i] or want >= 0:
                    cur = 0
        pos[i] = cur
    return pos


def supertrend_pos(high, low, close, period: int, mult: float) -> np.ndarray:
    atr_v = atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    basic_u = hl2 + mult * atr_v
    basic_l = hl2 - mult * atr_v
    final_u = np.copy(basic_u)
    final_l = np.copy(basic_l)
    trend = np.zeros(len(close))
    for i in range(1, len(close)):
        if not math.isfinite(atr_v[i]) or not math.isfinite(final_u[i - 1]):
            continue
        final_u[i] = basic_u[i] if (basic_u[i] < final_u[i - 1] or close[i - 1] > final_u[i - 1]) else final_u[i - 1]
        final_l[i] = basic_l[i] if (basic_l[i] > final_l[i - 1] or close[i - 1] < final_l[i - 1]) else final_l[i - 1]
        prev = trend[i - 1]
        if prev >= 0:
            trend[i] = -1 if close[i] < final_l[i] else 1
        else:
            trend[i] = 1 if close[i] > final_u[i] else -1
    return trend


def ma_pos(close, fast: int, slow: int, kind: str) -> np.ndarray:
    fn = ema if kind == "ema" else sma
    a = fn(close, fast)
    b = fn(close, slow)
    pos = np.zeros(len(close))
    for i in range(len(close)):
        if math.isfinite(a[i]) and math.isfinite(b[i]):
            pos[i] = 1 if a[i] > b[i] else -1
    return pos


def tsmom_pos(close, look: int, long_flat: bool) -> np.ndarray:
    past = np.full(len(close), np.nan)
    past[look:] = close[look:] / close[:-look] - 1.0
    pos = np.zeros(len(close))
    for i in range(len(close)):
        if not math.isfinite(past[i]):
            continue
        if past[i] > 0:
            pos[i] = 1
        elif not long_flat and past[i] < 0:
            pos[i] = -1
    return pos


def vol_target_pos(close, look_mom: int, vol_look: int, target: float, bar_hours: float) -> np.ndarray:
    direction = tsmom_pos(close, look_mom, long_flat=False)
    ret = np.zeros(len(close))
    ret[1:] = close[1:] / close[:-1] - 1.0
    periods = (24 / bar_hours) * 365
    rv = pd.Series(ret).rolling(vol_look).std().to_numpy() * math.sqrt(periods)
    scale = np.zeros(len(close))
    for i in range(len(close)):
        if math.isfinite(rv[i]) and rv[i] > 0.05:
            scale[i] = min(3.0, target / rv[i])
    step = max(1, int(round(24 / bar_hours)))
    pos = np.zeros(len(close))
    cur = 0.0
    for i in range(len(close)):
        want = direction[i] * scale[i]
        if i % step == 0 or np.sign(want) != np.sign(cur) or cur == 0:
            cur = want
        pos[i] = cur
    return pos


def rsi_range_pos(high, low, close, adx_max: float) -> np.ndarray:
    strength = adx_values(high, low, close, 14)
    osc = rsi(close, 14)
    pos = np.zeros(len(close))
    cur = 0
    for i in range(len(close)):
        if not math.isfinite(strength[i]) or not math.isfinite(osc[i]):
            pos[i] = 0
            continue
        tradable = strength[i] < adx_max
        if cur > 0 and osc[i] > 50:
            cur = 0
        elif cur < 0 and osc[i] < 50:
            cur = 0
        if tradable and cur == 0:
            if osc[i] < 30:
                cur = 1
            elif osc[i] > 70:
                cur = -1
        if not tradable and cur != 0 and strength[i] > adx_max + 5:
            cur = 0
        pos[i] = cur
    return pos


def bb_range_pos(high, low, close, adx_max: float) -> np.ndarray:
    strength = adx_values(high, low, close, 14)
    mid, up, lo = boll(close, 20, 2.0)
    pos = np.zeros(len(close))
    cur = 0
    for i in range(len(close)):
        if not math.isfinite(strength[i]) or not math.isfinite(mid[i]):
            continue
        if cur > 0 and close[i] > mid[i]:
            cur = 0
        elif cur < 0 and close[i] < mid[i]:
            cur = 0
        if strength[i] < adx_max and cur == 0:
            if close[i] < lo[i]:
                cur = 1
            elif close[i] > up[i]:
                cur = -1
        if strength[i] > adx_max + 5:
            cur = 0
        pos[i] = cur
    return pos


def funding_pos(rate: np.ndarray, known: np.ndarray, threshold: float) -> np.ndarray:
    pos = np.zeros(len(rate))
    for i in range(len(rate)):
        if not known[i]:
            continue
        if rate[i] > threshold:
            pos[i] = -1
        elif rate[i] < -threshold:
            pos[i] = 1
    return pos


def build_configs() -> list[Config]:
    configs = []
    donchian_grid = {
        "1h": [(24, 12), (72, 36), (168, 72)],
        "4h": [(20, 10), (42, 20), (90, 40)],
        "1d": [(20, 10), (55, 20), (100, 40)],
    }
    for tf, pairs in donchian_grid.items():
        for entry_n, exit_n in pairs:
            for trail in (0, 3):
                plain = f"收盘突破过去 {entry_n} 根的高点就做多，跌破低点就做空。回到过去 {exit_n} 根的反向通道就先平仓。"
                if trail:
                    plain += f" 另外用 {trail} 倍 ATR 跟踪止损，碰到就平仓。"
                configs.append(Config("donchian", "唐奇安突破", tf, "ls", {"entry": entry_n, "exit": exit_n, "trail": trail}, plain))
    ma_grid = [("ema", 10, 30), ("ema", 20, 50), ("ema", 50, 200), ("sma", 20, 50), ("sma", 50, 200)]
    for tf in ("1h", "4h", "1d"):
        for kind, fast, slow in ma_grid:
            label = "EMA" if kind == "ema" else "SMA"
            for trail in (0, 3):
                plain = f"{label}{fast} 上穿 {label}{slow} 做多，下穿做空。"
                if trail:
                    plain += f" 方向对了也用 {trail} 倍 ATR 跟踪止损。"
                configs.append(Config("ma", "均线交叉", tf, kind, {"fast": fast, "slow": slow, "trail": trail}, plain))
    for tf in ("1h", "4h", "1d"):
        for period, mult in ((10, 3), (14, 2), (14, 3)):
            plain = f"超级趋势（{period} 期 ATR 的 {mult:g} 倍）。价格在轨道上方做多，跌到轨道下方做空。"
            configs.append(Config("supertrend", "超级趋势", tf, "ls", {"period": period, "mult": mult}, plain))
    mom_grid = {"1h": (24 * 7, 24 * 28, 24 * 84), "4h": (6 * 7, 6 * 28, 6 * 84), "1d": (7, 28, 84)}
    for tf, looks in mom_grid.items():
        for look in looks:
            weeks = look / {"1h": 24 * 7, "4h": 6 * 7, "1d": 7}[tf]
            for long_flat in (False, True):
                mode = "longflat" if long_flat else "ls"
                plain = f"看过去约 {weeks:g} 周的涨跌。涨了做多。"
                plain += "跌了空仓。" if long_flat else "跌了做空。"
                configs.append(Config("tsmom", "时序动量", tf, mode, {"look": look}, plain))
    for tf in ("1h", "4h", "1d"):
        look = {"1h": 24 * 28, "4h": 6 * 28, "1d": 28}[tf]
        vol_look = {"1h": 24 * 20, "4h": 6 * 20, "1d": 20}[tf]
        for target in (0.30, 0.60):
            plain = f"方向仍看过去约 4 周的涨跌，仓位大小按实现波动调整，目标年化波动大约 {int(target*100)}%，最高放到 3 倍。波动大就缩小，波动小就放大。这是研究里的缩放，不是杠杆建议。"
            configs.append(Config("voltarget", "波动率目标趋势", tf, "ls", {"look": look, "vol_look": vol_look, "target": target}, plain))
    for tf in ("1h", "4h", "1d"):
        for adx_max in (20, 25):
            plain = f"只在 ADX 低于 {adx_max}（更像震荡）时做均值回归：RSI 低于 30 做多，高于 70 做空，回到 50 附近平仓。有趋势就退出。"
            configs.append(Config("rsi_adx", "震荡市 RSI", tf, "mr", {"adx": adx_max}, plain))
            plain = f"只在 ADX 低于 {adx_max} 时，跌破布林下轨做多，升破上轨做空，回到中轨平仓。"
            configs.append(Config("bb_adx", "震荡市布林", tf, "mr", {"adx": adx_max}, plain))
    for tf in ("1h", "4h", "1d"):
        configs.append(Config(
            "funding", "资金费率Carry", tf, "carry", {"thr": 0.00005},
            "资金费率为正（多头付钱）就做空，为负就做多，从而站在收钱的一边。费率接近 0 时空仓。历史费率只有最近几个月，更早的日子这套规则是空仓。",
        ))
        configs.append(Config(
            "funding", "资金费率极端逆向", tf, "contrarian", {"thr": 0.0003},
            "只有费率绝对值大过 0.03% 才进场：多头拥挤就做空，空头拥挤就做多。其余时间空仓。",
        ))
    for look, label in ((7, "1周"), (30, "1个月"), (90, "3个月")):
        configs.append(Config(
            "rotation", "相对强弱轮动", "1d", "longflat", {"look": look},
            f"比较 BTC 和 ETH 过去{label}谁涨得多，只持有更强的那个，另一个空仓。",
        ))
        configs.append(Config(
            "rotation", "相对强弱对冲", "1d", "ls", {"look": look},
            f"过去{label}更强的一边做多，更弱的一边做空，多空名义大致对半。",
        ))
    return configs


def _signal(df: pd.DataFrame, cfg: Config, funding: np.ndarray, known: np.ndarray) -> np.ndarray:
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    hours = BAR_HOURS[cfg.tf]
    if cfg.family == "donchian":
        raw = donchian_pos(h, l, c, int(cfg.params["entry"]), int(cfg.params["exit"]))
        if cfg.params["trail"]:
            raw = apply_trail(h, l, c, raw, atr(h, l, c, 14), float(cfg.params["trail"]))
        return raw
    if cfg.family == "ma":
        raw = ma_pos(c, int(cfg.params["fast"]), int(cfg.params["slow"]), cfg.mode)
        if cfg.params["trail"]:
            raw = apply_trail(h, l, c, raw, atr(h, l, c, 14), float(cfg.params["trail"]))
        return raw
    if cfg.family == "supertrend":
        return supertrend_pos(h, l, c, int(cfg.params["period"]), float(cfg.params["mult"]))
    if cfg.family == "tsmom":
        return tsmom_pos(c, int(cfg.params["look"]), cfg.mode == "longflat")
    if cfg.family == "voltarget":
        return vol_target_pos(c, int(cfg.params["look"]), int(cfg.params["vol_look"]), float(cfg.params["target"]), hours)
    if cfg.family == "rsi_adx":
        return rsi_range_pos(h, l, c, float(cfg.params["adx"]))
    if cfg.family == "bb_adx":
        return bb_range_pos(h, l, c, float(cfg.params["adx"]))
    if cfg.family == "funding":
        return funding_pos(funding, known, float(cfg.params["thr"]))
    raise KeyError(cfg.family)


def _align_funding(index: pd.DatetimeIndex, funding: pd.Series | None) -> tuple[np.ndarray, np.ndarray]:
    if funding is None or not len(funding):
        return np.zeros(len(index)), np.zeros(len(index), dtype=bool)
    aligned = funding.reindex(index, method="ffill")
    known = np.array(aligned.notna().to_numpy(), dtype=bool)
    # 第一笔已知费率之前不算已知。
    values = np.array(aligned.fillna(0).to_numpy(float), dtype=float)
    if known.any():
        first = int(np.argmax(known))
        known[:first] = False
        values[:first] = 0.0
    return values, known


def prepare_frames(market: dict[str, dict[str, pd.DataFrame]], funding: dict[str, pd.Series | None]) -> dict:
    """market[coin][tf] = ohlcv. 所有品种切到同一段，方便比较。"""
    start = max(market["BTC"]["1h"].index[0], market["ETH"]["1h"].index[0])
    end = min(market["BTC"]["1h"].index[-1], market["ETH"]["1h"].index[-1])
    out = {}
    for coin in ("BTC", "ETH"):
        out[coin] = {}
        for tf, df in market[coin].items():
            sl = df[(df.index >= start) & (df.index <= end)].copy()
            fund, known = _align_funding(sl.index, funding.get(coin))
            out[coin][tf] = (sl, fund, known)
    return out, start, end


def evaluate_config(frame, fund, known, cfg: Config, leverage: float = 1.0):
    pos = _signal(frame, cfg, fund, known)
    net, held = run_exposure(frame["close"].to_numpy(float), pos, fund, BAR_HOURS[cfg.tf], leverage)
    return pos, net, held


def _sharpe_slice(index, net, start, end) -> float:
    series = pd.Series(np.cumprod(1.0 + net), index=index)
    daily = series.resample("1D").last().dropna()
    part = daily[(daily.index >= start) & (daily.index < end)]
    if len(part) < 8:
        return -999.0
    ret = part.pct_change().dropna()
    if len(ret) < 8 or float(ret.std()) < 1e-8:
        return -999.0
    return float(ret.mean() / ret.std() * math.sqrt(365))


def _neighbors(cfg: Config, pool: list[Config]) -> list[Config]:
    found = []
    for other in pool:
        if other.family != cfg.family or other.tf != cfg.tf or other.mode != cfg.mode or other.key == cfg.key:
            continue
        diffs = [k for k in set(cfg.params) | set(other.params) if cfg.params.get(k) != other.params.get(k)]
        if len(diffs) != 1:
            continue
        key = diffs[0]
        values = sorted({c.params[key] for c in pool if c.family == cfg.family and c.tf == cfg.tf and c.mode == cfg.mode and key in c.params})
        if cfg.params[key] not in values or other.params[key] not in values:
            continue
        if abs(values.index(cfg.params[key]) - values.index(other.params[key])) == 1:
            found.append(other)
    return found


def walk_family(family: str, configs: list[Config], prepared, master: pd.DatetimeIndex) -> dict:
    group = [c for c in configs if c.family == family]
    edges = [master[min(len(master) - 1, int(len(master) * x))] for x in (0.40, 0.55, 0.70, 0.85, 1.0)]
    # 预先算好每个配置的日收益，走步只做切片。
    cache = {}
    for cfg in group:
        cache[cfg.key] = {}
        if family == "rotation":
            cache[cfg.key]["book"] = _rotation_daily(prepared, cfg)
        else:
            for coin in ("BTC", "ETH"):
                frame, fund, known = prepared[coin][cfg.tf]
                _pos, net, held = evaluate_config(frame, fund, known, cfg, 1.0)
                stats = summarize(frame.index, net, held)
                daily_ret = _daily_returns(frame.index, net)
                cache[cfg.key][coin] = {"net": net, "held": held, "index": frame.index, "stats": stats, "daily": daily_ret, "pos": _pos}

    def train_score(cfg: Config, start, end) -> float:
        if family == "rotation":
            ret = cache[cfg.key]["book"]
            part = ret[(ret.index >= start) & (ret.index < end)]
            if len(part) < 20 or float(part.std()) < 1e-8:
                return -999.0
            if float((part != 0).mean()) < 0.2:
                return -999.0
            return float(part.mean() / part.std() * math.sqrt(365))
        scores = []
        for coin in ("BTC", "ETH"):
            ret = cache[cfg.key][coin]["daily"]
            part = ret[(ret.index >= start) & (ret.index < end)].dropna()
            held = cache[cfg.key][coin]["held"]
            idx = cache[cfg.key][coin]["index"]
            mask = (idx >= start) & (idx < end)
            if mask.sum() < 10 or float(np.mean(np.abs(held[mask]) > 0.05)) < 0.08:
                return -999.0
            if len(part) < 20 or float(part.std()) < 1e-8:
                return -999.0
            scores.append(float(part.mean() / part.std() * math.sqrt(365)))
        return min(scores)

    chosen = []
    oos_parts = {coin: [] for coin in ("BTC", "ETH")}
    oos_trades = {coin: [] for coin in ("BTC", "ETH")}
    oos_book = []
    neighbor_scores = []
    lev_parts = {mult: {coin: [] for coin in ("BTC", "ETH")} for mult in (1, 2, 3)}
    for fold in range(4):
        train_start = master[0]
        test_start = edges[fold]
        test_end = edges[fold + 1] if fold < 3 else master[-1] + pd.Timedelta(days=1)
        best = None
        best_score = -999.0
        scored = []
        for cfg in group:
            score = train_score(cfg, train_start, test_start)
            scored.append((score, cfg))
            if score > best_score:
                best, best_score = cfg, score
        if best is None:
            continue
        chosen.append(best)
        if family == "rotation":
            part = cache[best.key]["book"]
            oos_book.append(part[(part.index >= test_start) & (part.index < test_end)])
        else:
            for coin in ("BTC", "ETH"):
                blob = cache[best.key][coin]
                part = blob["daily"]
                oos_parts[coin].append(part[(part.index >= test_start) & (part.index < test_end)])
                idx = blob["index"]
                mask = np.asarray((idx >= test_start) & (idx < test_end))
                oos_trades[coin].extend(_trades(blob["held"][mask], blob["net"][mask]))
                for mult in (2, 3):
                    net_l, _held_l = run_exposure(
                        prepared[coin][best.tf][0]["close"].to_numpy(float),
                        blob["pos"],
                        prepared[coin][best.tf][1],
                        BAR_HOURS[best.tf],
                        mult,
                    )
                    daily_l = _daily_returns(idx, net_l)
                    lev_parts[mult][coin].append(daily_l[(daily_l.index >= test_start) & (daily_l.index < test_end)])
        neigh = _neighbors(best, group)
        vals = []
        for item in neigh:
            if family == "rotation":
                ret = cache[item.key]["book"]
                sl = ret[(ret.index >= test_start) & (ret.index < test_end)]
                if len(sl) > 10 and float(sl.std()) > 1e-8:
                    vals.append(float(sl.mean() / sl.std() * math.sqrt(365)))
            else:
                pair = []
                for coin in ("BTC", "ETH"):
                    ret = cache[item.key][coin]["daily"]
                    sl = ret[(ret.index >= test_start) & (ret.index < test_end)].dropna()
                    if len(sl) > 10 and float(sl.std()) > 1e-8:
                        pair.append(float(sl.mean() / sl.std() * math.sqrt(365)))
                if len(pair) == 2:
                    vals.append(min(pair))
        if vals:
            neighbor_scores.append(float(np.median(vals)))
    result = {
        "family": family,
        "name": group[0].name,
        "chosen": [{"tf": c.tf, "mode": c.mode, "params": c.params, "plain": c.plain, "key": c.key} for c in chosen],
        "neighbor_sharpe": None if not neighbor_scores else round(float(np.median(neighbor_scores)), 3),
    }
    if family == "rotation":
        stitched = pd.concat(oos_book) if oos_book else pd.Series(dtype=float)
        stitched = stitched[~stitched.index.duplicated(keep="last")].sort_index()
        result["book"] = _metrics_from_daily(stitched)
        result["book_ret"] = stitched
        result["live_cfg"] = chosen[-1] if chosen else None
    else:
        for coin in ("BTC", "ETH"):
            stitched = pd.concat(oos_parts[coin]) if oos_parts[coin] else pd.Series(dtype=float)
            stitched = stitched[~stitched.index.duplicated(keep="last")].sort_index()
            result[coin] = _metrics_from_daily(stitched)
            result[coin + "_ret"] = stitched
        result["live_cfg"] = chosen[-1] if chosen else None
        for coin in ("BTC", "ETH"):
            result[coin].update(_trade_fields(oos_trades[coin], result[coin]["days"]))
        result["leverage"] = {}
        for mult in (2, 3):
            result["leverage"][str(mult)] = {}
            for coin in ("BTC", "ETH"):
                stitched_l = pd.concat(lev_parts[mult][coin]) if lev_parts[mult][coin] else pd.Series(dtype=float)
                stitched_l = stitched_l[~stitched_l.index.duplicated(keep="last")].sort_index()
                metrics = _metrics_from_daily(stitched_l)
                result["leverage"][str(mult)][coin] = {k: v for k, v in metrics.items() if k != "equity"}
    result["modal_stable"] = _stability(chosen)
    return result


def _trade_fields(trades: list[float], days: int) -> dict:
    if not trades:
        return {"win_rate": None, "expectancy": None, "trades": 0, "trades_per_year": 0.0}
    wins = [t for t in trades if t > 0]
    return {
        "win_rate": round(len(wins) / len(trades), 4),
        "expectancy": round(float(np.mean(trades)), 5),
        "trades": len(trades),
        "trades_per_year": round(len(trades) / max(1e-6, days / 365), 2),
    }


def _stability(chosen: list[Config]) -> float:
    if not chosen:
        return 0.0
    keys = [c.key for c in chosen]
    top = max(set(keys), key=keys.count)
    return keys.count(top) / len(keys)


def _daily_returns(index, net: np.ndarray) -> pd.Series:
    eq = pd.Series(np.cumprod(1.0 + net), index=index)
    daily = eq.resample("1D").last().dropna()
    return daily.pct_change().dropna()


def _metrics_from_daily(ret: pd.Series) -> dict:
    ret = ret.replace([np.inf, -np.inf], np.nan).dropna()
    if len(ret) < 5:
        return {"ann_return": 0.0, "max_drawdown": 0.0, "sharpe": 0.0, "calmar": 0.0, "days": int(len(ret)), "expectancy": None, "win_rate": None, "trades": 0, "trades_per_year": 0.0, "in_market": 0.0}
    eq = np.cumprod(1.0 + ret.to_numpy(float))
    peak = np.maximum.accumulate(eq)
    dd = float((eq / peak - 1).min())
    sharpe = float(ret.mean() / ret.std() * math.sqrt(365)) if float(ret.std()) > 1e-8 else 0.0
    span = max(1, (ret.index[-1] - ret.index[0]).days)
    ann = float(eq[-1] ** (365 / span) - 1) if eq[-1] > 0 else -1.0
    calmar = ann / abs(dd) if dd < -1e-6 else (99.0 if ann > 0 else 0.0)
    return {
        "ann_return": round(ann, 4),
        "max_drawdown": round(dd, 4),
        "sharpe": round(sharpe, 3),
        "calmar": round(float(calmar), 3) if math.isfinite(calmar) else None,
        "days": int(len(ret)),
        "equity": _curve(ret),
    }


def _curve(ret: pd.Series) -> list[dict]:
    if not len(ret):
        return []
    eq = (1.0 + ret.fillna(0)).cumprod()
    if len(eq) > 240:
        pos = [int(i * (len(eq) - 1) / 239) for i in range(240)]
        eq = eq.iloc[pos]
    rows = []
    seen = set()
    for ts, val in eq.items():
        stamp = int(pd.Timestamp(ts).timestamp())
        if stamp in seen or not math.isfinite(float(val)):
            continue
        seen.add(stamp)
        rows.append({"time": stamp, "value": round(float(val), 4)})
    return rows


def _rotation_daily(prepared, cfg: Config) -> pd.Series:
    btc = prepared["BTC"]["1d"][0]["close"]
    eth = prepared["ETH"]["1d"][0]["close"]
    both = pd.concat([btc.rename("BTC"), eth.rename("ETH")], axis=1).dropna()
    look = int(cfg.params["look"])
    strength = both["BTC"] / both["BTC"].shift(look) - both["ETH"] / both["ETH"].shift(look)
    ret_b = both["BTC"].pct_change()
    ret_e = both["ETH"].pct_change()
    sign = np.sign(strength.shift(1))
    if cfg.mode == "longflat":
        # 持有更强的一边。信号滞后一天。
        w_b = (sign > 0).astype(float)
        w_e = (sign < 0).astype(float)
    else:
        w_b = 0.5 * sign
        w_e = -0.5 * sign
    w_b = w_b.fillna(0)
    w_e = w_e.fillna(0)
    turnover = (w_b.diff().abs().fillna(w_b.abs()) + w_e.diff().abs().fillna(w_e.abs()))
    net = w_b * ret_b.fillna(0) + w_e * ret_e.fillna(0) - turnover * (FEE + SLIP)
    return net.dropna()


def buy_hold_daily(close: pd.Series) -> pd.Series:
    return close.resample("1D").last().dropna().pct_change().dropna()


def dca_stats(close: pd.Series) -> dict:
    weekly = close.resample("W-SUN").last().dropna()
    if len(weekly) < 4:
        return {}
    shares = 0.0
    spent = 0.0
    equity = []
    for ts, px in weekly.items():
        shares += 1.0 / px
        spent += 1.0
        equity.append((ts, shares * px / spent))
    values = np.array([v for _t, v in equity])
    peak = np.maximum.accumulate(values)
    dd = float((values / peak - 1).min())
    lump = weekly / float(weekly.iloc[0])
    lump_dd = float((lump / lump.cummax() - 1).min())
    return {
        "dca_multiple": round(float(values[-1]), 4),
        "dca_max_drawdown": round(dd, 4),
        "lump_multiple": round(float(lump.iloc[-1]), 4),
        "lump_max_drawdown": round(lump_dd, 4),
        "weeks": int(len(weekly)),
    }


def leverage_overlay(prepared, cfg: Config, oos_start) -> dict:
    """用已经选定的信号，在样本外把 1/2/3 倍再算一遍。不据此改排名。"""
    out = {}
    if cfg is None or cfg.family == "rotation":
        return out
    frame, fund, known = prepared["BTC"][cfg.tf]
    # 两个币一起算平均前，分别给数字。调用方按币取。
    return {}


def overlay_one(frame, fund, known, cfg: Config, leverage: float, oos_start):
    pos, net, held = evaluate_config(frame, fund, known, cfg, leverage)
    daily = _daily_returns(frame.index, net)
    part = daily[daily.index >= oos_start]
    return _metrics_from_daily(part)


def live_label(value: float) -> str:
    if value > 0.2:
        return "做多"
    if value < -0.2:
        return "做空"
    return "空仓"


def current_signal(prepared, cfg: Config) -> dict:
    if cfg is None:
        return {}
    if cfg.family == "rotation":
        btc = prepared["BTC"]["1d"][0]["close"]
        eth = prepared["ETH"]["1d"][0]["close"]
        both = pd.concat([btc.rename("BTC"), eth.rename("ETH")], axis=1).dropna()
        look = int(cfg.params["look"])
        if len(both) <= look:
            return {"summary": "样本不足"}
        strength = both["BTC"].iloc[-1] / both["BTC"].iloc[-1 - look] - both["ETH"].iloc[-1] / both["ETH"].iloc[-1 - look]
        if cfg.mode == "longflat":
            hold = "BTC" if strength > 0 else "ETH"
            return {"summary": f"持有 {hold}", "BTC": "做多" if hold == "BTC" else "空仓", "ETH": "做多" if hold == "ETH" else "空仓"}
        if strength > 0:
            return {"summary": "多 BTC、空 ETH", "BTC": "做多", "ETH": "做空"}
        return {"summary": "多 ETH、空 BTC", "BTC": "做空", "ETH": "做多"}
    out = {}
    for coin in ("BTC", "ETH"):
        frame, fund, known = prepared[coin][cfg.tf]
        pos = _signal(frame, cfg, fund, known)
        out[coin] = live_label(float(pos[-1]))
    return out


def public_metrics(row: dict) -> dict:
    keep = ("ann_return", "max_drawdown", "sharpe", "calmar", "win_rate", "expectancy", "trades", "trades_per_year", "in_market", "days")
    return {k: row.get(k) for k in keep if k in row}


def run_search(market, funding) -> dict:
    prepared, start, end = prepare_frames(market, funding)
    configs = build_configs()
    families = []
    seen = []
    for cfg in configs:
        if cfg.family not in seen:
            seen.append(cfg.family)
    master = prepared["BTC"]["1d"][0].index
    print(f"configs {len(configs)} families {seen} window {start.date()} {end.date()}", flush=True)
    reports = []
    for family in seen:
        print(" family", family, flush=True)
        reports.append(walk_family(family, configs, prepared, master))
    basket = 0.5 * buy_hold_daily(prepared["BTC"]["1d"][0]["close"]) + 0.5 * buy_hold_daily(prepared["ETH"]["1d"][0]["close"])
    oos_start = master[int(len(master) * 0.40)]
    bh = {}
    for coin in ("BTC", "ETH"):
        ret = buy_hold_daily(prepared[coin]["1d"][0]["close"])
        bh[coin] = _metrics_from_daily(ret[ret.index >= oos_start])
        bh[coin]["equity"] = _curve(ret[ret.index >= oos_start])
    bh["basket"] = _metrics_from_daily(basket[basket.index >= oos_start])
    dca = {}
    for coin in ("BTC", "ETH"):
        closes = prepared[coin]["1d"][0]["close"]
        dca[coin] = dca_stats(closes[closes.index >= oos_start])

    ranked = []
    for rep in reports:
        stable = rep["modal_stable"]
        neigh = rep["neighbor_sharpe"]
        if rep["family"] == "rotation":
            book = rep["book"]
            score = book["sharpe"]
            if neigh is not None:
                score = min(score, neigh)
            score *= 0.5 + 0.5 * stable
            if book["days"] < 100:
                score = -999.0
            exp_ok = book["ann_return"] > 0 and book["sharpe"] > 0 and (neigh is None or neigh > 0)
            ranked.append({
                "family": rep["family"],
                "name": rep["name"],
                "score": score,
                "positive": bool(exp_ok),
                "kind": "rotation",
                "report": rep,
            })
        else:
            a, b = rep["BTC"], rep["ETH"]
            score = min(a["sharpe"], b["sharpe"])
            if neigh is not None:
                score = min(score, neigh)
            score *= 0.5 + 0.5 * stable
            if a.get("trades", 0) < 8 or b.get("trades", 0) < 8 or a["days"] < 100 or b["days"] < 100:
                score = -999.0
            exp_ok = (
                (a.get("expectancy") or 0) > 0 and (b.get("expectancy") or 0) > 0
                and a["sharpe"] > 0 and b["sharpe"] > 0
                and a["ann_return"] > 0 and b["ann_return"] > 0
            )
            ranked.append({
                "family": rep["family"],
                "name": rep["name"],
                "score": score,
                "positive": bool(exp_ok),
                "kind": "single",
                "report": rep,
            })
    ranked.sort(key=lambda item: item["score"], reverse=True)
    top = ranked[:3]
    bh_sharpe_floor = min(bh["BTC"]["sharpe"], bh["ETH"]["sharpe"])
    any_beats = False
    cards = []
    for item in top:
        rep = item["report"]
        cfg = rep.get("live_cfg")
        live = current_signal(prepared, cfg)
        card = {
            "name": rep["name"],
            "family": rep["family"],
            "score": round(float(item["score"]), 3),
            "stable": rep["modal_stable"],
            "neighbor_sharpe": rep["neighbor_sharpe"],
            "plain": cfg.plain if cfg else "",
            "choice": rep["chosen"][-1] if rep["chosen"] else None,
            "folds": [{"tf": c["tf"], "params": c["params"]} for c in rep["chosen"]],
            "live": live,
        }
        if item["kind"] == "rotation":
            card["book"] = {k: v for k, v in rep["book"].items() if k != "equity"}
            card["equity"] = {"book": rep["book"].get("equity"), "basket": bh["basket"].get("equity")}
            card["deflated_sharpe"] = deflated_sharpe(rep["book_ret"].to_numpy(float), len(configs))
            beats = (
                rep["book"]["sharpe"] > bh["basket"]["sharpe"]
                and rep["book"]["ann_return"] > bh["basket"]["ann_return"]
                and rep["book"]["ann_return"] > 0
                and (rep["neighbor_sharpe"] is None or rep["neighbor_sharpe"] > 0)
                and (card["deflated_sharpe"] or 0) >= 0.95
            )
        else:
            card["BTC"] = {k: v for k, v in rep["BTC"].items() if k != "equity"}
            card["ETH"] = {k: v for k, v in rep["ETH"].items() if k != "equity"}
            card["equity"] = {
                "BTC": rep["BTC"].get("equity"),
                "ETH": rep["ETH"].get("equity"),
                "bh_BTC": bh["BTC"].get("equity"),
                "bh_ETH": bh["ETH"].get("equity"),
            }
            lev = {"1": {"BTC": card["BTC"], "ETH": card["ETH"]}}
            lev.update(rep.get("leverage") or {})
            card["leverage"] = lev
            worse = "BTC" if rep["BTC"]["sharpe"] <= rep["ETH"]["sharpe"] else "ETH"
            card["deflated_sharpe"] = deflated_sharpe(rep[worse + "_ret"].to_numpy(float), len(configs))
            beats = (
                min(rep["BTC"]["sharpe"], rep["ETH"]["sharpe"]) > bh_sharpe_floor
                and rep["BTC"]["ann_return"] > bh["BTC"]["ann_return"]
                and rep["ETH"]["ann_return"] > bh["ETH"]["ann_return"]
                and (rep["BTC"].get("expectancy") or 0) > 0
                and (rep["ETH"].get("expectancy") or 0) > 0
                and (rep["neighbor_sharpe"] is None or rep["neighbor_sharpe"] > 0)
                and (card["deflated_sharpe"] or 0) >= 0.95
            )
        card["beats_buy_hold"] = bool(beats)
        any_beats = any_beats or bool(beats)
        cards.append(card)

    ranking = []
    for item in ranked:
        rep = item["report"]
        row = {
            "name": rep["name"],
            "family": rep["family"],
            "score": round(float(item["score"]), 3),
            "positive": bool(item.get("positive") or item.get("beats")),
            "neighbor_sharpe": rep["neighbor_sharpe"],
            "stable": rep["modal_stable"],
            "choice": rep["chosen"][-1]["plain"] if rep["chosen"] else "",
            "tf": rep["chosen"][-1]["tf"] if rep["chosen"] else "",
        }
        if item["kind"] == "rotation":
            row["book"] = {k: rep["book"][k] for k in ("ann_return", "max_drawdown", "sharpe", "calmar", "days")}
        else:
            row["BTC"] = {k: rep["BTC"][k] for k in ("ann_return", "max_drawdown", "sharpe", "calmar", "win_rate", "expectancy", "trades_per_year", "days")}
            row["ETH"] = {k: rep["ETH"][k] for k in ("ann_return", "max_drawdown", "sharpe", "calmar", "win_rate", "expectancy", "trades_per_year", "days")}
        ranking.append(row)

    return {
        "window": {"start": str(start.date()), "end": str(end.date()), "oos_start": str(pd.Timestamp(oos_start).date())},
        "trials": len(configs),
        "beats_buy_hold": bool(any_beats),
        "bh_sharpe_floor": bh_sharpe_floor,
        "buy_hold": {k: {kk: vv for kk, vv in metrics.items() if kk != "equity"} for k, metrics in bh.items()},
        "dca": dca,
        "top": cards,
        "ranking": ranking,
        "note": (
            f"一共试了 {len(configs)} 套事先列好的参数。每一族只在当时已有的行情里按夏普挑参数，"
            "挑完才看下一段。排序用的是两个币里较差的那个样本外夏普，并和相邻参数、各段是否选到同一套参数一起看。"
            "紧缩夏普把「试了很多套」这件事算进去：它低，就说明这个优势和多试几次碰出来的结果不好区分。"
            "1 倍、2 倍、3 倍只是把同一条样本外信号放大后的结果，不作为选择，也不是杠杆建议。"
            "15 分钟因为手续费占比太高，没有进这张表。"
        ),
    }
