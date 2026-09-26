/**
 * Reply templates for mock mode, copied from agent/orchestrator/replies.py (TEMPLATES, REASONS, REVIEW,
 * CASE_NOTE) so a mock turn reads like a live one with no model configured. Keep them in sync by hand.
 */
import type { Language, TransferReasonCode } from "../types";

type Fields = Record<string, string>;

const TEMPLATES = {
  clarify_options: {
    es: "Encontré más de un cargo que coincide. ¿Cuál no reconoces?\n{options}\nSi no es ninguno, dímelo.",
    pt: "Encontrei mais de uma cobrança parecida. Qual você não reconhece?\n{options}\nSe não for nenhuma, me avise.",
  },
  confirm_open: {
    es: "Voy a registrar una disputa por este cargo: {label}. {review}Confírmalo con el botón para continuar.",
    pt: "Vou registrar uma contestação desta cobrança: {label}. {review}Confirme no botão para continuar.",
  },
  resolved: {
    es: "Listo. Registré la disputa del cargo {label} con el número de caso {case_id}. Comprobé en el sistema que el caso quedó abierto.",
    pt: "Pronto. Registrei a contestação da cobrança {label} com o número de caso {case_id}. Conferi no sistema que o caso está aberto.",
  },
  handed_off: {
    es: "Te paso con una persona del banco: {reason}. {case}Quien te atienda recibe los datos que ya comprobé de tu caso.",
    pt: "Vou te passar para uma pessoa do banco: {reason}. {case}Quem for te atender recebe os dados que já conferi do seu caso.",
  },
  declined: { es: "Entendido, no registré nada.", pt: "Entendido, não registrei nada." },
  recognize_check: {
    es: "Antes de abrir una disputa, revisa cómo aparece este cargo en tu cuenta: {label}. ¿Lo reconoces?",
    pt: "Antes de abrir uma contestação, veja como esta cobrança aparece na sua conta: {label}. Você a reconhece?",
  },
  recognized: {
    es: "Gracias por revisarlo. No abrí ninguna disputa por el cargo {label} y no cambié nada en tu cuenta.",
    pt: "Obrigado por conferir. Não abri contestação para a cobrança {label} e não mudei nada na sua conta.",
  },
  pending_recognition: {
    es: "Antes de seguir, dime con los botones si reconoces el cargo {label}.",
    pt: "Antes de continuar, me diga nos botões se você reconhece a cobrança {label}.",
  },
  pending_confirmation: {
    es: "Tienes una confirmación pendiente para {label}. Usa los botones para confirmar o cancelar.",
    pt: "Há uma confirmação pendente para {label}. Use os botões para confirmar ou cancelar.",
  },
  closed: {
    es: "Esta conversación ya terminó. Si necesitas algo más, abre una nueva.",
    pt: "Esta conversa já terminou. Se precisar de mais alguma coisa, abra uma nova.",
  },
} satisfies Record<string, Record<Language, string>>;

const REASONS: Record<Language, Record<TransferReasonCode, string>> = {
  es: {
    amount_above_threshold: "por el monto, la revisión la hace una persona del banco",
    suspected_fraud: "hay señales que debe revisar un especialista en fraude",
    policy_requires_review: "según la política aplicable, este caso lo debe revisar una persona",
    low_confidence: "no pude identificar el cargo con seguridad",
    tool_failure: "tuvimos un problema técnico y no pude comprobar el registro",
    security_event: "por seguridad, esta conversación pasa a un agente",
    out_of_scope: "esa solicitud no la puedo atender por este canal",
    customer_requested_human: "pediste hablar con una persona",
  },
  pt: {
    amount_above_threshold: "pelo valor, a análise é feita por uma pessoa do banco",
    suspected_fraud: "há sinais que precisam ser analisados por um especialista em fraude",
    policy_requires_review: "pela política aplicável, este caso precisa ser analisado por uma pessoa",
    low_confidence: "não consegui identificar a cobrança com segurança",
    tool_failure: "tivemos um problema técnico e não consegui confirmar o registro",
    security_event: "por segurança, esta conversa passa para um atendente",
    out_of_scope: "não consigo atender esse pedido por este canal",
    customer_requested_human: "você pediu para falar com uma pessoa",
  },
};

const REVIEW: Record<Language, string> = { es: "Aviso: {reason}. ", pt: "Aviso: {reason}. " };
const CASE_NOTE: Record<Language, string> = {
  es: "Tu caso quedó registrado con el número {case_id}. ",
  pt: "Seu caso foi registrado com o número {case_id}. ",
};

function fill(template: string, fields: Fields): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => fields[key] ?? "");
}

export function reply(kind: keyof typeof TEMPLATES, lang: Language, fields: Fields = {}): string {
  return fill(TEMPLATES[kind][lang], fields);
}

export function reasonText(code: TransferReasonCode, lang: Language): string {
  return REASONS[lang][code];
}

export function reviewNote(code: TransferReasonCode, lang: Language): string {
  return fill(REVIEW[lang], { reason: reasonText(code, lang) });
}

export function caseNote(caseId: string, lang: Language): string {
  return fill(CASE_NOTE[lang], { case_id: caseId });
}
