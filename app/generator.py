import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from app.completeness import detect_device_type, find_missing_fields
from app.llm.base import LLMError, LLMProvider, LLMReply, LLMResponseError
from app.models import (
    Card,
    Draft,
    JudgeVerdict,
    KeySpec,
    Meta,
    ProductInput,
    ProductRef,
)
from app.validators import (
    Violation,
    build_source_context,
    check_facts,
    find_input_issues,
    text_fields,
    validate_draft,
)

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
DRAFT_SCHEMA = Draft.model_json_schema()
JUDGE_SCHEMA = JudgeVerdict.model_json_schema()
MAX_REPAIRS = 2
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


def repair_message(violations: list[Violation]) -> dict:
    listed = "\n".join(f"- {v}" for v in violations)
    return {"role": "user", "content": load_prompt("repair").replace("{violations}", listed)}


@dataclass
class Usage:
    calls: int = 0
    tokens: int = 0
    latency_ms: int = 0
    cost_usd: float | None = 0.0
    violation_counts: Counter = field(default_factory=Counter)
    violations_by_attempt: list[list[str]] = field(default_factory=list)

    def add(self, reply: LLMReply) -> None:
        self.calls += 1
        self.tokens += reply.tokens
        self.latency_ms += reply.latency_ms
        if self.cost_usd is not None and reply.cost_usd is not None:
            self.cost_usd += reply.cost_usd
        else:
            self.cost_usd = None


def request_draft(llm: LLMProvider, messages: list[dict]) -> tuple[Draft, LLMReply]:
    reply = llm.complete_json(messages, DRAFT_SCHEMA)
    try:
        return Draft.model_validate(reply.data), reply
    except ValidationError as exc:
        raise LLMResponseError(
            f"Ответ модели не соответствует схеме: {exc.error_count()} ошибок"
        ) from exc


def generate_card(
    product: ProductInput,
    llm: LLMProvider,
    *,
    judge: bool = False,
    max_repairs: int = MAX_REPAIRS,
) -> Card:
    """Черновик карточки: генерация, проверка кодом, до max_repairs исправлений по списку нарушений.

    Не исправленные нарушения не роняют сервис — карточка уходит со статусом needs_review.
    """
    device_type = detect_device_type(product)
    source = build_source_context(product)
    messages = build_messages(product)
    usage = Usage()
    draft, violations = None, []

    for attempt in range(1, max_repairs + 2):
        try:
            new_draft, reply = request_draft(llm, messages)
        except LLMResponseError as exc:
            log.warning("попытка %d: %s", attempt, exc)
            if draft is None and attempt == max_repairs + 1:
                raise
            continue
        usage.add(reply)
        draft = new_draft
        violations = validate_draft(draft, source)
        usage.violation_counts.update(v.kind for v in violations)
        usage.violations_by_attempt.append([str(v) for v in violations])
        if not violations:
            break
        log.info("попытка %d: %s", attempt, "; ".join(map(str, violations)))
        messages = [
            *messages,
            {"role": "assistant", "content": draft.model_dump_json()},
            repair_message(violations),
        ]

    facts, fact_warnings = check_facts(draft.facts_from_description, source)
    warnings = [str(v) for v in violations] + fact_warnings
    warnings += [
        f"В описании есть фраза, похожая на инструкцию для модели: «{s}». "
        "Она не используется как источник фактов."
        for s in source.suspicious_sentences
    ]
    if judge:
        warnings += run_judge(llm, product, draft, usage)

    key_specs = [KeySpec(name=k, value=v, source="specs") for k, v in product.specs.items()]
    key_specs += [KeySpec(name=f.field, value=f.value, source="description") for f in facts]
    missing = find_missing_fields(device_type, [spec.name for spec in key_specs])
    key_specs += [
        KeySpec(name=m.field, value=NO_DATA, source="missing")
        for m in missing
        if m.importance == "critical"
    ]
    clarifications = list(dict.fromkeys(find_input_issues(product, source) + draft.clarifications))

    return Card(
        status="needs_review" if violations or usage.violation_counts["judge"] else "ok",
        product=ProductRef(name=product.name, manufacturer=product.manufacturer),
        device_type=device_type,
        seo_title=draft.seo_title,
        seo_description=draft.seo_description,
        key_specs=key_specs,
        search_queries=draft.search_queries,
        commercial_block=draft.commercial_block,
        missing_fields=missing,
        clarifications=clarifications,
        warnings=warnings,
        meta=Meta(
            provider=llm.name,
            model=llm.model,
            attempts=usage.calls,
            latency_ms=usage.latency_ms,
            tokens=usage.tokens,
            cost_usd=usage.cost_usd,
            violation_counts=dict(usage.violation_counts),
            violations_by_attempt=usage.violations_by_attempt,
        ),
    )


def run_judge(llm: LLMProvider, product: ProductInput, draft: Draft, usage: Usage) -> list[str]:
    """Вторая фаза проверки: модель-судья ищет смысловые выдумки, которые не ловит код."""
    draft_text = "\n\n".join(
        f"{name}:\n{text}" for name, text in text_fields(draft).items() if name != "clarifications"
    )
    prompt = (
        load_prompt("judge")
        .replace("{product_json}", product_json(product))
        .replace("{draft_text}", draft_text)
    )
    try:
        reply = llm.complete_json([{"role": "user", "content": prompt}], JUDGE_SCHEMA)
        verdict = JudgeVerdict.model_validate(reply.data)
    except (LLMError, ValidationError) as exc:
        log.warning("судья не отработал: %s", exc)
        return [f"Проверка судьёй не выполнена: {exc}"]
    usage.add(reply)
    unsupported = [c.claim for c in verdict.claims if c.verdict == "unsupported"]
    usage.violation_counts["judge"] += len(unsupported)
    return [f"Судья: не подтверждено входными данными — «{claim}»" for claim in unsupported]
