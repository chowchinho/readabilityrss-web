"""DeepSeek translation cost calculation (CNY).

Rates are CNY per 1M tokens for deepseek-chat (deepseek-v4-flash, non-thinking).
Source: https://api-docs.deepseek.com/quick_start/pricing-details-cny
Update these three constants if DeepSeek changes pricing.
"""

RATE_CACHE_HIT_CNY = 0.5
RATE_CACHE_MISS_CNY = 2.0
RATE_OUTPUT_CNY = 8.0


def deepseek_cost_cny(cache_hit_tokens: int, cache_miss_tokens: int, completion_tokens: int) -> float:
    return (
        cache_hit_tokens * RATE_CACHE_HIT_CNY
        + cache_miss_tokens * RATE_CACHE_MISS_CNY
        + completion_tokens * RATE_OUTPUT_CNY
    ) / 1_000_000


def cost_from_usage(usage: dict) -> float:
    """Cost in CNY from a DeepSeek API `usage` dict.

    When the cache hit/miss split is absent (only prompt_tokens reported),
    the whole prompt is billed at the cache-miss rate.
    """
    if not usage:
        return 0.0
    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if hit is None and miss is None:
        miss = usage.get("prompt_tokens", 0)
        hit = 0
    return deepseek_cost_cny(hit or 0, miss or 0, usage.get("completion_tokens", 0))
