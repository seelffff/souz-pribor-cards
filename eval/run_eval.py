import argparse
import datetime
import json
import logging
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from app.generator import generate_card
from app.llm.base import LLMError, LLMProvider, provider_from_env
from app.models import Card, ProductInput, parse_product_json
from app.normalize import extract_tokens
from app.validators import build_source_context

ROOT = Path(__file__).parent.parent
EXAMPLES = ["manometer_dm02", "mp3u_manotom", "pd100_feed_only", "injection"]
# Выдумки — то, что валидатор ловит как неподтверждённое значение или термин.
FABRICATION_KINDS = ("value", "term", "optional", "spelling")


def injected_tokens(product: ProductInput) -> set[str]:
    """Коды и значения, которые есть только во фразе-инструкции, а не в остальном входе."""
    source = build_source_context(product)
    tokens = set()
    for sentence in source.suspicious_sentences:
        found = extract_tokens(sentence)
        tokens |= {c for c in found.codes if c not in source.tokens.codes}
        tokens |= {
            f"{n} {u}" for n, u in found.quantities if (n, u) not in source.tokens.quantities
        }
    return tokens


def leaked(card: Card, tokens: set[str]) -> set[str]:
    """Значения из фразы-инструкции в любом тексте карточки, кроме замечаний проверки."""
    block = card.commercial_block
    texts = [card.seo_title, card.seo_description, *card.search_queries, block.audience]
    texts += [*block.tasks, block.typical_applications_note, *card.clarifications]
    found = extract_tokens(" ".join(texts))
    return tokens & (found.codes | {f"{n} {u}" for n, u in found.quantities})


def run_once(name: str, product: ProductInput, llm: LLMProvider) -> dict:
    try:
        card = generate_card(product, llm)
    except LLMError as exc:
        return {"example": name, "error": str(exc)}
    return {"example": name, "card": card.model_dump()}


def summarize(name: str, runs: list[dict], trap: set[str]) -> dict:
    cards = [Card.model_validate(r["card"]) for r in runs if "card" in r]
    counts = [c.meta.violation_counts for c in cards]
    row = {
        "example": name,
        "runs": len(runs),
        "errors": len(runs) - len(cards),
        "ok_first": sum(c.status == "ok" and c.meta.attempts == 1 for c in cards),
        "ok_final": sum(c.status == "ok" for c in cards),
        "attempts": _mean([c.meta.attempts for c in cards]),
        "caught": sum(n for vc in counts for k, n in vc.items() if k in FABRICATION_KINDS),
        "format": sum(n for vc in counts for k, n in vc.items() if k not in FABRICATION_KINDS),
        "desc_len": _mean([len(c.seo_description) for c in cards]),
        "latency_s": _mean([c.meta.latency_ms / 1000 for c in cards]),
        "cost": _mean([c.meta.cost_usd or 0 for c in cards]),
    }
    if trap:
        row["trap"] = f"{sum(not leaked(c, trap) for c in cards)}/{len(cards)}"
    return row


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def render_table(rows: list[dict], model: str, runs: int, prompt_version: str) -> str:
    lines = [
        f"Модель `{model}`, промпт {prompt_version}, {runs} прогонов на пример, "
        f"{datetime.date.today():%d.%m.%Y}.",
        "",
        "| Пример | ok с 1-й попытки | ok после repair | ср. вызовов | поймано выдумок | "
        "прочие замечания | ср. длина описания | ср. время, с | ср. цена, $ | ловушка |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        errors = f" (ошибок API: {r['errors']})" if r["errors"] else ""
        lines.append(
            f"| {r['example']}{errors} | {r['ok_first']}/{r['runs']} | "
            f"{r['ok_final']}/{r['runs']} | "
            f"{r['attempts']:.1f} | {r['caught']} | {r['format']} | {r['desc_len']:.0f} | "
            f"{r['latency_s']:.1f} | {r['cost']:.4f} | {r.get('trap', '—')} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--examples", default=",".join(EXAMPLES))
    parser.add_argument("--prompt-version", default="v3")
    parser.add_argument("--out", type=Path, default=ROOT / "eval" / "results.md")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.WARNING)
    llm = provider_from_env()
    names = args.examples.split(",")
    products = {
        n: parse_product_json(
            (ROOT / "examples" / "input" / f"{n}.json").read_text(encoding="utf-8")
        )
        for n in names
    }
    jobs = [(n, i) for n in names for i in range(args.runs)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda job: run_once(job[0], products[job[0]], llm), jobs))

    rows = [
        summarize(n, [r for r in results if r["example"] == n], injected_tokens(products[n]))
        for n in names
    ]
    table = render_table(rows, llm.model, args.runs, args.prompt_version)
    print(table)
    args.out.write_text(table + "\n", encoding="utf-8")
    raw = args.out.with_suffix(".jsonl")
    raw.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
