from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
DATA_DIR = PROJECT_ROOT / "data"
MARKET_DATA_DIR = DATA_DIR / "market"
SECTOR_DATA_DIR = DATA_DIR / "sectors"
HISTORY_DATA_DIR = DATA_DIR / "history"
VERIFICATION_DATA_DIR = DATA_DIR / "verification"
STATUS_DATA_DIR = DATA_DIR / "status"
RAW_DATA_DIR = DATA_DIR / "raw"

XUEQIU_COOKIE_FILE = PROJECT_ROOT / "xueqiu_cookie.txt"
EASTMONEY_COOKIE_FILE = PROJECT_ROOT / "eastmoney_cookie.txt"

DEFAULT_ACCOUNT = {
    "equity": 1_000_000.0,
    "cash": 1_000_000.0,
    "risk_per_trade_pct": 1.0,
    "max_stock_pct": 20.0,
    "max_theme_pct": 35.0,
    "max_total_pct": 85.0,
    "confirmed": False,
}

POSITION_COLUMNS = [
    "code",
    "name",
    "market",
    "theme",
    "shares",
    "available_shares",
    "cost",
    "buy_date",
    "note",
]

DEFAULT_INDICES = [
    ("SH000001", "上证指数", "A_INDEX", "A股主指数"),
    ("SZ399001", "深证成指", "A_INDEX", "A股主指数"),
    ("SZ399006", "创业板指", "A_INDEX", "成长风格"),
    ("SH000688", "科创50", "A_INDEX", "科技风格"),
    ("HKHSI", "恒生指数", "HK_INDEX", "港股主指数"),
    ("HKHSTECH", "恒生科技指数", "HK_INDEX", "港股科技"),
    ("HKHSCEI", "恒生中国企业指数", "HK_INDEX", "港股国企"),
]


def ensure_project_dirs() -> None:
    for path in (
        CONFIG_DIR,
        OUTPUT_DIR,
        DATA_DIR,
        MARKET_DATA_DIR,
        SECTOR_DATA_DIR,
        HISTORY_DATA_DIR,
        VERIFICATION_DATA_DIR,
        STATUS_DATA_DIR,
        RAW_DATA_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def bootstrap_config() -> None:
    """Only creates local defaults; it never reads another project."""
    ensure_project_dirs()

    account_path = CONFIG_DIR / "account.json"
    if not account_path.exists():
        account_path.write_text(
            json.dumps(DEFAULT_ACCOUNT, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    positions_path = CONFIG_DIR / "positions.csv"
    if not positions_path.exists():
        positions_path.write_text(",".join(POSITION_COLUMNS) + "\n", encoding="utf-8-sig")

    indices_path = CONFIG_DIR / "indices.csv"
    if not indices_path.exists():
        lines = ["symbol,name,market,active,note"]
        lines.extend(f"{symbol},{name},{market},1,{note}" for symbol, name, market, note in DEFAULT_INDICES)
        indices_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def load_account() -> dict:
    bootstrap_config()
    path = CONFIG_DIR / "account.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {**DEFAULT_ACCOUNT, **data}


def save_account(data: dict) -> None:
    ensure_project_dirs()
    merged = {**DEFAULT_ACCOUNT, **data}
    (CONFIG_DIR / "account.json").write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
