# Cautela: one-command targets. Windows without make: run the commands after each target by hand.
SEED ?= 42
SOURCE ?= data/fixture
TARGET ?= data/warehouse.duckdb
TARGET_S3 ?= data/warehouse_s3.duckdb

.PHONY: fixture pipeline pipeline-s3 cases test all

fixture:  ## synthetic test fixture (team-generated, not organizer data)
	uv run python -m data_engineering.fixtures.generate --out data/fixture --seed $(SEED)

pipeline:  ## bronze and silver, incremental; SOURCE can be a local path or s3://bucket/prefix
	uv run python -m data_engineering.pipelines.run --source $(SOURCE) --target $(TARGET)

pipeline-s3:  ## real data: source and credentials come from .env (LATAM_BANK_S3_URI, AWS_*)
	uv run python -m data_engineering.pipelines.run --target $(TARGET_S3)

cases:  ## rebuild the held-out dispute cases and check them against the committed manifest
	uv run python -m ml.scenarios.build --verify

test:
	uv run pytest

all: fixture pipeline test

# Gold layer and workflow evidence. REPORT_WAREHOUSE must hold the organizer data: the report is committed.
REPORT_WAREHOUSE ?= $(TARGET_S3)
.PHONY: gold analytics

gold:  ## gold serving and analytics tables from silver, incremental; TARGET selects the warehouse
	uv run python -m data_engineering.gold.run --target $(TARGET)

analytics:  ## data_analytics/reports: why-this-workflow.md and chart JSON, from a warehouse with gold built
	uv run python -m data_analytics.run --warehouse $(REPORT_WAREHOUSE)

# Public demo on organizer data. Inputs are git-ignored: the full warehouse, the learned model (data/ml/models, from
# `uv run python -m ml.train`) and the seed. Outputs: deploy/demo-bundle/ (git-ignored) and the committed lock.
DEMO_WAREHOUSE ?= data/warehouse_real.duckdb
DEMO_SEED ?= data/demo/real_seed.json
.PHONY: demo-seed demo-artifacts release

demo-seed:  ## pick the demo customers per scenario and validate each with the orchestrator and the learned model
	uv run python -m deploy.demo_select --warehouse $(DEMO_WAREHOUSE) --out $(DEMO_SEED)

demo-artifacts:  ## deploy/demo-bundle/ and deploy/demo-bundle.lock.json (commit the lock afterwards)
	@test -f $(DEMO_SEED) || $(MAKE) demo-seed
	uv run python -m deploy.bundle --source $(DEMO_WAREHOUSE) --seed $(DEMO_SEED)

release:  ## release/cautela-<sha>.tar.gz (git archive HEAD) and release/cautela-<sha>-demo-bundle.tar.gz
	uv run python -m deploy.release

# End-to-end evaluation of the agent (eval/). Needs data/warehouse_real.duckdb with gold built and data/ml/models.
# `eval` and `eval-suite` make no network calls. `eval-llm` calls OpenAI gpt-6-luna under the USD 3.00 cap of
# eval/budget.py (key from the git-ignored .env) and adds the llm configuration to eval/results.json.
.PHONY: eval eval-suite eval-llm

eval-suite:  ## warehouse slice for the test customers, then rebuild the held-out suite and check it against the manifest
	uv run python -m eval.slice
	uv run python -m eval.build --verify

eval:  ## rules and learned configurations, compliant and attentive customer: eval/results.json (no LLM, no cost)
	@test -f eval/heldout/suite.jsonl || $(MAKE) eval-suite
	uv run python -m eval.run --configs rules,learned --attentive

eval-llm:  ## llm configuration plus two repeated runs on the variance subset (paid, capped)
	uv run python -m eval.run --configs llm --variance-runs 2 --workers 6

# After-fix rerun of the deployed agent on the same suite, into the committed eval/results_after_fix.json. Every
# configuration records the git commit it ran on. The LLM run has its own ledger and a hard USD 2.00 cap.
AFTER_FIX ?= eval/results_after_fix.json
.PHONY: eval-after-fix eval-after-fix-llm report

eval-after-fix:  ## rules, learned (deployed decision) and the label-rule baseline, no LLM, no cost
	uv run python -m eval.run --configs rules,learned,label_rule --out $(AFTER_FIX)

eval-after-fix-llm:  ## llm configuration (gpt-6-luna, reasoning effort none), USD 2.00 cap on its own ledger
	uv run python -m eval.run --configs llm --workers 6 --out $(AFTER_FIX) --cap-usd 2.00 --ledger data/eval/llm_spend_after_fix.json

report:  ## eval/report.md from eval/report_template.md, eval/results.json and eval/results_after_fix.json
	uv run python -m eval.report_tables

# Update and freshness test on organizer data (data_engineering/freshness/). Copies data_backup_20260831/ (earlier
# state) and data/ (current state) from the .env bucket into the git-ignored data/freshness/ (about 1.9 GB), loads
# one after the other into a scratch warehouse, checks it against a single load, and writes the committed report
# data_engineering/reports/freshness_backup_vs_current.md. About 30 minutes on a laptop.
.PHONY: freshness-demo freshness-status

freshness-demo:  ## update test: earlier delivery state, then the current one, into the same warehouse
	uv run python -m data_engineering.freshness.demo

freshness-status:  ## freshness of TARGET against the policy; exit code 1 when a table or gold is stale
	uv run python -m data_engineering.freshness.status --target $(TARGET)

# ML experiment tracking made readable without the git-ignored mlruns/: refreshes ml/reports/experiments_runs.json from
# the local MLflow store when there is one, then writes ml/reports/experiments.md (no network, no organizer data).
.PHONY: experiments

experiments:  ## ml/reports/experiments.md: every MLflow run behind a committed ML result, and the model-selection story
	uv run python -m ml.experiments_report

# The fitted disposition model is committed in ml/models with a sha256 lock; the service loads it when the local
# training output data/ml/models is absent. `model-lock` refreshes the copy after `uv run python -m ml.train`.
.PHONY: model-verify model-lock ml-baselines

model-verify:  ## check ml/models/*.pkl against ml/models/models.lock.json (exit code 1 on a mismatch)
	uv run python -m ml.model_lock

model-lock:  ## copy data/ml/models into ml/models and rewrite the lock (commit both afterwards)
	uv run python -m ml.model_lock --write

ml-baselines:  ## label rule on parsed text and the deployed decision on val and test -> ml/reports/baselines_*.json
	uv run python -m ml.evaluate_baselines
