from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tradepush.config import DATA_DIR, ensure_project_dirs
from tradepush.models import MarketState
from tradepush.services.dashboard import DashboardSnapshot
from tradepush.time_context import snapshot_kind_label

SNAPSHOT_DIR = DATA_DIR / "snapshots"

FRAME_FILES = {
    "sectors": "sectors.csv",
    "sector_forecast": "sector_forecast.csv",
    "sector_horizon_forecasts": "sector_horizon_forecasts.csv",
    "stock_forecasts": "stock_forecasts.csv",
    "decisions": "decisions.csv",
    "prices": "prices.csv",
    "indices": "indices.csv",
    "positions": "positions.csv",
    "safety_zones": "safety_zones.csv",
    "source_health": "source_health.csv",
}


@dataclass(frozen=True)
class SnapshotRecord:
    snapshot_id: str
    data_date: str
    generated_at: str
    kind: str
    formal: bool
    label: str
    reason: str
    origin: str
    path: str
    complete: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value):
        return None
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _snapshot_root(root: Path | None = None) -> Path:
    ensure_project_dirs()
    target = root or SNAPSHOT_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def save_dashboard_snapshot(
    snapshot: DashboardSnapshot,
    *,
    kind: str,
    formal: bool,
    reason: str,
    origin: str,
    root: Path | None = None,
) -> SnapshotRecord:
    base = _snapshot_root(root)
    generated = datetime.now()
    date_key = str(snapshot.data_date or generated.date().isoformat()).replace("-", "")
    snapshot_id = f"{date_key}_{generated:%H%M%S_%f}_{kind}"
    folder = base / date_key / snapshot_id
    folder.mkdir(parents=True, exist_ok=False)

    for field_name, filename in FRAME_FILES.items():
        frame = getattr(snapshot, field_name)
        frame.to_csv(folder / filename, index=False, encoding="utf-8-sig")

    (folder / "market.json").write_text(
        json.dumps(snapshot.market.to_dict(), ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    (folder / "account.json").write_text(
        json.dumps(snapshot.account, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    (folder / "portfolio.json").write_text(
        json.dumps(snapshot.portfolio, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    record = SnapshotRecord(
        snapshot_id=snapshot_id,
        data_date=str(snapshot.data_date),
        generated_at=generated.isoformat(timespec="seconds"),
        kind=kind,
        formal=bool(formal),
        label=snapshot_kind_label(kind, formal),
        reason=reason,
        origin=origin,
        path=str(folder),
        complete=True,
    )
    (folder / "manifest.json").write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return record


def _read_frame(folder: Path, field_name: str) -> pd.DataFrame:
    path = folder / FRAME_FILES[field_name]
    if not path.exists() or path.stat().st_size <= 2:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig", dtype={"code": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def load_dashboard_snapshot(record_or_path: SnapshotRecord | str | Path) -> DashboardSnapshot:
    folder = Path(record_or_path.path if isinstance(record_or_path, SnapshotRecord) else record_or_path)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    market_data = json.loads((folder / "market.json").read_text(encoding="utf-8"))
    market = MarketState(**market_data)
    account = json.loads((folder / "account.json").read_text(encoding="utf-8"))
    portfolio = json.loads((folder / "portfolio.json").read_text(encoding="utf-8"))
    return DashboardSnapshot(
        market=market,
        sectors=_read_frame(folder, "sectors"),
        sector_forecast=_read_frame(folder, "sector_forecast"),
        sector_horizon_forecasts=_read_frame(folder, "sector_horizon_forecasts"),
        stock_forecasts=_read_frame(folder, "stock_forecasts"),
        decisions=_read_frame(folder, "decisions"),
        prices=_read_frame(folder, "prices"),
        indices=_read_frame(folder, "indices"),
        positions=_read_frame(folder, "positions"),
        safety_zones=_read_frame(folder, "safety_zones"),
        source_health=_read_frame(folder, "source_health"),
        portfolio=portfolio,
        account=account,
        data_date=str(manifest.get("data_date", "")),
        generated_at=str(manifest.get("generated_at", "")),
        snapshot_id=str(manifest.get("snapshot_id", folder.name)),
        snapshot_kind=str(manifest.get("kind", "intraday")),
        snapshot_label=str(manifest.get("label", "历史快照")),
        snapshot_formal=bool(manifest.get("formal", False)),
        snapshot_reason=str(manifest.get("reason", "")),
        snapshot_origin=str(manifest.get("origin", "")),
        archive_path=str(folder),
    )


def list_snapshot_records(root: Path | None = None) -> list[SnapshotRecord]:
    base = _snapshot_root(root)
    records: list[SnapshotRecord] = []
    for path in base.glob("*/*/manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["path"] = str(path.parent)
            records.append(SnapshotRecord(**data))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return sorted(records, key=lambda item: (item.data_date, item.generated_at), reverse=True)


def find_snapshot(snapshot_id: str, root: Path | None = None) -> SnapshotRecord | None:
    return next((record for record in list_snapshot_records(root) if record.snapshot_id == snapshot_id), None)


def existing_reconstruction(data_date: str, root: Path | None = None) -> SnapshotRecord | None:
    return next(
        (
            record
            for record in list_snapshot_records(root)
            if record.data_date == data_date and record.kind == "reconstructed"
        ),
        None,
    )


def latest_formal_close(
    *,
    before_or_on: str | None = None,
    exclude_date: str | None = None,
    root: Path | None = None,
) -> SnapshotRecord | None:
    for record in list_snapshot_records(root):
        if record.kind != "close" or not record.formal:
            continue
        if before_or_on and record.data_date > before_or_on:
            continue
        if exclude_date and record.data_date == exclude_date:
            continue
        return record
    return None


def snapshot_calendar(root: Path | None = None) -> pd.DataFrame:
    records = list_snapshot_records(root)
    if not records:
        return pd.DataFrame(
            columns=[
                "date",
                "intraday_count",
                "close_count",
                "reconstruction_count",
                "formal_close",
                "latest_time",
                "status",
            ]
        )
    frame = pd.DataFrame([record.to_dict() for record in records])
    rows: list[dict[str, Any]] = []
    for date_value, group in frame.groupby("data_date", sort=False):
        intraday = int((group["kind"] == "intraday").sum())
        close_count = int((group["kind"] == "close").sum())
        reconstruction_count = int((group["kind"] == "reconstructed").sum())
        formal_close = bool(((group["kind"] == "close") & group["formal"]).any())
        if formal_close:
            status = "完整"
        elif reconstruction_count:
            status = "有历史重建版"
        else:
            status = "缺正式收盘版"
        rows.append(
            {
                "date": date_value,
                "intraday_count": intraday,
                "close_count": close_count,
                "reconstruction_count": reconstruction_count,
                "formal_close": formal_close,
                "latest_time": str(group["generated_at"].max()),
                "status": status,
            }
        )
    return pd.DataFrame(rows).sort_values("date", ascending=False).reset_index(drop=True)
