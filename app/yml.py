import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import ValidationError

from app.models import InputError, ProductInput

# Идентификаторы учёта, а не характеристики прибора.
_SKIP_PARAMS = {"Артикул", "Код товара"}


def offer_to_product(feed: Path, offer_id: str) -> ProductInput:
    """Оффер YML-фида (Яндекс.Маркет) -> входные данные карточки. Фид читается потоково."""
    categories: dict[str, str] = {}
    try:
        for _, element in ET.iterparse(feed, events=("end",)):
            if element.tag == "category":
                categories[element.get("id", "")] = (element.text or "").strip()
            elif element.tag == "offer":
                if element.get("id") == offer_id:
                    return _product_from_offer(element, categories)
                element.clear()
    except ET.ParseError as exc:
        raise InputError(f"Не удалось разобрать YML-фид: {exc}") from exc
    raise InputError(f"Оффер {offer_id} не найден в фиде")


def _product_from_offer(offer: ET.Element, categories: dict[str, str]) -> ProductInput:
    specs = {
        param.get("name", ""): param.text or ""
        for param in offer.findall("param")
        if param.get("name") not in _SKIP_PARAMS
    }
    data = {
        "name": offer.findtext("name", ""),
        "manufacturer": offer.findtext("vendor", ""),
        "type": offer.findtext("typePrefix", ""),
        "category": categories.get(offer.findtext("categoryId", ""), ""),
        "specs": specs,
        "description": offer.findtext("description", ""),
    }
    try:
        return ProductInput.model_validate(data)
    except ValidationError as exc:
        raise InputError(f"Оффер {offer.get('id')} без названия или с битыми полями") from exc
