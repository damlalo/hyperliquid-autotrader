"""Base strategy interface: signals, no-trade conditions; respects risk layer (no cap override)."""


class BaseStrategy:
    """Override to implement signal generation. Risk caps applied by risk layer."""

    def signal(self, context: dict) -> dict:
        """Return signal dict; sizing/leverage applied by risk. TODO."""
        return {}  # TODO
