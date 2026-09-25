"""Clean row builders for the dimension and reference tables of the synthetic fixture.

Builders return plain dicts keyed by contract column names. They never produce contract violations on their own;
every issue in the fixture is injected later by `issues.py` so that the manifest counts are exact.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from data_engineering.fixtures import vocab

SIZES = {
    "customers": 500, "branches": 30, "service_agents": 40, "marketing_campaigns": 12,
    "transactions": 20_000, "call_center_interactions": 3_200, "call_transcripts": 800,
    "satisfaction_surveys": 1_000, "digital_events": 40_000, "complaints": 400, "campaign_sends": 8_000,
}


@dataclass
class Ctx:
    """Shared generation context. One seeded RNG drives everything, so output depends only on the seed."""

    seed: int
    start: date = date(2026, 4, 1)
    days: int = 60
    rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.days - 1)

    def day(self) -> date:
        return self.start + timedelta(days=self.rng.randrange(self.days))

    def moment(self, day: date) -> datetime:
        return datetime.combine(day, time(0)) + timedelta(seconds=self.rng.randrange(86_400))

    def past(self, days_back_min: int, days_back_max: int) -> date:
        return self.start - timedelta(days=self.rng.randint(days_back_min, days_back_max))

    def money(self, low: float, high: float) -> float:
        return round(self.rng.uniform(low, high), 2)


def _person(ctx: Ctx) -> tuple[str, str, str]:
    gender = ctx.rng.choice(["M", "F", "F", "M", "O"])
    pool = vocab.FIRST_NAMES_M if gender == "M" else vocab.FIRST_NAMES_F
    return ctx.rng.choice(pool), f"{ctx.rng.choice(vocab.LAST_NAMES)} {ctx.rng.choice(vocab.LAST_NAMES)}", gender


def _phone(ctx: Ctx, country: str) -> str:
    return f"{vocab.COUNTRIES[country]['phone_prefix']}{ctx.rng.randrange(10**9, 10**10)}"


def _slug(text: str) -> str:
    table = str.maketrans("áéíóúñÁÉÍÓÚÑ ", "aeiounAEIOUN.")
    return text.translate(table).lower()


def build_branches(ctx: Ctx) -> list[dict]:
    rows = []
    countries = list(vocab.COUNTRIES)
    for i in range(SIZES["branches"]):
        country = countries[i % 3]
        city, state = ctx.rng.choice(vocab.COUNTRIES[country]["cities"])
        has_atms = ctx.rng.random() < 0.85
        rows.append({
            "branch_id": f"BR{i + 1:04d}", "branch_code": f"S{i + 1:03d}", "branch_name": f"Sucursal {city} {i + 1}",
            "branch_type": ctx.rng.choice(["Main", "Express", "Premium", "Corporate"]),
            "address": f"{ctx.rng.choice(vocab.STREETS)} {ctx.rng.randint(10, 999)}, {city}",
            "city": city, "state": state, "country": country, "postal_code": f"{ctx.rng.randint(10000, 99999)}",
            "geographic_zone": ctx.rng.choice(["Urban", "Urban", "Suburban", "Rural"]),
            "phone": _phone(ctx, country), "email": f"sucursal{i + 1}@bancolatam.example",
            "opening_time": time(9, 0), "closing_time": time(ctx.rng.choice([15, 16, 17]), 0),
            "has_atms": has_atms, "atm_count": ctx.rng.randint(1, 6) if has_atms else 0,
            "has_teller_windows": True, "teller_window_count": ctx.rng.randint(2, 10),
            "latitude": round(ctx.rng.uniform(-35, 25), 7), "longitude": round(ctx.rng.uniform(-100, -58), 7),
            "branch_opening_date": ctx.past(365, 7000),
            "branch_status": ctx.rng.choice(["Active"] * 8 + ["Temporarily Closed", "Closed"]),
        })
    return rows


def build_customers(ctx: Ctx, branches: list[dict]) -> list[dict]:
    rows = []
    for i in range(SIZES["customers"]):
        branch = ctx.rng.choice(branches)
        country = branch["country"]
        info = vocab.COUNTRIES[country]
        first, last, gender = _person(ctx)
        city, state = ctx.rng.choice(info["cities"])
        registered = datetime.combine(ctx.past(30, 3000), time(10)) + timedelta(minutes=ctx.rng.randrange(600))
        rows.append({
            "customer_id": f"C{i + 1:06d}", "document_number": f"{ctx.rng.randrange(10**7, 10**10)}",
            "document_type": ctx.rng.choice(info["documents"]), "first_name": first, "last_name": last,
            "date_of_birth": date(ctx.rng.randint(1950, 2006), ctx.rng.randint(1, 12), ctx.rng.randint(1, 28)),
            "gender": gender, "email": f"{_slug(first)}.{_slug(last.split()[0])}{i}@correo.example",
            "mobile_phone": _phone(ctx, country), "landline_phone": _phone(ctx, country),
            "address": f"{ctx.rng.choice(vocab.STREETS)} {ctx.rng.randint(1, 999)}, {city}",
            "city": city, "state": state, "country": country, "postal_code": f"{ctx.rng.randint(10000, 99999)}",
            "detected_accent": ctx.rng.choice([info["accent"]] * 9 + ["neutral"]),
            "segment": ctx.rng.choice(["Basic", "Basic", "Plus", "Premium", "Student"]),
            "credit_score": ctx.rng.randint(300, 850), "estimated_monthly_income": ctx.money(300, 9000),
            "occupation": ctx.rng.choice(vocab.OCCUPATIONS), "marital_status": ctx.rng.choice(vocab.MARITAL),
            "education_level": ctx.rng.choice(vocab.EDUCATION), "registration_date": registered,
            "registration_branch_id": branch["branch_id"],
            "customer_status": ctx.rng.choice(["Active"] * 8 + ["Inactive", "Suspended"]),
            "last_updated": datetime.combine(ctx.day(), time(3)), "accepts_marketing": ctx.rng.random() < 0.6,
        })
    return rows


def build_agents(ctx: Ctx, branches: list[dict]) -> list[dict]:
    rows = []
    countries = list(vocab.COUNTRIES)
    for i in range(SIZES["service_agents"]):
        country = countries[i % 3]
        first, last, _ = _person(ctx)
        rows.append({
            "agent_id": f"AG{i + 1:04d}", "employee_code": f"E{i + 1:05d}", "first_name": first, "last_name": last,
            "email": f"agente{i + 1}@bancolatam.example", "phone": _phone(ctx, country),
            "native_accent": vocab.COUNTRIES[country]["accent"], "country_of_origin": country,
            "assigned_branch_id": ctx.rng.choice(branches)["branch_id"],
            "agent_type": ctx.rng.choice(["Phone", "In-Person", "Digital", "Hybrid"]),
            "experience_level": ctx.rng.choice(["Junior", "Mid-Senior", "Senior", "Specialist"]),
            "languages": "Español", "specialty": ctx.rng.choice(["Tarjetas", "Créditos", "Reclamos", "General"]),
            "hire_date": ctx.past(60, 3000), "avg_csat": round(ctx.rng.uniform(2.5, 5.0), 2),
            "total_monthly_interactions": ctx.rng.randint(80, 600),
            "agent_status": ctx.rng.choice(["Active"] * 8 + ["Vacation", "Leave"]),
            "work_shift": ctx.rng.choice(["Morning", "Afternoon", "Night", "Rotating"]),
        })
    return rows


def build_campaigns(ctx: Ctx) -> list[dict]:
    rows = []
    for i in range(SIZES["marketing_campaigns"]):
        start = ctx.start + timedelta(days=ctx.rng.randrange(-30, 40))
        rows.append({
            "campaign_id": f"CMP{i + 1:04d}", "campaign_name": f"{vocab.CAMPAIGN_NAMES[i % 6]} {2026}-{i + 1}",
            "description": "Campaña de prueba generada para el fixture sintético.",
            "campaign_type": ctx.rng.choice(["Email", "SMS", "Push", "WhatsApp", "Voice", "Mix"]),
            "campaign_objective": ctx.rng.choice(["Acquisition", "Retention", "Cross-sell", "Up-sell", "Reactivation"]),
            "promoted_product": ctx.rng.choice(vocab.PRODUCT_TYPES),
            "target_segment": ctx.rng.choice(["Basic", "Plus", "Premium", "Student"]),
            "target_country": ctx.rng.choice(list(vocab.COUNTRIES)), "start_date": start,
            "end_date": start + timedelta(days=ctx.rng.randint(15, 60)), "budget": ctx.money(5_000, 80_000),
            "campaign_status": ctx.rng.choice(["Planned", "Active", "Active", "Paused", "Completed"]),
            "expected_conversion_rate": round(ctx.rng.uniform(0.5, 12), 2),
        })
    return rows


def build_products(ctx: Ctx, customers: list[dict]) -> list[dict]:
    rows = []
    for customer in customers:
        currency = vocab.COUNTRIES[customer["country"]]["currency"]
        kinds = ["Debit Card", "Savings Account"] + ctx.rng.sample(vocab.PRODUCT_TYPES, ctx.rng.randint(0, 2))
        for kind in dict.fromkeys(kinds):
            credit = kind in ("Credit Card", "Personal Loan", "Mortgage")
            opened = ctx.past(30, 2500)
            rows.append({
                "product_id": f"P{len(rows) + 1:07d}", "customer_id": customer["customer_id"], "product_type": kind,
                "product_number": f"{ctx.rng.randrange(10**15, 10**16)}",
                "currency": currency if ctx.rng.random() < 0.95 else "USD",
                "current_balance": ctx.money(0, 50_000), "credit_limit": ctx.money(1_000, 90_000) if credit else None,
                "interest_rate": round(ctx.rng.uniform(5, 60), 2) if credit else None, "opening_date": opened,
                "expiration_date": opened + timedelta(days=365 * 4) if kind in vocab.CARD_PRODUCTS else None,
                "opening_branch_id": customer["registration_branch_id"],
                "product_status": ctx.rng.choice(["Active"] * 12 + ["Blocked", "Closed", "Suspended"]),
                "opening_channel": ctx.rng.choice(["Branch", "Web", "App", "Call Center"]),
                "has_linked_app": ctx.rng.random() < 0.7,
                "days_past_due": ctx.rng.choice([0, 0, 0, 15, 30]) if credit else None,
                "last_transaction_date": ctx.moment(ctx.day()),
                "last_updated": datetime.combine(ctx.day(), time(3)),
            })
    return rows


def build_exchange_rates(ctx: Ctx) -> list[dict]:
    rows = []
    for currency, base in vocab.USD_PER_UNIT.items():
        rate = base
        for offset in range(ctx.days):
            rate = rate * (1 + ctx.rng.uniform(-0.01, 0.01))
            rows.append({
                "date": ctx.start + timedelta(days=offset), "source_currency": currency, "target_currency": "USD",
                "exchange_rate": round(rate, 6), "buy_rate": round(rate * 0.99, 6),
                "sell_rate": round(rate * 1.01, 6), "source": "Fixture random walk",
            })
    return rows
