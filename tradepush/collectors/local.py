from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from tradepush.collectors.common import (
    cookie_status,
    date_from_path,
    latest_date,
    load_latest_usable,
    read_csv_safe,
)
from tradepush.config import (
    CONFIG_DIR,
    EASTMONEY_COOKIE_FILE,
    HISTORY_DATA_DIR,
    MARKET_DATA_DIR,
    SECTOR_DATA_DIR,
    STATUS_DATA_DIR,
    VERIFICATION_DATA_DIR,
    XUEQIU_COOKIE_FILE,
    bootstrap_config,
)


def load_watchlist() -> pd.DataFrame:
    bootstrap_config()
    return read_csv_safe(CONFIG_DIR / "watchlist.csv")


def load_safety_zones() -> pd.DataFrame:
    bootstrap_config()
    return read_csv_safe(CONFIG_DIR / "safety_zones.csv")


def load_positions() -> pd.DataFrame:
    bootstrap_config()
    df = read_csv_safe(CONFIG_DIR / "positions.csv")
    for col in ("shares", "available_shares", "cost"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df


def load_daily_prices() -> tuple[pd.DataFrame, Path | None]:
    return load_latest_usable(
        MARKET_DATA_DIR,
        "daily_prices_*.csv",
        {"code", "name", "market", "close"},
        "close",
    )


def load_market_indices() -> tuple[pd.DataFrame, Path | None]:
    return load_latest_usable(
        MARKET_DATA_DIR,
        "market_indices_*.csv",
        {"name", "close"},
        "close",
    )


def load_sector_summary() -> tuple[pd.DataFrame, Path | None]:
    return load_latest_usable(
        SECTOR_DATA_DIR,
        "sector_summary_*.csv",
        {"name", "pct_chg"},
    )


def load_sector_history(limit: int = 6) -> list[tuple[pd.DataFrame, Path]]:
    paths = sorted(SECTOR_DATA_DIR.glob("sector_summary_*.csv"), reverse=True)
    result: list[tuple[pd.DataFrame, Path]] = []
    for path in paths[:limit]:
        frame = read_csv_safe(path)
        if not frame.empty and {"name", "pct_chg"}.issubset(frame.columns):
            result.append((frame, path))
    return result


def load_prediction_verification() -> tuple[pd.DataFrame, list[Path]]:
    paths = sorted(VERIFICATION_DATA_DIR.glob("prediction_verify_*.csv"))
    frames = [read_csv_safe(path) for path in paths]
    frames = [df for df in frames if not df.empty]
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(), paths)


def history_candidates(code: str, name: str) -> list[Path]:
    raw = str(code).strip().replace(".0", "")
    width = 5 if raw.isdigit() and len(raw) <= 5 else 6
    normalized = raw.zfill(width)
    exact = HISTORY_DATA_DIR / f"{normalized}_{name}_500d.csv"
    candidates = [exact, *HISTORY_DATA_DIR.glob(f"{normalized}_*_500d.csv")]
    # The collector can also keep a source suffix while a run is in progress.
    candidates.extend(HISTORY_DATA_DIR.glob(f"{normalized}_*kline*.csv"))
    return list(dict.fromkeys(candidates))


def load_history(code: str, name: str) -> tuple[pd.DataFrame, Path | None]:
    for path in history_candidates(code, name):
        if not path.exists():
            continue
        df = read_csv_safe(path)
        if df.empty:
            continue
        rename = {
            "日期": "trade_date",
            "date": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }
        df = df.rename(columns=rename)
        if "trade_date" not in df or "close" not in df:
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
        for col in ("open", "high", "low", "close", "volume", "amount"):
            if col in df:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        clean = df.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
        if not clean.empty:
            return clean, path
    return pd.DataFrame(), None


def load_collection_status() -> pd.DataFrame:
    paths = sorted(STATUS_DATA_DIR.glob("collection_status_*.csv"), reverse=True)[:20]
    frames = [read_csv_safe(path) for path in paths]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True)
    if "run_time" in result:
        result = result.sort_values("run_time", ascending=False)
    if "source" in result:
        result = result.drop_duplicates("source", keep="first")
    return result.reset_index(drop=True)


def source_health() -> pd.DataFrame:
    daily, daily_path = load_daily_prices()
    indices, indices_path = load_market_indices()
    sectors, sectors_path = load_sector_summary()
    xq = cookie_status(XUEQIU_COOKIE_FILE, "XUEQIU_COOKIE", "xq_a_token=")
    em = cookie_status(EASTMONEY_COOKIE_FILE, "EASTMONEY_COOKIE", "qgqp_b_id=")
    history_count = len(list(HISTORY_DATA_DIR.glob("*_500d.csv")))
    status = load_collection_status()
    xq_runs = status[status.get("source", pd.Series(dtype=str)).astype(str).isin(["daily_prices", "market_indices"])] if not status.empty else pd.DataFrame()
    if not xq_runs.empty and (xq_runs["status"].astype(str) != "OK").all():
        xq["status"] = "采集失败/可能失效"
    em_runs = status[status.get("source", pd.Series(dtype=str)).astype(str).eq("sector_summary")] if not status.empty else pd.DataFrame()
    if not em_runs.empty and (em_runs["status"].astype(str) != "OK").all():
        em["status"] = "采集失败/可能失效"
    rows = [
        {"source": "雪球 Cookie", "status": xq["status"], "rows": "", "latest": "", "detail": xq["source"]},
        {"source": "东方财富 Cookie", "status": em["status"], "rows": "", "latest": "", "detail": em["source"]},
        {
            "source": "股票日行情",
            "status": "可用" if not daily.empty else "缺失",
            "rows": len(daily),
            "latest": latest_date(daily),
            "detail": str(daily_path or MARKET_DATA_DIR),
        },
        {
            "source": "市场指数",
            "status": "可用" if not indices.empty else "缺失",
            "rows": len(indices),
            "latest": latest_date(indices),
            "detail": str(indices_path or MARKET_DATA_DIR),
        },
        {
            "source": "板块资金流",
            "status": "可用" if not sectors.empty else "缺失",
            "rows": len(sectors),
            "latest": latest_date(sectors) or date_from_path(sectors_path),
            "detail": str(sectors_path or SECTOR_DATA_DIR),
        },
        {
            "source": "本地历史K线",
            "status": "可用" if history_count else "缺失",
            "rows": history_count,
            "latest": "",
            "detail": str(HISTORY_DATA_DIR),
        },
        {
            "source": "本地采集状态",
            "status": "可用" if not status.empty else "尚未运行",
            "rows": len(status),
            "latest": latest_date(status),
            "detail": str(STATUS_DATA_DIR),
        },
    ]
    return pd.DataFrame(rows)


def latest_data_timestamp() -> str:
    daily, _ = load_daily_prices()
    return latest_date(daily) or "未知"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def project_is_self_contained() -> bool:
    """A cheap runtime assertion used by tests and the data-center page."""
    project_root = CONFIG_DIR.parent.resolve()
    paths = [
        MARKET_DATA_DIR,
        SECTOR_DATA_DIR,
        HISTORY_DATA_DIR,
        VERIFICATION_DATA_DIR,
        XUEQIU_COOKIE_FILE,
        EASTMONEY_COOKIE_FILE,
    ]
    return all(path.resolve().is_relative_to(project_root) for path in paths)
