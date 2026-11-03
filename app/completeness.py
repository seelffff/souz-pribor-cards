import re
from typing import NamedTuple

from app.models import MissingField, ProductInput
from app.normalize import normalize_key, normalize_text


class ExpectedField(NamedTuple):
    name: str
    importance: str
    key_pattern: str


def _fields(critical: list[tuple[str, str]], recommended: list[tuple[str, str]]):
    return [ExpectedField(n, "critical", p) for n, p in critical] + [
        ExpectedField(n, "recommended", p) for n, p in recommended
    ]


_IP = ("степень защиты IP", r"степень защиты|\bip\b|пылевлаго")
_VERIFICATION = ("межповерочный интервал", r"межповер|поверк")
_REGISTRY = ("номер в Госреестре СИ", r"реестр|\bфиф\b")
_WARRANTY = ("гарантийный срок", r"гарант")
_CONNECTION = ("присоединение", r"присоедин|резьб")
_MEDIUM_PATTERN = r"^(?:измеряем\w* |рабоч\w* )?сред\w*$"

# Порядок важен: манометр проверяется раньше датчика давления.
DEVICE_TYPES = {
    "манометр": {
        "detect": r"манометр|мановакуумметр|вакуумметр|тягонапоромер",
        "fields": _fields(
            critical=[
                ("диапазон измерений", r"диапазон|предел"),
                ("класс точности", r"класс точности|точност|погрешност"),
                _CONNECTION,
                ("диаметр корпуса", r"диаметр"),
            ],
            recommended=[
                ("расположение штуцера", r"расположени"),
                ("измеряемая среда", _MEDIUM_PATTERN),
                (
                    "температура измеряемой среды",
                    r"температур\w* (?:измеряем|рабоч)|^температур\w*$",
                ),
                _IP,
                ("материал корпуса", r"^корпус$|материал"),
                _VERIFICATION,
                _REGISTRY,
                _WARRANTY,
            ],
        ),
    },
    "датчик давления": {
        "detect": r"датчик\w* давлени|преобразовател\w* давлени|\bпд\d",
        "fields": _fields(
            critical=[
                ("диапазон измерений", r"диапазон|предел"),
                ("тип измеряемого давления", r"тип давлени|вид давлени|измеряем\w* давлени"),
                ("основная погрешность", r"погрешност|точност|класс"),
                ("выходной сигнал", r"выходн|сигнал|интерфейс"),
                ("напряжение питания", r"питани"),
                _CONNECTION,
            ],
            recommended=[
                ("измеряемая среда", _MEDIUM_PATTERN),
                ("температура измеряемой среды", r"температур\w* (?:измеряем|рабоч)"),
                ("температура окружающей среды", r"окружающ"),
                _IP,
                ("материал мембраны", r"мембран"),
                ("электрическое подключение", r"разъем|электрическ\w* подключ|кабел"),
                _VERIFICATION,
                _REGISTRY,
                _WARRANTY,
            ],
        ),
    },
    "термопреобразователь сопротивления": {
        "detect": r"термопреобразовател\w* сопротивлени|термосопротивлени|\bтс[мп]\b|\bдтс\d",
        "fields": _fields(
            critical=[
                ("НСХ", r"нсх|статическ\w* характеристик|чувствительн\w* элемент"),
                ("класс допуска", r"допуск|класс"),
                ("диапазон измеряемых температур", r"диапазон"),
                ("длина монтажной части", r"длин"),
                ("схема соединения", r"схем|провод"),
            ],
            recommended=[
                ("диаметр монтажной части", r"диаметр"),
                ("материал защитной арматуры", r"материал|арматур|гильз"),
                _CONNECTION,
                _IP,
                ("показатель тепловой инерции", r"инерц"),
                _VERIFICATION,
                _REGISTRY,
                _WARRANTY,
            ],
        ),
    },
    "термопара": {
        "detect": r"термопар|термоэлектрическ|\bдтп\d|\bтх[акн]\b",
        "fields": _fields(
            critical=[
                ("НСХ (тип термопары)", r"нсх|тип термопар|статическ\w* характеристик"),
                ("класс допуска", r"допуск|класс"),
                ("диапазон измеряемых температур", r"диапазон"),
                ("длина монтажной части", r"длин"),
            ],
            recommended=[
                ("рабочий спай", r"спай"),
                ("диаметр монтажной части", r"диаметр"),
                ("материал защитной арматуры", r"материал|арматур|гильз"),
                _CONNECTION,
                _IP,
                ("показатель тепловой инерции", r"инерц"),
                _VERIFICATION,
                _WARRANTY,
            ],
        ),
    },
    "прибор": {
        "detect": r"",
        "fields": _fields(
            critical=[
                ("диапазон измерений", r"диапазон|предел"),
                ("точность", r"точност|погрешност|класс"),
            ],
            recommended=[
                ("напряжение питания", r"питани"),
                ("выходной сигнал или интерфейс", r"выходн|сигнал|интерфейс"),
                _CONNECTION,
                _IP,
                ("рабочая температура", r"температур"),
                _WARRANTY,
            ],
        ),
    },
}


def detect_device_type(product: ProductInput) -> str:
    text = normalize_text(" ".join([product.name, product.type, product.category]))
    for device_type, config in DEVICE_TYPES.items():
        if config["detect"] and re.search(config["detect"], text):
            return device_type
    return "прибор"


def find_missing_fields(device_type: str, known_keys: list[str]) -> list[MissingField]:
    """known_keys — названия характеристик из specs и подтверждённых фактов из description."""
    keys = [normalize_key(key) for key in known_keys]
    missing = []
    for expected in DEVICE_TYPES[device_type]["fields"]:
        if not any(re.search(expected.key_pattern, key) for key in keys):
            missing.append(MissingField(field=expected.name, importance=expected.importance))
    return missing
