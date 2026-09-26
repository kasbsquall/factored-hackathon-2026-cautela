/**
 * Deterministic English versions of the service's reply templates, for reviewers.
 *
 * Source: agent/orchestrator/replies.py (TEMPLATES, REASONS, STATUS, CUES, WHY, REVIEW, CASE_NOTE) and
 * agent/orchestrator/core.py `_missing_text`. Keep in sync by hand, like src/lib/api/mock/replies.ts.
 *
 * A template reply names its template in the turn trail (step "reply", outcome "template", detail.kind). The reply
 * text is matched against that template in the conversation language; the fields it captured (charge label, case
 * id, reason) are carried into the English template unchanged, except the phrases that come from the fixed lists
 * above, which have English versions here. When the text does not match (the trail names no template, or the
 * service changed a template), the caller falls back to machine translation.
 */
import type { Language, TransferReasonCode } from "@/lib/api/types";

type Tri = Record<Language | "en", string>;

export const TEMPLATES: Record<string, Tri> = {
  ask_details: {
    es: "Para ubicar el cargo necesito un dato más: ¿me dices {missing}?",
    pt: "Para localizar a cobrança preciso de mais um dado: pode me dizer {missing}?",
    en: "To find the charge I need one more detail: can you tell me {missing}?",
  },
  clarify_options: {
    es: "Encontré más de un cargo que coincide. ¿Cuál no reconoces?\n{options}\nSi no es ninguno, dímelo.",
    pt: "Encontrei mais de uma cobrança parecida. Qual você não reconhece?\n{options}\nSe não for nenhuma, me avise.",
    en: "I found more than one charge that fits. Which one do you not recognize?\n{options}\nIf it is none of them, tell me.",
  },
  confirm_open: {
    es: "Voy a registrar una disputa por este cargo: {label}. {review}Confírmalo con el botón para continuar.",
    pt: "Vou registrar uma contestação desta cobrança: {label}. {review}Confirme no botão para continuar.",
    en: "I am going to file a dispute for this charge: {label}. {review}Confirm with the button to continue.",
  },
  confirm_card: {
    es: "Voy a bloquear la tarjeta {label}. Confírmalo con el botón para continuar.",
    pt: "Vou bloquear o cartão {label}. Confirme no botão para continuar.",
    en: "I am going to block card {label}. Confirm with the button to continue.",
  },
  resolved: {
    es: "Listo. Registré la disputa del cargo {label} con el número de caso {case_id}. Comprobé en el sistema que el caso quedó abierto.",
    pt: "Pronto. Registrei a contestação da cobrança {label} com o número de caso {case_id}. Conferi no sistema que o caso está aberto.",
    en: "Done. I filed the dispute for charge {label} with case number {case_id}. I checked in the system that the case is open.",
  },
  card_blocked: {
    es: "Listo. La tarjeta {label} quedó bloqueada; lo comprobé en el sistema.",
    pt: "Pronto. O cartão {label} está bloqueado; conferi no sistema.",
    en: "Done. Card {label} is blocked; I checked it in the system.",
  },
  handed_off: {
    es: "Te paso con una persona del banco: {reason}. {case}Quien te atienda recibe los datos que ya comprobé de tu caso.",
    pt: "Vou te passar para uma pessoa do banco: {reason}. {case}Quem for te atender recebe os dados que já conferi do seu caso.",
    en: "I am passing you to a person at the bank: {reason}. {case}Whoever helps you receives the details of your case I already checked.",
  },
  not_disputable: {
    es: "El cargo {label} {status}. Por eso no hay una disputa que abrir.",
    pt: "A cobrança {label} {status}. Por isso não há contestação para abrir.",
    en: "The charge {label} {status}. So there is no dispute to open.",
  },
  declined: { es: "Entendido, no registré nada.", pt: "Entendido, não registrei nada.", en: "Understood, I did not file anything." },
  recognize_check: {
    es: "Antes de abrir una disputa, revisa cómo aparece este cargo en tu cuenta: {label}. ¿Lo reconoces?",
    pt: "Antes de abrir uma contestação, veja como esta cobrança aparece na sua conta: {label}. Você a reconhece?",
    en: "Before opening a dispute, check how this charge appears on your account: {label}. Do you recognize it?",
  },
  recognized: {
    es: "Gracias por revisarlo. No abrí ninguna disputa por el cargo {label} y no cambié nada en tu cuenta.",
    pt: "Obrigado por conferir. Não abri contestação para a cobrança {label} e não mudei nada na sua conta.",
    en: "Thanks for checking. I did not open a dispute for charge {label} and I did not change anything on your account.",
  },
  pending_recognition: {
    es: "Antes de seguir, dime con los botones si reconoces el cargo {label}.",
    pt: "Antes de continuar, me diga nos botões se você reconhece a cobrança {label}.",
    en: "Before going on, tell me with the buttons whether you recognize charge {label}.",
  },
  ref_not_found: {
    es: "No encontré esa referencia entre tus movimientos. ¿Puedes revisarla o decirme el monto, la fecha o el comercio del cargo?",
    pt: "Não encontrei essa referência nos seus movimentos. Pode conferir ou me dizer o valor, a data ou a loja da cobrança?",
    en: "I did not find that reference among your transactions. Can you check it, or tell me the amount, the date or the merchant of the charge?",
  },
  auth_required: {
    es: "Tu sesión {why}. Vuelve a identificarte con tu documento y el código que te enviamos; lo que ya me contaste queda guardado.",
    pt: "Sua sessão {why}. Identifique-se de novo com seu documento e o código que enviamos; o que você já me contou continua salvo.",
    en: "Your session {why}. Identify yourself again with your document and the code we send you; what you already told me is kept.",
  },
  pending_confirmation: {
    es: "Tienes una confirmación pendiente para {label}. Usa los botones para confirmar o cancelar.",
    pt: "Há uma confirmação pendente para {label}. Use os botões para confirmar ou cancelar.",
    en: "You have a pending confirmation for {label}. Use the buttons to confirm or cancel.",
  },
  closed: {
    es: "Esta conversación ya terminó. Si necesitas algo más, abre una nueva.",
    pt: "Esta conversa já terminou. Se precisar de mais alguma coisa, abra uma nova.",
    en: "This conversation has ended. If you need anything else, open a new one.",
  },
  no_active_card: {
    es: "No encontré una tarjeta activa para bloquear.",
    pt: "Não encontrei um cartão ativo para bloquear.",
    en: "I did not find an active card to block.",
  },
  card_options: { es: "¿Qué tarjeta quieres bloquear?\n{options}", pt: "Qual cartão você quer bloquear?\n{options}", en: "Which card do you want to block?\n{options}" },
};

const REASONS: Record<TransferReasonCode, Tri> = {
  amount_above_threshold: {
    es: "por el monto, la revisión la hace una persona del banco",
    pt: "pelo valor, a análise é feita por uma pessoa do banco",
    en: "because of the amount, a person at the bank does the review",
  },
  suspected_fraud: {
    es: "hay señales que debe revisar un especialista en fraude",
    pt: "há sinais que precisam ser analisados por um especialista em fraude",
    en: "there are signals a fraud specialist must review",
  },
  policy_requires_review: {
    es: "según la política aplicable, este caso lo debe revisar una persona",
    pt: "pela política aplicável, este caso precisa ser analisado por uma pessoa",
    en: "under the applicable policy, a person must review this case",
  },
  low_confidence: {
    es: "no pude identificar el cargo con seguridad",
    pt: "não consegui identificar a cobrança com segurança",
    en: "I could not identify the charge with certainty",
  },
  tool_failure: {
    es: "tuvimos un problema técnico y no pude comprobar el registro",
    pt: "tivemos um problema técnico e não consegui confirmar o registro",
    en: "we had a technical problem and I could not check the record",
  },
  security_event: {
    es: "por seguridad, esta conversación pasa a un agente",
    pt: "por segurança, esta conversa passa para um atendente",
    en: "for security, this conversation goes to an agent",
  },
  out_of_scope: {
    es: "esa solicitud no la puedo atender por este canal",
    pt: "não consigo atender esse pedido por este canal",
    en: "I cannot handle that request on this channel",
  },
  customer_requested_human: {
    es: "pediste hablar con una persona",
    pt: "você pediu para falar com uma pessoa",
    en: "you asked to talk to a person",
  },
};

const CUES: Record<string, Tri> = {
  amount: { es: "el monto", pt: "o valor", en: "the amount" },
  date: { es: "la fecha", pt: "a data", en: "the date" },
  merchant: { es: "el comercio", pt: "a loja", en: "the merchant" },
};
const STATUS: Record<string, Tri> = {
  Declined: { es: "fue rechazado, así que no se movió dinero", pt: "foi recusada, então nenhum dinheiro saiu", en: "was declined, so no money moved" },
  Reversed: { es: "ya fue revertido", pt: "já foi estornada", en: "was already reversed" },
};
const WHY: Record<string, Tri> = {
  session_expired: { es: "venció", pt: "expirou", en: "expired" },
  default: { es: "no es válida", pt: "não é válida", en: "is not valid" },
};
const REVIEW: Tri = { es: "Aviso: {reason}. ", pt: "Aviso: {reason}. ", en: "Note: {reason}. " };
const CASE_NOTE: Tri = {
  es: "Tu caso quedó registrado con el número {case_id}. ",
  pt: "Seu caso foi registrado com o número {case_id}. ",
  en: "Your case was filed with number {case_id}. ",
};

/** Fields whose value is one phrase of a fixed list: matched against the list, translated through it. */
type Phrasebook = Record<string, Tri>;
const PHRASEBOOK_FIELDS: Record<string, Phrasebook> = { reason: REASONS, status: STATUS, why: WHY };

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function alternatives(book: Phrasebook, lang: Language): string {
  return Object.values(book).map((p) => escape(p[lang])).join("|");
}

/** "el monto, la fecha o el comercio" in any order and length, from the CUES list. */
function missingPattern(lang: Language): string {
  const one = alternatives(CUES, lang);
  const last = lang === "es" ? " o " : " ou ";
  return `(?:${one})(?:(?:, (?:${one}))*${escape(last)}(?:${one}))?`;
}

/** Regex for one template in one language, with a named group per field. */
function patternFor(template: string, lang: Language): RegExp {
  const parts = template.split(/(\{\w+\})/);
  const body = parts.map((part) => {
    const field = /^\{(\w+)\}$/.exec(part)?.[1];
    if (!field) return escape(part);
    if (field === "review") {
      const inner = escape(REVIEW[lang]).replace(escape("{reason}"), `(?<reason>${alternatives(REASONS, lang)})`);
      return `(?<review>${inner})?`;
    }
    if (field === "case") {
      const inner = escape(CASE_NOTE[lang]).replace(escape("{case_id}"), "(?<case_id>\\S+)");
      return `(?<case>${inner})?`;
    }
    if (field === "missing") return `(?<missing>${missingPattern(lang)})`;
    if (field === "options") return "(?<options>[\\s\\S]*?)";
    const book = PHRASEBOOK_FIELDS[field];
    if (book) return `(?<${field}>${alternatives(book, lang)})`;
    return `(?<${field}>.+?)`;
  });
  return new RegExp(`^${body.join("")}$`);
}

function translatePhrase(book: Phrasebook, value: string, lang: Language): string | null {
  const hit = Object.values(book).find((p) => p[lang] === value);
  return hit ? hit.en : null;
}

function translateMissing(value: string, lang: Language): string {
  const names = Object.values(CUES).filter((c) => value.includes(c[lang])).map((c) => c.en);
  return names.length <= 1 ? names[0] ?? value : `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

/** The numbered option lines the UI shows as buttons are dropped before the text reaches here; tolerate both. */
function normalize(text: string): string {
  return text.replace(/\r\n/g, "\n").trim();
}

/**
 * English version of a template reply, or null when the text does not match that template in that language.
 * Captured fields that are data (labels, case ids) are carried over verbatim.
 */
export function translateTemplateReply(text: string, kind: string, lang: Language): string | null {
  const template = TEMPLATES[kind];
  if (!template) return null;
  const source = normalize(text);
  // The UI removes the numbered options from clarify replies; match against the template without them too.
  const withoutOptions = (t: string) => t.replace(/\n\{options\}(\n|$)/, "$1");
  const variants = template[lang].includes("{options}") ? [template[lang], withoutOptions(template[lang])] : [template[lang]];
  for (const variant of variants) {
    const match = patternFor(variant, lang).exec(source);
    if (!match) continue;
    const groups = match.groups ?? {};
    const fields: Record<string, string> = {};
    for (const [name, value] of Object.entries(groups)) {
      if (value === undefined) continue;
      const book = PHRASEBOOK_FIELDS[name];
      fields[name] = book ? translatePhrase(book, value, lang) ?? value : value;
    }
    if (groups.missing) fields.missing = translateMissing(groups.missing, lang);
    fields.review = groups.review && groups.reason ? REVIEW.en.replace("{reason}", fields.reason ?? groups.reason) : "";
    fields.case = groups.case && groups.case_id ? CASE_NOTE.en.replace("{case_id}", groups.case_id) : "";
    const english = variant === template[lang] ? template.en : withoutOptions(template.en);
    return english.replace(/\{(\w+)\}/g, (_, key: string) => fields[key] ?? "");
  }
  return null;
}

/** Test helper and mock support: render a template in any language with the given fields. */
export function renderTemplate(kind: string, lang: Language | "en", fields: Record<string, string> = {}): string {
  const template = TEMPLATES[kind];
  if (!template) throw new Error(`unknown template ${kind}`);
  return template[lang].replace(/\{(\w+)\}/g, (_, key: string) => fields[key] ?? "");
}

export const PHRASES = { REASONS, STATUS, CUES, WHY, REVIEW, CASE_NOTE };
