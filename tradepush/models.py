from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class MarketState:
    label: str
    score: float
    max_exposure_pct: float
    breadth_ratio: float
    index_avg_pct: float
    data_quality: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TradeDecision:
    code: str
    name: str
    market: str
    theme: str
    sector_state: str
    role: str
    path: str
    action: str
    current_price: float | None
    trigger_price: float | None
    stop_price: float | None
    target_price: float | None
    risk_reward: float | None
    suggested_weight_pct: float
    suggested_shares: int
    score: float
    gate_passed: bool
    cancel_condition: str
    reasons: list[str] = field(default_factory=list)
    hard_vetoes: list[str] = field(default_factory=list)
    evidence_time: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reasons"] = "；".join(self.reasons)
        data["hard_vetoes"] = "；".join(self.hard_vetoes)
        return data

