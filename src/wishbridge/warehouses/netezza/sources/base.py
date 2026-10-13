"""The building blocks every warehouse file uses (no WishBridge imports here, so any module can use them)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ERROR, WARNING, INFO = "error", "warning", "info"
I, M, S = re.IGNORECASE, re.MULTILINE, re.DOTALL

# Which source dialects a check applies to (rules.py)
TSQL = frozenset({"mssql", "synapse"})
SNOWFLAKE = frozenset({"snowflake"})
ORACLE = frozenset({"oracle"})
TERADATA = frozenset({"teradata"})
NETEZZA = frozenset({"netezza"})
ALL = None  # every source dialect


@dataclass(frozen=True)
class Source:
    """A source system a project can be (project.yml `source:`)."""
    key: str
    analyzer_tech: str  # value for the analyzer's --source-tech
    transpiler: str  # converter tried first: "morph" or "bladebridge"
    dialect: str  # value for the converter's --source-dialect

    @property
    def label(self) -> str:
        """What the user sees: WishBridge migrates data warehouses and the ETL that loads them."""
        from . import LABELS

        return LABELS.get(self.key, self.analyzer_tech)


@dataclass(frozen=True)
class DbType:
    """A database Lakehouse Federation can connect to with a user and password (source_db.py)."""
    key: str  # Databricks connection TYPE
    label: str
    port: int
    catalog_option: str | None  # option naming the database in CREATE FOREIGN CATALOG (None: not needed)
    catalog_label: str = "Database"
    extra: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (option, label) also required


@dataclass(frozen=True)
class Detector:
    """A check on the converted code (rules.py): the pattern found means something needs a person."""
    rule: str
    severity: str
    scope: frozenset | None
    pattern: re.Pattern
    message: str
    raw: bool = False  # match against raw text (strings visible); the match must still start in code
