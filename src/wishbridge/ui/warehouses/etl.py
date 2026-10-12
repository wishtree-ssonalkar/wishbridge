"""Screens for ETL tools (Informatica, DataStage; SSIS adds its own in ssis.py): the packages move the data,
the warehouse database holds the tables they load and the procedures that update them - read both, assess both."""

from __future__ import annotations

from .default import code_extras, report_extras  # noqa: F401 (nothing extra unless a tool adds it)

ASSESS_TOGETHER = True  # the packages and the database's code are both assessed
READ_CODE_DEFAULT = True  # the procedures are where the data is updated


def database_text() -> tuple[str, str]:
    return ("Recommended: the warehouse database these packages load",
            "The packages **move** the data; the **tables** they load and the **stored procedures, views and "
            "functions** where the data is updated live in the warehouse database. Read them here so both are "
            "assessed together — read-only, **no data is read or copied**.")


def open_database_section(inventory) -> bool:
    return not inventory  # recommended: open until it has been read
