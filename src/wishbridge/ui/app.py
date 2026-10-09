"""Wishtree WishBridge UI. Started by `wishbridge ui` (Streamlit)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml
import streamlit as st

from wishbridge import __version__
from wishbridge.config import (CONVERTER_DIALECTS, PURPOSE, SCOPE_ROWS, SOURCES, ConfigError, converter_summary,
                               load_config)
from wishbridge import discover
from wishbridge import source_db as sd
from wishbridge.dbx import SqlError, Warehouse
from wishbridge.lakebridge import LakeBridgeError
from wishbridge.state import load_state
from wishbridge.ui import helpers as h

STEP_ERRORS = (LakeBridgeError, SqlError, ConfigError, RuntimeError, FileNotFoundError, ValueError)
STATUS_ICON = {"ready": "✅ ready", "review": "🟡 review", "needs-fix": "🔴 needs fix"}

st.set_page_config(page_title="Wishtree WishBridge", page_icon="🌉", layout="wide")


# ----------------------------------------------------------------- state

def start_dir() -> str:
    args = sys.argv[1:]
    return args[args.index("--project") + 1] if "--project" in args else ""


ss = st.session_state
ss.setdefault("project", start_dir())
ss.setdefault("workspace", None)


def project_ok() -> bool:
    return bool(ss.project) and h.project_file(ss.project).exists()


# ----------------------------------------------------------------- sidebar

with st.sidebar:
    st.markdown(f"### 🌉 Wishtree WishBridge\nData warehouse migration to Databricks · v{__version__}")
    mode = st.radio("Project", ["Open a folder", "Create new"], horizontal=True, label_visibility="collapsed")
    if mode == "Open a folder":
        folder = st.text_input("Project or client code folder", value=ss.project,
                               placeholder=r"C:\migrations\acme-dw  or  C:\client-repo",
                               help="A WishBridge project opens directly. Any other folder of SQL/ETL code (a repository, "
                                    "a Visual Studio database project, an export) gets a project created for it.")
        if st.button("Open", width="stretch"):
            if h.project_file(folder).exists():
                ss.project = str(Path(folder).expanduser().resolve())
                ss.workspace = None
                ss.pop("code_info", None)
                st.rerun()
            else:
                try:
                    with st.spinner("Looking at the code..."):
                        ss.code_info = discover.inspect(folder)
                    ss.project = ""
                    st.rerun()
                except (ValueError, OSError) as e:
                    st.error(str(e))
        if ss.get("created_projects"):
            st.caption("Projects created for the client code:")
            for p in ss.created_projects:
                if st.button(f"📂 {Path(p).name}", key=f"open-{p}", width="stretch"):
                    ss.project, ss.workspace = p, None
                    st.rerun()
    else:
        with st.form("new-project"):
            name = st.text_input("Project name", placeholder="acme-dw")
            source = st.selectbox("Source system", list(SOURCES), format_func=lambda k: SOURCES[k].label)
            parent = st.text_input("Create in folder", value=r"C:\migrations")
            if st.form_submit_button("Create project", width="stretch"):
                try:
                    ss.project = str(h.create_project(parent, name, source))
                    ss.workspace = None
                    st.rerun()
                except (ValueError, OSError) as e:
                    st.error(str(e))
    if project_ok():
        st.success(f"Open: {Path(ss.project).name}")
        st.caption(ss.project)


st.title("Wishtree WishBridge")
st.caption(f"{PURPOSE} Assess, convert, deploy, copy the data and prove it matches — built on Databricks Labs LakeBridge.")


def show_scope() -> None:
    st.markdown("**What WishBridge migrates**")
    st.table(pd.DataFrame(SCOPE_ROWS, columns=["What the client has", "Migrate it?"]).set_index("What the client has"))

info = ss.get("code_info")
if info and not project_ok():
    # A folder of client code (no project.yml): offer to create WishBridge project(s) that read it in place.
    det = info.detection
    st.subheader("Create a project for this code")
    st.markdown(f"`{info.path}` is client code, not a WishBridge project yet: **{info.files} code files** "
                f"({info.sql_files} SQL). WishBridge only **reads** this folder; the project and everything it produces "
                f"go in a separate folder.")
    (st.success if det.confidence == "high" else st.info if det.confidence == "medium" else st.warning)(
        f"Detected source system: **{SOURCES[det.source].label}** ({det.confidence} confidence) - {det.reason}")
    if det.source in ("mssql", "oracle"):
        st.caption("SQL Server and Oracle are migrated when they hold a **data warehouse**. Analyze runs a fit check "
                   "that warns if this is an application's database instead.")
    with st.form("code-project"):
        c1, c2 = st.columns(2)
        src_keys = list(SOURCES)
        source = c1.selectbox("Source system", src_keys, index=src_keys.index(det.source),
                              format_func=lambda k: SOURCES[k].label)
        name = c2.text_input("Project name", value=discover.suggested_name(info.path))
        c1, c2 = st.columns(2)
        parent = c1.text_input("Create projects in", value=r"C:\migrations")
        catalog = c2.text_input("Databricks catalog", value="main",
                                help="Newer workspaces use `workspace`; you can change it later in Settings.")
        split = False
        if info.databases:
            dbs = ", ".join(f"{n} ({c} files)" for n, _, c in info.databases)
            st.markdown(f"The code holds **{len(info.databases)} databases**: {dbs}.")
            split = st.radio("Projects", [True, False], horizontal=True,
                             format_func=lambda s: "One project per database (recommended)" if s else "One project for everything")
        if st.form_submit_button("Create project" + ("s" if info.databases and split else ""), type="primary"):
            try:
                made = discover.create_projects_for_code(info, parent, name, source, split, catalog.strip() or "main")
            except (ValueError, OSError, ConfigError) as e:
                st.error(str(e))
            else:
                ss.created_projects = [str(p) for p in made]
                ss.project, ss.workspace = str(made[0]), None
                ss.pop("code_info", None)
                st.rerun()
    if st.button("Cancel"):
        ss.pop("code_info", None)
        st.rerun()
    st.stop()

if not project_ok():
    st.info("Open the client's warehouse code (a project folder, a repository, a database project or an ETL export) "
            "or create a new project in the sidebar to begin.")
    st.markdown(
        "**How it works:** 1. open the warehouse code · 2. fill in the settings · 3. analyze (is it a warehouse? how big?) · "
        "4. convert and deploy · 5. copy the data and reconcile · 6. share the report."
    )
    show_scope()
    st.stop()

try:
    raw = h.read_raw(ss.project)
    if not isinstance(raw, dict):
        raise ConfigError("the file does not contain WishBridge settings")
    cfg = load_config(h.project_file(ss.project))
except (ConfigError, yaml.YAMLError, KeyError, TypeError, ValueError) as e:
    st.error(f"This project's settings file can't be read: {str(e).splitlines()[0]}")
    st.markdown(f"Open `{h.project_file(ss.project)}` in a text editor and correct it, or open the client's code folder "
                "instead - the app can create a fresh project for it.")
    st.stop()

tab_settings, tab_code, tab_run, tab_results, tab_fixes, tab_env = st.tabs(
    ["1 · Settings", "2 · Code", "3 · Run", "4 · Results", "5 · Manual fixes", "Environment"])


# ----------------------------------------------------------------- settings

with tab_settings:
    dbx = raw.setdefault("databricks", {})
    data = raw.setdefault("data", {})
    ai = raw.setdefault("autofix", {})

    st.subheader("Project settings")
    c1, c2 = st.columns(2)
    with c1:
        src_keys = list(SOURCES)
        source = st.selectbox("Source system", src_keys, index=src_keys.index(cfg.source.key),
                              format_func=lambda k: SOURCES[k].label)
    with c2:
        # Only converters that support the chosen source are offered; "auto" is the default and the recommendation.
        conv_options = ["auto"] + [c for c in ("morph", "bladebridge") if SOURCES[source].dialect in CONVERTER_DIALECTS[c]]
        cur_conv = str(raw.get("transpiler") or "auto").lower()
        conv_labels = {"auto": "Automatic (recommended)", "morph": "Morph only", "bladebridge": "BladeBridge only"}
        transpiler = st.selectbox("Converter", conv_options, format_func=conv_labels.get,
                                  index=conv_options.index(cur_conv) if cur_conv in conv_options else 0)
        st.caption(converter_summary(source) if transpiler == "auto"
                   else "Fixed converter: no second attempt on files it can't convert.")
        scope_labels = {"all": "Everything", "recommended": "Only what belongs on Databricks (fit check)"}
        cur_scope = str(raw.get("scope") or "all").lower()
        scope = st.selectbox("What to deploy", list(scope_labels), format_func=scope_labels.get,
                             index=list(scope_labels).index(cur_scope) if cur_scope in scope_labels else 0,
                             help="The fit check (run with Analyze) marks application logic to keep on the source "
                                  "and objects Databricks does not need. 'Only what belongs' leaves those out of deploy.")

    # --- Databricks workspace: which saved login (profile) this project uses
    st.markdown("**Databricks workspace** — the client workspace this project migrates into.")
    if "profiles" not in ss:
        ss.profiles = h.list_profiles()
    profiles = ss.profiles
    names = [p["name"] for p in profiles]
    cur_profile = ss.get("pending_profile") or dbx.get("profile", "DEFAULT")
    if cur_profile not in names:
        names = [cur_profile] + names
    by_name = {p["name"]: p for p in profiles}

    def profile_label(n: str) -> str:
        p = by_name.get(n)
        if not p:
            return f"{n} — not set up on this computer"
        state = {True: "signed in", False: "login expired", None: "status unknown"}[p["valid"]]
        return f"{n} — {p['host']} ({state})"

    c1, c2 = st.columns([3, 2])
    with c1:
        profile = st.selectbox("Workspace login", names, index=names.index(cur_profile), format_func=profile_label)
    with c2:
        st.write("")
        if st.button("Load warehouses and catalogs", width="stretch"):
            with st.spinner("Asking the workspace..."):
                try:
                    ss.workspace = h.list_workspace(profile)
                except Exception as e:  # SDK raises many types; show the message
                    ss.workspace = None
                    st.error(f"Could not reach the workspace: {e}")
    chosen_host = (by_name.get(profile) or {}).get("host", "")
    project_host = str(dbx.get("host") or "")
    if ss.workspace and ss.workspace.get("profile") == profile:
        st.success(f"Connected as **{ss.workspace['user']}** on **{ss.workspace['host']}**")
    if project_host and chosen_host and project_host.rstrip("/").lower() != chosen_host.rstrip("/").lower():
        st.error(f"This project belongs to **{project_host}**, but the selected login is for **{chosen_host}**. "
                 "Pick the login for the project's workspace. Saving will move the project to the selected workspace.")
    if by_name.get(profile, {}).get("valid") is False:
        st.warning("This login has expired. Sign in again below with the same profile name.")

    with st.expander("Connect to another workspace (sign in)"):
        st.caption("Opens a browser on this computer to sign in. The login is saved in this user's ~/.databrickscfg; "
                   "WishBridge never sees the password.")
        n1, n2, n3 = st.columns([3, 2, 1])
        new_host = n1.text_input("Workspace URL", placeholder="https://adb-1234567890.12.azuredatabricks.net")
        new_profile = n2.text_input("Profile name", placeholder="CLIENT_ACME")
        n3.write("")
        if n3.button("Sign in"):
            with st.spinner("Finish signing in in the browser window..."):
                ok, msg = h.sign_in(new_host, new_profile)
            (st.success if ok else st.error)(msg)
            if ok:
                ss.profiles = h.list_profiles()
                ss.pending_profile = new_profile.strip()
                st.rerun()

    st.markdown("**Target in Databricks** — converted objects are created in this test schema.")
    c1, c2, c3 = st.columns(3)
    ws = ss.workspace if ss.workspace and ss.workspace.get("profile") == profile else None
    with c1:
        if ws and ws["warehouses"]:
            wh_ids = [""] + [w["id"] for w in ws["warehouses"]]
            labels = {"": "(first running warehouse)", **{w["id"]: f"{w['name']} · {w['state']}" for w in ws["warehouses"]}}
            cur = str(dbx.get("warehouse_id") or "")
            warehouse = st.selectbox("SQL warehouse", wh_ids, index=wh_ids.index(cur) if cur in wh_ids else 0,
                                     format_func=lambda i: labels[i])
        else:
            warehouse = st.text_input("SQL warehouse ID", value=str(dbx.get("warehouse_id") or ""),
                                      help="Leave blank to use the first running warehouse.")
    with c2:
        cats = [c["name"] for c in ws["catalogs"]] if ws else []
        cur_cat = dbx.get("catalog", "main")
        if cats:
            catalog = st.selectbox("Catalog", cats if cur_cat in cats else [cur_cat] + cats,
                                   index=(cats if cur_cat in cats else [cur_cat] + cats).index(cur_cat))
        else:
            catalog = st.text_input("Catalog", value=cur_cat)
    with c3:
        schema = st.text_input("Test schema", value=dbx.get("schema", "wishbridge"))
    if "prod" in f"{catalog}.{schema}".lower():
        st.warning("This looks like a production schema. WishBridge will refuse to deploy there.")

    st.markdown("**Schema mapping** — rename source schemas in the converted code (e.g. `dbo` → `main.sales`).")
    map_df = st.data_editor(pd.DataFrame(h.schema_map_rows(raw) or [{"source_schema": "", "target": ""}]),
                            num_rows="dynamic", width="stretch", key="schema_map",
                            column_config={"source_schema": "Source schema", "target": "Target catalog.schema"})

    # --- Source database: where the data lives. Optional - only needed to copy the data.
    st.markdown("**Source database** — optional, only needed to copy the data. WishBridge creates a Lakehouse "
                "Federation connection in Databricks; the password goes into Databricks secrets, never into project.yml.")
    sdb = raw.get("source_db") or {}
    default_type = sd.DEFAULT_FOR_SOURCE.get(cfg.source.key, "sqlserver")
    with st.container(border=True):
        if ss.get("sdb_msg"):
            kind, msg = ss.pop("sdb_msg")
            getattr(st, kind)(msg)
        if default_type is None and not sdb:
            st.info(sd.NOT_SUPPORTED_HINT)
        type_keys = list(sd.DB_TYPES)
        cur_type = sdb.get("type") or default_type or "sqlserver"
        d1, d2, d3 = st.columns([2, 3, 1])
        db_type = d1.selectbox("Database type", type_keys, index=type_keys.index(cur_type),
                               format_func=lambda k: sd.DB_TYPES[k].label, key="sdb_type")
        dbt = sd.DB_TYPES[db_type]
        host = d2.text_input("Server (host)", value=sdb.get("host", ""), placeholder="sqlprod01.client.com", key="sdb_host")
        port = d3.number_input("Port", value=int(sdb.get("port") or dbt.port), step=1, key="sdb_port")
        d1, d2, d3 = st.columns(3)
        database = d1.text_input(dbt.catalog_label, value=sdb.get("database", ""), key="sdb_db",
                                 disabled=dbt.catalog_option is None,
                                 help=None if dbt.catalog_option else f"Not needed for {dbt.label}: the whole server is exposed.")
        user = d2.text_input("User (read-only is enough)", value=sdb.get("user", ""), key="sdb_user")
        password = d3.text_input("Password", type="password", key="sdb_pw",
                                 help="Sent straight to Databricks secrets; not saved anywhere else.")
        extra = {opt: st.text_input(label, value=(sdb.get("options") or {}).get(opt, ""), key=f"sdb_{opt}")
                 for opt, label in dbt.extra}
        connected = bool(sdb.get("catalog"))
        b1, b2, b3 = st.columns(3)
        if b1.button("🔌 Re-create connection" if connected else "🔌 Create connection", width="stretch"):
            settings = {"type": db_type, "host": host.strip(), "port": int(port), "database": database.strip(),
                        "user": user.strip(), "options": extra}
            with st.spinner("Creating the connection in Databricks..."):
                try:
                    made = sd.create_connection(cfg, settings, password, Warehouse(cfg))
                except (SqlError, ValueError) as e:
                    st.error(f"Connection not created: {e}")
                else:
                    new = dict(raw)
                    new["source_db"] = {**settings, "connection": made["connection"], "catalog": made["catalog"]}
                    new["data"] = {**data, "method": "federation", "source_catalog": made["catalog"]}
                    h.save_raw(ss.project, new)
                    ss.pop("source_schemas", None)
                    ss.pop("sdb_pw", None)  # forget the typed password
                    ss.sdb_msg = ("success", f"Created connection `{made['connection']}` and catalog `{made['catalog']}`. "
                                             "Press *Test connection* to check Databricks can reach the database.")
                    st.rerun()
        if b2.button("🔎 Test connection", width="stretch", disabled=not connected):
            with st.spinner("Asking Databricks to read the database's schema list..."):
                try:
                    ss.source_schemas = sd.test_connection(sdb["catalog"], Warehouse(cfg))
                    st.success(f"Databricks reached the database: {len(ss.source_schemas)} schema(s) found.")
                except (SqlError, ValueError) as e:
                    ss.pop("source_schemas", None)
                    st.error(f"Databricks could not read the database: {e}")
                    st.caption("Check the server, port, database and login, and that the database accepts connections "
                               "from Databricks (firewall allow-list, VPN or private link - usually an IT change).")
        if b3.button("🗑 Remove connection", width="stretch", disabled=not connected):
            try:
                sd.remove_connection(cfg, Warehouse(cfg))
            except (SqlError, ValueError) as e:
                st.error(f"Could not remove it: {e}")
            else:
                new = dict(raw)
                new.pop("source_db", None)
                new["data"] = {**data, "source_catalog": ""}
                h.save_raw(ss.project, new)
                ss.pop("source_schemas", None)
                ss.sdb_msg = ("success", "Connection, catalog and stored password removed from Databricks.")
                st.rerun()
        if connected:
            st.caption(f"Connected through catalog `{sdb['catalog']}` ({sd.DB_TYPES.get(sdb.get('type'), dbt).label} "
                       f"at {sdb.get('host')}:{sdb.get('port')}).")
        if ss.get("source_schemas"):
            p1, p2 = st.columns([1, 2])
            schema_pick = p1.selectbox("Schema", ss.source_schemas, key="sdb_schema")
            if p1.button("List tables"):
                try:
                    ss.source_tables = {schema_pick: sd.list_tables(sdb["catalog"], schema_pick, Warehouse(cfg))}
                except (SqlError, ValueError) as e:
                    st.error(str(e))
            available = (ss.get("source_tables") or {}).get(schema_pick, [])
            picked = p2.multiselect("Tables to copy", available, key="sdb_tables",
                                    placeholder="Press List tables first" if not available else "Choose tables")
            if picked and p2.button(f"➕ Add {len(picked)} table(s) to the copy list"):
                current = h.rows_to_tables(h.table_rows(raw))
                have = {t if isinstance(t, str) else t["source"] for t in current}
                current += [f"{schema_pick}.{t}" for t in picked if f"{schema_pick}.{t}" not in have]
                new = dict(raw)
                new["data"] = {**data, "tables": current}
                h.save_raw(ss.project, new)
                ss.sdb_msg = ("success", f"Added {len(picked)} table(s) from {schema_pick}. Targets follow the schema mapping.")
                st.rerun()

    st.markdown("**Data to copy**")
    c1, c2, c3 = st.columns(3)
    with c1:
        method = st.selectbox("Method", ["federation", "files"], index=["federation", "files"].index(data.get("method", "federation")),
                              help="federation: read the source through a Lakehouse Federation catalog. files: COPY INTO from a volume.")
    with c2:
        if method == "federation":
            fed_options = [c["name"] for c in ws["catalogs"]] if ws else []
            cur_src = data.get("source_catalog", "")
            if fed_options:
                opts = [""] + fed_options if cur_src in fed_options or not cur_src else ["", cur_src] + fed_options
                source_catalog = st.selectbox("Source catalog (federation)", opts, index=opts.index(cur_src))
            else:
                source_catalog = st.text_input("Source catalog (federation)", value=cur_src)
            files_root, file_format = data.get("files_root", ""), data.get("file_format", "PARQUET")
        else:
            files_root = st.text_input("Files root (volume path)", value=data.get("files_root", ""),
                                       placeholder="/Volumes/main/landing/acme")
            file_format = st.selectbox("File format", ["PARQUET", "CSV", "JSON", "AVRO", "ORC"],
                                       index=["PARQUET", "CSV", "JSON", "AVRO", "ORC"].index(str(data.get("file_format", "PARQUET")).upper()))
            source_catalog = data.get("source_catalog", "")
    with c3:
        load_mode = st.selectbox("Load mode", ["append", "overwrite"], index=["append", "overwrite"].index(data.get("mode", "append")))
    tables_df = st.data_editor(pd.DataFrame(h.table_rows(raw) or [{"source": "", "target": ""}]),
                               num_rows="dynamic", width="stretch", key="tables",
                               column_config={"source": "Source table (schema.table)",
                                              "target": "Target (optional catalog.schema.table)"})

    with st.expander("AI suggestions and estimates"):
        ai_on = st.checkbox("Ask Claude for fix suggestions during convert (needs ANTHROPIC_API_KEY; sends code, not data)",
                            value=bool(ai.get("ai", False)))
        model = st.text_input("Claude model", value=ai.get("model", "claude-opus-5-5"))
        est = raw.get("estimate") or {}
        hpi = st.number_input("Hours per open item", value=float(est.get("hours_per_issue", 0.5)), step=0.25)

    if st.button("💾 Save settings", type="primary"):
        new = dict(raw)
        new["source"] = source
        if transpiler == "auto":
            new.pop("transpiler", None)  # automatic is the default
        else:
            new["transpiler"] = transpiler
        new["scope"] = scope
        host = (ws or {}).get("host") or chosen_host or project_host
        new["databricks"] = {**dbx, "profile": profile, "host": host, "warehouse_id": warehouse,
                             "catalog": catalog, "schema": schema}
        new["schema_map"] = h.rows_to_schema_map(map_df.to_dict("records"))
        new["data"] = {**data, "method": method, "source_catalog": source_catalog, "files_root": files_root,
                       "file_format": file_format, "mode": load_mode, "tables": h.rows_to_tables(tables_df.to_dict("records"))}
        new["autofix"] = {"ai": ai_on, "model": model}
        new["estimate"] = {**est, "hours_per_issue": hpi}
        try:
            h.save_raw(ss.project, new)
            ss.pop("pending_profile", None)
            st.success("Saved project.yml")
            st.rerun()
        except (ConfigError, KeyError, ValueError) as e:
            st.error(f"Not saved: {e}")


# ----------------------------------------------------------------- code

with tab_code:
    st.subheader("Legacy code")
    st.caption(f"Files in {cfg.input_dir}. Only code goes here — data is copied in the Run step.")
    uploads = st.file_uploader("Add files", accept_multiple_files=True, type=h.INPUT_EXTENSIONS)
    if uploads and st.button(f"Add {len(uploads)} file(s) to the project"):
        for up in uploads:
            h.save_upload(ss.project, up.name, up.getvalue(), raw.get("input", "input"))
        st.success(f"Added {len(uploads)} file(s)")
        st.rerun()
    files = h.input_files(ss.project, raw.get("input", "input"))
    if not files:
        st.info("No code yet. Upload files above, or copy them into the input folder.")
    else:
        st.dataframe(pd.DataFrame([{"file": str(p.relative_to(cfg.input_dir)), "size (KB)": round(p.stat().st_size / 1024, 1)}
                                   for p in files]), width="stretch", hide_index=True)
        pick = st.selectbox("Preview", [str(p.relative_to(cfg.input_dir)) for p in files])
        st.code((cfg.input_dir / pick).read_text(encoding="utf-8-sig", errors="replace")[:20000], language="sql")


# ----------------------------------------------------------------- run

def run_step(label: str, fn, summary) -> bool:
    with st.status(label, expanded=False) as box:
        try:
            res = fn()
        except STEP_ERRORS as e:
            box.update(label=f"{label} — failed", state="error", expanded=True)
            st.error(str(e))
            return False
        box.update(label=f"{label} — {summary(res)}", state="complete")
        return True


with tab_run:
    st.subheader("Run the migration")
    c1, c2, c3 = st.columns(3)
    with c1:
        do_analyze = st.checkbox("1. Analyze the code", value=True)
        do_convert = st.checkbox("2. Convert to Databricks SQL", value=True)
        use_ai = st.checkbox("…with Claude suggestions", value=cfg.ai_enabled, disabled=not do_convert)
    with c2:
        do_deploy = st.checkbox("3. Deploy to the test schema", value=False)
        recreate = st.checkbox("…rebuild objects that already exist", value=False, disabled=not do_deploy)
        do_load = st.checkbox("4. Copy the data", value=False)
        execute = st.checkbox("…really copy (otherwise only write the plan)", value=False, disabled=not do_load)
    with c3:
        do_reconcile = st.checkbox("5. Reconcile the data", value=False)
        do_report = st.checkbox("6. Build the report", value=True)
    st.caption(f"Target: `{cfg.target_schema}` · converter: {'automatic, ' if cfg.auto_converter else ''}{cfg.transpiler}"
               + (" first" if cfg.auto_converter else "") + f" · source: {cfg.source.analyzer_tech}")

    if st.button("▶ Start", type="primary"):
        from wishbridge.analysis import run_analyze
        from wishbridge.convert import run_convert
        from wishbridge.data import run_load
        from wishbridge.deploy import run_deploy
        from wishbridge.reconcile import run_reconcile
        from wishbridge.report import build_report

        steps = [
            (do_analyze, "Analyze", lambda: run_analyze(cfg),
             lambda r: f"{len(r['programs'])} files, estimate {r['estimated_hours_baseline']} h · fit: {r['fit']['verdict']}"),
            (do_convert, "Convert", lambda: run_convert(cfg, use_ai),
             lambda r: f"{r['summary']['ready']} ready, {r['summary']['review']} review, {r['summary']['needs_fix']} need fixes"),
            (do_deploy, "Deploy", lambda: run_deploy(cfg, recreate=recreate),
             lambda r: f"{r['summary']['statements_ok']}/{r['summary']['statements']} statements OK"),
            (do_load, "Load data", lambda: run_load(cfg, execute=execute),
             lambda r: (f"{r['summary']['loaded']}/{r['summary']['tables']} tables loaded" if execute
                        else f"plan for {r['summary']['tables']} tables written")),
            (do_reconcile, "Reconcile", lambda: run_reconcile(cfg),
             lambda r: f"{r['summary']['matched']}/{r['summary']['tables']} tables match"),
            (do_report, "Report", lambda: build_report(cfg), lambda r: "report.html updated"),
        ]
        for enabled, label, fn, summary in steps:
            if enabled and not run_step(label, fn, summary):
                st.warning("Stopped at the failed step. Fix the problem and start again.")
                break
        else:
            st.success("Done. Open the Results tab.")


# ----------------------------------------------------------------- results

with tab_results:
    state = load_state(cfg)
    if not state:
        st.info("Nothing has run yet. Use the Run tab.")
    else:
        a, c, d, ld, r = (state.get(k) for k in ("analyze", "convert", "deploy", "load", "reconcile"))
        m = st.columns(5)
        if a:
            m[0].metric("Files analysed", len(a["programs"]), help=f"Manual estimate {a['estimated_hours_baseline']} h")
        if c:
            s = c["summary"]
            m[1].metric("Ready", f"{s['ready']}/{s['files']}")
            m[2].metric("Open items", s["open_errors"] + s["open_warnings"])
        if d:
            m[3].metric("Statements deployed", f"{d['summary']['statements_ok']}/{d['summary']['statements']}")
        if r and r.get("mode") == "quick":
            m[4].metric("Tables reconciled", f"{r['summary']['matched']}/{r['summary']['tables']}")

        fit = state.get("fit")
        if fit:
            from wishbridge.fit import CATEGORY_LABELS

            st.markdown("#### Is this a data warehouse? (fit check)")
            (st.warning if fit["verdict"] == "application" else st.info if fit["verdict"] == "mixed" else st.success)(
                f"**{fit['headline']}**\n\n" + "\n".join(f"- {x}" for x in fit["advice"]))
            fm = st.columns(len(CATEGORY_LABELS))
            for col, (k, label) in zip(fm, CATEGORY_LABELS.items()):
                col.metric(label, fit["counts"].get(k, 0))
            with st.expander("Why, and every object's recommendation"):
                for key, label in (("app_evidence", "Signs of an application database"),
                                   ("warehouse_evidence", "Signs of reporting / warehouse use")):
                    if fit.get(key):
                        st.markdown(f"**{label}:** " + "; ".join(fit[key]))
                show = st.multiselect("Show", list(CATEGORY_LABELS), default=list(CATEGORY_LABELS),
                                      format_func=CATEGORY_LABELS.get, key="fit_filter")
                st.dataframe(pd.DataFrame([{
                    "file": o["file"], "type": o["type"], "recommendation": CATEGORY_LABELS[o["category"]],
                    "why": "; ".join(o["reasons"])} for o in fit["objects"] if o["category"] in show]),
                    width="stretch", hide_index=True)
            if cfg.scope != "recommended":
                st.caption("To deploy only what belongs on Databricks, set **What to deploy** in Settings.")

        if c:
            st.markdown("#### Files")
            st.dataframe(pd.DataFrame([{
                "file": f["file"],
                "status": STATUS_ICON.get(f["status"], f["status"]) + (" · manual fix" if f.get("manual_override") else ""),
                "converter": f.get("converter", ""),
                "auto-fixed": f["fixed"],
                "open errors": sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "error"),
                "open warnings": sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "warning"),
            } for f in c["files"]]), width="stretch", hide_index=True)
            items = [{"file": f["file"], "line": x["line"], "severity": x["severity"], "what to do": x["message"]}
                     for f in c["files"] for x in f["findings"] if not x["fixed"] and x["severity"] != "info"]
            if items:
                st.markdown("#### Open items")
                st.dataframe(pd.DataFrame(items), width="stretch", hide_index=True)
        if d:
            errs = [{"file": f["file"], "statement": x["n"], "error": x["error"]}
                    for f in d["files"] for x in f["results"] if not x["ok"]]
            if errs:
                st.markdown("#### Deployment errors")
                st.dataframe(pd.DataFrame(errs), width="stretch", hide_index=True)
        if r and r.get("mode") == "quick":
            st.markdown("#### Reconciliation")
            st.dataframe(pd.DataFrame([{
                "table": t["target"], "result": t["status"],
                "details": t.get("error") or ", ".join(f"{ch['check']}: {ch['source']} → {ch['target']}"
                                                       for ch in t.get("checks", []) if not ch["match"]) or "all checks equal",
            } for t in r["tables"]]), width="stretch", hide_index=True)

        report = cfg.output_dir / "report.html"
        if report.exists():
            html = report.read_text(encoding="utf-8")
            st.download_button("⬇ Download report.html", html, file_name=f"{cfg.name}-migration-report.html", mime="text/html")
            with st.expander("Show the full report"):
                st.iframe(html, height=900)  # our own generated report, all values HTML-escaped


# ----------------------------------------------------------------- manual fixes

with tab_fixes:
    state = load_state(cfg)
    conv = state.get("convert")
    if not conv:
        st.info("Run Convert first.")
    else:
        st.caption("Files saved here go into the project's overrides folder and replace the converted file on every run.")
        order = {"needs-fix": 0, "review": 1, "ready": 2}
        files = sorted(conv["files"], key=lambda f: (order.get(f["status"], 3), f["file"]))
        pick = st.selectbox("File", [f["file"] for f in files],
                            format_func=lambda n: f"{n} — {next(STATUS_ICON.get(f['status'], f['status']) for f in files if f['file'] == n)}")
        f = next(x for x in files if x["file"] == pick)
        todo = [x for x in f["findings"] if not x["fixed"] and x["severity"] != "info"]
        for x in todo:
            (st.error if x["severity"] == "error" else st.warning)(f"Line {x['line']}: {x['message']}")
        left, right = st.columns(2)
        with left:
            st.markdown("**Original**")
            orig = Path(f["input"])
            st.code(orig.read_text(encoding="utf-8-sig", errors="replace") if orig.exists() else "(not found)", language="sql")
        with right:
            ovr = h.override_path(ss.project, f["file"], raw.get("overrides", "overrides"))
            final = Path(f["final"])
            if not ovr.exists() and not final.exists():
                st.info("The converted file is not on disk (a convert may be running). Run Convert again, then come back.")
            else:
                start = ovr.read_text(encoding="utf-8-sig") if ovr.exists() else final.read_text(encoding="utf-8-sig")
                st.markdown("**Databricks version** " + ("(manual fix)" if ovr.exists() else "(converted — edit to fix)"))
                edited = st.text_area("Databricks SQL", value=start, height=420, label_visibility="collapsed", key=f"edit-{pick}")
                b1, b2 = st.columns(2)
                if b1.button("💾 Save as manual fix", type="primary"):
                    ovr.parent.mkdir(parents=True, exist_ok=True)
                    ovr.write_text(edited, encoding="utf-8")
                    st.success("Saved. Run Convert again to apply it, then Deploy to check it on Databricks.")
                if ovr.exists() and b2.button("🗑 Remove manual fix"):
                    ovr.unlink()
                    st.success("Removed. The converted version will be used on the next run.")
                    st.rerun()


# ----------------------------------------------------------------- environment

with tab_env:
    st.subheader("Environment check")
    st.caption("Everything WishBridge needs on this computer.")
    if st.button("Check now") or "env" not in ss:
        with st.spinner("Checking..."):
            ss.env = h.check_environment(cfg.profile)
    for chk in ss.env:
        if chk.ok:
            st.success(f"**{chk.name}** — {chk.detail}")
        else:
            st.error(f"**{chk.name}** — {chk.detail}")
            if chk.fix:
                st.code(chk.fix, language="powershell")
