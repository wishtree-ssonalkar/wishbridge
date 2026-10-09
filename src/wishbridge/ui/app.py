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

LABELS = {"settings": "Settings", "code": "Code", "run": "Run", "data": "Map the data", "results": "Results",
          "fix": "Fix code", "send": "Share the zip"}
from_package = (Path(ss.project) / "opened_from_package.json").exists()
if cfg.phase == "assessment":
    # At the client: everything ends up in the zip, so the last step is sending it. Fixing happens later,
    # in the project opened from that zip.
    ORDER = ["settings", "code", "run"] + (["fix"] if from_package else []) + ["send"]
else:
    ORDER = ["settings", "code", "run", "data", "results", "fix"]
state_now = load_state(cfg)
running = runner.is_running(cfg)
has_code = bool(h.input_files(ss.project, raw.get("input", "input")))
has_results = any(k in state_now for k in ("analyze", "convert", "fit", "deploy", "reconcile"))
packages = sorted((cfg.output_dir / "review_package").glob("*.zip"), key=lambda q: q.stat().st_mtime)
done = {
    "settings": bool(ss.get(f"settings_done:{ss.project}")) or has_results,  # confirmed with Next (or used before)
    "code": has_code,
    "run": has_results and not running,
    "data": bool((state_now.get("load") or {}).get("executed")) and not running,
    "results": has_results and not running,
    "fix": False,
    "send": bool(ss.get(f"sent:{ss.project}")),
}


def reachable(key: str) -> bool:
    if running:
        return key in ("run", "data")  # while a run (or the data copy) is going, stay there to follow it
    if key == "results":
        return all(done[k] for k in ("settings", "code", "run"))  # copying the data is not required to look
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
if running and ss.step not in ("run", "data"):
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
    ws, chosen_host, project_host = None, "", str(dbx.get("host") or "")
    src_done = False

    if offline:
        st.info("Databricks, source database and data-copy settings are not needed for the assessment. "
                "They appear here when you switch the phase to **Migration**.")

    def part(n: int, title: str, note: str = ""):
        """Heading of one part of the migration settings; call the result to tick it once the part is complete."""
        head = st.empty()
        head.markdown(f"#### {n} · {title}")
        if note:
            st.caption(note)
        return lambda: head.markdown(f"#### {n} · {title} ✅")

    def autosave(updates: dict) -> None:
        """Save a finished part right away (the next part - e.g. creating the connection - uses it)."""
        new_, changed = dict(raw), False
        for key_, val in updates.items():
            cur_ = raw.get(key_)
            merged = {**(cur_ or {}), **val} if isinstance(val, dict) else val
            if merged != cur_:
                new_[key_], changed = merged, True
        if changed:
            h.save_raw(ss.project, new_)
            st.rerun()

    def next_part_hint() -> None:
        st.caption("The next part appears here when this one is filled in.")

    if not offline:
        st.caption("Fill in each part; the next one appears when it is complete. Finished parts are saved right away.")
        # --- 1. Databricks workspace: which saved login (profile) this project uses
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

        tick_ws = part(1, "Databricks workspace", "The client workspace this project migrates into.")
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
        other_ws = bool(project_host and chosen_host
                        and project_host.rstrip("/").lower() != chosen_host.rstrip("/").lower())
        if other_ws:
            st.error(f"This project belongs to **{project_host}**, but the selected login is for **{chosen_host}**. "
                     "Pick the login for the project's workspace, or move the project to the selected one.")
            if st.button(f"Move this project to {chosen_host}"):
                autosave({"databricks": {"profile": profile, "host": chosen_host}})
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

        _login = by_name.get(profile)
        ws_done = bool(_login) and _login.get("valid") is not False and not other_ws
        target_done = src_done = False
        if not ws_done:
            next_part_hint()
        else:
            tick_ws()
            # --- 2. Target: where the converted objects are created
            tick_target = part(2, "Target in Databricks", "Converted objects and the copied data go into this test schema.")
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

            target_done = (bool(str(catalog or "").strip()) and bool(str(schema or "").strip())
                           and not looks_like_prod(f"{catalog}.{schema}"))
            if not target_done:
                next_part_hint()
            else:
                tick_target()
                changes = {"databricks": {"profile": profile, "host": (ws or {}).get("host") or chosen_host or project_host,
                                          "warehouse_id": warehouse, "catalog": catalog, "schema": schema.strip()},
                           "scope": scope}
                old_cat = str(dbx.get("catalog") or "")
                if old_cat and catalog != old_cat:  # schema names made from the old catalog follow the new one
                    changes["schema_map"] = {k: (f"{catalog}.{v[len(old_cat) + 1:]}" if str(v).startswith(old_cat + ".") else v)
                                             for k, v in (raw.get("schema_map") or {}).items()}
                autosave(changes)

        if target_done:
            # --- 3. Source data: where the data is copied from
            tick_src = part(3, "Source data", "Where the data is copied from.")
            method_labels = {"federation": "Read the source database directly (Lakehouse Federation)",
                             "files": "Exported files in a Unity Catalog volume"}
            method = st.radio("How to copy the data :red[*]", list(method_labels), format_func=method_labels.get,
                              horizontal=True, key="data_method",
                              index=list(method_labels).index(data.get("method", "federation")))
            if method == "federation":
                st.markdown("**Source database** :red[*] — WishBridge creates a Lakehouse Federation connection in "
                            "Databricks; the password goes into Databricks secrets, never into project.yml.")
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

                source_catalog = (raw.get("data") or {}).get("source_catalog", "")
                with st.expander("The client already has a federation catalog in Databricks"):
                    fed_options = [c["name"] for c in ws["catalogs"]] if ws else []
                    if fed_options:
                        opts = [""] + fed_options if source_catalog in fed_options or not source_catalog else ["", source_catalog] + fed_options
                        picked_cat = st.selectbox("Source catalog (federation)", opts, index=opts.index(source_catalog))
                    else:
                        picked_cat = st.text_input("Source catalog (federation)", value=source_catalog)
                    if (picked_cat or "") != source_catalog and st.button("Use this catalog"):
                        autosave({"data": {"method": "federation", "source_catalog": picked_cat}})
                if not str(source_catalog or "").strip():
                    required("Required: create the connection above (or use an existing federation catalog).")
                src_done = bool(str(source_catalog or "").strip())
            else:
                files_root = st.text_input("Files root (volume path) :red[*]", value=data.get("files_root", ""),
                                           placeholder="/Volumes/main/landing/acme")
                if not files_root.strip():
                    required("Required: the volume folder the exported tables are in.")
                file_format = st.selectbox("File format", ["PARQUET", "CSV", "JSON", "AVRO", "ORC"],
                                           index=["PARQUET", "CSV", "JSON", "AVRO", "ORC"].index(str(data.get("file_format", "PARQUET")).upper()))
                src_done = bool(files_root.strip())
            if not src_done:
                next_part_hint()
            else:
                tick_src()
                # --- 4. Copy options
                part(4, "Copy options")()
                load_mode = st.selectbox("Load mode", ["append", "overwrite"],
                                         index=["append", "overwrite"].index(data.get("mode", "append")),
                                         help="append: add to what is in the Databricks tables. overwrite: replace it.")
                st.caption("Which tables go where is chosen in the step *Map the data*, after the run has created "
                           "the tables in Databricks.")

    with st.expander("AI suggestions and estimates"):
        ai_on = st.checkbox("Ask Claude for fix suggestions during convert (needs ANTHROPIC_API_KEY; sends code, not data)",
                            value=bool(ai.get("ai", False)))
        model = st.text_input("Claude model", value=ai.get("model", "claude-opus-5-5"))
        est = raw.get("estimate") or {}
        hpi = st.number_input("Hours per open item", value=float(est.get("hours_per_issue", 0.5)), step=0.25)

    problems = settings_problems(offline, locals())
    if problems and (offline or src_done):
        st.markdown(":red[Fill in the fields marked * (see the red notes above) before going on.]")
    if (offline or src_done) and st.button("Next: add the code →", type="primary",
                                           help="Saves the settings and opens the Code tab.", disabled=bool(problems)):
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
        new["data"] = {**data, "method": method, "source_catalog": source_catalog, "files_root": files_root,
                       "file_format": file_format, "mode": load_mode}
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



def code_files_input(raw) -> str:
    """The folder of the client's code files (not the code read from the database)."""
    from wishbridge.dbcode import FOLDER

    current = raw.get("input", "input")
    return raw.get("code_files_input", "input") if current == FOLDER else current


def has_code_files(raw) -> bool:
    return bool(h.input_files(ss.project, code_files_input(raw)))


def use_database_code(raw, on: bool) -> None:
    """Assess the code read from the database (on) or the client's code files; table scripts follow."""
    from wishbridge import schema as schema_mod
    from wishbridge.dbcode import FOLDER

    new = dict(raw)
    current = new.get("input", "input")
    if on and current != FOLDER:
        new["code_files_input"], new["input"] = current, FOLDER
    elif not on and current == FOLDER:
        new["input"] = new.pop("code_files_input", "input")
    else:
        return
    h.save_raw(ss.project, new)
    fresh = load_config(h.project_file(ss.project))
    if load_state(fresh).get("inventory"):
        schema_mod.build_scripts(fresh)  # skip the tables the chosen code creates


def database_code_summary(cfg, raw) -> None:
    from wishbridge.dbcode import FOLDER

    dc = load_state(cfg).get("dbcode")
    if not dc:
        return
    st.markdown("**Code read from the database**")
    counts = " · ".join(f"{n} {k.lower()}" for k, n in sorted(dc["counts"].items())) or "no objects"
    st.markdown(f"{counts} (from {dc['read_from']}, {dc['read_at'].replace('T', ' ')}), saved as one .sql file per object.")
    if dc.get("encrypted"):
        st.warning(f"{len(dc['encrypted'])} object(s) are encrypted in the database and cannot be read — ask the client "
                   f"for their scripts: {', '.join(dc['encrypted'][:10])}")
    if dc.get("unreadable"):
        st.warning(f"{len(dc['unreadable'])} object(s) could not be read: the login needs VIEW DEFINITION. "
                   f"{', '.join(dc['unreadable'][:10])}")
    using_db = raw.get("input", "input") == FOLDER
    if not has_code_files(raw):
        st.caption("The run assesses this code (the project has no other code files).")
        if not using_db:
            use_database_code(raw, True)
            st.rerun()
        return
    cmp = load_state(cfg).get("dbcode_compare")
    if cmp:
        st.markdown(f"Compared with the code files: **{len(cmp['same'])} the same**, "
                    f"**{len(cmp['different'])} different**, **{len(cmp['only_in_database'])} only in the database**, "
                    f"**{len(cmp['only_in_files'])} only in the files**.")
        if cmp["different"] or cmp["only_in_database"] or cmp["only_in_files"]:
            with st.expander("What differs"):
                for label, key in (("Different", "different"), ("Only in the database", "only_in_database"),
                                   ("Only in the files", "only_in_files")):
                    if cmp[key]:
                        st.markdown(f"*{label}:* " + ", ".join(f"`{n}`" for n in cmp[key]))
                st.caption("The database holds what really runs; the files may be older or newer than it.")
    options = ["The code files", "The code from the database"]
    pick = st.radio("Code to assess", options, index=1 if using_db else 0, horizontal=True, key="code_source")
    if (pick == options[1]) != using_db:
        use_database_code(raw, pick == options[1])
        st.rerun()


def database_tables(cfg, raw) -> None:
    """Optional: read the source database's tables (catalog only, no data) and make the Databricks table scripts."""
    from wishbridge import inventory as inv_mod
    from wishbridge import schema as schema_mod

    inv = load_state(cfg).get("inventory")
    title = "Read from the database (optional) — tables and code"
    with st.expander(title, expanded=bool(load_state(cfg).get("dbcode")) or not has_code_files(raw)):
        st.markdown("Use this when the client has **no code repository**, or not all of it. WishBridge reads the list "
                    "of tables, columns and data types and, if you want, the **procedures, views, functions and "
                    "triggers** stored in the database — **no data is read or copied**. It writes a Databricks "
                    "`CREATE TABLE` script for every table the code does not already create.")
        how = st.radio("How to read them", ["Connect to the database", "Send a query to the client's DBA"],
                       horizontal=True, key="schema_how",
                       help="Connecting works for SQL Server, Azure SQL and Synapse. The DBA query works for every "
                            "database WishBridge supports.")
        if how == "Connect to the database":
            c1, c2 = st.columns(2)
            server = c1.text_input("Server :red[*]", key="live_server", placeholder="myserver\\SQLEXPRESS or myserver,1433")
            database = c2.text_input("Database :red[*]", key="live_db")
            kind = c1.selectbox("Database type", list(inv_mod.LIVE_DATABASES), key="live_type",
                                format_func={"sqlserver": "SQL Server / Azure SQL", "synapse": "Azure Synapse"}.get)
            login = c2.radio("Login", ["Windows login", "User and password"], horizontal=True, key="live_login")
            user = password = ""
            if login == "User and password":
                user = c1.text_input("User :red[*]", key="live_user")
                password = c2.text_input("Password :red[*]", type="password", key="live_pw",
                                         help="Used once to read the tables; never saved.")
            trust = st.checkbox("Trust the server certificate", key="live_trust",
                                help="Needed for servers with a self-signed certificate (common on local servers).")
            with_code = st.checkbox("Also read the code (procedures, views, functions, triggers)", value=True,
                                    key="live_code", help="Saved as one .sql file per object. Encrypted objects "
                                                          "cannot be read; they are listed so the client can send them.")
            missing = [n for n, v in [("server", server), ("database", database)] if not v.strip()]
            if login == "User and password":
                missing += [n for n, v in [("user", user), ("password", password)] if not v.strip()]
            if missing:
                st.caption(f":red[Fill in the {' and '.join(missing) if len(missing) < 3 else ', '.join(missing)} to read the tables.]")
            if st.button("🔍 Read the tables and code" if with_code else "🔍 Read the tables", type="primary",
                         disabled=bool(missing), key="live_read"):
                with st.spinner(f"Reading from {server} / {database}..."):
                    try:
                        inv_mod.read_live(cfg, server, database, login == "Windows login", user, password, trust, kind,
                                          with_code=with_code)
                    except (RuntimeError, ValueError) as e:
                        st.error(str(e))
                    else:
                        ss.pop("live_pw", None)
                        if with_code:
                            if has_code_files(raw):
                                from wishbridge import dbcode

                                dbcode.compare(cfg, Path(ss.project) / code_files_input(raw))
                            else:
                                use_database_code(raw, True)  # nothing else to assess: use what was read
                        st.rerun()
        else:
            st.markdown("Give the client's DBA this read-only query and import the CSV they send back.")
            st.caption("For the code without a connection: ask the DBA for SSMS › right-click the database › Tasks › "
                       "Generate Scripts (all procedures, views, functions; schema only) and add the files below.")
            db_types = list(inv_mod.QUERIES)
            default_db = (cfg.source_db or {}).get("type") or inv_mod.DEFAULT_DB.get(cfg.source.key, "sqlserver")
            db = st.selectbox("Database type", db_types, index=db_types.index(default_db) if default_db in db_types else 0)
            fname, text = inv_mod.script(cfg, db)
            st.download_button("Download the query for the DBA", text, file_name=fname, mime="text/plain")
            up = st.file_uploader("Import the DBA's CSV", type=["csv", "txt"], key="inventory_csv")
            if st.button("Import the CSV", disabled=up is None):
                tmp = cfg.out("inventory", "upload_" + Path(up.name).name)
                tmp.write_bytes(up.getvalue())
                try:
                    inv_mod.import_file(cfg, tmp, db)
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

        if not inv:
            return
        s = inv["summary"]
        st.success(f"{s['tables']} tables · {s['columns']} columns · {s['rows']:,} rows · {s['size_mb']:,} MB "
                   f"(from {inv.get('read_from', 'the DBA CSV')}, {inv['imported_at'].replace('T', ' ')})")
        st.dataframe(pd.DataFrame([{"table": f"{t['schema']}.{t['table']}", "rows": t["rows"], "size (MB)": t["size_mb"],
                                    "columns": len(t["columns"])} for t in inv["tables"]]), width="stretch", hide_index=True)

        database_code_summary(cfg, raw)

        st.markdown("**Databricks table scripts**")
        sch = load_state(cfg).get("schema")
        if not sch:
            try:
                sch = schema_mod.build_scripts(cfg, inv)
            except ValueError as e:
                st.error(str(e))
                return
        files = [f for f in sch["files"] if Path(f["path"]).is_file()]
        line = f"{sch['tables']} table(s) scripted in {len(files)} file(s)"
        if sch["skipped"]:
            line += f" · {len(sch['skipped'])} skipped because the code already creates them"
        st.markdown(line + ". They go into the converted code on the next run" +
                    (" and are created in Databricks first when you deploy." if cfg.phase == "migration" else
                     " and into the zip."))
        if sch.get("unknown_types"):
            st.warning("Some column types are unknown and were made STRING — search the scripts for `review`: "
                       + "; ".join(f"{t}: {', '.join(c)}" for t, c in list(sch["unknown_types"].items())[:5]))
        b1, b2 = st.columns(2)
        if files:
            pick = b1.selectbox("Script", [f["file"] for f in files], key="schema_pick",
                                format_func=lambda n: n.split("/")[-1])
            path = Path(next(f["path"] for f in files if f["file"] == pick))
            b2.download_button("⬇ Download this script", path.read_bytes(), file_name=path.name, mime="text/plain")
            st.code(path.read_text(encoding="utf-8")[:20000], language="sql")
        if b1.button("↻ Make the scripts again", help="After changing the schema mapping in Settings."):
            schema_mod.build_scripts(cfg, inv)
            st.rerun()
        if cfg.phase == "migration" and b2.button("Use these tables for the data copy"):
            new = dict(raw)
            new.setdefault("data", {})["tables"] = inv_mod.table_list(inv)
            h.save_raw(ss.project, new)
            st.success("Saved to Settings > Data. Check the mapping there.")
            st.rerun()

# ----------------------------------------------------------------- code

if STEP == "code":

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

    database_tables(cfg, raw)
    uploads = st.file_uploader("Add files", accept_multiple_files=True, type=h.INPUT_EXTENSIONS)
    if uploads and st.button(f"Add {len(uploads)} file(s) to the project"):
        for up in uploads:
            h.save_upload(ss.project, up.name, up.getvalue(), code_files_input(raw))
        st.success(f"Added {len(uploads)} file(s)")
        st.rerun()
    files = h.input_files(ss.project, raw.get("input", "input"))
    if not files:
        st.info("No code yet. Read it from the database above, upload files, or copy them into the input folder.")
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
    if status and "load" in [x["name"] for x in status.get("steps", [])]:
        status = None  # the last run was the data copy: shown in Map the data
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
            do_deploy = st.checkbox("3. Create the tables and code in the test schema", value=True)
            recreate = st.checkbox("…rebuild objects that already exist", value=False, disabled=not do_deploy)
        with c3:
            do_report = st.checkbox("4. Build the report", value=True)
            st.caption("Copying the data comes next, in *Map the data*.")
        st.caption(f"Target: `{cfg.target_schema}` · converter: {'automatic, ' if cfg.auto_converter else ''}{cfg.transpiler}"
                   + (" first" if cfg.auto_converter else "") + f" · source: {cfg.source.analyzer_tech}")
        chosen = [n for n, on in (("analyze", do_analyze), ("convert", do_convert), ("deploy", do_deploy),
                                  ("report", do_report)) if on]
        if st.button("▶ Start", type="primary", disabled=not chosen):
            runner.start(cfg, chosen, {"ai": use_ai, "recreate": recreate}, keep_going=False)
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
                next_button("Next: map the data →", "data", "next-run")


# ----------------------------------------------------------------- data

if STEP == "data":
    from wishbridge import mapping

    st.subheader("Map the data and copy it")
    st.markdown("The run created the tables in Databricks. Choose which source table goes into which Databricks "
                "table, check the columns, then copy the data. Nothing is copied until you press *Copy the data*.")
    if runner.is_running(cfg):
        st.markdown("#### Copying…")
        run_progress()
        st.stop()
    federation = cfg.data_method == "federation"
    if federation and not cfg.source_catalog:
        st.warning("Create the source database connection in **Settings › Source database** first.")
        st.stop()

    saved = h.table_rows(raw)
    if st.button("🔄 Propose the mapping", help="Lists the source tables and the Databricks tables and pairs them by name."):
        with st.spinner("Reading the table lists..."):
            try:
                wh = Warehouse(cfg)
                targets = mapping.target_tables(cfg, wh)
                ss.map_rows = mapping.propose(cfg, mapping.source_tables(cfg, wh), targets)
                ss.map_targets = targets
                ss.pop("map_check", None)
            except (SqlError, ValueError) as e:
                st.error(f"Could not read the tables: {e}")
    rows = ss.get("map_rows") or [{"copy": True, "source": r["source"], "target": r["target"] or cfg.map_table(r["source"])}
                                  for r in saved]
    if not rows:
        st.info("Press **Propose the mapping** to list the source tables and pair them with the Databricks tables.")
    else:
        targets = sorted(set(ss.get("map_targets") or []) | {r["target"] for r in rows if r["target"]})
        df = pd.DataFrame([{"copy": r["copy"], "source": r["source"], "target": r["target"]} for r in rows])
        edited = st.data_editor(
            df, hide_index=True, width="stretch", key="map_editor", num_rows="fixed",
            column_config={
                "copy": st.column_config.CheckboxColumn("Copy", width="small"),
                "source": st.column_config.TextColumn("Source table", disabled=True),
                "target": st.column_config.SelectboxColumn("Databricks table", options=targets, required=True),
            })
        if ss.get("map_rows"):
            missing = [r["target"] for r in ss.map_rows if not r.get("exists")]
            if missing:
                st.caption(f":red[{len(missing)} Databricks table(s) do not exist yet] (e.g. `{missing[0]}`): run "
                           "*Create the tables* in Run, or pick another table.")
        picked = [r for r in edited.to_dict("records") if r["copy"] and r["target"]]
        if st.button(f"💾 Save and check the columns ({len(picked)} table(s))", type="primary", disabled=not picked):
            new = dict(raw)
            new["data"] = {**(raw.get("data") or {}),
                           "tables": h.rows_to_tables(picked, (raw.get("data") or {}).get("tables"))}
            h.save_raw(ss.project, new)
            with st.spinner("Reading the columns of both sides..."):
                try:
                    ss.map_check = mapping.check(load_config(h.project_file(ss.project)))
                except (SqlError, ValueError) as e:
                    st.error(f"Could not check the columns: {e}")
            st.rerun()

    check = ss.get("map_check")
    if check:
        icon = {"ready": "✅", "check columns": "⚠️", "compare only": "➖", "no target table": "❌",
                "no source table": "❌", "no matching columns": "❌"}
        st.markdown("**Columns**")
        st.dataframe(pd.DataFrame([{
            "": icon.get(c["status"], ""), "source": c["source"], "Databricks table": c["target"], "status": c["status"],
            "columns paired": f"{len(c.get('pairs', {}))}/{len(c.get('target_columns', []))}" if "pairs" in c else "",
            "note": c.get("detail") or (", ".join(f"no source for {x}" for x in c.get("target_without_source", []))
                                        + ("; " if c.get("target_without_source") and c.get("source_not_copied") else "")
                                        + ", ".join(f"{x} not copied" for x in c.get("source_not_copied", [])))}
            for c in check]), hide_index=True, width="stretch")
        for c in check:
            if c["status"] not in ("check columns", "no matching columns"):
                continue
            with st.expander(f"Map the columns: {c['source']} → {c['target']}"):
                cdf = pd.DataFrame([{"Databricks column": t, "source column": c["pairs"].get(t, "")}
                                    for t in c["target_columns"]])
                key = f"cols:{c['target']}"
                ed = st.data_editor(cdf, hide_index=True, width="stretch", key=key, num_rows="fixed", column_config={
                    "Databricks column": st.column_config.TextColumn(disabled=True),
                    "source column": st.column_config.SelectboxColumn(options=[""] + c["source_columns"]),
                })
                st.caption("Leave a column empty to keep it NULL (or its default).")
                if st.button("💾 Save these columns", key=f"save-{key}"):
                    chosen = {r["Databricks column"]: r["source column"] for r in ed.to_dict("records") if r["source column"]}
                    tables = [dict(t) if isinstance(t, dict) else {"source": t}
                              for t in (raw.get("data") or {}).get("tables") or []]
                    for t in tables:
                        if t["source"] == c["source"]:
                            t["columns"] = chosen
                    new = dict(raw)
                    new["data"] = {**(raw.get("data") or {}), "tables": tables}
                    h.save_raw(ss.project, new)
                    with st.spinner("Checking again..."):
                        ss.map_check = mapping.check(load_config(h.project_file(ss.project)))
                    st.rerun()

        blocking = [c for c in check if c["status"] in ("no target table", "no source table", "no matching columns")]
        if blocking:
            st.markdown(f":red[Fix the {len(blocking)} table(s) marked ❌ (or untick them above) before copying.]")
        mode = "replaces what is in the Databricks tables" if cfg.load_mode == "overwrite" else "adds to the Databricks tables"
        st.caption(f"The copy {mode} (load mode in Settings) and then compares source and target.")
        if st.button("▶ Copy the data", type="primary", disabled=bool(blocking)):
            runner.start(cfg, ["load", "reconcile", "report"], {"execute": True}, keep_going=False)
            st.rerun()

    status = runner.read_status(cfg)
    if status and "load" in [x["name"] for x in status.get("steps", [])] and status.get("state") != "running":
        st.markdown("#### Last copy")
        show_run_status(status)
        ld = load_state(cfg).get("load") or {}
        if ld.get("executed"):
            st.dataframe(pd.DataFrame([{"source": t["source"], "Databricks table": t["target"], "status": t["status"],
                                        "rows": t.get("rows"), "error": t.get("error", "")} for t in ld["tables"]]),
                         hide_index=True, width="stretch")
            next_button("Next: see the results →", "results", "next-data")


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
            st.code(read_source(orig) if f["input"] and orig.is_file() else
                    ("(made from the database catalog - no original file)" if f.get("converter") == "database" else "(not found)"),
                    language="sql")
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
