# ml: which charge is the customer disputing?

This is the learned component of Cautela. A customer writes something like "me cobraron algo raro la semana pasada, como 500 pesos, en una tienda". Complaints carry no `transaction_id`, so the service has to decide which of the customer's own recent transactions they mean. It then does one of three things: act on that charge, ask the customer to pick from a short list (clarify), or stop and hand the case to a person (abstain).

The problem statement asks us to "evaluate at least one learned component against an appropriate baseline", with "valid labels or relevance judgments", leakage prevention, and justified "representations, metrics, thresholds, and evaluation splits". This folder is that evidence. Every number quoted here comes from `ml/reports/results.json` or `ml/reports/fitted.json`, which the code writes. `ml/reports/results.md` is generated from those files and holds the full tables.

## Run it

```bash
uv sync
uv run python -m ml.scenarios.build       # real transactions in data/raw -> eval/cases/disputes/*.jsonl (about 25 s)
uv run python -m ml.scenarios.datasheet   # -> ml/DATASHEET.md
uv run python -m ml.train                 # ranker, calibrators, disposition model, thresholds (about 35 s)
uv run python -m ml.evaluate              # test split only -> ml/reports/results.json, MLflow runs
uv run python -m ml.report                # -> ml/reports/results.md
uv run pytest tests/ml_tests
uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db   # browse runs (mlruns/ is git-ignored)
```

The builder is seeded and does not depend on the order of its inputs. Two runs on the same files give byte-identical case files; the manifest records a `data_version` hash, and `ml.evaluate` refuses fitted artifacts from another data version. MLflow 3 puts the plain `./mlruns` file store in maintenance mode, so runs go to a SQLite database inside `./mlruns`. Every run logs the data version, parameters, thresholds and metrics.

## Task framing

The work splits into two parts because they fail in different ways.

1. **Ranking.** Score each candidate transaction against the description. Metrics: top-1 accuracy, top-3 recall and MRR on cases with exactly one right answer.
2. **Disposition.** Decide whether the description fits one charge, several, or none. This is what the problem statement means by "clarify ambiguity" and "when it must abstain or transfer to a human", and it is where acting on the wrong charge happens.

On this data, ranking turned out to be the easy half. With a parser that reads the cues, the top candidate is right on about 99% of match cases for every system. The meaningful differences show up in the disposition. That result shaped the design: the learned component that earns its place is the case-level disposition model, not the pointwise ranker.

## Labels: why generated, and why they are valid

The supplied text cannot label this task. The transcripts and complaint descriptions are templated: 546 distinct transcripts over 171k rows, 42 distinct customer utterances, unfilled placeholders, and topic labels that do not match the text. Complaints also have no link to a transaction. Any label taken from them would be noise.

So the scenarios are built on **real organizer transactions** with **team-generated descriptions**, and the label is *valid by construction*. A description is rendered from structured hints about one transaction: approximate amount, relative date, merchant (exact, partial, misspelled or a noun), type, channel, city. The label is an explicit function of those hints and the candidate pool, with fixed tolerances. The rule is written in `ml/scenarios/hints.py` and `ml/DATASHEET.md`, and `tests/ml_tests/test_committed_cases.py` recomputes the label of every committed case from its stored hints. Relevance is judged by a rule anyone can audit, with no annotator in the loop.

The labels cover three situations:

* `match`: exactly one candidate fits. This includes *recall-error* cases, where the customer misremembers one detail (the amount off by 35 to 80 percent, the date by 6 to 12 days, or the wrong channel) and still only one candidate fits every other cue.
* `ambiguous`: two or more candidates fit. The right move is to clarify.
* `no_match`: nothing fits. The hints describe a perturbed copy of one of the customer's charges, or another customer's transaction. The right move is to abstain.

The known risk is that **a model can learn the generator**. A system that mirrors the labeling rule will look very good on phrasing it has seen. The mitigations are listed in the next section, and results are always reported separately for seen and held-out phrasing.

Portuguese (pt-BR) cases are **TEAM-GENERATED** renderings of the same hints. Brazil is not in the customer base and no supplied text is in Portuguese.

## Splits and leakage prevention

| Control | How | Test |
|---|---|---|
| Group split by customer | Salted hash, 60/20/20; no customer or transaction in two splits | `test_group_split_has_no_customer_or_transaction_overlap` |
| Time split | Report dates: train until 2025-06-30, val 2025-07 to 2025-12, test 2026-01 to 2026-06 | `test_time_split` |
| Held-out template families | F5 (formal letter) and F6 (oral, regional slang, number words) exist only in test. The ranker lexicon and parser were written from F1 to F4 only and live in separate files from the generator vocabulary | `test_heldout_families_absent_from_train_and_val` |
| Portuguese stays with its source | Same split, customer, pool and label; shared bootstrap group | `test_portuguese_cases_share_split_and_content_with_source` |
| Rankers never see labels or hints | `ranker_input` exposes only text, report date and language | `test_ranker_input_hides_hints_and_labels` |
| Thresholds and calibrators never touch test | `Calibrator.fit` accepts only train records, `search_policy` only val records; the evaluation path is run with every fit function replaced by one that raises | `test_thresholds_refuse_non_val_records`, `test_evaluation_path_never_fits` |
| Out-of-fold calibration | The learned ranker's train scores used by the calibrator and the disposition model come from 5-fold GroupKFold by customer | `ml/train.py` |

Customers are sampled stratified by country and segment, with a per-stratum floor, so Student, Premium and Argentina have enough cases to report. The datasheet lists counts by label, language, country, segment, family, information level and pool size.

## Representations

The description is parsed deterministically into cues (`ml/features/parse.py`): an amount with regional number formats and "mil" or "millones", a currency word, a date range from relative phrases, and type, channel and merchant-noun keywords. There is also a small typo corrector for cue words. Each candidate then gets 26 features comparing it with those cues: log amount ratio, days outside the date range, fuzzy token similarity to the merchant name, noun-to-merchant match, type and channel agreement, debit or credit, and rank of each proximity within the pool (`ml/features/pairwise.py`).

We did not use embeddings or TF-IDF. The decisive information is numbers, dates and short proper names, and an embedding represents "como 500" versus 467.08, or "la semana pasada" relative to a report date, poorly. A parser also fails visibly: `results.md` has a parser cue-recovery table per language and family. The trade-off is that the parser does not cover phrasing it has never seen. The held-out families measure exactly that. On test, the amount is read back in 152 of 230 Spanish held-out descriptions versus 399 of 399 seen ones.

## Components compared (same test set, same cases)

| System | Ranker | Decision | Fitted on |
|---|---|---|---|
| `rules_fixed` | hand-weighted rules | the fixed clarify rule from `agent/tools/ranking.py` (top < 0.60 or margin < 0.15, never abstains) | nothing |
| `rules_tuned` (**baseline**) | hand-weighted rules | calibrated confidence + two thresholds | calibrator on train, thresholds on val |
| `learned_ranker_calibrated` (ablation) | learned pointwise ranker | same as baseline | ranker on train, model chosen on val |
| `learned_ranker_disposition` (**proposed**) | learned pointwise ranker | learned disposition model: P(match), P(no_match) | ranker and disposition on train, model family and thresholds on val |

* **Learned ranker**: a binary classifier over candidate features, trained on match cases (the target is positive) and no_match cases (all negative). Ambiguous cases are left out because their label does not say which candidate is right. Logistic regression and gradient boosting were compared. Selection was by val MRR, with val log loss as the tie-break, because every candidate reached a val MRR of 1.0. The selected model is recorded in `fitted.json`.
* **Disposition model**: a multinomial classifier over case-level features: the ranker's score profile, which cues were parsed, how many candidates satisfy every parsed cue, and how many fail exactly one. Multinomial logistic regression beat gradient boosting on val log loss.
* **LLM ranker** (`ml/rankers/llm.py`, prompt `ml/rankers/prompts/rank_v1.md`): optional. It is **not run** in the committed results because no `ANTHROPIC_API_KEY` was configured. When a key is present it runs on a stratified subset with its own train calibration and val thresholds, three repeated runs on test, and cost from API token counts. The default model is `claude-sonnet-5`, overridable with `CAUTELA_RANKER_MODEL`. Before any request, the description is masked with `agent.security.pii.mask_text` (the ranker refuses to run if that module is missing), and candidates are reduced to date, amount, currency, type, channel, merchant and city, with ids replaced by labels C1..Cn. The output is constrained by a JSON schema, and the prompt tells the model to treat the description as data.

All rankers implement the `CandidateRanker` shape of `agent/tools/ranking.py`: `rank(features, candidates) -> [(transaction_id, score in [0, 1])]`, best first. `ml/rankers/protocol.py` also accepts the agent's `DescriptionFeatures` object, where structured values override the parser. A test checks compatibility against the agent's protocol when that module can be imported.

## Metrics and why

The metrics follow the outcome definitions in the problem statement's "Evaluation evidence" section:

* **Correct decision rate**: act on the target for `match`, clarify with the target listed for `ambiguous`, abstain for `no_match`.
* **Unsafe outcomes**, given as count over all cases and over acted cases: acting on the wrong charge, or acting at all when the case was ambiguous or had no match.
* **Safe automated resolution** is a correct act with no clarifying turn, over all test cases, together with the share of cases where automation was attempted. **Containment** is reported separately, and it does not show the problem was solved: `rules_fixed` contains 100% of cases because it never hands off.
* **Escalation quality**: missed transfers (no_match cases not handed off) and unnecessary transfers (match or ambiguous cases handed off).
* **Ranking quality**: top-1 accuracy, top-3 recall and MRR on match cases. Top-3 recall is also given on pools of four or more candidates, since a pool of three makes it trivially 1.
* **Calibration**: ECE of the act confidence against "acting would be correct", over all cases.
* **Latency**: p50 and p95 per case, in process. **Cost**: API tokens times list price for the LLM. The local rankers have no metered cost.

Every rate has a 95% bootstrap interval, with 1,000 resamples of Spanish source groups so that a case and its Portuguese twin move together. The comparison with the baseline is a paired bootstrap on the same cases. Breakdowns cover language, country, segment, seen or held-out family, family, pool size, information level, and country by family.

## Thresholds

Both thresholds are chosen on val only. The search maximizes the correct decision rate subject to an unsafe rate of at most 1% on val. The cap is a policy choice: in dispute intake, acting on the wrong charge costs more than one extra question. The chosen values are recorded in `fitted.json`. The cap holds on val (0.66% unsafe for the proposed system) but not on test (15 of 1,107, 1.4%, 95% interval 0.6% to 2.2%). The test split contains held-out phrasing that val does not, and 1,107 cases cannot establish a rate that low. A threshold met on validation is not a guarantee.

## Results (offline, test split, data version in results.json)

The proposed system gets **92.0%** correct decisions [90.1%, 93.6%], against **82.8%** [80.1%, 85.2%] for the tuned rules baseline. The paired difference is +9.2 points [+6.7, +11.6]. On held-out families alone it is +8.4 points [+5.0, +11.7], so the gain does not come only from phrasing the parser was written for. Unsafe outcomes are 15 of 1,107 for the proposed system and 17 of 1,107 for the baseline, a difference of -0.2 points [-1.1, +0.7]: **no measurable safety improvement**. The fixed agent rule has 40 of 1,107 unsafe. Top-1 on match cases is 98.8% versus 98.7%, so the learned ranker adds nothing to ranking. The learned ranker with a calibrated threshold (the ablation) matches the baseline, so the gain comes from the disposition model.

Safe automated resolution is 56.3% of all cases for the proposed system versus 46.2% for the baseline. Unnecessary transfers fall from 137 to 24 of 888 in-scope cases, and missed transfers rise from 11 to 36 of 219 no-match cases. That trade has to be read with the safety cost in mind: 12 of those 36 were acted on, and 24 were answered with a clarifying question.

Val versus test: the proposed system scores 98.1% correct on val and 92.0% on test, and 96.6% on test's seen families. Most of the drop comes from unseen phrasing. By family, F6 (slang and number words) is the weakest at 78.8%. By country, Argentina and Colombia trail Mexico only on held-out families (79.5%, 79.5% and 91.3%); on seen families all three are between 96% and 97%. The disparity follows regional slang and large peso amounts written in slang ("lucas", "palos", "barras"), not the customer's country as such. Segments range from 90.6% (Basic) to 94.5% (Plus), with overlapping sample sizes of 115 to 593.

## Error analysis

These cases come from `results.json` (proposed system). `results.md` lists them with their top three candidates.

* **An unread cue is silently dropped** (`test-00023-es`, F6: "me cayó un cargo el mes pasado en la tlapalería"). The parser does not know the slang noun, so the description reduces to "last month". One purchase fits, and the system acts with high confidence on a no_match case. A fix: count content words the parser could not map, and refuse to act when a description carries unexplained cues.
* **Shared merchant tokens** (`test-00099-es`: "Laboratorio Central" versus "Mercado Central" get the same score). The merchant feature takes the best single-token match. Whole-name similarity should carry more weight.
* **Amount outweighs merchant** (`test-00123-es`: "casi 880 mil pesos en Uber" acted on a Taxi Seguro charge of 937,244 COP; `test-00328-es`: "en un súper" acted on a payment with no merchant). The learned ranker learned that amounts are reliable. When a merchant is stated and does not match, the score should drop harder.
* **Held-out date and amount formats** (`test-00183-es` and its twin `test-00183-pt`: "el pasado 15 de diciembre de 2025", "15/12/2025"; `test-00240-es`: "8 palos"). The parser misses them. The system clarified or handed off, which is the safe failure.
* **Label tolerance** (`test-00011-es`: "alrededor de 250 dólares" with candidates of 221.84 and 243.09). The rule labels this ambiguous because both are within a factor of 1.25. The system acted on the closer one. Some reviewers would accept that. The tolerance is a team choice, and moving it would move cases between labels.

## Limitations

* The labels are generated, with no human judgment. The held-out families reduce, but do not remove, the risk of learning the generator, and no human-written complaint was available to validate against.
* Pools are small: organizer customers have about one transaction a month, and 90-day pools have a median of 4 candidates. Ranking would be harder with real transaction volumes, and the pool-size breakdown is the only view of that.
* Portuguese is a template rendering, not reviewed by native speakers, and Brazil is absent from the data.
* The organizer data combines type, channel and merchant at random (for example withdrawals "por la app"), and Mexican customers transact mostly in USD, so some descriptions read oddly.
* The LLM ranker was not run. Its latency, cost, variability and quality are unknown. Masking with the agent's PII module also masks large peso amounts written like an Argentine DNI ("16.371.485"): privacy wins over amount information there, and that trade-off should be measured when the LLM runs.
* Case files include organizer-derived transaction fields (ids, amounts, merchants, dates) with pseudonymized customer references, about 9 MB in total. They are committed so that results can be reproduced without the raw data.

## Files

| Path | Purpose |
|---|---|
| `scenarios/build.py`, `hints.py`, `render.py`, `vocab.py`, `source.py` | Scenario builder, label rule, templates, generator vocabulary, DuckDB reader |
| `scenarios/datasheet.py` | Writes `DATASHEET.md` from the case files |
| `features/parse.py`, `lexicon.py`, `pairwise.py` | Parser, ranker-side lexicon (seen families only), candidate features |
| `rankers/protocol.py`, `rules.py`, `learned.py`, `llm.py`, `prompts/rank_v1.md` | Rankers and the shared interface |
| `decision.py`, `disposition.py` | Deciders and threshold search |
| `train.py`, `evaluate.py`, `analysis.py`, `metrics.py`, `report.py`, `tracking.py` | Fitting, test evaluation, aggregation, metric functions, report, MLflow |
| `reports/fitted.json`, `results.json`, `results.md` | Committed outputs; every reported number comes from these |
| `../eval/cases/disputes/` | Case files and manifest |
| `../tests/ml_tests/` | Tests |
