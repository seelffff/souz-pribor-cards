import json
import logging
from pathlib import Path

from pydantic import ValidationError

from app.completeness import detect_device_type, find_missing_fields
from app.llm.base import LLMProvider, LLMReply, LLMResponseError
from app.models import Card, Draft, KeySpec, Meta, ProductInput, ProductRef

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
DRAFT_SCHEMA = Draft.model_json_schema()
NO_DATA = "данных нет"


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def product_json(product: ProductInput) -> str:
    data = product.model_dump()
    data = {key: value for key, value in data.items() if value or key in ("name", "specs")}
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_messages(product: ProductInput) -> list[dict]:
    user = load_prompt("user").replace("{product_json}", product_json(product))
    return [
        {"role": "system", "content": load_prompt("system")},
        {"role": "user", "content": user},
    ]


def request_draft(llm: LLMProvider, messages: list[dict]) -> tuple[Draft, LLMReply]:
    reply = llm.complete_json(messages, DRAFT_SCHEMA)
    try:
        return Draft.model_validate(reply.data), reply
    except ValidationError as exc:
        raise LLMResponseError(
            f"Ответ модели не соответствует схеме: {exc.error_count()} ошибок"
        ) from exc


def generate_card(product: ProductInput, llm: LLMProvider) -> Card:
    device_type = detect_device_type(product)
    draft, reply = request_draft(llm, build_messages(product))

    key_specs = [KeySpec(name=k, value=v, source="specs") for k, v in product.specs.items()]
    key_specs += [
        KeySpec(name=fact.field, value=fact.value, source="description")
        for fact in draft.facts_from_description
    ]
    missing = find_missing_fields(device_type, [spec.name for spec in key_specs])
    key_specs += [
        KeySpec(name=field.field, value=NO_DATA, source="missing")
        for field in missing
        if field.importance == "critical"
    ]

    return Card(
        status="ok",
        product=ProductRef(name=product.name, manufacturer=product.manufacturer),
        device_type=device_type,
        seo_title=draft.seo_title,
        seo_description=draft.seo_description,
        key_specs=key_specs,
        search_queries=draft.search_queries,
        commercial_block=draft.commercial_block,
        missing_fields=missing,
        clarifications=draft.clarifications,
        warnings=[],
        meta=Meta(
            provider=llm.name,
            model=llm.model,
            attempts=1,
            latency_ms=reply.latency_ms,
            tokens=reply.tokens,
            cost_usd=reply.cost_usd,
        ),
    )
