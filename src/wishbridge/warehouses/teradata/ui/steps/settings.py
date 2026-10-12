"""Step 1 - Settings: the phase and source system; workspace and target for a migration; source data for a data
migration."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from wishbridge.warehouses.teradata import source_db as sd
from wishbridge.warehouses.teradata.config import CONVERTER_DIALECTS, SOURCES, ConfigError, converter_summary, looks_like_prod
from wishbridge.warehouses.teradata.dbx import SqlError, Warehouse
from wishbridge.warehouses.teradata.ui import helpers as h
from wishbridge.warehouses.teradata.ui.common import required

ss = st.session_state


def settings_problems(offline: bool, v: dict) -> list[str]:
    """What must be filled in before leaving Settings. Assessment needs only the source system; the code migration
    also needs the Databricks workspace and target; the data migration also needs the source data."""
    from wishbridge.warehouses.teradata.config import looks_like_prod

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
    if v.get("phase") != "data-migration":
        return problems  # the source data is needed only to copy the data
    if v.get("method") == "files":
        if not str(v.get("files_root") or "").strip():
            problems.append("Data to copy: the files folder (volume path) the exported tables are in")
    elif not str(v.get("source_catalog") or "").strip():
        problems.append("Source database: create the connection (Source database section) or enter the source catalog")
    return problems


def render(cfg, raw, packages) -> None:
    dbx = raw.setdefault("databricks", {})
    data = raw.setdefault("data", {})
    ai = raw.setdefault("autofix", {})

    phase_labels = {"assessment": "Assessment - offline: analyze and convert the code",
                    "code-migration": "Code migration - create the tables and code in Databricks",
                    "data-migration": "Data migration - copy the data and check it matches"}

    def _save_phase() -> None:
        new_ = dict(raw)
        new_["phase"] = ss.phase_radio
        h.save_raw(ss.project, new_)
        ss.goal = ss.phase_radio  # the sidebar choice follows
        ss.goal_applied = (ss.project, ss.phase_radio)

    from wishbridge.warehouses.teradata.config import normalise_phase

    cur_phase = normalise_phase(raw.get("phase"))
    phase = st.radio("Phase", list(phase_labels), format_func=phase_labels.get, horizontal=True, key="phase_radio",
                     index=list(phase_labels).index(cur_phase) if cur_phase in phase_labels else 1, on_change=_save_phase,
                     help="Assessment: nothing is sent to Databricks or the client's database. Switch to Migration for "
                          "the visit where the code is deployed and the data copied. Changing it saves right away.")
    offline = phase == "assessment"

    # In the data migration these were settled in the assessment and code migration: kept as they are, shown on request.
    source = cfg.source.key
    transpiler = str(raw.get("transpiler") or "auto").lower()
    map_df = pd.DataFrame(h.schema_map_rows(raw) or [{"source_schema": "", "target": ""}])
    show_project = phase != "data-migration" or st.toggle(
        f"Show the project settings ({cfg.source.label}, from the assessment and code migration)", key="show_project")
    if show_project:
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
            conv_labels = {"auto": "Automatic (recommended)", "morph": "First method only", "bladebridge": "Second method only"}
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
                "They appear here in the **Code migration** and **Data migration** phases.")

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

        # Data migration: workspace and target were set in the code migration - a ticked line, changed on request.
        settled = phase == "data-migration" and all(dbx.get(k) for k in ("host", "catalog", "schema"))
        change = not settled
        if settled:
            st.success(f"✅ **Workspace and target** (from the code migration): {dbx.get('host')} · "
                       f"`{dbx.get('catalog')}.{dbx.get('schema')}`")
            change = st.toggle("Change the workspace or target", key="change_target")
            target_done = True
        if change:
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

        if target_done and phase == "data-migration":  # the code migration needs no source data
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
                st.caption("Which tables to copy, only new rows or all, and whether to add to or replace what is in "
                           "Databricks are chosen in *Map the data*.")

    est = raw.get("estimate") or {}
    ai_on, model = bool(ai.get("ai", False)), ai.get("model", "claude-opus-5-5")
    hpi = float(est.get("hours_per_issue", 0.5))
    if phase != "data-migration":  # about converting the code: not part of the data migration
        with st.expander("AI suggestions and estimates"):
            ai_on = st.checkbox("Ask Claude for fix suggestions during convert (needs ANTHROPIC_API_KEY; sends code, "
                                "not data)", value=ai_on)
            model = st.text_input("Claude model", value=model)
            hpi = st.number_input("Hours per open item", value=hpi, step=0.25)

    problems = settings_problems(offline, locals())
    ready = offline or src_done or (phase == "code-migration" and target_done)
    to_data = phase == "data-migration"
    if problems and ready:
        st.markdown(":red[Fill in the fields marked * (see the red notes above) before going on.]")
    if ready and st.button("Next: map the data →" if to_data else "Next: add the code →", type="primary",
                           help="Saves the settings and opens the next step.", disabled=bool(problems)):
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
            ss.go_step = "data" if to_data else "code"
            st.rerun()
        except (ConfigError, KeyError, ValueError) as e:
            st.error(f"Not saved: {e}")
