import logging
import re
from dataclasses import dataclass, field

from app.models import DescriptionFact, Draft, ProductInput
from app.normalize import (
    Tokens,
    extract_tokens,
    fold_code,
    fold_letters,
    is_unit,
    normalize_key,
    normalize_number,
    normalize_text,
    unit_from_key,
)

log = logging.getLogger(__name__)

TITLE_MAX = 70
DESCRIPTION_MIN, DESCRIPTION_MAX = 800, 1200
QUERIES_MIN, QUERIES_MAX = 3, 5

# Нечисловые утверждения, которые модель охотно добавляет «из знаний» о приборе.
# Термин в ответе допустим, только если тот же шаблон находится во входных данных.
RISKY_TERMS = {
    "нержавеющая сталь": r"\bнержаве",
    "сталь": r"\bсталь|\bстальн|\b(?:из|нержавеющей|углеродистой|легированной)\s+стали\b",
    "латунь": r"\bлатун",
    "бронза": r"\bбронз",
    "медный сплав": r"\bмедн|\bмед[ьи]\b",
    "алюминий": r"\bалюмини",
    "пластик": r"\bполистирол|\bпластик|\bполикарбонат|\bполиамид",
    "прочие материалы": r"\bтитан|\bчугун|\bкерамик|\bфторопласт|\bptfe\b",
    "покрытие": r"\bхромир|\bникелир|\bоцинков|\bпорошков\w* покрыт",
    "гидрозаполнение": (
        r"\bглицерин|\bгидрозаполн|\bмаслозаполн|\bмаслонаполн|\bзаполн\w* масл"
        r"|\bзаполненн\w* жидкост|\bжидкостн\w* заполн"
    ),
    "взрывозащита": r"\bвзрыво|\bexd\b|\bexia?\b|\bискробезопас",
    "кислородное исполнение": r"\bкислород",
    "аммиачное исполнение": r"\bаммиа",
    "ацетилен": r"\bацетилен",
    "хладоны": r"\bхладон|\bфреон",
    "агрессивные среды": r"\bагрессивн",
    "коррозионная стойкость": r"\bкоррози|\bантикоррози",
    "тип давления": (
        r"\bизбыточн|\bабсолютн\w* давлени|\bразрежени|\bдифференциальн|\bгидростатическ"
    ),
    "виброзащита": r"\bвибро",
    "ударопрочность": r"\bударопрочн|\bударостойк",
    "радиальный штуцер": r"\bрадиальн",
    "осевой штуцер": r"\bосев|\bтыльн|\bторцев",
    "фланец": r"\bфланц|\bфланец",
    "пыле- и влагозащита": (
        r"\bпылевлаг|\bвлагозащ|\bпылезащ|\bот пыли\b|\bбрызг|\bгерметичн\w* корпус"
    ),
    "поверка": r"поверк|поверен|поверочн|\bкалибров|\bутвержден\w* типа",
    "госреестр и сертификаты": r"реестр|\bсертифи|\bдеклараци|\bтр тс\b|\bеаэс|\beac\b|\bce\b",
    "ГОСТ и ТУ": r"\bгост|\bту\s+(?:у\s+)?\d",
    "гарантия": r"\bгаранти(?:я|и|ю|ей|йн\w*)\b",
    "срок службы": r"\bсрок\w* службы|\bресурс(?:а|ом|у)?\b",
    "климатическое исполнение": r"\bклиматическ|\bухл\d|\bморозост|\bтропическ|\bуличн",
    "температура": r"\bтемператур|°c",
    "питание": r"\bпитани(?!\w* котл)|\bбатаре(?!\w* отоплени)|\bаккумулятор",
    "выходной сигнал": (
        r"\bинтерфейс|\bвыходн\w* сигнал|\bmodbus|\brs-?485|\bhart\b"
        r"|\bтоков\w* (?:выход|сигнал)|\bдвухпровод|\bтрехпровод"
    ),
    "электроконтакты": r"\bэлектроконтакт|\bсигнализирующ",
    "демпфер": r"\bдемпф|\bгасител\w* пульсац",
    "страна производства": r"\bроссийск|\bотечествен|\bимпортн|\bимпортозамещ",
    "совместимость": r"\bсовместим|\bвзаимозаменя|\bзаменяет\b|\bполн\w* аналог|\bвместо [a-z]",
}
_MATERIALS = {
    "нержавеющая сталь",
    "сталь",
    "латунь",
    "бронза",
    "медный сплав",
    "алюминий",
    "пластик",
}
# «на стальных трубопроводах» — про место установки, а не про материал прибора.
_PIPE_CONTEXT = re.compile(r"\w*(?:\s+\w+){0,2}\s+(?:труб|арматур|магистрал|сет)")
# «Питание и выходной сигнал уточняйте у менеджера» ничего не утверждает о приборе.
_HEDGE = re.compile(r"уточня|по паспорту|у менеджера")
_CLAIM = re.compile(
    r"\b(?:имеет|есть|проходит|прошел|поставляется|комплектуется|оснащ|выполнен|изготовлен"
    r"|обеспечива|требует|не требует)"
)

_STAMPS = re.compile(
    r"\bлучш|\bуникальн|\bинновацион|\bидеальн|\bпревосходн|\bвысококачествен|\bнепревзойд"
    r"|\bреволюцион|\bвысочайш|\bбезупречн|\bпередов|\bвысок\w* точност|\bпремиальн"
    r"|\bмирового уровня"
)
_FOREIGN_SCRIPT = re.compile("[֐-ۿ぀-ヿ㐀-鿿가-힯]")
_OPTIONAL_MARK = re.compile(r"по заказу|на заказ|по запросу|опционал")
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
    # Сегменты кодов и числа из них: в уточнениях модель законно спрашивает,
    # что значит «ДИ0,6» или «111» в коде заказа.
    code_parts: set[str] = field(default_factory=set)


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
        if not is_unit(word)
    }
    return SourceContext(
        tokens=tokens,
        corpus=corpus,
        description=normalize_text(description),
        optional=_optional_tokens([*product.specs.values(), description]),
        spec_keys={normalize_key(key) for key in product.specs},
        latin_spellings={w: c for w, c in latin.items() if not re.search(rf"\b{c}\b", corpus)},
        suspicious_sentences=suspicious,
        code_parts={part for code in tokens.codes for part in _code_parts(code)},
    )


def _code_parts(code: str) -> set[str]:
    numbers = {normalize_number(n) for n in re.findall(r"\d+(?:\.\d+)?", code)}
    return numbers | set(code.split("-"))


def _merge(target: Tokens, source: Tokens) -> None:
    target.codes |= source.codes
    target.standards |= source.standards
    target.numbers |= source.numbers
    target.quantities |= source.quantities


def _optional_tokens(values: list[str]) -> Tokens:
    """Значения, которые во входе встречаются только как исполнение «по заказу»."""
    optional, base = Tokens(), Tokens()
    for value in values:
        for sentence in split_sentences(normalize_text(value)):
            for segment in sentence.split(";"):
                optional_parts, base_parts = _split_optional(segment)
                for part in optional_parts:
                    _merge(optional, extract_tokens(part))
                for part in base_parts:
                    _merge(base, extract_tokens(part))
    return Tokens(
        codes=optional.codes - base.codes,
        standards=optional.standards - base.standards,
        numbers=optional.numbers - base.all_numbers(),
        quantities=optional.quantities - base.quantities,
    )


def _split_optional(segment: str) -> tuple[list[str], list[str]]:
    """'IP40 (по заказу IP53)', 'IP54 - по заказу', 'IP40, по заказу IP54' -> (опции, базовое)."""
    optional, base = [], []

    def take_paren(match: re.Match) -> str:
        inner = match.group(1)
        if not _OPTIONAL_MARK.search(inner):
            base.append(inner)
            return " "
        rest = _OPTIONAL_MARK.sub(" ", inner)
        if re.search(r"\d", rest):
            optional.append(rest)
            return " "
        # «IP54 (по заказу)» — пометка относится к значению перед скобкой.
        return " по заказу "

    segment = re.sub(r"\(([^()]*)\)", take_paren, segment)
    # «1.0 - по заказу, кроме МП3-У на 0.6 кгс/см²»: после «кроме» — исключение, не опция.
    segment, _, exception = segment.partition("кроме")
    base.append(exception)
    mark = _OPTIONAL_MARK.search(segment)
    if not mark:
        base.append(segment)
        return optional, base
    before, after = segment[: mark.start()], segment[mark.end() :]
    if re.search(r"\d", after):
        optional.append(after)
        base.append(before)
    else:
        head, _, item = before.rpartition(",")
        optional.append(item)
        base.append(head)
    return optional, base


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
        if name == "clarifications":
            # В уточнениях модель законно спрашивает про материал или поверку.
            continue
        violations += find_unknown_terms(text, source, name)
        violations += check_text_purity(text, source, name)
        if name != "search_queries":
            # «манометр мп3-у ip54» — нормальный запрос: прибор в IP54 действительно продаётся.
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
    allowed_codes = allowed.codes
    if field_name == "clarifications":
        allowed_numbers |= source.code_parts
        allowed_codes = allowed_codes | source.code_parts
    unknown = [code.upper() for code in found.codes if not _code_allowed(code, allowed_codes)]
    unknown += [s.upper() for s in found.standards if not _code_allowed(s, allowed.standards)]
    unknown += [
        f"{n} {u}"
        for n, u in found.quantities
        if not any((v, u) in allowed.quantities for v in _sign_variants(n))
    ]
    unknown += [
        n for n in found.numbers if not any(v in allowed_numbers for v in _sign_variants(n))
    ]
    return [
        Violation("value", field_name, f"значение «{value}» отсутствует во входных данных")
        for value in sorted(unknown)
    ]


def _sign_variants(number: str) -> list[str]:
    # «50 °С» без знака допустимо и при «минус 50 °С» во входе; «-50» при «плюс 50» — нет.
    return [number] if number.startswith("-") else [number, "-" + number]


def _code_allowed(code: str, allowed: set[str]) -> bool:
    # «G1/2» допустимо при «G1/2-B» во входе, «ПД100» при «ПД100-ДИ0,6», а «ПД10» и «G1» — нет.
    key, letters = fold_code(code), fold_letters(code)
    return any(
        fold_code(a) == key
        or (fold_letters(a).startswith(letters) and a[len(code) : len(code) + 1] == "-")
        for a in allowed
    )


def find_unknown_terms(text: str, source: SourceContext, field_name: str) -> list[Violation]:
    violations = []
    claims = [s for s in split_sentences(normalize_text(text)) if not _is_hedge(s)]
    for label, pattern in RISKY_TERMS.items():
        if re.search(pattern, source.corpus):
            continue
        for sentence in claims:
            match = next(
                (
                    m
                    for m in re.finditer(pattern, sentence)
                    if label not in _MATERIALS or not _PIPE_CONTEXT.match(sentence[m.end() :])
                ),
                None,
            )
            if match:
                word = _word_at(sentence, match)
                violations.append(
                    Violation(
                        "term", field_name, f"«{word}» ({label}) не подтверждено входными данными"
                    )
                )
                break
    return violations


def _is_hedge(sentence: str) -> bool:
    tokens = extract_tokens(sentence)
    has_values = tokens.codes or tokens.numbers or tokens.quantities or tokens.standards
    return bool(_HEDGE.search(sentence)) and not has_values and not _CLAIM.search(sentence)


def _word_at(text: str, match: re.Match) -> str:
    start = re.search(r"[^\s(«]*$", text[: match.start()]).start()
    end = match.end() + re.match(r"[^\s.,;:)»]*", text[match.end() :]).end()
    return text[start:end]


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
        if not is_unit(word) and not re.search(rf"\b{word}\b", source.corpus):
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
        hits = [c.upper() for c in tokens.codes if _code_allowed(c, optional.codes)]
        hits += [f"{n} {u}" for n, u in tokens.quantities & optional.quantities]
        if hits and not _OPTIONAL_MARK.search(normalize_text(sentence)):
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
    """Оставляет факты, чья цитата дословно есть в description, а значение и поле — в цитате."""
    accepted, warnings = [], []
    for fact in facts:
        if normalize_key(fact.field) in source.spec_keys:
            log.info("факт %r дублирует specs, пропущен", fact.field)
            continue
        quote = normalize_text(fact.source_quote).strip(" .…")
        label = f"Факт «{fact.field}: {fact.value}» отброшен"
        if not quote or quote not in source.description:
            warnings.append(f"{label}: цитата не найдена в описании")
            continue
        quote_source = _quote_context(quote)
        if find_unknown_values(fact.value, quote_source, "facts") or find_unknown_terms(
            fact.value, quote_source, "facts"
        ):
            warnings.append(f"{label}: значение не совпадает с цитатой")
            continue
        if not _field_mentioned(fact.field, quote):
            warnings.append(f"{label}: цитата не о «{fact.field}»")
            continue
        if extract_tokens(fact.value).codes & source.optional.codes and not _OPTIONAL_MARK.search(
            quote
        ):
            warnings.append(f"{label}: во входе это исполнение по заказу")
            continue
        accepted.append(fact)
    return accepted, warnings


def _field_mentioned(field_name: str, quote: str) -> bool:
    # Хотя бы одно содержательное слово поля (по первым 5 буквам) должно быть в цитате:
    # отсекает «Гарантийный срок» с цитатой «не менее 10 лет».
    stems = [w[:5] for w in re.findall(r"\w{4,}", normalize_text(field_name))]
    return not stems or any(stem in quote for stem in stems)


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
    has_model_part = any(re.search(r"\d", s) and re.search(r"[^\W\d_]", s) for s in segments)
    digit_segments = sum(bool(re.search(r"\d", s)) for s in segments)
    return len(segments) >= 3 and digit_segments >= 2 and has_model_part


def _fmt(values: set[str]) -> str:
    return ", ".join(sorted(v.upper() for v in values))
