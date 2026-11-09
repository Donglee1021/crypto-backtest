"""模拟盘。只用公开行情记账，不下单，不使用 API 密钥。

扫描用搜索里相对稳一点的两套：日线 SMA20/50 加 3 倍 ATR 跟踪，以及约 12 周时序动量。
两套方向一致才考虑进场。风控不通过就只写一条原因。仓位比例是模拟参数，不是建议。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

from analysis.market import drop_forming, fetch_funding, fetch_ohlcv
from analysis.scoring import atr
from analysis.search import apply_trail, ma_pos, tsmom_pos

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "paper" / "state.json"
DB_PATH = ROOT / "data" / "paper.duckdb"
VIENNA = ZoneInfo("Europe/Vienna")
START = 10_000.0
FEE = 0.0005
SLIP = 0.0002
# 模拟参数：单笔亏损预算是权益的 1%，名义价值不超过权益的 20%。不是仓位建议。
RISK_FRAC = 0.01
MAX_NOTIONAL_FRAC = 0.20
COINS = ("BTC", "ETH")


def now_vienna() -> datetime:
    return datetime.now(VIENNA).replace(microsecond=0)


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS paper_log (
            ts TIMESTAMP,
            coin VARCHAR,
            action VARCHAR,
            reason VARCHAR,
            equity DOUBLE,
            price DOUBLE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS paper_state (
            id INTEGER PRIMARY KEY,
            payload VARCHAR
        )
    """)
    return con


def blank_state(stamp: datetime) -> dict:
    return {
        "started_at": stamp.isoformat(),
        "cash": START,
        "equity": START,
        "wins": 0,
        "losses": 0,
        "trades": 0,
        "positions": {},
        "last_closed": None,
        "equity_curve": [{"time": int(stamp.timestamp()), "value": START}],
        "log": [],
        "seen_bars": {},
    }


def load_state(stamp: datetime) -> dict:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            data = data.get("state") or data
            data.setdefault("positions", {})
            data.setdefault("log", [])
            data.setdefault("equity_curve", [])
            data.setdefault("seen_bars", {})
            data.setdefault("cash", START)
            return data
        except Exception:
            pass
    return blank_state(stamp)


def side_name(side: int) -> str:
    if side > 0:
        return "做多"
    if side < 0:
        return "做空"
    return "空仓"


def liquidation_cluster(coin: str, price: float) -> str:
    """返回 cluster / clear / unknown。只读 OKX 公共强平列表。"""
    uly = f"{coin}-USDT"
    url = f"https://www.okx.com/api/v5/public/liquidation-orders?instType=SWAP&uly={uly}&state=filled"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "crypto-backtest"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except Exception:
        return "unknown"
    details = []
    for block in payload.get("data") or []:
        details.extend(block.get("details") or [])
    if not details or price <= 0:
        return "unknown"
    near = 0.0
    for item in details:
        try:
            px = float(item.get("bkPx") or 0)
            sz = float(item.get("sz") or 0)
        except (TypeError, ValueError):
            continue
        if px <= 0:
            continue
        if abs(px - price) / price <= 0.01:
            near += sz
    # 返回的是最近一小批单。附近张数明显堆在一起才拦截。
    limit = 8.0 if coin == "BTC" else 80.0
    return "cluster" if near >= limit else "clear"


def latest_funding(coin: str) -> float | None:
    try:
        hist = fetch_funding(coin, days=14)
    except Exception:
        return None
    if hist is None or not len(hist):
        return None
    return float(hist["rate"].iloc[-1])


def signals(coin: str) -> dict:
    daily = drop_forming(fetch_ohlcv(coin, "1d"), "1d")
    h4 = drop_forming(fetch_ohlcv(coin, "4h"), "4h")
    close = daily["close"].to_numpy(float)
    high = daily["high"].to_numpy(float)
    low = daily["low"].to_numpy(float)
    ma = apply_trail(high, low, close, ma_pos(close, 20, 50, "sma"), atr(high, low, close, 14), 3)
    mom = tsmom_pos(close, 84, False)
    h4c = h4["close"].to_numpy(float)
    h4_ma = ma_pos(h4c, 20, 50, "sma")
    atr_v = atr(high, low, close, 14)
    return {
        "bar": daily.index[-1].isoformat(),
        "price": float(close[-1]),
        "atr": float(atr_v[-1]) if np.isfinite(atr_v[-1]) else float(close[-1]) * 0.02,
        "ma": int(ma[-1]),
        "mom": int(mom[-1]),
        "h4": int(h4_ma[-1]),
        "low": float(low[-1]),
        "high": float(high[-1]),
    }


def mark_equity(state: dict, marks: dict[str, float]) -> float:
    equity = float(state["cash"])
    for coin, pos in (state.get("positions") or {}).items():
        if not pos:
            continue
        px = marks.get(coin, pos["entry"])
        equity += pos["side"] * pos["qty"] * (px - pos["entry"])
    return equity


def append_log(state: dict, stamp: datetime, coin: str, action: str, reason: str, equity: float, price: float | None):
    line = {
        "ts": stamp.isoformat(),
        "coin": coin,
        "action": action,
        "reason": reason,
        "text": f"{stamp.strftime('%m-%d %H:%M:%S')}  {coin}  {action}  原因：{reason}",
    }
    state["log"].append(line)
    state["log"] = state["log"][-80:]
    try:
        con = connect()
        con.execute(
            "INSERT INTO paper_log VALUES (?, ?, ?, ?, ?, ?)",
            [stamp.replace(tzinfo=None), coin, action, reason, equity, price],
        )
        con.execute("DELETE FROM paper_log WHERE ts < now() - INTERVAL 30 DAY")
        con.close()
    except Exception as exc:
        print("duckdb log", exc, flush=True)


def open_position(state: dict, coin: str, side: int, price: float, atr_v: float, reason: str) -> dict:
    equity = mark_equity(state, {coin: price})
    stop_dist = max(atr_v * 3, price * 0.005)
    risk_cash = equity * RISK_FRAC
    qty = risk_cash / stop_dist
    notional = qty * price
    cap = equity * MAX_NOTIONAL_FRAC
    if notional > cap:
        notional = cap
        qty = notional / price
    fill = price * (1 + side * SLIP)
    fee = qty * fill * FEE
    state["cash"] = float(state["cash"]) - fee
    stop = fill - side * stop_dist
    pos = {
        "side": side,
        "entry": round(fill, 2 if fill >= 100 else 4),
        "qty": qty,
        "notional": round(notional, 2),
        "stop": round(stop, 2 if fill >= 100 else 4),
        "opened_at": reason and None,
        "fee_open": round(fee, 4),
        "reason": reason,
    }
    return pos


def close_position(state: dict, coin: str, pos: dict, price: float) -> float:
    side = pos["side"]
    fill = price * (1 - side * SLIP)
    gross = side * pos["qty"] * (fill - pos["entry"])
    fee = pos["qty"] * fill * FEE
    pnl = gross - fee
    state["cash"] = float(state["cash"]) + pnl
    state["trades"] = int(state["trades"]) + 1
    if pnl >= 0:
        state["wins"] = int(state["wins"]) + 1
    else:
        state["losses"] = int(state["losses"]) + 1
    state["last_closed"] = {
        "coin": coin,
        "side": side_name(side),
        "entry": pos["entry"],
        "exit": round(fill, 2 if fill >= 100 else 4),
        "qty": pos["qty"],
        "notional": pos["notional"],
        "pnl": round(pnl, 2),
        "reason": pos.get("reason") or "",
    }
    return pnl


def run() -> dict:
    stamp = now_vienna()
    state = load_state(stamp)
    marks = {}
    flow = "scan"
    candidate_label = "没有候选"
    decisions = []
    for coin in COINS:
        info = signals(coin)
        marks[coin] = info["price"]
        ma_side, mom_side = info["ma"], info["mom"]
        if ma_side != 0 and ma_side == mom_side:
            candidate = ma_side
        else:
            candidate = 0
        rate = latest_funding(coin)
        blocks = []
        notes = []
        if candidate == 0:
            if ma_side == 0 and mom_side == 0:
                notes.append("均线和 12 周动量都没有方向")
            else:
                blocks.append(f"均线是{side_name(ma_side)}，动量是{side_name(mom_side)}，方向相反")
        if candidate and info["h4"] != 0 and info["h4"] != candidate:
            blocks.append(f"4 小时均线是{side_name(info['h4'])}，和日线不一致")
        if candidate and rate is not None:
            if candidate > 0 and rate > 0.0003:
                blocks.append("资金费率过热，多头拥挤")
            elif candidate < 0 and rate < -0.0003:
                blocks.append("资金费率过冷，空头拥挤")
        elif rate is None:
            notes.append("资金费率暂时没取到，这项不拦截")
        if candidate and info["atr"] / info["price"] > 0.06:
            blocks.append("日线波动太大")
        liq = liquidation_cluster(coin, info["price"])
        if candidate and liq == "cluster":
            blocks.append("附近清算单扎堆")
        elif liq == "unknown":
            notes.append("清算簇没有拉到，这项不拦截")
        pos = (state.get("positions") or {}).get(coin)
        action = "跳过"
        reason = ""
        trail_hit = False
        if pos:
            if pos["side"] > 0 and info["low"] <= pos["stop"]:
                trail_hit = True
            elif pos["side"] < 0 and info["high"] >= pos["stop"]:
                trail_hit = True
        if pos and (trail_hit or (candidate != 0 and candidate != pos["side"]) or (ma_side == 0 and mom_side == 0)):
            why = "碰到 ATR 跟踪止损" if trail_hit else "两套方案不再支持原来的方向"
            pnl = close_position(state, coin, pos, info["price"])
            state["positions"].pop(coin, None)
            action = "平仓"
            reason = f"{why}，模拟盈亏 {pnl:+.2f} USDT"
            flow = "exec"
            candidate_label = f"{coin} 平仓"
        elif pos:
            action = "跳过"
            reason = "持仓还在，信号没有反过来"
            notes_txt = "；".join(notes)
            if notes_txt:
                reason += "。" + notes_txt
            flow = "log"
            candidate_label = f"{coin} {side_name(pos['side'])}"
        elif candidate and blocks:
            action = "过滤"
            reason = "；".join(blocks)
            flow = "risk"
            candidate_label = f"{coin} {side_name(candidate)}"
        elif candidate and not blocks:
            if state["seen_bars"].get(coin) == info["bar"] and not pos:
                action = "跳过"
                reason = "这根日线已经看过，不重复开仓"
            else:
                new_pos = open_position(
                    state, coin, candidate, info["price"], info["atr"],
                    f"日线均线与 12 周动量同为{side_name(candidate)}",
                )
                new_pos["opened_at"] = stamp.isoformat()
                new_pos["bar"] = info["bar"]
                state["positions"][coin] = new_pos
                state["seen_bars"][coin] = info["bar"]
                action = "开仓"
                reason = f"{side_name(candidate)}，模拟名义 {new_pos['notional']:.0f} USDT，止损价 {new_pos['stop']}"
                flow = "exec"
                candidate_label = f"{coin} {side_name(candidate)}"
        else:
            action = "过滤" if blocks else "跳过"
            reason = "；".join(blocks + notes) or "没有一致的进场信号"
            flow = "risk" if blocks else "scan"
            if ma_side or mom_side:
                candidate_label = f"{coin} 均线{side_name(ma_side)} / 动量{side_name(mom_side)}"
        if action == "过滤":
            state["seen_bars"][coin] = info["bar"]
        decisions.append({"coin": coin, "action": action, "reason": reason, "price": info["price"], "candidate": candidate, "info": info, "blocks": blocks})
        equity_now = mark_equity(state, marks)
        state["equity"] = equity_now
        append_log(state, stamp, coin, action, reason, equity_now, info["price"])

    equity_now = mark_equity(state, marks)
    state["equity"] = round(equity_now, 2)
    state["cash"] = round(float(state["cash"]), 2)
    state["equity_curve"].append({"time": int(stamp.timestamp()), "value": state["equity"]})
    state["equity_curve"] = state["equity_curve"][-400:]
    open_positions = []
    for coin, pos in state["positions"].items():
        px = marks.get(coin, pos["entry"])
        upnl = pos["side"] * pos["qty"] * (px - pos["entry"])
        open_positions.append({
            "coin": coin,
            "side": side_name(pos["side"]),
            "side_sign": pos["side"],
            "entry": pos["entry"],
            "stop": pos["stop"],
            "qty": pos["qty"],
            "notional": pos["notional"],
            "fee_open": pos.get("fee_open"),
            "slippage": SLIP,
            "reason": pos.get("reason"),
            "upnl": round(upnl, 2),
            "mark": px,
        })
    focus = next((d for d in decisions if d["action"] in ("开仓", "平仓", "过滤")), decisions[0] if decisions else None)
    if focus and focus["action"] == "过滤":
        flow = "risk"
    elif focus and focus["action"] in ("开仓", "平仓"):
        flow = "exec"
    elif open_positions:
        flow = "equity"
    agents = {
        "scanner": {
            "title": "扫描",
            "en": "SCANNER",
            "line": "看 BTC 和 ETH 的日线均线、ATR 跟踪和 12 周动量。",
            "badge": "有候选" if any(d["candidate"] for d in decisions) else "无信号",
            "tone": "run" if any(d["candidate"] for d in decisions) else "idle",
        },
        "risk": {
            "title": "风控检查",
            "en": "RISK CHECK",
            "line": "费率极端、4 小时方向相反、波动过大或附近清算扎堆，就拦住。",
            "badge": "拦截" if any(d["action"] == "过滤" for d in decisions) else "放行",
            "tone": "block" if any(d["action"] == "过滤" for d in decisions) else "ok",
        },
        "sizer": {
            "title": "仓位",
            "en": "SIZER",
            "line": "模拟参数：按 1% 模拟权益对 3 倍 ATR 止损来算张数，名义不超过权益的 20%。不是建议。",
            "badge": "已计算" if any(d["action"] == "开仓" for d in decisions) else "空闲",
            "tone": "run" if any(d["action"] == "开仓" for d in decisions) else "idle",
        },
        "executor": {
            "title": "执行",
            "en": "EXECUTOR",
            "line": "只在账本上开平仓，扣 0.05% 吃单费和 0.02% 滑点。",
            "badge": "有成交" if any(d["action"] in ("开仓", "平仓") for d in decisions) else "无动作",
            "tone": "run" if any(d["action"] in ("开仓", "平仓") for d in decisions) else "idle",
        },
        "logger": {
            "title": "记录",
            "en": "LOGGER",
            "line": "每条决定都写下时间和原因。",
            "badge": "已写入",
            "tone": "ok",
        },
        "equity": {
            "title": "资金",
            "en": "EQUITY",
            "line": "模拟权益 = 现金 + 持仓按最新收盘价算的浮动盈亏。",
            "badge": f"{state['equity']:.0f} USDT",
            "tone": "ok" if state["equity"] >= START else "block",
        },
    }
    payload = {
        "generated": stamp.isoformat(),
        "timezone": "Europe/Vienna",
        "started_at": state["started_at"],
        "cash": state["cash"],
        "equity": state["equity"],
        "start_equity": START,
        "wins": state["wins"],
        "losses": state["losses"],
        "trades": state["trades"],
        "positions": open_positions,
        "marks": marks,
        "agents": agents,
        "flow_step": flow,
        "candidate": candidate_label,
        "last_closed": state.get("last_closed"),
        "equity_curve": state["equity_curve"],
        "log": state["log"],
        "honesty": "过去的回测没有在扣完成本后同时好过两个币的买入持有。这里只是模拟记账。",
        "fee": FEE,
        "slippage": SLIP,
    }
    # 持久字段写回，页面字段单独导出。
    persisted = {
        "started_at": state["started_at"],
        "cash": state["cash"],
        "equity": state["equity"],
        "wins": state["wins"],
        "losses": state["losses"],
        "trades": state["trades"],
        "positions": state["positions"],
        "last_closed": state.get("last_closed"),
        "equity_curve": state["equity_curve"],
        "log": state["log"],
        "seen_bars": state["seen_bars"],
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({**payload, "_state": persisted}, ensure_ascii=False), encoding="utf-8")
    # 页面不需要内部账本细节里的 qty 精度以外的东西，但持仓要给前端算浮动盈亏。
    public = dict(payload)
    public["positions"] = open_positions
    STATE_PATH.write_text(json.dumps({"public": public, "state": persisted}, ensure_ascii=False), encoding="utf-8")
    try:
        con = connect()
        con.execute("DELETE FROM paper_state")
        con.execute("INSERT INTO paper_state VALUES (1, ?)", [json.dumps(persisted, ensure_ascii=False)])
        con.close()
    except Exception as exc:
        print("duckdb state", exc, flush=True)
    print("paper equity", public["equity"], "trades", public["trades"], "log", public["log"][-1]["text"] if public["log"] else "", flush=True)
    return public


if __name__ == "__main__":
    run()
