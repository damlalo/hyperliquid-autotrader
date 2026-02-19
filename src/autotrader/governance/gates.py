"""Governance gates: candidate must beat baseline on utility and respect risk constraints."""
from __future__ import annotations


def evaluate_candidate(
    candidate_report: dict, baseline_report: dict, config: dict
) -> dict:
    """
    Returns:
      { "pass": bool, "reasons": [...], "delta": {...}, "artifact_paths": {...} }
    """
    # 1) require out-of-sample > baseline + threshold
    # 2) maxDD <= cap
    # 3) robustness suite pass
    # 4) operational feasibility (order rate vs limits)
    return {}
