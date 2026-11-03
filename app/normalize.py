import re
from dataclasses import dataclass, field
from decimal import Decimal

_DASHES = re.compile("[‐‑‒–—―−]")

# Однобуквенные единицы (м, в, с, г, а) не берём: они совпадают с предлогами
# и дают ложные пары «число + единица».
_UNIT_CANON = {
    "кгс/см2": "кгс/см2",
    "мм рт.ст.": "мм рт.ст.",
    "мм вод.ст.": "мм вод.ст.",
    "мпа": "мпа",
    "кпа": "кпа",
    "па": "па",
    "бар": "бар",
    "bar": "бар",
    "атм": "атм",
    "psi": "psi",
    "мм": "мм",
    "см": "см",
    "°c": "°c",
    "кгц": "кгц",
    "гц": "гц",
    "кг": "кг",
    "ма": "ма",
    "мв": "мв",
    "вт": "вт",
    "ом": "ом",
    "%": "%",
    "года": "год",
    "год": "год",
    "лет": "год",
    "месяцев": "мес",
    "месяца": "мес",
    "мес": "мес",
}
_UNIT_RE = "|".join(re.escape(u) for u in sorted(_UNIT_CANON, key=len, reverse=True))
_UNIT_AFTER_DIGIT = re.compile(rf"(\d)(?=(?:{_UNIT_RE})(?!\w))")

_NUM = r"\d+(?:\.\d+)?(?:/\d+)?"
_SIGN = r"(?:минус\s+|плюс\s+|[-+])?"
_QUANTITY = re.compile(
    rf"(?<![\w.])(?:от\s+)?{_SIGN}{_NUM}"
    rf"(?:\s*(?:-|до|÷|\.\.\.|…)\s*{_SIGN}{_NUM})*"
    rf"(?:\s*(?P<unit>{_UNIT_RE})(?!\w))?"
)
_STANDARD = re.compile(r"\b(?:гост|ост|ту|din|en|iso)(?:\s+р)?\s+\d[\d.\-]*\d")
_WORD_WITH_DIGITS = re.compile(r"[\w./-]+")


def normalize_text(text: str) -> str:
    """Один вид записи: 1,6 -> 1.6, любые тире -> '-', 100мм -> 100 мм, g 1/2 -> g1/2."""
    text = text.lower().replace("ё", "е").replace("²", "2").replace("º", "°")
    text = _DASHES.sub("-", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(?<=\d)\s*[x×х*]\s*(?=\d)", "x", text)
    text = re.sub(r"\b[gг]\s+(?=\d)", "g", text)
    text = re.sub(r"\b[кk]\s*(?=\d+/\d)", "k", text)
    text = re.sub(r"\b[мm]\s*(?=\d+x\d)", "m", text)
    text = re.sub(r"\bip\s*-?\s*(?=\d{2}\b)", "ip", text)
    text = re.sub(r"°\s*[сc](?!\w)", "°c", text)
    text = re.sub(r"мм\s*рт\.?\s*ст\.?", "мм рт.ст.", text)
    text = re.sub(r"мм\s*вод\.?\s*ст\.?", "мм вод.ст.", text)
    text = _UNIT_AFTER_DIGIT.sub(r"\1 ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_number(raw: str) -> str:
    if "/" in raw:
        return raw
    return format(Decimal(raw).normalize(), "f")


@dataclass
class Tokens:
    """Проверяемые значения: коды (G1/2, IP54, МП3-У), стандарты, числа, пары число+единица."""

    codes: set[str] = field(default_factory=set)
    standards: set[str] = field(default_factory=set)
    numbers: set[str] = field(default_factory=set)
    quantities: set[tuple[str, str]] = field(default_factory=set)

    def all_numbers(self) -> set[str]:
        return self.numbers | {number for number, _ in self.quantities}


def extract_tokens(text: str) -> Tokens:
    tokens = Tokens()
    text = normalize_text(text)

    for match in _STANDARD.finditer(text):
        tokens.standards.add(match.group())
    text = _STANDARD.sub(" ", text)

    # Числа внутри кодов (ПД100-ДИ0,6) не считаются самостоятельными значениями,
    # иначе «расшифровка» кода заказа прошла бы проверку.
    def take_code(match: re.Match) -> str:
        word = match.group().strip("./-")
        if word in _UNIT_CANON:
            return match.group()
        if re.search(r"\d", word) and re.search(r"[^\W\d_]", word):
            tokens.codes.add(word)
            return " "
        return match.group()

    text = _WORD_WITH_DIGITS.sub(take_code, text)

    for match in _QUANTITY.finditer(text):
        unit = match.group("unit")
        numbers_part = text[match.start() : match.start("unit")] if unit else match.group()
        for raw in re.findall(_NUM, numbers_part):
            number = normalize_number(raw)
            if unit:
                tokens.quantities.add((number, _UNIT_CANON[unit]))
            else:
                tokens.numbers.add(number)
    return tokens


def normalize_key(key: str) -> str:
    key = re.sub(r"\(.*?\)", " ", normalize_text(key))
    return re.sub(r"[^\w/]+", " ", key).strip()


def unit_from_key(key: str) -> str | None:
    """'Диаметр корпуса (мм)' -> 'мм': в таблицах магазина единица часто стоит в названии поля."""
    match = re.search(r"\(([^)]*)\)", key)
    if match and normalize_text(match.group(1)) in _UNIT_CANON:
        return match.group(1)
    return None
