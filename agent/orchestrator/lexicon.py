"""Request lexicon for Spanish and Portuguese: asking for a person, and banking requests outside dispute intake.

Used by the deterministic parser (intent.parse_intent), with the LLM off and as the escalation floor with it on.
Every pattern runs on `intent.plain` text (lowercase, accents removed), so "límite" and "limite", "alguém" and
"alguem" are the same string. Patterns are grouped by what they recognize, not by the phrasings of any test suite;
tests/orchestrator/test_lexicon.py checks them on phrasings written for that test.

Asking for a person (`asks_for_human`) is one of three shapes:
  1. a verb of talking or being attended (hablar, conversar, comunicar, atender; falar, conversar, atender) near a
     person noun, including the generic ones (persona, alguien; pessoa, alguém);
  2. a verb of routing (pasar, transferir, derivar, conectar, poner; passar, transferir, encaminhar) aimed at the
     customer ("pásame", "me transfiere") near a person noun, or near a staff noun without the clitic. "Transferir
     a otra persona" is a money transfer, so routing verbs never pair with a generic person noun on their own;
  3. a phrase that names a human against a machine ("de carne y hueso", "alguém de verdade", "en vez de un robot",
     "atención humana"), or a want verb right before a staff noun ("quiero un asesor", "preciso de um atendente").
Staff nouns cover regional usage: asesor/asesora (all), ejecutivo (CL, MX, PE, CO), operador (all), gerente (all;
in Brazil the account manager), atendente and consultor (BR), representante, supervisor, encargado, funcionario.

Out-of-scope topics (`out_of_scope_topic`) come in two strengths:
  explicit  a request verb and its object ("aumentar el cupo", "pedir un préstamo", "cerrar mi cuenta",
            "fazer um pix"). An explicit request wins over a dispute in the same message, except for the topics in
            DISPUTE_OVERLAP (a transfer, a refund, a loan), whose words also describe the charge being disputed:
            those yield to a dispute signal (an amount alone does not make "transferir 500 pesos" a dispute).
  mention   a topic noun alone ("horario", "certificado bancario", "préstamo"). A mention counts only when the
            message carries no dispute signal and no charge cue, because "vi en mi extracto un cargo" or "la cuota
            del préstamo que no reconozco" are disputes.
Regional notes: cupo (CO, CL, EC: credit limit), tope (AR), lana and plata (MX, AR, CO: money), grana (BR: money),
pix/TED/DOC (BR transfers), CDT (CO), plazo fijo (AR), CDB and Tesouro Direto (BR), paz y salvo (CO: clearance
letter).

`dispute_signal` says whether the text reads as a dispute at all: a non-recognition phrase ("no reconozco",
"yo no fui", "ni idea qué es", "não fui eu"), a charge that "appeared" ("me cayó un cargo", "caiu uma cobrança"),
or a dispute verb with its object ("reclamar un cobro", "contestar uma cobrança"). `mentions_charge` adds the
charge nouns themselves ("un cobro", "uma cobrança", "un débito").
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_W = r"(?:\S+\s+)"  # one intervening word

# ---- asking for a person --------------------------------------------------------------------------------------
_STAFF = (r"(?:asesor(?:a|es)?|agente(?:s)? humano|ejecutiv[oa]s?|operador(?:a|es)?|representante|gerente|"
          r"supervisor(?:a)?|encargad[oa]|funcionari[oa]|emplead[oa] del banco|atendente|consultor(?:a)?|"
          r"humano|humana|ser humano)")
_PERSON = rf"(?:{_STAFF}|persona|personas|alguien|pessoa|pessoas|alguem|agente)"
_TALK = (r"(?:hablar|hable|habla|conversar|converse|comunicar(?:me|nos)?|comunicame|comuniquen(?:me)?|"
         r"comunique(?:me)?|me comunica[sn]?|atienda|atiendan|atenderme|atender|contactar(?:me)?|llamar(?:me)?|"
         r"llame(?:n)?me|falar|fale|atenda|atendam|me atender|ligar|ligue|me ligue)")
_ROUTE_ME = (r"(?:pasame|pasarme|me pasas?|me pasen|me pasan|me pase|pasenme|transfiereme|transferirme|"
             r"me transfier[ea]s?|me transfieran|transfieranme|derivame|derivarme|me deriv[ae]s?|me deriven|"
             r"conectame|conectarme|me conect[ae]s?|me conecten|ponme|ponerme|pongame|ponganme|me pon(?:es|ga|gan)|"
             r"me passa|me passe|me passem|me transfer[ea]|me transfira|me encaminh[ae]|me conect[ae])")
_ROUTE = r"(?:pasar|passar|transferir|derivar|encaminhar|conectar|escalar)"
_MACHINE = (r"(?:robot|robo|bot|chatbot|maquina|contestadora|ia|inteligencia artificial|asistente virtual|"
            r"assistente virtual)")
_WANT = (r"(?:quiero|quisiera|necesito|prefiero|pido|exijo|solicito|me gustaria|dame|denme|quero|queria|preciso|"
         r"prefiro|peco|gostaria)")

HUMAN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(p) for p in (
    rf"\b{_TALK}\s+{_W}{{0,3}}{_PERSON}\b",
    rf"\b{_ROUTE_ME}\s+{_W}{{0,4}}{_PERSON}\b",
    rf"\b{_ROUTE}\s+{_W}{{0,3}}{_STAFF}\b",
    rf"\b{_WANT}\s+(?:de\s+|con\s+|com\s+)?(?:un|una|um|uma|el|la|o|a|al|ao)\s+{_STAFF}\b",
    r"\bcarne (?:y hueso|e osso)\b",
    r"\b(?:alguien|persona|alguem|pessoa)(?: \S+){0,2} (?:real|de verdad|de verdade)\b",
    r"\b(?:atencion|atendimento|asesoria|asesor|agente|atendente) (?:humana|humano|personalizada|personal)\b",
    r"\b(?:en vez de|en lugar de|em vez de|no quiero hablar con|nao quero falar com)\s+"
    rf"(?:un |una |el |la |um |uma |o |a |este |esse )?{_MACHINE}\b",
    rf"\b(?:no|nao) (?:quiero|quero) (?:un |una |um |uma )?{_MACHINE}\b",
))


def asks_for_human(text: str) -> str | None:
    """The phrase that asks for a person, or None. `text` must be plain (see intent.plain)."""
    for pattern in HUMAN_PATTERNS:
        if m := pattern.search(text):
            return m.group(0)
    return None


# ---- dispute signal ---------------------------------------------------------------------------------------------
_CHARGE = (r"(?:cargo|cobro|cobrito|cobranca|compra|debito|descuento|retiro|extraccion|saque|pago|pagamento|"
           r"transferencia|movimiento|movimento|consumo|transaccion|transacao)")
DISPUTE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(p) for p in (
    r"\b(?:no|nao) (?:lo |la |los |las |o |a )?(?:reconozco|reconoco|reconheco|reconhecemos|reconocemos)\b",
    r"\b(?:desconozco|desconheco)\b",
    r"\b(?:no|nao) (?:me suena|me soa|me lembro|recuerdo|lo hice|la hice|hice|realice|autorice|fiz|realizei|"
    r"autorizei)\b",
    r"\b(?:yo no fui|no fui yo|nao fui eu|eu nao fiz)\b",
    r"\b(?:no|nao) (?:es|e) (?:mio|mia|meu|minha)\b",
    r"\b(?:ni idea|no se|nao sei|nao faco ideia)(?: \S+){0,2} (?:que es|de que|de donde|o que e|do que e|de onde)\b",
    r"\b(?:me|nos) (?:cobraron|cobraram|debitaron|debitaram|descontaron|descontaram|sacaron)\b",
    r"\b(?:indebid[oa]|indevid[oa]|fraude|fraudulent[oa]|estafa|golpe|clonaron|clonaram|clonada|clonado)\b",
    rf"\b(?:me )?(?:cayo|llego|salio|aparecio|aparece|aparecen|caiu|apareceu|veio|entrou)\s+{_W}?{_CHARGE}\b",
    rf"\b(?:reclamar|disputar|impugnar|objetar|contestar|desconocer|reclamo por|disputa por)\s+{_W}{{0,2}}"
    rf"{_CHARGE}\b",
    # asking the bank what a charge is means asking about one on the account ("¿qué es un cobro que me salió?"):
    # about a third of the validation descriptions take this form (eval/cases/disputes/val.jsonl)
    rf"\b(?:que es|que seria|o que e|o que seria) (?:este|ese|esta|esa|un|una|el|la|esse|essa|um|uma|o|a) "
    rf"{_CHARGE}\b",
))
_CHARGE_NOUN = re.compile(rf"\b{_CHARGE}s?\b")
_NEGATED = re.compile(r"\b(?:no|nao) (?:lo |la |los |las |o |a )?([a-z]{6,})\b")
_RECOGNIZE = ("reconozco", "reconheco", "reconocemos", "reconhecemos")


def dispute_signal(text: str) -> bool:
    if any(p.search(text) for p in DISPUTE_PATTERNS):
        return True
    # "no lo reconnozco", "nao reconheso": a misspelled "recognize" after a negation (edit ratio >= 0.8)
    return any(SequenceMatcher(None, m.group(1), verb).ratio() >= 0.8
               for m in _NEGATED.finditer(text) for verb in _RECOGNIZE)


def mentions_charge(text: str) -> bool:
    """A dispute signal, or a word for a charge ("cargo", "cobro", "cobrança", "compra", "débito", ...)."""
    return bool(_CHARGE_NOUN.search(text)) or dispute_signal(text)


# ---- out-of-scope topics ----------------------------------------------------------------------------------------
DISPUTE_OVERLAP = frozenset({"money_transfer", "immediate_refund", "loan_application"})
_REQUEST = (r"(?:quiero|quisiera|necesito|me gustaria|como (?:hago|puedo)|puedo|podria|me ayudas? a|ayudame a|"
            r"deseo|voy a|quero|queria|preciso|gostaria|como (?:faco|posso)|posso|me ajuda a|da para)")
_UP_TO_2, _UP_TO_3 = r"(?:\S+\s+){0,2}", r"(?:\S+\s+){0,3}"

TOPIC_PATTERNS: dict[str, dict[str, tuple[str, ...]]] = {
    "credit_limit_increase": {
        "explicit": (rf"\b(?:aument|sub|ampli|elev|increment|mejor)\w*\s+{_UP_TO_2}(?:cupo|limite|tope)\b",
                     r"\b(?:aumento|ampliacion|ampliacao|incremento|subida) (?:de|del|do) (?:cupo|limite|tope)\b",
                     r"\b(?:mas|mais) (?:cupo|limite)\b", r"\b(?:cupo|limite) (?:mas alto|maior|mais alto)\b"),
        "mention": (r"\bcupo\b", r"\blimite de credito\b", r"\blimite do cartao\b",
                    r"\blimite de (?:la|mi) tarjeta\b"),
    },
    "loan_application": {
        "explicit": (rf"\b(?:{_REQUEST}|pedir|solicitar|sacar|tramitar|tomar|fazer|contratar)\s+{_UP_TO_2}"
                     r"(?:prestamo|emprestimo|financiamiento|financiamento|consignado|credito (?:personal|pessoal|"
                     r"hipotecario|imobiliario|de consumo|vehicular|automotriz|de libre inversion))\b",
                     r"\b(?:me prestan|me pueden prestar|me emprestam|podem me emprestar)\b"),
        "mention": (r"\b(?:prestamo|emprestimo|hipoteca|credito personal|credito pessoal)\b",),
    },
    "money_transfer": {
        "explicit": (rf"\b(?:{_REQUEST})\s+{_UP_TO_2}(?:transferir|enviar|mandar|girar|depositar)\b",
                     r"\b(?:hacer|realizar|fazer|mandar) (?:una|un|uma|um) (?:transferencia|giro|pix|ted|doc)\b",
                     r"\b(?:enviar|mandar|girar) (?:dinero|plata|lana|dinheiro|grana)\b",
                     r"\btransferir (?:dinero|plata|dinheiro)\b"),
        "mention": (),
    },
    "immediate_refund": {
        "explicit": (r"\b(?:devuelvan(?:me)?|devuelveme|devolverme|devolvam|me devolvam|reembolsen(?:me)?|"
                     r"reintegren(?:me)?)\b",
                     r"\b(?:quiero|quero) (?:que me devuelvan |que me devolvam )?(?:mi |el |meu |o )?"
                     r"(?:dinero|dinheiro|plata)(?: \S+){0,2} (?:de vuelta|de volta|ya|agora|ahora)\b",
                     r"\b(?:reembolso|devolucion|devolucao|reintegro|estorno) (?:ya|ja|ahora|agora|inmediat[oa]|"
                     r"imediat[oa]|hoy|hoje)\b"),
        "mention": (),
    },
    "change_personal_data": {
        "explicit": (r"\b(?:actualizar|cambiar|modificar|corregir|editar|alterar|atualizar|mudar|trocar)\s+"
                     rf"{_UP_TO_3}(?:datos|direccion|domicilio|correo|email|e-mail|telefono|celular|nombre|dados|"
                     r"endereco|nome)\b",
                     r"\bnumeros? complet[oa]s?\b"),
        "mention": (),
    },
    "account_closure": {
        "explicit": (rf"\b(?:cerrar|cancelar|dar de baja|eliminar|encerrar|fechar)\s+{_UP_TO_2}"
                     r"(?:cuenta|cuentas|conta|contas)\b",
                     r"\bdarme de baja\b"),
        "mention": (),
    },
    "investment_advice": {
        "explicit": (r"\b(?:invertir|investir)\b",
                     r"\bdonde (?:me conviene )?(?:poner|meter) (?:mi|mis) (?:plata|dinero|ahorros)\b",
                     r"\bonde (?:vale a pena )?(?:colocar|aplicar) (?:meu|o) dinheiro\b"),
        "mention": (r"\b(?:inversion|inversiones|investimento|investimentos|cdt|plazo fijo|fondos? de inversion|cdb|"
                    r"tesouro direto|renda fixa|criptomonedas?|criptomoedas?)\b",),
    },
    "other_customer_request": {
        "explicit": (r"\b(?:todas las cuentas|otro cliente|otra cuenta que no es mia|outro cliente|todas as contas)\b",
                     r"\ba que hora (?:abre|abren|cierra|cierran|atienden)\b",
                     r"\bque horas? (?:abre|fecha|funciona)\b",
                     r"\b(?:sucursal|oficina|agencia|cajero|caixa eletronico) mais proxim[oa]\b",
                     r"\b(?:sucursal|oficina|agencia|cajero) mas cercan[oa]\b",
                     r"\b(?:donde|onde) (?:queda|esta|hay|fica|tem) (?:\S+ )?"
                     r"(?:sucursal|oficina|agencia|cajero|caixa)\b",
                     rf"\b(?:{_REQUEST}|pedir|solicitar|sacar|tramitar|emitir|emitan|emitam)\s+{_UP_TO_2}"
                     r"(?:certificado|constancia|carta (?:de referencia|bancaria)|referencia bancaria|paz y salvo|"
                     r"declaracao|comprovante|chequera|talonario|tarjeta adicional|cartao adicional|nueva tarjeta|"
                     r"novo cartao)\b",
                     rf"\b(?:{_REQUEST})\s+{_UP_TO_2}(?:abrir|abra) (?:una|um|uma) (?:cuenta|conta)\b",
                     r"\b(?:olvide|recuperar|cambiar|restablecer|esqueci|trocar|redefinir) (?:mi |la |minha |a )?"
                     r"(?:clave|contrasena|senha|pin)\b",
                     rf"\b(?:{_REQUEST}|consultar)\s+{_UP_TO_2}(?:saldo)\b",
                     r"\b(?:cual|qual) (?:es |e )?(?:el |o )?(?:mi |meu )?saldo\b"),
        "mention": (r"\bhorarios?\b", r"\bcertificado bancario\b", r"\bconstancia\b",
                    r"\bdeclaracao (?:do banco|bancaria)\b", r"\bpaz y salvo\b", r"\breferencia bancaria\b"),
    },
}
_COMPILED = {topic: {k: tuple(re.compile(p) for p in ps) for k, ps in kinds.items()}
             for topic, kinds in TOPIC_PATTERNS.items()}


def out_of_scope_topic(text: str, has_charge_cue: bool = False) -> tuple[str, str] | None:
    """(topic, matched phrase) for a request outside dispute intake, or None. `text` must be plain.

    Explicit requests are checked first for every topic, then mentions; the precedence rules are in the module
    docstring. `has_charge_cue` is whether the parser read an amount, a date or a merchant in the same text.
    """
    disputed = dispute_signal(text)
    for topic, kinds in _COMPILED.items():
        if topic in DISPUTE_OVERLAP and disputed:
            continue
        for pattern in kinds["explicit"]:
            if m := pattern.search(text):
                return topic, m.group(0)
    if disputed or has_charge_cue:
        return None
    for topic, kinds in _COMPILED.items():
        for pattern in kinds["mention"]:
            if m := pattern.search(text):
                return topic, m.group(0)
    return None
