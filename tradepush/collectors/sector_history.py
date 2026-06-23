from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time
from typing import Any

import numpy as np
import pandas as pd
import requests

from tradepush.collectors.common import read_csv_safe
from tradepush.config import SECTOR_DATA_DIR

FLOW_HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
PRICE_HISTORY_URL = "https://91.push2his.eastmoney.com/api/qt/stock/kline/get"


def _session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return session


def _board_universe() -> pd.DataFrame:
    paths = sorted(SECTOR_DATA_DIR.glob("sector_summary_*.csv"), reverse=True)
    for path in paths:
        frame = read_csv_safe(path)
        if frame.empty or not {"name", "board_code", "board_type"}.issubset(frame.columns):
            continue
        work = frame[["name", "board_code", "board_type"]].dropna(subset=["board_code"])
        return work.drop_duplicates("board_code", keep="first").reset_index(drop=True)
    return pd.DataFrame(columns=["name", "board_code", "board_type"])


def _request_lines(url: str, params: dict[str, str], retries: int = 3) -> tuple[list[list[str]], str]:
    error = ""
    for attempt in range(retries):
        try:
            response = _session().get(url, params=params, timeout=25)
            response.raise_for_status()
            payload = response.json()
            lines = ((payload.get("data") or {}).get("klines") or [])
            return [str(line).split(",") for line in lines], ""
        except (requests.RequestException, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            time.sleep(0.45 * (attempt + 1))
    return [], error


def _fetch_board_history(
    board_code: str,
    start_date: str,
    end_date: str,
) -> tuple[list[list[str]], list[list[str]], str]:
    flow_params = {
        "lmt": "0",
        "klt": "101",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "secid": f"90.{board_code}",
    }
    price_params = {
        "secid": f"90.{board_code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "0",
        "beg": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "smplmt": "10000",
        "lmt": "1000000",
    }
    price_lines, price_error = _request_lines(PRICE_HISTORY_URL, price_params)
    flow_lines, flow_error = _request_lines(FLOW_HISTORY_URL, flow_params)
    errors = " | ".join(value for value in (price_error, flow_error) if value)
    return price_lines, flow_lines, errors


def collect_sector_history_dates(
    dates: list[str],
    *,
    max_workers: int = 4,
) -> pd.DataFrame:
    target_dates = {pd.Timestamp(value).strftime("%Y-%m-%d") for value in dates}
    universe = _board_universe()
    if universe.empty:
        return pd.DataFrame(
            [
                {
                    "date": "",
                    "status": "ERROR",
                    "rows": 0,
                    "message": "缺少当前板块代码表，请先运行一次东方财富板块采集",
                }
            ]
        )

    rows_by_date: dict[str, list[dict[str, Any]]] = {value: [] for value in target_dates}
    errors: list[str] = []
    start_date = min(target_dates)
    end_date = max(target_dates)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _fetch_board_history,
                str(row["board_code"]),
                start_date,
                end_date,
            ): row
            for _, row in universe.iterrows()
        }
        for future in as_completed(future_map):
            meta = future_map[future]
            price_lines, flow_lines, error = future.result()
            if error:
                errors.append(f"{meta['board_code']}: {error}")
            price_by_date = {
                values[0]: values
                for values in price_lines
                if len(values) >= 11 and values[0] in target_dates
            }
            flow_by_date = {
                values[0]: values
                for values in flow_lines
                if len(values) >= 13 and values[0] in target_dates
            }
            for date_value in target_dates:
                price_values = price_by_date.get(date_value)
                flow_values = flow_by_date.get(date_value)
                if price_values is None and flow_values is None:
                    continue
                pct_chg = pd.to_numeric(
                    price_values[8] if price_values is not None else flow_values[12],
                    errors="coerce",
                )
                net_amount = pd.to_numeric(
                    flow_values[1] if flow_values is not None else np.nan,
                    errors="coerce",
                )
                main_net_ratio = pd.to_numeric(
                    flow_values[6] if flow_values is not None else np.nan,
                    errors="coerce",
                )
                rows_by_date[date_value].append(
                    {
                        "group": "行业历史" if meta["board_type"] == "industry" else "概念历史",
                        "source": (
                            "东方财富板块历史行情与资金流重建"
                            if flow_values is not None
                            else "东方财富板块历史行情重建（资金缺失）"
                        ),
                        "name": str(meta["name"]),
                        "pct_chg": pct_chg,
                        "net_amount": net_amount / 1e8 if pd.notna(net_amount) else np.nan,
                        "amount": (
                            pd.to_numeric(price_values[6], errors="coerce") / 1e8
                            if price_values is not None
                            else np.nan
                        ),
                        "leader": "",
                        "leader_pct": 0.0,
                        "theme_hit": "",
                        "reason": (
                            "东方财富历史板块涨跌与主力资金；当日领涨股不可回溯"
                            if flow_values is not None
                            else "仅恢复东方财富历史板块行情；主力资金与领涨股缺失"
                        ),
                        "board_type": str(meta["board_type"]),
                        "board_code": str(meta["board_code"]),
                        "main_net_ratio": main_net_ratio,
                        "trade_date": date_value,
                    }
                )

    status_rows: list[dict[str, Any]] = []
    for date_value in sorted(target_dates):
        output = pd.DataFrame(rows_by_date[date_value])
        if not output.empty:
            flow_coverage = float(output["net_amount"].notna().mean())
            output["rank"] = (
                output.groupby("board_type")["net_amount"]
                .rank(method="first", ascending=False, na_option="bottom")
                .astype("Int64")
            )
            output["line_state"] = "观察"
            output.loc[(output["pct_chg"] >= 1.5) & (output["net_amount"] > 0), "line_state"] = "强"
            output.loc[(output["pct_chg"] > 1.5) & (output["net_amount"] < 0), "line_state"] = "资金背离"
            output.loc[(output["pct_chg"] <= -2) & (output["net_amount"] < 0), "line_state"] = "弱"
            output.to_csv(
                SECTOR_DATA_DIR / f"sector_summary_{date_value.replace('-', '')}.csv",
                index=False,
                encoding="utf-8-sig",
            )
        else:
            flow_coverage = 0.0
        status_rows.append(
            {
                "date": date_value,
                "status": "CREATED" if not output.empty else "MISSING",
                "rows": len(output),
                "flow_coverage_pct": round(flow_coverage * 100, 1),
                "message": (
                    f"历史板块行情已保存，资金覆盖{flow_coverage:.0%}；领涨股字段留空"
                    if not output.empty
                    else "东方财富历史接口未返回该日期，可能超出保留范围"
                ),
                "errors": "；".join(errors[:5]),
                "run_time": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return pd.DataFrame(status_rows)
