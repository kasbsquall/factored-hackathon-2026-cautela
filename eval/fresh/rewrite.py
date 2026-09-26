"""Rewrites of test_fresh descriptions for the multilingual category of eval_fresh.

Three rewrites, each built so the case label cannot change:
  code_switch     swaps Spanish phrases for Portuguese ones (or the reverse) in the case's own description, with
                  numbers, ids and the merchant surface masked, so every cue keeps its value;
  render_charge   re-renders every hint of the case (type, amount, merchant, date, channel, city, with the same
                  values) in new wording: the regional-slang frames and the magnitude-word frames use it;
  magnitude       rewrites the amount hint as magnitude words ("1,7 millones", "dos lucas", "cinco palos"). The
                  stated amount can move to a round value; the builder keeps the rewrite only when the label rule
                  of ml/scenarios/hints.py gives the same consistent and near-consistent charges as before.
"""

from __future__ import annotations

import random
import re
from datetime import date

from eval.fresh import texts

LOCAL_CURRENCY = {"MX": "MXN", "CO": "COP", "AR": "ARS"}

# ---- number words ------------------------------------------------------------------------------------------
_ES_0_29 = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
            "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte",
            "veintiuno", "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete",
            "veintiocho", "veintinueve"]
_ES_TENS = {3: "treinta", 4: "cuarenta", 5: "cincuenta", 6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa"}
_ES_HUNDREDS = {1: "ciento", 2: "doscientos", 3: "trescientos", 4: "cuatrocientos", 5: "quinientos",
                6: "seiscientos", 7: "setecientos", 8: "ochocientos", 9: "novecientos"}
_PT_0_19 = ["zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze",
            "treze", "catorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
_PT_TENS = {2: "vinte", 3: "trinta", 4: "quarenta", 5: "cinquenta", 6: "sessenta", 7: "setenta", 8: "oitenta",
            9: "noventa"}
_PT_HUNDREDS = {1: "cento", 2: "duzentos", 3: "trezentos", 4: "quatrocentos", 5: "quinhentos", 6: "seiscentos",
                7: "setecentos", 8: "oitocentos", 9: "novecentos"}


def _es_below_1000(n: int, apocope: bool) -> str:
    if n == 100:
        return "cien"
    h, rest = divmod(n, 100)
    out = [_ES_HUNDREDS[h]] if h else []
    if rest:
        if rest < 30:
            word = _ES_0_29[rest]
        else:
            t, u = divmod(rest, 10)
            word = _ES_TENS[t] + (f" y {_ES_0_29[u]}" if u else "")
        if apocope and word.endswith("uno"):
            word = word[:-3] + ("ún" if word != "uno" else "un")
        out.append(word)
    return " ".join(out)


def words_es(n: int) -> str:
    """Spanish cardinal for 1 <= n < 1,000,000 ('un' before a noun: 'veintiún mil', 'un millón' is handled by
    the caller)."""
    if not 1 <= n < 1_000_000:
        raise ValueError(n)
    th, rest = divmod(n, 1000)
    out = []
    if th:
        out.append("mil" if th == 1 else _es_below_1000(th, apocope=True) + " mil")
    if rest:
        out.append(_es_below_1000(rest, apocope=False))
    return " ".join(out)


def _pt_below_1000(n: int) -> str:
    if n == 100:
        return "cem"
    h, rest = divmod(n, 100)
    parts = [_PT_HUNDREDS[h]] if h else []
    if rest:
        if rest < 20:
            parts.append(_PT_0_19[rest])
        else:
            t, u = divmod(rest, 10)
            parts.append(_PT_TENS[t] + (f" e {_PT_0_19[u]}" if u else ""))
    return " e ".join(parts)


def words_pt(n: int) -> str:
    if not 1 <= n < 1_000_000:
        raise ValueError(n)
    th, rest = divmod(n, 1000)
    out = []
    if th:
        out.append("mil" if th == 1 else _pt_below_1000(th) + " mil")
    if rest:
        joiner = "e " if (rest < 100 or rest % 100 == 0) and th else ""
        out.append(joiner + _pt_below_1000(rest))
    return " ".join(out)


# ---- amounts ----------------------------------------------------------------------------------------------
def fmt_number(x: float, region: str, decimals: bool) -> str:
    """Thousands separator: comma in MX, dot in CO, AR and Portuguese text."""
    s = f"{x:,.2f}" if decimals else f"{x:,.0f}"
    return s if region == "MX" else s.replace(",", "_").replace(".", ",").replace("_", ".")


def _decimal(m: float, region: str) -> str:
    return str(int(m)) if m == int(m) else f"{m:.1f}".replace(".", "." if region == "MX" else ",")


def currency_class(stated: str | None, pool_currencies: set[str], country: str) -> str:
    """local: pesos of the customer's country; usd; none_local: not stated and every candidate in local pesos;
    none: not stated otherwise (no unit word may be written)."""
    local = LOCAL_CURRENCY[country]
    if stated == "USD":
        return "usd"
    if stated == local:
        return "local"
    if stated is None and pool_currencies == {local}:
        return "none_local"
    return "none" if stated is None else "other"


def magnitude_value(claimed: float) -> list[float]:
    """Round values a magnitude phrase can state exactly, closest first."""
    if claimed >= 1_000_000:
        return [round(claimed / 100_000) * 100_000.0]
    if claimed >= 1000:
        out = [round(claimed / 1000) * 1000.0, round(claimed / 100) * 100.0]
        return list(dict.fromkeys(v for v in out if v >= 1000))
    return []


def _luca_count(k: int) -> str:
    """'una luca', 'dos lucas', 'cincuenta lucas', '150 lucas' (luca is feminine; digits above the simple words)."""
    if k == 1:
        return "una"
    if k <= 20:
        return _ES_0_29[k]
    if k < 100 and k % 10 == 0:
        return _ES_TENS[k // 10]
    return str(k)


def magnitude_phrase(value: float, cls: str, lang: str, region: str, rng: random.Random) -> str | None:
    """A phrase that states `value` exactly with a magnitude word, or None when no form fits."""
    if cls == "other":
        return None
    forms: list[str] = []
    local_unit = {"es": "pesos", "pt": "pesos"}[lang]
    usd_unit = "dólares"
    slang_ok = lang == "es" and cls in ("local", "none_local")
    if value >= 1_000_000:
        m = value / 1_000_000
        if lang == "es":
            num = "un millón" if m == 1 else f"{_decimal(m, region)} millones"
            forms.append(num)
            if m == int(m) and 1 < m <= 20:
                forms.append(f"{_ES_0_29[int(m)]} millones")
            if slang_ok:
                one, many = texts.MAGNITUDE["es"]["million_slang"][region]
                whole, rest = int(m), round((m - int(m)) * 10)
                if rest == 0 and whole <= 20:
                    forms.append(f"un {one}" if whole == 1 else f"{_ES_0_29[whole]} {many}")
                elif rest == 5 and whole <= 20:
                    forms.append(f"{one} y medio" if whole == 1 else f"{_ES_0_29[whole]} {many} y medio")
                elif region in ("CO", "AR") and whole <= 20:
                    lead = f"un {one}" if whole == 1 else f"{_ES_0_29[whole]} {many}"
                    forms.append(f"{lead} {_ES_HUNDREDS[rest] if rest != 1 else 'cien'}")
            unit = {"local": f"de {local_unit}", "usd": f"de {usd_unit}"}.get(cls, "")
        else:
            num = "um milhão" if m == 1 else f"{_decimal(m, 'BR')} {'milhão' if m < 2 else 'milhões'}"
            forms.append(num)
            if m == int(m) and 1 < m < 20:
                forms.append(f"{_PT_0_19[int(m)]} milhões")
            unit = {"local": f"de {local_unit}", "usd": f"de {usd_unit}"}.get(cls, "")
        phrase = rng.choice(forms)
        slang = any(w in phrase for w in ("palo", "melón", "melones"))
        return phrase if slang or not unit else f"{phrase} {unit}"
    if value < 1000:
        return None
    n = int(value)
    k = value / 1000
    unit = {"local": local_unit, "usd": usd_unit}.get(cls, "")
    if lang == "es":
        if k == int(k) and k > 1:
            forms.append(f"{int(k)} mil")
        forms.append(words_es(n))
        slang: list[str] = []
        if slang_ok and k == int(k):
            word = texts.MAGNITUDE["es"]["thousand_slang"][region]
            if region in ("CO", "AR"):
                slang.append(f"{_luca_count(int(k))} {'luca' if k == 1 else word}")
            else:
                slang.append(f"{words_es(n)} varos")
        if cls == "usd" and k == int(k) and region == "AR":
            slang.append(f"{_luca_count(int(k))} {'luca verde' if k == 1 else 'lucas verdes'}")
        if cls == "usd":
            slang.append(f"{words_es(n)} verdes")
        pick = rng.choice(forms + slang)
        if pick in slang:
            return pick
        return f"{pick} {unit}".strip()
    if k == int(k) and k > 1:
        forms.append(f"{int(k)} mil")
    forms.append(words_pt(n))
    return f"{rng.choice(forms)} {unit}".strip()


def plain_amount(h: dict, cls: str, lang: str, region: str, rng: random.Random) -> str:
    x, exact = h["claimed"], h["exact"]
    num = fmt_number(x, region if lang == "es" else "BR", decimals=exact)
    unit = {"usd": "dólares", "local": "pesos"}.get(cls, "")
    phrase = f"{num} {unit}".strip()
    return phrase if exact else f"{rng.choice(texts.APPROX[lang])} {phrase}"


# ---- the rest of the charge --------------------------------------------------------------------------------
def date_words(h: dict, lang: str, report: date) -> str:
    k = h["kind"]
    if k == "days_ago":
        return f"hace unos {h['n']} días" if lang == "es" else f"faz uns {h['n']} dias"
    if k == "weekday":
        wd = texts.WEEKDAYS[lang][h["weekday"]]
        if lang == "es":
            return f"el {wd} recién pasado"
        return f"no último {wd}" if h["weekday"] >= 5 else f"na última {wd}"
    if k == "exact_day":
        prev = (report.year, report.month - 1) if report.month > 1 else (report.year - 1, 12)
        when = (h["year"], h["month"])
        if when == (report.year, report.month):
            return f"el {h['day']} de este mes" if lang == "es" else f"no dia {h['day']} deste mês"
        if when == prev:
            return f"el {h['day']} del mes pasado" if lang == "es" else f"no dia {h['day']} do mês passado"
        month = texts.MONTHS[lang][h["month"] - 1]
        return f"el {h['day']} de {month}" if lang == "es" else f"no dia {h['day']} de {month}"
    if k == "formal_date":
        month = texts.MONTHS[lang][h["month"] - 1]
        if lang == "es":
            return f"el {h['day']} de {month} del {h['year']}"
        return f"em {h['day']} de {month} de {h['year']}"
    return texts.DATE_WORDS[lang][k]


def merchant_words(h: dict, lang: str, rng: random.Random) -> str:
    if h["form"] == "noun":
        return texts.NOUN_WORDS[h["noun_key"]][lang]
    return rng.choice(texts.MERCHANT_FRAMES[lang]).format(m=h["surface"])


def render_charge(hints: dict, lang: str, region: str, report: date, rng: random.Random, cls: str,
                  amount_text: str | None = None) -> str:
    """Every hint of the case, with its value, in the fresh wording. `amount_text` replaces the plain amount."""
    if "type" in hints:
        words = texts.TYPE_WORDS[lang][hints["type"]["value"]]
        obj = words[region] if lang == "es" else words
    else:
        obj = texts.GENERIC_OBJECT["es"][region] if lang == "es" else texts.GENERIC_OBJECT["pt"]
    parts = [obj]
    if "amount" in hints:
        parts.append("de " + (amount_text or plain_amount(hints["amount"], cls, lang, region, rng)))
    if "merchant" in hints:
        parts.append(merchant_words(hints["merchant"], lang, rng))
    if "date" in hints:
        parts.append(date_words(hints["date"], lang, report))
    if "channel" in hints:
        parts.append(texts.CHANNEL_WORDS[lang][hints["channel"]["value"]])
    if "city" in hints:
        parts.append(("en " if lang == "es" else "em ") + hints["city"]["value"])
    return " ".join(parts)


# ---- code-switching ----------------------------------------------------------------------------------------
_PROTECT = re.compile(r"(?:TRX|PRD|CASE|CLI)-[A-Z0-9]+|\d[\d.,/:]*")


def code_switch(text: str, swaps: list[tuple[str, str]], rng: random.Random,
                protect: tuple[str, ...] = ()) -> str | None:
    """Apply about half of the swaps that occur in `text` (at least one), on whole words only, never inside a
    number, an id or a protected surface (the merchant). None when no swap applies."""
    masked: list[str] = []

    def hide(s: str) -> str:  # one private-use character per masked span: no digit, no word character
        masked.append(s)
        return chr(0xF0000 + len(masked) - 1)

    work = text
    for p in sorted((p for p in protect if p), key=len, reverse=True):
        work = work.replace(p, hide(p))
    work = _PROTECT.sub(lambda m: hide(m.group(0)), work)
    pattern = {a: re.compile(r"(?<!\w)" + re.escape(a) + r"(?!\w)") for a, _ in swaps}
    present = [(a, b) for a, b in swaps if pattern[a].search(work)]
    if not present:
        return None
    rng.shuffle(present)
    chosen = present[:max(1, (len(present) + 1) // 2)]
    order = {a: i for i, (a, _) in enumerate(swaps)}  # longer phrases are listed first; keep that precedence
    for a, b in sorted(chosen, key=lambda s: order[s[0]]):
        work = pattern[a].sub(b, work)
    for i, s in enumerate(masked):
        work = work.replace(chr(0xF0000 + i), s)
    return work if work != text else None
