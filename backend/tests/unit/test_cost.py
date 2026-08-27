"""Pricing table lookups and cost calculation."""
from app.services.cost import calculate_cost, get_pricing_table


def test_known_model_calculates_expected_cost():
    # llama-3.1-8b-instant: $0.05 / $0.08 per 1M input/output tokens.
    cost = calculate_cost("groq", "llama-3.1-8b-instant", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.13


def test_zero_tokens_costs_nothing():
    assert calculate_cost("groq", "llama-3.1-8b-instant", 0, 0) == 0.0


def test_unknown_provider_returns_zero_not_an_error():
    assert calculate_cost("unknown-provider", "some-model", 100, 100) == 0.0


def test_unknown_model_for_known_provider_returns_zero():
    assert calculate_cost("groq", "not-a-real-model", 100, 100) == 0.0


def test_pricing_table_includes_both_active_providers():
    table = get_pricing_table()
    assert "groq" in table
    assert "gemini" in table
    assert "gemini-2.5-flash" in table["gemini"]
