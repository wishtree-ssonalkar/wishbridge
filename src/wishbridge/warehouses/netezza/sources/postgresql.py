"""PostgreSQL: a source of data for the copy (Lakehouse Federation).

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

from .base import DbType

FEDERATION = DbType("postgresql", "PostgreSQL", 5432, "database")
