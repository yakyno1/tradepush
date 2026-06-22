from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from tradepush.collectors.local import (
    latest_data_timestamp,
    load_daily_prices,
    load_history,
    load_market_indices,
    load_positions,
    load_safety_zones,
    load_sector_history,
    load_sector_summary,
    source_health,
)
from tradepush.config import load_account
from tradepush.models import MarketState
from tradepush.risk.positioning import portfolio_metrics
from tradepush.rules.engine import build_decisions, classify_sectors, evaluate_market, forecast_sectors


@dataclass
class DashboardSnapshot:
    market: MarketState
    sectors: pd.DataFrame
    sector_forecast: pd.DataFrame
    decisions: pd.DataFrame
    prices: pd.DataFrame
    indices: pd.DataFrame
    positions: pd.DataFrame
    safety_zones: pd.DataFrame
    source_health: pd.DataFrame
    portfolio: dict
    account: dict
    data_date: str
    generated_at: str


def build_snapshot() -> DashboardSnapshot:
    prices, _ = load_daily_prices()
    indices, _ = load_market_indices()
    sector_raw, _ = load_sector_summary()
    positions = load_positions()
    safety_zones = load_safety_zones()
    account = load_account()
    health = source_health()
    market = evaluate_market(indices, prices, sector_raw)
    sectors = classify_sectors(sector_raw)
    sector_forecast = forecast_sectors(sector_raw, load_sector_history())
    data_date = latest_data_timestamp()
    global_vetoes: list[str] = []
    source_dates = {
        str(row["source"]): pd.to_datetime(row.get("latest"), errors="coerce")
        for _, row in health.iterrows()
    }
    data_ts = pd.to_datetime(data_date, errors="coerce")
    sector_ts = source_dates.get("板块资金流")
    if pd.notna(data_ts) and (sector_ts is None or pd.isna(sector_ts) or (data_ts - sector_ts).days > 3):
        global_vetoes.append("板块资金流过期")
        market.score = max(round(market.score - 12, 1), 0)
        market.label = "谨慎" if market.score >= 45 else "停止新开仓"
        market.max_exposure_pct = 50.0 if market.label == "谨慎" else 0.0
        market.reasons.append("板块资金流未与行情同步，禁止新开仓")
    decisions = build_decisions(
        prices=prices,
        sectors=sector_raw,
        market_state=market,
        safety_zones=safety_zones,
        positions=positions,
        account=account,
        history_loader=load_history,
        data_date=data_date,
        global_vetoes=global_vetoes,
    )
    portfolio = portfolio_metrics(
        positions,
        prices,
        float(account.get("equity", 0)),
        float(account.get("cash", 0)),
    )
    return DashboardSnapshot(
        market=market,
        sectors=sectors,
        sector_forecast=sector_forecast,
        decisions=decisions,
        prices=prices,
        indices=indices,
        positions=positions,
        safety_zones=safety_zones,
        source_health=health,
        portfolio=portfolio,
        account=account,
        data_date=data_date,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )
