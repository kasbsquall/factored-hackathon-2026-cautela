"""Customer-facing replies in Spanish or Portuguese, built only from verified facts.

The model (MaskedLLM.reply) may phrase the message. Its text is used only if it passes `check_grounded`:
  * every number in the reply appears in the facts it was given (no invented amounts, dates or deadlines);
  * every string listed in `must_mention` (case ids, option labels) is present;
  * it reads as the requested language (a small function-word count, enough to catch a reply in the other one);
  * it is not empty and not too long.
Otherwise the deterministic template below is used and the trail records why. With no model configured the
template is always used. The confirmation token is never part of the facts, so it cannot reach a reply.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from agent.llm.port import LanguageModel, LLMUnavailable
from agent.orchestrator.intent import plain

MAX_REPLY_CHARS = 900

REASONS = {
    "es": {
        "amount_above_threshold": "por el monto, la revisión la hace una persona del banco",
        "suspected_fraud": "hay señales que debe revisar un especialista en fraude",
        "policy_requires_review": "según la política aplicable, este caso lo debe revisar una persona",
        "low_confidence": "no pude identificar el cargo con seguridad",
        "tool_failure": "tuvimos un problema técnico y no pude comprobar el registro",
        "security_event": "por seguridad, esta conversación pasa a un agente",
        "out_of_scope": "esa solicitud no la puedo atender por este canal",
        "customer_requested_human": "pediste hablar con una persona",
    },
    "pt": {
        "amount_above_threshold": "pelo valor, a análise é feita por uma pessoa do banco",
        "suspected_fraud": "há sinais que precisam ser analisados por um especialista em fraude",
        "policy_requires_review": "pela política aplicável, este caso precisa ser analisado por uma pessoa",
        "low_confidence": "não consegui identificar a cobrança com segurança",
        "tool_failure": "tivemos um problema técnico e não consegui confirmar o registro",
        "security_event": "por segurança, esta conversa passa para um atendente",
        "out_of_scope": "não consigo atender esse pedido por este canal",
        "customer_requested_human": "você pediu para falar com uma pessoa",
    },
}
CUES = {"es": {"amount": "el monto", "date": "la fecha", "merchant": "el comercio"},
        "pt": {"amount": "o valor", "date": "a data", "merchant": "a loja"}}
STATUS = {"es": {"Declined": "fue rechazado, así que no se movió dinero", "Reversed": "ya fue revertido"},
          "pt": {"Declined": "foi recusada, então nenhum dinheiro saiu", "Reversed": "já foi estornada"}}

TEMPLATES: dict[str, dict[str, str]] = {
    "ask_details": {
        "es": "Para ubicar el cargo necesito un dato más: ¿me dices {missing}?",
        "pt": "Para localizar a cobrança preciso de mais um dado: pode me dizer {missing}?"},
    "clarify_options": {
        "es": "Encontré más de un cargo que coincide. ¿Cuál no reconoces?\n{options}\nSi no es ninguno, dímelo.",
        "pt": "Encontrei mais de uma cobrança parecida. Qual você não reconhece?\n{options}\nSe não for nenhuma, "
              "me avise."},
    "confirm_open": {
        "es": "Voy a registrar una disputa por este cargo: {label}. {review}Confírmalo con el botón para continuar.",
        "pt": "Vou registrar uma contestação desta cobrança: {label}. {review}Confirme no botão para continuar."},
    "confirm_card": {
        "es": "Voy a bloquear la tarjeta {label}. Confírmalo con el botón para continuar.",
        "pt": "Vou bloquear o cartão {label}. Confirme no botão para continuar."},
    "resolved": {
        "es": "Listo. Registré la disputa del cargo {label} con el número de caso {case_id}. Comprobé en el "
              "sistema que el caso quedó abierto.",
        "pt": "Pronto. Registrei a contestação da cobrança {label} com o número de caso {case_id}. Conferi no "
              "sistema que o caso está aberto."},
    "card_blocked": {
        "es": "Listo. La tarjeta {label} quedó bloqueada; lo comprobé en el sistema.",
        "pt": "Pronto. O cartão {label} está bloqueado; conferi no sistema."},
    "handed_off": {
        "es": "Te paso con una persona del banco: {reason}. {case}Quien te atienda recibe los datos que ya "
              "comprobé de tu caso.",
        "pt": "Vou te passar para uma pessoa do banco: {reason}. {case}Quem for te atender recebe os dados que já "
              "conferi do seu caso."},
    "not_disputable": {
        "es": "El cargo {label} {status}. Por eso no hay una disputa que abrir.",
        "pt": "A cobrança {label} {status}. Por isso não há contestação para abrir."},
    "declined": {"es": "Entendido, no registré nada.", "pt": "Entendido, não registrei nada."},
    "recognize_check": {
        "es": "Antes de abrir una disputa, revisa cómo aparece este cargo en tu cuenta: {label}. ¿Lo reconoces?",
        "pt": "Antes de abrir uma contestação, veja como esta cobrança aparece na sua conta: {label}. Você a "
              "reconhece?"},
    "recognized": {
        "es": "Gracias por revisarlo. No abrí ninguna disputa por el cargo {label} y no cambié nada en tu cuenta.",
        "pt": "Obrigado por conferir. Não abri contestação para a cobrança {label} e não mudei nada na sua conta."},
    "pending_recognition": {
        "es": "Antes de seguir, dime con los botones si reconoces el cargo {label}.",
        "pt": "Antes de continuar, me diga nos botões se você reconhece a cobrança {label}."},
    "ref_not_found": {
        "es": "No encontré esa referencia entre tus movimientos. ¿Puedes revisarla o decirme el monto, la fecha "
              "o el comercio del cargo?",
        "pt": "Não encontrei essa referência nos seus movimentos. Pode conferir ou me dizer o valor, a data ou a "
              "loja da cobrança?"},
    "auth_required": {
        "es": "Tu sesión {why}. Vuelve a identificarte con tu documento y el código que te enviamos; lo que ya "
              "me contaste queda guardado.",
        "pt": "Sua sessão {why}. Identifique-se de novo com seu documento e o código que enviamos; o que você já "
              "me contou continua salvo."},
    "pending_confirmation": {
        "es": "Tienes una confirmación pendiente para {label}. Usa los botones para confirmar o cancelar.",
        "pt": "Há uma confirmação pendente para {label}. Use os botões para confirmar ou cancelar."},
    "closed": {"es": "Esta conversación ya terminó. Si necesitas algo más, abre una nueva.",
               "pt": "Esta conversa já terminou. Se precisar de mais alguma coisa, abra uma nova."},
    "no_active_card": {"es": "No encontré una tarjeta activa para bloquear.",
                       "pt": "Não encontrei um cartão ativo para bloquear."},
    "card_options": {"es": "¿Qué tarjeta quieres bloquear?\n{options}", "pt": "Qual cartão você quer bloquear?\n{options}"},
}
WHY = {"es": {"session_expired": "venció", "default": "no es válida"},
       "pt": {"session_expired": "expirou", "default": "não é válida"}}
REVIEW = {"es": "Aviso: {reason}. ", "pt": "Aviso: {reason}. "}
CASE_NOTE = {"es": "Tu caso quedó registrado con el número {case_id}. ",
             "pt": "Seu caso foi registrado com o número {case_id}. "}
_ES_ONLY = {"el", "los", "usted", "tu", "tus", "ya", "hay", "cargo", "puedes", "necesito", "quedo", "registre"}
_PT_ONLY = {"voce", "o", "os", "seu", "sua", "nao", "cobranca", "pode", "preciso", "foi", "registrei", "esta"}


@dataclass(frozen=True)
class Reply:
    text: str
    source: str  # "llm" or "template"
    note: str | None = None


def render_template(kind: str, lang: str, fields: Mapping[str, Any]) -> str:
    return TEMPLATES[kind][lang].format(**fields)


def reason_text(code: str | None, lang: str) -> str:
    return REASONS[lang].get(code or "", REASONS[lang]["policy_requires_review"])


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def _language_ok(text: str, lang: str) -> bool:
    words = re.findall(r"[a-z]+", plain(text))
    es, pt = sum(w in _ES_ONLY for w in words), sum(w in _PT_ONLY for w in words)
    return pt >= es if lang == "pt" else es >= pt


def check_grounded(text: str, facts_text: str, must_mention: Iterable[str], lang: str) -> str | None:
    """Return None when the reply is usable, else the reason it was rejected."""
    if not text.strip():
        return "empty"
    if len(text) > MAX_REPLY_CHARS:
        return "too_long"
    extra = _numbers(text) - _numbers(facts_text)
    if extra:
        return "number_not_in_facts"
    missing = [m for m in must_mention if m not in text]
    if missing:
        return "missing_required_mention"
    if not _language_ok(text, lang):
        return "wrong_language"
    return None


def compose(llm: LanguageModel | None, kind: str, lang: str, fields: Mapping[str, Any],
            must_mention: Iterable[str] = (), known_names: Iterable[str] = (), trace_id: str | None = None) -> Reply:
    template = render_template(kind, lang, fields)
    if llm is None:
        return Reply(template, "template", "no_llm_configured")
    facts = {"message_kind": kind, "language": lang, "facts": dict(fields), "must_mention": list(must_mention),
             "reference_wording": template}
    try:
        text = llm.reply(facts, lang, known_names=known_names, trace_id=trace_id)
    except (LLMUnavailable, ValueError) as exc:
        return Reply(template, "template", f"llm_failed:{type(exc).__name__}")
    rejected = check_grounded(text, template + " " + " ".join(map(str, fields.values())), must_mention, lang)
    if rejected:
        return Reply(template, "template", f"llm_reply_rejected:{rejected}")
    return Reply(text.strip(), "llm")
