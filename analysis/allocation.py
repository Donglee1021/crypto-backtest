"""BTC / ETH / 稳定币的历史配置。只说明过去的权重，不是仓位建议，也不涉及杠杆。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _metrics(pred) -> dict:
    def grab(name, digits):
        value = getattr(pred, name, None)
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            return None
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        if name == "max_drawdown" and value > 0:
            value = -value
        return round(value, digits)
    return {
        "total_return": grab("total_return", 4) if hasattr(pred, "total_return") else None,
        "annualized_return": grab("annualized_mean", 4),
        "annualized_vol": grab("annualized_standard_deviation", 4),
        "sharpe": grab("annualized_sharpe_ratio", 3),
        "max_drawdown": grab("max_drawdown", 4),
    }


def _curve(pred) -> list[dict]:
    rets = getattr(pred, "returns_df", None)
    if rets is None:
        rets = getattr(pred, "returns", None)
    if rets is None:
        return []
    try:
        series = pd.Series(rets).dropna()
    except Exception:
        return []
    if not len(series):
        return []
    equity = (1.0 + series.astype(float)).cumprod()
    if len(equity) > 220:
        pos = [min(len(equity) - 1, int(i * (len(equity) - 1) / 219)) for i in range(220)]
        equity = equity.iloc[pos]
    rows = []
    seen = set()
    for ts, val in equity.items():
        stamp = int(pd.Timestamp(ts).timestamp())
        if stamp in seen:
            continue
        seen.add(stamp)
        rows.append({"time": stamp, "value": round(float(val), 4)})
    return rows


def analyze(btc_daily: pd.Series, eth_daily: pd.Series) -> dict:
    from skfolio import RiskMeasure
    from skfolio.moments import ShrunkCovariance
    from skfolio.optimization import EqualWeighted, MeanRisk, RiskBudgeting
    from skfolio.preprocessing import prices_to_returns
    from skfolio.prior import EmpiricalPrior

    prices = pd.concat([
        btc_daily.rename("BTC"),
        eth_daily.rename("ETH"),
    ], axis=1).dropna()
    prices["稳定币"] = 1.0
    returns = prices_to_returns(prices).dropna()
    if len(returns) < 80:
        return {"error": "日线样本不够，没有做配置计算。"}
    cut = int(len(returns) * 0.7)
    train, test = returns.iloc[:cut], returns.iloc[cut:]
    prior = EmpiricalPrior(covariance_estimator=ShrunkCovariance())
    specs = [
        ("最小方差（含稳定币）", MeanRisk(
            risk_measure=RiskMeasure.VARIANCE, prior_estimator=prior,
            transaction_costs=0.0005, min_weights=0.0,
        )),
        ("风险平价（含稳定币）", RiskBudgeting(
            risk_measure=RiskMeasure.VARIANCE, prior_estimator=prior,
            transaction_costs=0.0005, min_weights=0.0,
        )),
        ("等权（含稳定币）", EqualWeighted()),
    ]
    # 只在 BTC 和 ETH 之间分配，方便看两者的相对权重。
    risky = returns[["BTC", "ETH"]]
    risky_train, risky_test = risky.iloc[:cut], risky.iloc[cut:]
    specs_risky = [
        ("最小方差（仅 BTC/ETH）", MeanRisk(
            risk_measure=RiskMeasure.VARIANCE,
            prior_estimator=EmpiricalPrior(covariance_estimator=ShrunkCovariance()),
            transaction_costs=0.0005, min_weights=0.0,
        )),
        ("风险平价（仅 BTC/ETH）", RiskBudgeting(
            risk_measure=RiskMeasure.VARIANCE,
            prior_estimator=EmpiricalPrior(covariance_estimator=ShrunkCovariance()),
            transaction_costs=0.0005, min_weights=0.0,
        )),
        ("等权（仅 BTC/ETH）", EqualWeighted()),
    ]
    portfolios = []
    portfolios.extend(_fit_all(specs, train, test, list(returns.columns)))
    portfolios.extend(_fit_all(specs_risky, risky_train, risky_test, ["BTC", "ETH"]))
    return {
        "start": str(returns.index[0].date()),
        "end": str(returns.index[-1].date()),
        "split": str(train.index[-1].date()),
        "note": "权重来自 skfolio，在前 70% 日收益上估计，后 30% 只用来看这条历史路径。稳定币用恒定为 1 的价格表示，收益按 0 计算，所以最小方差会把大部分权重放在它上面。这不是把资金换成稳定币的建议，也不涉及杠杆。最小方差和风险平价按 OKX 吃单 0.05% 计一次换仓成本。这一版 skfolio 的等权接口不接收手续费，等权曲线没有扣这笔成本。",
        "portfolios": portfolios,
    }


def _fit_all(specs, train, test, names: list[str]) -> list[dict]:
    rows = []
    for name, model in specs:
        model.fit(train)
        weights = {asset: round(float(w), 4) for asset, w in zip(names, np.asarray(model.weights_, dtype=float))}
        pred = model.predict(test)
        rows.append({
            "name": name,
            "weights": weights,
            "test": _metrics(pred),
            "equity": _curve(pred),
        })
    return rows
