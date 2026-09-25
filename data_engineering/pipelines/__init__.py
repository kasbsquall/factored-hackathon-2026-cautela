"""Bronze and silver pipeline over DuckDB. Entry point: `python -m data_engineering.pipelines.run`."""

import importlib.util
import sys

# The DuckDB Python client probes for pandas on many calls. pandas is not a dependency here, and a failed import
# is not cached by Python, so every probe walks sys.path again. Measured on the fixture run: 5,338 probes, about
# half of the wall time on Windows. Recording pandas as absent makes each probe a dictionary lookup. This only
# applies when pandas is not installed.
if "pandas" not in sys.modules and importlib.util.find_spec("pandas") is None:
    sys.modules["pandas"] = None  # type: ignore[assignment]
