from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from tradepush.collectors.common import read_csv_safe, sanitize_filename
from tradepush.config import CONFIG_DIR, HISTORY_DATA_DIR, bootstrap_config


def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    rename = {
        "日期": "trade_date",
        "date": "trade_date",
        "开盘": "open",
        "open": "open",
        "最高": "high",
        "high": "high",
        "最低": "low",
        "low": "low",
        "收盘": "close",
        "close": "close",
        "成交量": "volume",
        "volume": "volume",
        "成交额": "amount",
        "amount": "amount",
    }
    frame = raw.rename(columns=rename)
    if "trade_date" not in frame or "close" not in frame:
        return pd.DataFrame()
    keep = [col for col in ("trade_date", "open", "high", "low", "close", "volume", "amount") if col in frame]
    frame = frame[keep].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    for col in keep:
        if col != "trade_date":
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["trade_date", "close"]).drop_duplicates("trade_date").sort_values("trade_date")


def _fetch_akshare(code: str, market: str, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    if market == "HK":
        try:
            raw = ak.stock_hk_hist(
                symbol=code.zfill(5),
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )
        except (AttributeError, TypeError):
            raw = ak.stock_hk_daily(symbol=code.zfill(5), adjust="qfq")
    else:
        raw = ak.stock_zh_a_hist(
            symbol=code.zfill(6),
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
    return _normalize(raw)


def collect_akshare_history(limit: int | None = None) -> list[dict[str, Any]]:
    bootstrap_config()
    watchlist = read_csv_safe(CONFIG_DIR / "watchlist.csv")
    if watchlist.empty:
        return [{"source": "akshare_history", "status": "ERROR", "rows": 0, "error": "empty watchlist"}]
    active = pd.to_numeric(watchlist.get("active", 1), errors="coerce").fillna(1).astype(int) == 1
    work = watchlist.loc[active].head(limit) if limit else watchlist.loc[active]
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=900)).strftime("%Y%m%d")
    ok = 0
    errors: list[str] = []
    for _, row in work.iterrows():
        market = str(row.get("market", "A")).upper()
        if market not in {"A", "HK"}:
            continue
        width = 5 if market == "HK" else 6
        code = str(row.get("code", "")).replace(".0", "").zfill(width)
        name = str(row.get("name", code))
        try:
            history = _fetch_akshare(code, market, start, end)
            if history.empty:
                raise RuntimeError("empty history")
            history.tail(500).to_csv(
                HISTORY_DATA_DIR / f"{code}_{sanitize_filename(name)}_500d.csv",
                index=False,
                encoding="utf-8-sig",
            )
            ok += 1
        except Exception as exc:  # Collectors must continue after one symbol fails.
            errors.append(f"{code} {name}: {type(exc).__name__}: {exc}")
        time.sleep(0.12)
    return [
        {
            "source": "akshare_history",
            "status": "OK" if ok else "ERROR",
            "rows": ok,
            "path": str(HISTORY_DATA_DIR),
            "error": "；".join(errors[:8]),
            "run_time": datetime.now().isoformat(timespec="seconds"),
        }
    ]
