import re

_ORCID_COMPACT_REGEX = re.compile(r"^\d{15}[0-9X]$")


def normalize_optional_text(value: str | None) -> str | None:
    """Trim optional text inputs and collapse blank strings to None."""

    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


def normalize_required_text(value: str) -> str:
    """Trim required text inputs and reject blank values."""

    normalized = value.strip()
    if not normalized:
        raise ValueError("Value must not be blank")
    return normalized


_KNOWN_TUNNELING = frozenset({"none", "wigner", "eckart", "sct"})


def normalize_tunneling_model(value):
    """Coerce a producer-supplied tunneling model to a canonical token.

    Case-insensitive; folds any unrecognized non-empty value to ``other``,
    mirroring the DB migration (DR-0032). ``None``/blank → ``None``. Real
    producers (e.g. ARC) emit ``"Eckart"``, which normalizes to ``eckart``.
    Returns a string the enum field then coerces (or ``None``).
    """
    if value is None:
        return None
    # Accept an already-resolved enum member (read path, ORM → schema):
    # use its raw ``.value`` rather than ``str(member)`` which, for a
    # str-mixin Enum, can render as ``"TunnelingModel.eckart"``.
    raw = getattr(value, "value", value)
    text = str(raw).strip().lower()
    if not text:
        return None
    return text if text in _KNOWN_TUNNELING else "other"


def generate_orcid_check_digit(base_digits: str) -> str:
    """Generate the ISO 7064 Mod 11-2 ORCID check digit."""

    total = 0
    for digit in base_digits:
        total = (total + int(digit)) * 2

    remainder = total % 11
    result = (12 - remainder) % 11
    return "X" if result == 10 else str(result)


def normalize_orcid(value: str | None) -> str | None:
    """Normalize ORCID input to canonical hyphenated form and validate checksum."""

    if value is None:
        return None

    normalized = value.strip().upper()
    if not normalized:
        return None

    compact = re.sub(r"[^0-9X]", "", normalized)
    if not _ORCID_COMPACT_REGEX.fullmatch(compact):
        raise ValueError(
            "ORCID must contain 16 characters in the form XXXX-XXXX-XXXX-XXXX, "
            'where the final character may be "X".'
        )

    base_digits = compact[:-1]
    provided_check_digit = compact[-1]
    computed_check_digit = generate_orcid_check_digit(base_digits)
    if computed_check_digit != provided_check_digit:
        raise ValueError(
            f'Invalid ORCID check digit for "{value.strip()}". '
            f'Expected "{computed_check_digit}".'
        )

    return f"{compact[0:4]}-{compact[4:8]}-{compact[8:12]}-{compact[12:16]}"
