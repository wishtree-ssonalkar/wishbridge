"""Wishtree WishBridge UI. Started by `wishbridge ui` (Streamlit)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import yaml
import streamlit as st

from wishbridge import __version__
from wishbridge.config import (CONVERTER_DIALECTS, PURPOSE, SCOPE_ROWS, SOURCES, ConfigError, converter_summary,
                               load_config, looks_like_prod)
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


def system_ready() -> bool:
    """Projects can be opened once the computer has been checked and is ready (or the user chose to continue)."""
    from wishbridge import system

    return bool(ss.get("system")) and (system.ready(ss.system) or ss.get("continue_anyway", False))


def system_check() -> None:
    """Step 0: check this computer on request, install what is missing on request, then allow opening a project."""
    from wishbridge import system

    proc = ss.get("setup_proc")
    if proc is not None and proc.poll() is None:
        _setup_progress()
        return
    if proc is not None:  # an install just finished: check again and keep the record
        ss.setup_proc = None
        ss.system = system.check()
        system.save_report(system.report(ss.system))
    if not ss.get("system"):
        st.info("**Step 0 · System check** — check that this computer has what WishBridge needs before opening a project.")
        if st.button("🔍 Check this computer", type="primary", width="stretch"):
            with st.spinner("Checking..."):
                ss.system = system.check()
                system.save_report(system.report(ss.system))
            st.rerun()
        return
    missing = [s for s in ss.system if not (s.ok and s.compatible)]
    if not missing:
        st.success("✅ This computer is ready")
    else:
        st.warning("Missing: " + ", ".join(s.name for s in missing))
    with st.expander("System check details", expanded=bool(missing)):
        for s in ss.system:
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
    if missing:
        if st.button("⬇ Install missing", type="primary", width="stretch",
                     help="Installs in the background (winget / Homebrew / Databricks CLI); the progress shows here."):
            ss.setup_proc = system.start_background_install({s.key for s in missing}, SETUP_STATUS)
            ss.install_tried = True
            st.rerun()
        if ss.get("install_tried"):
            st.checkbox("Continue anyway - steps that need the missing tools will be skipped", key="continue_anyway")
    if st.button("Check again", width="stretch"):
        ss.system = system.check()
        system.save_report(system.report(ss.system))
        st.rerun()


@st.fragment(run_every="3s")
def _setup_progress() -> None:
    """Shown while the background install runs; refreshes itself and hands back to the page when done."""
    from wishbridge import system

    proc = ss.get("setup_proc")
    if proc is None or proc.poll() is not None:
        st.rerun()  # finished: check again and show the result
    rep = system.load_report(SETUP_STATUS) or {}
    st.info(f"⏳ Installing in the background — {rep.get('current') or 'starting…'}")


with st.sidebar:
    st.markdown(f"### 🌉 Wishtree WishBridge\nData warehouse migration to Databricks · v{__version__}")
    system_check()
    locked = not system_ready()
    if locked:
        st.caption("🔒 Opening a project is available once the system check is done and the computer is ready.")
    mode = st.radio("Project", ["Open a folder", "Open a package", "Create new"], horizontal=True,
                    label_visibility="collapsed", disabled=locked)
    if mode == "Open a folder":
        folder = st.text_input("Project or client code folder", value=ss.project, disabled=locked,
                               placeholder=r"C:\migrations\acme-dw  or  C:\client-repo",
                               help="A WishBridge project opens directly. Any other folder of SQL/ETL code (a repository, "
                                    "a Visual Studio database project, an export) gets a project created for it.")
        if st.button("Open", width="stretch", disabled=locked or not folder.strip()):
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
                if st.button(f"📂 {Path(p).name}", key=f"open-{p}", width="stretch", disabled=locked):
                    ss.project, ss.workspace = p, None
                    st.rerun()
    elif mode == "Open a package":
        st.caption("A zip saved with *Assess and save everything* (or `wishbridge package`) on any computer. "
                   "It becomes a project here, ready to review and fix.")
        pkg_up = st.file_uploader("Package (.zip)", type=["zip"], key="pkg_upload", disabled=locked)
        pkg_parent = st.text_input("Create the project in", value=r"C:\migrations", key="pkg_parent", disabled=locked)
        if st.button("Open package", width="stretch", disabled=locked or pkg_up is None or not pkg_parent.strip()):
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
        name = st.text_input("Project name :red[*]", placeholder="acme-dw", disabled=locked, key="new_name")
        source = st.selectbox("Source system :red[*]", list(SOURCES), format_func=lambda k: SOURCES[k].label, disabled=locked)
        parent = st.text_input("Create in folder :red[*]", value=r"C:\migrations", disabled=locked, key="new_parent")
        if st.button("Create project", width="stretch", disabled=locked or not name.strip() or not parent.strip()):
            try:
                ss.project = str(h.create_project(parent, name, source))
                ss.workspace = None
                st.rerun()
            except (ValueError, OSError) as e:
                st.error(str(e))
    if project_ok() and not locked:
        st.success(f"Open: {Path(ss.project).name}")
        st.caption(ss.project)


st.title("Wishtree WishBridge")
st.caption(f"{PURPOSE} Assess, convert, deploy, copy the data and prove it matches.")


def show_scope() -> None:
    st.markdown("**What WishBridge migrates**")
    st.table(pd.DataFrame(SCOPE_ROWS, columns=["What the client has", "Migrate it?"]).set_index("What the client has"))


def show_welcome() -> None:
    st.markdown(
        "**How it works:** 1. open the warehouse code · 2. fill in the settings · 3. analyze (is it a warehouse? how big?) · "
        "4. convert and deploy · 5. copy the data and reconcile · 6. share the report."
    )
    show_scope()


if not system_ready():
    st.info("Start with **Step 0 · System check** in the sidebar: *Check this computer*, and *Install missing* if "
            "anything is not there. Then open the client's warehouse code (a project folder, a repository, a database "
            "project or an ETL export) or create a new project.")
    show_welcome()
    st.stop()

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
    with st.container(border=True):
        c1, c2 = st.columns(2)
        src_keys = list(SOURCES)
        source = c1.selectbox("Source system :red[*]", src_keys, index=src_keys.index(det.source),
                              format_func=lambda k: SOURCES[k].label)
        default_parent = r"C:\migrations"
        name = c2.text_input("Project name :red[*]", value=discover.suggested_name(info.path, default_parent), key="code_name")
        parent = st.text_input("Create projects in :red[*]", value=default_parent, key="code_parent")
        split = False
        if info.databases:
            dbs = ", ".join(f"{n} ({c} files)" for n, _, c in info.databases)
            st.markdown(f"The code holds **{len(info.databases)} databases**: {dbs}.")
            split = st.radio("Projects", [True, False], horizontal=True,
                             format_func=lambda s: "One project per database (recommended)" if s else "One project for everything")
        if st.button("Create project" + ("s" if info.databases and split else ""), type="primary",
                     disabled=not name.strip() or not parent.strip()):
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
    show_welcome()
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

from wishbridge import runner

LABELS = {"settings": "Settings", "code": "Code", "run": "Run", "results": "Results", "fix": "Fix code",
          "send": "Share the zip"}
from_package = (Path(ss.project) / "opened_from_package.json").exists()
if cfg.phase == "assessment":
    # At the client: everything ends up in the zip, so the last step is sending it. Fixing happens later,
    # in the project opened from that zip.
    ORDER = ["settings", "code", "run"] + (["fix"] if from_package else []) + ["send"]
else:
    ORDER = ["settings", "code", "run", "results", "fix"]
state_now = load_state(cfg)
running = runner.is_running(cfg)
has_code = bool(h.input_files(ss.project, raw.get("input", "input")))
has_results = any(k in state_now for k in ("analyze", "convert", "fit", "deploy", "reconcile"))
packages = sorted((cfg.output_dir / "review_package").glob("*.zip"), key=lambda q: q.stat().st_mtime)
done = {
    "settings": bool(ss.get(f"settings_done:{ss.project}")) or has_results,  # confirmed with Next (or used before)
    "code": has_code,
    "run": has_results and not running,
    "results": has_results and not running,
    "fix": False,
    "send": bool(ss.get(f"sent:{ss.project}")),
}


def reachable(key: str) -> bool:
    if running:
        return key == "run"  # while a run is going, stay on Run to follow it
    if key == "fix":
        return "convert" in state_now and all(done[k] for k in ("settings", "code"))
    if key == "send":
        return bool(packages) and all(done[k] for k in ("settings", "code", "run"))
    return all(done[k] for k in ORDER[:ORDER.index(key)])


if ss.get("step") not in ORDER or ss.get("step_project") != ss.project:
    ss.step_project = ss.project
    ss.step = next((k for k in ORDER if not done[k]), ORDER[-1])  # first unfinished step
if ss.get("go_step") is not None:
    _go = ss.pop("go_step")
    if _go in ORDER:
        ss.step = _go
if running:
    ss.step = "run"
if not reachable(ss.step):
    ss.step = [k for k in ORDER if reachable(k)][-1]
STEP = ss.step

cols = st.columns(len(ORDER))
for i, (col, key) in enumerate(zip(cols, ORDER)):
    mark = "✓ " if done[key] else ""
    if col.button(f"{i + 1} · {mark}{LABELS[key]}", key=f"step-{key}", width="stretch", disabled=not reachable(key),
                  type="primary" if key == STEP else "secondary"):
        ss.step = key
        st.rerun()
if running:
    st.caption("🔒 A run is going in the background - the other steps open when it has finished.")
st.divider()


def next_button(label: str, step: str, key: str) -> None:
    if st.button(label, type="primary", key=key):
        ss.go_step = step
        st.rerun()


def required(message: str) -> None:
    """A red note right under a required field that is missing or wrong."""
    st.markdown(f":red[{message}]")


def settings_problems(offline: bool, v: dict) -> list[str]:
    """What must be filled in before leaving Settings. Assessment needs only the source system; the migration
    phase also needs the Databricks target and the source data connection."""
    from wishbridge.config import looks_like_prod

    problems = []
    if not v.get("source"):
        problems.append("Source system")
    if offline:
        return problems
    login = (v.get("by_name") or {}).get(v.get("profile"))
    if not login:
        problems.append("Workspace login: choose a saved login or sign in to the client's workspace")
    elif login.get("valid") is False:
        problems.append("Workspace login has expired: sign in again")
    if not str(v.get("catalog") or "").strip():
        problems.append("Catalog")
    schema_ = str(v.get("schema") or "").strip()
    if not schema_:
        problems.append("Test schema")
    elif looks_like_prod(f"{v.get('catalog')}.{schema_}"):
        problems.append("Test schema: it looks like production - use a test schema")
    if v.get("method") == "files":
        if not str(v.get("files_root") or "").strip():
            problems.append("Data to copy: the files folder (volume path) the exported tables are in")
    elif not str(v.get("source_catalog") or "").strip():
        problems.append("Source database: create the connection (Source database section) or enter the source catalog")
    tables_ = v.get("tables_df")
    rows = h.rows_to_tables(tables_.to_dict("records")) if tables_ is not None else []
    if not rows:
        problems.append("Data to copy: at least one table (use the inventory or List tables)")
    return problems


# ----------------------------------------------------------------- settings

if STEP == "settings":
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
        source = st.selectbox("Source system :red[*]", src_keys, index=src_keys.index(cfg.source.key),
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
            profile = st.selectbox("Workspace login :red[*]", names, index=names.index(cur_profile), format_func=profile_label)
            _login = by_name.get(profile)
            if not _login:
                required("Required: choose a saved login, or sign in to the client's workspace below.")
            elif _login.get("valid") is False:
                required("This login has expired: sign in again below.")
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
            new_host = n1.text_input("Workspace URL :red[*]", placeholder="https://adb-1234567890.12.azuredatabricks.net")
            new_profile = n2.text_input("Profile name :red[*]", placeholder="CLIENT_ACME")
            n3.write("")
            if new_host.strip() and not new_host.strip().lower().startswith("https://"):
                required("The workspace URL starts with https://")
            if n3.button("Sign in", disabled=not new_host.strip().lower().startswith("https://") or not new_profile.strip()):
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
                catalog = st.selectbox("Catalog :red[*]", cats if cur_cat in cats else [cur_cat] + cats,
                                       index=(cats if cur_cat in cats else [cur_cat] + cats).index(cur_cat))
            else:
                catalog = st.text_input("Catalog :red[*]", value=cur_cat)
            if not str(catalog or "").strip():
                required("Required: the Databricks catalog.")
        with c3:
            schema = st.text_input("Test schema :red[*]", value=dbx.get("schema", "wishbridge"))
            if not schema.strip():
                required("Required: the test schema.")
        scope_labels = {"all": "Everything", "recommended": "Only what belongs on Databricks (fit check)"}
        scope = st.selectbox("What to deploy", list(scope_labels), format_func=scope_labels.get,
                             index=list(scope_labels).index(scope) if scope in scope_labels else 0,
                             help="The fit check (run with Analyze) marks application logic to keep on the source "
                                  "and objects Databricks does not need. 'Only what belongs' leaves those out of deploy.")

        if looks_like_prod(f"{catalog}.{schema}"):
            required("This looks like a production schema - use a test schema. WishBridge will not deploy there.")

        # --- Source database: where the data lives. Optional - only needed to copy the data.
        st.markdown("**Source database** :red[*] — where the data is copied from (or choose *files* under Data to copy). "
                    "WishBridge creates a Lakehouse Federation connection in Databricks; the password goes into Databricks "
                    "secrets, never into project.yml.")
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
            host = d2.text_input("Server (host) :red[*]", value=sdb.get("host", ""), placeholder="sqlprod01.client.com", key="sdb_host")
            port = d3.number_input("Port", value=int(sdb.get("port") or dbt.port), step=1, key="sdb_port")
            d1, d2, d3 = st.columns(3)
            database = d1.text_input(dbt.catalog_label + (" :red[*]" if dbt.catalog_option else ""), value=sdb.get("database", ""), key="sdb_db",
                                     disabled=dbt.catalog_option is None,
                                     help=None if dbt.catalog_option else f"Not needed for {dbt.label}: the whole server is exposed.")
            user = d2.text_input("User (read-only is enough) :red[*]", value=sdb.get("user", ""), key="sdb_user")
            password = d3.text_input("Password :red[*]", type="password", key="sdb_pw",
                                     help="Sent straight to Databricks secrets; not saved anywhere else.")
            extra = {opt: st.text_input(label + " :red[*]", value=(sdb.get("options") or {}).get(opt, ""), key=f"sdb_{opt}")
                     for opt, label in dbt.extra}
            conn_missing = [n for n, v in (("server", host), ("user", user), ("password", password)) if not str(v).strip()]
            if dbt.catalog_option and not database.strip():
                conn_missing.append(dbt.catalog_label.lower())
            conn_missing += [label.lower() for opt, label in dbt.extra if not str(extra.get(opt, "")).strip()]
            connected = bool(sdb.get("catalog"))
            b1, b2, b3 = st.columns(3)
            if conn_missing:
                st.caption(":red[To create the connection, fill in: " + ", ".join(conn_missing) + ".]")
            if b1.button("🔌 Re-create connection" if connected else "🔌 Create connection", width="stretch",
                         disabled=bool(conn_missing)):
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

        st.markdown("**Data to copy** :red[*]")
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
                    source_catalog = st.selectbox("Source catalog (federation) :red[*]", opts, index=opts.index(cur_src))
                else:
                    source_catalog = st.text_input("Source catalog (federation) :red[*]", value=cur_src)
                if not str(source_catalog or "").strip():
                    required("Required: create the connection in *Source database* above, or enter the source catalog.")
                files_root, file_format = data.get("files_root", ""), data.get("file_format", "PARQUET")
            else:
                files_root = st.text_input("Files root (volume path) :red[*]", value=data.get("files_root", ""),
                                           placeholder="/Volumes/main/landing/acme")
                if not files_root.strip():
                    required("Required: the volume folder the exported tables are in.")
                file_format = st.selectbox("File format", ["PARQUET", "CSV", "JSON", "AVRO", "ORC"],
                                           index=["PARQUET", "CSV", "JSON", "AVRO", "ORC"].index(str(data.get("file_format", "PARQUET")).upper()))
                source_catalog = data.get("source_catalog", "")
        with c3:
            load_mode = st.selectbox("Load mode", ["append", "overwrite"], index=["append", "overwrite"].index(data.get("mode", "append")))
        tables_df = st.data_editor(pd.DataFrame(h.table_rows(raw) or [{"source": "", "target": ""}]),
                                   num_rows="dynamic", width="stretch", key="tables",
                                   column_config={"source": "Source table (schema.table) *",
                                                  "target": "Target (optional catalog.schema.table)"})
        if not h.rows_to_tables(tables_df.to_dict("records")):
            required("Required: at least one table to copy (type it, or use the inventory in Code / *List tables* above).")

    with st.expander("AI suggestions and estimates"):
        ai_on = st.checkbox("Ask Claude for fix suggestions during convert (needs ANTHROPIC_API_KEY; sends code, not data)",
                            value=bool(ai.get("ai", False)))
        model = st.text_input("Claude model", value=ai.get("model", "claude-opus-5-5"))
        est = raw.get("estimate") or {}
        hpi = st.number_input("Hours per open item", value=float(est.get("hours_per_issue", 0.5)), step=0.25)

    problems = settings_problems(offline, locals())
    if problems:
        st.markdown(":red[Fill in the fields marked * (see the red notes above) before going on.]")
    if st.button("Next: add the code →", type="primary", help="Saves the settings and opens the Code tab.",
                 disabled=bool(problems)):
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
            ss[f"settings_done:{ss.project}"] = True
            ss.go_step = "code"
            st.rerun()
        except (ConfigError, KeyError, ValueError) as e:
            st.error(f"Not saved: {e}")


# ----------------------------------------------------------------- code

if STEP == "code":
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
        next_button("Next: run →", "run", "next-code")
        st.dataframe(pd.DataFrame([{"file": str(p.relative_to(cfg.input_dir)), "size (KB)": round(p.stat().st_size / 1024, 1)}
                                   for p in files]), width="stretch", hide_index=True)
        pick = st.selectbox("Preview", [str(p.relative_to(cfg.input_dir)) for p in files])
        st.code(read_source(cfg.input_dir / pick)[:20000], language="sql")


# ----------------------------------------------------------------- run

ICON = {"pending": "⏸", "running": "⏳", "done": "✅", "failed": "❌", "skipped": "➖"}


def show_run_status(status: dict) -> None:
    for s in status.get("steps", []):
        line = f"{ICON.get(s['state'], '•')} **{s['label']}**"
        if s.get("summary"):
            line += f" — {s['summary']}"
        if s["state"] == "running":
            line += " — working…"
        st.markdown(line)
        if s.get("error"):
            st.caption(f"{'Skipped' if status.get('keep_going') else 'Stopped'}: {s['error']}")


@st.fragment(run_every="2s")
def run_progress() -> None:
    """Live progress of the background run; when it ends the whole page refreshes (step bar unlocks)."""
    status = runner.read_status(cfg) or {}
    if status.get("state") != "running":
        st.rerun()
    st.info("⏳ Running in the background. You can wait here; the steps above unlock when it is finished.")
    show_run_status(status)


if STEP == "run":
    offline = cfg.phase == "assessment"
    status = runner.read_status(cfg)
    if runner.is_running(cfg):
        st.subheader("Running…")
        run_progress()
    elif offline:
        st.subheader("First run: assess the code and save everything")
        st.markdown(
            "One button does it all, on this computer only (nothing goes to Databricks or the client's database):\n"
            "1. **Describe** the code: what is there and whether it really is a data warehouse.\n"
            "2. **Analyze** it: size, complexity and effort.\n"
            "3. **Convert** it to Databricks.\n"
            "4. Write the **report** and the **code overview**.\n"
            "5. Save **everything in one zip**: the code before and after conversion, the report, the code overview "
            "and the list of open items.\n\n"
            "Nothing needs to be fixed now. A step that cannot run here is noted and skipped; the rest still runs.")
        if st.button("▶ Assess and save everything", type="primary"):
            runner.start(cfg, runner.ASSESSMENT_STEPS, {}, keep_going=True)
            st.rerun()
    else:
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
        chosen = [n for n, on in (("analyze", do_analyze), ("convert", do_convert), ("deploy", do_deploy),
                                  ("load", do_load), ("reconcile", do_reconcile), ("report", do_report)) if on]
        if st.button("▶ Start", type="primary", disabled=not chosen):
            runner.start(cfg, chosen, {"ai": use_ai, "recreate": recreate, "execute": execute}, keep_going=False)
            st.rerun()
    if status and status.get("state") in ("done", "interrupted") and not runner.is_running(cfg):
        st.markdown("#### Last run")
        if status["state"] == "interrupted":
            st.warning("The last run stopped before it finished (the app or computer was closed). Start it again.")
        show_run_status(status)
        pkg = Path(status.get("package") or "")
        if status.get("package") and pkg.is_file():
            st.success(f"Everything is saved in **{pkg.name}** ({pkg.stat().st_size // 1024:,} KB), also kept at `{pkg}`.")
            st.download_button("⬇ Download the zip", pkg.read_bytes(), file_name=pkg.name, mime="application/zip")
        if load_state(cfg):
            if cfg.phase == "assessment":
                next_button("Next: share the zip →", "send", "next-run")
            else:
                next_button("Next: see the results →", "results", "next-run")


# ----------------------------------------------------------------- send

if STEP == "send":
    from wishbridge import mailer

    st.subheader("Share the zip")
    if not packages:
        st.info("No zip yet - run *Assess and save everything* first.")
    else:
        pkg = packages[-1]
        size_mb = pkg.stat().st_size / (1024 * 1024)
        st.markdown("Everything from this visit is in **one zip file**. WishBridge does not send it anywhere: "
                    "the client's team shares it with Wishtree the way they prefer (e-mail, OneDrive, SharePoint...).")
        st.markdown(f"**File:** `{pkg.name}` · {size_mb:.1f} MB  \n**Location:** `{pkg}`")
        b1, b2 = st.columns(2)
        if b1.button("📂 Show the zip in its folder"):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(pkg)])
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(pkg.parent)])
        b2.download_button("⬇ Download the zip", pkg.read_bytes(), file_name=pkg.name, mime="application/zip")

        st.markdown("**What is inside**")
        st.table(pd.DataFrame(mailer.CONTENTS, columns=["In the zip", "What it is"]).set_index("In the zip"))

        st.markdown("**Who to send it to**")
        share = raw.get("share") or {}
        c1, c2 = st.columns(2)
        to_text = c1.text_input("Send to (Wishtree)", value=", ".join(share.get("to", [])),
                                placeholder="you@wishtreetech.com")
        cc_text = c2.text_input("CC (optional)", value=", ".join(share.get("cc", [])), placeholder="name@client.com")
        to, cc = mailer.split_addresses(to_text), mailer.split_addresses(cc_text)
        bad = mailer.invalid_addresses(to + cc)
        if bad:
            st.error("Not a valid e-mail address: " + ", ".join(bad))
        elif (to, cc) != (share.get("to", []), share.get("cc", [])) and to:
            new = dict(raw)
            new["share"] = {"to": to, "cc": cc}
            h.save_raw(ss.project, new)  # remembered for this project

        too_big = size_mb > mailer.MAX_ATTACHMENT_MB
        link = ""
        if too_big:
            st.markdown("**Share it through OneDrive**")
            st.warning(f"The zip is {size_mb:.0f} MB - too big for e-mail (the usual limit is {mailer.MAX_ATTACHMENT_MB} MB). "
                       "Share it through OneDrive and send the link instead.")
            od = mailer.onedrive_folder()
            if od is not None:
                st.markdown("1. Press **Put the zip in OneDrive** - it goes to the *WishBridge* folder and OneDrive uploads it.\n"
                            "2. In the folder that opens, right-click the zip → **Share** → **Copy link**.\n"
                            "3. Paste the link below and send the e-mail.")
                if st.button("☁ Put the zip in OneDrive"):
                    try:
                        with st.spinner("Copying to OneDrive..."):
                            dest = mailer.copy_to_onedrive(pkg)
                        if os.name == "nt":
                            subprocess.Popen(["explorer", "/select,", str(dest)])
                        st.success(f"Copied to `{dest}`. OneDrive is uploading it - share it once the upload has finished.")
                    except OSError as e:
                        st.error(f"Could not copy it: {e}")
            else:
                st.markdown("OneDrive is not set up on this computer:\n"
                            "1. Press **Open OneDrive** and sign in with the company account.\n"
                            "2. Upload the zip (use *Show the zip in its folder* above to find it).\n"
                            "3. Share it → **Copy link**, paste the link below and send the e-mail.")
                st.link_button("☁ Open OneDrive", "https://www.office.com/launch/onedrive")
            link = st.text_input("OneDrive link to the zip", placeholder="https://...sharepoint.com/... or https://1drv.ms/...",
                                 key="share_link").strip()
            if link and not link.lower().startswith("https://"):
                st.error("The link should start with https://")

        subject, body = mailer.default_message(cfg.name, load_state(cfg), pkg.name, link)
        st.markdown("**Ready-made message for the client's team** (copy with the button at the top right of the box)")
        handover = (f"To: {', '.join(to) or '<Wishtree e-mail>'}" + (f"\nCC: {', '.join(cc)}" if cc else "")
                    + f"\nSubject: {subject}" + ("" if too_big else f"\nAttach: {pkg}") + f"\n\n{body}")
        st.code(handover, language=None)
        ss[f"sent:{ss.project}"] = True

        st.divider()
        if not too_big:
            label, ready, attach = "📧 Send mail with the zip attached", bool(to) and not bad, pkg
            hint = "Opens a new e-mail with To, CC, subject, message and the zip attached."
        else:
            label, ready, attach = "📧 Send mail with the OneDrive link", bool(to) and not bad and link.startswith("https://"), None
            hint = "Opens a new e-mail with To, CC, subject and the message including the OneDrive link."
        if st.button(label, type="primary", disabled=not ready,
                     help=hint + " Nothing is sent until you check it and press Send in your mail program."):
            ok, msg = mailer.open_mail_draft(to, cc, subject, body, attach)
            (st.success if ok else st.error)(msg)
        if not to:
            st.caption("Enter the Wishtree e-mail in *Send to* above to use this button.")
        elif too_big and not link:
            st.caption("Paste the OneDrive link above to use this button.")


# ----------------------------------------------------------------- results

if STEP == "results":
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

if STEP == "fix":
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
                if b1.button("💾 Save as manual fix", type="primary", disabled=edited == start):
                    ovr.parent.mkdir(parents=True, exist_ok=True)
                    ovr.write_text(edited, encoding="utf-8")
                    st.success("Saved. Run Convert again to apply it, then Deploy to check it on Databricks.")
                if ovr.exists() and b2.button("🗑 Remove manual fix"):
                    ovr.unlink()
                    st.success("Removed. The converted version will be used on the next run.")
                    st.rerun()
