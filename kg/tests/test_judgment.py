"""Judgments made by the local model, on cases with a known answer (slow: one model call each)."""

import pytest

from core import playbooks

DESC = "Latest stock price and day change (absolute and percent) for a public company ticker"


@pytest.mark.slow
@pytest.mark.parametrize("situation,params,field,op,value,live,keep", [
    ("AMD stock price surpasses $1 trillion market capitalization", {"symbol": "AMD"}, "price", ">=", 1000, 643.9, False),
    ("AMD shares fall more than 8% in one trading day", {"symbol": "AMD"}, "change_percent", "<=", -8, -0.5, True),
    ("NVIDIA stock trades above $300", {"symbol": "NVDA"}, "price", ">", 300, 237.0, True),
    ("NVIDIA stock trades above $300", {"symbol": "AMD"}, "price", ">", 300, 643.9, False),          # wrong entity
    ("TSMC CoWoS capacity exceeds 140,000 wafers per month", {"symbol": "TSM"}, "price", ">", 500, 472.0, False),
])
async def test_trigger_must_measure_the_situation(situation, params, field, op, value, live, keep):
    problem = await playbooks._meaning(situation, "stock_quote", params, field, op, value, live, DESC)
    assert (problem == "") is keep, problem
