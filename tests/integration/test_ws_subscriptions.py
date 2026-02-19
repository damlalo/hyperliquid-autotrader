"""WS subscriptions (skip without key). Placeholder."""

import pytest


@pytest.mark.skip(reason="Requires WS; run manually")
def test_ws_connect() -> None:
    from autotrader.hl import ws

    assert ws.connect is not None
