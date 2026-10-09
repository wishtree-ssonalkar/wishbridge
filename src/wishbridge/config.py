"""Project configuration (project.yml) and the catalogue of supported source systems."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Source:
    key: str
    analyzer_tech: str  # value for `lakebridge analyze --source-tech`
    transpiler: str  # "morph" or "bladebridge"
    dialect: str  # value for `lakebridge transpile --source-dialect`

    @property
    def label(self) -> str:
        """What the user sees: WishBridge migrates data warehouses and the ETL that loads them."""
        return SOURCE_LABELS.get(self.key, self.analyzer_tech)


SOURCE_LABELS = {
    "mssql": "SQL Server data warehouse",
    "synapse": "Azure Synapse",
    "snowflake": "Snowflake",
    "oracle": "Oracle data warehouse",
    "teradata": "Teradata",
    "redshift": "Amazon Redshift",
    "bigquery": "Google BigQuery",
    "netezza": "IBM Netezza",
    "datastage": "IBM DataStage (ETL)",
    "informatica": "Informatica PowerCenter (ETL)",
    "informatica-cloud": "Informatica Cloud (ETL)",
    "ssis": "SSIS (ETL)",
}

# What WishBridge is for, in one place (shown by the app, the CLI and the docs).
PURPOSE = "Migrate your data warehouse and the ETL that loads it to Databricks."
SCOPE_ROWS = [
    ("SQL Server used as a data warehouse (fact/dimension tables, SSIS loads, Power BI or SSRS reports)", "Yes"),
    ("Oracle used as a data warehouse (often Exadata, PL/SQL loads, Informatica)", "Yes"),
    ("Teradata, Snowflake, Azure Synapse, Amazon Redshift, Google BigQuery, IBM Netezza", "Yes"),
    ("SSIS, Informatica or DataStage jobs that load a warehouse", "Yes"),
    ("SQL Server or Oracle behind a live application (order entry, bookings, HR screens)",
     "No - it stays; the fit check warns if you open one"),
]


# Analyzer names come from lakebridge Analyzer.supported_source_technologies();
# dialects come from each transpiler's lib/config.yml.
SOURCES: dict[str, Source] = {
    s.key: s
    for s in [
        Source("mssql", "MS SQL Server", "morph", "mssql"),
        Source("synapse", "Synapse", "morph", "synapse"),
        Source("snowflake", "Snowflake", "morph", "snowflake"),
        Source("oracle", "Oracle", "morph", "oracle"),
        # BladeBridge: in testing Morph failed to parse plain Teradata DDL and SEL / TOP queries
        Source("teradata", "Teradata", "bladebridge", "teradata"),
        Source("redshift", "Redshift", "morph", "redshift"),
        Source("bigquery", "BigQuery", "morph", "bigquery"),
        Source("netezza", "Netezza", "bladebridge", "netezza"),
        Source("datastage", "Datastage", "bladebridge", "datastage"),
        Source("informatica", "Informatica - PC", "bladebridge", "informatica (desktop edition)"),
        Source("informatica-cloud", "Informatica Cloud", "bladebridge", "informatica cloud"),
        Source("ssis", "SSIS", "bladebridge", "ssis"),
    ]
}

TRANSPILER_DIRS = {"morph": "databricks-morph-plugin", "bladebridge": "bladebridge"}

# ETL tools: BladeBridge asks which code to generate; WishBridge defaults to SPARKSQL.
ETL_SOURCES = frozenset({"datastage", "informatica", "informatica-cloud", "ssis"})
# What BladeBridge can generate for each ETL tool (tech_mapper_main.json): Informatica Cloud only to PySpark.
ETL_TARGETS = {"datastage": "SPARKSQL", "informatica": "SPARKSQL", "informatica-cloud": "PYSPARK", "ssis": "SPARKSQL"}

# Source dialects each LakeBridge converter accepts (from each transpiler's lib/config.yml).
CONVERTER_DIALECTS = {
    "morph": frozenset({"bigquery", "oracle", "teradata", "mssql", "redshift", "snowflake", "synapse"}),
    "bladebridge": frozenset({"datastage", "informatica (desktop edition)", "informatica cloud", "ssis", "mssql",
                              "netezza", "oracle", "redshift", "synapse", "teradata"}),
}

DEFAULT_HOURS_PER_FILE = {"LOW": 0.5, "MEDIUM": 2.0, "HIGH": 6.0, "VERY HIGH": 12.0}

_PROD_SEGMENT = re.compile(r"(^|[._\-])(prod|production)($|[._\-])", re.IGNORECASE)


def looks_like_prod(name: str) -> bool:
    return bool(_PROD_SEGMENT.search(name or ""))


@dataclass
class TableMapping:
    source: str
    target: str
    load: bool = True  # False = compare only (e.g. an ETL job's output, produced by `wishbridge execute`)
    columns: dict[str, str] = field(default_factory=dict)  # target column -> source column (empty: same names)


@dataclass
class ProjectConfig:
    path: Path  # location of project.yml
    name: str
    source: Source
    transpiler: str
    input_dir: Path
    output_dir: Path
    target_technology: str = ""  # BladeBridge ETL sources: SPARKSQL or PYSPARK
    auto_converter: bool = True  # transpiler "auto": also try the other converter on files with errors
    source_db: dict = field(default_factory=dict)  # legacy database connection settings (no password)
    overrides_dir: Path | None = None  # hand-fixed files that replace converted output
    profile: str = "DEFAULT"
    host: str = ""  # workspace this project belongs to; WishBridge refuses to run against another
    warehouse_id: str = ""
    catalog: str = "main"
    schema: str = "wishbridge"
    schema_map: dict[str, str] = field(default_factory=dict)
    ai_enabled: bool = False
    ai_model: str = "claude-opus-5-5"
    data_method: str = "federation"
    source_catalog: str = ""
    files_root: str = ""
    file_format: str = "PARQUET"
    load_mode: str = "append"
    tables: list[TableMapping] = field(default_factory=list)
    hours_per_file: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_HOURS_PER_FILE))
    hours_per_issue: float = 0.5
    scope: str = "all"  # all | recommended: deploy leaves out objects the fit check says stay on the source
    phase: str = "migration"  # assessment (offline: analyze + convert only) | migration (deploy, data, reconcile)

    @property
    def target_schema(self) -> str:
        return f"{self.catalog}.{self.schema}"

    def out(self, *parts: str) -> Path:
        p = self.output_dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def map_table(self, source_name: str) -> str:
        """Translate a source `schema.table` into a target `catalog.schema.table`."""
        parts = source_name.split(".")
        table = parts[-1]
        if len(parts) >= 2:
            src_schema = parts[-2]
            for k, v in self.schema_map.items():
                if k.lower() == src_schema.lower():
                    return f"{v}.{table}"
        return f"{self.target_schema}.{table}"


def fallback_converter(cfg: ProjectConfig) -> str | None:
    """The second converter automatic mode tries, if it supports this source; None when there is none."""
    if not cfg.auto_converter:
        return None
    other = "bladebridge" if cfg.transpiler == "morph" else "morph"
    return other if cfg.source.dialect in CONVERTER_DIALECTS[other] else None


def converter_summary(source_key: str) -> str:
    """Plain-words description of automatic mode for a source, e.g. for the app."""
    s = SOURCES[source_key]
    other = "bladebridge" if s.transpiler == "morph" else "morph"
    names = {"morph": "Morph", "bladebridge": "BladeBridge"}
    if s.dialect in CONVERTER_DIALECTS[other]:
        return (f"{names[s.transpiler]} converts {s.analyzer_tech}; any file it can't fully convert is also tried "
                f"with {names[other]}, and the better result is kept.")
    return f"{names[s.transpiler]} converts {s.analyzer_tech} (the only converter for it)."


def load_config(path: str | Path) -> ProjectConfig:
    path = Path(path).resolve()
    if not path.exists():
        raise ConfigError(f"Project file not found: {path}. Create one with `wishbridge init`.")
    raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    base = path.parent

    source_key = str(raw.get("source", "")).lower()
    if source_key not in SOURCES:
        raise ConfigError(f"Unknown source '{source_key}'. Supported: {', '.join(SOURCES)}")
    source = SOURCES[source_key]

    chosen = str(raw.get("transpiler") or "auto").lower()
    auto_converter = chosen == "auto"
    transpiler = source.transpiler if auto_converter else chosen
    if transpiler not in TRANSPILER_DIRS:
        raise ConfigError(f"Unknown transpiler '{transpiler}'. Use one of: {', '.join(TRANSPILER_DIRS)}")

    dbx = raw.get("databricks") or {}
    ai = raw.get("autofix") or {}
    data = raw.get("data") or {}
    est = raw.get("estimate") or {}

    cfg = ProjectConfig(
        path=path,
        name=raw.get("name") or base.name,
        source=source,
        transpiler=transpiler,
        auto_converter=auto_converter,
        source_db=dict(raw.get("source_db") or {}),
        target_technology=str(raw.get("target_technology") or ETL_TARGETS.get(source.key, "")).upper(),
        input_dir=(base / raw.get("input", "input")).resolve(),
        output_dir=(base / raw.get("output", "output")).resolve(),
        overrides_dir=(base / raw.get("overrides", "overrides")).resolve(),
        profile=dbx.get("profile", "DEFAULT"),
        host=str(dbx.get("host") or ""),
        warehouse_id=str(dbx.get("warehouse_id") or ""),
        catalog=dbx.get("catalog", "main"),
        schema=dbx.get("schema", "wishbridge"),
        schema_map={str(k): str(v) for k, v in (raw.get("schema_map") or {}).items()},
        ai_enabled=bool(ai.get("ai", False)),
        ai_model=ai.get("model", "claude-opus-5-5"),
        data_method=data.get("method", "federation"),
        source_catalog=data.get("source_catalog", ""),
        files_root=str(data.get("files_root", "")).rstrip("/"),
        file_format=str(data.get("file_format", "PARQUET")).upper(),
        load_mode=data.get("mode", "append"),
        hours_per_file={**DEFAULT_HOURS_PER_FILE, **(est.get("hours_per_file") or {})},
        hours_per_issue=float(est.get("hours_per_issue", 0.5)),
        scope=str(raw.get("scope") or "all").lower(),
        phase=str(raw.get("phase") or "migration").lower(),
    )
    if cfg.phase not in ("assessment", "migration"):
        raise ConfigError("phase must be 'assessment' or 'migration'")
    if cfg.scope not in ("all", "recommended"):
        raise ConfigError("scope must be 'all' or 'recommended'")
    if cfg.data_method not in ("federation", "files"):
        raise ConfigError("data.method must be 'federation' or 'files'")
    if cfg.load_mode not in ("append", "overwrite"):
        raise ConfigError("data.mode must be 'append' or 'overwrite'")
    for t in data.get("tables") or []:
        if isinstance(t, str):
            t = {"source": t}
        cfg.tables.append(TableMapping(source=t["source"], target=t.get("target") or cfg.map_table(t["source"]),
                                       load=bool(t.get("load", True)),
                                       columns={str(k): str(v) for k, v in (t.get("columns") or {}).items() if v}))
    return cfg


PROJECT_TEMPLATE = """\
# WishBridge project file. Paths are relative to this file.
name: {name}
source: {source}            # one of: {sources}
# transpiler: auto          # auto (default): best converter per file | morph | bladebridge
input: input                # put the legacy SQL / ETL files here
output: output              # everything WishBridge produces goes here
overrides: overrides        # hand-fixed versions of converted files (same file names) - kept across runs
scope: all                  # all | recommended: deploy only what belongs on Databricks (see the fit check)
phase: assessment           # assessment: offline - analyze and convert only, nothing goes to Databricks or the client
                            # database. Change to migration when the code is ready to deploy and the data to copy.

databricks:
  profile: DEFAULT          # profile in ~/.databrickscfg
  host: ""                  # workspace URL this project belongs to; WishBridge refuses to run against another
  warehouse_id: ""          # SQL warehouse used by deploy/load/reconcile (blank = auto-pick)
  catalog: main             # newer workspaces usually use `workspace`
  schema: wishbridge_{name_id}   # dev/test schema the converted objects are deployed to

# Rename source schemas to target catalog.schema in the converted code
schema_map:{schema_map}

autofix:
  ai: false                 # true = ask Claude for fix suggestions (needs ANTHROPIC_API_KEY)
  model: claude-opus-5-5

data:
  method: federation        # federation (Lakehouse Federation catalog) | files (COPY INTO from a volume)
  source_catalog: ""        # federation: foreign catalog that points at the source database
  files_root: ""            # files: e.g. /Volumes/main/landing/{name_id}  (one sub-folder per table)
  file_format: PARQUET      # files: PARQUET | CSV | JSON | AVRO | ORC
  mode: append              # append | overwrite
  tables: []                # e.g. - dbo.Customers   or   - {{source: dbo.Orders, target: main.sales.orders}}

estimate:
  hours_per_file: {{LOW: 0.5, MEDIUM: 2, HIGH: 6, VERY HIGH: 12}}
  hours_per_issue: 0.5
"""


def render_template(name: str, source: str) -> str:
    name_id = re.sub(r"[^a-z0-9_]", "_", name.lower())
    schema_map = f"\n  dbo: main.wishbridge_{name_id}" if source in ("mssql", "synapse") else "         # e.g. {SALES: main.sales}"
    return PROJECT_TEMPLATE.format(name=name, name_id=name_id, source=source, sources=", ".join(SOURCES), schema_map=schema_map)
