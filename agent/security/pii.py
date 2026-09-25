"""PII masking applied before any text leaves the service (external model calls, audit args, handoff summaries).

What is masked, and how:
  email            j***@correo.example           local part reduced to its first character
  card / account   ************1234              any run of 12 to 19 digits (spaces or dashes allowed) keeps
                                                  the last four, which is what a customer uses to recognize a card
  phone            +57 ******12                  international or 10-digit local numbers keep the last two
  CURP (Mexico)    [DOC]                         18-character CURP pattern
  document number  [DOC]                         7 to 11 digit runs, or 2 to 4 dot-separated groups
                                                  (DNI 12.345.678), unless the context says it is an amount
  names            [NAME]                        names the service already knows (the session customer's
                                                  profile) plus phrases such as "me llamo", "mi nombre es",
                                                  "meu nome e", "soy"; a free-text name that matches neither
                                                  is not detected, which is a documented limitation

Amounts versus document numbers. Argentine and Colombian amounts use dots as thousands separators
("2.140.000"), which look exactly like a dotted DNI or cedula. A number is decided by its immediate context:
  1. a document cue right before it (DNI, CC, CE, CURP, CUIT, CPF, RG, "documento", "cedula", "identidad",
     "pasaporte", optionally followed by "no.", "numero", "es", ":") masks it;
  2. otherwise a currency code or symbol right before or after it (COP, ARS, USD, MXN, BRL, $, US$, R$, pesos,
     reais, dolares), an amount word right before it ("como", "unos", "por", "aprox", "cerca de", "cargo de",
     "monto de"), or a decimal part ("2.140.000,50") keeps it;
  3. anything else is masked. A bare number with no context is treated as a document.
A value in a field designated as a document (DOCUMENT_KEYS) is always fully redacted.

Residual risk. A document number written right after a currency or amount word ("pague por 12.345.678") is
kept, because the text reads as an amount; this requires the customer to write their document where an amount
would go. The opposite error, a bare amount with no context masked as [DOC], loses information but leaks
nothing. Both are covered by tests in tests/agent/test_pii_audit.py.

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
_DOC = re.compile(r"(?<![\w*.,])(?:\d{1,3}(?:\.\d{3}){2,3}|\d{7,11})(?![\w*]|[.,]\d)")
_DOC_CUE = re.compile(
    r"(?i)\b(?:dni|c\.?\s?c|c\.?\s?e|curp|cuit|cuil|rut|nit|cpf|rg|documento|doc|c[ée]dula|identidad|"
    r"identificaci[óo]n|pasaporte|passaporte)\b[\s.:#°º-]*(?:(?:n[°ºo]|nro|n[úu]mero|es|de|[ée]|:)[\s.:#°º-]*)*$"
)
_CURRENCY_BEFORE = re.compile(r"(?i)(?:\b(?:cop|ars|usd|mxn|brl)|us\$|r\$|\$)\s*$")
_AMOUNT_WORD_BEFORE = re.compile(
    r"(?i)\b(?:como|unos|unas|uns|umas|por|aprox\.?|aproximadamente|cerca de|alrededor de|"
    r"(?:cargo|cobro|compra|monto|valor|total|pago|d[ée]bito|saldo|importe)\s+(?:de|por))\s*$"
)
_CURRENCY_AFTER = re.compile(r"(?i)^\s*(?:cop|ars|usd|mxn|brl|pesos?|reais|real|d[óo]lares)\b")
_CONTEXT_CHARS = 40
_NAME_CUE = re.compile(
    r"\b((?i:me llamo|mi nombre es|meu nome [eé]|me chamo|soy|sou|titular))\s+"
    r"((?:[A-ZÁÉÍÓÚÑÃÕÇ][a-záéíóúñãõç]+)(?:\s+[A-ZÁÉÍÓÚÑÃÕÇ][a-záéíóúñãõç]+){0,3})"
)

DOCUMENT_KEYS = frozenset({"document_number", "document", "documento", "cedula", "dni", "curp", "national_id",
                           "id_number"})
SENSITIVE_KEYS = frozenset({
    "first_name", "last_name", "full_name", "name", "email", "mobile_phone", "landline_phone",
    "phone", "address", "product_number", "card_number", "account_number", "date_of_birth", *DOCUMENT_KEYS,
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


def is_amount_context(before: str, after: str) -> bool:
    """True when the text around a number says it is money, and no document cue sits right before it."""
    if _DOC_CUE.search(before):
        return False
    return bool(_CURRENCY_BEFORE.search(before) or _AMOUNT_WORD_BEFORE.search(before)
                or _CURRENCY_AFTER.search(after))


def _mask_documents(text: str) -> str:
    def replace(match: re.Match) -> str:
        before = text[max(0, match.start() - _CONTEXT_CHARS):match.start()]
        after = text[match.end():match.end() + _CONTEXT_CHARS]
        return match.group(0) if is_amount_context(before, after) else "[DOC]"
    return _DOC.sub(replace, text)


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
    out = _mask_documents(out)
    return _mask_names(out, known_names)


def document_hint(value: str) -> str:
    """Last three characters of a document, for showing a customer their own profile. Never used for logs."""
    text = str(value)
    return "*" * max(len(text) - 3, 0) + text[-3:] if len(text) > 3 else "***"


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
    if key in DOCUMENT_KEYS:
        return "[DOC]"  # fully redacted in logs; only the customer's own profile shows the last 3 digits
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
