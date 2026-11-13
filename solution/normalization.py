
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def normalize_phone(value):
    """Convert an Indian mobile number into +91XXXXXXXXXX."""

    if not value:
        return None

    digits = re.sub(r"\D", "", str(value))

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits

    return None


def normalize_date(value):
    """Convert supported date formats into YYYY-MM-DD."""

    if not value or not str(value).strip():
        return None

    value = str(value).strip()

    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d-%b-%Y"
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    return None


def normalize_decimal(value):
    """Convert rupee amounts into a decimal with 2 places."""

    if value is None or not str(value).strip():
        return None

    value = str(value).strip()
    value = re.sub(r"^(₹|Rs\.?|INR)\s*", "", value, flags=re.IGNORECASE)
    value = value.replace(",", "")

    try:
        amount = Decimal(value)

        if not amount.is_finite() or amount < 0:
            return None

        return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    except InvalidOperation:
        return None


def normalize_enum(value, allowed_values):
    """Match a value against allowed enum values."""

    if not value or not str(value).strip():
        return None

    value = str(value).strip().lower()
    allowed = [str(x).strip().lower() for x in allowed_values]

    return value if value in allowed else None
if __name__ == "__main__":
    print("Phone:", normalize_phone("09876543210"))
    print("Date:", normalize_date("13/11/2026"))
    print("Amount:", normalize_decimal("Rs. 1,500.50"))
    print("Invalid phone:", normalize_phone("12345"))
