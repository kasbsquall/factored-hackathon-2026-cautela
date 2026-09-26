"""Gold layer: serving tables for the agent tools and analytics tables for the workflow evidence.

Every gold table has a YAML contract in `contracts/` and a SELECT in `sql/`. `build.py` materializes them from
silver incrementally, `checks.py` validates them, and `run.py` is the CLI (`make gold`).
"""
