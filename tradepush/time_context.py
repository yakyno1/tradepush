from __future__ import annotations

from datetime import datetime


def market_phase(now: datetime | None = None) -> str:
    """Return the combined A/H workflow phase in local project time."""
    current = now or datetime.now()
    if current.weekday() >= 5:
        return "休市"
    hhmm = current.hour * 100 + current.minute
    if hhmm < 925:
        return "盘前"
    if hhmm <= 1610:
        return "盘中"
    return "收盘后"


def snapshot_kind(now: datetime | None = None) -> str:
    return "intraday" if market_phase(now) == "盘中" else "close"


def snapshot_kind_label(kind: str, formal: bool = False) -> str:
    if kind == "close":
        return "收盘正式版" if formal else "收盘快照"
    if kind == "reconstructed":
        return "历史重建版"
    return "盘中快照"
