from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import pandas as pd

from tradepush.collectors.eastmoney import collect_eastmoney
from tradepush.collectors.history import collect_akshare_history
from tradepush.collectors.xueqiu import collect_xueqiu
from tradepush.config import STATUS_DATA_DIR, bootstrap_config
from tradepush.time_context import snapshot_kind


def _safe_run(name: str, runner: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    try:
        return runner()
    except Exception as exc:
        return [
            {
                "source": name,
                "status": "ERROR",
                "rows": 0,
                "path": "",
                "error": f"{type(exc).__name__}: {exc}",
                "run_time": datetime.now().isoformat(timespec="seconds"),
            }
        ]


def save_status(rows: list[dict[str, Any]]) -> pd.DataFrame:
    bootstrap_config()
    frame = pd.DataFrame(rows)
    path = STATUS_DATA_DIR / f"collection_status_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return frame


def _archive_after_collection(kind: str, reason: str) -> None:
    try:
        from tradepush.services.dashboard import build_snapshot
        from tradepush.storage.snapshots import save_dashboard_snapshot

        snapshot = build_snapshot()
        save_dashboard_snapshot(
            snapshot,
            kind=kind,
            formal=False,
            reason=reason,
            origin="collection",
        )
    except Exception:
        # Collection results remain usable even if the audit archive cannot be written.
        pass


def run_intraday() -> pd.DataFrame:
    rows = []
    rows.extend(_safe_run("xueqiu", lambda: collect_xueqiu(collect_history=False)))
    rows.extend(_safe_run("eastmoney", collect_eastmoney))
    frame = save_status(rows)
    _archive_after_collection("intraday", "盘中刷新后自动归档")
    return frame


def run_all(include_akshare_history: bool = False) -> pd.DataFrame:
    rows = []
    rows.extend(_safe_run("xueqiu", lambda: collect_xueqiu(collect_history=True)))
    rows.extend(_safe_run("eastmoney", collect_eastmoney))
    if include_akshare_history:
        rows.extend(_safe_run("akshare_history", collect_akshare_history))
    frame = save_status(rows)
    kind = snapshot_kind()
    reason = "完整采集后自动归档"
    _archive_after_collection(kind, reason)
    return frame
