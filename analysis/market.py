"""只读拉取 OKX 公共行情。不下单，不读账户，不使用 API 密钥。"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"

SYMBOLS = {"BTC": "BTC/USDT:USDT", "ETH": "ETH/USDT:USDT"}
# 15 分钟尽量覆盖一年半；1 小时和 4 小时两年；日线更长，方便周线 EMA200。
HISTORY = {"15m": 540, "1h": 730, "4h": 730, "1d": 2200}
BARS_MS = {"15m": 15 * 60 * 1000, "1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000, "1d": 24 * 60 * 60 * 1000}


def exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {"defaultType": "swap"},
    })


def _read_cache(path: Path):
    if not path.exists():
        return None
    try:
        old = pd.read_parquet(path)
    except Exception:
        return None
    if "ts" not in old.columns or not len(old):
        return None
    return old.sort_values("ts")


def _paginate(ex, symbol: str, timeframe: str, since: int, until: int) -> list:
    rows = []
    step = BARS_MS[timeframe]
    last_seen = None
    pages = 0
    while since < until and pages < 2500:
        pages += 1
        batch = None
        err = None
        for attempt in range(4):
            try:
                batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=100)
                err = None
                break
            except Exception as exc:
                err = exc
                time.sleep(1.2 * (attempt + 1))
        if err is not None or not batch:
            if err is not None:
                print("ohlcv stop", symbol, timeframe, err)
            break
        rows.extend(row for row in batch if since - step <= row[0] < until)
        nxt = int(batch[-1][0]) + step
        if last_seen is not None and batch[-1][0] <= last_seen:
            break
        last_seen = batch[-1][0]
        since = nxt
        if pages % 20 == 0:
            print("  ", symbol, timeframe, "pages", pages, "bars", len(rows))
        if len(batch) < 100 or batch[-1][0] >= until:
            break
    return rows


def fetch_ohlcv(coin: str, timeframe: str, days: int | None = None) -> pd.DataFrame:
    ex = exchange()
    symbol = SYMBOLS[coin]
    days = days if days is not None else HISTORY[timeframe]
    # 缓存按该周期的最长窗口保留。短请求只影响返回值，不能把已经下载的历史裁掉。
    keep_days = max(days, HISTORY[timeframe])
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    keep_from = int((datetime.now(timezone.utc) - timedelta(days=keep_days)).timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    path = CACHE / f"{coin}_{timeframe}.parquet"
    old = _read_cache(path)
    frames = []
    if old is None:
        print("fetch", coin, timeframe, "days", days)
        frames.append(pd.DataFrame(
            _paginate(ex, symbol, timeframe, start, now_ms + BARS_MS[timeframe]),
            columns=["ts", "open", "high", "low", "close", "volume"],
        ))
    else:
        first = int(old["ts"].iloc[0])
        last = int(old["ts"].iloc[-1])
        step = BARS_MS[timeframe]
        if first > start + step * 2:
            gap = _paginate(ex, symbol, timeframe, start, first)
            if gap:
                frames.append(pd.DataFrame(gap, columns=["ts", "open", "high", "low", "close", "volume"]))
        frames.append(old[["ts", "open", "high", "low", "close", "volume"]])
        if last + step < now_ms - step:
            gap = _paginate(ex, symbol, timeframe, last + step, now_ms + step)
            if gap:
                frames.append(pd.DataFrame(gap, columns=["ts", "open", "high", "low", "close", "volume"]))
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        raise RuntimeError(f"没有拉到 {coin} {timeframe}")
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates("ts").sort_values("ts")
    df = df[df["ts"] >= keep_from]
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("time")
    CACHE.mkdir(parents=True, exist_ok=True)
    df.reset_index(drop=True).to_parquet(path, index=False)
    print(coin, timeframe, "bars", len(df), df.index[0].date(), "→", df.index[-1].date())
    if days < keep_days:
        cut = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
        return df[df.index >= cut]
    return df


def fetch_funding(coin: str, days: int = 730) -> pd.DataFrame:
    ex = exchange()
    symbol = SYMBOLS[coin]
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    path = CACHE / f"{coin}_funding.parquet"
    old = _read_cache(path)
    if old is not None and len(old):
        since = max(since, int(old["ts"].iloc[-1]) + 8 * 60 * 60 * 1000)
    rows = []
    pages = 0
    while pages < 80:
        pages += 1
        try:
            batch = ex.fetch_funding_rate_history(symbol, since=since, limit=100)
        except Exception as exc:
            print("funding stop", coin, exc)
            break
        if not batch:
            break
        rows.extend(batch)
        last = batch[-1].get("timestamp") or 0
        nxt = int(last) + 8 * 60 * 60 * 1000
        if nxt <= since:
            break
        since = nxt
        if len(batch) < 100:
            break
    frames = []
    if old is not None and len(old):
        frames.append(old)
    if rows:
        raw = pd.DataFrame(rows)
        ts = raw["timestamp"] if "timestamp" in raw else raw.get("fundingTime")
        rate = raw["fundingRate"] if "fundingRate" in raw else raw.get("funding_rate")
        frames.append(pd.DataFrame({"ts": ts, "rate": pd.to_numeric(rate, errors="coerce")}))
    if not frames:
        return pd.DataFrame(columns=["rate"])
    out = pd.concat(frames, ignore_index=True)
    out = out.dropna().drop_duplicates("ts").sort_values("ts")
    out["time"] = pd.to_datetime(out["ts"], unit="ms", utc=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    out[["ts", "rate"]].to_parquet(path, index=False)
    return out.set_index("time")[["rate"]]


def fetch_fng() -> pd.DataFrame:
    req = urllib.request.Request(
        "https://api.alternative.me/fng/?limit=0",
        headers={"User-Agent": "crypto-backtest"},
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = json.loads(resp.read().decode())["data"]
    rows = [{"time": pd.to_datetime(int(x["timestamp"]), unit="s", utc=True), "value": int(x["value"])} for x in data]
    df = pd.DataFrame(rows).drop_duplicates("time").sort_values("time").set_index("time")
    return df


def drop_forming(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """丢掉还没收盘的最后一根，避免把未完成的 K 线写进研究。"""
    if df is None or not len(df):
        return df
    close_at = df.index[-1] + pd.Timedelta(milliseconds=BARS_MS[timeframe])
    now = pd.Timestamp.now(tz="UTC")
    if close_at > now:
        return df.iloc[:-1]
    return df
