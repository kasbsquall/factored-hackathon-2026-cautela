/**
 * Synthetic customers for mock mode. Every name, document, card number, merchant and id here is invented.
 *
 * Scores and reasons follow agent/tools/ranking.py (RuleBasedRanker: amount 0.5, date 0.3, merchant 0.2,
 * renormalized over the hints given; ambiguous if top < 0.60 or top minus second < 0.15). Deadlines follow
 * agent/policy/rules.yaml windows. The mock "today" is 2026-09-25.
 */
import type {
  CandidateChargesResult,
  DisputePolicyView,
  FindCandidateChargesInput,
  TransactionView,
} from "../../types";
import type { TestIdentity } from "../../client";

export const MOCK_TODAY = "2026-09-25T14:30:00-05:00";
export const TEST_OTP = "246810";
export const AMBIGUITY_RULE = "ambiguous if no candidate, top score < 0.6, or top minus second < 0.15";
export const POLICY_VERSION = "2026-09-25.1";

export interface MockCustomer {
  identity: TestIdentity;
  hints: FindCandidateChargesInput;
  transactions: TransactionView[];
  candidates: Omit<CandidateChargesResult, "candidates"> & { ranked: { id: string; score: number; reasons: string[] }[] };
  policies: Record<string, DisputePolicyView>;
  outcome: "open" | "pending_human_review" | "tool_unavailable";
  sessionSeconds: number;
}

const tx = (t: Partial<TransactionView> & Pick<TransactionView, "transaction_id">): TransactionView => ({
  transaction_date: null,
  amount: null,
  currency: null,
  merchant_name: null,
  channel: null,
  transaction_type: "Purchase",
  transaction_status: "Approved",
  product_id: null,
  transaction_country: null,
  transaction_city: null,
  ...t,
});

const daysSince = (t: TransactionView): number =>
  Math.round((Date.parse("2026-09-25") - Date.parse((t.transaction_date ?? "2026-09-25").slice(0, 10))) / 86_400_000);

const confirmRule = {
  rule_id: "SYN-CONFIRM-001",
  source: "synthetic_policy",
  verification: null,
  message: "confirmation required for open_dispute_case",
};

/* ---------------- 1. Colombia, ambiguous match, dispute opened and verified ---------------- */
const valentinaTx = [
  tx({ transaction_id: "TX00004169", transaction_date: "2026-09-20T18:05:00", amount: 49990, currency: "COP",
    merchant_name: "SUPERMERCADO EL PORTAL SYN", channel: "POS", product_id: "PRD-SYN-CO-01",
    transaction_country: "Colombia", transaction_city: "Bogotá" }),
  tx({ transaction_id: "TX00004182", transaction_date: "2026-09-22T19:42:00", amount: 48900, currency: "COP",
    merchant_name: "MERCANUBE*APP SYN", channel: "App", product_id: "PRD-SYN-CO-01",
    transaction_country: "Colombia", transaction_city: "Bogotá" }),
  tx({ transaction_id: "TX00004177", transaction_date: "2026-09-21T12:10:00", amount: 52300, currency: "COP",
    merchant_name: "TIENDA ANDINA WEB SYN", channel: "Web", product_id: "PRD-SYN-CO-01",
    transaction_country: "Colombia", transaction_city: "Medellín" }),
];

const valentina: MockCustomer = {
  identity: { document: "1020000001", person: "Valentina R. · Colombia", scenario: "ambiguous",
    label: "Ambiguous: three similar charges; asks which one, then opens a verified case",
    messages: { es: ["Me cobraron algo que no reconozco, como 50 mil pesos, el domingo o el lunes."], pt: ["Me cobraram algo que não reconheço, uns 50 mil pesos, no domingo ou na segunda."] } },
  hints: { amount: 50000, currency: "COP", date_hint: "2026-09-21", date_tolerance_days: 3 },
  transactions: valentinaTx,
  candidates: {
    is_ambiguous: true, best_transaction_id: null, ranker: "rule_based_v1", ambiguity_rule: AMBIGUITY_RULE,
    searched: 23,
    ranked: [
      { id: "TX00004169", score: 0.8925, reasons: ["amount=1.00", "date=0.72"] },
      { id: "TX00004182", score: 0.753, reasons: ["amount=0.78", "date=0.72"] },
      { id: "TX00004177", score: 0.725, reasons: ["amount=0.56", "date=1.00"] },
    ],
  },
  policies: Object.fromEntries(valentinaTx.map((t) => {
    const app = t.channel === "App" || t.channel === "Web";
    const windowRule = app ? "CO-WINDOW-001" : "CO-WINDOW-002";
    const deadline = app ? (t.transaction_id === "TX00004182" ? "2026-09-29" : "2026-09-28") : "2026-12-19";
    return [t.transaction_id, {
      transaction_id: t.transaction_id,
      decision: {
        policy_version: POLICY_VERSION,
        allowed_actions: ["get_case_status", "get_dispute_policy", "get_transaction", "open_dispute_case"],
        required_confirmations: ["open_dispute_case"],
        must_escalate: false,
        escalation_reasons: [],
        rules_fired: [
          { rule_id: windowRule, source: app ? "legal" : "synthetic_policy",
            verification: app ? "team_research" : "pending_verification",
            message: app ? `claim window 5 business days, deadline ${deadline}: inside`
              : `claim window 90 natural days, deadline ${deadline}: inside` },
          confirmRule,
        ],
        facts: { window_rule: windowRule, window_deadline: deadline, days_since_transaction: daysSince(t),
          amount_usd: Math.round(((t.amount ?? 0) / 4010) * 100) / 100,
          bank_response_deadline: app ? "2026-10-16" : undefined },
      },
    }];
  })),
  outcome: "open",
  sessionSeconds: 15 * 60,
};

/* ---------------- 2. Mexico, clear match, above the review threshold ---------------- */
const rafaelTx = [
  tx({ transaction_id: "TX00007730", transaction_date: "2026-09-18T22:14:00", amount: 9840, currency: "MXN",
    merchant_name: "VIAJES PACIFICO ONLINE SYN", channel: "Web", product_id: "PRD-SYN-MX-07",
    transaction_country: "Mexico", transaction_city: "Guadalajara" }),
  tx({ transaction_id: "TX00007712", transaction_date: "2026-09-15T09:31:00", amount: 1250, currency: "MXN",
    merchant_name: "FARMACIA CENTRO SYN", channel: "POS", product_id: "PRD-SYN-MX-07",
    transaction_country: "Mexico", transaction_city: "Guadalajara" }),
];

const rafael: MockCustomer = {
  identity: { document: "1020000002", person: "Rafael M. · México", scenario: "human",
    label: "Human review: a charge at or above USD 450 (SYN-AMOUNT-001)",
    messages: { es: ["Tengo un cargo de casi 10 mil pesos de una agencia de viajes que no hice."],
      pt: ["Tenho uma cobrança de quase 10 mil pesos de uma agência de viagens que eu não fiz."] } },
  hints: { amount: 10000, currency: "MXN", merchant_hint: "viajes", date_tolerance_days: 3 },
  transactions: rafaelTx,
  candidates: {
    is_ambiguous: false, best_transaction_id: "TX00007730", ranker: "rule_based_v1", ambiguity_rule: AMBIGUITY_RULE,
    searched: 17,
    ranked: [
      { id: "TX00007730", score: 0.8836, reasons: ["amount=0.84", "merchant=1.00"] },
      { id: "TX00007712", score: 0.0857, reasons: ["amount=0.00", "merchant=0.30"] },
    ],
  },
  policies: Object.fromEntries(rafaelTx.map((t) => [t.transaction_id, {
    transaction_id: t.transaction_id,
    decision: {
      policy_version: POLICY_VERSION,
      allowed_actions: ["get_case_status", "get_dispute_policy", "get_transaction", "open_dispute_case"],
      required_confirmations: ["open_dispute_case"],
      must_escalate: t.transaction_id === "TX00007730",
      escalation_reasons: t.transaction_id === "TX00007730" ? ["amount_above_threshold"] : [],
      rules_fired: [
        { rule_id: "MX-WINDOW-001", source: "legal", verification: "team_research",
          message: "claim window 90 natural days, deadline 2026-12-17: inside" },
        ...(t.transaction_id === "TX00007730"
          ? [{ rule_id: "SYN-AMOUNT-001", source: "synthetic_policy", verification: null,
            message: "USD 512.40 at or above review threshold" }]
          : []),
        confirmRule,
      ],
      facts: { window_rule: "MX-WINDOW-001", window_deadline: "2026-12-17", days_since_transaction: 7,
        amount_usd: t.transaction_id === "TX00007730" ? 512.4 : 65.1, bank_response_deadline: "2026-11-09" },
    },
  }])),
  outcome: "pending_human_review",
  sessionSeconds: 15 * 60,
};

/* ---------------- 3. Argentina, clear match, the case store fails ---------------- */
const luciaTx = [
  tx({ transaction_id: "TX00009911", transaction_date: "2026-09-19T21:03:00", amount: 84500, currency: "ARS",
    merchant_name: "PLATAFORMA STREAM SYN", channel: "App", product_id: "PRD-SYN-AR-03",
    transaction_country: "Argentina", transaction_city: "Rosario" }),
  tx({ transaction_id: "TX00009904", transaction_date: "2026-09-16T08:47:00", amount: 12300, currency: "ARS",
    merchant_name: "KIOSCO SYN", channel: "POS", product_id: "PRD-SYN-AR-03",
    transaction_country: "Argentina", transaction_city: "Rosario" }),
];

const lucia: MockCustomer = {
  identity: { document: "1020000003", person: "Lucía F. · Argentina", scenario: "tool_failure",
    label: "Tool failure: the case store fails after bounded retries; handoff tool_failure",
    messages: { es: ["No reconozco un cobro de unos 85 mil pesos del viernes a la noche."],
      pt: ["Não reconheço uma cobrança de uns 85 mil pesos de sexta à noite."] } },
  hints: { amount: 85000, currency: "ARS", date_hint: "2026-09-19", date_tolerance_days: 3 },
  transactions: luciaTx,
  candidates: {
    is_ambiguous: false, best_transaction_id: "TX00009911", ranker: "rule_based_v1", ambiguity_rule: AMBIGUITY_RULE,
    searched: 31,
    ranked: [
      { id: "TX00009911", score: 0.9632, reasons: ["amount=0.94", "date=1.00"] },
      { id: "TX00009904", score: 0.138, reasons: ["amount=0.00", "date=0.37"] },
    ],
  },
  policies: Object.fromEntries(luciaTx.map((t) => [t.transaction_id, {
    transaction_id: t.transaction_id,
    decision: {
      policy_version: POLICY_VERSION,
      allowed_actions: ["block_card", "get_case_status", "get_dispute_policy", "get_transaction", "open_dispute_case"],
      required_confirmations: ["open_dispute_case"],
      must_escalate: false,
      escalation_reasons: [],
      rules_fired: [
        { rule_id: "AR-WINDOW-001", source: "legal", verification: "verified_primary",
          message: "claim window 30 natural days, deadline 2026-10-19: inside" },
        confirmRule,
      ],
      facts: { window_rule: "AR-WINDOW-001", window_deadline: "2026-10-19", days_since_transaction: 6,
        amount_usd: 88.1, bank_response_deadline: "2026-10-10" },
    },
  }])),
  outcome: "tool_unavailable",
  sessionSeconds: 15 * 60,
};

/* ---------------- 4. Colombia, same data as 1, session that expires after 40 seconds ---------------- */
const tomas: MockCustomer = {
  ...valentina,
  identity: { document: "1020000004", person: "Tomás G. · Colombia", scenario: "short_session",
    label: "Short session: the session expires after 40 seconds",
    messages: { es: ["Me cobraron algo que no reconozco, como 50 mil pesos, el domingo o el lunes."], pt: ["Me cobraram algo que não reconheço, uns 50 mil pesos, no domingo ou na segunda."] } },
  sessionSeconds: 40,
};

export const MOCK_CUSTOMERS: MockCustomer[] = [valentina, rafael, lucia, tomas];

export function findCustomerByDocument(document: string): MockCustomer | undefined {
  const clean = document.replace(/\D/g, "");
  return MOCK_CUSTOMERS.find((c) => c.identity.document === clean);
}
