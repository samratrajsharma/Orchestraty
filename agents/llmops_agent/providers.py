"""LLM provider adapters. Mock is default + offline; real ones opt-in via env."""

import os


def mock_complete(model, prompt):
    snippet = (prompt or "").strip().replace("\n", " ")
    if len(snippet) > 160:
        snippet = snippet[:160] + "..."
    return f"[{model}] {snippet}"


async def complete(provider, model, prompt):
    provider = (provider or "mock").lower()
    if provider == "openai":
        return await _openai(model, prompt)
    if provider == "anthropic":
        return await _anthropic(model, prompt)
    if provider == "ollama":
        return await _ollama(model, prompt)
    return mock_complete(model, prompt)


async def _openai(model, prompt):
    import httpx

    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return mock_complete(model, prompt)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def _anthropic(model, prompt):
    import httpx

    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return mock_complete(model, prompt)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            json={"model": model, "max_tokens": 1024,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]


async def _ollama(model, prompt):
    import httpx

    base = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{base}/api/generate",
                         json={"model": model, "prompt": prompt, "stream": False})
        r.raise_for_status()
        return r.json().get("response", "")
