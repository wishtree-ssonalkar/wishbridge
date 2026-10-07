"""Run SQL on a Databricks SQL warehouse through the Statement Execution API."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import ProjectConfig


class SqlError(Exception):
    pass


@dataclass
class SqlResult:
    rows: list[list[Any]]
    columns: list[str]


class Warehouse:
    def __init__(self, cfg: ProjectConfig):
        from databricks.sdk import WorkspaceClient

        self.w = WorkspaceClient(profile=cfg.profile)
        self.warehouse_id = cfg.warehouse_id or self._pick_warehouse()

    def _pick_warehouse(self) -> str:
        from databricks.sdk.service.sql import State

        whs = list(self.w.warehouses.list())
        if not whs:
            raise SqlError("No SQL warehouses in this workspace. Create one, then set databricks.warehouse_id.")
        running = [w for w in whs if w.state == State.RUNNING]
        return (running or whs)[0].id

    def run(self, sql: str, catalog: str | None = None, schema: str | None = None, timeout_s: int = 900) -> SqlResult:
        from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout, StatementState

        resp = self.w.statement_execution.execute_statement(
            statement=sql,
            warehouse_id=self.warehouse_id,
            catalog=catalog,
            schema=schema,
            wait_timeout="30s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
        )
        deadline = time.monotonic() + timeout_s
        while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
            if time.monotonic() > deadline:
                self.w.statement_execution.cancel_execution(resp.statement_id)
                raise SqlError(f"Timed out after {timeout_s}s")
            time.sleep(2)
            resp = self.w.statement_execution.get_statement(resp.statement_id)
        if resp.status.state != StatementState.SUCCEEDED:
            err = resp.status.error
            raise SqlError(err.message if err and err.message else str(resp.status.state))
        cols = [c.name for c in (resp.manifest.schema.columns or [])] if resp.manifest and resp.manifest.schema else []
        rows = (resp.result.data_array or []) if resp.result else []
        return SqlResult(rows=rows, columns=cols)
