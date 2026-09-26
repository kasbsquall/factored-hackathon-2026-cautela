"""Understand step: turn one customer message into a typed DisputeIntent.

Two sources produce the same model:
  llm                   MaskedLLM.extract against INTENT_SCHEMA, then pydantic validation and a literal check on
                        any record reference (a reference the customer did not write is dropped).
  deterministic_parser  ml.features.parse.parse_description (amount, currency word, relative dates) plus explicit
                        patterns for currency codes, "27 de mayo" style dates, record ids, option picks and a
                        short keyword list for human requests and out-of-scope topics. Used when no model is
                        configured or the model fails or returns invalid output; the reason is recorded.

Injection markers are checked deterministically before any model call. A match is a security event under
SYN-SEC-001, whatever the model would have said. The markers are narrow on purpose: an impatient customer
("no me hagas confirmar nada") is not flagged; the confirmation is simply still required.
"""

from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ml.features.lexicon import MONTHS
from ml.features.parse import parse_description

IntentKind = Literal["dispute_charge", "select_option", "reject_options", "block_card", "request_human",
                     "out_of_scope", "other"]
Topic = Literal["credit_limit_increase", "loan_application", "money_transfer", "immediate_refund",
                "change_personal_data", "account_closure", "investment_advice", "other_customer_request"]
Currency = Literal["USD", "MXN", "COP", "ARS", "BRL", "PESOS"]
RECORD_REF = r"^[A-Za-z0-9_\-]{3,40}$"
_REF_IN_TEXT = re.compile(r"\b(TX\d{6,}|TRX-[A-Z0-9]{6,}|CASE-[A-F0-9]{6,}|PRD-[A-Z0-9]{6,}|P\d{6,}|CLI-[A-Z0-9]{6,}|"
                          r"C\d{6})\b")


def ref_kind(ref: str) -> Literal["case", "product", "customer", "transaction"]:
    """Record kind from the id formats of the fixture (TX, P, C) and the organizer data (TRX-, PRD-, CLI-)."""
    ref = ref.upper()
    if ref.startswith("CASE-"):
        return "case"
    if re.fullmatch(r"PRD-[A-Z0-9]+|P\d+", ref):
        return "product"
    if re.fullmatch(r"CLI-[A-Z0-9]+|C\d+", ref):
        return "customer"
    return "transaction"


def find_refs(message: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _REF_IN_TEXT.finditer(message.upper())))


class DisputeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: IntentKind
    topic: Topic | None = None
    amount: float | None = Field(None, gt=0, lt=1e11)
    currency: Currency | None = None
    date: _dt.date | None = None
    date_tolerance_days: int = Field(1, ge=0, le=15)
    merchant: str | None = Field(None, max_length=100)
    selected_option: int | None = Field(None, ge=1, le=5)
    record_ref: str | None = Field(None, pattern=RECORD_REF)
    source: Literal["llm", "deterministic_parser", "injection_detector"] = "deterministic_parser"
    fallback_reason: str | None = None

    def has_charge_cues(self) -> bool:
        return self.amount is not None or self.date is not None or bool(self.merchant)


def intent_schema(today: date, n_options: int = 0) -> dict[str, Any]:
    """JSON Schema sent to the model. Today's date is data inside it so relative dates can be resolved."""
    nullable = lambda t, **kw: {"type": [t, "null"], **kw}  # noqa: E731
    return {
        "type": "object", "additionalProperties": False,
        "required": ["intent", "topic", "amount", "currency", "date", "merchant", "selected_option", "record_ref"],
        "properties": {
            "intent": {"enum": list(IntentKind.__args__),
                       "description": "dispute_charge: the customer does not recognize a charge or adds details "
                                      "about it. select_option: picks one numbered option. reject_options: none "
                                      "of the options. block_card: asks to block a card. request_human: asks "
                                      "for a person. out_of_scope: any other banking request."},
            "topic": {"enum": [*Topic.__args__, None], "description": "Only for out_of_scope"},
            "amount": nullable("number", description="Amount the customer states, as a plain number"),
            "currency": {"enum": [*Currency.__args__, None],
                         "description": "PESOS when the customer says pesos without a country"},
            "date": nullable("string", pattern=r"^\d{4}-\d{2}-\d{2}$",
                             description=f"Date of the charge as YYYY-MM-DD. Today is {today.isoformat()}."),
            "merchant": nullable("string", maxLength=100),
            "selected_option": nullable("integer", minimum=1, maximum=5,
                                        description=f"The customer was shown {n_options} numbered options"),
            "record_ref": nullable("string", pattern=RECORD_REF,
                                   description="A transaction, card or case id copied literally from the message"),
        },
    }


def from_llm(data: dict[str, Any], message: str) -> DisputeIntent:
    """Validate model output. Raises ValidationError; a reference not present in the message is dropped."""
    ref = data.get("record_ref")
    if ref and ref.upper() not in message.upper():
        data = {**data, "record_ref": None}
    return DisputeIntent.model_validate({**data, "source": "llm"})


# ---- injection markers (deterministic, checked before any model call) -----------------------------------
_INJECTION = re.compile("|".join([
    r"\bignor[ae]\w* (?:\w+ ){0,3}(?:instrucc|instru[cç]|regras|reglas|prompt)",
    r"\b(?:olvida|esquec[ea]) (?:\w+ ){0,3}(?:instrucc|instru[cç]|regras|reglas)",
    r"^\s*(?:system|sistema)\s*:", r"</?\s*(?:instru|system|sistema)", r"\[(?:system|sistema)\]",
    r"\bmodo (?:desarrollador|desenvolvedor|administrador|admin)\b", r"\bdeveloper mode\b",
    r"\bpermis(?:o|os|s[aã]o) de administrador\b", r"\b(?:nuevo|novo) contexto\b",
    r"\bselect\s+\*?\s*\w*\s*from\b", r"\btoken de confirma", r"\b(?:usa|use) (?:esta|essa) (?:sesi[oó]n|sess[aã]o)",
    r"\bel usuario autenticado (?:ahora )?es\b", r"\bo usu[aá]rio autenticado\b",
]), re.IGNORECASE | re.MULTILINE)


def detect_injection(message: str) -> str | None:
    match = _INJECTION.search(message)
    return match.group(0).strip()[:60] if match else None


# ---- language ---------------------------------------------------------------------------------------------
_PT = {"nao", "voce", "cartao", "reconheco", "compra", "cobranca", "ontem", "passada", "passado", "meu", "minha",
       "uma", "um", "foi", "estou", "obrigado", "obrigada", "quero", "falar", "atendente", "conta", "valor", "reais",
       "loja", "dia", "semana", "mes", "isso", "esse", "essa", "sim", "contestacao", "bloquear", "sou", "tem"}
_ES = {"no", "usted", "tarjeta", "reconozco", "cobro", "ayer", "pasada", "pasado", "mi", "una", "fue", "estoy",
       "gracias", "quiero", "hablar", "asesor", "cuenta", "monto", "tienda", "el", "los", "las", "esto", "si",
       "disputa", "bloquear", "soy", "tengo", "un", "del", "me", "cargo"}


def plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def detect_language(message: str, default: str = "es") -> str:
    words = re.findall(r"[a-z]+", plain(message))
    pt, es = sum(w in _PT for w in words), sum(w in _ES for w in words)
    if re.search(r"[ãõç]", message.lower()):
        pt += 2
    if re.search(r"[ñ¿¡]", message.lower()):
        es += 2
    return "pt" if pt > es else "es" if es > pt else default


# ---- deterministic fallback ---------------------------------------------------------------------------------
_HUMAN = re.compile(r"\b(?:hablar con (?:una persona|alguien|un (?:asesor|agente|humano|ejecutivo))|asesor humano|"
                    r"agente humano|persona real|falar com (?:uma pessoa|algu[eé]m|um (?:atendente|humano|agente))|"
                    r"atendente humano|pessoa de verdade)\b")
_TOPICS: dict[str, str] = {
    r"\b(?:cupo|limite de credito|limite do cartao|aumento de limite)\b": "credit_limit_increase",
    r"\b(?:prestamo|emprestimo|credito personal|credito pessoal)\b": "loan_application",
    r"\b(?:transferir|transferencia a|fazer uma transferencia|enviar dinero|mandar dinheiro)\b": "money_transfer",
    r"\b(?:devolv\w+ (?:el|mi|o|meu) dinero|reembolso (?:ya|inmediato|agora|imediato)|"
    r"devolv\w+ (?:o|meu) dinheiro|devolverme el dinero)\b": "immediate_refund",
    r"\b(?:cambiar mis datos|actualizar mi (?:direccion|correo|telefono)|alterar (?:meus )?dados|"
    r"numero completo|numeros completos)\b": "change_personal_data",
    r"\b(?:cerrar (?:mi|la) cuenta|encerrar (?:minha|a) conta)\b": "account_closure",
    r"\b(?:invertir|inversion|investir|investimento)\b": "investment_advice",
    r"\b(?:todas las cuentas|otro cliente|outro cliente|todas as contas)\b": "other_customer_request",
}
_BLOCK = re.compile(r"\b(?:bloque\w*|bloqueie|bloquear|bloqueio)\b.{0,30}\b(?:tarjeta|cartao)\b|"
                    r"\b(?:tarjeta|cartao)\b.{0,30}\bbloque\w*")
_ORDINALS = {"primer": 1, "primera": 1, "primero": 1, "primeira": 1, "primeiro": 1, "segunda": 2, "segundo": 2,
             "tercer": 3, "tercera": 3, "tercero": 3, "terceira": 3, "terceiro": 3, "cuarta": 4, "cuarto": 4,
             "quarta": 4, "quarto": 4}
_REJECT = re.compile(r"\b(?:ninguna|ninguno|nenhuma|nenhum|ningun|none)\b")
_CODE = re.compile(r"(?i)(?:\b(USD|MXN|COP|ARS|BRL)\s*\$?\s*\d|\d[\d.,]*\s*(USD|MXN|COP|ARS|BRL)\b|(R\$|US\$)\s*\d)")
_EXPLICIT_DATE = re.compile(r"\b(\d{1,2}) de (%s)(?: de (\d{4}))?\b" % "|".join(MONTHS))
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def pick_option(message: str, n_options: int) -> int | None:
    text = plain(message).strip(" .!?")
    if len(text.split()) > 6:
        return None
    if m := re.fullmatch(r"(?:(?:la|el|a|o|opcion|opcao|numero|n)\s*)*(\d)\b.*", text):
        value = int(m.group(1))
        return value if 1 <= value <= n_options else None
    for word in re.findall(r"[a-z]+", text):
        if word in _ORDINALS and _ORDINALS[word] <= n_options:
            return _ORDINALS[word]
        if word in {"ultima", "ultimo"}:
            return n_options
    return None


def _explicit_date(text: str, today: date) -> date | None:
    if m := _ISO_DATE.search(text):
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    if not (m := _EXPLICIT_DATE.search(text)):
        return None
    day, month = int(m.group(1)), MONTHS[m.group(2)]
    years = [int(m.group(3))] if m.group(3) else [today.year, today.year - 1]
    for year in years:
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate <= today or m.group(3):
            return candidate
    return None


def _currency(message: str, parsed_currency: str | None) -> str | None:
    if m := _CODE.search(message):
        code = (m.group(1) or m.group(2) or m.group(3) or "").upper()
        return {"R$": "BRL", "US$": "USD"}.get(code, code)
    return parsed_currency if parsed_currency in {"USD", "PESOS", "COP", "ARS"} else None


def parse_intent(message: str, today: date, n_options: int = 0, reason: str | None = None) -> DisputeIntent:
    """Deterministic understanding. Never guesses a value the text does not state."""
    text = plain(message)
    base: dict[str, Any] = {"source": "deterministic_parser", "fallback_reason": reason}
    ref = _REF_IN_TEXT.search(message.upper())
    if ref:
        base["record_ref"] = ref.group(1)
    if _HUMAN.search(text):
        return DisputeIntent(intent="request_human", **base)
    if n_options:
        if _REJECT.search(text):
            return DisputeIntent(intent="reject_options", **base)
        if (choice := pick_option(message, n_options)) is not None:
            return DisputeIntent(intent="select_option", selected_option=choice, **base)
    if _BLOCK.search(text):
        return DisputeIntent(intent="block_card", **base)
    for pattern, topic in _TOPICS.items():
        if re.search(pattern, text):
            return DisputeIntent(intent="out_of_scope", topic=topic, **base)
    parsed = parse_description(message, today)
    when, tolerance = _explicit_date(text, today), 0
    if when is None and parsed.date_lo is not None and parsed.date_hi is not None:
        half = (parsed.date_hi - parsed.date_lo).days // 2
        when, tolerance = parsed.date_lo + timedelta(days=half), min(15, half + 1)
    amount = parsed.amount if parsed.amount and parsed.amount > 0 else None
    return DisputeIntent(intent="dispute_charge", amount=amount, currency=_currency(message, parsed.currency),
                         date=when, date_tolerance_days=tolerance, **base)


def text_cues(text: str, today: date) -> set[str]:
    """Cue kinds the deterministic parser can read in the accumulated text (merchant names are checked later
    against the customer's own charges, since only those can be matched)."""
    parsed = parse_description(text, today)
    found = {"amount"} if parsed.amount else set()
    found |= {"date"} if parsed.date_lo is not None or _explicit_date(plain(text), today) else set()
    found |= {"type"} if parsed.types else set()
    found |= {"channel"} if parsed.channels else set()
    return found | ({"merchant"} if parsed.noun_merchants else set())


def validation_reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        fields = sorted({".".join(map(str, e["loc"])) or "_" for e in exc.errors()})
        return "llm_invalid_output:" + ",".join(fields)[:80]
    return type(exc).__name__
