from app.models import Card

_SOURCE_LABELS = {"specs": "характеристики", "description": "описание", "missing": "—"}
_IMPORTANCE_LABELS = {"critical": "критично", "recommended": "желательно"}


def render_markdown(card: Card) -> str:
    lines = [f"# {card.seo_title}", ""]
    if card.status == "needs_review":
        lines += ["> **Требует проверки:** автоматическая проверка нашла замечания, см. ниже.", ""]
    lines += [
        f"{card.product.name} · {card.product.manufacturer or 'производитель не указан'}",
        "",
        "## Описание",
        "",
        card.seo_description,
        "",
        "## Ключевые характеристики",
        "",
        "| Характеристика | Значение | Источник |",
        "|---|---|---|",
    ]
    lines += [
        f"| {_cell(s.name)} | {_cell(s.value)} | {_SOURCE_LABELS[s.source]} |"
        for s in card.key_specs
    ]
    lines += ["", "## Поисковые запросы", ""]
    lines += [f"- {query}" for query in card.search_queries]

    block = card.commercial_block
    lines += ["", "## Для менеджера", "", f"Кому подходит: {block.audience}", "", "Задачи:"]
    lines += [f"- {task}" for task in block.tasks]
    lines += ["", f"Типовое применение приборов этого класса: {block.typical_applications_note}"]

    if card.missing_fields:
        lines += ["", "## Недостающие данные", ""]
        lines += [f"- {m.field} — {_IMPORTANCE_LABELS[m.importance]}" for m in card.missing_fields]
    if card.clarifications:
        lines += ["", "## Требует уточнения", ""]
        lines += [f"- {item}" for item in card.clarifications]
    if card.warnings:
        lines += ["", "## Замечания проверки", ""]
        lines += [f"- {item}" for item in card.warnings]

    meta = card.meta
    lines += [
        "",
        "---",
        "",
        f"Заголовок: {len(card.seo_title)} симв. · описание: {len(card.seo_description)} симв. · "
        f"модель: {meta.model} · попыток: {meta.attempts}",
        "",
    ]
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
