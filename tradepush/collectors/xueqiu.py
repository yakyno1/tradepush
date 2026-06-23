from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from tradepush.collectors.common import (
    deduplicate_securities,
    load_cookie,
    read_csv_safe,
    safe_number,
    sanitize_filename,
)
from tradepush.config import (
    CONFIG_DIR,
    HISTORY_DATA_DIR,
    MARKET_DATA_DIR,
    RAW_DATA_DIR,
    XUEQIU_COOKIE_FILE,
    bootstrap_config,
)

XUEQIU_HOME = "https://xueqiu.com/"
XUEQIU_API = "https://stock.xueqiu.com"


def normalize_symbol(code: str, market: str) -> str:
    raw = str(code).strip().upper().replace(".0", "")
    market = str(market).upper()
    if raw.startswith(("SH", "SZ", "US")):
        return raw
    if raw.startswith("HK"):
        return raw[2:].zfill(5) if raw[2:].isdigit() else raw
    if market.startswith("HK"):
        return raw.zfill(5)
    if market.startswith("A"):
        return ("SH" if raw.startswith(("5", "6", "9")) else "SZ") + raw.zfill(6)
    return raw


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://xueqiu.com/",
            "Origin": "https://xueqiu.com",
        }
    )
    cookie = load_cookie(XUEQIU_COOKIE_FILE, "XUEQIU_COOKIE")
    if cookie:
        session.headers["Cookie"] = cookie
    try:
        session.get(XUEQIU_HOME, timeout=10)
    except requests.RequestException:
        pass
    return session


def _get_json(
    session: requests.Session,
    endpoint: str,
    params: dict[str, Any],
    retries: int = 1,
) -> tuple[dict | None, str]:
    error = ""
    for attempt in range(retries + 1):
        try:
            response = session.get(f"{XUEQIU_API}{endpoint}", params=params, timeout=15)
            if response.status_code == 200:
                return response.json(), ""
            error = f"HTTP {response.status_code}: {(response.text or '')[:180]}"
        except (requests.RequestException, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.4 * (attempt + 1))
    return None, error


def fetch_quotes(session: requests.Session, symbols: list[str]) -> tuple[dict[str, dict], dict[str, str]]:
    quotes: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for offset in range(0, len(symbols), 20):
        chunk = symbols[offset : offset + 20]
        payload, error = _get_json(
            session,
            "/v5/stock/batch/quote.json",
            {"symbol": ",".join(chunk), "extend": "detail"},
        )
        if payload:
            for item in ((payload.get("data") or {}).get("items") or []):
                quote = item.get("quote") if isinstance(item, dict) else None
                if isinstance(quote, dict) and quote.get("symbol"):
                    quotes[str(quote["symbol"]).upper()] = quote
        missing = [symbol for symbol in chunk if symbol.upper() not in quotes]
        if "400016" in error:
            for symbol in missing:
                errors[symbol] = error
            continue
        for symbol in missing:
            payload_one, error_one = _get_json(
                session,
                "/v5/stock/quote.json",
                {"symbol": symbol, "extend": "detail"},
            )
            quote = ((payload_one or {}).get("data") or {}).get("quote")
            if isinstance(quote, dict):
                quotes[symbol.upper()] = quote
            else:
                errors[symbol] = error_one or error or "empty quote"
    return quotes, errors


def fetch_kline(session: requests.Session, symbol: str, count: int = 500) -> tuple[pd.DataFrame, str]:
    payload, error = _get_json(
        session,
        "/v5/stock/chart/kline.json",
        {
            "symbol": symbol,
            "begin": int(time.time() * 1000),
            "period": "day",
            "type": "before",
            "count": -abs(count),
            "indicator": "kline,pe,pb,ps,pcf,market_capital,agt,ggt,balance",
        },
    )
    data = (payload or {}).get("data") or {}
    columns = data.get("column") or []
    items = data.get("item") or []
    if not columns or not items:
        return pd.DataFrame(), error or "empty kline"
    frame = pd.DataFrame(items, columns=columns)
    if "timestamp" in frame:
        frame["trade_date"] = (
            pd.to_datetime(frame["timestamp"], unit="ms", errors="coerce", utc=True)
            .dt.tz_convert("Asia/Shanghai")
            .dt.tz_localize(None)
            .dt.normalize()
        )
    elif "date" in frame:
        frame["trade_date"] = pd.to_datetime(frame["date"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume", "amount", "turnoverrate"):
        if col in frame:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    keep = [col for col in ("trade_date", "open", "high", "low", "close", "volume", "amount", "turnoverrate") if col in frame]
    clean = frame[keep].dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    return clean, ""


def _trade_date(quote: dict) -> str:
    timestamp = quote.get("timestamp") or quote.get("time")
    if timestamp:
        parsed = pd.to_datetime(timestamp, unit="ms", errors="coerce")
        if pd.notna(parsed):
            return parsed.strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def _history_features(history: pd.DataFrame) -> dict[str, float]:
    if history.empty or "close" not in history:
        return {}
    close = pd.to_numeric(history["close"], errors="coerce").dropna()
    if close.empty:
        return {}
    result: dict[str, float] = {}
    for window in (5, 10, 20, 60):
        if len(close) >= window:
            result[f"ma{window}"] = round(float(close.tail(window).mean()), 4)
    for window in (20, 60):
        sample = history.tail(window)
        if len(sample) >= min(window, 10) and {"high", "low"}.issubset(sample.columns):
            high = pd.to_numeric(sample["high"], errors="coerce").max()
            low = pd.to_numeric(sample["low"], errors="coerce").min()
            result[f"position_{window}d"] = round(float((close.iloc[-1] - low) / (high - low)), 4) if high > low else 0.5
    return result


def _quote_row(meta: pd.Series, symbol: str, quote: dict, history: pd.DataFrame, error: str) -> dict:
    market = str(meta.get("market", "A")).upper()
    width = 5 if market == "HK" else 6
    code = str(meta.get("code", "")).replace(".0", "").zfill(width)
    current = safe_number(quote.get("current"))
    prev_close = safe_number(quote.get("last_close"))
    pct_chg = safe_number(quote.get("percent"))
    if not pct_chg and current and prev_close:
        pct_chg = (current / prev_close - 1) * 100
    row = {
        **meta.to_dict(),
        "code": code,
        "date": _trade_date(quote).replace("-", ""),
        "trade_date": _trade_date(quote),
        "target_date": _trade_date(quote),
        "is_target_date": "是",
        "open": safe_number(quote.get("open"), current),
        "high": safe_number(quote.get("high"), current),
        "low": safe_number(quote.get("low"), current),
        "close": current,
        "prev_close": prev_close,
        "pct_chg": round(pct_chg, 4),
        "volume": safe_number(quote.get("volume")),
        "amount": safe_number(quote.get("amount")),
        "turnover": safe_number(quote.get("turnover_rate")),
        "source_used": "XUEQIU_LOCAL",
        "status": "OK" if current > 0 else "ERROR",
        "error": error,
        "symbol": symbol,
    }
    row.update(_history_features(history))
    return row


def _error_row(meta: pd.Series, symbol: str, error: str) -> dict:
    market = str(meta.get("market", "A")).upper()
    width = 5 if market == "HK" else 6
    return {
        **meta.to_dict(),
        "code": str(meta.get("code", "")).replace(".0", "").zfill(width),
        "date": datetime.now().strftime("%Y%m%d"),
        "symbol": symbol,
        "status": "ERROR",
        "error": error,
        "source_used": "XUEQIU_LOCAL_FAILED",
    }


def _collect_table(
    session: requests.Session,
    metadata: pd.DataFrame,
    output_prefix: str,
    collect_history: bool,
    kline_count: int,
) -> dict[str, Any]:
    if metadata.empty:
        return {"source": output_prefix, "status": "ERROR", "rows": 0, "error": "empty metadata"}
    work = metadata.copy()
    work = work[pd.to_numeric(work.get("active", 1), errors="coerce").fillna(1).astype(int) == 1]
    work["symbol"] = [normalize_symbol(code, market) for code, market in zip(work["code"], work["market"])]
    quotes, quote_errors = fetch_quotes(session, work["symbol"].tolist())
    rows: list[dict] = []
    history_ok = 0
    history_errors: list[str] = []
    for _, meta in work.iterrows():
        symbol = str(meta["symbol"])
        quote = quotes.get(symbol.upper())
        if not quote:
            rows.append(_error_row(meta, symbol, quote_errors.get(symbol, "quote missing")))
            continue
        history = pd.DataFrame()
        history_error = ""
        if collect_history:
            history, history_error = fetch_kline(session, symbol, count=kline_count)
            if not history.empty:
                width = 5 if str(meta.get("market", "")).upper().startswith("HK") else 6
                code = str(meta.get("code", "")).replace(".0", "").zfill(width)
                filename = f"{code}_{sanitize_filename(str(meta.get('name', code)))}_500d.csv"
                history.to_csv(HISTORY_DATA_DIR / filename, index=False, encoding="utf-8-sig")
                history_ok += 1
            elif history_error:
                history_errors.append(f"{symbol}: {history_error}")
        rows.append(_quote_row(meta, symbol, quote, history, history_error))
        time.sleep(0.06)
    output = pd.DataFrame(rows)
    tag = datetime.now().strftime("%Y%m%d")
    output_path = MARKET_DATA_DIR / f"{output_prefix}_{tag}.csv"
    output.to_csv(output_path, index=False, encoding="utf-8-sig")
    ok_rows = int(pd.to_numeric(output.get("close"), errors="coerce").gt(0).sum()) if "close" in output else 0
    return {
        "source": output_prefix,
        "status": "OK" if ok_rows else "ERROR",
        "rows": ok_rows,
        "history_rows": history_ok,
        "path": str(output_path),
        "error": "；".join([*list(quote_errors.values())[:3], *history_errors[:3]]),
        "run_time": datetime.now().isoformat(timespec="seconds"),
    }


def collect_xueqiu(collect_history: bool = True, kline_count: int = 500) -> list[dict[str, Any]]:
    bootstrap_config()
    watchlist = deduplicate_securities(read_csv_safe(CONFIG_DIR / "watchlist.csv"))
    indices = read_csv_safe(CONFIG_DIR / "indices.csv")
    session = _session()
    results = [
        _collect_table(session, watchlist, "daily_prices", collect_history, kline_count),
        _collect_table(session, indices.rename(columns={"symbol": "code"}), "market_indices", collect_history, kline_count),
    ]
    raw_path = RAW_DATA_DIR / f"xueqiu_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    raw_path.write_text(pd.Series({"results": results}).to_json(force_ascii=False), encoding="utf-8")
    return results
