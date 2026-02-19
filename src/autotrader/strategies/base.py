"""Base strategy interface: Signal + IStrategy. Risk layer applies caps (no override)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


@dataclass(frozen=True)
class Signal:
    side: str  # "long"|"short"|"flat"
    entry: Optional[float]
    stop: Optional[float]
    take_profit: Optional[float]
    confidence: float
    metadata: Dict[str, Any]


class IStrategy(Protocol):
    name: str

    def applicable_regimes(self) -> List[str]: ...
    def compute_signal(self, ctx: "MarketContext") -> Signal: ...
    def invariants_ok(self, signal: Signal) -> bool: ...


class BaseStrategy:
    """Concrete base implementing IStrategy. Override in strategy modules."""

    name: str = "base"

    def applicable_regimes(self) -> List[str]:
        return []

    def compute_signal(self, ctx: Any) -> Signal:
        return Signal(
            side="flat",
            entry=None,
            stop=None,
            take_profit=None,
            confidence=0.0,
            metadata={},
        )

    def invariants_ok(self, signal: Signal) -> bool:
        return True
