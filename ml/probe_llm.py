"""Model-selection probe: the prompted LLM ranker on a small stratified validation sample.

    LLM_PROVIDER=ollama LLM_MODEL=qwen2.5:7b-instruct uv run python -m ml.probe_llm --n 50 --parallel 4

Runs on the validation split only, so choosing a provider never looks at test. The same cases, with the
same prompt, are scored by the rules and learned rankers so the numbers are directly comparable. Every
call goes through the provider-agnostic port in agent/llm (masking, schema validation, tokens, latency and
cost). When an NVIDIA GPU is present, its temperature, power, fan and memory are sampled during the run.

Writes ml/reports/probe/<provider>__<model>.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median

from agent.llm.config import build_adapter, load_settings
from agent.llm.port import MaskedLLM
from ml.data import MODELS_DIR, REPORTS_DIR, load_cases, ranker_input
from ml.evaluate import stratified_subset
from ml.rankers.learned import LearnedRanker
from ml.rankers.llm import SCHEMA, build_payload, load_prompt
from ml.rankers.protocol import order
from ml.rankers.rules import RuleRanker

PROBE_DIR = REPORTS_DIR / "probe"
GPU_FIELDS = ("temperature.gpu", "power.draw", "fan.speed", "utilization.gpu", "memory.used")


def pick_cases(n: int) -> list[dict]:
    """Smallest stratified sample (Spanish sources plus their Portuguese twins) with at least n cases."""
    val = load_cases("val")
    k = max(1, n // 2)
    while True:
        cases = stratified_subset(val, k)
        if len(cases) >= n or k > len(val):
            return sorted(cases, key=lambda c: c["case_id"])[:n]
        k += 1


class LLMPortRanker:
    """rank_v1 through the provider-agnostic port. The instructions travel in the message, then the payload."""

    def __init__(self, llm: MaskedLLM) -> None:
        self.llm = llm
        self.prompt_version, self.instructions = load_prompt()

    def rank(self, case: dict) -> list[tuple[str, float]]:
        cands = case["candidates"]
        payload, labels = build_payload(ranker_input(case), cands)
        data = self.llm.extract(f"{self.instructions}\n\nINPUT:\n{payload}", SCHEMA, trace_id=case["case_id"])
        scores = {labels[s["candidate"]]: min(1.0, max(0.0, float(s["score"])))
                  for s in data["scores"] if s.get("candidate") in labels}
        ids = [c["transaction_id"] for c in cands]
        return order(ids, [scores.get(i, 0.0) for i in ids], cands)


def judge(case: dict, ranked: list[tuple[str, float]]) -> dict:
    """Ranking quality only; the calibrated act/ask/hand-off decision is fitted in the full evaluation."""
    top_id, top_s = ranked[0] if ranked else (None, 0.0)
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    label = case["label"]
    return {"label": label,
            "top1_hit": (top_id == case["target_transaction_id"]) if label == "match" else None,
            "top1_consistent": (top_id in case["consistent_ids"]) if label == "ambiguous" else None,
            "no_match_top_score": round(top_s, 4) if label == "no_match" else None,
            "margin": round(top_s - second, 4)}


def rate(rows: list[dict], key: str) -> dict:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return {"n": len(vals), "rate": round(sum(vals) / len(vals), 4) if vals else None}


def gpu_sampler(stop: threading.Event, out: list, every_s: float = 5.0) -> None:
    query = ["nvidia-smi", f"--query-gpu={','.join(GPU_FIELDS)}", "--format=csv,noheader,nounits"]
    while not stop.is_set():
        try:
            line = subprocess.run(query, capture_output=True, text=True, timeout=10).stdout.strip().splitlines()[0]
            out.append(dict(zip(GPU_FIELDS, (float(v) for v in line.split(",")))))
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return  # no NVIDIA GPU or driver: the probe still runs, only without hardware readings
        stop.wait(every_s)


def gpu_summary(samples: list[dict]) -> dict | None:
    if not samples:
        return None
    return {f: {"max": max(s[f] for s in samples), "median": median(s[f] for s in samples)} for f in GPU_FIELDS} | {
        "samples": len(samples)}


def run_llm(cases: list[dict], parallel: int) -> tuple[list[dict], MaskedLLM, float]:
    llm = MaskedLLM(build_adapter(load_settings()), max_tokens=600)
    ranker = LLMPortRanker(llm)

    def one(case: dict) -> dict:
        try:
            return {"case_id": case["case_id"], "language": case["language"], "ok": True,
                    **judge(case, ranker.rank(case))}
        except Exception as exc:  # invalid JSON, schema mismatch or provider failure: counted, never hidden
            return {"case_id": case["case_id"], "language": case["language"], "ok": False,
                    "error": type(exc).__name__, "label": case["label"]}

    t0 = time.perf_counter()
    with ThreadPoolExecutor(parallel) as ex:
        rows = list(ex.map(one, cases))
    return rows, llm, time.perf_counter() - t0


def baseline(cases: list[dict]) -> dict:
    out = {}
    learned_path = MODELS_DIR / "learned.pkl"
    rankers = {"rules": RuleRanker()} | ({"learned": LearnedRanker.load(learned_path)} if learned_path.exists() else {})
    for name, rk in rankers.items():
        rows = [judge(c, rk.rank(ranker_input(c), c["candidates"])) for c in cases]
        out[name] = {"top1_match": rate(rows, "top1_hit"), "top1_ambiguous_consistent": rate(rows, "top1_consistent")}
    return out


def summarize(rows: list[dict], llm: MaskedLLM, wall_s: float, gpu: list[dict]) -> dict:
    ok = [r for r in rows if r["ok"]]
    usage = llm.usage.summary()
    no_match = [r["no_match_top_score"] for r in ok if r.get("no_match_top_score") is not None]
    return {
        "cases": len(rows), "valid_outputs": len(ok), "errors": sorted({r["error"] for r in rows if not r["ok"]}),
        "top1_match": rate(ok, "top1_hit"), "top1_ambiguous_consistent": rate(ok, "top1_consistent"),
        "no_match_median_top_score": round(median(no_match), 4) if no_match else None,
        "by_language": {lang: {"cases": sum(r["language"] == lang for r in rows),
                               "valid": sum(r["ok"] and r["language"] == lang for r in rows),
                               "top1_match": rate([r for r in ok if r["language"] == lang], "top1_hit")}
                        for lang in ("es", "pt")},
        "wall_clock_s": round(wall_s, 1), "seconds_per_case": round(wall_s / len(rows), 2),
        "usage": usage, "gpu": gpu_summary(gpu),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--parallel", type=int, default=4)
    args = ap.parse_args()
    settings = load_settings()
    cases = pick_cases(args.n)
    stop, samples = threading.Event(), []
    sampler = threading.Thread(target=gpu_sampler, args=(stop, samples), daemon=True)
    sampler.start()
    rows, llm, wall = run_llm(cases, args.parallel)
    stop.set()
    sampler.join(timeout=15)
    report = {"generated_by": "ml/probe_llm.py", "split": "val", "provider": settings.provider,
              "model": settings.model, "prompt_version": LLMPortRanker(llm).prompt_version, "parallel": args.parallel,
              "summary": summarize(rows, llm, wall, samples), "baselines_same_cases": baseline(cases), "rows": rows}
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    path = PROBE_DIR / f"{settings.provider}__{settings.model.replace(':', '_').replace('/', '_')}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report["summary"] | {"baselines_same_cases": report["baselines_same_cases"]}, indent=2,
                     default=str))
    print(f"written: {path}")


if __name__ == "__main__":
    main()
