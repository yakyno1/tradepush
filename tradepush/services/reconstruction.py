from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tradepush.collectors.common import latest_date, read_csv_safe
from tradepush.collectors.sector_history import collect_sector_history_dates
from tradepush.collectors.local import (
    load_history,
    load_positions,
    load_safety_zones,
    load_sector_history,
)
from tradepush.config import CONFIG_DIR, load_account
from tradepush.features.forecasting import build_sector_horizon_forecasts, build_stock_forecasts
from tradepush.features.technical import enrich_history
from tradepush.risk.positioning import portfolio_metrics
from tradepush.rules.engine import build_decisions, classify_sectors, evaluate_market, forecast_sectors
from tradepush.services.dashboard import DashboardSnapshot
from tradepush.storage.snapshots import existing_reconstruction, save_dashboard_snapshot


@dataclass(frozen=True)
class ReconstructionResult:
    data_date: str
    status: str
    stocks: int = 0
    indices: int = 0
    sectors: int = 0
    snapshot_id: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _code(value: object, market: str) -> str:
    raw = str(value).strip().replace(".0", "")
    return raw.zfill(5 if str(market).upper() == "HK" else 6)


def _historical_row(code: str, name: str, target: pd.Timestamp) -> tuple[dict, pd.DataFrame] | None:
    history, _ = load_history(code, name, as_of=target.strftime("%Y-%m-%d"))
    if history.empty:
        return None
    work = enrich_history(history)
    dates = pd.to_datetime(work["trade_date"], errors="coerce").dt.normalize()
    matches = work.loc[dates == target.normalize()]
    if matches.empty:
        return None
    row_index = matches.index[-1]
    row = work.loc[row_index].to_dict()
    previous = work.loc[work.index < row_index, "close"]
    prev_close = float(previous.iloc[-1]) if not previous.empty else None
    close = float(row["close"])
    row["prev_close"] = prev_close
    row["pct_chg"] = ((close / prev_close - 1) * 100) if prev_close else 0.0
    return row, work.loc[work.index <= row_index].copy()


def _reconstruct_table(metadata: pd.DataFrame, target: pd.Timestamp, *, indices: bool = False) -> pd.DataFrame:
    rows: list[dict] = []
    for _, meta in metadata.iterrows():
        market = str(meta.get("market", "A")).upper()
        raw_code = meta.get("symbol") if indices else meta.get("code")
        code = str(raw_code).strip().replace(".0", "")
        name = str(meta.get("name", code))
        historical = _historical_row(code, name, target)
        if historical is None:
            continue
        row, _ = historical
        output = {
            **meta.to_dict(),
            "code": code if indices else _code(code, market),
            "trade_date": target.strftime("%Y-%m-%d"),
            "date": target.strftime("%Y%m%d"),
            "target_date": target.strftime("%Y-%m-%d"),
            "is_target_date": "是",
            "source_used": "LOCAL_HISTORY_RECONSTRUCTION",
            "status": "OK",
            "error": "",
        }
        for column in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "prev_close",
            "pct_chg",
            "ma5",
            "ma10",
            "ma20",
            "ma60",
            "position_20d",
            "position_60d",
        ):
            output[column] = row.get(column)
        rows.append(output)
    return pd.DataFrame(rows)


def _sector_snapshot_for_date(target: pd.Timestamp) -> pd.DataFrame:
    path = CONFIG_DIR.parent / "data" / "sectors" / f"sector_summary_{target:%Y%m%d}.csv"
    return read_csv_safe(path)


def available_reconstruction_dates(
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[str]:
    """Return the union of locally available A-share and HK index trading dates."""
    dates: set[pd.Timestamp] = set()
    for code, name in (("SH000001", "上证指数"), ("HKHSI", "恒生指数")):
        history, _ = load_history(code, name)
        if history.empty:
            continue
        parsed = pd.to_datetime(history["trade_date"], errors="coerce").dropna().dt.normalize()
        dates.update(parsed.tolist())
    start = pd.to_datetime(start_date, errors="coerce")
    end = pd.to_datetime(end_date, errors="coerce")
    selected = [
        value
        for value in dates
        if (pd.isna(start) or value >= start.normalize())
        and (pd.isna(end) or value <= end.normalize())
    ]
    return [value.strftime("%Y-%m-%d") for value in sorted(selected)]


def reconstruction_date_bounds() -> tuple[str, str] | tuple[None, None]:
    dates = available_reconstruction_dates()
    return (dates[0], dates[-1]) if dates else (None, None)


def build_reconstructed_snapshot(data_date: str) -> DashboardSnapshot:
    target = pd.to_datetime(data_date, errors="raise").normalize()
    watchlist = read_csv_safe(CONFIG_DIR / "watchlist.csv")
    if not watchlist.empty:
        active = pd.to_numeric(watchlist.get("active", 1), errors="coerce").fillna(1).astype(int) == 1
        watchlist = watchlist.loc[active].drop_duplicates(["market", "code"], keep="first")
    indices_meta = read_csv_safe(CONFIG_DIR / "indices.csv")
    if not indices_meta.empty:
        active = pd.to_numeric(indices_meta.get("active", 1), errors="coerce").fillna(1).astype(int) == 1
        indices_meta = indices_meta.loc[active]

    prices = _reconstruct_table(watchlist, target)
    indices = _reconstruct_table(indices_meta, target, indices=True)
    sector_raw = _sector_snapshot_for_date(target)
    positions = load_positions()
    safety_zones = load_safety_zones()
    account = load_account()

    sector_available = not sector_raw.empty
    sector_reconstructed = (
        sector_available
        and sector_raw.get("source", pd.Series("", index=sector_raw.index))
        .astype(str)
        .str.contains("历史")
        .any()
    )
    sector_flow_coverage = (
        float(pd.to_numeric(sector_raw.get("net_amount"), errors="coerce").notna().mean())
        if sector_available and "net_amount" in sector_raw
        else 0.0
    )
    sector_partial = sector_reconstructed and sector_flow_coverage < 0.8
    health = pd.DataFrame(
        [
            {
                "source": "股票日行情",
                "status": "历史重建",
                "rows": len(prices),
                "latest": target.strftime("%Y-%m-%d"),
                "detail": "从本地历史K线精确提取当日OHLCV",
            },
            {
                "source": "市场指数",
                "status": "历史重建",
                "rows": len(indices),
                "latest": target.strftime("%Y-%m-%d"),
                "detail": "从本地指数历史K线精确提取",
            },
            {
                "source": "板块资金流",
                "status": (
                    "历史部分重建"
                    if sector_partial
                    else "历史重建"
                    if sector_reconstructed
                    else "原始快照"
                    if sector_available
                    else "历史缺失"
                ),
                "rows": len(sector_raw),
                "latest": latest_date(sector_raw),
                "detail": (
                    "东方财富历史接口恢复涨跌和主力资金；当日领涨股不可回溯"
                    if sector_reconstructed and not sector_partial
                    else f"已恢复板块行情，主力资金覆盖{sector_flow_coverage:.0%}；当日领涨股不可回溯"
                    if sector_partial
                    else "使用当日已归档文件"
                    if sector_available
                    else "该日未保存东方财富板块资金快照，不能事后伪造"
                ),
            },
            {
                "source": "账户与自选池",
                "status": "当前配置",
                "rows": len(watchlist),
                "latest": "",
                "detail": "重建使用当前本地配置，不冒充当日配置版本",
            },
        ]
    )

    market = evaluate_market(indices, prices, sector_raw)
    if sector_reconstructed or not sector_available:
        market.data_quality = round(
            (25 if not prices.empty else 0)
            + (25 if not indices.empty else 0)
            + (25 if sector_available else 0)
            + 25 * sector_flow_coverage,
            1,
        )
    sectors = classify_sectors(sector_raw)
    sector_history = load_sector_history(as_of=target.strftime("%Y-%m-%d"))
    sector_forecast = forecast_sectors(sector_raw, sector_history)
    sector_horizon_forecasts = build_sector_horizon_forecasts(sectors, sector_history)

    global_vetoes: list[str] = []
    if not sector_available:
        global_vetoes.append("当日板块资金原始快照缺失")
        market.reasons.append("历史重建缺少当日板块资金快照，板块与个股结论降级")
        market.label = "停止新开仓"
        market.max_exposure_pct = 0.0
    elif sector_reconstructed:
        market.reasons.append("板块资金来自历史接口重建，缺少当日领涨股结构")
        if sector_partial:
            global_vetoes.append("当日板块资金历史覆盖不足")
            market.reasons.append(f"板块主力资金仅恢复{sector_flow_coverage:.0%}，禁止据此开新仓")
            market.label = "停止新开仓"
            market.max_exposure_pct = 0.0

    history_loader = lambda code, name: load_history(  # noqa: E731
        code,
        name,
        as_of=target.strftime("%Y-%m-%d"),
    )
    decisions = build_decisions(
        prices=prices,
        sectors=sector_raw,
        market_state=market,
        safety_zones=safety_zones,
        positions=positions,
        account=account,
        history_loader=history_loader,
        data_date=target.strftime("%Y-%m-%d"),
        global_vetoes=global_vetoes,
    )
    stock_forecasts = build_stock_forecasts(
        decisions=decisions,
        history_loader=history_loader,
        market=market,
        data_date=target.strftime("%Y-%m-%d"),
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
        sector_horizon_forecasts=sector_horizon_forecasts,
        stock_forecasts=stock_forecasts,
        decisions=decisions,
        prices=prices,
        indices=indices,
        positions=positions,
        safety_zones=safety_zones,
        source_health=health,
        portfolio=portfolio,
        account=account,
        data_date=target.strftime("%Y-%m-%d"),
        generated_at=datetime.now().isoformat(timespec="seconds"),
        snapshot_kind="reconstructed",
        snapshot_label="历史重建版",
        snapshot_formal=False,
        snapshot_reason="从本地历史K线补算；不等同于当日原始盘后快照",
        snapshot_origin="reconstruction",
    )


def reconstruct_and_archive(
    data_date: str,
    *,
    force: bool = False,
    fetch_sector_history: bool = False,
    root: Path | None = None,
) -> ReconstructionResult:
    target = pd.to_datetime(data_date, errors="coerce")
    if pd.isna(target):
        return ReconstructionResult(str(data_date), "ERROR", message="日期格式无效，请使用 YYYY-MM-DD")
    date_text = target.strftime("%Y-%m-%d")
    available = set(available_reconstruction_dates(date_text, date_text))
    if date_text not in available:
        return ReconstructionResult(
            date_text,
            "SKIPPED",
            message="本地指数历史中没有该交易日，可能是周末、休市日或超出历史覆盖范围",
        )
    sector_missing = _sector_snapshot_for_date(target).empty
    existing = existing_reconstruction(date_text, root=root)
    if existing and not force and not (fetch_sector_history and sector_missing):
        return ReconstructionResult(
            date_text,
            "EXISTS",
            snapshot_id=existing.snapshot_id,
            message=f"已有历史重建版，生成于 {existing.generated_at}",
        )
    if fetch_sector_history and sector_missing:
        sector_result = collect_sector_history_dates([date_text])
        if not sector_result.empty and sector_result.iloc[0]["status"] == "MISSING":
            sector_message = str(sector_result.iloc[0]["message"])
        else:
            sector_message = ""
    else:
        sector_message = ""
    try:
        snapshot = build_reconstructed_snapshot(date_text)
        if snapshot.prices.empty and snapshot.indices.empty:
            return ReconstructionResult(date_text, "ERROR", message="该日股票和指数历史数据均为空")
        record = save_dashboard_snapshot(
            snapshot,
            kind="reconstructed",
            formal=False,
            reason=(
                "从本地K线和东方财富历史板块接口补算；当日领涨股不可回溯"
                if not snapshot.sectors.empty
                else "从本地历史K线补算；板块资金缺失时保持缺失"
            ),
            origin="reconstruction",
            root=root,
        )
        return ReconstructionResult(
            date_text,
            "CREATED",
            stocks=len(snapshot.prices),
            indices=len(snapshot.indices),
            sectors=len(snapshot.sectors),
            snapshot_id=record.snapshot_id,
            message=(
                "历史重建版已生成；重复强制补算会保留为新版本"
                + (f"；{sector_message}" if sector_message else "")
            ),
        )
    except Exception as exc:
        return ReconstructionResult(
            date_text,
            "ERROR",
            message=f"{type(exc).__name__}: {exc}",
        )


def reconstruct_range(
    start_date: str,
    end_date: str,
    *,
    force: bool = False,
    fetch_sector_history: bool = False,
    max_dates: int | None = None,
    root: Path | None = None,
) -> pd.DataFrame:
    dates = available_reconstruction_dates(start_date, end_date)
    if max_dates is not None and len(dates) > max_dates:
        return pd.DataFrame(
            [
                ReconstructionResult(
                    f"{start_date}~{end_date}",
                    "ERROR",
                    message=f"区间包含{len(dates)}个交易日，超过单次上限{max_dates}日，请拆分区间",
                ).to_dict()
            ]
        )
    if not dates:
        return pd.DataFrame(
            [
                ReconstructionResult(
                    f"{start_date}~{end_date}",
                    "SKIPPED",
                    message="区间内没有本地可用交易日",
                ).to_dict()
            ]
        )
    if fetch_sector_history:
        collect_sector_history_dates(dates)
    return pd.DataFrame(
        [
            reconstruct_and_archive(
                date_value,
                force=force,
                fetch_sector_history=False,
                root=root,
            ).to_dict()
            for date_value in dates
        ]
    )
