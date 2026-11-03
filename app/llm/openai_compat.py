import json
import logging
import re
import time

import openai

from app.llm.base import LLMError, LLMReply, LLMResponseError

log = logging.getLogger(__name__)

_RETRYABLE = (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)


class OpenAICompatProvider:
    """OpenAI, OpenRouter, DeepSeek, Ollama — всё, что понимает /chat/completions."""

    name = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        timeout: float = 60,
        max_tokens: int = 8000,
        response_format: str = "json_schema",
        reasoning_effort: str | None = None,
        retry_delays: tuple[float, ...] = (1, 3, 8),
        http_client=None,
    ):
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.response_format = response_format
        self.reasoning_effort = reasoning_effort
        self.retry_delays = retry_delays
        # Повторы делаем сами: SDK не различает 429 и 401 в сообщениях для пользователя.
        self.client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )

    def complete_json(self, messages: list[dict], schema: dict) -> LLMReply:
        started = time.monotonic()
        response = self._create_with_retries(messages, schema)
        choice = response.choices[0]
        content = choice.message.content
        if choice.finish_reason == "length":
            raise LLMResponseError(
                "Ответ модели обрезан по LLM_MAX_TOKENS — увеличьте лимит в .env"
            )
        if not content:
            raise LLMResponseError("Модель вернула пустой ответ")
        usage = response.usage
        return LLMReply(
            data=parse_json_content(content),
            tokens=usage.total_tokens if usage else 0,
            cost_usd=(usage.model_extra or {}).get("cost") if usage else None,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def _create_with_retries(self, messages: list[dict], schema: dict):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.response_format == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "product_card", "strict": True, "schema": schema},
            }
        else:
            kwargs["response_format"] = {"type": "json_object"}
            kwargs["messages"] = [
                *messages[:-1],
                {
                    "role": "user",
                    "content": messages[-1]["content"]
                    + "\n\nОтвет — JSON по схеме:\n"
                    + json.dumps(schema, ensure_ascii=False),
                },
            ]
        if self.reasoning_effort:
            kwargs["extra_body"] = {"reasoning": {"effort": self.reasoning_effort}}

        for attempt, delay in enumerate((*self.retry_delays, None), start=1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except openai.AuthenticationError as exc:
                raise LLMError("API-ключ отклонён (401). Проверьте LLM_API_KEY в .env") from exc
            except openai.PermissionDeniedError as exc:
                raise LLMError(f"Доступ к модели запрещён (403): {_detail(exc)}") from exc
            except openai.NotFoundError as exc:
                raise LLMError(f"Модель {self.model!r} или endpoint не найдены (404)") from exc
            except openai.APIStatusError as exc:
                if exc.status_code == 402:
                    raise LLMError("Недостаточно средств на счёте провайдера (402)") from exc
                if exc.status_code < 500 and not isinstance(exc, openai.RateLimitError):
                    raise LLMError(f"Запрос отклонён ({exc.status_code}): {_detail(exc)}") from exc
                error = exc
            except _RETRYABLE as exc:
                error = exc
            if delay is None:
                break
            log.warning("LLM: %s, повтор %d через %.0f с", type(error).__name__, attempt, delay)
            time.sleep(delay)

        if isinstance(error, openai.APITimeoutError):
            raise LLMError(f"Модель не ответила за {self.timeout:.0f} с") from error
        if isinstance(error, openai.RateLimitError):
            raise LLMError("Превышен лимит запросов (429), попробуйте позже") from error
        if isinstance(error, openai.APIConnectionError):
            raise LLMError("Нет соединения с LLM API, проверьте LLM_BASE_URL и сеть") from error
        raise LLMError(f"Сервис LLM недоступен ({error.status_code})") from error


def parse_json_content(content: str) -> dict:
    # В режиме json_object часть моделей всё равно оборачивает ответ в ```json ... ```.
    content = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"Модель вернула невалидный JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise LLMResponseError("Модель вернула JSON, но не объект")
    return data


def _detail(exc: openai.APIStatusError) -> str:
    body = exc.body if isinstance(exc.body, dict) else {}
    return str(body.get("message") or exc.message)[:200]
