"""Family F7: a phrasing family written for the fresh test split only (``test_fresh``).

F7 was written on 2026-09-25, after every model in ``ml/reports/fitted.json`` was trained and before any model
was run on a single F7 description. It is a messaging-app register: a greeting, a short piece of context, the
charge, and a request for help, with Spanish variants for Mexico, Colombia and Argentina and a pt-BR rendering.
Its surface forms (amount formats such as "$1,250 pesos mexicanos", "US$ 40" and "35k", number words for small day
counts, numeric dates such as "el 12/3", type and channel wording, merchant framing such as "a nombre de") are new:
none of them is used by families F1 to F6, and the ranker lexicon and parser were not changed after F7 existed.

The labels do not depend on this file. F7 renders the same structured hints as every other family, so the label
rule in ``hints.py`` applies unchanged. Only the Portuguese text is TEAM-GENERATED in the same sense as F1 to F6:
Brazil is not in the organizer data.

Generator-side only: rankers must not import this module.
"""

from __future__ import annotations

import random

from ml.scenarios.vocab import MONTHS_ES, MONTHS_PT, WEEKDAYS_ES, WEEKDAYS_PT

NUM_ES = {3: "tres", 4: "cuatro", 5: "cinco", 6: "seis"}
NUM_PT = {3: "três", 4: "quatro", 5: "cinco", 6: "seis"}
CURRENCY_ES = {"MXN": "pesos mexicanos", "COP": "pesos colombianos", "ARS": "pesos argentinos"}
TYPE_ES = {
    "Purchase": {"MX": "una compra", "CO": "un consumo", "AR": "un consumo"},
    "Withdrawal": {"MX": "un retiro de efectivo", "CO": "un retiro en efectivo", "AR": "una extracción de efectivo"},
    "Transfer": {"MX": "un envío de dinero", "CO": "un envío de plata", "AR": "un envío de plata"},
    "Payment": {"MX": "un pago", "CO": "un pago", "AR": "un pago"},
    "Adjustment": {"MX": "un ajuste", "CO": "un ajuste", "AR": "un ajuste"},
}
TYPE_PT = {"Purchase": "uma compra", "Withdrawal": "um saque em dinheiro", "Transfer": "um envio de dinheiro",
           "Payment": "um pagamento", "Adjustment": "um ajuste"}
GENERIC_ES = {"MX": "un cargo a mi tarjeta", "CO": "un movimiento", "AR": "un movimiento"}
CHANNEL_ES = {
    "ATM": {"MX": "en un cajero automático", "CO": "en un cajero automático", "AR": "en un cajero automático"},
    "App": {"MX": "desde la aplicación", "CO": "por la aplicación del banco", "AR": "por la aplicación"},
    "Web": {"MX": "en línea", "CO": "en una compra en línea", "AR": "online"},
    "POS": {"MX": "pasando la tarjeta en el local", "CO": "pagando con tarjeta en el local",
            "AR": "pagando con la tarjeta en el local"},
    "Branch": {"MX": "en ventanilla", "CO": "en ventanilla", "AR": "por ventanilla"},
    "Transfer": {"MX": "vía transferencia", "CO": "vía transferencia", "AR": "vía transferencia"},
}
CHANNEL_PT = {"ATM": "num terminal de autoatendimento", "App": "pelo app do banco", "Web": "online",
              "POS": "passando o cartão na loja", "Branch": "no caixa da agência", "Transfer": "via transferência"}
NOUNS = {  # noun group -> F7 surface forms; the groups and their merchants are the ones in vocab.NOUNS
    "supermarket": {"es": ["una tienda de autoservicio", "un almacén"], "pt": ["um atacadão"]},
    "restaurant": {"es": ["un lugar para comer"], "pt": ["um lugar para comer"]},
    "utility": {"es": ["una cuenta de servicios"], "pt": ["uma conta de consumo"]},
    "fuel": {"es": ["una estación de gasolina"], "pt": ["um posto de combustível"]},
    "ride": {"es": ["un servicio de transporte"], "pt": ["um serviço de transporte"]},
    "hardware": {"es": ["una tienda de materiales"], "pt": ["uma loja de material de construção"]},
    "clothing": {"es": ["un almacén de ropa"], "pt": ["uma loja de moda"]},
    "mall": {"es": ["un centro de compras"], "pt": ["um centro de compras"]},
    "entertainment": {"es": ["unas boletas de un evento"], "pt": ["uns bilhetes de um evento"]},
    "subscription": {"es": ["una suscripción mensual"], "pt": ["uma mensalidade"]},
    "pharmacy": {"es": ["una droguería"], "pt": ["uma farmácia de bairro"]},
    "health": {"es": ["un servicio de salud"], "pt": ["um serviço de saúde"]},
}
CONTEXT_ES = {"MX": ["Tengo la tarjeta conmigo.", "Acabo de revisar el estado de cuenta."],
              "CO": ["Tengo la tarjeta en mi poder.", "Estaba mirando el extracto."],
              "AR": ["La tarjeta la tengo yo.", "Estaba mirando el resumen."]}
CONTEXT_PT = ["Estou com o cartão aqui comigo.", "Acabei de olhar a fatura."]


def _fmt(x: float, region: str, decimals: bool) -> str:
    s = f"{x:,.2f}" if decimals else f"{x:,.0f}"
    return s if region == "MX" else s.replace(",", "_").replace(".", ",").replace("_", ".")


def amount_f7(h: dict, lang: str, region: str, rng: random.Random) -> str:
    x, cur, exact = h["claimed"], h["currency"], h["exact"]
    num = _fmt(x, region, decimals=exact)
    if not exact and 10_000 <= x < 1_000_000 and x % 1000 == 0 and rng.random() < 0.5:
        num = f"{int(x // 1000)}k"
    if cur == "USD":
        text = rng.choice([f"US$ {num}", f"{num} dólares americanos"])
        if lang == "es" and region == "AR":
            text = rng.choice([f"U$S {num}", f"{num} dólares americanos"])
    elif cur and lang == "es":
        text = rng.choice([f"${num} {CURRENCY_ES.get(cur, 'pesos')}", f"{num} {CURRENCY_ES.get(cur, 'pesos')}"])
    elif cur:
        text = f"{num} pesos"
    else:
        text = f"${num}"
    if exact:
        return text
    approx = (["algo así como", "aprox.", "más o menos"] if lang == "es"
              else ["aproximadamente", "algo em torno de", "mais ou menos"])
    return f"{rng.choice(approx)} {text}"


def date_f7(h: dict, lang: str, region: str, rng: random.Random) -> str:
    k = h["kind"]
    if lang == "es":
        table = {"today": "el día de hoy", "yesterday": "el día de ayer",
                 "day_before": "el día de anteayer" if region == "AR" else "el día de antier",
                 "this_week": "esta misma semana", "last_week": "la semana anterior",
                 "two_weeks": "hace un par de semanas", "about_month": "hace cosa de un mes",
                 "early_month": "los primeros días de este mes", "end_last_month": "los últimos días del mes pasado",
                 "last_month": "en el mes anterior", "recently": rng.choice(["estos días", "hace poquito"])}
        if k == "days_ago":
            return f"hace {NUM_ES[h['n']]} días"
        if k == "weekday":
            return f"este {WEEKDAYS_ES[h['weekday']]} que pasó"
        if k == "exact_day":
            return rng.choice([f"el {h['day']}/{h['month']}", f"el día {h['day']} de {MONTHS_ES[h['month'] - 1]}"])
        return table[k]
    table = {"today": "no dia de hoje", "yesterday": "ontem mesmo", "day_before": "antes de ontem",
             "this_week": "nesta semana", "last_week": "semana passada", "two_weeks": "umas duas semanas atrás",
             "about_month": "faz mais ou menos um mês", "early_month": "no início do mês",
             "end_last_month": "no final do mês passado", "last_month": "mês passado", "recently": "faz poucos dias"}
    if k == "days_ago":
        return f"faz {NUM_PT[h['n']]} dias"
    if k == "weekday":
        wd = WEEKDAYS_PT[h["weekday"]]
        return f"nesse último {wd}" if h["weekday"] >= 5 else f"nessa última {wd}"
    if k == "exact_day":
        return rng.choice([f"dia {h['day']}/{h['month']:02d}", f"dia {h['day']} de {MONTHS_PT[h['month'] - 1]}"])
    return table[k]


def where_f7(h: dict, lang: str, rng: random.Random) -> str:
    if h["form"] == "noun":
        return ("en " if lang == "es" else "em ") + rng.choice(NOUNS[h["noun_key"]][lang])
    if lang == "es":
        return rng.choice([f"en el comercio {h['surface']}", f"a nombre de {h['surface']}"])
    return rng.choice([f"no estabelecimento {h['surface']}", f"com o nome {h['surface']}"])


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


def render_f7(hints: dict, lang: str, region: str, rng: random.Random) -> str:
    if lang == "es":
        obj = TYPE_ES[hints["type"]["value"]][region] if "type" in hints else GENERIC_ES[region]
    else:
        obj = TYPE_PT[hints["type"]["value"]] if "type" in hints else "uma movimentação"
    amount = f"por {amount_f7(hints['amount'], lang, region, rng)}" if "amount" in hints else ""
    when = date_f7(hints["date"], lang, region, rng) if "date" in hints else ""
    where = where_f7(hints["merchant"], lang, rng) if "merchant" in hints else ""
    channel = ""
    if "channel" in hints:
        channel = CHANNEL_ES[hints["channel"]["value"]][region] if lang == "es" else CHANNEL_PT[hints["channel"]["value"]]
    city = ""
    if "city" in hints:
        city = ("en la ciudad de " if lang == "es" else "na cidade de ") + hints["city"]["value"]
    charge = _join(obj, amount, where, when, channel, city)
    context = rng.choice(CONTEXT_ES[region] if lang == "es" else CONTEXT_PT) if rng.random() < 0.5 else ""
    if lang == "pt":
        return rng.choice([
            _join("Bom dia, tudo bem?", f"Apareceu {charge} que não fui eu que fiz.", context, "Podem verificar?"),
            _join("Oi, boa tarde.", context, f"Tem {charge} que eu desconheço. Como faço para contestar?"),
        ])
    frames = {
        "MX": [_join("Buenas tardes, fíjese que me llegó una notificación de", charge + ".", context,
                     "La neta yo no lo hice, ¿me pueden ayudar?"),
               _join(f"Qué tal, les escribo porque me aparece {charge} y no lo reconozco.", context)],
        "CO": [_join("Buenas, qué pena molestar.", f"Me aparece {charge} que yo no hice.", context,
                     "¿Me colaboran con eso?"),
               _join("Hola, buen día.", context, f"Me está saliendo {charge} y no sé de dónde salió.")],
        "AR": [_join("Hola, buenas.", f"Me figura {charge} que no hice yo.", context, "¿Me dan una mano?"),
               _join(f"Buenas, les consulto: me aparece {charge} y no tengo idea de qué es.", context)],
    }
    return rng.choice(frames[region])
