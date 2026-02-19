"""HL info endpoint smoke (skip if no network or key). Placeholder."""

import pytest


@pytest.mark.skip(reason="Requires network; run manually with HL env set")
def test_hl_info_ping() -> None:
    from autotrader.hl.client import info

    out = info("meta", None)
    assert isinstance(out, dict)
