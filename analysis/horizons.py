"""页面上的超短线 / 中线 / 长线。

公式来自 js/indicators.js 的 horizonScalp、overallCall、horizonLong 和 finishFactors。
没有可靠长历史的因子直接不传：新闻、清算、盘口、1 分钟动量、5 分钟技术、
美股影响、入场清单、见底见顶、多空人数比。剩下的因子仍按原权重，
再由 finishFactors 按实际出现的权重重新归一，这和页面上「缺数据就跳过」一致。

支撑阻力只用摆动高低点（左右各 5 根、回看 120 根）。页面上还有枢轴、
成交密集和整数关口，回测没有逐根重算。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analysis.scoring import label_tf, score_frame

DROPPED = ["新闻", "清算", "盘口", "1分钟动量", "5分钟技术", "美股影响", "入场清单", "见底见顶", "多空人数比"]


def _clip(unit: float) -> float:
    return max(-1.0, min(1.0, float(unit)))


def finish_factors(factors: list[tuple[str, float, float]]) -> dict:
    """与 indicators.js finishFactors 的舍入一致。factors: (name, weight, unit)。"""
    if not factors:
        return {"score": None, "call": "观望", "strength": "观望", "conflict": False}
    wsum = sum(w for _n, w, _u in factors)
    score = 0.0
    signed = []
    for _name, weight, unit in factors:
        unit = _clip(unit)
        score += (weight / wsum) * unit * 10.0
        if abs(unit) >= 0.2:
            signed.append(unit)
    # 与 JavaScript Math.round 一致：.5 朝正无穷。
    score = math.floor(score * 10.0 + 0.5) / 10.0
    strong_long = any(u >= 0.45 for _n, _w, u in ((n, w, _clip(u)) for n, w, u in factors))
    strong_short = any(u <= -0.45 for _n, _w, u in ((n, w, _clip(u)) for n, w, u in factors))
    conflict = strong_long and strong_short
    call = "观望"
    strength = "观望"
    if not conflict:
        if score >= 6:
            call, strength = "做多", "强烈做多"
        elif score >= 2.5:
            call, strength = "做多", "偏多"
        elif score <= -6:
            call, strength = "做空", "强烈做空"
        elif score <= -2.5:
            call, strength = "做空", "偏空"
    return {"score": score, "call": call, "strength": strength, "conflict": conflict}


def funding_unit_scalp(rate: float) -> float:
    pct = rate * 100.0
    if pct > 0.05:
        return -0.8
    if pct < -0.03:
        return 0.8
    if pct > 0:
        return -0.2
    if pct < 0:
        return 0.2
    return 0.0


def funding_unit_mid(rate: float) -> float:
    pct = rate * 100.0
    if pct > 0.05:
        return -1.0
    if pct > 0.03:
        return -0.55
    if pct < -0.03:
        return 1.0
    if pct < 0:
        return 0.35
    if pct > 0:
        return -0.25
    return 0.0


def fng_unit_mid(value: float) -> float:
    if value <= 20:
        return 1.0
    if value <= 30:
        return 0.45
    if value >= 80:
        return -1.0
    if value >= 70:
        return -0.45
    return 0.0


def fng_unit_long(value: float) -> float:
    if value <= 20:
        return 1.0
    if value >= 80:
        return -1.0
    return 0.0


def weekly_rsi_unit(r: float) -> float:
    if r >= 70:
        return 0.55
    if r >= 55:
        return 0.35
    if r <= 30:
        return -0.55
    if r <= 45:
        return -0.35
    return 0.0


def sr_unit_from_flags(flags: dict, mode: str) -> float | None:
    """mode=mid 用 overallCall 的档位，scalp/long 用 srUnitFrom 的档位。"""
    if flags is None:
        return None
    broke_down = bool(flags.get("broke_down"))
    broke_up = bool(flags.get("broke_up"))
    near_s = bool(flags.get("near_support"))
    near_r = bool(flags.get("near_resistance"))
    sd = flags.get("support_dist")
    rd = flags.get("resist_dist")
    if mode == "mid":
        if broke_down and not broke_up:
            return -0.85
        if broke_up and not broke_down:
            return 0.85
        if near_s and near_r:
            return 0.0
        if near_s:
            return 0.75
        if near_r:
            return -0.75
        if sd is None or rd is None or (isinstance(sd, float) and math.isnan(sd)) or (isinstance(rd, float) and math.isnan(rd)):
            return None
        ds, dr = abs(float(sd)), abs(float(rd))
        if ds < dr:
            return min(0.35, (dr - ds) / 8.0)
        return -min(0.35, (ds - dr) / 8.0)
    if broke_down and not broke_up:
        return -0.85
    if broke_up and not broke_down:
        return 0.85
    if near_s and not near_r:
        return 0.7
    if near_r and not near_s:
        return -0.7
    if sd is None or rd is None or (isinstance(sd, float) and math.isnan(sd)) or (isinstance(rd, float) and math.isnan(rd)):
        return None
    return 0.25 if abs(float(sd)) < abs(float(rd)) else -0.25


def swing_sr(high: np.ndarray, low: np.ndarray, close: np.ndarray, look: int = 5, window: int = 120) -> list[dict | None]:
    n = len(close)
    is_ph = np.zeros(n, dtype=bool)
    is_pl = np.zeros(n, dtype=bool)
    for i in range(look, n - look):
        seg_h = high[i - look:i + look + 1]
        seg_l = low[i - look:i + look + 1]
        if high[i] >= seg_h.max():
            is_ph[i] = True
        if low[i] <= seg_l.min():
            is_pl[i] = True
    out: list[dict | None] = [None] * n
    for i in range(max(window, look + 2), n):
        start = i - window + 1
        end = i - look
        price = float(close[i])
        prev = float(close[i - 1])
        if not price:
            continue
        levels = []
        for j in range(start, end + 1):
            if is_ph[j]:
                levels.append(float(high[j]))
            if is_pl[j]:
                levels.append(float(low[j]))
        if not levels:
            levels = [float(np.max(high[start:i + 1])), float(np.min(low[start:i + 1]))]
        band = price * 0.0005
        supports = [p for p in levels if p < price - band]
        resistances = [p for p in levels if p > price + band]
        broke_down = False
        broke_up = False
        for p in resistances:
            if p and abs(price - p) / p <= 0.015 and prev >= p > price:
                broke_down = True
                break
        for p in supports:
            if p and abs(price - p) / p <= 0.015 and prev <= p < price:
                broke_up = True
                break
        sup = max(supports) if supports else None
        res = min(resistances) if resistances else None
        sd = None if sup is None else (sup - price) / price * 100.0
        rd = None if res is None else (res - price) / price * 100.0
        out[i] = {
            "broke_down": broke_down,
            "broke_up": broke_up,
            "near_support": sd is not None and abs(sd) <= 0.5,
            "near_resistance": rd is not None and abs(rd) <= 0.5,
            "support_dist": sd,
            "resist_dist": rd,
        }
    return out


def _ffill_closed(series: pd.Series, shift: pd.Timedelta, index: pd.DatetimeIndex) -> pd.Series:
    src = series.dropna().copy()
    src.index = src.index + shift
    return src.reindex(index, method="ffill")


def _known_rate(funding: pd.Series | None, index: pd.DatetimeIndex) -> pd.Series:
    if funding is None or not len(funding):
        return pd.Series(np.nan, index=index)
    return funding.reindex(index, method="ffill")


def _known_fng(fng: pd.DataFrame | None, index: pd.DatetimeIndex) -> pd.Series:
    if fng is None or not len(fng):
        return pd.Series(np.nan, index=index)
    src = fng["value"].copy()
    # 当天的指数按次日才当已知，避免用到当天收盘才定稿的读数。
    src.index = src.index + pd.Timedelta(days=1)
    return src.reindex(index, method="ffill")


def _attach_ohlc(base: pd.DataFrame, raw_score: pd.Series, call: pd.Series, strength: pd.Series) -> pd.DataFrame:
    """信号用上一根已收盘的分数，成交价用当前这根的开盘（回测引擎取 open）。"""
    out = pd.DataFrame(index=base.index)
    out["open"] = base["open"]
    out["high"] = base["high"]
    out["low"] = base["low"]
    out["close"] = base["close"]
    out["raw_score"] = raw_score.shift(1)
    out["call"] = call.shift(1)
    out["strength"] = strength.shift(1)
    out["atr"] = base["atr"].shift(1) if "atr" in base.columns else np.nan
    tradable = []
    for raw, side in zip(out["raw_score"], out["call"]):
        if raw is None or (isinstance(raw, float) and math.isnan(raw)) or side not in ("做多", "做空"):
            tradable.append(np.nan if raw is None or (isinstance(raw, float) and math.isnan(raw)) else 0.0)
        else:
            tradable.append(float(raw))
    out["score"] = tradable
    return out.dropna(subset=["raw_score"])


def _weekly_frame(daily: pd.DataFrame) -> pd.DataFrame:
    idx = daily.index
    monday = (idx - pd.to_timedelta(idx.dayofweek, unit="D")).normalize()
    g = daily.copy()
    g["week"] = monday
    agg = g.groupby("week").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    })
    agg.index = pd.DatetimeIndex(agg.index, tz="UTC")
    # 不满一周的最后一组如果本周还没过完，丢掉。
    if len(agg):
        week_end = agg.index[-1] + pd.Timedelta(days=7)
        if week_end > pd.Timestamp.now(tz="UTC"):
            agg = agg.iloc[:-1]
    return score_frame(agg)


def prepare_frames(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out = {}
    for key, df in frames.items():
        scored = score_frame(df)
        out[key] = scored
    return out


def build_scalp(m15: pd.DataFrame, funding: pd.Series | None) -> pd.DataFrame:
    flags = swing_sr(m15["high"].to_numpy(float), m15["low"].to_numpy(float), m15["close"].to_numpy(float))
    rate = _known_rate(funding, m15.index)
    raw, calls, strengths = [], [], []
    for i, (ts, row) in enumerate(m15.iterrows()):
        factors = []
        sc = row["score"]
        if sc is not None and not (isinstance(sc, float) and math.isnan(sc)):
            factors.append(("15分钟技术", 0.14, float(sc) / 10.0))
        fr = rate.loc[ts]
        if fr is not None and not (isinstance(fr, float) and math.isnan(fr)):
            factors.append(("当前资金费率", 0.08, funding_unit_scalp(float(fr))))
        if flags[i] is not None:
            unit = sr_unit_from_flags(flags[i], "scalp")
            if unit is not None:
                factors.append(("短线位置", 0.12, unit))
        done = finish_factors(factors)
        raw.append(np.nan if done["score"] is None else done["score"])
        calls.append(done["call"])
        strengths.append(done["strength"])
    base = m15.copy()
    base["score"] = raw
    return _attach_ohlc(base, pd.Series(raw, index=m15.index), pd.Series(calls, index=m15.index), pd.Series(strengths, index=m15.index))


def build_mid(h1: pd.DataFrame, h4: pd.DataFrame, d1: pd.DataFrame, funding: pd.Series | None, fng: pd.DataFrame | None) -> pd.DataFrame:
    index = h1.index
    s1 = h1["score"].shift(1)
    s4 = _ffill_closed(h4["score"], pd.Timedelta(hours=4), index)
    sd = _ffill_closed(d1["score"], pd.Timedelta(days=1), index)
    rate = _known_rate(funding, index)
    mood = _known_fng(fng, index)
    flags = swing_sr(h4["high"].to_numpy(float), h4["low"].to_numpy(float), h4["close"].to_numpy(float))
    flag_series = pd.Series(flags, index=h4.index)
    flag_closed = flag_series.copy()
    flag_closed.index = h4.index + pd.Timedelta(hours=4)
    flag_on = []
    last = None
    closed_times = list(flag_closed.index)
    closed_vals = list(flag_closed.values)
    j = 0
    for ts in index:
        while j < len(closed_times) and closed_times[j] <= ts:
            last = closed_vals[j]
            j += 1
        flag_on.append(last)
    raw, calls, strengths = [], [], []
    for i, ts in enumerate(index):
        factors = []
        a, b, c = s1.loc[ts], s4.loc[ts], sd.loc[ts]
        if all(pd.notna(x) for x in (a, b, c)):
            tech = float(a) * 0.2 + float(b) * 0.3 + float(c) * 0.5
            factors.append(("多周期技术", 0.22, tech / 10.0))
        if flag_on[i] is not None:
            unit = sr_unit_from_flags(flag_on[i], "mid")
            if unit is not None:
                factors.append(("支撑阻力", 0.14, unit))
        fr = rate.loc[ts]
        if pd.notna(fr):
            factors.append(("资金费率", 0.08, funding_unit_mid(float(fr))))
        fv = mood.loc[ts]
        if pd.notna(fv):
            factors.append(("恐惧贪婪", 0.05, fng_unit_mid(float(fv))))
        done = finish_factors(factors)
        raw.append(done["score"] if done["score"] is not None else np.nan)
        calls.append(done["call"])
        strengths.append(done["strength"])
    base = h1.copy()
    # 中线分数在当前 1 小时开盘时已经只用已收盘的高周期，不再额外后移一根。
    out = pd.DataFrame(index=index)
    out["open"] = base["open"]
    out["high"] = base["high"]
    out["low"] = base["low"]
    out["close"] = base["close"]
    out["atr"] = base["atr"].shift(1)
    out["raw_score"] = raw
    out["call"] = calls
    out["strength"] = strengths
    tradable = []
    for value, side in zip(raw, calls):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            tradable.append(np.nan)
        elif side not in ("做多", "做空"):
            tradable.append(0.0)
        else:
            tradable.append(float(value))
    out["score"] = tradable
    return out.dropna(subset=["raw_score"])


def build_long(d1: pd.DataFrame, weekly: pd.DataFrame, fng: pd.DataFrame | None) -> pd.DataFrame:
    index = d1.index
    sd = d1["score"].shift(1)
    ws = _ffill_closed(weekly["score"], pd.Timedelta(days=7), index)
    wr = _ffill_closed(weekly["rsi"], pd.Timedelta(days=7), index)
    we = _ffill_closed(weekly["ema200"], pd.Timedelta(days=7), index)
    wc = _ffill_closed(weekly["close"], pd.Timedelta(days=7), index)
    mood = _known_fng(fng, index)
    flags = swing_sr(d1["high"].to_numpy(float), d1["low"].to_numpy(float), d1["close"].to_numpy(float))
    raw, calls, strengths = [], [], []
    for i, ts in enumerate(index):
        factors = []
        if pd.notna(ws.loc[ts]):
            factors.append(("周线趋势", 0.26, float(ws.loc[ts]) / 10.0))
        if pd.notna(sd.loc[ts]):
            factors.append(("日线趋势", 0.18, float(sd.loc[ts]) / 10.0))
        if pd.notna(wr.loc[ts]):
            factors.append(("周线RSI", 0.10, weekly_rsi_unit(float(wr.loc[ts]))))
        if pd.notna(we.loc[ts]) and pd.notna(wc.loc[ts]):
            factors.append(("周线EMA200", 0.12, 0.7 if float(wc.loc[ts]) > float(we.loc[ts]) else -0.7))
        # 日线摆动位置用前一根，避免用到当天收盘。
        prev_flag = flags[i - 1] if i else None
        if prev_flag is not None:
            unit = sr_unit_from_flags(prev_flag, "long")
            if unit is not None:
                factors.append(("大级别位置", 0.12, unit))
        fv = mood.loc[ts]
        if pd.notna(fv):
            factors.append(("恐惧贪婪", 0.05, fng_unit_long(float(fv))))
        done = finish_factors(factors)
        raw.append(done["score"] if done["score"] is not None else np.nan)
        calls.append(done["call"])
        strengths.append(done["strength"])
    out = pd.DataFrame(index=index)
    out["open"] = d1["open"]
    out["high"] = d1["high"]
    out["low"] = d1["low"]
    out["close"] = d1["close"]
    out["atr"] = d1["atr"].shift(1)
    out["raw_score"] = raw
    out["call"] = calls
    out["strength"] = strengths
    tradable = []
    for value, side in zip(raw, calls):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            tradable.append(np.nan)
        elif side not in ("做多", "做空"):
            tradable.append(0.0)
        else:
            tradable.append(float(value))
    out["score"] = tradable
    return out.dropna(subset=["raw_score"])


def rule_frame(scored: pd.DataFrame) -> pd.DataFrame:
    """单周期规则：上一根收盘的整数分数，阈值在回测里套用。"""
    out = scored.copy()
    out["raw_score"] = out["score"].shift(1)
    out["atr"] = out["atr"].shift(1)
    out["score"] = out["raw_score"]
    out["call"] = out["raw_score"].map(lambda x: None if pd.isna(x) else ("做多" if x >= 3 else "做空" if x <= -3 else "观望"))
    out["strength"] = out["raw_score"].map(lambda x: None if pd.isna(x) else label_tf(float(x)))
    return out.dropna(subset=["score"])


def _last_number(series: pd.Series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if not len(s):
        return None
    return float(s.iloc[-1])


def current_signals(scored: dict[str, pd.DataFrame], weekly: pd.DataFrame, funding: pd.Series | None, fng_value: float | None) -> dict:
    """用最后一根已收盘 K 线做当前结论，口径和页面缺数据时一样。"""
    price = float(scored["1h"]["close"].iloc[-1])
    fund = None
    if funding is not None and len(funding):
        fund = float(funding.iloc[-1])
    out = {}
    for key, name in (("15m", "15分钟"), ("1h", "1小时"), ("4h", "4小时"), ("1d", "日线")):
        val = _last_number(scored[key]["score"])
        if val is None:
            continue
        out[name] = {"score": val, "verdict": label_tf(val), "price": price}
    m15 = scored["15m"]
    flags15 = swing_sr(m15["high"].to_numpy(float), m15["low"].to_numpy(float), m15["close"].to_numpy(float))
    factors = []
    if "15分钟" in out:
        factors.append(("15分钟技术", 0.14, out["15分钟"]["score"] / 10.0))
    if fund is not None:
        factors.append(("当前资金费率", 0.08, funding_unit_scalp(fund)))
    if flags15 and flags15[-1] is not None:
        unit = sr_unit_from_flags(flags15[-1], "scalp")
        if unit is not None:
            factors.append(("短线位置", 0.12, unit))
    done = finish_factors(factors)
    if done["score"] is not None:
        out["超短线"] = {"score": done["score"], "verdict": done["strength"], "price": price}

    h4 = scored["4h"]
    flags4 = swing_sr(h4["high"].to_numpy(float), h4["low"].to_numpy(float), h4["close"].to_numpy(float))
    factors = []
    if all(k in out for k in ("1小时", "4小时", "日线")):
        tech = out["1小时"]["score"] * 0.2 + out["4小时"]["score"] * 0.3 + out["日线"]["score"] * 0.5
        factors.append(("多周期技术", 0.22, tech / 10.0))
    if flags4 and flags4[-1] is not None:
        unit = sr_unit_from_flags(flags4[-1], "mid")
        if unit is not None:
            factors.append(("支撑阻力", 0.14, unit))
    if fund is not None:
        factors.append(("资金费率", 0.08, funding_unit_mid(fund)))
    if fng_value is not None:
        factors.append(("恐惧贪婪", 0.05, fng_unit_mid(fng_value)))
    done = finish_factors(factors)
    if done["score"] is not None:
        out["中线"] = {"score": done["score"], "verdict": done["strength"], "price": price}

    d1 = scored["1d"]
    flags_d = swing_sr(d1["high"].to_numpy(float), d1["low"].to_numpy(float), d1["close"].to_numpy(float))
    factors = []
    wscore = _last_number(weekly["score"]) if weekly is not None and len(weekly) else None
    if wscore is not None:
        factors.append(("周线趋势", 0.26, wscore / 10.0))
    if "日线" in out:
        factors.append(("日线趋势", 0.18, out["日线"]["score"] / 10.0))
    wr = _last_number(weekly["rsi"]) if weekly is not None and len(weekly) else None
    if wr is not None:
        factors.append(("周线RSI", 0.10, weekly_rsi_unit(wr)))
    we = _last_number(weekly["ema200"]) if weekly is not None and len(weekly) else None
    wc = _last_number(weekly["close"]) if weekly is not None and len(weekly) else None
    if we is not None and wc is not None:
        factors.append(("周线EMA200", 0.12, 0.7 if wc > we else -0.7))
    if flags_d and flags_d[-1] is not None:
        unit = sr_unit_from_flags(flags_d[-1], "long")
        if unit is not None:
            factors.append(("大级别位置", 0.12, unit))
    if fng_value is not None:
        factors.append(("恐惧贪婪", 0.05, fng_unit_long(fng_value)))
    done = finish_factors(factors)
    if done["score"] is not None:
        out["长线"] = {"score": done["score"], "verdict": done["strength"], "price": price}
    return out


def latest_view(frame: pd.DataFrame) -> dict | None:
    if frame is None or not len(frame):
        return None
    row = frame.iloc[-1]
    raw = row["raw_score"] if "raw_score" in frame.columns else row["score"]
    if pd.isna(raw):
        return None
    strength = row["strength"] if "strength" in frame.columns else None
    if not isinstance(strength, str) or strength == "nan":
        from analysis.scoring import label_tf
        strength = label_tf(float(raw))
    return {
        "score": float(raw),
        "verdict": str(strength),
        "price": float(row["close"]),
        "time": frame.index[-1].isoformat(),
    }
