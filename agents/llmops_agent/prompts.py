"""Versioned prompt templates (pure)."""

PROMPTS = {
    "summarize": {
        "1": "Summarize the following text:\n\n{text}",
        "2": "Summarize concisely in no more than 3 sentences:\n\n{text}",
    },
    "classify": {
        "1": "Classify the sentiment (positive, negative, or neutral):\n\n{text}",
    },
}


class PromptError(ValueError):
    pass


def latest_version(template):
    versions = PROMPTS.get(template)
    if not versions:
        raise PromptError(f"unknown template '{template}'")
    return max(versions)


def render(template, version, variables):
    versions = PROMPTS.get(template)
    if versions is None:
        raise PromptError(f"unknown template '{template}'")
    key = str(version) if version else max(versions)
    tmpl = versions.get(key)
    if tmpl is None:
        raise PromptError(f"unknown version '{version}' for template '{template}'")
    try:
        return tmpl.format(**(variables or {}))
    except KeyError as exc:
        raise PromptError(f"missing variable {exc}")
