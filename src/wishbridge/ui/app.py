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
from wishbridge import discover, hosting
from wishbridge.ui import helpers as h

LAPTOP_ONLY = "On the laptop only"


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

GOALS = {"assessment": "🔍 Assessment", "code-migration": "🧩 Code migration", "data-migration": "🚚 Data migration"}
# Ways to start, per goal (label -> the form that does it)
START = {
    "assessment": {"Open a folder": "folder", "Upload the code (zip)": "codezip", "Start from the database": "new"},
    "code-migration": {"Open the assessment zip": "package", "Open a project": "folder", "Create new": "new"},
    # The data goes into tables the code migration created: open that project. "Data only" is for tables that
    # already exist in Databricks (created some other way). The assessment zip has no tables in Databricks yet.
    "data-migration": {"Open the code migration project": "folder", "Data only: tables already in Databricks": "new"},
}
# The same choices on the laptop and in the Databricks App; each form says where it works. In the app, projects live
# on the workspace's volume, so "open a project" lists those instead of asking for a folder.
APP = hosting.is_app()

if project_ok() and "goal" not in ss:  # a project opened before (or by a test): its phase is the goal
    try:
        from wishbridge.config import normalise_phase

        ss.goal = normalise_phase((h.read_raw(ss.project) or {}).get("phase"))
        ss.goal_applied = (ss.project, ss.goal)
    except Exception:  # unreadable settings are reported further down
        pass


def choose_goal(goal: str) -> None:
    ss.goal = goal


# ----------------------------------------------------------------- sidebar

SETUP_STATUS = Path.home() / ".wishbridge" / "setup_status.json"


def system_ready() -> bool:
    """Projects can be opened once the computer has been checked and is ready (or the user chose to continue)."""
    if APP:
        return bool(ss.get("app_check")) and all(r["ok"] for r in ss.app_check)
    from wishbridge import system

    return bool(ss.get("system")) and (system.ready(ss.system) or ss.get("continue_anyway", False))


def app_check() -> None:
    """Step 0 in a Databricks App: the app's identity, its SQL warehouse and the volume that keeps the projects."""
    if not ss.get("app_check"):
        with st.spinner("Checking the app's access in this workspace..."):
            ss.app_check = hosting.check_app()
    from wishbridge import toolkit_setup

    toolkit_setup.ensure_started()  # analyze and convert in the app (installs in the background, once per start)
    if "claude_key_problem" not in ss:
        ss.claude_key_problem = hosting.load_claude_key()  # the Anthropic API key from a Databricks secret, if set
    rows = ss.app_check
    if all(r["ok"] for r in rows):
        st.success("✅ Running in Databricks")
    else:
        st.error("The app is missing access: " + ", ".join(r["name"] for r in rows if not r["ok"]))
    with st.expander("App check details", expanded=not all(r["ok"] for r in rows)):
        for r in rows:
            st.markdown(f"{'✅' if r['ok'] else '❌'} **{r['name']}** — {r['detail']}")
            if not r["ok"] and r.get("fix"):
                st.caption(r["fix"])
        tk = toolkit_setup.status()
        if toolkit_setup.ready():
            mark, text = "✅", "installed - analyze and convert (BladeBridge and Morpheus) run here"
        elif tk.get("state") == "failed":
            mark, text = "⚠️", f"could not be installed: {tk.get('error')}"
        else:
            mark, text = "⏳", f"installing ({tk.get('step') or 'starting'}) - a few minutes after the app starts"
        st.markdown(f"{mark} **Migration toolkit** — {text}")
        import os as _os

        if _os.environ.get("ANTHROPIC_API_KEY"):
            st.markdown("✅ **Claude** — API key from the workspace's secret")
        elif ss.get("claude_key_problem"):
            st.markdown(f"⚠️ **Claude** — {ss.claude_key_problem}")
        st.caption(" · ".join(f"{'✓' if ok else '✗'} {name}" for name, ok in toolkit_setup.parts().items()))
        if not toolkit_setup.ready() and toolkit_setup.log_tail():
            st.code(toolkit_setup.log_tail(), language="text")
        st.caption("Projects are kept on the volume and saved after every change.")
    if st.button("Check again", width="stretch"):
        ss.app_check = None
        st.rerun()


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


def open_saved(locked: bool) -> None:
    """Databricks App: open a project kept on the workspace's volume."""
    st.caption(f"In the Databricks app projects are kept in this workspace (`{hosting.projects_volume()}`); a "
               "folder path works on the laptop.")
    if "saved_projects" not in ss or st.button("↻ Refresh the list", width="stretch", disabled=locked):
        try:
            ss.saved_projects = hosting.saved_projects()
        except Exception as e:  # noqa: BLE001 - permission / volume missing: the app check says what to do
            ss.saved_projects = []
            st.error(f"Could not list the projects: {str(e).splitlines()[0][:200]}")
    saved = ss.saved_projects
    if not saved:
        st.caption("No projects in this workspace yet: open an assessment zip (Code migration) or upload the code "
                   "(Assessment) first.")
        return
    labels = {p.name: f"{p.name} · {p.modified} · {p.size // 1024:,} KB" for p in saved}
    name = st.selectbox("Project", list(labels), format_func=labels.get, disabled=locked, key="saved_pick")
    if st.button("Open", width="stretch", disabled=locked or not name, key="saved_open"):
        try:
            with st.spinner("Opening..."):
                ss.project, ss.workspace = str(hosting.pull(name)), None
            ss.pop("goal_applied", None)
            st.rerun()
        except (ValueError, OSError) as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001 - SDK errors
            st.error(f"Could not open {name}: {str(e).splitlines()[0][:200]}")


with st.sidebar:
    st.markdown(f"### 🌉 Wishtree WishBridge\nData warehouse migration to Databricks · v{__version__}")
    if APP:
        app_check()
    else:
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
        ways = START[goal or next(iter(START))]
        mode = ways[st.radio("Project", list(ways), horizontal=True, label_visibility="collapsed", disabled=locked,
                             key=f"start_{goal}")]
        if mode == "folder" and APP:
            if goal == "assessment":
                st.caption(f"A folder on your computer: {LAPTOP_ONLY.lower()} - the app runs in Databricks and cannot "
                           "see it. Here: **Upload the code (zip)**, or open a project kept in this workspace.")
            open_saved(locked)
        elif mode == "codezip":
            st.caption("A zip of the client's code folder (SQL scripts, SSIS packages, a database project...). WishBridge "
                       "unpacks it and creates the project, the same as opening the folder. Works on the laptop and in "
                       "the Databricks app.")
            code_up = st.file_uploader("Client code (.zip)", type=["zip"], key=f"code_upload_{goal}", disabled=locked)
            if st.button("Open the code", width="stretch", disabled=locked or code_up is None):
                try:
                    folder = h.unpack_code_zip(code_up.name, code_up.getvalue(), h.default_parent(goal))
                    with st.spinner("Looking at the code..."):
                        ss.code_info = discover.inspect(folder)
                    ss.project = ""
                    st.rerun()
                except (ValueError, OSError) as e:
                    st.error(str(e))
        elif mode == "folder":
            folder = st.text_input("Project or client code folder", value=ss.project, disabled=locked,
                                   placeholder=h.default_parent(goal) + r"\acme-dw  or  C:\client-repo",
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
            pkg_parent = h.default_parent(goal) if APP else st.text_input(
                "Create the project in", value=h.default_parent(goal), key=f"pkg_parent_{goal}", disabled=locked,
                help="Migrations and assessments have their own folders.")
            if st.button("Open package", width="stretch", disabled=locked or pkg_up is None or not pkg_parent.strip()):
                from wishbridge.open_package import open_package

                tmp = Path(pkg_parent).expanduser() / ".wishbridge-incoming" / Path(pkg_up.name).name
                try:
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    tmp.write_bytes(pkg_up.getvalue())
                    new_name = None
                    if APP:  # no folder to choose in the app: the same zip opened again becomes name-2, name-3...
                        import json as _json
                        import zipfile as _zip

                        from wishbridge.open_package import MANIFEST

                        with _zip.ZipFile(tmp) as _z:
                            base = _json.loads(_z.read(MANIFEST))["name"] if MANIFEST in _z.namelist() else ""
                        new_name = hosting.free_name(base) if base else None
                        if new_name and new_name != base:
                            st.toast(f"{base} is already open in this workspace (Open a saved project): this copy is "
                                     f"{new_name}.")
                    dest = open_package(tmp, pkg_parent, new_name)
                    tmp.unlink(missing_ok=True)
                    if APP:
                        hosting.push(dest, force=True)  # kept on the volume from the start
                    ss.project, ss.workspace = str(dest), None
                    st.rerun()
                except (ValueError, OSError, ConfigError) as e:
                    st.error(str(e))
        else:
            if APP and goal == "assessment":
                st.caption(f"Reading the client's database: {LAPTOP_ONLY.lower()} (it needs the SQL Server driver and a "
                           "route to the database). Here, create the project and add the code with **Upload the code "
                           "(zip)**.")
            elif goal == "assessment":
                st.caption("No code folder? Create the project here, then read the tables and code straight from the "
                           "client's database in **Code › Read from the database** (read-only, no data).")
            elif goal == "data-migration":
                st.caption("Only when the tables already exist in Databricks (created outside WishBridge). Then give "
                           "the workspace, the schema the tables are in, and where the data comes from in Settings, "
                           "and map the tables. Otherwise open the code migration project instead.")
            name = st.text_input("Project name :red[*]", placeholder="acme-dw", disabled=locked, key="new_name")
            source = st.selectbox("Source system :red[*]", list(SOURCES), format_func=lambda k: SOURCES[k].label, disabled=locked)
            parent = h.default_parent(goal) if APP else st.text_input(
                "Create in folder :red[*]", value=h.default_parent(goal), disabled=locked, key=f"new_parent_{goal}",
                help="Migrations and assessments have their own folders.")
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
        if APP:
            st.caption(f"Saved in the workspace: `{hosting.projects_volume()}/{Path(ss.project).name}.zip`"
                       + (f" · last saved {ss.saved_at}" if ss.get("saved_at") else ""))
        else:
            st.caption(ss.project.replace("\\", "\\\\").replace("_", "\\_"))  # show the path as typed, not as Markdown


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
    """First page: assessment, code migration or data migration. The sidebar then offers the ways to start."""
    st.subheader("What do you want to do?")
    if APP:
        st.caption("WishBridge is running in the Databricks workspace. Everything is here; a few choices work on the "
                   "laptop only (they say so). For an assessment at the client, offline, use WishBridge on the laptop "
                   "and open its zip here.")
    c1, c2, c3 = st.columns(3)
    cards = {
        "assessment": (c1, "At the client, offline (nothing goes to Databricks):\n"
                           "- open the client's code folder, **or** read the tables and code straight from their "
                           "database (read-only, no data)\n"
                           "- check it really is a data warehouse, size it and convert it\n"
                           "- share everything as one zip"),
        "code-migration": (c2, "Move the code to Databricks:\n"
                               "- usually starts from the **assessment zip**\n"
                               "- needs the client's Databricks workspace\n"
                               "- create the tables and code there, check them, fix what is left"),
        "data-migration": (c3, "Move the data to Databricks:\n"
                               "- on the project whose **code migration** is done\n"
                               "- needs the client's database reachable from Databricks (or exported files)\n"
                               "- map the tables, copy the data, prove it matches"),
    }
    if APP:
        cards["assessment"] = (c1, "Assess the client's code:\n"
                                   "- **upload the code as a zip** (a local folder or the database: on the laptop)\n"
                                   "- check it really is a data warehouse, size it and convert it\n"
                                   "- everything as one zip (the code is processed in this workspace)")
    for key, (col, text) in cards.items():
        with col.container(border=True):
            chosen = ss.get("goal") == key
            st.markdown(f"### {GOALS[key]}" + (" ✅" if chosen else ""))
            st.markdown(text)
            st.button("Selected" if chosen else {"assessment": "Start an assessment", "code-migration":
                      "Start a code migration", "data-migration": "Start a data migration"}[key],
                      key=f"goal-{key}", type="primary" if chosen else "secondary", width="stretch",
                      on_click=choose_goal, args=(key,), disabled=chosen)


if not system_ready():
    show_goal_choice()
    if APP:
        st.info("The app cannot start yet: see **App check details** in the sidebar for what the workspace admin "
                "needs to give it.")
    else:
        st.info("Then **Step 0 · System check** in the sidebar: *Check this computer*, and *Install missing* if "
                "anything is not there.")
    show_welcome()
    st.stop()

info = ss.get("code_info")
if info and not project_ok():
    # A folder of client code (no project.yml): offer to create WishBridge project(s) that read it in place.
    det = info.detection
    st.subheader("Create a project for this code")
    shown = info.path.name if APP else info.path  # the app's own folders mean nothing to the person
    st.markdown(f"`{shown}` is client code, not a WishBridge project yet: **{info.files} code files** "
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
        parent = default_parent if APP else st.text_input("Create projects in :red[*]", value=default_parent,
                                                          key="code_parent")
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
    elif goal == "code-migration":
        st.info("Next, in the sidebar: **Open the assessment zip** (or an existing project). Then fill in the "
                "Databricks workspace and target in Settings, and run.")
    elif goal == "data-migration":
        st.info("Next, in the sidebar: **Open the code migration project** (or **Data only** when the tables already exist in Databricks). Then fill in the "
                "source data (database connection or exported files) in Settings, and map the data.")
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
    from wishbridge.config import normalise_phase  # noqa: E402

    if raw.get("phase") != ss.goal:  # also rewrites "migration" (saved before the split) as the chosen phase
        raw["phase"] = ss.goal
        h.save_raw(ss.project, raw, check=False)
        st.rerun()

# ----------------------------------------------------------------- the project's warehouse takes over
# Everything after opening a project (step bar, steps) is in wishbridge/warehouses/<warehouse>/ui/.

def save_to_workspace() -> None:
    """Databricks App: keep the project on the volume (only when something changed since the last save)."""
    try:
        if hosting.push(ss.project):
            from datetime import datetime

            ss.saved_at = datetime.now().strftime("%H:%M:%S")
    except Exception as e:  # noqa: BLE001 - shown, never lost silently
        st.sidebar.warning(f"Could not save the project to the workspace: {str(e).splitlines()[0][:200]}")


try:
    ui_for(source_key).run(raw)
finally:
    if APP:
        save_to_workspace()  # also when a step stops or reruns the page
