from __future__ import annotations

import math


def lot_size(market: str, configured_hk_lot: int | None = None) -> int:
    market = str(market).upper()
    if market == "A":
        return 100
    if market == "HK":
        return max(int(configured_hk_lot or 100), 1)
    return 1


def calculate_position(
    equity: float,
    entry: float | None,
    stop: float | None,
    market: str,
    risk_per_trade_pct: float = 1.0,
    max_stock_pct: float = 20.0,
    hk_lot: int | None = None,
) -> dict:
    if not entry or not stop or entry <= stop or equity <= 0:
        return {"shares": 0, "weight_pct": 0.0, "planned_loss": 0.0, "reason": "价格或账户参数无效"}
    per_share_risk = entry - stop
    risk_budget = equity * risk_per_trade_pct / 100
    max_value = equity * max_stock_pct / 100
    raw_shares = min(risk_budget / per_share_risk, max_value / entry)
    lot = lot_size(market, hk_lot)
    shares = max(math.floor(raw_shares / lot) * lot, 0)
    market_value = shares * entry
    return {
        "shares": shares,
        "weight_pct": round(market_value / equity * 100, 2) if equity else 0.0,
        "planned_loss": round(shares * per_share_risk, 2),
        "reason": "" if shares else "风险预算不足一个交易单位",
    }


def portfolio_metrics(positions, prices, equity: float, cash: float) -> dict:
    total_value = 0.0
    total_pnl = 0.0
    theme_values: dict[str, float] = {}
    rows = []
    if positions is None or positions.empty:
        return {
            "market_value": 0.0,
            "exposure_pct": 0.0,
            "cash_pct": 100.0 if equity else 0.0,
            "pnl": 0.0,
            "theme_values": {},
            "rows": rows,
        }
    price_map = {}
    if prices is not None and not prices.empty:
        price_map = {
            str(row["code"]).zfill(5 if str(row.get("market", "")).upper() == "HK" else 6): float(row["close"])
            for _, row in prices.dropna(subset=["close"]).iterrows()
        }
    for _, row in positions.iterrows():
        market = str(row.get("market", "A")).upper()
        code = str(row.get("code", "")).zfill(5 if market == "HK" else 6)
        shares = float(row.get("shares", 0) or 0)
        cost = float(row.get("cost", 0) or 0)
        current = price_map.get(code, cost)
        value = shares * current
        pnl = shares * (current - cost)
        theme = str(row.get("theme", "未分类") or "未分类")
        total_value += value
        total_pnl += pnl
        theme_values[theme] = theme_values.get(theme, 0.0) + value
        rows.append(
            {
                **row.to_dict(),
                "current": current,
                "market_value": value,
                "pnl": pnl,
                "pnl_pct": ((current / cost - 1) * 100) if cost else 0.0,
            }
        )
    return {
        "market_value": total_value,
        "exposure_pct": total_value / equity * 100 if equity else 0.0,
        "cash_pct": cash / equity * 100 if equity else 0.0,
        "pnl": total_pnl,
        "theme_values": theme_values,
        "rows": rows,
    }

