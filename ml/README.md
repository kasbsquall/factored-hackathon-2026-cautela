# ml: which charge is the customer disputing?

This is the learned component of Cautela. A customer writes something like "me cobraron algo raro la semana pasada, como 500 pesos, en una tienda". Complaints carry no `transaction_id`, so the service has to decide which of the customer's own recent transactions they mean. It then does one of three things: act on that charge, ask the customer to pick from a short list (clarify), or stop and hand the case to a person (abstain).

The problem statement asks us to "evaluate at least one learned component against an appropriate baseline", with "valid labels or relevance judgments", leakage prevention, and justified "representations, metrics, thresholds, and evaluation splits". This folder is that evidence. Every number quoted here comes from `ml/reports/results.json` or `ml/reports/fitted.json`, which the code writes. `ml/reports/results.md` is generated from those files and holds the full tables.

## Run it

```bash
uv sync
uv run python -m ml.scenarios.build --verify   # rebuild eval/cases/disputes/*.jsonl and check the committed manifest
uv run python -m ml.scenarios.datasheet   # -> ml/DATASHEET.md
uv run python -m ml.train                 # ranker, calibrators, disposition model, thresholds (about 50 s)
uv run python -m ml.evaluate              # test split only -> ml/reports/results.json, MLflow runs
uv run python -m ml.evaluate --split test_fresh   # once -> ml/reports/results_fresh.json (refuses a second run)
uv run python -m ml.parser_readback       # amount and date read-back on val and test -> ml/reports/parser_readback.json
uv run python -m ml.report                # -> ml/reports/results.md
uv run pytest tests/ml_tests
uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db   # browse runs (mlruns/ is git-ignored)
```

The builder is seeded and does not depend on the order of its inputs. Two runs on the same files give byte-identical case files; the manifest records a `data_version` hash, and `ml.evaluate` refuses fitted artifacts from another data version. MLflow 3 puts the plain `./mlruns` file store in maintenance mode, so runs go to a SQLite database inside `./mlruns`. Every run logs the data version, parameters, thresholds and metrics.

### Rebuilding the case files

The case files (`train.jsonl`, `val.jsonl`, `test.jsonl`, `test_fresh.jsonl`, about 12 MB of organizer-derived rows) are git-ignored. Only `eval/cases/disputes/manifest.json` (sha256 of each file and the `data_version`) and `ml/DATASHEET.md` are committed. Rebuild them before training, evaluating or running `tests/ml_tests/test_committed_cases.py` (which skips when they are absent):

```bash
# from a local copy of the organizer files in data/raw
uv run python -m ml.scenarios.build --verify
# straight from the organizer bucket; credentials come from .env (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION)
uv run python -m ml.scenarios.build --verify --source s3://<bucket>/data
```

Without `--source`, the builder reads `data/raw` when it exists and otherwise `LATAM_BANK_S3_URI` from `.env` plus `/data`. With `--verify` it rebuilds in memory, compares the three sha256 values, the `data_version` and the `test_fresh` sha256 with the committed manifest, and writes the case files only when all of them match; on a mismatch it exits with status 1 and writes nothing. The manifest is never rewritten in that mode. We checked both routes: the local copy and the bucket both reproduce data version `0189e386ce7fe882`. A plain run without `--verify` rebuilds and rewrites the manifest, which is how a deliberate change to the generator is recorded. A `make cases` target would call the `--verify` command; the Makefile is outside this folder, so that line is left to whoever maintains it.

## Task framing

The work splits into two parts because they fail in different ways.

1. **Ranking.** Score each candidate transaction against the description. Metrics: top-1 accuracy, top-3 recall and MRR on cases with exactly one right answer.
2. **Disposition.** Decide whether the description fits one charge, several, or none. This is what the problem statement means by "clarify ambiguity" and "when it must abstain or transfer to a human", and it is where acting on the wrong charge happens.

On this data, ranking turned out to be the easy half. With a parser that reads the cues, the top candidate is right on about 99% of match cases for every system. The meaningful differences show up in the disposition. That result shaped the design: the learned component that earns its place is the case-level disposition model, not the pointwise ranker.

## Labels: why generated, and why they are valid

The supplied text cannot label this task. The transcripts and complaint descriptions are templated: 546 distinct transcripts over 171k rows, 42 distinct customer utterances, unfilled placeholders, and topic labels that do not match the text. Complaints also have no link to a transaction. Any label taken from them would be noise.

So the scenarios are built on **real organizer transactions** with **team-generated descriptions**, and the label is *valid by construction*. A description is rendered from structured hints about one transaction: approximate amount, relative date, merchant (exact, partial, misspelled or a noun), type, channel, city. The label is an explicit function of those hints and the candidate pool, with fixed tolerances. The rule is written in `ml/scenarios/hints.py` and `ml/DATASHEET.md`, and `tests/ml_tests/test_committed_cases.py` recomputes the label of every case from its stored hints. Relevance is judged by a rule anyone can audit, with no annotator in the loop.

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
| Fresh test split | `test_fresh`: another generation seed, customers of the test bucket that the original build never loaded, family F7 written after training; frozen by sha256 and evaluated once (next section) | `test_fresh_shares_no_customer_or_transaction_with_any_split`, `test_evaluation_of_test_fresh_is_one_shot_and_hash_checked` |
| Held-out template families | F5 (formal letter) and F6 (oral, regional slang, number words) exist only in test. The ranker lexicon and parser were written from F1 to F4 only and live in separate files from the generator vocabulary. Exception: the number-word and slang parser added later covers F6 amount vocabulary (see "Number words and amount slang") | `test_heldout_families_absent_from_train_and_val` |
| Portuguese stays with its source | Same split, customer, pool and label; shared bootstrap group | `test_portuguese_cases_share_split_and_content_with_source` |
| Rankers never see labels or hints | `ranker_input` exposes only text, report date and language | `test_ranker_input_hides_hints_and_labels` |
| Thresholds and calibrators never touch test | `Calibrator.fit` accepts only train records, `search_policy` only val records; the evaluation path is run with every fit function replaced by one that raises | `test_thresholds_refuse_non_val_records`, `test_evaluation_path_never_fits` |
| Out-of-fold calibration | The learned ranker's train scores used by the calibrator and the disposition model come from 5-fold GroupKFold by customer | `ml/train.py` |

Customers are sampled stratified by country and segment, with a per-stratum floor, so Student, Premium and Argentina have enough cases to report. The datasheet lists counts by label, language, country, segment, family, information level and pool size.

## Representations

The description is parsed deterministically into cues (`ml/features/parse.py`): an amount with regional number formats, Spanish and Portuguese number words and regional amount slang (next section), a currency word, a date range from relative phrases, and type, channel and merchant-noun keywords. There is also a small typo corrector for cue words; number and slang words are kept out of it. Each candidate then gets 29 features comparing it with those cues: log amount ratio, days outside the date range, fuzzy token similarity to the merchant name, an IDF-weighted merchant similarity, whether the description names a known merchant and whether this candidate contradicts it, noun-to-merchant match, type and channel agreement, debit or credit, and rank of each proximity within the pool (`ml/features/pairwise.py`).

Two merchant fixes came out of the previous error analysis. First, a token shared by several merchants counts less: similarity is weighted by 1 over the number of merchants in the organizer merchant list (`MERCHANT_DIRECTORY` in `lexicon.py`, 24 names) that contain the token, so "central" no longer ties "Laboratorio Central" with "Mercado Central". Second, when the description names a merchant from that list (IDF-weighted similarity of at least 0.85) and the candidate is a different merchant, the candidate gets `merch_mismatch = 1`: the rules ranker drops its merchant credit to zero, and the disposition model stops counting the merchant cue as satisfied. "Uber" against a "Taxi Seguro" charge is the motivating case.

We did not use embeddings or TF-IDF over the text. The decisive information is numbers, dates and short proper names, and an embedding represents "como 500" versus 467.08, or "la semana pasada" relative to a report date, poorly. A parser also fails visibly: `results.md` has parser read-back tables per language and family. The trade-off is that the parser does not cover phrasing it has never seen, and the held-out families measure exactly that.

### Number words and amount slang

`ml/features/numbers.py` rewrites number words as digits before the amount regex runs ("mil quinientos" to 1500, "dos millones" to 2000000, "trezentos e cinquenta" to 350). A run of number words is converted only when its value is at least 100 or a money word follows, so "dos semanas" and "un cargo" are left alone, and a scale word right after digits keeps its meaning ("36 mil" stays 36000). Slang meanings and their provenance:

| Word | Region | Reading | Provenance |
|---|---|---|---|
| luca, lucas | AR, CO | 1,000 pesos per unit ("20 lucas" = 20,000 pesos) | ASALE, *Diccionario de americanismos*, entry "luca" (https://www.asale.org/damer/luca). We located the entry by web search; its text could not be retrieved from this environment, so the gloss should be checked |
| barra, barras | CO | 1,000 pesos per unit | team assumption |
| palo, palos | CO, AR | 1,000,000 per unit; no currency implied, since AR usage can mean dollars | team assumption |
| gamba, gambas | AR | 100 pesos per unit | team assumption |
| varo, varos, baro, baros | MX | peso as the unit of money ("500 varos" = 500 pesos) | team assumption |
| conto, contos | BR | one unit of money ("cinquenta conto" = 50); no currency implied | team assumption |
| medio (before palo, luca or millón) | all | one half of the unit | team assumption |

Each mapping has a unit test in `tests/ml_tests/test_numbers.py`, and a test checks that every slang entry carries a provenance string. "verdes", "pila" and "el finde" are deliberately not supported.

Protocol: the change was checked on val, and test read-back was measured once afterwards with `ml/parser_readback.py`; no mapping was chosen or changed from test numbers. Val read-back was already complete and stayed so (Spanish amounts 419 of 419 before and after, Portuguese 105 of 105). On test, Spanish held-out amounts went from 152 of 230 to 209 of 230, all of it in F6 (65 of 149 to 122 of 149); F5 stayed at 144 of 144 and Portuguese held-out at 57 of 63. Dates did not change (F5 30 of 125, F6 110 of 138). **Caveat:** F6 is the slang and number-word family, and the parser now knows vocabulary the generator uses for it. F6 is therefore no longer held out for amount vocabulary, and the F6 amount gain is not evidence of generalization. The F5 and F6 date phrasings and the unsupported slang above remain unseen.

## Components compared (same test set, same cases)

| System | Ranker | Decision | Fitted on |
|---|---|---|---|
| `rules_fixed` | hand-weighted rules | the fixed clarify rule from `agent/tools/ranking.py` (top < 0.60 or margin < 0.15, never abstains) | nothing |
| `rules_tuned` (**baseline**) | hand-weighted rules | calibrated confidence + two thresholds | calibrator on train, thresholds on val |
| `learned_ranker_calibrated` (ablation) | learned pointwise ranker | same as baseline | ranker on train, model chosen on val |
| `learned_ranker_disposition` (**proposed**) | learned pointwise ranker | learned disposition model: P(match), P(no_match) | ranker and disposition on train, model family and thresholds on val |

* **Learned ranker**: a binary classifier over candidate features, trained on match cases (the target is positive) and no_match cases (all negative). Ambiguous cases are left out because their label does not say which candidate is right. Logistic regression and gradient boosting were compared. Selection was by val MRR, with val log loss as the tie-break, because every candidate reached a val MRR of 1.0. The selected model is recorded in `fitted.json`.
* **Disposition model**: a three-class classifier over case-level features: the ranker's score profile, which cues were parsed, how many candidates satisfy every parsed cue, and how many fail exactly one. In this run multinomial logistic regression beat gradient boosting (depth 3) on val log loss (0.0481 against 0.0541); in the previous run the order was reversed (0.0757 against 0.0777), so either is a defensible choice.
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

Both thresholds are chosen on val only. The search maximizes the correct decision rate subject to an unsafe rate of at most 1% on val. The cap is a policy choice: in dispute intake, acting on the wrong charge costs more than one extra question. The chosen values are recorded in `fitted.json`. A threshold met on validation is not a guarantee: in the previous run the cap held on val but not on test (17 of 1,107 unsafe). In this run the proposed system has 0.0% unsafe on val and 3 of 1,107 on test (0.3%, 95% interval 0.0% to 0.6%), but 1,107 cases still cannot pin down a rate that low.

**Business floor on acting.** On top of the val-tuned act threshold there is a minimum confidence for acting, `DEFAULT_ACT_FLOOR = 0.60` in `ml/decision.py`. The system acts only when its confidence reaches max(val threshold, floor). The floor is a policy set by the business and was not fitted: 0.60 is the same bar the agent's fixed rule already uses for the top score (`agent/tools/ranking.py`), and it was fixed before any result of this run was seen. It can be changed with `CAUTELA_ACT_FLOOR` or `ml.train --act-floor`. The reason for it is the previous run, where the val search put the disposition model's act threshold at P(match) >= 0.28 and four unsafe test acts had confidence between 0.34 and 0.50. In this run the val search chose 0.635 (rules baseline), 0.91 (calibrated learned ranker) and 0.79 (disposition model), all above the floor, so the floor does not bind: unsafe outcomes are 21, 20 and 3 of 1,107 with and without it, and safe automated resolution is unchanged (paired difference 0.0 points for every system). Its cost is zero here, and it caps the damage if a refit lands on a low threshold again. `results.md` has the ablation table.

## Headline results: fresh test split (evaluated once)

The original test split was used for error analysis, so its numbers are optimistic. `test_fresh` (`ml/scenarios/fresh.py`) is the clean estimate: 1,240 cases (900 Spanish sources, 340 Portuguese renderings), another generation seed (20261001), customers from the original test bucket that the original build never loaded (no customer or transaction shared with train, val or test, tested), and a new phrasing family F7 on 35% of the Spanish sources. F7 is a messaging-app register with Mexican, Colombian and Argentine variants and a pt-BR rendering, written after training and before any model ran on it (`ml/scenarios/render_fresh.py`). The report-date window is the same as test (2026-01-01 to 2026-06-17) because the organizer transactions end on 2026-06-18, so a later window does not exist. The file was frozen by sha256 in the manifest, and the already-trained systems were evaluated on it once: no refit, no threshold change, no fix afterwards. `ml.evaluate --split test_fresh` refuses to run a second time.

| System (test_fresh, 1,240 cases) | Correct decisions | Unsafe (of 1,240; acted) | Safe automated resolution | Top-1 on match |
|---|---|---|---|---|
| `rules_fixed` | 69.6% [66.2, 72.9] | 55 (4.4%; 706 acted) | 52.5% | 96.5% |
| `rules_tuned` (baseline) | 82.2% [79.5, 84.8] | 24 (1.9%; 617 acted) | 47.8% | 96.5% |
| `learned_ranker_calibrated` | 76.5% [73.9, 79.2] | 19 (1.5%; 600 acted) | 46.9% | 98.3% |
| `learned_ranker_disposition` (proposed) | **89.8%** [87.9, 91.8] | **4 (0.3%)** [0.1, 0.7]; 666 acted | 53.4% [50.2, 56.8] | 98.3% |

Paired against the tuned baseline on the same cases, the proposed system gains +7.7 points of correct decisions [+5.7, +9.6], lowers unsafe outcomes by 1.6 points [-2.4, -0.8] and raises safe automated resolution by 5.6 points [+3.8, +7.3]. On held-out families (F5, F6, F7) the gain is +6.7 [+4.3, +9.3]; on seen families +9.1 [+5.8, +12.4].

| Group (test_fresh) | Unsafe, baseline | Unsafe, proposed | Correct, baseline / proposed | Correct diff [95% CI] | Unsafe diff [95% CI] |
|---|---|---|---|---|---|
| Spanish | 16 / 900 | 1 / 900 | 82.9% / 90.2% | +7.3 [+5.4, +9.2] | -1.7 [-2.6, -0.9] |
| Portuguese | 8 / 340 | 3 / 340 | 80.3% / 88.8% | +8.5 [+5.6, +11.5] | -1.5 [-2.9, -0.3] |
| Argentina | 3 / 306 | 0 / 306 | 82.0% / 89.2% | +7.2 [+3.4, +11.2] | -1.0 [-2.3, +0.0] |
| Colombia | 9 / 391 | 2 / 391 | 84.1% / 90.0% | +5.9 [+3.0, +9.1] | -1.8 [-3.4, -0.5] |
| Mexico | 12 / 543 | 2 / 543 | 80.8% / 90.1% | +9.2 [+6.3, +12.4] | -1.8 [-3.4, -0.5] |
| F7 (new family) | 10 / 422 | 3 / 422 | 74.6% / 79.9% | +5.2 [+2.6, +8.4] | -1.7 [-3.2, -0.5] |

What the fresh split changes. Every system makes fewer correct decisions than on the original test: the proposed system goes from 93.9% to 89.8% correct and from 56.9% to 53.4% safe automated resolution; unsafe stays low (3 of 1,107 before, 4 of 1,240 now). The baseline drops more (86.8% to 82.2%), so the paired gain is about the same (+7.0 before, +7.7 now). F7 is the weakest family by a wide margin: 79.9% correct against 84.8% on held-out families overall and 97.0% on seen ones, and the safe-automation gain there is only +1.9 points [+0.2, +3.9]. The parser reads back 340 of 347 held-out Spanish amounts but only 146 of 307 held-out Spanish dates (68 of 122 in Portuguese), so the new date wording is the likely weak point; that is a hypothesis from the read-back table, not a diagnosed cause, and nothing was changed after seeing it. Three of the four unsafe outcomes are Portuguese, and three of the four are F7 descriptions without an amount (for example "uma movimentação num terminal de autoatendimento"), acted on at P(match) 0.96 to 0.99; the fourth is an F5 letter with a full date. The Portuguese interval for unsafe outcomes is wide (340 cases). Full tables: the headline section of `results.md`.

## Results on the original test split (used for error analysis)

This run includes the number-word and slang parser, the merchant fixes and the act floor. The previous run's numbers are kept in `ml/reports/previous/` and in the "Previous run" section of `results.md`. **These numbers are optimistic:** the merchant fixes and the slang mappings were prompted by error analysis of the previous run on this same test split, so part of the improvement on these cases is not an independent estimate. The fresh split above is the independent one.

| System (test, 1,107 cases) | Correct decisions | Unsafe | Safe automated resolution | Top-1 on match |
|---|---|---|---|---|
| `rules_fixed` | 71.9% [68.9, 74.9] | 45 (4.1%) | 56.0% | 99.1% |
| `rules_tuned` (baseline) | 86.8% [84.6, 89.0] | 21 (1.9%) | 50.3% | 99.1% |
| `learned_ranker_calibrated` | 79.4% [76.7, 81.9] | 20 (1.8%) | 49.6% | 99.9% |
| `learned_ranker_disposition` (proposed) | **93.9%** [92.0, 95.5] | **3 (0.3%)** [0.0, 0.6] | 56.9% [53.8, 60.2] | 99.9% |

Paired against the tuned baseline on the same cases, the proposed system gains +7.0 points of correct decisions [+4.9, +9.3], +5.2 [+1.4, +9.3] on held-out families alone. Unsafe outcomes fall by 1.6 points [-2.5, -0.9]; in the previous run that difference was -0.6 [-1.5, +0.1] and included zero, so it is now a measured safety improvement on this split, with the caveat above. The baseline also improved (82.2% to 86.8%), because it shares the parser and merchant features, which is why the correct-decision gap narrowed from +10.0 to +7.0 points. Top-1 is 99.9% against 99.1% (+0.7 points [+0.1, +1.5]); ranking remains nearly saturated. The calibrated learned ranker (ablation) got worse on safety, from 9 to 20 unsafe of 1,107; we have not diagnosed why.

Safe automated resolution is 56.9% of all cases for the proposed system against 50.3% for the baseline. Unnecessary transfers are 49 against 80 of 883 in-scope cases, and missed transfers 3 against 11 of 224 no-match cases (2 acted on, 1 answered with a clarifying question).

Val versus test: the proposed system scores 98.0% correct on val, 93.9% on test, 96.2% on test's seen families and 90.1% on held-out ones. By family, F5 (formal letter) is now the weakest at 88.9% and F6 (slang and number words) is at 91.1%, up from 76.3%; the F6 gain partly reflects the parser now knowing F6 amount vocabulary. On held-out families Argentina and Mexico trail Colombia (88.1%, 88.5% and 94.3%); on seen families all three are between 94.9% and 97.6%. Segments range from 90.8% (Premium, 131 cases) to 94.8% (Basic, 592).

**Country disparity.** Against the baseline, the proposed system improves correct decisions in every country (AR +6.0 points [+0.8, +11.5], CO +8.7 [+4.9, +12.7], MX +6.4 [+3.4, +9.6]) and lowers unsafe outcomes in every country (AR -2.6 [-5.4, -0.7], CO -2.0 [-3.5, -0.6], MX -0.8 [-1.7, -0.2]). The gain in safe automated resolution is uneven: +8.4 points in Colombia and +7.0 in Mexico, but +3.4 [-0.8, +7.9] in Argentina, where the interval includes zero. The spread across countries in safe automated resolution therefore grows from 1.4 points for the baseline to 6.4 for the proposed system (Argentina 53.0%, Colombia 59.4%, Mexico 57.2%), while the spread in unsafe rate shrinks from 1.8 to 0.4 points. Argentina gets the safety gain but less of the automation gain; the full table with intervals is in `results.md`.

## Error analysis

The cases below were found in the previous run's `results.json` (proposed system); the status after this run's changes was checked by re-running the proposed system on each case.

* **An unread cue is silently dropped** (`test-00023-es`, F6: "me cayó un cargo el mes pasado en la tlapalería"; `test-00098-es`, F6: "como cuatrocientos verdes"). The parser does not know the slang noun, so the description shrinks to the cues it did read, one transaction fits those, and the system acts on a no_match case. `test-00098-es` now abstains correctly (the number word is read). `test-00023-es` is still acted on at P(match) 0.992 and is one of the 3 unsafe outcomes. The fix we would try next: count content words the parser could not map, and refuse to act when a description carries unexplained cues.
* **Shared merchant tokens** (`test-00099-es`: "Laboratorio Central" and "Mercado Central" scored 0.9966 and 0.9972). With the IDF-weighted similarity the system no longer acts on it; it now asks the customer to choose (P(match) 0.172), which is safe but not the correct decision for a match case.
* **Amount outweighs merchant** (`test-00124-es`: "casi 880 mil pesos en Uber" was acted on a Taxi Seguro charge of 937,244 COP). With the mismatch feature it now abstains correctly.
* **Acting at middle confidence** (`test-00064-es`, `test-00068-es`: no_match cases acted on at P(match) 0.346 and 0.358). Both now abstain correctly; the act threshold is 0.79 in this run, and the 0.60 floor would stop this even with a low threshold.
* **Held-out date and amount formats** (`test-00185-es`: "el pasado 15 de diciembre de 2025"; `test-00242-es`: "8 palos"). "8 palos" is now read and acted on correctly. The full date with a year is still missed and the case is clarified, which is the safe failure.
* **Remaining unsafe outcomes** in this run: `test-00023-es` above; an ambiguous F2 case ("vi un cargo en un cajero") acted on at 0.990; and a formal F5 no_match letter ("USD 353 realizado el pasado 23 de abril de 2026") acted on at 0.973. The last one is another full-date phrasing the parser does not read.
* **Label tolerance** (`test-00011-es`: "alrededor de 250 dólares" with purchases of 221.84 and 243.09). The rule labels this ambiguous because both are within a factor of 1.25. It was acted on in the previous run and is now clarified. The tolerance is a team choice, and moving it would move cases between labels.

## Limitations

* The labels are generated, with no human judgment. The held-out families reduce, but do not remove, the risk of learning the generator, and no human-written complaint was available to validate against.
* Pools are small: organizer customers have about one transaction a month, and 90-day pools have a median of 4 candidates. Ranking would be harder with real transaction volumes, and the pool-size breakdown is the only view of that.
* Portuguese is a template rendering, not reviewed by native speakers, and Brazil is absent from the data.
* The organizer data combines type, channel and merchant at random (for example withdrawals "por la app"), and Mexican customers transact mostly in USD, so some descriptions read oddly.
* The LLM ranker was not run. Its latency, cost, variability and quality are unknown. Masking with the agent's PII module also masks large peso amounts written like an Argentine DNI ("16.371.485"): privacy wins over amount information there, and that trade-off should be measured when the LLM runs.
* Case files include organizer-derived transaction fields (ids, amounts, merchants, dates) with pseudonymized customer references, about 9 MB in total. They are not committed; `ml.scenarios.build --verify` rebuilds them from the organizer files and checks them against the committed manifest (see "Rebuilding the case files").
* Changes made after reading test errors (merchant features, slang mappings) make the original test numbers optimistic; `test_fresh` measures that (93.9% to 89.8% correct for the proposed system). The fresh split shares the test report-date window, because the organizer data has no later transactions, so it is fresh in customers, seed and phrasing but not in time. It is now spent as well: any change made after reading its results needs another fresh split to be measured.

## Files

| Path | Purpose |
|---|---|
| `scenarios/build.py`, `hints.py`, `render.py`, `vocab.py`, `source.py` | Scenario builder, label rule, templates, generator vocabulary, DuckDB reader |
| `scenarios/fresh.py`, `render_fresh.py` | Fresh test split builder and the F7 family written for it |
| `scenarios/datasheet.py` | Writes `DATASHEET.md` from the case files |
| `features/parse.py`, `numbers.py`, `lexicon.py`, `pairwise.py` | Parser, number words and slang, ranker-side lexicon and merchant list, candidate features |
| `rankers/protocol.py`, `rules.py`, `learned.py`, `llm.py`, `prompts/rank_v1.md` | Rankers and the shared interface |
| `decision.py`, `disposition.py` | Deciders, threshold search and the business act floor |
| `train.py`, `evaluate.py`, `analysis.py`, `metrics.py`, `report.py`, `report_changes.py`, `report_fresh.py`, `tracking.py` | Fitting, test evaluation, aggregation, metric functions, report, MLflow |
| `parser_readback.py` | Amount and date read-back on val and test |
| `reports/fitted.json`, `results_fresh.json`, `results.json`, `results.md`, `parser_readback*.json` | Committed outputs; every reported number comes from these |
| `reports/previous/` | Snapshot of the previous run's `results.json` and `fitted.json` |
| `../eval/cases/disputes/` | Committed manifest; the git-ignored case files are rebuilt there |
| `../tests/ml_tests/` | Tests |
