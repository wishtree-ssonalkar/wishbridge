"""Screens for warehouses with nothing special (SQL Server, Synapse, Oracle, Snowflake, Teradata, ...)."""

from __future__ import annotations

ASSESS_TOGETHER = False  # code files and database code are alternatives: "Code to assess" picks one
READ_CODE_DEFAULT = False  # the folder has the code


def database_text() -> tuple[str, str]:
    return ("Optional: add the table list from the database",
            "**Not needed** when the folder has the code. Useful when the code does not create the tables "
            "(scripts without `CREATE TABLE`), or to add row counts and sizes. Only the list of tables, columns "
            "and data types is read — **no data is read or copied**.")


def open_database_section(inventory) -> bool:
    return False


def code_extras(cfg, raw) -> None:
    pass


def report_extras(cfg, state) -> None:
    pass
