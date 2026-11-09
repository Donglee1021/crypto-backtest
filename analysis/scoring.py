"""与 js/indicators.js 的 scoreTf 同一套单周期打分。

回测用这份整数规则，不用 TA-Lib 的默认口径，方便和页面对照。
TA-Lib 只在快照里另留一份 RSI / MACD / 布林 / ATR / STOCH 读数。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def ema(xs: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(xs), np.nan)
    if len(xs) < n:
        return out
    e = float(np.sum(xs[:n]) / n)
    out[n - 1] = e
    k = 2 / (n + 1)
    for i in range(n, len(xs)):
        e = xs[i] * k + e * (1 - k)
        out[i] = e
    return out


def sma(xs: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(xs), np.nan)
    if len(xs) < n:
        return out
    s = float(np.sum(xs[:n]))
    out[n - 1] = s / n
    for i in range(n, len(xs)):
        s += xs[i] - xs[i - n]
        out[i] = s / n
    return out


def rsi(xs: np.ndarray, n: int = 14) -> np.ndarray:
    out = np.full(len(xs), np.nan)
    if len(xs) <= n:
        return out
    gains = np.zeros(len(xs))
    losses = np.zeros(len(xs))
    for i in range(1, len(xs)):
        d = xs[i] - xs[i - 1]
        gains[i] = max(d, 0.0)
        losses[i] = max(-d, 0.0)
    ag = float(np.sum(gains[1:n + 1]) / n)
    al = float(np.sum(losses[1:n + 1]) / n)
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(n + 1, len(xs)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def macd(xs: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    e_fast, e_slow = ema(xs, fast), ema(xs, slow)
    line = np.where(np.isnan(e_fast) | np.isnan(e_slow), np.nan, e_fast - e_slow)
    valid = line[~np.isnan(line)]
    sig_v = ema(valid, signal)
    sig = np.full(len(xs), np.nan)
    if len(valid):
        sig[len(xs) - len(valid):] = sig_v
    hist = np.where(np.isnan(line) | np.isnan(sig), np.nan, line - sig)
    return line, sig, hist


def boll(xs: np.ndarray, n: int = 20, k: float = 2.0):
    mid = sma(xs, n)
    up = np.full(len(xs), np.nan)
    lo = np.full(len(xs), np.nan)
    for i in range(n - 1, len(xs)):
        w = xs[i - n + 1:i + 1]
        sd = math.sqrt(float(np.sum((w - mid[i]) ** 2) / n))
        up[i] = mid[i] + k * sd
        lo[i] = mid[i] - k * sd
    return mid, up, lo


def kdj(h, l, c, n: int = 9):
    ks = np.full(len(c), np.nan)
    ds = np.full(len(c), np.nan)
    js = np.full(len(c), np.nan)
    k_val = 50.0
    d_val = 50.0
    for i in range(n - 1, len(c)):
        hh = float(np.max(h[i - n + 1:i + 1]))
        ll = float(np.min(l[i - n + 1:i + 1]))
        rsv = 50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100.0
        k_val = 2.0 / 3.0 * k_val + 1.0 / 3.0 * rsv
        d_val = 2.0 / 3.0 * d_val + 1.0 / 3.0 * k_val
        ks[i] = k_val
        ds[i] = d_val
        js[i] = 3.0 * k_val - 2.0 * d_val
    return ks, ds, js


def atr(h, l, c, n: int = 14) -> np.ndarray:
    out = np.full(len(c), np.nan)
    if len(c) < n:
        return out
    tr = np.zeros(len(c))
    tr[0] = h[0] - l[0]
    for i in range(1, len(c)):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    a = float(np.sum(tr[:n]) / n)
    out[n - 1] = a
    for i in range(n, len(c)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def _nan(*vals) -> bool:
    for x in vals:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return True
    return False


def score_at(i, c, o, e20, e50, e200, ml, ms, mh, rs, ks, ds, js, bu, bl, v, vavg):
    if _nan(e20[i], e50[i], e200[i], ml[i], ms[i], mh[i], rs[i], ks[i], ds[i], js[i], bu[i], bl[i], vavg[i]):
        return None
    if i == 0 or _nan(mh[i - 1]) or vavg[i] == 0:
        return None
    p = c[i]
    sc = 0
    if e20[i] > e50[i] > e200[i]:
        sc += 2
    elif e20[i] < e50[i] < e200[i]:
        sc -= 2
    sc += 1 if p > e200[i] else -1
    sc += 1 if p > e20[i] else -1
    sc += (1 if ml[i] > ms[i] else -1) + (1 if mh[i] > mh[i - 1] else -1)
    r = rs[i]
    if r >= 70:
        sc -= 1
    elif r <= 30:
        sc += 1
    elif r > 55:
        sc += 1
    elif r < 45:
        sc -= 1
    j = js[i]
    if j > 100:
        sc -= 1
    elif j < 0:
        sc += 1
    else:
        sc += 1 if ks[i] > ds[i] else -1
    pb = 0.5 if bu[i] == bl[i] else (p - bl[i]) / (bu[i] - bl[i])
    if pb > 1:
        sc -= 1
    elif pb < 0:
        sc += 1
    elif pb > 0.5:
        sc += 1
    else:
        sc -= 1
    if v[i] / vavg[i] > 1.3:
        sc += 1 if c[i] > o[i] else -1
    return int(sc)


def score_frame(df: pd.DataFrame) -> pd.DataFrame:
    """按每根已给出的 K 线打分。列：open high low close volume。"""
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    e20, e50, e200 = ema(c, 20), ema(c, 50), ema(c, 200)
    ml, ms, mh = macd(c)
    rs = rsi(c)
    _mid, bu, bl = boll(c)
    ks, ds, js = kdj(h, l, c)
    vavg = sma(v, 20)
    atr_v = atr(h, l, c)
    scores = [
        score_at(i, c, o, e20, e50, e200, ml, ms, mh, rs, ks, ds, js, bu, bl, v, vavg)
        for i in range(len(c))
    ]
    return pd.DataFrame({
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v,
        "score": scores,
        "atr": atr_v,
        "rsi": rs,
        "ema20": e20,
        "ema200": e200,
    }, index=df.index)


def label_tf(score: float) -> str:
    if score >= 6:
        return "强烈做多"
    if score >= 3:
        return "做多"
    if score <= -6:
        return "强烈做空"
    if score <= -3:
        return "做空"
    return "观望"


def fng_adjust(value: float) -> int:
    if value <= 20:
        return 1
    if value >= 80:
        return -1
    return 0


def funding_adjust(rate: float) -> int:
    if rate is None or (isinstance(rate, float) and math.isnan(rate)):
        return 0
    if rate > 0.0003:
        return -1
    if rate < 0:
        return 1
    return 0


def talib_last(df: pd.DataFrame) -> dict:
    import talib
    c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    rsi_v = talib.RSI(c, timeperiod=14)
    macd_v, sig, hist = talib.MACD(c, fastperiod=12, slowperiod=26, signalperiod=9)
    ema20 = talib.EMA(c, timeperiod=20)
    upper, mid, lower = talib.BBANDS(c, timeperiod=20, nbdevup=2, nbdevdn=2)
    atr_v = talib.ATR(h, l, c, timeperiod=14)
    k, d = talib.STOCH(h, l, c, fastk_period=9, slowk_period=3, slowd_period=3)

    def last(arr):
        valid = arr[~np.isnan(arr)]
        return None if len(valid) == 0 else round(float(valid[-1]), 4)

    return {
        "rsi": last(rsi_v),
        "macd": last(macd_v),
        "macd_signal": last(sig),
        "macd_hist": last(hist),
        "ema20": last(ema20),
        "bb_upper": last(upper),
        "bb_mid": last(mid),
        "bb_lower": last(lower),
        "atr": last(atr_v),
        "stoch_k": last(k),
        "stoch_d": last(d),
    }
