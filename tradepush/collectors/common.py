from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


def read_csv_safe(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size <= 2:
        return pd.DataFrame()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return pd.read_csv(path, dtype={"code": str}, encoding=encoding)
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
        except OSError:
            return pd.DataFrame()
    return pd.DataFrame()


def deduplicate_securities(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one row per market/security code while preserving display values."""
    if df.empty or "code" not in df.columns:
        return df.copy()
    work = df.copy()
    market = work.get("market", pd.Series("", index=work.index)).astype(str).str.upper().str.strip()
    code = work["code"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    work["_security_key"] = market + ":" + code
    return work.drop_duplicates("_security_key", keep="first").drop(columns="_security_key").reset_index(drop=True)


def file_date_key(path: Path) -> tuple[str, float]:
    dates = re.findall(r"(?<!\d)(20\d{6})(?!\d)", path.stem)
    return (max(dates) if dates else "", path.stat().st_mtime)


def latest_file(folder: Path, pattern: str) -> Path | None:
    files = list(folder.glob(pattern)) if folder.exists() else []
    return max(files, key=file_date_key) if files else None


def load_latest_usable(
    folder: Path,
    pattern: str,
    required_columns: set[str],
    numeric_positive_column: str | None = None,
) -> tuple[pd.DataFrame, Path | None]:
    paths = sorted(folder.glob(pattern), key=file_date_key, reverse=True) if folder.exists() else []
    for path in paths:
        df = read_csv_safe(path)
        if df.empty or not required_columns.issubset(df.columns):
            continue
        if numeric_positive_column:
            values = pd.to_numeric(df[numeric_positive_column], errors="coerce")
            if not values.gt(0).any():
                continue
        return df, path
    return pd.DataFrame(), None


def load_cookie(path: Path, env_name: str) -> str:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value.strip().strip('"').strip("'")
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    prefix = f"{env_name}="
    if text.startswith(prefix):
        text = text.split("=", 1)[1].strip()
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    text = " ".join(lines).strip().strip('"').strip("'")
    if "这里粘贴" in text or "Cookie占位" in text:
        return ""
    return text


def cookie_status(path: Path, env_name: str, token_hint: str) -> dict[str, str]:
    value = load_cookie(path, env_name)
    if not value:
        return {"status": "缺失/失效", "source": str(path)}
    status = "已配置" if token_hint in value or len(value) > 100 else "可能失效"
    source = f"环境变量 {env_name}" if os.environ.get(env_name, "").strip() else str(path)
    return {"status": status, "source": source}


def safe_number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return default if pd.isna(result) else result
    except (TypeError, ValueError):
        return default


def latest_date(df: pd.DataFrame) -> str:
    for col in ("trade_date", "date", "target_date", "run_time"):
        if col in df and not df.empty:
            parsed = pd.to_datetime(df[col], errors="coerce").dropna()
            if not parsed.empty:
                return parsed.max().strftime("%Y-%m-%d")
    return ""


def date_from_path(path: Path | None) -> str:
    if not path:
        return ""
    match = re.findall(r"20\d{6}", path.stem)
    if not match:
        return ""
    try:
        return datetime.strptime(match[-1], "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def sanitize_filename(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", str(value)).strip()
