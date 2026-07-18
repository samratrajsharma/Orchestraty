"""Cost-aware model routing (pure). Prices are USD per 1M tokens (input, output)."""

CATALOG = {
    "mock": {"cheap": ("mock-small", 0.0, 0.0), "capable": ("mock-large", 0.0, 0.0)},
    "openai": {"cheap": ("gpt-4o-mini", 0.15, 0.60), "capable": ("gpt-4o", 2.50, 10.00)},
    "anthropic": {"cheap": ("claude-3-5-haiku", 0.80, 4.00), "capable": ("claude-3-5-sonnet", 3.00, 15.00)},
    "ollama": {"cheap": ("llama3.2", 0.0, 0.0), "capable": ("llama3.1:70b", 0.0, 0.0)},
}
_HIGH = {"high", "complex", "hard", "capable"}


def choose(provider, complexity, override=None):
    tiers = CATALOG.get(provider, CATALOG["mock"])
    if override:
        for _provider, tier_map in CATALOG.items():
            for _tier, (name, pin, pout) in tier_map.items():
                if name == override:
                    return name, pin, pout
        return override, 0.0, 0.0
    tier = "capable" if str(complexity).lower() in _HIGH else "cheap"
    return tiers[tier]


def count_tokens(text):
    return max(1, len(text or "") // 4)


def cost(price_in_per_m, price_out_per_m, in_tok, out_tok):
    return round(in_tok / 1e6 * price_in_per_m + out_tok / 1e6 * price_out_per_m, 6)
