import pytest

from llmops_agent import prompts, providers, routing


def test_routing_tiers():
    assert routing.choose("mock", "low") == ("mock-small", 0.0, 0.0)
    assert routing.choose("openai", "high") == ("gpt-4o", 2.5, 10.0)
    assert routing.choose("openai", "low")[0] == "gpt-4o-mini"


def test_routing_override_and_cost():
    assert routing.choose("openai", "low", override="gpt-4o") == ("gpt-4o", 2.5, 10.0)
    assert routing.count_tokens("abcd" * 10) == 10
    assert routing.cost(2.5, 10.0, 1000, 500) == round(1000 / 1e6 * 2.5 + 500 / 1e6 * 10.0, 6)


def test_prompts_render_and_versions():
    assert "hello" in prompts.render("summarize", None, {"text": "hello"})
    assert prompts.latest_version("summarize") == "2"


def test_prompts_errors():
    with pytest.raises(prompts.PromptError):
        prompts.render("nope", None, {})
    with pytest.raises(prompts.PromptError):
        prompts.render("summarize", "9", {})
    with pytest.raises(prompts.PromptError):
        prompts.render("summarize", "1", {})


def test_providers_mock():
    assert providers.mock_complete("mock-small", "hi").startswith("[mock-small]")
