import pytest

from app.generator import generate_card
from app.llm.mock import MockProvider
from app.models import InputError, parse_product_json
from app.validators import build_source_context, find_unknown_terms, find_unknown_values

DM02 = """{
  "name": "Манометр показывающий DM 02",
  "manufacturer": "Манотомь",
  "specs": {"диапазон измерений": "0–1,6 МПа", "класс точности": "1,5",
            "присоединение": "G1/2", "диаметр корпуса": "100 мм"},
  "description": "Прибор предназначен для измерения избыточного давления..."
}"""


@pytest.fixture
def dm02():
    return parse_product_json(DM02)


def unknown(text, product):
    source = build_source_context(product)
    return find_unknown_values(text, source, "seo_description") + find_unknown_terms(
        text, source, "seo_description"
    )


def test_values_from_input_pass(dm02):
    # те же значения, но записаны по-другому
    text = "Манометр DM 02 на 0-1.6 МПа, класс точности 1.5, корпус 100мм, резьба g 1/2."
    assert unknown(text, dm02) == []


def test_invented_ip_is_caught(dm02):
    assert unknown("Степень защиты IP54.", dm02)


def test_unit_conversion_is_caught(dm02):
    assert unknown("Диапазон до 16 бар.", dm02)


def test_invented_material_is_caught(dm02):
    assert unknown("Корпус из нержавеющей стали.", dm02)


def test_broken_json():
    with pytest.raises(InputError, match="Некорректный JSON"):
        parse_product_json('{"name": "ДМ 02",}')


def test_empty_name():
    with pytest.raises(InputError):
        parse_product_json('{"name": "", "specs": {}}')


def test_mock_card_for_dm02(dm02):
    card = generate_card(dm02, MockProvider())
    assert card.status == "ok"
    assert len(card.seo_title) <= 70
    assert 800 <= len(card.seo_description) <= 1200
    names = [spec.name for spec in card.key_specs]
    assert "класс точности" in names
