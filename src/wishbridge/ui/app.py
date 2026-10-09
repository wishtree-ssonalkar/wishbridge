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
from wishbridge.staging import read_source
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

SETUP_STATUS = Path.home() / ".wishbridge" / "setup_status.json"


def system_check() -> None:
    """Check this computer; install what the work needs in the background. Never blocks the app."""
    from wishbridge import system

    project_cfg = None
    if project_ok():
        try:
            project_cfg = load_config(h.project_file(ss.project))
        except (ConfigError, ValueError, OSError):
            pass
    needed = system.needed_for(project_cfg)
    key = ",".join(sorted(needed))
    proc = ss.get("setup_proc")
    running = proc is not None and proc.poll() is None
    if ss.get("system_key") != key or ss.get("system_recheck") or (proc is not None and not running and not ss.get("setup_read")):
        ss.pop("system_recheck", None)
        if proc is not None and not running:
            ss.setup_read = True
        ss.system, ss.system_key = system.check(needed), key
        extra = [project_cfg.output_dir / "logs" / "system_check.json"] if project_cfg else []
        system.save_report(system.report(ss.system), *extra)
        attempted = ss.setdefault("setup_attempted", set())
        if not running and not system.ready(ss.system) and key not in attempted:
            attempted.add(key)  # try automatically once per need; the user can retry
            ss.setup_proc, ss.setup_read = system.start_background_install(needed, SETUP_STATUS, extra), False
            running = True
    if running:
        _setup_progress()
        return
    missing = [s for s in ss.system if s.needed and not (s.ok and s.compatible)]
    if not missing:
        st.caption("✅ This computer is ready")
    else:
        st.warning("Missing: " + ", ".join(s.name for s in missing) + ". You can keep working: only steps that "
                   "need it will stop with a message. Details are saved for later.")
    with st.expander("System check", expanded=False):
        for s in ss.system:
            if not s.needed and not s.ok:
                st.markdown(f"➖ **{s.name}** — not needed for this work")
                continue
            mark = "✅" if s.ok and s.compatible else "⚠️" if s.ok else "❌"
            ver = f" `{s.version}`" if s.version else ""
            st.markdown(f"{mark} **{s.name}**{ver} — {s.why}")
            if s.ok and not s.compatible:
                st.caption(f"Version {s.version} is older than the minimum {s.minimum}.")
            if not s.ok:
                st.code(s.manual, language="powershell")
                for line in s.log[-2:]:
                    st.caption((line.strip().splitlines() or [""])[-1][-300:])
        st.caption(f"Record of every check and install: `{system.REPORT}`")
        if st.button("Install missing again" if missing else "Check again", width="stretch"):
            ss.system_recheck = True
            ss.get("setup_attempted", set()).discard(key)
            st.rerun()


@st.fragment(run_every="3s")
def _setup_progress() -> None:
    """Shown while the background install runs; refreshes itself and hands back to the page when done."""
    from wishbridge import system

    proc = ss.get("setup_proc")
    if proc is None or proc.poll() is not None:
        st.rerun()  # finished: re-check and show the result
    rep = system.load_report(SETUP_STATUS) or {}
    st.info(f"⏳ Setting up this computer in the background — {rep.get('current') or 'checking…'} "
            "You can keep working meanwhile.")


with st.sidebar:
    st.markdown(f"### 🌉 Wishtree WishBridge\nData warehouse migration to Databricks · v{__version__}")
    system_check()
    mode = st.radio("Project", ["Open a folder", "Open a package", "Create new"], horizontal=True,
                    label_visibility="collapsed")
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
    elif mode == "Open a package":
        st.caption("A zip saved with *Assess and save everything* (or `wishbridge package`) on any computer. "
                   "It becomes a project here, ready to review and fix.")
        pkg_up = st.file_uploader("Package (.zip)", type=["zip"], key="pkg_upload")
        pkg_parent = st.text_input("Create the project in", value=r"C:\migrations", key="pkg_parent")
        if pkg_up is not None and st.button("Open package", width="stretch"):
            from wishbridge.package import open_package

            tmp = Path(pkg_parent).expanduser() / ".wishbridge-incoming" / Path(pkg_up.name).name
            try:
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(pkg_up.getvalue())
                dest = open_package(tmp, pkg_parent)
                tmp.unlink(missing_ok=True)
                ss.project, ss.workspace = str(dest), None
                st.rerun()
            except (ValueError, OSError, ConfigError) as e:
                st.error(str(e))
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
        default_parent = r"C:\migrations"
        name = c2.text_input("Project name", value=discover.suggested_name(info.path, default_parent))
        parent = st.text_input("Create projects in", value=default_parent)
        split = False
        if info.databases:
            dbs = ", ".join(f"{n} ({c} files)" for n, _, c in info.databases)
            st.markdown(f"The code holds **{len(info.databases)} databases**: {dbs}.")
            split = st.radio("Projects", [True, False], horizontal=True,
                             format_func=lambda s: "One project per database (recommended)" if s else "One project for everything")
        if st.form_submit_button("Create project" + ("s" if info.databases and split else ""), type="primary"):
            try:
                made = discover.create_projects_for_code(info, parent, name, source, split)
            except (ValueError, OSError, ConfigError) as e:
                existing = Path(parent).expanduser() / name.strip()
                if (existing / "project.yml").exists():
                    ss.existing_project = str(existing.resolve())
                st.error(str(e))
            else:
                ss.created_projects = [str(p) for p in made]
                ss.project, ss.workspace = str(made[0]), None
                ss.pop("code_info", None)
                st.rerun()
    if ss.get("existing_project"):
        if st.button(f"📂 Open the existing project {Path(ss.existing_project).name} instead", type="primary"):
            ss.project, ss.workspace = ss.pop("existing_project"), None
            ss.pop("code_info", None)
            st.rerun()
    if st.button("Cancel"):
        ss.pop("code_info", None)
        ss.pop("existing_project", None)
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

TABS = ["1 · Settings", "2 · Code", "3 · Run", "4 · Results", "5 · Fix code (later)"]
if ss.get("go_tab"):
    ss["tab"] = ss.pop("go_tab")  # a Next button asked for the next tab
tab_settings, tab_code, tab_run, tab_results, tab_fixes = st.tabs(TABS, key="tab", on_change="rerun")


def next_button(label: str, tab: str, key: str) -> None:
    if st.button(label, type="primary", key=key):
        ss.go_tab = tab
        st.rerun()


# ----------------------------------------------------------------- settings

with tab_settings:
    dbx = raw.setdefault("databricks", {})
    data = raw.setdefault("data", {})
    ai = raw.setdefault("autofix", {})

    phase_labels = {"assessment": "Assessment - offline: analyze and convert the code",
                    "migration": "Migration - deploy to Databricks, copy the data, reconcile"}

    def _save_phase() -> None:
        new_ = dict(raw)
        new_["phase"] = ss.phase_radio
        h.save_raw(ss.project, new_)

    cur_phase = str(raw.get("phase") or "migration").lower()
    phase = st.radio("Phase", list(phase_labels), format_func=phase_labels.get, horizontal=True, key="phase_radio",
                     index=list(phase_labels).index(cur_phase) if cur_phase in phase_labels else 1, on_change=_save_phase,
                     help="Assessment: nothing is sent to Databricks or the client's database. Switch to Migration for "
                          "the visit where the code is deployed and the data copied. Changing it saves right away.")
    offline = phase == "assessment"

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

    with st.expander("Schema names in the converted code (optional)"):
        st.caption("Rename source schemas in the converted code, e.g. `dbo` -> `main.sales`. WishBridge fills in a "
                   "sensible default; adjust it once the Databricks catalog is known.")
        map_df = st.data_editor(pd.DataFrame(h.schema_map_rows(raw) or [{"source_schema": "", "target": ""}]),
                                num_rows="dynamic", width="stretch", key="schema_map",
                                column_config={"source_schema": "Source schema", "target": "Target catalog.schema"})

    # Values kept as they are while the migration settings are hidden.
    profile, warehouse = dbx.get("profile", "DEFAULT"), str(dbx.get("warehouse_id") or "")
    catalog, schema = dbx.get("catalog", "main"), dbx.get("schema", "wishbridge")
    scope = str(raw.get("scope") or "all").lower()
    method, source_catalog = data.get("method", "federation"), data.get("source_catalog", "")
    files_root, file_format, load_mode = data.get("files_root", ""), data.get("file_format", "PARQUET"), data.get("mode", "append")
    tables_df = pd.DataFrame(h.table_rows(raw) or [{"source": "", "target": ""}])
    ws, chosen_host, project_host = None, "", str(dbx.get("host") or "")

    if offline:
        st.info("Databricks, source database and data-copy settings are not needed for the assessment. "
                "They appear here when you switch the phase to **Migration**.")

    if not offline:
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
        scope_labels = {"all": "Everything", "recommended": "Only what belongs on Databricks (fit check)"}
        scope = st.selectbox("What to deploy", list(scope_labels), format_func=scope_labels.get,
                             index=list(scope_labels).index(scope) if scope in scope_labels else 0,
                             help="The fit check (run with Analyze) marks application logic to keep on the source "
                                  "and objects Databricks does not need. 'Only what belongs' leaves those out of deploy.")

        if "prod" in f"{catalog}.{schema}".lower():
            st.warning("This looks like a production schema. WishBridge will refuse to deploy there.")

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

    if st.button("Next: add the code →", type="primary", help="Saves the settings and opens the Code tab."):
        new = dict(raw)
        new["source"] = source
        if transpiler == "auto":
            new.pop("transpiler", None)  # automatic is the default
        else:
            new["transpiler"] = transpiler
        new["scope"] = scope
        new["phase"] = phase
        host = (ws or {}).get("host") or chosen_host or project_host
        new["databricks"] = {**dbx, "profile": profile, "host": host, "warehouse_id": warehouse,
                             "catalog": catalog, "schema": schema}
        new["schema_map"] = h.rows_to_schema_map(map_df.to_dict("records"))
        old_cat = str(dbx.get("catalog") or "")
        if old_cat and catalog != old_cat:  # schema names made from the old catalog follow the new one
            new["schema_map"] = {k: (f"{catalog}.{v[len(old_cat) + 1:]}" if str(v).startswith(old_cat + ".") else v)
                                 for k, v in new["schema_map"].items()}
        new["data"] = {**data, "method": method, "source_catalog": source_catalog, "files_root": files_root,
                       "file_format": file_format, "mode": load_mode, "tables": h.rows_to_tables(tables_df.to_dict("records"))}
        new["autofix"] = {"ai": ai_on, "model": model}
        new["estimate"] = {**est, "hours_per_issue": hpi}
        try:
            h.save_raw(ss.project, new)
            ss.pop("pending_profile", None)
            ss.go_tab = TABS[1]
            st.rerun()
        except (ConfigError, KeyError, ValueError) as e:
            st.error(f"Not saved: {e}")


# ----------------------------------------------------------------- code

with tab_code:
    from wishbridge import inventory as inv_mod

    st.subheader("Code overview")
    ov = load_state(cfg).get("overview")
    if ov:
        st.markdown(f"**{ov['files']} files** · {ov['lines']:,} lines · {ov['tables']} tables · {ov['procedures']} "
                    f"procedures · {ov['views']} views · {ov['functions']} functions"
                    + (f" · schemas: {', '.join(ov['schemas'])}" if ov.get("schemas") else ""))
        if ov.get("headline"):
            st.caption(ov["headline"])
        ovf = Path(ov["file"])
        if ovf.is_file():
            st.download_button("📘 Open the code overview (structure, tables, procedures, data flows)",
                               ovf.read_bytes(), file_name=ovf.name, mime="text/html")
    else:
        st.caption("Run Analyze to get a description of the code base: structure, tables, procedures, data flows "
                   "and what needs attention.")

    with st.expander("Source database inventory — no connection needed", expanded=False):
        st.markdown("Get the list of tables, columns, row counts and sizes **without connecting** to the client's "
                    "database: give their DBA this read-only query, and import the CSV they send back.")
        db_types = list(inv_mod.QUERIES)
        default_db = (cfg.source_db or {}).get("type") or inv_mod.DEFAULT_DB.get(cfg.source.key, "sqlserver")
        db = st.selectbox("Database type", db_types, index=db_types.index(default_db) if default_db in db_types else 0)
        fname, text = inv_mod.script(cfg, db)
        st.download_button("Download the query for the DBA", text, file_name=fname, mime="text/plain")
        up = st.file_uploader("Import the DBA's CSV", type=["csv", "txt"], key="inventory_csv")
        if up is not None and st.button("Import inventory"):
            tmp = cfg.out("inventory", "upload_" + Path(up.name).name)
            tmp.write_bytes(up.getvalue())
            try:
                inv_mod.import_file(cfg, tmp)
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        inv = load_state(cfg).get("inventory")
        if inv:
            s = inv["summary"]
            st.success(f"{s['tables']} tables · {s['columns']} columns · {s['rows']:,} rows · {s['size_mb']:,} MB "
                       f"(imported {inv['imported_at'].replace('T', ' ')})")
            st.dataframe(pd.DataFrame([{"table": f"{t['schema']}.{t['table']}", "rows": t["rows"], "size (MB)": t["size_mb"],
                                        "columns": len(t["columns"])} for t in inv["tables"]]), width="stretch", hide_index=True)
            if st.button("Use these tables for the data copy"):
                new = dict(raw)
                new.setdefault("data", {})["tables"] = inv_mod.table_list(inv)
                h.save_raw(ss.project, new)
                st.success("Saved to Settings > Data. Load targets follow the schema map.")
                st.rerun()
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
        next_button("Next: run →", TABS[2], "next-code")
        st.dataframe(pd.DataFrame([{"file": str(p.relative_to(cfg.input_dir)), "size (KB)": round(p.stat().st_size / 1024, 1)}
                                   for p in files]), width="stretch", hide_index=True)
        pick = st.selectbox("Preview", [str(p.relative_to(cfg.input_dir)) for p in files])
        st.code(read_source(cfg.input_dir / pick)[:20000], language="sql")


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
    offline = cfg.phase == "assessment"
    if offline:
        st.subheader("First run: assess the code and save everything")
        st.markdown(
            "One button does it all, on this computer only (nothing goes to Databricks or the client's database):\n"
            "1. **Analyze** the code: size, complexity, effort, and whether it really is a data warehouse.\n"
            "2. **Convert** it to Databricks.\n"
            "3. Write the **report** and the **code overview** (how the system is built: structure, tables, "
            "procedures, data flows).\n"
            "4. Save **everything in one zip**: the code before and after conversion, the report, the code overview "
            "and the list of open items.\n\n"
            "Nothing needs to be fixed now. Open the zip later (sidebar > *Open a package*) and fix the code in the "
            "**Fix code (later)** tab.")
        if st.button("▶ Assess and save everything", type="primary"):
            from wishbridge.analysis import run_analyze
            from wishbridge.convert import run_convert
            from wishbridge.fit import run_fit
            from wishbridge.overview import build_overview
            from wishbridge.package import build_package
            from wishbridge.report import build_report

            # The visit is about collecting everything: a step that fails (e.g. a converter that could not be
            # installed here) is noted and skipped; the rest still runs and the zip is always made.
            steps = [
                ("Describe the code (fit check and code overview)", lambda: (run_fit(cfg), build_overview(cfg))[0],
                 lambda r: f"{len(r['objects'])} objects · {r['verdict']}"),
                ("Analyze (LakeBridge)", lambda: run_analyze(cfg),
                 lambda r: f"{len(r['programs'])} files, estimate {r['estimated_hours_baseline']} h"),
                ("Convert", lambda: run_convert(cfg, False),
                 lambda r: f"{r['summary']['ready']} ready, {r['summary']['review']} review, {r['summary']['needs_fix']} need fixes"),
                ("Report", lambda: build_report(cfg), lambda r: "report.html written"),
                ("Save everything in one zip", lambda: build_package(cfg), lambda r: Path(r).name),
            ]
            skipped = [label for label, fn, summary in steps if not run_step(label, fn, summary)]
            if skipped:
                st.warning("Saved what could be done. Not done: " + ", ".join(skipped) + ". Run it again later "
                           "(for example after the System check has installed what was missing) - nothing is lost.")
            pkgs = sorted((cfg.output_dir / "review_package").glob("*.zip"), key=lambda q: q.stat().st_mtime)
            ss.review_package = str(pkgs[-1]) if pkgs else ""
        if ss.get("review_package") and Path(ss.review_package).is_file():
            pkg = Path(ss.review_package)
            st.success(f"Everything is saved in **{pkg.name}** ({pkg.stat().st_size // 1024:,} KB), also kept at `{pkg}`.")
            st.download_button("⬇ Download the zip", pkg.read_bytes(), file_name=pkg.name, mime="application/zip",
                               type="primary")
    else:
        st.subheader("Run the migration")
        c1, c2, c3 = st.columns(3)
        with c1:
            do_analyze = st.checkbox("1. Analyze the code", value=True)
            do_convert = st.checkbox("2. Convert to Databricks SQL", value=True)
            use_ai = st.checkbox("…with Claude suggestions", value=cfg.ai_enabled and not offline, disabled=not do_convert or offline,
                                 help="Not in the assessment phase: it sends code to the Claude API.")
        with c2:
            do_deploy = st.checkbox("3. Deploy to the test schema", value=False, disabled=offline)
            recreate = st.checkbox("…rebuild objects that already exist", value=False, disabled=not do_deploy)
            do_load = st.checkbox("4. Copy the data", value=False, disabled=offline)
            execute = st.checkbox("…really copy (otherwise only write the plan)", value=False, disabled=not do_load)
        with c3:
            do_reconcile = st.checkbox("5. Reconcile the data", value=False, disabled=offline)
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
        if state.get("convert"):
            if st.button("📦 Save everything in one zip"):
                from wishbridge.package import build_package

                try:
                    ss.review_package = str(build_package(cfg))
                except STEP_ERRORS as e:
                    st.error(str(e))
            if ss.get("review_package") and Path(ss.review_package).is_file():
                pkg = Path(ss.review_package)
                st.download_button(f"Download {pkg.name}", pkg.read_bytes(), file_name=pkg.name, mime="application/zip")
                st.caption("Report, code overview, open items, and the code before and after conversion in one zip.")
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
    st.subheader("Fix code (later)")
    st.markdown(
        "Some files cannot be converted completely by a tool. This tab is where the team finishes them - "
        "**later, at Wishtree, not at the client**.\n\n"
        "1. Pick a file. Files that need a fix come first; the red and yellow boxes say what to change and on which line.\n"
        "2. The client's **original** code is on the left, the **Databricks version** on the right. Edit the right side.\n"
        "3. Press **Save as manual fix**. Your version is kept in the project's `overrides` folder and used on every "
        "later run, so a new conversion never overwrites it.")
    if cfg.phase == "assessment" and not ss.get("fixing_now"):
        st.info("At the client you don't need this tab: *Run > Assess and save everything* puts the code and the list "
                "of open items in one zip. Open that zip at Wishtree (sidebar > *Open a package*) and fix the code there.")
        if conv and st.button("I want to fix files now"):
            ss.fixing_now = True
            st.rerun()
    elif not conv:
        st.info("Run Convert first.")
    else:
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
            st.code(read_source(orig) if orig.exists() else "(not found)", language="sql")
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
