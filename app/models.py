import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class InputError(ValueError):
    """Некорректные входные данные; текст сообщения показывается пользователю."""


class ProductInput(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    manufacturer: str = ""
    type: str = ""
    category: str = ""
    specs: dict[str, str] = Field(default_factory=dict)
    description: str = Field(default="", max_length=20_000)

    @field_validator("manufacturer", "type", "category", "description", mode="before")
    @classmethod
    def none_to_empty(cls, value):
        return "" if value is None else value

    @field_validator("specs", mode="before")
    @classmethod
    def coerce_specs(cls, value):
        # В выгрузках встречаются и словарь, и список пар, и список {"name", "value"}.
        if value is None:
            return {}
        if isinstance(value, dict):
            items = value.items()
        elif isinstance(value, list):
            items = [_spec_pair(item) for item in value]
        else:
            raise ValueError("ожидается объект {характеристика: значение} или список пар")
        specs = {}
        for key, val in items:
            key = str(key).strip()
            val = "" if val is None else str(val).strip()
            if key and val:
                specs[key] = val
        return specs


def _spec_pair(item) -> tuple:
    if isinstance(item, dict) and "name" in item:
        return item["name"], item.get("value")
    if isinstance(item, list | tuple) and len(item) == 2:
        return item[0], item[1]
    raise ValueError(f"не удалось разобрать элемент specs: {item!r}")


def parse_product_json(raw: str | bytes) -> ProductInput:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputError(
            f"Некорректный JSON: {exc.msg} (строка {exc.lineno}, столбец {exc.colno})"
        ) from exc
    if not isinstance(data, dict):
        raise InputError("Ожидается JSON-объект с полями name, manufacturer, specs, description")
    try:
        return ProductInput.model_validate(data)
    except ValidationError as exc:
        raise InputError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    messages = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"]) or "вход"
        if error["type"] == "missing":
            messages.append(f"{field}: обязательное поле")
        elif error["type"] == "string_too_short":
            messages.append(f"{field}: поле пустое")
        elif error["type"] == "string_too_long":
            limit = error["ctx"]["max_length"]
            messages.append(f"{field}: слишком длинный текст (больше {limit} символов)")
        else:
            messages.append(f"{field}: {error['msg'].removeprefix('Value error, ')}")
    return "Ошибка во входных данных: " + "; ".join(messages)


class CommercialBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audience: str
    tasks: list[str]
    typical_applications_note: str


class DescriptionFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    value: str
    source_quote: str


class Draft(BaseModel):
    """Ответ модели. key_specs сюда намеренно не входит — его собирает код."""

    model_config = ConfigDict(extra="forbid")

    seo_title: str
    seo_description: str
    search_queries: list[str]
    commercial_block: CommercialBlock
    facts_from_description: list[DescriptionFact]
    clarifications: list[str]


class JudgeClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    verdict: Literal["supported", "generic", "unsupported"]
    quote: str


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[JudgeClaim]


class KeySpec(BaseModel):
    name: str
    value: str
    source: Literal["specs", "description", "missing"]


class MissingField(BaseModel):
    field: str
    importance: Literal["critical", "recommended"]


class ProductRef(BaseModel):
    name: str
    manufacturer: str


class Meta(BaseModel):
    provider: str
    model: str
    attempts: int
    latency_ms: int
    tokens: int
    cost_usd: float | None = None
    violation_counts: dict[str, int] = Field(default_factory=dict)
    violations_by_attempt: list[list[str]] = Field(default_factory=list)


class Card(BaseModel):
    status: Literal["ok", "needs_review"]
    product: ProductRef
    device_type: str
    seo_title: str
    seo_description: str
    key_specs: list[KeySpec]
    search_queries: list[str]
    commercial_block: CommercialBlock
    missing_fields: list[MissingField]
    clarifications: list[str]
    warnings: list[str]
    meta: Meta
