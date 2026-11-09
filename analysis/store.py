"""信号和回测结果放进 DuckDB，同时导出页面用的 JSON。"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "history.duckdb"
PARQUET = ROOT / "data" / "history" / "signals.parquet"
SUMMARY = ROOT / "data" / "backtest" / "summary.json"
SIGNALS = ROOT / "data" / "backtest" / "signals.json"
CHARTS = ROOT / "data" / "backtest" / "charts.json"
LATEST = ROOT / "data" / "backtest" / "latest.json"

OUTCOME_COLS = ["ret_1h", "ret_4h", "ret_24h", "hit_1h", "hit_4h", "hit_24h"]


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            ts TIMESTAMP,
            coin VARCHAR,
            horizon VARCHAR,
            score DOUBLE,
            verdict VARCHAR,
            price DOUBLE,
            factors VARCHAR,
            ret_1h DOUBLE,
            ret_4h DOUBLE,
            ret_24h DOUBLE,
            hit_1h BOOLEAN,
            hit_4h BOOLEAN,
            hit_24h BOOLEAN
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS backtests (
            run_ts TIMESTAMP,
            coin VARCHAR,
            horizon VARCHAR,
            kind VARCHAR,
            params VARCHAR,
            split VARCHAR,
            total_return DOUBLE,
            bench_return DOUBLE,
            max_drawdown DOUBLE,
            sharpe DOUBLE,
            win_rate DOUBLE,
            trades INTEGER,
            profit_factor DOUBLE,
            avg_hold_hours DOUBLE
        )
    """)
    return con


def _iso(ts) -> str:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.isoformat()


def _naive(ts) -> pd.Timestamp:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp.floor("s")


def insert_signals(rows: list[dict]):
    if not rows:
        return
    con = connect()
    df = pd.DataFrame(rows)
    df["ts"] = df["ts"].map(_naive)
    for col in OUTCOME_COLS:
        if col not in df.columns:
            df[col] = None
    con.register("incoming", df)
    con.execute("""
        INSERT INTO signals
        SELECT ts, coin, horizon, score, verdict, price, factors,
               ret_1h, ret_4h, ret_24h, hit_1h, hit_4h, hit_24h
        FROM incoming i
        WHERE NOT EXISTS (
            SELECT 1 FROM signals s
            WHERE s.ts = i.ts AND s.coin = i.coin AND s.horizon = i.horizon
        )
    """)
    con.close()


def update_outcomes():
    """用后来记下的价格，给还没有结果的信号补上 1 小时 / 4 小时 / 24 小时。"""
    con = connect()
    df = con.execute("SELECT * FROM signals ORDER BY ts").df()
    if df.empty:
        con.close()
        return
    for _coin, group in df.groupby("coin"):
        path = group.drop_duplicates("ts").sort_values("ts")
        times = list(path["ts"])
        prices = {t: p for t, p in zip(path["ts"], path["price"])}
        for _, row in group.iterrows():
            price0 = row["price"]
            if not price0 or pd.isna(price0):
                continue
            updates = {}
            for label, hours in (("1h", 1), ("4h", 4), ("24h", 24)):
                if row[f"ret_{label}"] is not None and not pd.isna(row[f"ret_{label}"]):
                    continue
                target = row["ts"] + pd.Timedelta(hours=hours)
                later = [t for t in times if t >= target]
                if not later:
                    continue
                ret = (prices[later[0]] / price0 - 1.0) * 100.0
                hit = _hit(row["verdict"], ret)
                updates[f"ret_{label}"] = round(float(ret), 3)
                updates[f"hit_{label}"] = hit
            if updates:
                sets = ", ".join(f"{key} = ?" for key in updates)
                con.execute(
                    f"UPDATE signals SET {sets} WHERE ts = ? AND coin = ? AND horizon = ?",
                    list(updates.values()) + [row["ts"], row["coin"], row["horizon"]],
                )
    con.close()


def _hit(verdict, ret_pct):
    text = verdict or ""
    if "多" in text and "空" not in text:
        return bool(ret_pct > 0)
    if "空" in text:
        return bool(ret_pct < 0)
    return None


def _rate(frame: pd.DataFrame, col: str) -> dict:
    if frame.empty or col not in frame:
        return {"hit": None, "n": 0}
    valid = frame[col].dropna()
    if not len(valid):
        return {"hit": None, "n": 0}
    return {"hit": round(float(valid.astype(bool).mean()), 4), "n": int(len(valid))}


def export_frontend():
    con = connect()
    con.execute("DELETE FROM signals WHERE ts < now() - INTERVAL 180 DAY")
    con.execute("""
        DELETE FROM backtests
        WHERE run_ts NOT IN (SELECT DISTINCT run_ts FROM backtests ORDER BY run_ts DESC LIMIT 8)
    """)
    full = con.execute("SELECT * FROM signals ORDER BY ts").df()
    PARQUET.parent.mkdir(parents=True, exist_ok=True)
    if not full.empty:
        full.to_parquet(PARQUET, index=False)
    sig = con.execute("SELECT * FROM signals ORDER BY ts DESC LIMIT 50").df()
    SIGNALS.parent.mkdir(parents=True, exist_ok=True)
    items = []
    for _, row in sig.iterrows():
        items.append(_signal_item(row))
    stats = {"by_horizon": {}, "by_coin": {}}
    if not full.empty:
        for horizon, group in full.groupby("horizon"):
            stats["by_horizon"][str(horizon)] = {
                "1h": _rate(group, "hit_1h"),
                "4h": _rate(group, "hit_4h"),
                "24h": _rate(group, "hit_24h"),
            }
        for coin, coin_group in full.groupby("coin"):
            stats["by_coin"][str(coin)] = {}
            for horizon, group in coin_group.groupby("horizon"):
                stats["by_coin"][str(coin)][str(horizon)] = {
                    "1h": _rate(group, "hit_1h"),
                    "4h": _rate(group, "hit_4h"),
                    "24h": _rate(group, "hit_24h"),
                }
    SIGNALS.write_text(json.dumps({
        "items": items,
        "stats": stats,
        "count": int(len(full)),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    con.close()


def _signal_item(row) -> dict:
    def num(key):
        value = row[key]
        return None if pd.isna(value) else (round(float(value), 4) if key == "score" else float(value))

    def flag(key):
        value = row[key]
        if pd.isna(value):
            return None
        return bool(value)

    return {
        "ts": _iso(row["ts"]),
        "coin": row["coin"],
        "horizon": row["horizon"],
        "score": num("score"),
        "verdict": row["verdict"],
        "price": None if pd.isna(row["price"]) else float(row["price"]),
        "ret_1h": num("ret_1h"),
        "ret_4h": num("ret_4h"),
        "ret_24h": num("ret_24h"),
        "hit_1h": flag("hit_1h"),
        "hit_4h": flag("hit_4h"),
        "hit_24h": flag("hit_24h"),
    }


def insert_backtests(rows: list[dict]):
    if not rows:
        return
    con = connect()
    df = pd.DataFrame(rows)
    df["run_ts"] = df["run_ts"].map(_naive)
    con.register("bt", df)
    con.execute("""
        INSERT INTO backtests
        SELECT run_ts, coin, horizon, kind, params, split,
               total_return, bench_return, max_drawdown, sharpe, win_rate,
               trades, profit_factor, avg_hold_hours
        FROM bt
    """)
    con.close()


def backfill_signals(coin: str, frames: dict[str, pd.DataFrame], hourly: pd.DataFrame, days: int = 60):
    """用已收盘的历史分数补最近两个月的信号，并直接填上之后的涨跌。"""
    if hourly is None or len(hourly) < 30:
        return []
    end = hourly.index[-1]
    start = end - pd.Timedelta(days=days)
    clock = hourly[hourly.index >= start]
    closes = clock["close"]
    opens = clock["open"]
    rows = []
    aligned = {}
    for name, frame in frames.items():
        if frame is None or not len(frame) or "raw_score" not in frame.columns:
            continue
        view = frame[["raw_score", "strength"]].copy()
        view["raw_score"] = pd.to_numeric(view["raw_score"], errors="coerce")
        aligned[name] = view.reindex(clock.index, method="ffill")
    for ts in clock.index:
        price = float(opens.loc[ts])
        rets = {}
        for label, hours in (("1h", 1), ("4h", 4), ("24h", 24)):
            future_open = ts + pd.Timedelta(hours=hours - 1)
            if future_open not in closes.index:
                rets[label] = None
            else:
                rets[label] = (float(closes.loc[future_open]) / price - 1.0) * 100.0
        for name, view in aligned.items():
            raw = view.loc[ts, "raw_score"] if ts in view.index else None
            if raw is None or pd.isna(raw):
                continue
            verdict = view.loc[ts, "strength"]
            if not isinstance(verdict, str) or verdict in ("nan", "None"):
                continue
            item = {
                "ts": ts,
                "coin": coin,
                "horizon": name,
                "score": round(float(raw), 4),
                "verdict": verdict,
                "price": price,
                "factors": "由已收盘K线重算，不是当时页面的实时快照",
            }
            for label in ("1h", "4h", "24h"):
                ret = rets[label]
                item[f"ret_{label}"] = None if ret is None else round(ret, 3)
                item[f"hit_{label}"] = None if ret is None else _hit(verdict, ret)
            rows.append(item)
    return rows
