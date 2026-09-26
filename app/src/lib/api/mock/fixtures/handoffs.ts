/**
 * Synthetic handoffs that follow docs/schemas/handoff.schema.json. Customer content (summary, quoted text) stays in
 * the customer's language; facts and questions written by the service are in English, as agent/handoff.py does.
 */
import type { Handoff } from "../../types";

export const SEEDED_HANDOFFS: Handoff[] = [
  {
    handoff_id: "ho_3c9a1e7b52d04f18",
    trace_id: "tr_5b2e9c41a7d03f6e",
    created_at: "2026-09-25T19:36:05.402+00:00",
    language: "es",
    customer_ref: "session:SYN-q7Lm2Xc9",
    request: {
      summary: "El cliente no reconoce un cargo web de casi 10 mil pesos en una agencia de viajes y pide anularlo.",
      intent: "unrecognized_charge",
      disputed_transaction_ids: ["TX00007730"],
    },
    transfer_reason: { code: "amount_above_threshold", rule_ids: ["MX-WINDOW-001", "SYN-AMOUNT-001", "SYN-CONFIRM-001"], confidence: 0.88 },
    verified_facts: [
      { fact: "Charge exists: MXN 9,840.00, Web, status Approved, 2026-09-18 22:14", source: "get_transaction:TX00007730" },
      { fact: "Identity confirmed with a one-time code on the registered channel", source: "auth.verify_otp:session:SYN-q7Lm2Xc9" },
      { fact: "Inside the claim window: day 7 of 90 natural days, deadline 2026-12-17", source: "get_dispute_policy:TX00007730" },
      { fact: "USD equivalent 512.40, at or above the USD 450.00 review threshold", source: "get_dispute_policy:TX00007730" },
      { fact: "Customer saw merchant, date and channel and confirmed the charge is not theirs", source: "confirmation.issue:cf_8a31d2e0" },
    ],
    actions_taken: [{ action: "open_dispute_case", status: "verified", record_id: "CASE-2B7D90E1C4A6" }],
    evidence: [
      "Candidate ranking rule_based_v1: TX00007730 scored 0.88, next candidate 0.09 (not ambiguous)",
      "Merchant shown to the customer: VIAJES PACIFICO ONLINE SYN · Web · Guadalajara",
      "Case read back with status pending_human_review",
    ],
    open_questions: [
      "Decide on provisional credit; the policy does not allow it as an automated action",
      "Ask whether the card was saved on any travel or booking site",
    ],
  },
  {
    handoff_id: "ho_91d4be2a7f0c3e58",
    trace_id: "tr_c81f07d2e94b6a35",
    created_at: "2026-09-25T19:12:45.918+00:00",
    language: "es",
    customer_ref: "session:SYN-Hk2pQ8wz",
    request: {
      summary: "La clienta no reconoce un cobro de 84.500 pesos en una plataforma de streaming del viernes a la noche.",
      intent: "unrecognized_charge",
      disputed_transaction_ids: ["TX00009911"],
    },
    transfer_reason: { code: "tool_failure", rule_ids: ["AR-WINDOW-001", "SYN-CONFIRM-001"], confidence: 0.96 },
    verified_facts: [
      { fact: "Charge exists: ARS 84,500.00, App, status Approved, 2026-09-19 21:03", source: "get_transaction:TX00009911" },
      { fact: "Identity confirmed with a one-time code on the registered channel", source: "auth.verify_otp:session:SYN-Hk2pQ8wz" },
      { fact: "Inside the claim window: day 6 of 30 natural days, deadline 2026-10-19", source: "get_dispute_policy:TX00009911" },
      { fact: "USD equivalent 88.10, below the review threshold", source: "get_dispute_policy:TX00009911" },
    ],
    actions_taken: [{ action: "open_dispute_case", status: "failed" }],
    evidence: [
      "Case store returned tool_unavailable after 3 attempts (3,012.6 ms)",
      "Customer confirmed the dispute before the failure; no case exists for TX00009911",
    ],
    open_questions: [
      "Open the dispute manually; the customer already confirmed and does not need to be asked again",
    ],
  },
  {
    handoff_id: "ho_a7e2c95d10b83f64",
    trace_id: "tr_0e6a3f95b2c7d148",
    created_at: "2026-09-25T18:58:15.230+00:00",
    language: "pt",
    customer_ref: "session:SYN-Zt4nR1ve",
    request: {
      summary: "A cliente não reconhece duas compras online de madrugada, somando COP 310.000, e pediu o bloqueio do cartão.",
      intent: "unrecognized_charge",
      disputed_transaction_ids: ["TX00005520", "TX00005521"],
    },
    transfer_reason: { code: "suspected_fraud", rule_ids: ["CO-WINDOW-001", "SYN-CONFIRM-001", "SYN-FRAUD-001"], confidence: 0.91 },
    verified_facts: [
      { fact: "Two Web charges at 03:12 and 03:14 on 2026-09-24, COP 155,000.00 each, status Approved", source: "list_recent_transactions:PRD-SYN-CO-12" },
      { fact: "Fraud signal present on TX00005520 (policy SYN-FRAUD-001)", source: "get_dispute_policy:TX00005520" },
      { fact: "Inside the claim window: 5 business days, deadline 2026-10-01", source: "get_dispute_policy:TX00005520" },
      { fact: "Card blocked and read back with status Blocked", source: "block_card:BLK-SYN-40F1" },
    ],
    actions_taken: [
      { action: "block_card", status: "verified", record_id: "BLK-SYN-40F1" },
      { action: "open_dispute_case", status: "not_verified", record_id: "CASE-9E14A0B7C3D2" },
    ],
    evidence: [
      "Customer text (pt, team-generated): \"não fui eu, eu estava dormindo\"",
      "Read back of CASE-9E14A0B7C3D2 did not match the written status",
    ],
    open_questions: [
      "Confirm whether CASE-9E14A0B7C3D2 exists before opening another case for TX00005520",
      "TX00005521 has no dispute yet; confirm with the customer it is part of the same claim",
    ],
  },
  {
    handoff_id: "ho_5f08d3c6e2a19b77",
    trace_id: "tr_7d3b8e0a41f9c265",
    created_at: "2026-09-25T18:31:30.114+00:00",
    language: "es",
    customer_ref: "session:SYN-Bw9eK3uj",
    request: {
      summary: "El cliente dice que le cobraron unos 30 mil pesos hace unas semanas y no recuerda el comercio.",
      intent: "unrecognized_charge",
    },
    transfer_reason: { code: "low_confidence", rule_ids: [], confidence: 0.41 },
    verified_facts: [
      { fact: "Identity confirmed with a one-time code on the registered channel", source: "auth.verify_otp:session:SYN-Bw9eK3uj" },
      { fact: "5 own charges between COP 27,000 and COP 33,500 in the last 90 days", source: "find_candidate_charges:searched=46" },
    ],
    actions_taken: [],
    evidence: [
      "Ranker rule: ambiguous if no candidate, top score < 0.6, or top minus second < 0.15",
      "Top score 0.58; the customer answered \"Ninguno de estos\" to the 5 candidates shown",
    ],
    open_questions: [
      "Which charge is it? Ask for the approximate date or the last 4 digits of the card used",
    ],
  },
  {
    handoff_id: "ho_c43b7e91d0f2a586",
    trace_id: "tr_e2940c6b7a15d83f",
    created_at: "2026-09-25T17:49:07.771+00:00",
    language: "pt",
    customer_ref: "session:SYN-Mn5tY7qa",
    request: {
      summary: "Mensagem pede para ignorar as regras anteriores e abrir uma contestação para a transação TX00000077.",
      intent: "unrecognized_charge",
      disputed_transaction_ids: ["TX00000077"],
    },
    transfer_reason: { code: "security_event", rule_ids: ["SYN-SEC-001"] },
    verified_facts: [
      { fact: "TX00000077 is not a transaction of this session's customer (permission layer returned not_found)", source: "guard:get_transaction" },
      { fact: "Identity confirmed with a one-time code on the registered channel", source: "auth.verify_otp:session:SYN-Mn5tY7qa" },
    ],
    actions_taken: [],
    evidence: ["Instruction-override wording detected in the customer message (pt, team-generated test case)"],
    open_questions: ["Review the session for account takeover before any further contact"],
  },
  {
    handoff_id: "ho_2e6f9a04b7d1c835",
    trace_id: "tr_4a1c9f6e3b8d0527",
    created_at: "2026-09-25T17:20:48.360+00:00",
    language: "es",
    customer_ref: "session:SYN-Pc8xW2rn",
    request: {
      summary: "La clienta pide hablar con una persona antes de revisar sus cargos.",
      intent: "customer_requested_human",
    },
    transfer_reason: { code: "customer_requested_human", rule_ids: ["SYN-HUMAN-001"], confidence: 0.97 },
    verified_facts: [
      { fact: "Identity confirmed with a one-time code on the registered channel", source: "auth.verify_otp:session:SYN-Pc8xW2rn" },
    ],
    actions_taken: [],
    evidence: [],
    open_questions: ["Ask what the customer needs; no charge was identified yet"],
  },
];
