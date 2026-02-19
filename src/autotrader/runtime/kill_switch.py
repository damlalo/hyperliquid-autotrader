"""Kill switch: triggers and actions (cancel orders, reduce/flatten, stop new orders, alert)."""


def is_triggered() -> bool:
    """True if kill switch is triggered."""
    return False  # TODO: check triggers


def execute() -> None:
    """Execute kill switch actions."""
    # TODO: cancel open orders, flatten/reduce per policy, alert
    pass
