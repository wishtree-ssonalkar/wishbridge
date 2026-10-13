"""Azure Synapse dedicated SQL pool: T-SQL, with distribution and columnstore options.

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

from .base import DbType, Source

SOURCES = [Source("synapse", "Synapse", "morph", "synapse")]
LABELS = {"synapse": "Azure Synapse"}
HINTS = {"synapse": [r"\bDISTRIBUTION\s*=\s*(?:HASH|ROUND_ROBIN|REPLICATE)", r"\bCLUSTERED\s+COLUMNSTORE\s+INDEX\b"]}  # Synapse code is T-SQL too: only these markers make it Synapse
DEFAULT_DB = {"synapse": "synapse"}
FEDERATION = DbType("sqldw", "Azure Synapse (dedicated SQL pool)", 1433, "database")
FEDERATION_FOR = {"synapse": "sqldw"}

DATABASE = "synapse"  # database type
INVENTORY_QUERY = """SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name, c.column_id AS ordinal,
       ty.name AS data_type, c.max_length, c.precision, c.scale,
       CASE WHEN c.is_nullable = 1 THEN 'YES' ELSE 'NO' END AS is_nullable,
       (SELECT SUM(p.rows) FROM sys.partitions p WHERE p.object_id = t.object_id AND p.index_id IN (0, 1)  /* per distribution: approximate */) AS row_count,
       (SELECT CAST(SUM(a.total_pages) * 8 / 1024.0 AS DECIMAL(18, 2)) FROM sys.partitions p
          JOIN sys.allocation_units a ON a.container_id = p.partition_id WHERE p.object_id = t.object_id) AS size_mb
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name, c.column_id;"""
HOW_TO_SAVE = 'SSMS / Azure Data Studio: run, then save the result grid as CSV.'
TYPES = {"timestamp": "BINARY"}  # where a type name means something else here than in the shared map

# The T-SQL checks and tidy-ups are shared with SQL Server (sqlserver.py).
