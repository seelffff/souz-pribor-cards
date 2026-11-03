import json
import re

from app.llm.base import LLMReply

_PADDING = [
    "Карточка подготовлена по данным поставщика. Параметры, которых нет в таблице "
    "характеристик, перед заказом стоит уточнить у менеджера.",
    "Перед подбором сверьте параметры рабочей среды и условия монтажа "
    "с технической документацией производителя.",
    "Модификацию и комплектность поставки можно согласовать с менеджером при оформлении заказа.",
    "Для коммерческого предложения укажите количество приборов, модификацию и желаемый срок "
    "поставки.",
    "Наличие на складе, сроки отгрузки и условия доставки менеджер подтвердит вместе со счётом.",
    "Если нужна замена установленного прибора, пришлите его маркировку или фото шильдика: "
    "менеджер подберёт вариант по параметрам.",
    "Если прибор нужен для замены, укажите в запросе место установки и рабочие условия: "
    "это поможет сверить модификацию до отгрузки.",
    "Счёт и сопроводительные документы менеджер пришлёт вместе с коммерческим предложением.",
    "Это черновой текст: он собран без обращения к языковой модели и нужен для проверки "
    "работы сервиса без ключа доступа.",
]


class MockProvider:
    """Детерминированный ответ без сети. С replies отдаёт заранее заданные ответы (для тестов)."""

    name = "mock"
    model = "mock"

    def __init__(self, replies: list | None = None):
        self.replies = list(replies) if replies is not None else None
        self.calls: list[list[dict]] = []

    def complete_json(self, messages: list[dict], schema: dict) -> LLMReply:
        if self.replies is not None:
            self.calls.append(messages)
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            data = reply
        elif "claims" in schema.get("properties", {}):
            data = {"claims": []}
        else:
            data = _draft_from_prompt(messages)
        return LLMReply(data=data, tokens=0, cost_usd=0.0, latency_ms=0)


def _draft_from_prompt(messages: list[dict]) -> dict:
    # Mock видит то же, что и настоящая модель, — промпт с блоком <product_data>.
    first_user = next(m["content"] for m in messages if m["role"] == "user")
    match = re.search(r"<product_data>\s*(\{.*\})\s*</product_data>", first_user, re.S)
    product = json.loads(match.group(1))
    name, manufacturer = product["name"], product.get("manufacturer", "")

    title = f"{name} {manufacturer}".strip()
    if len(title) > 70:
        title = name[:70].rstrip()

    intro = name + (f", производитель {manufacturer}." if manufacturer else ".")
    parts = [intro]
    specs = [f"{key} — {value}" for key, value in product.get("specs", {}).items()]
    while specs and len(intro) + len("; ".join(specs)) > 700:
        specs.pop()
    if specs:
        parts.append("Характеристики по данным поставщика: " + "; ".join(specs) + ".")
    for sentence in _PADDING:
        if sum(len(p) + 1 for p in parts) >= 820:
            break
        parts.append(sentence)

    short = name.split()[0].lower()
    return {
        "seo_title": title,
        "seo_description": " ".join(parts),
        "search_queries": list(
            dict.fromkeys(
                [
                    name.lower(),
                    f"{short} {manufacturer}".lower().strip(),
                    f"{short} купить",
                    f"{short} цена",
                ]
            )
        )[:4],
        "commercial_block": {
            "audience": "Инженеры КИПиА, службы главного механика, отделы снабжения",
            "tasks": [
                "Подбор прибора по параметрам из карточки",
                "Замена установленного прибора на аналогичный",
            ],
            "typical_applications_note": (
                "Типовые области применения для этого класса приборов уточняются у менеджера."
            ),
        },
        "facts_from_description": [],
        "clarifications": [],
    }
