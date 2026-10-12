"""Informatica PowerCenter (XML exports) and Informatica Cloud (IICS .zip export packages).

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

from .base import Source

SOURCES = [Source("informatica", "Informatica - PC", "bladebridge", "informatica (desktop edition)"), Source("informatica-cloud", "Informatica Cloud", "bladebridge", "informatica cloud")]
LABELS = {"informatica": "Informatica PowerCenter (ETL)", "informatica-cloud": "Informatica Cloud (ETL)"}
ETL_TARGETS = {"informatica": "SPARKSQL", "informatica-cloud": "PYSPARK"}  # what the ETL converter generates
DEFAULT_DB = {"informatica": "oracle", "informatica-cloud": "oracle"}  # the database it usually loads
