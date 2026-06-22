from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any

import pandas as pd
import requests

from tradepush.collectors.common import load_cookie
from tradepush.config import EASTMONEY_COOKIE_FILE, RAW_DATA_DIR, SECTOR_DATA_DIR, bootstrap_config

RANK_URL = "https://push2.eastmoney.com/weblogin/api/qt/clist/get"


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
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    cookie = load_cookie(EASTMONEY_COOKIE_FILE, "EASTMONEY_COOKIE")
    if cookie:
        session.headers["Cookie"] = cookie
    return session


def _parse_jsonp(text: str) -> dict:
    value = (text or "").strip()
    if value.startswith("{"):
        return json.loads(value)
    match = re.search(r"^[\w$]+\((.*)\)\s*;?$", value, re.S)
    if match:
        return json.loads(match.group(1))
    raise ValueError(f"not JSON/JSONP: {value[:160]}")


def _fetch_rank(session: requests.Session, board_type: str, page_size: int = 120) -> tuple[pd.DataFrame, str]:
    if board_type == "industry":
        fs = "m:90+t:2"
        referer = "https://data.eastmoney.com/bkzj/hy.html"
        source = "东方财富行业资金流"
    else:
        fs = "m:90+t:3"
        referer = "https://data.eastmoney.com/bkzj/gn.html"
        source = "东方财富概念资金流"
    params = {
        "pn": "1",
        "pz": str(page_size),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f62",
        "fs": fs,
        "fields": "f12,f14,f2,f3,f6,f62,f66,f72,f78,f84,f184,f204,f205,f124",
        "_": str(int(time.time() * 1000)),
    }
    try:
        response = session.get(RANK_URL, params=params, headers={"Referer": referer}, timeout=20)
        if response.status_code != 200:
            return pd.DataFrame(), f"HTTP {response.status_code}: {(response.text or '')[:180]}"
        payload = _parse_jsonp(response.text)
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        return pd.DataFrame(), f"{type(exc).__name__}: {exc}"
    diff = ((payload.get("data") or {}).get("diff") or [])
    if not diff:
        return pd.DataFrame(), "empty diff"
    frame = pd.DataFrame(diff).rename(
        columns={
            "f12": "board_code",
            "f14": "name",
            "f2": "close",
            "f3": "pct_chg",
            "f6": "amount_raw",
            "f62": "net_amount_raw",
            "f184": "main_net_ratio",
            "f204": "leader",
            "f205": "leader_code",
            "f124": "timestamp",
        }
    )
    for col in ("close", "pct_chg", "amount_raw", "net_amount_raw", "main_net_ratio"):
        frame[col] = pd.to_numeric(frame.get(col), errors="coerce")
    frame["board_type"] = board_type
    frame["source"] = source
    frame["amount"] = frame["amount_raw"] / 1e8
    frame["net_amount"] = frame["net_amount_raw"] / 1e8
    frame["leader_pct"] = 0.0
    return frame, ""


def _normalize_summary(frames: list[pd.DataFrame], top_n: int = 25) -> pd.DataFrame:
    full = pd.concat([frame for frame in frames if not frame.empty], ignore_index=True)
    if full.empty:
        return pd.DataFrame()
    full = full.dropna(subset=["name"]).drop_duplicates(["board_type", "name"])
    output = full.copy()
    output["rank"] = (
        output.groupby("board_type")["net_amount"]
        .rank(method="first", ascending=False, na_option="bottom")
        .astype("Int64")
    )
    output["group"] = output["board_type"].map({"industry": "行业全量", "concept": "概念全量"}).fillna("板块全量")
    output["line_state"] = "观察"
    output.loc[(output["pct_chg"] >= 1.5) & (output["net_amount"] > 0), "line_state"] = "强"
    output.loc[(output["pct_chg"] > 1.5) & (output["net_amount"] < 0), "line_state"] = "资金背离"
    output.loc[(output["pct_chg"] <= -2) & (output["net_amount"] < 0), "line_state"] = "弱"
    output["theme_hit"] = ""
    output["reason"] = "东方财富全量板块快照"
    output["trade_date"] = pd.to_datetime(output.get("timestamp"), unit="s", errors="coerce").dt.strftime("%Y-%m-%d")
    keep = [
        "group",
        "rank",
        "source",
        "name",
        "pct_chg",
        "net_amount",
        "amount",
        "leader",
        "leader_pct",
        "theme_hit",
        "line_state",
        "reason",
        "board_type",
        "board_code",
        "main_net_ratio",
        "trade_date",
    ]
    output = output[[col for col in keep if col in output]].copy()
    # A sector can appear in both ranking groups; keep its strongest evidence.
    output["_priority"] = output["pct_chg"].fillna(0) + output["net_amount"].fillna(0).clip(-50, 50) / 10
    return output.sort_values("_priority", ascending=False).drop(columns="_priority").reset_index(drop=True)


def collect_eastmoney() -> list[dict[str, Any]]:
    bootstrap_config()
    session = _session()
    industry, industry_error = _fetch_rank(session, "industry")
    concept, concept_error = _fetch_rank(session, "concept")
    summary = _normalize_summary([industry, concept])
    data_dates = pd.to_datetime(summary.get("trade_date"), errors="coerce").dropna() if not summary.empty else pd.Series(dtype="datetime64[ns]")
    tag = data_dates.max().strftime("%Y%m%d") if not data_dates.empty else datetime.now().strftime("%Y%m%d")
    path = SECTOR_DATA_DIR / f"sector_summary_{tag}.csv"
    if not summary.empty:
        summary.to_csv(path, index=False, encoding="utf-8-sig")
    raw = pd.concat([frame for frame in (industry, concept) if not frame.empty], ignore_index=True)
    raw_path = RAW_DATA_DIR / f"eastmoney_sector_full_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    if not raw.empty:
        raw.to_csv(raw_path, index=False, encoding="utf-8-sig")
    error = "；".join(value for value in (industry_error, concept_error) if value)
    return [
        {
            "source": "sector_summary",
            "status": "OK" if not summary.empty else "ERROR",
            "rows": len(summary),
            "path": str(path if not summary.empty else SECTOR_DATA_DIR),
            "error": error,
            "run_time": datetime.now().isoformat(timespec="seconds"),
        }
    ]
