import pytest
from app.services.translation_cost import deepseek_cost_cny, cost_from_usage


def test_cost_combines_hit_miss_output_rates():
    # 1M cache-hit (0.5) + 1M cache-miss (2) + 1M output (8) = 10.5 CNY
    cost = deepseek_cost_cny(cache_hit_tokens=1_000_000,
                             cache_miss_tokens=1_000_000,
                             completion_tokens=1_000_000)
    assert cost == pytest.approx(10.5)


def test_cost_from_usage_reads_deepseek_keys():
    usage = {
        "prompt_cache_hit_tokens": 2000,
        "prompt_cache_miss_tokens": 500,
        "completion_tokens": 800,
    }
    # 2000*0.5 + 500*2 + 800*8 all per-million
    expected = (2000 * 0.5 + 500 * 2 + 800 * 8) / 1_000_000
    assert cost_from_usage(usage) == pytest.approx(expected)


def test_cost_from_usage_treats_missing_cache_split_as_miss():
    # Some responses only report prompt_tokens with no hit/miss breakdown.
    usage = {"prompt_tokens": 3000, "completion_tokens": 1000}
    expected = (3000 * 2 + 1000 * 8) / 1_000_000
    assert cost_from_usage(usage) == pytest.approx(expected)


def test_cost_from_empty_usage_is_zero():
    assert cost_from_usage({}) == 0.0
