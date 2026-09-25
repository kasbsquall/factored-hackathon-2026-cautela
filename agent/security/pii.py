"""PII masking applied before any text leaves the service (external model calls, audit args, handoff summaries).

What is masked, and how:
  email            j***@correo.example           local part reduced to its first character
  card / account   ************1234              any run of 12 to 19 digits (spaces or dashes allowed) keeps
                                                  the last four, which is what a customer uses to recognize a card
  phone            +57 ******12                  international or 10-digit local numbers keep the last two
  CURP (Mexico)    [DOC]                         18-character CURP pattern
  document number  [DOC]                         7 to 11 digit runs, optionally with dots (DNI 12.345.678)
  names            [NAME]                        names the service already knows (the session customer's
                                                  profile) plus phrases such as "me llamo", "mi nombre es",
                                                  "meu nome e", "soy"; a free-text name that matches neither
                                                  is not detected, which is a documented limitation

The functions are pure and deterministic so they can be unit-tested and reused by the audit log.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_INTL_PHONE = re.compile(r"(?<![\w*+])(\+\d{1,3})[ -]?((?:\d[ -]?){6,11}\d)(?!\w)")
_CARD = re.compile(r"(?<![\w*])(?:\d[ -]?){11,18}\d(?!\w)")
_LOCAL_PHONE = re.compile(r"(?<![\w*])\(?\d{2,3}\)?[ -]\d{3,4}[ -]?\d{4}(?!\w)")
_CURP = re.compile(r"\b[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d\b", re.IGNORECASE)
_DOC = re.compile(r"(?<![\w*.])(?:\d{1,3}(?:\.\d{3}){2}|\d{7,11})(?![\w*])")
_NAME_CUE = re.compile(
    r"\b((?i:me llamo|mi nombre es|meu nome [eé]|me chamo|soy|sou|titular))\s+"
    r"((?:[A-ZÁÉÍÓÚÑÃÕÇ][a-záéíóúñãõç]+)(?:\s+[A-ZÁÉÍÓÚÑÃÕÇ][a-záéíóúñãõç]+){0,3})"
)

SENSITIVE_KEYS = frozenset({
    "first_name", "last_name", "full_name", "name", "document_number", "email", "mobile_phone", "landline_phone",
    "phone", "address", "product_number", "card_number", "account_number", "date_of_birth",
})


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def mask_email(match: re.Match) -> str:
    return f"{match.group(1)}***@{match.group(2)}"


def mask_digits_keep_last(value: str, keep: int = 4) -> str:
    digits = re.sub(r"\D", "", value)
    return "*" * max(len(digits) - keep, 0) + digits[-keep:]


def _mask_card(match: re.Match) -> str:
    return mask_digits_keep_last(match.group(0), 4)


def _mask_intl_phone(match: re.Match) -> str:
    return f"{match.group(1)} ******{re.sub(r'[^0-9]', '', match.group(2))[-2:]}"


def _mask_local_phone(match: re.Match) -> str:
    return "******" + re.sub(r"\D", "", match.group(0))[-2:]


def _mask_names(text: str, known_names: Iterable[str]) -> str:
    for name in sorted({n.strip() for n in known_names if n and len(n.strip()) >= 3}, key=len, reverse=True):
        pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
        text = pattern.sub("[NAME]", text)
        plain = _strip_accents(name)
        if plain != name:
            text = re.compile(r"\b" + re.escape(plain) + r"\b", re.IGNORECASE).sub("[NAME]", text)
    return _NAME_CUE.sub(lambda m: f"{m.group(1)} [NAME]", text)


def mask_text(text: str, known_names: Iterable[str] = ()) -> str:
    """Mask PII in free text. Order matters: emails first, then long digit runs, then shorter ones."""
    if not text:
        return text
    out = _EMAIL.sub(mask_email, text)
    out = _CURP.sub("[DOC]", out)
    out = _INTL_PHONE.sub(_mask_intl_phone, out)
    out = _CARD.sub(_mask_card, out)
    out = _LOCAL_PHONE.sub(_mask_local_phone, out)
    out = _DOC.sub("[DOC]", out)
    return _mask_names(out, known_names)


def mask_field(key: str, value: Any) -> Any:
    """Mask a structured value by its field name; unknown fields fall back to free-text masking."""
    if value is None:
        return None
    text = str(value)
    if key in {"product_number", "card_number", "account_number"}:
        return mask_digits_keep_last(text, 4)
    if key in {"mobile_phone", "landline_phone", "phone"}:
        return "******" + re.sub(r"\D", "", text)[-2:]
    if key == "email":
        return _EMAIL.sub(mask_email, text)
    if key == "document_number":
        return "*" * max(len(text) - 3, 0) + text[-3:] if len(text) > 3 else "***"
    if key in {"first_name", "last_name", "full_name", "name", "address", "date_of_birth"}:
        return "[REDACTED]"
    return mask_text(text) if isinstance(value, str) else value


def _is_identifier(key: str) -> bool:
    """Record ids are pseudonymous keys, not PII, and must stay intact for traceability."""
    return key.endswith("_id") or key.endswith("_ids") or key in {"idempotency_key", "request_id", "trace_id"}


def mask_mapping(data: Mapping[str, Any], known_names: Iterable[str] = ()) -> dict[str, Any]:
    """Recursively mask a dict: sensitive keys by rule, every other string as free text."""
    names = list(known_names)
    out: dict[str, Any] = {}
    for key, value in data.items():
        if _is_identifier(key):
            out[key] = value
        elif key in SENSITIVE_KEYS:
            out[key] = mask_field(key, value)
        elif isinstance(value, Mapping):
            out[key] = mask_mapping(value, names)
        elif isinstance(value, list):
            out[key] = [mask_mapping(v, names) if isinstance(v, Mapping)
                        else mask_text(v, names) if isinstance(v, str) else v for v in value]
        elif isinstance(value, str):
            out[key] = mask_text(value, names)
        else:
            out[key] = value
    return out
