"""Provider-independent LLM adapter. Both classes expose `async json(system, prompt, schema, effort) -> dict`."""
import json

import httpx
from anthropic import AsyncAnthropic

from app.config import settings


class LLMError(Exception):
    """Refusal or truncated output: not retryable, the post gets skipped."""


class AnthropicLLM:
    def __init__(self, model: str, api_key: str):
        self.client = AsyncAnthropic(api_key=api_key or None)  # None -> SDK resolves env/profile credentials
        self.model = model

    async def json(self, system: str, prompt: str, schema: dict, effort: str = "low") -> dict:
        r = await self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            # Server-side refusal fallback: a declined request is re-run on a recommended model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if r.stop_reason in ("refusal", "max_tokens"):
            raise LLMError(r.stop_reason)
        return json.loads(next(b.text for b in r.content if b.type == "text"))


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model

    async def json(self, system: str, prompt: str, schema: dict, effort: str = "low") -> dict:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                    "response_format": {"type": "json_schema",
                                        "json_schema": {"name": "result", "strict": True, "schema": schema}},
                },
            )
        r.raise_for_status()
        choice = r.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LLMError("max_tokens")
        return json.loads(choice["message"]["content"])


def get_llm() -> AnthropicLLM | OpenAICompatLLM:
    if settings.LLM_PROVIDER == "openai_compat":
        return OpenAICompatLLM(settings.OPENAI_COMPAT_BASE_URL, settings.OPENAI_COMPAT_API_KEY, settings.LLM_MODEL)
    return AnthropicLLM(settings.LLM_MODEL, settings.ANTHROPIC_API_KEY)
