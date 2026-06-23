from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from tradepush.config import OUTPUT_DIR, ensure_project_dirs


def output_folder(data_date: str) -> Path:
    ensure_project_dirs()
    key = data_date.replace("-", "") if data_date else datetime.now().strftime("%Y%m%d")
    path = OUTPUT_DIR / key
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_analysis(
    data_date: str,
    market: dict,
    sectors: pd.DataFrame,
    decisions: pd.DataFrame,
    stock_forecasts: pd.DataFrame | None = None,
    sector_forecasts: pd.DataFrame | None = None,
) -> Path:
    folder = output_folder(data_date)
    decisions.to_csv(folder / "trade_decisions.csv", index=False, encoding="utf-8-sig")
    sectors.to_csv(folder / "sector_signals.csv", index=False, encoding="utf-8-sig")
    if stock_forecasts is not None:
        stock_forecasts.to_csv(folder / "stock_multi_horizon_forecasts.csv", index=False, encoding="utf-8-sig")
    if sector_forecasts is not None:
        sector_forecasts.to_csv(folder / "sector_multi_horizon_forecasts.csv", index=False, encoding="utf-8-sig")
    (folder / "market_state.json").write_text(
        json.dumps(market, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# TradePush 每日交易指示",
        "",
        f"- 数据日期：{data_date}",
        f"- 市场状态：{market.get('label')}（{market.get('score')}分）",
        "",
        "## 今日交易清单",
        "",
    ]
    visible = [
        "code",
        "name",
        "sector_state",
        "role",
        "path",
        "action",
        "current_price",
        "trigger_price",
        "suggested_weight_pct",
        "stop_price",
        "target_price",
        "hard_vetoes",
    ]
    show = decisions[[column for column in visible if column in decisions.columns]].head(30)
    lines.append(show.to_markdown(index=False) if not show.empty else "没有可用交易决策。")
    if stock_forecasts is not None and not stock_forecasts.empty:
        lines.extend(["", "## 个股多周期预测", ""])
        forecast_show = stock_forecasts[
            [
                "code",
                "name",
                "horizon",
                "result",
                "expected_return_pct",
                "confidence",
                "conviction",
                "reason",
            ]
        ].head(80)
        lines.append(forecast_show.to_markdown(index=False))
    (folder / "trade_report.md").write_text("\n".join(lines), encoding="utf-8")
    return folder
