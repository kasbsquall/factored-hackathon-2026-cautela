"""Deterministic parser: free-text dispute description -> structured cues.

Shared by the rule baseline and the learned ranker. It extracts an approximate
amount and currency, a relative date range, and type / channel / merchant-noun
keywords. Only the phrasing of the seen families is supported (see lexicon.py).
A cue the parser cannot read is left as None; it is never guessed.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from datetime import date, timedelta

from ml.features.lexicon import (APPROX_WORDS, CHANNEL_WORDS, CURRENCY_WORDS, MONTHS, MULTIPLIERS, NOUN_MERCHANTS,
                                 TYPE_WORDS, WEEKDAYS)


def norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@dataclass
class ParsedDescription:
    text: str
    amount: float | None = None
    approx: bool = False
    currency: str | None = None           # "USD", "PESOS", "COP", "ARS" or None
    date_lo: date | None = None
    date_hi: date | None = None
    types: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    noun_merchants: set[str] = field(default_factory=set)


_NUM = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_AMOUNT_RE = re.compile(rf"(?:(usd|cop|ars|\$)\s*)?({_NUM})(?:\s*(mil|k|millones|millon|milhoes|milhao)\b)?"
                        rf"(?:\s*(?:de\s+)?(dolares|dolar|usd|dlls|dls|pesos))?")


def parse_number(tok: str) -> float:
    """'1.627.495,96' -> 1627495.96, '10,000' -> 10000, '1,8' -> 1.8, '467.08' -> 467.08."""
    seps = [c for c in tok if c in ".,"]
    if not seps:
        return float(tok)
    last = max(tok.rfind("."), tok.rfind(","))
    tail = len(tok) - last - 1
    if tail == 3 and (len(set(seps)) == 1):
        return float(re.sub(r"[.,]", "", tok))
    head = re.sub(r"[.,]", "", tok[:last])
    return float(f"{head}.{tok[last + 1:]}")


def _date_context(t: str, start: int, end: int) -> bool:
    before, after = t[max(0, start - 8):start], t[end:end + 7]
    if re.search(r"(\bel|\bdia|\bhace|\bha)\s*$", before) or re.match(r"\s*(dias|de (?:%s))" % "|".join(MONTHS), after):
        return True
    return bool(re.match(r"\s*/", after)) or bool(re.search(r"/\s*$", before))


def _amount(t: str, p: ParsedDescription) -> None:
    best = None
    for m in _AMOUNT_RE.finditer(t):
        code, num, mult, word = m.groups()
        if _date_context(t, m.start(2), m.end(2)) and not (code or mult or word):
            continue
        value = parse_number(num) * MULTIPLIERS.get(mult or "", 1)
        strength = bool(code) + bool(mult) + bool(word)
        if 2020 <= value <= 2030 and not strength:
            continue
        if best is None or strength > best[0]:
            best = (strength, value, code, word)
    if best:
        _, p.amount, code, word = best
        cur = (code or "") + " " + (word or "")
        for key, words in CURRENCY_WORDS.items():
            if any(w in cur.split() for w in words):
                p.currency = key
        if code in ("cop", "ars"):
            p.currency = code.upper()
        p.approx = any(re.search(rf"\b{w}\b", t) for w in APPROX_WORDS)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _dates(t: str, report: date, p: ParsedDescription) -> None:
    def rng(lo_age: int, hi_age: int) -> None:
        p.date_lo, p.date_hi = report - timedelta(days=hi_age), report - timedelta(days=lo_age)

    wd = report.weekday()
    prev_end = _month_start(report) - timedelta(days=1)
    if m := re.search(r"\b(?:hace|ha) (\d+) dias\b", t):
        n = int(m.group(1)); rng(n - 1, n + 1)
    elif re.search(r"\b(antier|anteayer|anteontem)\b", t):
        rng(2, 2)
    elif re.search(r"\b(ayer|ontem)\b", t):
        rng(1, 1)
    elif re.search(r"\b(hoy|hoje)\b", t):
        rng(0, 0)
    elif re.search(r"\bdos semanas|duas semanas\b", t):
        rng(10, 18)
    elif re.search(r"\bsemana pasada|semana passada\b", t):
        rng(wd + 1, wd + 7)
    elif re.search(r"\besta semana\b", t):
        rng(0, wd)
    elif re.search(r"\bun mes\b|\bum mes\b", t):
        rng(22, 40)
    elif re.search(r"principios de|inicios de|comeco do mes", t):
        rng(max(report.day - 10, 0), report.day - 1)
    elif re.search(r"(fin|finales) del mes pasado|fim do mes passado", t):
        rng(report.day, report.day + 9)
    elif re.search(r"\bmes pasado|mes passado\b", t):
        p.date_lo, p.date_hi = _month_start(prev_end), prev_end
    elif m := re.search(r"\b(?:el|no dia) (\d{1,2})(?: de (%s))?\b" % "|".join(MONTHS), t):
        _exact_day(int(m.group(1)), MONTHS.get(m.group(2) or ""), report, p)
    elif m := re.search(r"\b(?:el|na|no) (%s)\b" % "|".join(WEEKDAYS), t):
        age = (wd - WEEKDAYS[m.group(1)]) % 7 or 7
        rng(age, age)
    elif re.search(r"hace unos dias|hace poco|ha poucos dias|esses dias", t):
        rng(0, 14)


def _exact_day(day: int, month: int | None, report: date, p: ParsedDescription) -> None:
    y, mo = report.year, month or report.month
    for _ in range(13):
        if day <= calendar.monthrange(y, mo)[1] and date(y, mo, day) <= report:
            p.date_lo = p.date_hi = date(y, mo, day)
            return
        if month:
            y -= 1
        else:
            mo -= 1
            if mo == 0:
                y, mo = y - 1, 12


_CUE_WORDS = sorted({w for phrase in [*APPROX_WORDS, *MULTIPLIERS, *MONTHS, *WEEKDAYS,
                                      *(x for ws in TYPE_WORDS.values() for x in ws),
                                      *(x for ws in CHANNEL_WORDS.values() for x in ws),
                                      *(x for ws in CURRENCY_WORDS.values() for x in ws),
                                      "semana", "semanas", "pasada", "pasado", "passada", "passado", "principios",
                                      "inicios", "finales", "comeco", "dias", "antier", "anteayer", "anteontem",
                                      "ayer", "ontem", "esta", "duas"]
                     for w in phrase.split() if len(w) >= 5})


@lru_cache(maxsize=8192)
def _correct(word: str) -> str:
    """Map a misspelled cue word (edit ratio >= 0.8) to the lexicon spelling; other words pass through."""
    if len(word) < 5 or not word.isalpha() or word in _CUE_WORDS:
        return word
    best = max(_CUE_WORDS, key=lambda w: SequenceMatcher(None, word, w).ratio())
    return best if SequenceMatcher(None, word, best).ratio() >= 0.8 else word


def parse_description(text: str, report: date) -> ParsedDescription:
    raw = norm(text)
    t = " ".join(_correct(w) for w in raw.split())
    p = ParsedDescription(text=raw)
    _amount(t, p)
    _dates(t, report, p)
    for typ, words in TYPE_WORDS.items():
        if any(re.search(rf"\b{w}\b", t) for w in words):
            p.types.append(typ)
    for ch, words in CHANNEL_WORDS.items():
        if any(re.search(rf"\b{w}\b", t) for w in words):
            p.channels.append(ch)
    for noun, merchants in NOUN_MERCHANTS.items():
        if re.search(rf"\b{noun}\b", t):
            p.noun_merchants.update(merchants)
    return p
