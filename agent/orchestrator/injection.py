"""Deterministic prompt-injection detection, run on every customer message before any model call.

A customer describing a charge talks about the charge. An injection talks to the assistant: it gives it orders,
claims to be its system or its boss, or tries to switch off a control. Detection looks for those acts, in Spanish
and Portuguese (and English fragments inside them), on `intent.plain` text:

  marker     explicit jailbreak syntax: "ignora tus instrucciones", "SYSTEM:", "modo desarrollador", a SQL
             statement, a fake closing tag. One is enough.
  strong     one is enough:
             role         reassigning the assistant's role ("a partir de ahora eres", "actúa como", "you are now")
             ai_note      a note addressed to the model ("nota para el asistente", "tus instrucciones son")
             system_claim text that claims to be instructions or policy of the system ("instrucción del sistema",
                          "actualización de política interna", a "###" header, a bracketed [nota/instrucción])
  weak       two different kinds are needed:
             skip_control asking to switch off confirmation, verification, review or policy ("sin confirmar",
                          "ignora la verificación", "aprueba todo", "marca el caso como aprobado")
             ai_address   a conditional address to a model ("si eres una IA", "se você é um bot"): a customer may
                          say it honestly ("si eres un bot, pásame con alguien"), so it needs a second signal
             authority    speaking for the assistant's operator ("tu supervisor", "soy el administrador del sistema")
             output       dictating the reply ("responde solo", "escribe 'X'", a code-like token such as ABC-1234)
             execute      asking it to run something ("ejecútalo", "executa")
             english      English imperatives inside an es/pt message ("approve", "ignore", "reply", "bypass")

An impatient customer ("no me hagas confirmar nada", "sem pedir confirmação, estou com pressa") gives one weak signal
and is not flagged: the confirmation is simply still required. False positives are measured on validation dispute
descriptions (tests/orchestrator/test_injection.py). The result feeds SYN-SEC-001 (security_event). A model may add
a flag (intent.injection_quote) when its quote is in the message and carries at least one signal other than
skip_control: a single signal is below this detector's bar, and a request to skip a step is a customer's wish that
the confirmation and policy layers already refuse, not an order to the assistant. The model can never remove this
detector's flag, because this check runs before the model is called.
"""

from __future__ import annotations

import re

_MARKER = re.compile("|".join([
    r"\bignor[ae]\w* (?:\w+ ){0,3}(?:instrucc|instru[cç]|regras|reglas|prompt)",
    r"\b(?:olvida|esquec[ea]) (?:\w+ ){0,3}(?:instrucc|instru[cç]|regras|reglas)",
    r"^\s*(?:system|sistema)\s*:", r"</?\s*(?:instru|system|sistema)", r"\[(?:system|sistema)\]",
    r"\bmodo (?:desarrollador|desenvolvedor|administrador|admin)\b", r"\bdeveloper mode\b",
    r"\bpermis(?:o|os|s[aã]o) de administrador\b", r"\b(?:nuevo|novo) contexto\b",
    r"\bselect\s+\*?\s*\w*\s*from\b", r"\btoken de confirma", r"\b(?:usa|use) (?:esta|essa) (?:sesi[oó]n|sess[aã]o)",
    r"\bel usuario autenticado (?:ahora )?es\b", r"\bo usu[aá]rio autenticado\b",
]), re.IGNORECASE | re.MULTILINE)

_BOT = (r"(?:una? |uma? )?(?:ia|inteligencia artificial|bot|chatbot|robot|robo|modelo(?: de lenguaje)?|llm|"
        r"asistente|assistente|gpt|chatgpt)")
_CONTROL = r"(?:confirma\w*|verifica\w*|revision|revisao|validacion|validacao|politica|seguridad|seguranca|control\w*)"
_STRONG = {
    "role": (r"\b(?:a partir de ahora|desde ahora|de ahora en adelante|a partir de agora|de agora em diante)\b"
             r"[^.;]{0,30}\b(?:eres|seras|sos|actua|actuaras|voce e|voce sera|seja|sera|aja|atue)\b",
             r"(?:^|[.,;:!?]\s*|\b(?:ahora|agora|que)\s+)(?:actua|actues|actue|aja|atue|atues) como\b",
             r"\bhaz de cuenta que eres\b", r"\bfaca de conta que\b",
             r"\b(?:finge|fingi|finja|simula) (?:que eres|ser|que e|que voce e)\b", r"\bpretende que eres\b",
             r"\byou are (?:now )?(?:a|an|my)\b", r"\bact as\b", r"\bpretend (?:to be|you are)\b"),
    "ai_note": (rf"\b(?:nota|mensaje|mensagem|aviso|recado|instruccion|instrucao) (?:para|al|a la|ao|a|pro|pra) "
                rf"(?:el |la |o |a )?{_BOT}\b",
                r"\b(?:tus|sus|suas|tuas) (?:instrucciones|instrucoes|reglas|regras|directivas) (?:ahora son|"
                r"agora sao|nuevas son|novas sao|son las siguientes|sao as seguintes|cambiaron|mudaram)\b"),
    "system_claim": (r"\b(?:instruccion(?:es)?|instrucao|instrucoes|mensaje|mensagem|prompt|directiva|diretiva|"
                     r"comando|orden) (?:del|do|da|de) "
                     r"(?:sistema|administrador|desarrollador|desenvolvedor|operador)\b",
                     r"\b(?:actualizacion|nueva|nuevo|cambio|atualizacao|nova|novo|mudanca) (?:de |da |do |en |na )?"
                     r"(?:la |a |las |as )?(?:politica|regla|regra|norma|instruccion|instrucao)s? internas?\b",
                     r"(?:^|[.:!?]\s*)#{2,}\s",
                     r"\[\s*(?:instruccion|instrucao|nota interna|admin|developer|dev|sistema)[^\]]*\]"),
}
_WEAK = {
    "skip_control": (r"\bsin (?:pedir(?:me|le)? |solicitar |hacer |necesidad de )?"
                     r"(?:confirma\w*|verifica\w*|revision|validacion)",
                     r"\bsem (?:pedir |solicitar |fazer )?(?:confirma\w*|verifica\w*|revisao|validacao)",
                     r"\b(?:ignora|ignore|omite|omita|saltate|salta|salte|pula|pule|desactiva|desativa)\w* "
                     rf"(?:\S+ ){{0,2}}{_CONTROL}",
                     r"\b(?:no|nao) (?:necesita\w*|requiere\w*|precisa\w*|hace falta|e necessario|es necesario) "
                     r"(?:de )?confirma\w*",
                     r"\b(?:aprueb[ae]s?|aprueben|aprobar|aprove|aprovar|aprova|autoriza|autorize) "
                     r"(?:todo|tudo|el reembolso|o estorno|la devolucion|a devolucao)\b",
                     r"\b(?:marca|marque|pon|ponga|deja|coloca|mude|muda|cambia)\w* (?:el|o|mi|meu) caso "
                     r"(?:como |para )?(?:aprobado|aprovado|resuelto|resolvido|cerrado)\b"),
    "ai_address": (rf"\b(?:si|se) (?:eres|sos|es|e|voce e|vc e|tu es) {_BOT}\b",),
    "authority": (r"\b(?:tu|seu|teu) (?:supervisor|jefe|chefe|administrador|desarrollador|desenvolvedor|creador|"
                  r"criador|programador)\b",
                  r"\b(?:soy|sou) (?:el |la |o |a |tu |su |seu )?(?:administrador|desarrollador|desenvolvedor|"
                  r"programador|ingeniero|engenheiro|operador) (?:del|de|do|da) (?:sistema|bot|asistente|"
                  r"assistente|modelo)\b",
                  r"\b(?:el banco|o banco|la gerencia|a gerencia|seguridad|seguranca) (?:ya )?"
                  r"(?:autorizo|autorizou|aprobo|aprovou) (?:todo|tudo|esto|isso)\b"),
    "output": (r"\b(?:responde|responda|contesta|conteste|escribe|escriba|escreva|diga|repite|repita|imprime|imprima)\b"
               r"[^.]{0,30}?(?:\"[^\"]{2,40}\"|'[^']{2,40}'|\b[a-z]{3,}-\d{3,}\b)",
               r"\b(?:responde|responda|contesta|conteste) (?:solo|solamente|unicamente|apenas|somente)\b"),
    "execute": (r"\b(?:ejecuta|ejecutalo|ejecutala|ejecute|ejecutar|executa|execute|executar|executalo)\b",),
    "english": (r"\b(?:approve|reply|respond|disregard|override|bypass|jailbreak|system prompt)\b",),  # "ignore" is pt
}
_STRONG_RE = {k: tuple(re.compile(p, re.MULTILINE) for p in ps) for k, ps in _STRONG.items()}
_WEAK_RE = {k: tuple(re.compile(p) for p in ps) for k, ps in _WEAK.items()}


def _first(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        if m := pattern.search(text):
            return m.group(0)
    return None


def signals(plain_text: str) -> set[str]:
    """Every strong and weak signal kind present in the text (markers count as "marker")."""
    found = {"marker"} if _MARKER.search(plain_text) else set()
    found |= {kind for kind, patterns in _STRONG_RE.items() if _first(patterns, plain_text)}
    return found | {kind for kind, patterns in _WEAK_RE.items() if _first(patterns, plain_text)}


def detect(message: str, plain_text: str) -> str | None:
    """A short description of why the message is an injection attempt, or None.

    `message` is the raw text (for the explicit markers, which include case and symbols); `plain_text` is the same
    message after intent.plain.
    """
    if m := _MARKER.search(message) or _MARKER.search(plain_text):
        return m.group(0).strip()[:60]
    for kind, patterns in _STRONG_RE.items():
        if hit := _first(patterns, plain_text):
            return f"{kind}: {hit.strip()}"[:60]
    weak = [(kind, hit) for kind, patterns in _WEAK_RE.items() if (hit := _first(patterns, plain_text))]
    if len(weak) >= 2:
        return "; ".join(f"{k}: {h.strip()}" for k, h in weak)[:60]
    return None
