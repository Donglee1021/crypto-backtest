#!/usr/bin/env python3
"""每日回测。只读公共行情，不下单。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.allocation import analyze
from analysis.engine import (
    downsample_line,
    overfit_flag,
    sweep_positions,
    sweep_scores,
    trade_markers,
    walk_forward_positions,
    walk_forward_scores,
)
from analysis.horizons import (
    DROPPED,
    _weekly_frame,
    build_long,
    build_mid,
    build_scalp,
    current_signals,
    rule_frame,
)
from analysis.market import drop_forming, fetch_fng, fetch_funding, fetch_ohlcv
from analysis.scoring import score_frame
from analysis.store import CHARTS, SUMMARY, backfill_signals, insert_backtests, insert_signals
from analysis.strategies import CLASSIC

REPORTS = ROOT / "reports"
TF_GRID = [(th, mult) for th in (2, 3, 4, 5, 6) for mult in (0.0, 1.5, 2.0)]
HZ_GRID = [(th, mult) for th in (2.5, 4.0, 6.0) for mult in (0.0, 1.5, 2.0)]
BAR = {"15分钟": 0.25, "1小时": 1.0, "4小时": 4.0, "日线": 24.0, "超短线": 0.25, "中线": 1.0, "长线": 24.0}


def log(msg: str):
    print(msg, flush=True)


def _span(df: pd.DataFrame) -> tuple[str, str, int]:
    return df.index[0].date().isoformat(), df.index[-1].date().isoformat(), int(len(df))


def _params_text(threshold: float, mult: float) -> str:
    stop = "不止损" if not mult else f"ATR 止损 {mult:g} 倍"
    th = int(threshold) if float(threshold).is_integer() else threshold
    return f"阈值 ±{th}，{stop}"


def _row(coin, name, group, timeframe, variant, params, train, test, walk, df, view):
    start, end, bars = _span(df)
    return {
        "coin": coin,
        "name": name,
        "group": group,
        "timeframe": timeframe,
        "variant": variant,
        "params": params,
        "train": train,
        "test": test,
        "overfit": overfit_flag(train, test),
        "walk_forward": walk,
        "bars": bars,
        "start": start,
        "end": end,
        "view": view,
        "train_end": None,
        "test_start": None,
    }


def _db(now, item, split, metrics):
    return {
        "run_ts": now,
        "coin": item["coin"],
        "horizon": f"{item['name']}·{item['variant']}",
        "kind": item["group"],
        "params": item["params"],
        "split": split,
        "total_return": metrics["total_return"],
        "bench_return": metrics["bench_return"],
        "max_drawdown": metrics["max_drawdown"],
        "sharpe": metrics["sharpe"],
        "win_rate": metrics["win_rate"],
        "trades": metrics["trades"],
        "profit_factor": metrics["profit_factor"],
        "avg_hold_hours": metrics["avg_hold_hours"],
    }


def _view(detail: dict) -> dict:
    return {
        "equity": downsample_line(detail["equity"]),
        "bench": downsample_line(detail["bench"]),
        "markers": trade_markers(detail.get("trade_list") or []),
    }


def _candles(df: pd.DataFrame) -> list[dict]:
    tail = df.iloc[-420:]
    rows = []
    for ts, row in tail.iterrows():
        def px(value):
            value = float(value)
            return round(value, 2 if abs(value) >= 100 else 4)
        rows.append({
            "time": int(pd.Timestamp(ts).timestamp()),
            "open": px(row["open"]),
            "high": px(row["high"]),
            "low": px(row["low"]),
            "close": px(row["close"]),
        })
    return rows


def _snap(markers: list[dict], candles: list[dict]) -> list[dict]:
    if not markers or not candles:
        return []
    times = [c["time"] for c in candles]
    used = set()
    out = []
    for mark in markers:
        if mark["time"] < times[0] or mark["time"] > times[-1]:
            continue
        nearest = min(times, key=lambda item: abs(item - mark["time"]))
        if nearest in used:
            continue
        used.add(nearest)
        copied = dict(mark)
        copied["time"] = nearest
        out.append(copied)
    out.sort(key=lambda item: item["time"])
    return out


def write_report(equity: pd.Series, bench: pd.Series, path: Path, title: str):
    import logging
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["font.family"] = "DejaVu Sans"
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    import quantstats as qs

    def daily(series: pd.Series) -> pd.Series:
        out = series.copy()
        if getattr(out.index, "tz", None) is not None:
            out.index = out.index.tz_convert("UTC").tz_localize(None)
        out = out[~out.index.duplicated(keep="last")].resample("1D").last().dropna()
        rets = out.pct_change().replace([float("inf"), float("-inf")], pd.NA).dropna()
        rets.name = title
        return rets

    path.parent.mkdir(parents=True, exist_ok=True)
    rets = daily(equity)
    bh = daily(bench).reindex(rets.index).fillna(0.0)
    if len(rets) < 30:
        raise RuntimeError("日收益太短，跳过 QuantStats")
    qs.reports.html(rets, benchmark=bh, output=str(path), title=title, download_filename=path.name)


def _rule_job(coin, name, timeframe, df, funding, grid, default, walk: bool):
    if len(df) < 180:
        log(f"skip {coin} {name} bars {len(df)}")
        return None, []
    log(f"sweep {coin} {name} bars {len(df)}")
    result = sweep_scores(df, funding, BAR[name], grid, default, with_full=name in ("中线", "长线", "1小时"))
    walk_sum = None
    if walk:
        log(f"walk-forward {coin} {name}")
        walk_sum = walk_forward_scores(df, funding, BAR[name], grid, default)
    rows = []
    rows.append(_row(
        coin, name, "页面规则", timeframe, "默认",
        _params_text(*default), result["default_train"], result["default_test"],
        None, df, name if name in ("中线", "长线", "1小时") else None,
    ))
    best = result["best"]
    rows.append(_row(
        coin, name, "页面规则", timeframe, "样本内最优",
        _params_text(*best), result["best_train"], result["best_test"],
        walk_sum, df, None,
    ))
    for item in rows:
        item["train_end"] = result["train_end"]
        item["test_start"] = result["test_start"]
    return result, rows


def run_coin(coin: str, fng: pd.DataFrame, now: datetime):
    raw = {}
    for tf in ("15m", "1h", "4h", "1d"):
        raw[tf] = drop_forming(fetch_ohlcv(coin, tf), tf)
    scored = {tf: score_frame(df) for tf, df in raw.items()}
    weekly = _weekly_frame(raw["1d"])
    try:
        funding_df = fetch_funding(coin)
        funding = funding_df["rate"] if len(funding_df) else None
    except Exception as exc:
        log(f"funding {coin} {exc}")
        funding = None
    scalp = build_scalp(scored["15m"], funding)
    mid = build_mid(scored["1h"], scored["4h"], scored["1d"], funding, fng)
    longer = build_long(scored["1d"], weekly, fng)
    rules = {
        "15分钟": rule_frame(scored["15m"]),
        "1小时": rule_frame(scored["1h"]),
        "4小时": rule_frame(scored["4h"]),
        "日线": rule_frame(scored["1d"]),
        "超短线": scalp,
        "中线": mid,
        "长线": longer,
    }
    board = []
    db_rows = []
    views = {}
    full_mid = None
    for name, frame in rules.items():
        grid = HZ_GRID if name in ("超短线", "中线", "长线") else TF_GRID
        default = (2.5, 1.5) if name in ("超短线", "中线", "长线") else (3, 1.5)
        tf_label = {"超短线": "15分钟", "中线": "1小时", "长线": "日线"}.get(name, name)
        walk = name in ("1小时", "中线", "长线")
        result, rows = _rule_job(coin, name, tf_label, frame, funding, grid, default, walk)
        if result is None:
            continue
        for item in rows:
            board.append(item)
            db_rows.append(_db(now, item, "train", item["train"]))
            db_rows.append(_db(now, item, "test", item["test"]))
        if name in ("中线", "长线", "1小时") and result.get("full"):
            views[name] = _view(result["full"])
            if name == "中线":
                full_mid = result["full"]
    classic_rows = []
    classic_full = {}
    for tf_name, hours, source in (("1小时", 1.0, raw["1h"]), ("4小时", 4.0, raw["4h"])):
        for spec in CLASSIC:
            def build_variants(frame, spec=spec):
                return [{"label": p["label"], "pos": spec["build"](frame["close"], p)} for p in spec["params"]]
            variants = build_variants(source)
            log(f"classic {coin} {spec['name']} {tf_name}")
            swept = sweep_positions(source, funding, hours, variants, with_full=(tf_name == "1小时"))
            walk = None
            if tf_name == "1小时":
                walk = walk_forward_positions(source, funding, hours, build_variants)
            item = _row(
                coin, spec["name"], "经典策略", tf_name, "样本内最优",
                swept["label"], swept["best_train"], swept["best_test"],
                walk, source, None,
            )
            item["train_end"] = swept["train_end"]
            item["test_start"] = swept["test_start"]
            classic_rows.append(item)
            board.append(item)
            db_rows.append(_db(now, item, "train", item["train"]))
            db_rows.append(_db(now, item, "test", item["test"]))
            if tf_name == "1小时" and swept.get("full"):
                classic_full[spec["name"]] = swept
    best_classic = None
    if classic_rows:
        pool = [item for item in classic_rows if item["timeframe"] == "1小时"]
        best_classic = max(pool, key=lambda item: item["test"]["sharpe"] if item["test"]["sharpe"] is not None else -1e9)
        held = classic_full.get(best_classic["name"])
        if held and held.get("test_detail"):
            views["经典样本外"] = _view(held["test_detail"])
            views["经典样本外"]["strategy"] = best_classic["name"]
            views["经典样本外"]["params"] = best_classic["params"]
            best_classic["view"] = "经典样本外"
    fng_value = int(fng["value"].iloc[-1]) if fng is not None and len(fng) else None
    latest = current_signals(scored, weekly, funding, fng_value)
    signal_rows = backfill_signals(coin, rules, raw["1h"], days=60)
    candles = _candles(raw["4h"])
    for key, view in views.items():
        view["markers"] = _snap(view.get("markers") or [], candles)
    return {
        "board": board,
        "db_rows": db_rows,
        "views": views,
        "candles": candles,
        "latest": latest,
        "signals": signal_rows,
        "full_mid": full_mid,
        "best_classic": best_classic,
        "classic_detail": classic_full.get(best_classic["name"]) if best_classic else None,
        "funding_points": 0 if funding is None else int(len(funding)),
        "price": float(raw["1h"]["close"].iloc[-1]),
    }


def main():
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    try:
        fng = fetch_fng()
        log(f"fng {len(fng)}")
    except Exception as exc:
        log(f"fng failed {exc}")
        fng = pd.DataFrame(columns=["value"])
    payload = {
        "generated": now.isoformat(),
        "fee": FEE_NOTE(),
        "dropped_factors": DROPPED,
        "notes": NOTES(),
        "leaderboard": [],
        "best_classic": {},
        "latest": {},
        "reports": [],
        "allocation": None,
    }
    charts = {"generated": now.isoformat(), "coins": {}}
    db_rows = []
    signal_rows = []
    daily_close = {}
    for coin in ("BTC", "ETH"):
        log(f"=== {coin} ===")
        result = run_coin(coin, fng, now)
        payload["leaderboard"].extend(result["board"])
        db_rows.extend(result["db_rows"])
        signal_rows.extend(result["signals"])
        charts["coins"][coin] = {"candles": result["candles"], "views": result["views"]}
        payload["latest"][coin] = {
            "price": result["price"],
            "funding_points": result["funding_points"],
            "horizons": result["latest"],
        }
        if result["best_classic"]:
            item = result["best_classic"]
            payload["best_classic"][coin] = {
                "name": item["name"],
                "timeframe": item["timeframe"],
                "params": item["params"],
                "train": item["train"],
                "test": item["test"],
                "overfit": item["overfit"],
            }
        daily = drop_forming(fetch_ohlcv(coin, "1d"), "1d")
        daily_close[coin] = daily["close"]
        if result["full_mid"]:
            path = REPORTS / f"{coin.lower()}_mid_default.html"
            try:
                write_report(result["full_mid"]["equity"], result["full_mid"]["bench"], path, f"{coin} mid default")
                payload["reports"].append({"coin": coin, "title": f"{coin} 中线默认规则", "href": f"reports/{path.name}"})
                log(f"report {path.name}")
            except Exception as exc:
                log(f"quantstats mid {coin} {exc}")
        detail = result.get("classic_detail") or {}
        if detail.get("test_detail"):
            path = REPORTS / f"{coin.lower()}_classic_oos.html"
            try:
                write_report(detail["test_detail"]["equity"], detail["test_detail"]["bench"], path, f"{coin} classic oos")
                title = f"{coin} 经典策略样本外（{result['best_classic']['name']}）"
                payload["reports"].append({"coin": coin, "title": title, "href": f"reports/{path.name}"})
                log(f"report {path.name}")
            except Exception as exc:
                log(f"quantstats classic {coin} {exc}")
    try:
        payload["allocation"] = analyze(daily_close["BTC"], daily_close["ETH"])
    except Exception as exc:
        log(f"allocation {exc}")
        payload["allocation"] = {"error": str(exc)[:300]}
    if payload["latest"]:
        fng_now = None if not len(fng) else int(fng["value"].iloc[-1])
        for coin, block in payload["latest"].items():
            block["fng"] = fng_now
    SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    CHARTS.write_text(json.dumps(charts, ensure_ascii=False), encoding="utf-8")
    from analysis.store import LATEST
    LATEST.write_text(json.dumps({"generated": now.isoformat(), "coins": payload["latest"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        insert_signals(signal_rows)
        from analysis.store import export_frontend, update_outcomes
        update_outcomes()
        export_frontend()
    except Exception as exc:
        log(f"duckdb signals {exc}")
        raise
    try:
        insert_backtests(db_rows)
    except Exception as exc:
        log(f"duckdb backtests {exc}")
    log(f"wrote {SUMMARY}")
    try:
        from analysis.run_exits import main as exit_main
        exit_main()
    except Exception as exc:
        log(f"出场与杠杆模拟失败 {exc}")
        raise
    try:
        from analysis.run_search import main as search_main
        search_main()
    except Exception as exc:
        log(f"策略搜索失败 {exc}")
        raise


def FEE_NOTE():
    return "OKX 永续吃单 0.05% / 边。有资金费率历史时，按持仓方向和 K 线占 8 小时的比例计入。"


def NOTES():
    return [
        "单周期分数与实时看板 js/indicators.js 的 scoreTf 相同，任务里会用 Node 对同一段 K 线做对照。",
        "超短线、中线、长线用的是页面 horizonScalp、overallCall、horizonLong。没有长历史的因子不参与，剩下的权重按页面的办法重新归一。",
        "没有计入的因子：新闻、清算、盘口、1 分钟动量、5 分钟技术、美股影响、入场清单、见底见顶、多空人数比。恐惧贪婪用 alternative.me 的历史，并滞后一天。",
        "支撑阻力只用摆动高低点。页面上还有枢轴、成交密集和整数关口，回测没有逐根重算。",
        "信号在上一根收盘后才用，成交按下一根开盘。页面规则默认阈值：单周期 ±3，三个持仓周期 ±2.5，止损 1.5 倍 ATR。",
        "参数只在前 70% K 线里按夏普挑选，后 30% 留作样本外。走步检验把样本切成 4 段，每次只用前面的数据重选参数，再看下一段。样本内更好看，通常就是过拟合。",
        "经典策略（EMA 交叉、RSI 均值回归、布林突破、MACD）只作为对照，同样收手续费和资金费，不用 ATR 止损。",
        "买入持有是同期价格涨跌，不含手续费和资金费。策略净值从 1 起算，已经扣过这两项。",
        "减仓和平仓是出场研究的分类，不是让你减掉多少，也不是开仓建议。杠杆模拟只是把固定规则套在历史上，不推荐任何倍数。",
        "这是历史研究，不构成投资建议，也不提供仓位或杠杆建议。过去的收益不代表以后。",
    ]


if __name__ == "__main__":
    main()
