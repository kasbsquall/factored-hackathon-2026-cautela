import { renderTemplate, TEMPLATES, translateTemplateReply, PHRASES } from "@/lib/i18n/reply-templates";
import type { Language, TurnResponse } from "@/lib/api/types";
import turns from "../fixtures/live-turns.json";

const LABEL = "30 may 2026, Marketplace Uno, MXN 5,335.32";
const FIELDS: Record<string, Record<Language, Record<string, string>>> = {
  ask_details: { es: { missing: "el monto, la fecha o el comercio" }, pt: { missing: "o valor, a data ou a loja" } },
  clarify_options: { es: { options: "1) a\n2) b" }, pt: { options: "1) a\n2) b" } },
  card_options: { es: { options: "1) **** 4821" }, pt: { options: "1) **** 4821" } },
  handed_off: {
    es: { reason: PHRASES.REASONS.amount_above_threshold.es, case: "Tu caso quedó registrado con el número CASE-1. " },
    pt: { reason: PHRASES.REASONS.out_of_scope.pt, case: "" },
  },
  not_disputable: { es: { label: LABEL, status: PHRASES.STATUS.Declined!.es }, pt: { label: LABEL, status: PHRASES.STATUS.Reversed!.pt } },
  auth_required: { es: { why: "venció" }, pt: { why: "não é válida" } },
  confirm_open: { es: { label: LABEL, review: "Aviso: pediste hablar con una persona. " }, pt: { label: LABEL, review: "" } },
};

describe("translateTemplateReply", () => {
  for (const kind of Object.keys(TEMPLATES)) {
    for (const lang of ["es", "pt"] as Language[]) {
      it(`matches ${kind} in ${lang} and produces the English template`, () => {
        const fields = FIELDS[kind]?.[lang] ?? { label: LABEL, case_id: "CASE-D02905339816" };
        const text = renderTemplate(kind, lang, fields);
        const english = translateTemplateReply(text, kind, lang);
        expect(english).not.toBeNull();
        expect(english).not.toMatch(/\{\w+\}/);
        if (fields.label && TEMPLATES[kind]!.es.includes("{label}")) expect(english).toContain(fields.label);
        if (fields.case_id && TEMPLATES[kind]!.es.includes("{case_id}")) expect(english).toContain(fields.case_id);
      });
    }
  }

  it("translates the fixed phrases inside a template", () => {
    const text = renderTemplate("handed_off", "es", FIELDS.handed_off!.es);
    expect(translateTemplateReply(text, "handed_off", "es")).toBe(
      "I am passing you to a person at the bank: because of the amount, a person at the bank does the review. " +
        "Your case was filed with number CASE-1. The person who helps you gets the details of your case that I already checked.",
    );
    expect(translateTemplateReply(renderTemplate("ask_details", "pt", FIELDS.ask_details!.pt), "ask_details", "pt")).toBe(
      "To find the charge I need one more detail: can you tell me the amount, the date or the merchant?",
    );
    expect(translateTemplateReply(renderTemplate("confirm_open", "es", FIELDS.confirm_open!.es), "confirm_open", "es")).toContain(
      "Note: you asked to talk to a person. Confirm",
    );
  });

  it("matches a card question after the UI dropped its option lines", () => {
    expect(translateTemplateReply("Qual cartão você quer bloquear?", "card_options", "pt")).toBe("Which card do you want to block?");
  });

  it("matches a clarify reply after the UI dropped the numbered option lines", () => {
    const shown = "Encontré más de un cargo que coincide. ¿Cuál no reconoces?\nSi no es ninguno, dímelo.";
    expect(translateTemplateReply(shown, "clarify_options", "es")).toBe(
      "I found more than one charge that fits. Which one do you not recognize?\nIf it is none of them, tell me.",
    );
  });

  it("returns null for text that is not that template, so the caller falls back to machine translation", () => {
    expect(translateTemplateReply("Hola, ¿cómo estás?", "resolved", "es")).toBeNull();
    expect(translateTemplateReply(renderTemplate("resolved", "es", { label: LABEL, case_id: "X" }), "resolved", "pt")).toBeNull();
    expect(translateTemplateReply("anything", "not_a_template", "es")).toBeNull();
  });

  it("translates every template reply the live service returned in the captured runs", () => {
    const all = Object.values(turns as unknown as Record<string, TurnResponse[]>).flat();
    for (const turn of all) {
      const kind = turn.trail.find((s) => s.step === "reply")?.detail.kind as string;
      expect(turn.reply_source).toBe("template");
      const shown = turn.options.length ? turn.reply.split("\n").filter((l) => !/^\d+\)\s/.test(l)).join("\n") : turn.reply;
      expect(translateTemplateReply(shown, kind, turn.language), `${kind}: ${turn.reply}`).not.toBeNull();
    }
  });
});
