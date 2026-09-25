"""Clean row builders for contact-center, digital and campaign fact tables of the synthetic fixture."""

from __future__ import annotations

import json
from datetime import timedelta

from data_engineering.fixtures import vocab
from data_engineering.fixtures.dimensions import SIZES, Ctx
from data_engineering.fixtures.facts_money import event_and_process

_INTERACTION = [("Inbound Call", "Phone"), ("Inbound Call", "Phone"), ("Outbound Call", "Phone"),
                ("Chat", "Web Chat"), ("Chat", "WhatsApp"), ("Email", "Email"), ("Video", "App")]
_SENTIMENT = [("Positive", 0.3, 1.0), ("Neutral", -0.2, 0.3), ("Negative", -0.7, -0.2), ("Very Negative", -1.0, -0.7)]


def build_interactions(ctx: Ctx, customers: list[dict], agents: list[dict]) -> list[dict]:
    with_transcript = set(ctx.rng.sample(range(SIZES["call_center_interactions"]), SIZES["call_transcripts"]))
    rows = []
    for i in range(SIZES["call_center_interactions"]):
        customer = ctx.rng.choice(customers)
        agent = ctx.rng.choice(agents)
        kind, channel = ctx.rng.choice(_INTERACTION)
        reason, category = ctx.rng.choice(vocab.CONTACT_REASONS)
        sentiment, low, high = ctx.rng.choice(_SENTIMENT)
        event, process = event_and_process(ctx)
        rows.append({
            "interaction_id": f"IN{i + 1:08d}", "interaction_date": event, "process_date": process,
            "customer_id": customer["customer_id"], "agent_id": agent["agent_id"], "interaction_type": kind,
            "channel": channel, "contact_reason": reason, "reason_category": category,
            "duration_seconds": ctx.rng.randint(60, 1800), "wait_time_seconds": ctx.rng.randint(0, 900),
            "was_resolved": ctx.rng.random() < 0.65, "requires_followup": ctx.rng.random() < 0.3,
            "detected_sentiment": sentiment, "sentiment_score": round(ctx.rng.uniform(low, high), 2),
            "customer_detected_accent": customer["detected_accent"], "agent_used_accent": agent["native_accent"],
            "was_escalated": ctx.rng.random() < 0.1, "mentioned_products": None,
            "has_transcript": i in with_transcript, "has_recording": kind in ("Inbound Call", "Outbound Call"),
        })
    return rows


def build_transcripts(ctx: Ctx, interactions: list[dict]) -> list[dict]:
    rows = []
    for it in (x for x in interactions if x["has_transcript"]):
        accent = it["customer_detected_accent"]
        opener = vocab.ACCENT_OPENERS.get(accent, "Buenas tardes,")
        customer_text = f"{opener} llamo por {it['contact_reason'].lower()}."
        agent_text = "Con gusto le ayudo. ¿Me confirma su número de documento, por favor?"
        rows.append({
            "transcript_id": f"TR{len(rows) + 1:08d}", "interaction_id": it["interaction_id"],
            "process_date": it["process_date"], "customer_id": it["customer_id"], "agent_id": it["agent_id"],
            "full_text": f"AGENTE: Gracias por comunicarse. CLIENTE: {customer_text} AGENTE: {agent_text}",
            "customer_text": customer_text, "agent_text": agent_text, "detected_language": "es",
            "detected_accent": accent, "accent_confidence": round(ctx.rng.uniform(0.55, 0.99), 2),
            "detected_keywords": it["contact_reason"].lower(),
            "mentioned_entities": json.dumps({"motivo": it["contact_reason"]}, ensure_ascii=False),
            "detected_intents": it["contact_reason"].lower().replace(" ", "_"),
            "main_topics": it["reason_category"], "transcription_model": ctx.rng.choice(["Whisper", "Google STT"]),
            "audio_quality": ctx.rng.choice(["High", "Medium", "Low"]), "duration_seconds": it["duration_seconds"],
        })
    return rows


def build_surveys(ctx: Ctx, interactions: list[dict]) -> list[dict]:
    rows = []
    for i in range(SIZES["satisfaction_surveys"]):
        it = ctx.rng.choice(interactions)
        survey_type = ctx.rng.choice(["CSAT", "CSAT", "NPS", "CES"])
        score = ctx.rng.randint(0, 10) if survey_type == "NPS" else ctx.rng.randint(1, 5)
        nps = None
        if survey_type == "NPS":
            nps = "Promoter" if score >= 9 else "Passive" if score >= 7 else "Detractor"
        event, process = event_and_process(ctx, min(it["interaction_date"].date() + timedelta(days=1), ctx.end))
        rows.append({
            "survey_id": f"SV{i + 1:08d}", "survey_date": event, "process_date": process,
            "interaction_id": it["interaction_id"], "customer_id": it["customer_id"], "agent_id": it["agent_id"],
            "survey_type": survey_type, "send_channel": ctx.rng.choice(["Email", "SMS", "IVR", "App", "Web"]),
            "main_score": score, "nps_category": nps,
            "question_1_text": vocab.SURVEY_QUESTIONS[0], "question_1_response": ctx.rng.randint(1, 5),
            "question_2_text": vocab.SURVEY_QUESTIONS[1], "question_2_response": ctx.rng.randint(1, 5),
            "question_3_text": vocab.SURVEY_QUESTIONS[2], "question_3_response": ctx.rng.randint(1, 5),
            "open_comments": ctx.rng.choice(vocab.SURVEY_COMMENTS),
            "comment_sentiment": ctx.rng.choice(["Positive", "Neutral", "Negative"]),
            "response_time_hours": round(ctx.rng.uniform(0.5, 72), 2),
            "campaign_response_rate": round(ctx.rng.uniform(1, 40), 2),
        })
    return rows


_EVENTS = [("PageView", "Navigation", 40), ("Click", "Navigation", 25), ("Login", "Authentication", 12),
           ("Logout", "Authentication", 8), ("FormSubmit", "Transaction", 7), ("Purchase", "Transaction", 4),
           ("Error", "Product", 4)]
_CHANNELS = [("Android App", "Android", True), ("iOS App", "iOS", True), ("Desktop Web", "Windows", False),
             ("Desktop Web", "MacOS", False), ("Mobile Web", "Android", True)]


def build_digital_events(ctx: Ctx, customers: list[dict], products: list[dict]) -> list[dict]:
    products_of: dict[str, list[str]] = {}
    for p in products:
        products_of.setdefault(p["customer_id"], []).append(p["product_id"])
    weights = [e[2] for e in _EVENTS]
    rows = []
    for i in range(SIZES["digital_events"]):
        customer = customers[ctx.rng.randrange(len(customers))]
        event_type, category, _ = ctx.rng.choices(_EVENTS, weights=weights)[0]
        channel, platform, mobile = _CHANNELS[ctx.rng.randrange(len(_CHANNELS))]
        event, process = event_and_process(ctx)
        city, _state = ctx.rng.choice(vocab.COUNTRIES[customer["country"]]["cities"])
        is_app = channel.endswith("App")
        rows.append({
            "event_id": f"EV{i + 1:09d}", "event_date": event, "process_date": process,
            "customer_id": customer["customer_id"], "session_id": f"S{ctx.rng.randrange(16**12):012x}",
            "event_type": event_type, "event_category": category, "channel": channel, "platform": platform,
            "browser": None if is_app else ctx.rng.choice(["Chrome", "Safari", "Edge", "Firefox"]),
            "app_version": f"5.{ctx.rng.randint(0, 9)}.{ctx.rng.randint(0, 20)}" if is_app else None,
            "page_url": f"/banca/{category.lower()}", "page_title": f"Banca en línea - {category}",
            "action": event_type.lower(), "element_id": f"btn-{ctx.rng.randint(1, 40)}",
            "product_id": ctx.rng.choice(products_of[customer["customer_id"]]) if category == "Transaction" else None,
            "event_value": ctx.money(10, 5000) if event_type == "Purchase" else None,
            "duration_seconds": ctx.rng.randint(1, 600), "ip_address": f"10.{ctx.rng.randint(0, 255)}.{ctx.rng.randint(0, 255)}.{ctx.rng.randint(1, 254)}",
            "ip_country": customer["country"], "ip_city": city, "is_mobile": mobile,
            "referrer": None, "utm_source": ctx.rng.choice([None, "email", "sms", "push"]),
            "utm_medium": None, "utm_campaign": None,
        })
    return rows


def build_campaign_sends(ctx: Ctx, customers: list[dict], campaigns: list[dict]) -> list[dict]:
    rows = []
    for i in range(SIZES["campaign_sends"]):
        campaign = ctx.rng.choice(campaigns)
        customer = ctx.rng.choice(customers)
        event, process = event_and_process(ctx)
        status = ctx.rng.choices(["Sent", "Failed", "Bounced", "Blocked"], weights=[90, 4, 4, 2])[0]
        delivered = status == "Sent"
        opened = delivered and ctx.rng.random() < 0.35
        clicked = opened and ctx.rng.random() < 0.3
        converted = clicked and ctx.rng.random() < 0.2
        channel = campaign["campaign_type"] if campaign["campaign_type"] != "Mix" else "Email"
        rows.append({
            "send_id": f"SD{i + 1:08d}", "send_date": event, "process_date": process,
            "campaign_id": campaign["campaign_id"], "customer_id": customer["customer_id"], "send_channel": channel,
            "template_used": f"tpl_{campaign['campaign_id'].lower()}", "subject": campaign["campaign_name"],
            "send_status": status, "was_delivered": delivered, "was_opened": opened,
            "open_date": event + timedelta(hours=2) if opened else None, "was_clicked": clicked,
            "click_date": event + timedelta(hours=3) if clicked else None,
            "click_count": ctx.rng.randint(1, 4) if clicked else 0, "had_conversion": converted,
            "conversion_date": event + timedelta(days=1) if converted else None,
            "conversion_value": ctx.money(50, 3000) if converted else None,
            "open_device": ctx.rng.choice(["Android", "iPhone", "Desktop"]) if opened else None,
            "open_country": customer["country"] if opened else None,
            "failure_reason": None if delivered else "Destinatario no disponible",
            "send_cost": round(ctx.rng.uniform(0.001, 0.05), 4),
        })
    return rows
