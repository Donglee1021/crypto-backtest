"""几条常见规则，用来和页面打分对照。信号在收盘确定，下一根开盘成交。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from analysis.scoring import boll, ema, macd, rsi


def _shift(pos: np.ndarray, index) -> pd.Series:
    return pd.Series(pos, index=index).shift(1).fillna(0.0)


def ema_cross(close: pd.Series, fast: int, slow: int) -> pd.Series:
    values = close.to_numpy(float)
    fast_v = ema(values, fast)
    slow_v = ema(values, slow)
    pos = np.zeros(len(values))
    for i in range(len(values)):
        if math.isnan(fast_v[i]) or math.isnan(slow_v[i]):
            continue
        pos[i] = 1.0 if fast_v[i] > slow_v[i] else -1.0
    return _shift(pos, close.index)


def rsi_reversion(close: pd.Series, period: int, low: float, high: float) -> pd.Series:
    values = rsi(close.to_numpy(float), period)
    pos = 0.0
    out = np.zeros(len(values))
    for i, val in enumerate(values):
        if math.isnan(val):
            out[i] = 0.0
            continue
        if val <= low:
            pos = 1.0
        elif val >= high:
            pos = -1.0
        elif pos > 0 and val >= 50:
            pos = 0.0
        elif pos < 0 and val <= 50:
            pos = 0.0
        out[i] = pos
    return _shift(out, close.index)


def boll_breakout(close: pd.Series, period: int, width: float) -> pd.Series:
    values = close.to_numpy(float)
    mid, up, lo = boll(values, period, width)
    pos = 0.0
    out = np.zeros(len(values))
    for i in range(len(values)):
        if math.isnan(up[i]) or math.isnan(lo[i]):
            out[i] = 0.0
            continue
        price = values[i]
        if price > up[i]:
            pos = 1.0
        elif price < lo[i]:
            pos = -1.0
        elif pos > 0 and price < mid[i]:
            pos = 0.0
        elif pos < 0 and price > mid[i]:
            pos = 0.0
        out[i] = pos
    return _shift(out, close.index)


def macd_cross(close: pd.Series, fast: int, slow: int, signal: int) -> pd.Series:
    values = close.to_numpy(float)
    line, sig, _hist = macd(values, fast, slow, signal)
    pos = np.zeros(len(values))
    for i in range(len(values)):
        if math.isnan(line[i]) or math.isnan(sig[i]):
            continue
        pos[i] = 1.0 if line[i] > sig[i] else -1.0
    return _shift(pos, close.index)


CLASSIC = [
    {
        "name": "EMA交叉",
        "params": [
            {"fast": 8, "slow": 21, "label": "EMA 8/21"},
            {"fast": 12, "slow": 26, "label": "EMA 12/26"},
            {"fast": 20, "slow": 50, "label": "EMA 20/50"},
        ],
        "build": lambda close, p: ema_cross(close, p["fast"], p["slow"]),
    },
    {
        "name": "RSI均值回归",
        "params": [
            {"period": 14, "low": 30, "high": 70, "label": "RSI(14) 30/70"},
            {"period": 14, "low": 25, "high": 75, "label": "RSI(14) 25/75"},
            {"period": 7, "low": 30, "high": 70, "label": "RSI(7) 30/70"},
        ],
        "build": lambda close, p: rsi_reversion(close, p["period"], p["low"], p["high"]),
    },
    {
        "name": "布林突破",
        "params": [
            {"period": 20, "width": 2.0, "label": "布林 20 / 2 倍标准差"},
            {"period": 20, "width": 2.5, "label": "布林 20 / 2.5 倍标准差"},
        ],
        "build": lambda close, p: boll_breakout(close, p["period"], p["width"]),
    },
    {
        "name": "MACD",
        "params": [
            {"fast": 12, "slow": 26, "signal": 9, "label": "MACD 12/26/9"},
            {"fast": 8, "slow": 21, "signal": 5, "label": "MACD 8/21/5"},
        ],
        "build": lambda close, p: macd_cross(close, p["fast"], p["slow"], p["signal"]),
    },
]
