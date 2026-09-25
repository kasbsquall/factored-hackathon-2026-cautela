"""Render structured hints as a customer's free-text description.

Six template families. F1-F4 are used in every split; F5 (formal letter) and
F6 (oral, regional slang, number words) appear only in the test split. Spanish
has MX, CO and AR variants. Portuguese (pt-BR) text is TEAM-GENERATED: it is a
rendering of the same hints, not a translation of any organizer text.
"""

from __future__ import annotations

import random
import re

from ml.scenarios.hints import norm, typo
from ml.scenarios.vocab import (APPROX_ES, APPROX_PT, CHANNEL_ES, CHANNEL_PT, MONTHS_ES, MONTHS_PT, NOUNS,
                                NUMBER_WORDS_ES, NUMBER_WORDS_PT, TYPE_ES, TYPE_PT, WEEKDAYS_ES, WEEKDAYS_PT)

SEEN_FAMILIES = ("F1", "F2", "F3", "F4")
HELDOUT_FAMILIES = ("F5", "F6")
PLACE_NOUNS = ("súper", "supermercado", "restaurante", "gasolinera", "ferretería", "tienda", "centro",
               "farmacia", "mercado", "posto", "loja", "shopping", "chino", "bomba", "tlapalería", "farma",
               "drogaria", "depósito", "mercadinho")


def _fmt_number(x: float, region: str, decimals: bool) -> str:
    """Thousands separator: comma in MX, dot in CO, AR and BR."""
    s = f"{x:,.2f}" if decimals else f"{x:,.0f}"
    if region in ("CO", "AR", "BR"):
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return s


def _scaled(x: float, region: str, lang: str) -> str | None:
    """'20 mil', '1,5 millones' style when the value allows it."""
    dec = "." if region == "MX" else ","
    if x >= 1_000_000 and (x / 100_000) == int(x / 100_000):
        m = x / 1_000_000
        num = str(int(m)) if m == int(m) else f"{m:.1f}".replace(".", dec)
        if lang == "es":
            return f"{num} millón" if m == 1 else f"{num} millones"
        return f"{num} milhão" if m == 1 else f"{num} milhões"
    if 10_000 <= x < 1_000_000 and x % 1000 == 0:
        return f"{int(x // 1000)} mil"
    return None


def amount_phrase(h: dict, lang: str, region: str, family: str, rng: random.Random) -> str:
    x, cur, exact = h["claimed"], h["currency"], h["exact"]
    if family == "F6":
        return _amount_slang(x, cur, lang, region, rng)
    if exact:
        num = _fmt_number(x, region, decimals=True)
    else:
        num = (_scaled(x, region, lang) if rng.random() < 0.7 else None) or _fmt_number(x, region, False)
    if family == "F5":
        code = f"{cur} " if cur else "$ "
        return code + num
    if cur == "USD":
        word = rng.choice(["dólares", "dólares", "USD"] + (["dlls"] if region == "MX" else []))
    elif cur:
        word = "pesos"
    else:
        word = ""
    if word and ("mill" in num or "milh" in num):
        word = "de " + word
    phrase = f"{num} {word}".strip() if word != "USD" else f"USD {num}"
    if not exact and rng.random() < 0.85:
        phrase = f"{rng.choice(APPROX_ES if lang == 'es' else APPROX_PT)} {phrase}"
    return phrase


def _amount_slang(x: float, cur: str | None, lang: str, region: str, rng: random.Random) -> str:
    words = NUMBER_WORDS_ES if lang == "es" else NUMBER_WORDS_PT
    near = round(x, -2)
    if near in words and abs(near / x - 1) < 0.1:
        num = words[int(near)]
    elif cur != "USD" and x >= 1000 and lang == "es" and region in ("AR", "CO"):
        if x >= 1_000_000 and region == "CO":
            m = round(x / 1_000_000, 1)
            num = "medio palo" if m == 0.5 else f"{m:g} palo".replace(".", ",") + ("" if m == 1 else "s")
        else:
            num = f"{round(x / 1000)} " + ("lucas" if region == "AR" else "barras")
        return f"como {num}" if lang == "es" else f"uns {num}"
    else:
        num = _fmt_number(x, region, False)
    if cur == "USD":
        unit = rng.choice(["verdes", "dólares"]) if lang == "es" else "dólares"
    elif cur:
        unit = "pesos" if lang == "es" else rng.choice(["pesos", "pila"])
    else:
        unit = "" if lang == "es" else "pila"
    lead = rng.choice(["como", "tipo"]) if lang == "es" else rng.choice(["tipo", "uns"])
    return f"{lead} {num} {unit}".strip()


def date_phrase(h: dict, lang: str, region: str, rng: random.Random) -> str:
    k = h["kind"]
    if lang == "es":
        wd = WEEKDAYS_ES
        table = {
            "today": "hoy", "yesterday": "ayer", "day_before": "anteayer" if region == "AR" else "antier",
            "this_week": "esta semana", "last_week": "la semana pasada",
            "two_weeks": rng.choice(["hace dos semanas", "hace como dos semanas"]),
            "about_month": rng.choice(["hace como un mes", "hace un mes más o menos"]),
            "early_month": rng.choice(["a principios de mes", "a inicios de este mes"]),
            "end_last_month": rng.choice(["a fin del mes pasado", "a finales del mes pasado"]),
            "last_month": "el mes pasado", "recently": rng.choice(["hace unos días", "hace poco"]),
            "just_now": "hace un rato", "other_week": "la otra semana", "weekend": "el finde",
        }
        if k == "days_ago":
            return f"hace {h['n']} días"
        if k == "weekday":
            return rng.choice([f"el {wd[h['weekday']]}", f"el {wd[h['weekday']]} pasado"])
        if k == "exact_day":
            return rng.choice([f"el {h['day']}", f"el {h['day']} de {MONTHS_ES[h['month'] - 1]}"])
        if k == "formal_date":
            return rng.choice([f"el pasado {h['day']} de {MONTHS_ES[h['month'] - 1]} de {h['year']}",
                               f"con fecha {h['day']:02d}/{h['month']:02d}/{h['year']}"])
        return table[k]
    wd = WEEKDAYS_PT
    table = {
        "today": "hoje", "yesterday": "ontem", "day_before": "anteontem", "this_week": "esta semana",
        "last_week": "na semana passada", "two_weeks": "há duas semanas",
        "about_month": rng.choice(["há mais ou menos um mês", "há cerca de um mês"]),
        "early_month": "no começo do mês", "end_last_month": "no fim do mês passado",
        "last_month": "no mês passado", "recently": rng.choice(["há poucos dias", "esses dias"]),
        "just_now": "agora há pouco", "other_week": "na outra semana", "weekend": "no fds",
    }
    if k == "days_ago":
        return f"há {h['n']} dias"
    if k == "weekday":
        return ("no " if h["weekday"] >= 5 else "na ") + wd[h["weekday"]]
    if k == "exact_day":
        return rng.choice([f"no dia {h['day']}", f"no dia {h['day']} de {MONTHS_PT[h['month'] - 1]}"])
    if k == "formal_date":
        return f"em {h['day']:02d}/{h['month']:02d}/{h['year']}"
    return table[k]


def where_phrase(h: dict, lang: str, family: str, rng: random.Random) -> str:
    if h["form"] == "noun":
        spec = NOUNS[h["noun_key"]]
        pool = spec[("slang_" if family == "F6" else "") + lang]
        noun = rng.choice(pool)
        place = any(p in noun for p in PLACE_NOUNS)
        if lang == "es":
            return ("en " if place else "de ") + noun
        return ("em " if place else "de ") + noun
    return ("en " if lang == "es" else "em ") + h["surface"]


def pieces(hints: dict, lang: str, region: str, family: str, rng: random.Random) -> dict:
    generic = {"MX": "un cargo", "CO": "un cobro", "AR": rng.choice(["un débito", "un cargo"])}
    if lang == "es":
        obj = TYPE_ES[hints["type"]["value"]][region] if "type" in hints else generic[region]
    else:
        obj = TYPE_PT[hints["type"]["value"]] if "type" in hints else "uma cobrança"
    p = {"obj": obj, "A": "", "D": "", "W": "", "C": "", "Y": ""}
    if "amount" in hints:
        p["A"] = amount_phrase(hints["amount"], lang, region if lang == "es" else "BR", family, rng)
    if "date" in hints:
        p["D"] = date_phrase(hints["date"], lang, region, rng)
    if "merchant" in hints:
        p["W"] = where_phrase(hints["merchant"], lang, family, rng)
    if "channel" in hints:
        ch = hints["channel"]["value"]
        p["C"] = rng.choice(CHANNEL_ES[ch][region] if lang == "es" else CHANNEL_PT[ch])
    if "city" in hints:
        p["Y"] = ("en " if lang == "es" else "em ") + hints["city"]["value"]
    return p


def _join(*parts: str) -> str:
    return " ".join(x for x in parts if x).replace(" ,", ",")


def _chatify(text: str, rng: random.Random) -> str:
    plain = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", "", norm(text).replace("¿", "").replace("?", ""))
    words = plain.split()
    for _ in range(rng.choice([0, 1, 1, 2])):
        idx = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha()]
        if idx:
            i = rng.choice(idx)
            words[i] = typo(rng, words[i])
    return " ".join(words)


def render(hints: dict, lang: str, region: str, family: str, rng: random.Random) -> str:
    p = pieces(hints, lang, region, family, rng)
    a = f"de {p['A']}" if p["A"] else ""
    tail = _join(p["D"], p["W"], p["C"], p["Y"])
    if lang == "es":
        return _render_es(p, a, tail, region, family, rng)
    return _render_pt(p, a, tail, family, rng)


def _render_es(p: dict, a: str, tail: str, region: str, family: str, rng: random.Random) -> str:
    verb = {"MX": "no reconozco", "CO": "no reconozco", "AR": "no reconozco"}[region]
    if family == "F1":
        opener = rng.choice(["", "Hola.", "Buenas tardes."])
        core = rng.choice([f"Me hicieron {_join(p['obj'], a, tail)} y {verb} ese movimiento.",
                           f"Tengo {_join(p['obj'], a, tail)} que {verb}."])
        return _join(opener, core)
    if family == "F2":
        opener = rng.choice(["Hola,", "Buen día,"])
        return _join(opener, f"estaba revisando mis movimientos y vi {_join(p['obj'], a, tail)}.",
                     rng.choice(["Yo no fui.", "Yo no hice eso.", "No sé qué es."]))
    if family == "F3":
        core = f"¿Qué es {_join(p['obj'], a)} que me salió {tail}?" if tail else f"¿Qué es {_join(p['obj'], a)}?"
        return _join(core, rng.choice(["No lo reconozco.", "No me suena.", ""]))
    if family == "F4":
        return _chatify(_join(p["obj"], "raro", a, tail, "no lo reconozco"), rng)
    if family == "F5":
        a5 = f"por un monto aproximado de {p['A']}" if p["A"] else ""
        d5 = f"realizado {p['D']}" if p["D"] else ""
        return _join("Estimados:", f"por medio de la presente solicito la revisión de {_join(p['obj'], a5, d5, p['W'], p['C'], p['Y'])},",
                     "el cual no reconozco. Quedo atento a su respuesta.")
    opener = {"AR": "Che,", "MX": "Oigan,", "CO": "Qué más,"}[region]
    return _join(opener, f"me cayó {_join(p['obj'], a, tail)} y ni idea qué es")


def _render_pt(p: dict, a: str, tail: str, family: str, rng: random.Random) -> str:
    if family == "F1":
        return _join(rng.choice(["", "Olá."]), f"Fizeram {_join(p['obj'], a, tail)} que eu não reconheço.")
    if family == "F2":
        return _join("Oi,", f"olhando meu extrato vi {_join(p['obj'], a, tail)}.", "Não fui eu.")
    if family == "F3":
        core = f"O que é {_join(p['obj'], a)} que apareceu {tail}?" if tail else f"O que é {_join(p['obj'], a)}?"
        return _join(core, rng.choice(["Não reconheço.", ""]))
    if family == "F4":
        return _chatify(_join(p["obj"], "estranha", a, tail, "nao reconheco"), rng)
    if family == "F5":
        a5 = f"no valor aproximado de {p['A']}" if p["A"] else ""
        d5 = f"realizada {p['D']}" if p["D"] else ""
        return _join("Prezados,", f"venho solicitar a análise de {_join(p['obj'], a5, d5, p['W'], p['C'], p['Y'])},",
                     "que não reconheço. Aguardo retorno.")
    return _join("Gente,", f"caiu {_join(p['obj'], a, tail)} e não faço ideia do que é")
