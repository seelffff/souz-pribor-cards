import os
from dataclasses import dataclass
from typing import Protocol


class LLMError(Exception):
    """Ошибка обращения к модели; текст сообщения показывается пользователю."""


class LLMResponseError(LLMError):
    """Модель ответила, но не JSON по схеме. Такой ответ имеет смысл запросить повторно."""


@dataclass
class LLMReply:
    data: dict
    tokens: int
    cost_usd: float | None
    latency_ms: int


class LLMProvider(Protocol):
    name: str
    model: str

    def complete_json(self, messages: list[dict], schema: dict) -> LLMReply: ...


def provider_from_env() -> LLMProvider:
    from app.llm.mock import MockProvider
    from app.llm.openai_compat import OpenAICompatProvider

    provider = os.getenv("LLM_PROVIDER", "mock")
    if provider == "mock":
        return MockProvider()
    if provider == "openai_compat":
        api_key = os.getenv("LLM_API_KEY", "")
        if not api_key:
            raise LLMError("Не задан LLM_API_KEY в .env (или выберите LLM_PROVIDER=mock)")
        return OpenAICompatProvider(
            base_url=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=api_key,
            model=os.getenv("LLM_MODEL", "deepseek/deepseek-v4.1-flash"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.2")),
            timeout=float(os.getenv("LLM_TIMEOUT", "60")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "4000")),
            response_format=os.getenv("LLM_RESPONSE_FORMAT", "json_schema"),
            reasoning_effort=os.getenv("LLM_REASONING_EFFORT") or None,
        )
    raise LLMError(f"Неизвестный LLM_PROVIDER={provider!r}: допустимы mock и openai_compat")
