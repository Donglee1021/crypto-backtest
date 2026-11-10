"""模拟盘。只用公开行情记账，不下单，不使用 API 密钥。

超短线：1 分钟、5 分钟、15 分钟方向一致，并且和 1 小时同向，才记账。
止损、减仓和止盈按 15 分钟 ATR，是模拟参数，不是建议。
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from analysis.market import fetch_funding
from analysis.scalp import (
    COOLDOWN_MIN,
    DAY_LOSS,
    STOP_MULT,
    TARGET1_MULT,
    TARGET2_MULT,
    apply_exit,
    latest,
    side_of,
)

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
    stop_dist = max(atr_v * STOP_MULT, price * 0.001)
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
        "target1": round(fill + side * atr_v * TARGET1_MULT, 2 if fill >= 100 else 4),
        "target2": round(fill + side * atr_v * TARGET2_MULT, 2 if fill >= 100 else 4),
        "reduced": False,
        "realized": round(-fee, 4),
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
    total = pnl + float(pos.get("realized") or 0)
    state["day_pnl"] = float(state.get("day_pnl") or 0) + pnl
    state["trades"] = int(state["trades"]) + 1
    if total >= 0:
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
        "pnl": round(total, 2),
        "reason": pos.get("reason") or "",
    }
    return total


def reduce_position(state: dict, pos: dict, price: float) -> float:
    part = pos["qty"] * 0.5
    side = pos["side"]
    fill = price * (1 - side * SLIP)
    gross = side * part * (fill - pos["entry"])
    fee = part * fill * FEE
    pnl = gross - fee
    state["cash"] = float(state["cash"]) + pnl
    state["day_pnl"] = float(state.get("day_pnl") or 0) + pnl
    pos["realized"] = float(pos.get("realized") or 0) + pnl
    pos["qty"] = pos["qty"] - part
    pos["reduced"] = True
    pos["stop"] = pos["entry"]
    pos["notional"] = round(pos["qty"] * fill, 2)
    return pnl


def backtest_public():
    path = ROOT / "data" / "paper" / "scalp_backtest.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    brief = {"days": data.get("days"), "note": data.get("note"), "coins": {}}
    for coin, stats in (data.get("coins") or {}).items():
        brief["coins"][coin] = {
            "trades": stats.get("trades"),
            "win_rate": None if stats.get("win_rate") is None else round(stats["win_rate"], 4),
            "return": round(stats.get("return") or 0, 4),
            "max_drawdown": round(stats.get("max_drawdown") or 0, 4),
        }
    return brief


def run() -> dict:
    stamp = now_vienna()
    state = load_state(stamp)
    state.setdefault("cooldown", {})
    marks = {}
    calls = {}
    flow = "scan"
    candidate_label = "没有候选"
    decisions = []
    equity_now = mark_equity(state, {})
    day_key = stamp.date().isoformat()
    if state.get("day_key") != day_key:
        state["day_key"] = day_key
        state["day_pnl"] = 0.0
        state["day_equity"] = equity_now
    for coin in COINS:
        try:
            view = latest(coin)
        except Exception as exc:
            print("latest", coin, exc, flush=True)
            decisions.append({"coin": coin, "action": "跳过", "reason": "行情没取到", "price": None, "candidate": 0, "blocks": []})
            append_log(state, stamp, coin, "跳过", "行情没取到", mark_equity(state, marks), None)
            continue
        intra = view["intraday"]
        hour = view["hour"]
        price = float(intra.get("price") or hour.get("price") or 0)
        marks[coin] = price
        vote = side_of(intra["call"])
        hside = side_of(hour["call"])
        calls[coin] = {
            "intraday": {"call": intra["call"], "score": intra["score"], "reasons": intra["reasons"]},
            "hour": {"call": hour["call"], "score": hour["score"], "reasons": hour["reasons"]},
        }
        pos = (state.get("positions") or {}).get(coin)
        action = "跳过"
        reason = ""
        blocks = []
        candidate = vote if vote and vote == hside else 0
        if pos:
            m5 = view["frames"]["5m"]
            opened = datetime.fromisoformat(pos["opened_at"])
            for ts, bar in m5.iterrows():
                bar_ts = ts.to_pydatetime()
                if bar_ts.tzinfo is None:
                    bar_ts = bar_ts.replace(tzinfo=VIENNA)
                event = apply_exit(pos, float(bar["high"]), float(bar["low"]), float(bar["close"]), bar_ts, vote, hside)
                if not event:
                    continue
                if event["action"] == "减仓":
                    pnl = reduce_position(state, pos, event["fill"])
                    action = "减仓"
                    reason = f"{event['reason']}，这一笔 {pnl:+.2f} USDT"
                    event = apply_exit(pos, float(bar["high"]), float(bar["low"]), float(bar["close"]), bar_ts, vote, hside)
                    if not event:
                        continue
                if event["action"] == "平仓":
                    pnl = close_position(state, coin, pos, event["fill"])
                    state["positions"].pop(coin, None)
                    pos = None
                    action = "平仓"
                    reason = f"{event['reason']}，模拟盈亏 {pnl:+.2f} USDT"
                    candidate_label = f"{coin} 平仓"
                    flow = "exec"
                    if "止损" in event["reason"]:
                        until = bar_ts + timedelta(minutes=COOLDOWN_MIN)
                        state["cooldown"][coin] = until.isoformat()
                    break
            if pos:
                if action != "减仓":
                    action = "跳过"
                    reason = "持仓还在。日内是%s，1 小时是%s" % (intra["call"], hour["call"])
                candidate_label = f"{coin} {side_name(pos['side'])}"
                flow = "log"
        else:
            blocks = []
            notes = []
            if vote and hside and vote != hside:
                blocks.append(f"日内是{intra['call']}，1 小时是{hour['call']}，方向相反")
            elif vote == 0 or hside == 0:
                notes.append(f"日内是{intra['call']}，1 小时是{hour['call']}")
            rate = latest_funding(coin)
            if candidate and rate is not None:
                if candidate > 0 and rate > 0.0005:
                    blocks.append("资金费率过热，多头拥挤")
                elif candidate < 0 and rate < -0.0005:
                    blocks.append("资金费率过冷，空头拥挤")
            elif rate is None:
                notes.append("资金费率暂时没取到，这项不拦截")
            if candidate and price > 0 and intra.get("atr", 0) / price > 0.02:
                blocks.append("15 分钟波动太大")
            liq = liquidation_cluster(coin, price) if price else "unknown"
            if candidate and liq == "cluster":
                blocks.append("附近清算单扎堆")
            elif liq == "unknown":
                notes.append("清算簇没有拉到，这项不拦截")
            day_equity = float(state.get("day_equity") or equity_now or START)
            if candidate and day_equity > 0 and float(state.get("day_pnl") or 0) <= -DAY_LOSS * day_equity:
                blocks.append("今天模拟亏损已经到 3%，暂停开新仓")
            cool = state.get("cooldown", {}).get(coin)
            if candidate and cool:
                try:
                    if stamp < datetime.fromisoformat(cool):
                        blocks.append("止损后还在冷却")
                except Exception:
                    pass
            if candidate and not blocks:
                if state["seen_bars"].get(coin) == intra.get("bar"):
                    action = "跳过"
                    reason = "这根 15 分钟已经看过，不重复开仓"
                else:
                    new_pos = open_position(
                        state, coin, candidate, price, intra["atr"],
                        f"日内与 1 小时同为{side_name(candidate)}",
                    )
                    new_pos["opened_at"] = stamp.isoformat()
                    new_pos["bar"] = intra.get("bar")
                    state["positions"][coin] = new_pos
                    state["seen_bars"][coin] = intra.get("bar")
                    action = "开多" if candidate > 0 else "开空"
                    reason = f"模拟名义 {new_pos['notional']:.0f} USDT，止损 {new_pos['stop']}，第一目标 {new_pos['target1']}"
                    flow = "exec"
                    candidate_label = f"{coin} {side_name(candidate)}"
            elif blocks:
                action = "过滤"
                reason = "；".join(blocks)
                flow = "risk"
                candidate_label = f"{coin} 日内{intra['call']} / 1小时{hour['call']}"
            else:
                action = "跳过"
                reason = "；".join(notes) or "没有同时满足的进场信号"
                flow = "scan"
                candidate_label = f"{coin} 日内{intra['call']} / 1小时{hour['call']}"
        if action == "过滤":
            state["seen_bars"][coin] = intra.get("bar")
        decisions.append({
            "coin": coin,
            "action": action,
            "reason": reason,
            "price": price,
            "candidate": candidate,
            "blocks": blocks if not pos else [],
        })
        equity_now = mark_equity(state, marks)
        state["equity"] = equity_now
        append_log(state, stamp, coin, action, reason, equity_now, price)

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
            "target1": pos.get("target1"),
            "reduced": bool(pos.get("reduced")),
            "qty": pos["qty"],
            "notional": pos["notional"],
            "fee_open": pos.get("fee_open"),
            "slippage": SLIP,
            "reason": pos.get("reason"),
            "upnl": round(upnl, 2),
            "mark": px,
        })
    acted = ("开多", "开空", "减仓", "平仓")
    focus = next((d for d in decisions if d["action"] in acted or d["action"] == "过滤"), decisions[0] if decisions else None)
    if focus and focus["action"] == "过滤":
        flow = "risk"
    elif focus and focus["action"] in acted:
        flow = "exec"
    elif open_positions:
        flow = "equity"
    agents = {
        "scanner": {
            "title": "扫描",
            "en": "SCANNER",
            "line": "看 1 分钟、5 分钟、15 分钟是否和 1 小时同一个方向。",
            "badge": "有候选" if any(d["candidate"] for d in decisions) else "无信号",
            "tone": "run" if any(d["candidate"] for d in decisions) else "idle",
        },
        "risk": {
            "title": "风控检查",
            "en": "RISK CHECK",
            "line": "日内和 1 小时相反、费率极端、当天亏到 3%、止损冷却或清算扎堆，就拦住。",
            "badge": "拦截" if any(d["action"] == "过滤" for d in decisions) else "放行",
            "tone": "block" if any(d["action"] == "过滤" for d in decisions) else "ok",
        },
        "sizer": {
            "title": "仓位",
            "en": "SIZER",
            "line": "模拟参数：1.5 倍 15 分钟 ATR 为止损，第一目标减半，第二目标平仓。名义不超过权益的 20%。不是建议。",
            "badge": "已计算" if any(d["action"] in ("开多", "开空") for d in decisions) else "空闲",
            "tone": "run" if any(d["action"] in ("开多", "开空") for d in decisions) else "idle",
        },
        "executor": {
            "title": "执行",
            "en": "EXECUTOR",
            "line": "只在账本上开仓、减仓和平仓，扣 0.05% 费用和 0.02% 滑点。最长拿 6 小时。",
            "badge": "有成交" if any(d["action"] in acted for d in decisions) else "无动作",
            "tone": "run" if any(d["action"] in acted for d in decisions) else "idle",
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
        "calls": calls,
        "strategy": "超短线",
        "backtest": backtest_public(),
        "marks": marks,
        "agents": agents,
        "flow_step": flow,
        "candidate": candidate_label,
        "last_closed": state.get("last_closed"),
        "equity_curve": state["equity_curve"],
        "log": state["log"],
        "honesty": "这是超短线模拟记账，不是建议。方向不一致、当天亏到 3% 或止损冷却时不会开新仓。",
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
        "calls": calls,
        "cooldown": state.get("cooldown") or {},
        "day_key": state.get("day_key"),
        "day_pnl": state.get("day_pnl") or 0,
        "day_equity": state.get("day_equity"),
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
