"""IBM DataStage jobs (XML exports).

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

from .base import Source

SOURCES = [Source("datastage", "Datastage", "bladebridge", "datastage")]
LABELS = {"datastage": "IBM DataStage (ETL)"}
ETL_TARGETS = {"datastage": "SPARKSQL"}  # what the ETL converter generates
DEFAULT_DB = {"datastage": "oracle"}  # the database it usually loads
