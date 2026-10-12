"""Wishtree WishBridge UI. Started by `wishbridge ui` (Streamlit).

This file is the shell: system check, first page, sidebar and the step bar. Each step is its own file in
ui/steps/, shared pieces are in ui/common.py and ui/database.py, and the screens that differ per warehouse
are in ui/warehouses/ (e.g. SSIS in ui/warehouses/ssis.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml
import streamlit as st

from wishbridge import __version__
from wishbridge.config import PURPOSE, SCOPE_ROWS, SOURCES, ConfigError
from wishbridge import discover
from wishbridge.ui import helpers as h


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


# ----------------------------------------------------------------- what to do

GOALS = {"assessment": "🔍 Assessment", "migration": "🚚 Migration"}
# Ways to start, per goal (label -> the form that does it)
START = {
    "assessment": {"Open a folder": "folder", "Start from the database": "new"},
    "migration": {"Open the assessment zip": "package", "Open a project": "folder", "Create new": "new"},
}

if project_ok() and "goal" not in ss:  # a project opened before (or by a test): its phase is the goal
    try:
        ss.goal = str((h.read_raw(ss.project) or {}).get("phase") or "migration").lower()
        ss.goal_applied = (ss.project, ss.goal)
    except Exception:  # unreadable settings are reported further down
        pass


def choose_goal(goal: str) -> None:
    ss.goal = goal


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
    goal = ss.get("goal")
    st.radio("I want to do", list(GOALS), format_func=GOALS.get, key="goal", index=None, horizontal=True,
             help="Assessment: at the client, offline - read and assess the code, share one zip. Migration: deploy to "
                  "Databricks, copy the data and prove it matches.")
    locked = not system_ready() or not goal
    if not system_ready():
        st.caption("🔒 Opening a project is available once the system check is done and the computer is ready.")
    elif not goal:
        st.caption("🔒 Choose **Assessment** or **Migration** first.")
    # While a project is open, the ways to start another one are folded away.
    with (st.expander("Open or start another project") if project_ok() and not locked else st.container()):
        ways = START[goal or "assessment"]
        mode = ways[st.radio("Project", list(ways), horizontal=True, label_visibility="collapsed", disabled=locked,
                             key=f"start_{goal}")]
        if mode == "folder":
            folder = st.text_input("Project or client code folder", value=ss.project, disabled=locked,
                                   placeholder=r"C:\WishBridge\Assessments\acme-dw  or  C:\client-repo",
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
        elif mode == "package":
            st.caption("The zip saved by the assessment (*Assess and save everything*) on any computer. "
                       "It becomes a project here, ready to migrate.")
            pkg_up = st.file_uploader("Package (.zip)", type=["zip"], key="pkg_upload", disabled=locked)
            pkg_parent = st.text_input("Create the project in", value=h.default_parent(goal), key=f"pkg_parent_{goal}",
                                       disabled=locked, help="Migrations and assessments have their own folders.")
            if st.button("Open package", width="stretch", disabled=locked or pkg_up is None or not pkg_parent.strip()):
                from wishbridge.open_package import open_package

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
            if goal == "assessment":
                st.caption("No code folder? Create the project here, then read the tables and code straight from the "
                           "client's database in **Code › Read from the database** (read-only, no data).")
            name = st.text_input("Project name :red[*]", placeholder="acme-dw", disabled=locked, key="new_name")
            source = st.selectbox("Source system :red[*]", list(SOURCES), format_func=lambda k: SOURCES[k].label, disabled=locked)
            parent = st.text_input("Create in folder :red[*]", value=h.default_parent(goal), disabled=locked,
                                   key=f"new_parent_{goal}", help="Migrations and assessments have their own folders.")
            if st.button("Create project", width="stretch", disabled=locked or not name.strip() or not parent.strip()):
                try:
                    ss.project = str(h.create_project(parent, name, source))
                    ss.workspace = None
                    if goal == "assessment":  # "Start from the database": the Code step leads with the database
                        new_raw = h.read_raw(ss.project)
                        new_raw["code_from"] = "database"
                        h.save_raw(ss.project, new_raw)
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


def show_goal_choice() -> None:
    """First page: assessment or migration. The sidebar then offers the ways to start that fit."""
    st.subheader("What do you want to do?")
    c1, c2 = st.columns(2)
    cards = {
        "assessment": (c1, "At the client, offline (nothing goes to Databricks):\n"
                           "- open the client's code folder, **or** read the tables and code straight from their "
                           "database (read-only, no data)\n"
                           "- check it really is a data warehouse, size it and convert it\n"
                           "- share everything as one zip"),
        "migration": (c2, "Move it to Databricks:\n"
                          "- usually starts from the **assessment zip**\n"
                          "- deploy the tables and code to the client's workspace\n"
                          "- connect the source database, map the tables and copy the data\n"
                          "- prove the data matches"),
    }
    for key, (col, text) in cards.items():
        with col.container(border=True):
            chosen = ss.get("goal") == key
            st.markdown(f"### {GOALS[key]}" + (" ✅" if chosen else ""))
            st.markdown(text)
            st.button("Selected" if chosen else f"Start {'an assessment' if key == 'assessment' else 'a migration'}",
                      key=f"goal-{key}", type="primary" if chosen else "secondary", width="stretch",
                      on_click=choose_goal, args=(key,), disabled=chosen)


if not system_ready():
    show_goal_choice()
    st.info("Then **Step 0 · System check** in the sidebar: *Check this computer*, and *Install missing* if "
            "anything is not there.")
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
        default_parent = h.default_parent(ss.get("goal"))  # assessments and migrations in their own folders
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
    show_goal_choice()
    goal = ss.get("goal")
    if goal == "assessment":
        st.info("Next, in the sidebar: **Open a folder** with the client's warehouse code (a project folder, a "
                "repository, a database project or an ETL export), or **Start from the database** when there is no "
                "code folder.")
    elif goal == "migration":
        st.info("Next, in the sidebar: **Open the assessment zip** (or an existing project). Then fill in the "
                "Databricks workspace and the source database connection in Settings.")
    show_welcome()
    st.stop()

# The main screen reads only `source:` (which warehouse); the warehouse's own copy checks the rest of the
# file, so a warehouse can add or change its settings without the others (or this screen) knowing.
from wishbridge.warehouses import package_for, ui_for  # noqa: E402

try:
    raw = h.read_raw(ss.project)
    if not isinstance(raw, dict):
        raise ConfigError("the file does not contain WishBridge settings")
    source_key = str(raw.get("source") or "").strip()
    package_for(source_key)  # an unknown source system is reported here
except (ConfigError, yaml.YAMLError, KeyError, TypeError, ValueError) as e:
    st.error(f"This project's settings file can't be read: {str(e).splitlines()[0]}")
    st.markdown(f"Open `{h.project_file(ss.project)}` in a text editor and correct it, or open the client's code folder "
                "instead - the app can create a fresh project for it.")
    st.stop()

if ss.get("goal") and ss.get("goal_applied") != (ss.project, ss.goal):
    ss.goal_applied = (ss.project, ss.goal)
    if str(raw.get("phase") or "migration").lower() != ss.goal:
        raw["phase"] = ss.goal
        h.save_raw(ss.project, raw, check=False)
        st.rerun()

# ----------------------------------------------------------------- the project's warehouse takes over
# Everything after opening a project (step bar, steps) is in wishbridge/warehouses/<warehouse>/ui/.

ui_for(source_key).run(raw)
