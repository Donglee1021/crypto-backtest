"""超短线信号和回测。只读公开 K 线，不下单，不用密钥。

日内看 1 分钟、5 分钟、15 分钟。1 小时只做方向过滤。两边不一致就空仓。
止损和止盈按 15 分钟 ATR 算，是模拟参数，不是建议。
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from analysis.scoring import atr, boll, ema, macd, rsi, sma

BARS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}
OKX_BAR = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H"}
BINANCE_BAR = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h"}
FEE = 0.0005
SLIP = 0.0002
STOP_MULT = 1.5
TARGET1_MULT = 1.5
TARGET2_MULT = 3.0
MAX_HOLD_HOURS = 6
DAY_LOSS = 0.03
COOLDOWN_MIN = 45


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-backtest"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def _bars_from_okx(rows) -> pd.DataFrame:
    recs = []
    for row in rows:
        recs.append({
            "ts": pd.Timestamp(int(row[0]), unit="ms", tz="UTC"),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5] or 0),
        })
    if not recs:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(recs).drop_duplicates("ts").sort_values("ts")
    return df.set_index("ts")


def fetch_okx(coin: str, timeframe: str, days: float) -> pd.DataFrame:
    inst = f"{coin}-USDT-SWAP"
    bar = OKX_BAR[timeframe]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = []
    after = None
    for _ in range(160):
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar={bar}&limit=100"
        if after:
            url += f"&after={after}"
        payload = _get(url)
        if payload.get("code") != "0":
            raise RuntimeError(payload.get("msg") or "okx")
        batch = payload.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        oldest = int(batch[-1][0])
        after = str(oldest)
        if oldest <= int(start.timestamp() * 1000):
            break
        time.sleep(0.05)
    df = _bars_from_okx(rows)
    return df[df.index >= pd.Timestamp(start)]


def fetch_binance(coin: str, timeframe: str, days: float) -> pd.DataFrame:
    symbol = f"{coin}USDT"
    interval = BINANCE_BAR[timeframe]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    cursor = int(start.timestamp() * 1000)
    stop = int(end.timestamp() * 1000)
    rows = []
    for _ in range(40):
        url = (
            "https://fapi.binance.com/fapi/v1/klines"
            f"?symbol={symbol}&interval={interval}&limit=1500&startTime={cursor}&endTime={stop}"
        )
        batch = _get(url)
        if not isinstance(batch, list) or not batch:
            break
        for row in batch:
            rows.append({
                "ts": pd.Timestamp(int(row[0]), unit="ms", tz="UTC"),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5] or 0),
            })
        nxt = int(batch[-1][0]) + BARS[timeframe] * 1000
        if nxt <= cursor or len(batch) < 1500:
            break
        cursor = nxt
        time.sleep(0.05)
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).drop_duplicates("ts").sort_values("ts").set_index("ts")
    return df


def fetch_frame(coin: str, timeframe: str, days: float) -> pd.DataFrame:
    try:
        df = fetch_okx(coin, timeframe, days)
        if len(df) > 30:
            return df
    except Exception as exc:
        print("okx", coin, timeframe, exc, flush=True)
    return fetch_binance(coin, timeframe, days)


def _pack(close, high, low, volume, fast: int, slow: int) -> dict:
    macd_line, _sig, hist = macd(close)
    mid, _up, _lo = boll(close)
    return {
        "ema_fast": ema(close, fast),
        "ema_slow": ema(close, slow),
        "rsi": rsi(close, 14),
        "hist": hist,
        "mid": mid,
        "atr": atr(high, low, close, 14),
        "vol": sma(volume, 20),
        "volume": volume,
    }


def _finite(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in (float("inf"), float("-inf"))


def score_at(close, pack, i: int, kind: str) -> tuple[int, list[str]]:
    """返回带符号的分数和原因。正数偏多，负数偏空。"""
    reasons = []
    score = 0
    fast = pack["ema_fast"][i]
    slow = pack["ema_slow"][i]
    hist = pack["hist"][i]
    rs = pack["rsi"][i]
    mid = pack["mid"][i]
    px = close[i]
    if not all(_finite(v) for v in (fast, slow, hist, rs, mid, px)):
        return 0, ["指标还没凑齐"]
    if kind == "hour":
        if fast > slow:
            score += 1
            reasons.append("EMA20 在 EMA50 之上")
        else:
            score -= 1
            reasons.append("EMA20 在 EMA50 之下")
        if px > fast:
            score += 1
            reasons.append("价格在 EMA20 之上")
        else:
            score -= 1
            reasons.append("价格在 EMA20 之下")
    else:
        if fast > slow:
            score += 1
            reasons.append("EMA9 在 EMA21 之上")
        else:
            score -= 1
            reasons.append("EMA9 在 EMA21 之下")
        if px > mid:
            score += 1
            reasons.append("价格在布林中轨之上")
        else:
            score -= 1
            reasons.append("价格在布林中轨之下")
    if hist > 0:
        score += 1
        reasons.append("MACD 柱为正")
    elif hist < 0:
        score -= 1
        reasons.append("MACD 柱为负")
    if kind == "hour":
        if rs >= 50:
            score += 1
            reasons.append(f"RSI {rs:.0f}，在 50 之上")
        else:
            score -= 1
            reasons.append(f"RSI {rs:.0f}，在 50 之下")
    else:
        if rs >= 55:
            score += 1
            reasons.append(f"RSI {rs:.0f}")
        elif rs <= 45:
            score -= 1
            reasons.append(f"RSI {rs:.0f}")
        vol = pack["volume"][i]
        vavg = pack["vol"][i]
        if _finite(vavg) and vavg > 0 and vol > vavg:
            reasons.append("成交量高于近 20 根均量")
    return score, reasons


def call_from_score(score: int, need: int) -> str:
    if score >= need:
        return "做多"
    if score <= -need:
        return "做空"
    return "观望"


def side_of(call: str) -> int:
    if call == "做多":
        return 1
    if call == "做空":
        return -1
    return 0


def frame_snapshot(df: pd.DataFrame, kind: str) -> dict | None:
    if df is None or len(df) < 60:
        return None
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    volume = df["volume"].to_numpy(float)
    fast, slow = (20, 50) if kind == "hour" else (9, 21)
    pack = _pack(close, high, low, volume, fast, slow)
    score, reasons = score_at(close, pack, len(df) - 1, kind)
    need = 2
    call = call_from_score(score, need)
    atr_v = pack["atr"][-1]
    return {
        "call": call,
        "score": int(score),
        "reasons": reasons,
        "price": float(close[-1]),
        "atr": float(atr_v) if _finite(atr_v) else float(close[-1]) * 0.002,
        "bar": df.index[-1].isoformat(),
        "high": float(high[-1]),
        "low": float(low[-1]),
    }


def intraday_from_frames(frames: dict[str, pd.DataFrame]) -> dict:
    snaps = {}
    parts = []
    for name in ("1m", "5m", "15m"):
        snap = frame_snapshot(frames.get(name), "intra")
        if not snap:
            parts.append(f"{name} 数据不够")
            continue
        snaps[name] = snap
        parts.append(f"{name} {snap['call']}（{snap['score']:+d}）：" + "，".join(snap["reasons"][:2]))
    if not snaps:
        return {"call": "观望", "score": 0, "reasons": parts or ["日内 K 线没取到"], "atr": 0.0, "price": 0.0, "bar": "", "high": 0.0, "low": 0.0}
    s5 = side_of(snaps.get("5m", {}).get("call", "观望"))
    s15 = side_of(snaps.get("15m", {}).get("call", "观望"))
    s1 = side_of(snaps["1m"]["call"]) if "1m" in snaps else None
    vote = _intra_vote(s5, s15, s1)
    if s5 and s15 and s5 != s15:
        parts.append("5 分钟和 15 分钟方向相反")
    call = "做多" if vote > 0 else "做空" if vote < 0 else "观望"
    named = [snaps[k] for k in snaps if snaps[k]["call"] == call] if call != "观望" else []
    score = int(round(sum(v["score"] for v in named) / len(named))) if named else 0
    anchor = snaps.get("15m") or snaps.get("5m") or snaps.get("1m")
    return {
        "call": call,
        "score": score,
        "reasons": parts,
        "atr": anchor["atr"],
        "price": anchor["price"],
        "bar": anchor["bar"],
        "high": anchor["high"],
        "low": anchor["low"],
    }


def prepare(df: pd.DataFrame, kind: str) -> dict:
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    volume = df["volume"].to_numpy(float)
    fast, slow = (20, 50) if kind == "hour" else (9, 21)
    pack = _pack(close, high, low, volume, fast, slow)
    side = np.zeros(len(df), dtype=int)
    for i in range(len(df)):
        score, _ = score_at(close, pack, i, kind)
        if score >= 2:
            side[i] = 1
        elif score <= -2:
            side[i] = -1
    return {"side": side, "atr": pack["atr"], "index": df.index}


def _asof_side(clock: pd.DatetimeIndex, frame: pd.DataFrame, kind: str, shift: pd.Timedelta) -> np.ndarray:
    """只用已经收盘的高周期 K 线，避免把未收盘的那根算进去。"""
    prep = prepare(frame, kind)
    ready = pd.Series(prep["side"], index=frame.index + shift)
    aligned = ready.reindex(clock, method="ffill")
    return aligned.fillna(0).to_numpy(int)


def run_book(m5: pd.DataFrame, m15: pd.DataFrame, h1: pd.DataFrame, m1: pd.DataFrame | None = None) -> dict:
    """在 5 分钟上记账。止损优先于止盈。费用按名义收取。"""
    intra5 = _asof_side(m5.index, m5, "intra", pd.Timedelta(minutes=5))
    intra15 = _asof_side(m5.index, m15, "intra", pd.Timedelta(minutes=15))
    hour = _asof_side(m5.index, h1, "hour", pd.Timedelta(hours=1))
    intra1 = _asof_side(m5.index, m1, "intra", pd.Timedelta(minutes=1)) if m1 is not None and len(m1) else None
    atr15 = prepare(m15, "intra")["atr"]
    atr_series = pd.Series(atr15, index=m15.index + pd.Timedelta(minutes=15)).reindex(m5.index, method="ffill")
    cash = 10_000.0
    equity_peak = cash
    max_dd = 0.0
    pos = None
    wins = 0
    losses = 0
    trades = []
    day = None
    day_pnl = 0.0
    day_equity = cash
    cooldown_until = None
    curve = []

    def mark(i, price):
        equity = cash
        if pos:
            equity += pos["side"] * pos["qty"] * (price - pos["entry"])
        return equity

    for i in range(len(m5)):
        ts = m5.index[i]
        opened_px = float(m5["open"].iloc[i])
        price = float(m5["close"].iloc[i])
        high = float(m5["high"].iloc[i])
        low = float(m5["low"].iloc[i])
        if day != ts.date():
            day = ts.date()
            day_pnl = 0.0
            day_equity = mark(i, price)
        if pos is None:
            vote_now = _intra_vote(intra5[i], intra15[i], None if intra1 is None else intra1[i])
            blocked = day_equity > 0 and day_pnl <= -DAY_LOSS * day_equity
            cooling = cooldown_until is not None and ts < cooldown_until
            if vote_now != 0 and vote_now == int(hour[i]) and not blocked and not cooling:
                atr_v = atr_series.iloc[i]
                atr_v = float(atr_v) if _finite(float(atr_v)) else opened_px * 0.002
                stop_dist = max(atr_v * STOP_MULT, opened_px * 0.001)
                equity_now = mark(i, opened_px)
                qty = min(equity_now * 0.01 / stop_dist, equity_now * 0.20 / opened_px)
                if qty > 0 and equity_now > 0:
                    fill = opened_px * (1 + vote_now * SLIP)
                    fee = qty * fill * FEE
                    cash -= fee
                    pos = {
                        "side": int(vote_now),
                        "entry": fill,
                        "qty": qty,
                        "stop": fill - vote_now * stop_dist,
                        "target1": fill + vote_now * atr_v * TARGET1_MULT,
                        "target2": fill + vote_now * atr_v * TARGET2_MULT,
                        "reduced": False,
                        "realized": -fee,
                        "opened": ts,
                    }
        if pos and ts >= pos["opened"]:
            exit_px = None
            why = ""
            if pos["side"] > 0 and low <= pos["stop"]:
                exit_px, why = pos["stop"], "碰到 ATR 止损"
            elif pos["side"] < 0 and high >= pos["stop"]:
                exit_px, why = pos["stop"], "碰到 ATR 止损"
            elif not pos["reduced"]:
                hit1 = (pos["side"] > 0 and high >= pos["target1"]) or (pos["side"] < 0 and low <= pos["target1"])
                if hit1:
                    part = pos["qty"] * 0.5
                    fill = pos["target1"]
                    gross = pos["side"] * part * (fill - pos["entry"])
                    fee = part * fill * FEE
                    pnl = gross - fee
                    cash += pnl
                    day_pnl += pnl
                    pos["realized"] += pnl
                    pos["qty"] -= part
                    pos["reduced"] = True
                    pos["stop"] = pos["entry"]
            if exit_px is None and pos["reduced"]:
                hit2 = (pos["side"] > 0 and high >= pos["target2"]) or (pos["side"] < 0 and low <= pos["target2"])
                if hit2:
                    exit_px, why = pos["target2"], "到达第二目标"
            if exit_px is None and ts >= pos["opened"] + pd.Timedelta(hours=MAX_HOLD_HOURS):
                exit_px, why = price, "持仓超过 6 小时"
            if exit_px is None and ts > pos["opened"]:
                vote_now = _intra_vote(intra5[i], intra15[i], None if intra1 is None else intra1[i])
                if vote_now != 0 and vote_now != pos["side"]:
                    exit_px, why = price, "日内方向反过来了"
                elif int(hour[i]) != 0 and int(hour[i]) != pos["side"]:
                    exit_px, why = price, "1 小时方向反过来了"
            if exit_px is not None:
                fill = exit_px * (1 - pos["side"] * SLIP)
                gross = pos["side"] * pos["qty"] * (fill - pos["entry"])
                fee = pos["qty"] * fill * FEE
                pnl = gross - fee + pos["realized"]
                cash += gross - fee
                day_pnl += gross - fee
                trades.append(pnl)
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
                if why == "碰到 ATR 止损":
                    cooldown_until = ts + pd.Timedelta(minutes=COOLDOWN_MIN)
                pos = None
        equity = mark(i, price)
        equity_peak = max(equity_peak, equity)
        if equity_peak > 0:
            max_dd = max(max_dd, (equity_peak - equity) / equity_peak)
        if i % 12 == 0:
            curve.append(equity)
    if pos:
        price = float(m5["close"].iloc[-1])
        gross = pos["side"] * pos["qty"] * (price - pos["entry"])
        fee = pos["qty"] * price * FEE
        pnl = gross - fee + pos["realized"]
        cash += gross - fee
        trades.append(pnl)
        if pnl >= 0:
            wins += 1
        else:
            losses += 1
        pos = None
    end = cash
    count = wins + losses
    return {
        "trades": count,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / count) if count else None,
        "return": end / 10_000.0 - 1.0,
        "end_equity": end,
        "max_drawdown": max_dd,
    }


def _intra_vote(s5: int, s15: int, s1) -> int:
    votes = [s5, s15] if s1 is None else [s5, s15, s1]
    longs = sum(1 for v in votes if v > 0)
    shorts = sum(1 for v in votes if v < 0)
    if longs > 0 and shorts > 0:
        return 0
    if s5 > 0 and s15 > 0:
        return 1
    if s5 < 0 and s15 < 0:
        return -1
    if s1 is not None and ((s5 > 0 and s1 > 0) or (s15 > 0 and s1 > 0)) and shorts == 0:
        return 1
    if s1 is not None and ((s5 < 0 and s1 < 0) or (s15 < 0 and s1 < 0)) and longs == 0:
        return -1
    return 0


def apply_exit(pos: dict, high: float, low: float, close: float, ts: datetime, vote: int, hour_side: int):
    """返回平仓或减仓指令。止损优先。pos 里的减仓标记会被改掉。"""
    opened = pos.get("opened_at")
    if isinstance(opened, str):
        opened_ts = datetime.fromisoformat(opened)
    else:
        opened_ts = opened
    if opened_ts is not None and ts <= opened_ts:
        return None
    side = pos["side"]
    if side > 0 and low <= pos["stop"]:
        return {"action": "平仓", "fill": pos["stop"], "reason": "碰到 ATR 止损"}
    if side < 0 and high >= pos["stop"]:
        return {"action": "平仓", "fill": pos["stop"], "reason": "碰到 ATR 止损"}
    if not pos.get("reduced"):
        hit1 = (side > 0 and high >= pos["target1"]) or (side < 0 and low <= pos["target1"])
        if hit1:
            return {"action": "减仓", "fill": pos["target1"], "reason": "到达第一目标，减掉一半，止损挪到开仓价"}
    if pos.get("reduced"):
        hit2 = (side > 0 and high >= pos["target2"]) or (side < 0 and low <= pos["target2"])
        if hit2:
            return {"action": "平仓", "fill": pos["target2"], "reason": "到达第二目标"}
    if opened_ts is not None and ts >= opened_ts + timedelta(hours=MAX_HOLD_HOURS):
        return {"action": "平仓", "fill": close, "reason": "持仓超过 6 小时"}
    if vote != 0 and vote != side:
        return {"action": "平仓", "fill": close, "reason": "日内方向反过来了"}
    if hour_side != 0 and hour_side != side:
        return {"action": "平仓", "fill": close, "reason": "1 小时方向反过来了"}
    return None


def latest(coin: str) -> dict:
    frames = {
        "1m": fetch_frame(coin, "1m", 1.2),
        "5m": fetch_frame(coin, "5m", 5),
        "15m": fetch_frame(coin, "15m", 8),
        "1h": fetch_frame(coin, "1h", 20),
    }
    intra = intraday_from_frames(frames)
    hour = frame_snapshot(frames["1h"], "hour") or {
        "call": "观望", "score": 0, "reasons": ["1 小时 K 线没取到"], "atr": intra.get("atr") or 0, "price": intra.get("price") or 0, "bar": "",
    }
    return {"frames": frames, "intraday": intra, "hour": hour}


def backtest(days: int = 30) -> dict:
    out = {"days": days, "coins": {}, "book": None}
    books = []
    for coin in ("BTC", "ETH"):
        print("backtest", coin, flush=True)
        m5 = fetch_frame(coin, "5m", days + 12)
        m15 = fetch_frame(coin, "15m", days + 12)
        h1 = fetch_frame(coin, "1h", days + 20)
        cutoff = m5.index.max() - pd.Timedelta(days=days)
        m5 = m5[m5.index >= cutoff]
        stats = run_book(m5, m15, h1)
        stats["bars"] = int(len(m5))
        out["coins"][coin] = stats
        books.append(stats)
        print(coin, stats, flush=True)
    # 两个币分开各用 1 万，避免合成时互相挤占仓位把结论搅在一起。
    out["note"] = "每个币单独用 1 万模拟权益。信号用已经收盘的 K 线，下一根 5 分钟的开盘价成交。止损和止盈用当根最高最低价，同一根里先算止损。扣了 0.05% 费用和 0.02% 滑点。"
    return out


if __name__ == "__main__":
    print(json.dumps(backtest(30), ensure_ascii=False, indent=2))
