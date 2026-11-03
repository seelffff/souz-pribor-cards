import re
from dataclasses import dataclass, field
from decimal import Decimal

_DASHES = re.compile("[‐‑‒–—―−]")
_SYMBOLS = str.maketrans({"½": "1/2", "¼": "1/4", "¾": "3/4", "⅛": "1/8", "⅜": "3/8", "⁄": "/"})

# Однобуквенные единицы (м, в, с, г, а) не берём: они совпадают с предлогами
# и дают ложные пары «число + единица».
_UNIT_CANON = {
    "кгс/см2": "кгс/см2",
    "кгс": "кгс",
    "мм рт.ст.": "мм рт.ст.",
    "мм вод.ст.": "мм вод.ст.",
    "мпа": "мпа",
    "mpa": "мпа",
    "кпа": "кпа",
    "kpa": "кпа",
    "па": "па",
    "pa": "па",
    "мбар": "мбар",
    "mbar": "мбар",
    "бар": "бар",
    "bar": "бар",
    "атм": "атм",
    "psi": "psi",
    "мм": "мм",
    "mm": "мм",
    "мкм": "мкм",
    "см": "см",
    "дюйм": "дюйм",
    "°c": "°c",
    "кгц": "кгц",
    "khz": "кгц",
    "гц": "гц",
    "hz": "гц",
    "кг": "кг",
    "kg": "кг",
    "ма": "ма",
    "ma": "ма",
    "мв": "мв",
    "mv": "мв",
    "вт": "вт",
    "ом": "ом",
    "л/мин": "л/мин",
    "%": "%",
    "года": "год",
    "год": "год",
    "лет": "год",
    "месяцев": "мес",
    "месяца": "мес",
    "месяц": "мес",
    "мес": "мес",
}
_UNIT_RE = "|".join(re.escape(u) for u in sorted(_UNIT_CANON, key=len, reverse=True))
_UNIT_AFTER_DIGIT = re.compile(rf"(\d)(?=(?:{_UNIT_RE})(?!\w))")
# «1 000 кПа»; первая группа без ведущего нуля и не после «/»: «G1/2 100 мм» — это два значения.
_THOUSANDS = re.compile(rf"(?<![\w./])[1-9]\d{{0,2}}(?: \d{{3}})+(?= ?(?:{_UNIT_RE})(?!\w))")

_NUMBER_WORDS = {
    "один": "1", "одного": "1", "два": "2", "двух": "2", "три": "3", "трех": "3",
    "четыре": "4", "четырех": "4", "пять": "5", "пяти": "5", "шесть": "6", "шести": "6",
    "семь": "7", "семи": "7", "восемь": "8", "восьми": "8", "девять": "9", "девяти": "9",
    "десять": "10", "десяти": "10", "двенадцать": "12", "пятнадцать": "15",
    "двадцать": "20", "тридцать": "30",
}  # fmt: skip
_NUMBER_WORD_RE = "|".join(_NUMBER_WORDS)

_NUM = r"\d+(?:\.\d+)?(?:/\d+)?"
_SIGN = r"(?:минус\s+|плюс\s+|[-+])?"
_QUANTITY = re.compile(
    rf"(?<![\w.])(?:от\s+)?{_SIGN}{_NUM}"
    rf"(?:\s*(?:-|до|÷|\.\.\.|…)\s*{_SIGN}{_NUM})*"
    rf"(?:\s*(?P<unit>{_UNIT_RE})(?!\w))?"
)
_NUMBER_IN_GROUP = re.compile(
    rf"(?P<conn>-|до|от|÷|\.\.\.|…)?\s*(?P<sign>минус\s+|плюс\s+|[-+](?=\d))?(?P<num>{_NUM})"
)
_STANDARD = re.compile(r"\b(?:гост|ост|ту|din|en|iso)(?:\s+р)?\s+\d[\d.\-]*\d")
_WORD_WITH_DIGITS = re.compile(r"[\w./-]+")
# Кириллица и латиница в кодах выглядят одинаково, а модель почти всегда пишет «134a» латиницей.
_HOMOGLYPHS = str.maketrans("авекмнорстух", "abekmhopctyx")


def normalize_text(text: str) -> str:
    """Один вид записи: 1,6 -> 1.6, любые тире -> '-', 100мм -> 100 мм, g 1/2 -> g1/2."""
    text = text.translate(_SYMBOLS).lower().replace("ё", "е").replace("²", "2")
    text = text.replace("º", "°").replace("℃", "°c")
    text = re.sub(r"\s+", " ", _DASHES.sub("-", text))
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)
    text = re.sub(r"(?<=\d)\s*[x×х*]\s*(?=\d)", "x", text)
    text = re.sub(r"\b[gг]\s+(?=\d)", "g", text)
    text = re.sub(r"\b[кk]\s*(?=\d+/\d)", "k", text)
    text = re.sub(r"\b[мm]\s*(?=\d+x\d)", "m", text)
    text = re.sub(r"\bip\s*-?\s*(?=\d{2}\b)", "ip", text)
    text = re.sub(r"[ø⌀]\s*(?=\d)", "ø ", text)
    text = re.sub(r"°\s*[сc](?!\w)", "°c", text)
    text = re.sub(r"мм\s*рт\.?\s*ст\.?", "мм рт.ст.", text)
    text = re.sub(r"мм\s*вод\.?\s*ст\.?", "мм вод.ст.", text)
    text = re.sub(r"\bполгода\b", "6 месяцев", text)
    text = re.sub(
        rf"\b({_NUMBER_WORD_RE})(?:\s+|-?)(?=(?:лет|года?|месяц\w*)\b)",
        lambda m: _NUMBER_WORDS[m.group(1)] + " ",
        text,
    )
    text = re.sub(
        rf"\b({_NUMBER_WORD_RE})летн\w*", lambda m: _NUMBER_WORDS[m.group(1)] + " лет", text
    )
    text = _UNIT_AFTER_DIGIT.sub(r"\1 ", text)
    text = _THOUSANDS.sub(lambda m: m.group().replace(" ", ""), text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_number(raw: str) -> str:
    if "/" in raw:
        return raw
    return format(Decimal(raw).normalize(), "f")


def fold_letters(text: str) -> str:
    return text.translate(_HOMOGLYPHS)


def fold_code(code: str) -> str:
    """Ключ сравнения кодов: «МП3-У», «MП3У» и «мп3 у» совпадают."""
    return re.sub(r"[\s-]", "", fold_letters(code))


def is_unit(word: str) -> bool:
    return word in _UNIT_CANON


@dataclass
class Tokens:
    """Проверяемые значения: коды (G1/2, IP54, МП3-У), стандарты, числа, пары число+единица.

    Отрицательные числа хранятся со знаком: «минус 50 °С» -> ('-50', '°c').
    """

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

    # «DM 02», «WIKA 111.10» — обозначение модели, а не число: иначе в «DM 02 – 0–1,6 МПа»
    # номер модели склеивается с диапазоном в пару «2 МПа».
    def take_latin_model(match: re.Match) -> str:
        if match.group(1) in _UNIT_CANON:
            return match.group()
        tokens.codes.add(match.group(1) + match.group(2))
        return " "

    text = re.sub(r"\b([a-z]{1,5}) (\d[\d./]*\d|\d)(?![\w])", take_latin_model, text)

    # Числа внутри кодов (ПД100-ДИ0,6) не считаются самостоятельными значениями,
    # иначе «расшифровка» кода заказа прошла бы проверку.
    def take_code(match: re.Match) -> str:
        word = match.group().strip("./-")
        if word in _UNIT_CANON or not _is_code(word):
            return match.group()
        # «IP40/IP54», «У2/Т2» — перечисление; в «G1/2» после слеша цифра, это одна резьба.
        parts = re.split(r"/(?=[^\W\d_])", word)
        tokens.codes.update(parts if all(_is_code(p) for p in parts) else [word])
        return " "

    text = _WORD_WITH_DIGITS.sub(take_code, text)

    for match in _QUANTITY.finditer(text):
        unit = match.group("unit")
        numbers_part = text[match.start() : match.start("unit")] if unit else match.group()
        for index, number_match in enumerate(_NUMBER_IN_GROUP.finditer(numbers_part)):
            sign = number_match.group("sign") or ""
            # Дефис в начале группы — знак («-40...+150»), между числами — диапазон («0-1.6»).
            leading_minus = index == 0 and re.match(r"-\d", number_match.group())
            negative = sign.startswith(("минус", "-")) or leading_minus
            number = normalize_number(number_match.group("num"))
            if negative and number != "0":
                number = "-" + number
            if unit:
                tokens.quantities.add((number, _UNIT_CANON[unit]))
            else:
                tokens.numbers.add(number)
    return tokens


def _is_code(word: str) -> bool:
    return bool(re.search(r"\d", word) and re.search(r"[^\W\d_]", word))


def normalize_key(key: str) -> str:
    key = re.sub(r"\(.*?\)", " ", normalize_text(key))
    return re.sub(r"[^\w/]+", " ", key).strip()


def unit_from_key(key: str) -> str | None:
    """'Диаметр корпуса (мм)', '... [мм]', '..., мм' -> 'мм': так единицы пишут в выгрузках."""
    for pattern in (r"\(([^)]*)\)", r"\[([^\]]*)\]", r",\s*([^,()]+?)\s*$"):
        match = re.search(pattern, key)
        if match and canonical_unit(match.group(1)):
            return canonical_unit(match.group(1))
    return None


def canonical_unit(unit: str) -> str | None:
    return _UNIT_CANON.get(normalize_text(unit).rstrip("."))
