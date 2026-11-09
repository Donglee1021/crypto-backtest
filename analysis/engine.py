"""用 VectorBT 做研究回测。只比较历史路径，不下单。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

FEE = 0.0005  # OKX 永续吃单 0.05%


def position_from_score(score: pd.Series, threshold: float) -> pd.Series:
    values = pd.to_numeric(score, errors="coerce").to_numpy(dtype=float)
    out = np.zeros(len(values))
    for i, val in enumerate(values):
        if math.isnan(val):
            continue
        if val >= threshold:
            out[i] = 1.0
        elif val <= -threshold:
            out[i] = -1.0
    return pd.Series(out, index=score.index)


def _finite(value: float, digits: int | None = None):
    if value is None or not math.isfinite(value):
        return None
    if digits is None:
        return float(value)
    return round(float(value), digits)


def run_positions(df: pd.DataFrame, pos: pd.Series, atr_mult: float, funding: pd.Series | None,
                  bar_hours: float, with_details: bool = False) -> dict:
    import vectorbt as vbt

    pos = pos.reindex(df.index).fillna(0.0)
    close = df["close"].astype(float)
    open_ = df["open"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    kwargs = dict(
        entries=pos > 0,
        exits=pos <= 0,
        short_entries=pos < 0,
        short_exits=pos >= 0,
        price=open_,
        open=open_,
        high=high,
        low=low,
        fees=FEE,
        init_cash=10_000,
        upon_opposite_entry="reverse",
        freq=pd.Timedelta(hours=bar_hours),
    )
    if atr_mult and "atr" in df.columns:
        sl = (df["atr"].astype(float) * float(atr_mult) / open_).clip(lower=0.002, upper=0.5)
        kwargs["sl_stop"] = sl.fillna(0.5)
    try:
        pf = vbt.Portfolio.from_signals(close, **kwargs)
    except Exception as exc:
        print("vectorbt 止损参数失败，改为不止损", exc)
        kwargs.pop("sl_stop", None)
        pf = vbt.Portfolio.from_signals(close, **kwargs)
        atr_mult = 0.0
    rets = pf.returns().reindex(df.index).fillna(0.0).astype(float)
    if funding is not None and len(funding):
        rate = funding.reindex(df.index, method="ffill").fillna(0.0)
        rets = rets - rate * pos.astype(float) * (bar_hours / 8.0)
    equity = (1.0 + rets).cumprod()
    bench = close / float(close.iloc[0])
    total = float(equity.iloc[-1] - 1.0) if len(equity) else 0.0
    bench_ret = float(bench.iloc[-1] - 1.0) if len(bench) else 0.0
    dd = float((equity / equity.cummax() - 1.0).min()) if len(equity) else 0.0
    sharpe = 0.0
    std = float(rets.std()) if len(rets) else 0.0
    if std and math.isfinite(std) and std > 0:
        periods = (24.0 / bar_hours) * 365.0
        sharpe = float(rets.mean() / std * math.sqrt(periods))
    trades = _read_trades(pf)
    closed = [t for t in trades if t["closed"]]
    n = len(closed)
    if n:
        pnl = np.array([t["pnl"] for t in closed], dtype=float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        win_rate = float(len(wins) / n)
        gross_loss = abs(float(losses.sum())) if len(losses) else 0.0
        profit_factor = float(wins.sum() / gross_loss) if gross_loss else None
        holds = [t["hold_hours"] for t in closed if t["hold_hours"] is not None]
        avg_hold = float(np.mean(holds)) if holds else 0.0
    else:
        win_rate = 0.0
        profit_factor = None
        avg_hold = 0.0
    out = {
        "total_return": _finite(total, 4),
        "bench_return": _finite(bench_ret, 4),
        "max_drawdown": _finite(dd, 4),
        "sharpe": _finite(sharpe, 3),
        "win_rate": _finite(win_rate, 4),
        "trades": n,
        "profit_factor": _finite(profit_factor, 3) if profit_factor is not None else None,
        "avg_hold_hours": _finite(avg_hold, 2),
    }
    if with_details:
        out["equity"] = equity
        out["bench"] = bench
        out["trade_list"] = trades
    return out


def _read_trades(pf) -> list[dict]:
    try:
        count = int(pf.trades.count())
    except Exception:
        return []
    if not count:
        return []
    rec = pf.trades.records_readable
    rows = []
    for _, row in rec.iterrows():
        direction = str(row.get("Direction") or "")
        side = "short" if "Short" in direction or "short" in direction else "long"
        entry = pd.Timestamp(row["Entry Timestamp"])
        exit_raw = row.get("Exit Timestamp")
        exit_ts = None if pd.isna(exit_raw) else pd.Timestamp(exit_raw)
        hold = None
        if exit_ts is not None:
            hold = float((exit_ts - entry).total_seconds() / 3600.0)
        pnl = row.get("PnL")
        rows.append({
            "side": side,
            "entry": int(entry.timestamp() * 1000),
            "exit": None if exit_ts is None else int(exit_ts.timestamp() * 1000),
            "pnl": None if pnl is None or pd.isna(pnl) else round(float(pnl), 2),
            "hold_hours": hold,
            "closed": str(row.get("Status") or "Closed") == "Closed",
        })
    return rows


def objective(row: dict, min_trades: int) -> float:
    if row["trades"] < min_trades:
        return -1e9
    sharpe = row["sharpe"] if row["sharpe"] is not None else -1e9
    return float(sharpe)


def min_trade_count(bar_hours: float) -> int:
    if bar_hours >= 24:
        return 4
    if bar_hours >= 4:
        return 6
    return 8


def overfit_flag(train: dict, test: dict) -> bool:
    if train["trades"] < 8 or train["sharpe"] is None or test["sharpe"] is None:
        return False
    if train["sharpe"] - test["sharpe"] >= 0.8 and train["sharpe"] > 0.2:
        return True
    if (train["total_return"] or 0) > 0.1 and (test["total_return"] or 0) < 0:
        return True
    return False


def split_frame(df: pd.DataFrame, ratio: float = 0.7):
    cut = int(len(df) * ratio)
    cut = min(max(cut, 1), len(df) - 1)
    return df.iloc[:cut].copy(), df.iloc[cut:].copy()


def sweep_scores(df: pd.DataFrame, funding, bar_hours: float, grid: list[tuple[float, float]],
                 default: tuple[float, float], with_full: bool = True) -> dict:
    train, test = split_frame(df)
    need = min_trade_count(bar_hours)
    d_th, d_mult = default
    best_row = run_positions(train, position_from_score(train["score"], d_th), d_mult, funding, bar_hours)
    best_key = default
    best_obj = objective(best_row, need)
    for threshold, mult in grid:
        res = run_positions(train, position_from_score(train["score"], threshold), mult, funding, bar_hours)
        scored = objective(res, need)
        if scored > best_obj:
            best_row = res
            best_key = (threshold, mult)
            best_obj = scored
    default_train = run_positions(train, position_from_score(train["score"], d_th), d_mult, funding, bar_hours)
    default_test = run_positions(test, position_from_score(test["score"], d_th), d_mult, funding, bar_hours)
    best_test = run_positions(test, position_from_score(test["score"], best_key[0]), best_key[1], funding, bar_hours)
    out = {
        "default": default,
        "best": best_key,
        "default_train": _public(default_train),
        "default_test": _public(default_test),
        "best_train": _public(best_row if best_row is not None else default_train),
        "best_test": _public(best_test),
        "train_end": str(train.index[-1].date()),
        "test_start": str(test.index[0].date()),
    }
    if with_full:
        full = run_positions(df, position_from_score(df["score"], d_th), d_mult, funding, bar_hours, with_details=True)
        out["full"] = full
    return out


def sweep_positions(df: pd.DataFrame, funding, bar_hours: float, variants: list[dict], with_full: bool = False) -> dict:
    """variants: {label, pos} where pos is a Series aligned to df."""
    train, test = split_frame(df)
    need = min_trade_count(bar_hours)
    best = None
    best_label = variants[0]["label"]
    best_pos = variants[0]["pos"]
    for item in variants:
        res = run_positions(train, item["pos"].reindex(train.index).fillna(0.0), 0.0, funding, bar_hours)
        if best is None or objective(res, need) > objective(best, need):
            best = res
            best_label = item["label"]
            best_pos = item["pos"]
    chosen = next(item for item in variants if item["label"] == best_label)
    test_res = run_positions(test, best_pos.reindex(test.index).fillna(0.0), 0.0, funding, bar_hours)
    out = {
        "label": best_label,
        "best_train": _public(best),
        "best_test": _public(test_res),
        "train_end": str(train.index[-1].date()),
        "test_start": str(test.index[0].date()),
        "pos": best_pos,
    }
    if with_full:
        out["full"] = run_positions(df, best_pos.reindex(df.index).fillna(0.0), 0.0, funding, bar_hours, with_details=True)
        test_detail = run_positions(test, best_pos.reindex(test.index).fillna(0.0), 0.0, funding, bar_hours, with_details=True)
        out["test_detail"] = test_detail
    return out


def walk_forward_scores(df: pd.DataFrame, funding, bar_hours: float, grid, default) -> dict | None:
    n = len(df)
    if n < 240:
        return None
    edges = [int(n * i / 4) for i in range(5)]
    need = min_trade_count(bar_hours)
    folds = []
    for k in range(1, 4):
        train = df.iloc[:edges[k]]
        test = df.iloc[edges[k]:edges[k + 1]]
        if len(train) < 120 or len(test) < 30:
            continue
        d_th, d_mult = default
        best_row = run_positions(train, position_from_score(train["score"], d_th), d_mult, funding, bar_hours)
        best_key = default
        best_obj = objective(best_row, need)
        for threshold, mult in grid:
            res = run_positions(train, position_from_score(train["score"], threshold), mult, funding, bar_hours)
            scored = objective(res, need)
            if scored > best_obj:
                best_row = res
                best_key = (threshold, mult)
                best_obj = scored
        tested = run_positions(test, position_from_score(test["score"], best_key[0]), best_key[1], funding, bar_hours)
        folds.append(tested)
    return _fold_summary(folds)


def walk_forward_positions(df: pd.DataFrame, funding, bar_hours: float, build_variants) -> dict | None:
    """build_variants(frame) -> list of {label, pos} on that frame's index."""
    n = len(df)
    if n < 240:
        return None
    edges = [int(n * i / 4) for i in range(5)]
    need = min_trade_count(bar_hours)
    folds = []
    for k in range(1, 4):
        train = df.iloc[:edges[k]]
        test = df.iloc[edges[k]:edges[k + 1]]
        if len(train) < 120 or len(test) < 30:
            continue
        variants = build_variants(train)
        best = None
        best_label = variants[0]["label"]
        for item in variants:
            res = run_positions(train, item["pos"], 0.0, funding, bar_hours)
            if best is None or objective(res, need) > objective(best, need):
                best = res
                best_label = item["label"]
        # 在测试段上用同一组参数重算信号，不用训练段的仓位。
        again = {item["label"]: item["pos"] for item in build_variants(test)}
        tested = run_positions(test, again[best_label], 0.0, funding, bar_hours)
        folds.append(tested)
    return _fold_summary(folds)


def _fold_summary(folds: list[dict]) -> dict | None:
    if not folds:
        return None
    def avg(key):
        vals = [f[key] for f in folds if f.get(key) is not None]
        return round(float(np.mean(vals)), 4) if vals else None
    return {
        "folds": len(folds),
        "avg_test_sharpe": avg("sharpe"),
        "avg_test_return": avg("total_return"),
        "avg_test_bench": avg("bench_return"),
    }


def _public(row: dict) -> dict:
    return {k: row[k] for k in (
        "total_return", "bench_return", "max_drawdown", "sharpe", "win_rate", "trades", "profit_factor", "avg_hold_hours"
    )}


def downsample_line(series: pd.Series, n: int = 220) -> list[dict]:
    s = series.dropna()
    if not len(s):
        return []
    if len(s) > n:
        pos = [min(len(s) - 1, int(i * (len(s) - 1) / (n - 1))) for i in range(n)]
        s = s.iloc[pos]
    rows = []
    seen = set()
    for ts, val in s.items():
        stamp = int(pd.Timestamp(ts).timestamp())
        if stamp in seen or not math.isfinite(float(val)):
            continue
        seen.add(stamp)
        rows.append({"time": stamp, "value": round(float(val), 4)})
    return rows


def trade_markers(trades: list[dict], limit: int = 80) -> list[dict]:
    marks = []
    for trade in trades[-limit:]:
        side = trade["side"]
        marks.append({
            "time": int(trade["entry"] // 1000),
            "position": "belowBar" if side == "long" else "aboveBar",
            "color": "#26a69a" if side == "long" else "#ef5350",
            "shape": "arrowUp" if side == "long" else "arrowDown",
            "text": "多" if side == "long" else "空",
        })
    marks.sort(key=lambda item: item["time"])
    return marks
