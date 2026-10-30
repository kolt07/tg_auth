import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _e164(number: str, region: str | None) -> str | None:
    try:
        parsed = phonenumbers.parse(number, region)
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def normalize_phone(raw: str, default_region: str = "RU") -> str | None:
    """Нормалізувати номер. Плюс у вводі ігноруємо — працюємо з цифрами і регіоном за замовчуванням."""
    text = raw.strip()
    if not text or len(text) > 40:
        return None
    digits = _digits(text)
    if not digits:
        return None
    if digits.startswith("00"):
        return _e164("+" + digits[2:], None)

    national = _e164(digits, default_region)
    if national:
        return national
    international = _e164("+" + digits, None)
    if international:
        return international
    if len(digits) == 11 and digits.startswith("8"):
        return _e164("+7" + digits[1:], None)
    return None
