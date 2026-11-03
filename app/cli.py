import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.generator import generate_card
from app.llm.base import LLMError, provider_from_env
from app.models import Card, InputError, ProductInput, parse_product_json
from app.render import render_markdown
from app.yml import offer_to_product


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="Черновик карточки товара")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="карточка по JSON-файлу товара")
    generate.add_argument("input", type=Path, help="JSON с name, manufacturer, specs, description")

    from_yml = commands.add_parser("from-yml", help="карточка по офферу из YML-фида магазина")
    from_yml.add_argument("feed", type=Path, help="YML-фид (feed.xml)")
    from_yml.add_argument("--offer-id", required=True)
    from_yml.add_argument("--save-input", type=Path, help="сохранить входной JSON оффера")

    for command in (generate, from_yml):
        command.add_argument("--format", choices=["md", "json"], default="md")
        command.add_argument("--judge", action="store_true", help="доп. проверка LLM-судьёй")
        command.add_argument("--out", type=Path, help="записать в файл вместо stdout")
        command.add_argument("--out-dir", type=Path, help="записать <имя>.json и <имя>.md в папку")

    args = parser.parse_args(argv)
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Строки HTTP-запросов SDK только мешают читать лог нарушений.
    logging.getLogger("httpx2").setLevel(logging.WARNING)

    try:
        product = load_product(args)
        card = generate_card(product, provider_from_env(), judge=args.judge)
        write_output(card, args)
    except OSError as exc:
        print(f"Не удалось открыть или записать {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 2
    except InputError as exc:
        print(exc, file=sys.stderr)
        return 2
    except LLMError as exc:
        print(f"Ошибка LLM: {exc}", file=sys.stderr)
        return 1
    if card.status == "needs_review":
        print("Карточка требует проверки: см. раздел замечаний.", file=sys.stderr)
    return 0


def load_product(args: argparse.Namespace) -> ProductInput:
    if args.command == "generate":
        return parse_product_json(args.input.read_bytes())
    product = offer_to_product(args.feed, args.offer_id)
    if args.save_input:
        args.save_input.write_text(product.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return product


def write_output(card: Card, args: argparse.Namespace) -> None:
    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        stem = args.input.stem if args.command == "generate" else f"offer_{args.offer_id}"
        (args.out_dir / f"{stem}.json").write_text(card.model_dump_json(indent=2) + "\n", "utf-8")
        (args.out_dir / f"{stem}.md").write_text(render_markdown(card), "utf-8")
        return
    output = render_markdown(card) if args.format == "md" else card.model_dump_json(indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
