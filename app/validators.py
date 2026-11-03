import logging
import re
from dataclasses import dataclass, field

from app.models import DescriptionFact, Draft, ProductInput
from app.normalize import Tokens, extract_tokens, normalize_key, normalize_text, unit_from_key

log = logging.getLogger(__name__)

TITLE_MAX = 70
DESCRIPTION_MIN, DESCRIPTION_MAX = 800, 1200
QUERIES_MIN, QUERIES_MAX = 3, 5

# Нечисловые утверждения, которые модель охотно добавляет «из знаний» о приборе.
# Термин в ответе допустим, только если тот же шаблон находится во входных данных.
RISKY_TERMS = {
    "нержавеющая сталь": r"\bнержаве",
    "сталь": r"\bсталь|\bстальн|\bстали\b",
    "латунь": r"\bлатун",
    "бронза": r"\bбронз",
    "медный сплав": r"\bмедн",
    "алюминий": r"\bалюмини",
    "пластик": r"\bполистирол|\bпластик|\bполикарбонат|\bполиамид",
    "гидрозаполнение": r"\bглицерин|\bгидрозаполн|\bзаполненн\w* жидкост|\bжидкостн\w* заполн",
    "взрывозащита": r"\bвзрыво|\bexd\b|\bexia?\b|\bискробезопас",
    "кислородное исполнение": r"\bкислород",
    "аммиачное исполнение": r"\bаммиа",
    "ацетилен": r"\bацетилен",
    "хладоны": r"\bхладон|\bфреон",
    "агрессивные среды": r"\bагрессивн",
    "коррозионная стойкость": r"\bкоррози|\bантикоррози",
    "виброзащита": r"\bвибро",
    "ударопрочность": r"\bударопрочн|\bударостойк",
    "радиальный штуцер": r"\bрадиальн",
    "осевой штуцер": r"\bосев|\bтыльн|\bторцев",
    "фланец": r"\bфланц|\bфланец",
    "пыле- и влагозащита": r"\bпылевлаг|\bвлагозащ|\bпылезащ|\bгерметичн",
    "поверка": r"поверк|поверен|поверочн",
    "госреестр и сертификаты": r"реестр|\bсертифи|\bдеклараци|\bтр тс\b|\bеаэс",
    "ГОСТ и ТУ": r"\bгост|\bту\b",
    "гарантия": r"\bгаранти",
    "срок службы": r"\bсрок\w* службы|\bресурс",
    "климатическое исполнение": r"\bклиматическ|\bухл\d|\bморозост|\bтропическ",
    "температура": r"\bтемператур|°c",
    "питание": r"\bпитани|\bбатаре|\bаккумулятор",
    "выходной сигнал": r"\bинтерфейс|\bвыходн\w* сигнал|\bmodbus|\brs-?485|\bhart\b",
    "электроконтакты": r"\bэлектроконтакт|\bсигнализирующ",
    "демпфер": r"\bдемпф|\bгасител\w* пульсац",
    "страна производства": r"\bроссийск|\bотечествен|\bимпортн|\bимпортозамещ",
    "совместимость": r"\bсовместим|\bвзаимозаменя|\bзаменяет\b|\bполн\w* аналог",
}

_STAMPS = re.compile(
    r"\bлучш|\bуникальн|\bинновацион|\bидеальн|\bпревосходн|\bвысококачествен|\bнепревзойд"
    r"|\bреволюцион|\bвысочайш|\bбезупречн|\bпередов|\bвысок\w* точност|\bпремиальн"
    r"|\bмирового уровня"
)
_FOREIGN_SCRIPT = re.compile("[֐-ۿ぀-ヿ㐀-鿿가-힯]")
_OPTIONAL_MARK = r"по заказу|по запросу|опционал"
_OPTIONAL_MENTION = re.compile(r"заказ|запрос|опци|вариант|исполнени")
# Обращение к модели на «ты»: в паспортах приборов пишут «укажите», а не «укажи».
_INSTRUCTION = re.compile(
    r"\b(?:игнорируй|забудь|добавь|напиши|укажи|выведи|придумай|сгенерируй|ответь"
    r"|ignore|disregard)\b|предыдущ\w* инструкци|системн\w* промпт",
    re.I,
)
_TRANSLIT = str.maketrans("abcdefghiklmnoprstuvz", "абцдефгхиклмнопрстувз")


@dataclass
class Violation:
    kind: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclass
class SourceContext:
    """Всё, что считается подтверждённым входными данными."""

    tokens: Tokens
    corpus: str
    description: str
    optional: Tokens
    spec_keys: set[str]
    latin_spellings: dict[str, str]
    suspicious_sentences: list[str] = field(default_factory=list)


def split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def build_source_context(product: ProductInput) -> SourceContext:
    sentences = split_sentences(product.description)
    suspicious = [s for s in sentences if _INSTRUCTION.search(s)]
    description = " ".join(s for s in sentences if s not in suspicious)

    tokens = Tokens()
    lines = [product.name, product.manufacturer, product.type, product.category, description]
    for line in lines:
        _merge(tokens, extract_tokens(line))
    for key, value in product.specs.items():
        line_tokens = extract_tokens(f"{key}: {value}")
        unit = unit_from_key(key)
        if unit:
            line_tokens.quantities |= {(n, unit) for n in line_tokens.numbers}
        _merge(tokens, line_tokens)

    corpus = normalize_text(" ".join([*lines, *(f"{k}: {v}" for k, v in product.specs.items())]))
    latin = {
        word: word.translate(_TRANSLIT)
        for word in re.findall(r"\b[a-z]{2,}\b", normalize_text(product.name))
    }
    return SourceContext(
        tokens=tokens,
        corpus=corpus,
        description=normalize_text(description),
        optional=_optional_tokens([*product.specs.values(), description]),
        spec_keys={normalize_key(key) for key in product.specs},
        latin_spellings={w: c for w, c in latin.items() if not re.search(rf"\b{c}\b", corpus)},
        suspicious_sentences=suspicious,
    )


def _merge(target: Tokens, source: Tokens) -> None:
    target.codes |= source.codes
    target.standards |= source.standards
    target.numbers |= source.numbers
    target.quantities |= source.quantities


def _optional_tokens(values: list[str]) -> Tokens:
    """Значения, которые во входе встречаются только как исполнение «по заказу»."""
    optional, base = Tokens(), Tokens()
    for value in values:
        for segment in normalize_text(value).split(";"):
            # «IP 40 (по заказу IP-53, IP-54)»: до скобки базовое исполнение, в скобке опции.
            paren = re.search(rf"\((?:[^)]*?)(?:{_OPTIONAL_MARK})([^)]*)\)", segment)
            if paren:
                _merge(optional, extract_tokens(paren.group(1)))
                _merge(base, extract_tokens(segment[: paren.start()] + segment[paren.end() :]))
            elif re.search(_OPTIONAL_MARK, segment):
                _merge(optional, extract_tokens(segment))
            else:
                _merge(base, extract_tokens(segment))
    return Tokens(
        codes=optional.codes - base.codes,
        standards=optional.standards - base.standards,
        numbers=optional.numbers - base.all_numbers(),
        quantities=optional.quantities - base.quantities,
    )


def text_fields(draft: Draft) -> dict[str, str]:
    block = draft.commercial_block
    return {
        "seo_title": draft.seo_title,
        "seo_description": draft.seo_description,
        "search_queries": "\n".join(draft.search_queries),
        "commercial_block": "\n".join(
            [block.audience, *block.tasks, block.typical_applications_note]
        ),
        "clarifications": "\n".join(draft.clarifications),
    }


def validate_draft(draft: Draft, source: SourceContext) -> list[Violation]:
    violations = check_lengths(draft)
    for name, text in text_fields(draft).items():
        violations += find_unknown_values(text, source, name)
        violations += check_spelling(text, source, name)
        if name != "clarifications":
            # В уточнениях модель законно спрашивает про материал или поверку.
            violations += find_unknown_terms(text, source, name)
            violations += check_text_purity(text, source, name)
        if name in ("seo_title", "seo_description"):
            violations += check_optional_marked(text, source, name)
    return violations


def check_lengths(draft: Draft) -> list[Violation]:
    violations = []
    title_len = len(draft.seo_title)
    if not 0 < title_len <= TITLE_MAX:
        violations.append(
            Violation("length", "seo_title", f"длина {title_len} символов, нужно до {TITLE_MAX}")
        )
    desc_len = len(draft.seo_description)
    if not DESCRIPTION_MIN <= desc_len <= DESCRIPTION_MAX:
        violations.append(
            Violation(
                "length",
                "seo_description",
                f"длина {desc_len} символов, нужно {DESCRIPTION_MIN}–{DESCRIPTION_MAX}",
            )
        )
    queries = [normalize_text(q) for q in draft.search_queries if q.strip()]
    if not QUERIES_MIN <= len(queries) <= QUERIES_MAX:
        violations.append(
            Violation(
                "queries",
                "search_queries",
                f"{len(queries)} запросов, нужно {QUERIES_MIN}–{QUERIES_MAX}",
            )
        )
    if len(set(queries)) != len(queries):
        violations.append(Violation("queries", "search_queries", "есть повторяющиеся запросы"))
    return violations


def find_unknown_values(text: str, source: SourceContext, field_name: str) -> list[Violation]:
    found = extract_tokens(text)
    allowed = source.tokens
    allowed_numbers = allowed.all_numbers()
    unknown = [code.upper() for code in found.codes if not _code_allowed(code, allowed.codes)]
    unknown += [s.upper() for s in found.standards if not _code_allowed(s, allowed.standards)]
    unknown += [f"{n} {u}" for n, u in found.quantities if (n, u) not in allowed.quantities]
    unknown += [n for n in found.numbers if n not in allowed_numbers]
    return [
        Violation("value", field_name, f"значение «{value}» отсутствует во входных данных")
        for value in sorted(unknown)
    ]


def _code_allowed(code: str, allowed: set[str]) -> bool:
    # «G1/2» допустимо при «G1/2-B» во входе, «ПД10» при «ПД100» — нет.
    return any(a == code or (a.startswith(code) and a[len(code)] in "-./x") for a in allowed)


def find_unknown_terms(text: str, source: SourceContext, field_name: str) -> list[Violation]:
    normalized = normalize_text(text)
    violations = []
    for label, pattern in RISKY_TERMS.items():
        match = re.search(pattern, normalized)
        if match and not re.search(pattern, source.corpus):
            word = re.search(r"\S*" + re.escape(match.group()) + r"\S*", normalized).group()
            violations.append(
                Violation(
                    "term",
                    field_name,
                    f"«{word.strip('.,;:()')}» ({label}) не подтверждено входными данными",
                )
            )
    return violations


def check_spelling(text: str, source: SourceContext, field_name: str) -> list[Violation]:
    normalized = normalize_text(text)
    return [
        Violation(
            "spelling",
            field_name,
            f"«{cyrillic.upper()}» вместо «{latin.upper()}» — сохраните написание из входа",
        )
        for latin, cyrillic in source.latin_spellings.items()
        if re.search(rf"\b{cyrillic}\b", normalized)
    ]


def check_text_purity(text: str, source: SourceContext, field_name: str) -> list[Violation]:
    violations = []
    for char in sorted(set(_FOREIGN_SCRIPT.findall(text))):
        violations.append(Violation("text", field_name, f"посторонний символ «{char}»"))
    normalized = normalize_text(text)
    for word in sorted(set(re.findall(r"\b[a-z]{3,}\b", normalized))):
        if not re.search(rf"\b{word}\b", source.corpus):
            violations.append(
                Violation("text", field_name, f"латинское слово «{word}» отсутствует во входе")
            )
    for match in sorted({m.group() for m in _STAMPS.finditer(normalized)}):
        violations.append(Violation("style", field_name, f"рекламный штамп «{match}…»"))
    return violations


def check_optional_marked(text: str, source: SourceContext, field_name: str) -> list[Violation]:
    optional = source.optional
    violations = []
    for sentence in split_sentences(text):
        tokens = extract_tokens(sentence)
        hits = [c.upper() for c in tokens.codes & optional.codes]
        hits += [f"{n} {u}" for n, u in tokens.quantities & optional.quantities]
        if hits and not _OPTIONAL_MENTION.search(normalize_text(sentence)):
            violations.append(
                Violation(
                    "optional",
                    field_name,
                    f"«{', '.join(sorted(hits))}» во входе — исполнение по заказу; "
                    "в тексте нужна пометка «по заказу»",
                )
            )
    return violations


def check_facts(
    facts: list[DescriptionFact], source: SourceContext
) -> tuple[list[DescriptionFact], list[str]]:
    """Оставляет факты, чья цитата дословно есть в description, а значения — в цитате."""
    accepted, warnings = [], []
    for fact in facts:
        if normalize_key(fact.field) in source.spec_keys:
            log.info("факт %r дублирует specs, пропущен", fact.field)
            continue
        quote = normalize_text(fact.source_quote).strip(" .…")
        if not quote or quote not in source.description:
            warnings.append(
                f"Факт «{fact.field}: {fact.value}» отброшен: цитата не найдена в описании"
            )
            continue
        if find_unknown_values(fact.value, _quote_context(quote), "facts"):
            warnings.append(
                f"Факт «{fact.field}: {fact.value}» отброшен: значение не совпадает с цитатой"
            )
            continue
        accepted.append(fact)
    return accepted, warnings


def _quote_context(quote: str) -> SourceContext:
    return SourceContext(
        tokens=extract_tokens(quote),
        corpus=quote,
        description=quote,
        optional=Tokens(),
        spec_keys=set(),
        latin_spellings={},
    )


def find_input_issues(product: ProductInput, source: SourceContext) -> list[str]:
    """Проблемы самих входных данных — уходят в clarifications до вызова модели."""
    issues = []
    if not product.specs and not product.description:
        issues.append(
            "Во входных данных нет характеристик и описания: карточка содержит только "
            "общие сведения о типе прибора. Нужны характеристики модификации."
        )
    if re.search(r"(\.\.\.|…)\s*$", product.description):
        issues.append(
            "Описание во входных данных обрывается многоточием — нужен полный текст из паспорта."
        )

    spec_tokens, desc_tokens = Tokens(), extract_tokens(source.description)
    for value in product.specs.values():
        _merge(spec_tokens, extract_tokens(value))
    for label, pattern in [
        ("Степень защиты", r"^ip\d"),
        ("Резьба присоединения", r"^(?:g|m|k|r|npt)\d"),
    ]:
        in_specs = {c for c in spec_tokens.codes if re.match(pattern, c)}
        only_in_desc = {
            c for c in desc_tokens.codes if re.match(pattern, c) and not _code_allowed(c, in_specs)
        }
        if in_specs and only_in_desc:
            issues.append(
                f"{label}: в характеристиках {_fmt(in_specs)}, в описании также "
                f"{_fmt(only_in_desc)} — уточните у производителя, какие исполнения доступны."
            )
    only_in_desc = desc_tokens.standards - spec_tokens.standards
    if spec_tokens.standards and only_in_desc:
        issues.append(
            f"Стандарт: в характеристиках {_fmt(spec_tokens.standards)}, "
            f"в описании {_fmt(only_in_desc)} — уточните, какой документ действующий."
        )

    has_decoding = any(
        re.search(r"карта заказа|расшифровк|код заказа", normalize_key(k)) for k in product.specs
    )
    for word in product.name.split():
        if _looks_like_order_code(normalize_text(word)) and not has_decoding:
            issues.append(
                f"Название содержит код заказа «{word}». Без карты заказа его параметры "
                "не расшифровывались — добавьте характеристики модификации или таблицу расшифровки."
            )
    return issues


def _looks_like_order_code(word: str) -> bool:
    if re.match(r"^(?:g|m|k|r|npt)\d", word):
        return False
    segments = re.split(r"[-.]", word)
    return len(segments) >= 3 and sum(bool(re.search(r"\d", s)) for s in segments) >= 2


def _fmt(values: set[str]) -> str:
    return ", ".join(sorted(v.upper() for v in values))
