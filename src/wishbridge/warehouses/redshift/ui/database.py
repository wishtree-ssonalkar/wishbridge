"""Code step: read the tables (and code) from the client's database - shared by every warehouse."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from wishbridge.warehouses.redshift.state import load_state
from wishbridge.warehouses.redshift.ui import helpers as h
from wishbridge.warehouses.redshift.ui.screens import warehouse_ui
from wishbridge.warehouses.redshift.ui.common import code_files_input, code_from_database, has_code_files, use_database_code

ss = st.session_state


def database_code_summary(cfg, raw) -> None:
    from wishbridge.warehouses.redshift.dbcode import FOLDER

    dc = load_state(cfg).get("dbcode")
    if not dc:
        return
    st.markdown("**Code read from the database**")
    counts = " · ".join(f"{n} {k.lower() if n != 1 else k.lower()[:-1]}" for k, n in sorted(dc["counts"].items())) or "no objects"
    st.markdown(f"{counts} (from {dc['read_from']}, {dc['read_at'].replace('T', ' ')}), saved as one .sql file per object.")
    if dc.get("encrypted"):
        st.warning(f"{len(dc['encrypted'])} object(s) are encrypted in the database and cannot be read — ask the client "
                   f"for their scripts: {', '.join(dc['encrypted'][:10])}")
    if dc.get("unreadable"):
        st.warning(f"{len(dc['unreadable'])} object(s) could not be read: the login needs VIEW DEFINITION. "
                   f"{', '.join(dc['unreadable'][:10])}")
    using_db = raw.get("input", "input") == FOLDER
    if warehouse_ui(cfg).ASSESS_TOGETHER and has_code_files(raw):
        st.success(f"Assessed **together with the packages**: the packages as {cfg.source.label}, this code as the "
                   "warehouse database's SQL. Both are converted, counted in the estimate and in the zip.")
        return
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
    from wishbridge.warehouses.redshift import inventory as inv_mod
    from wishbridge.warehouses.redshift import schema as schema_mod

    inv = load_state(cfg).get("inventory")
    from_db = code_from_database(raw)
    ui = warehouse_ui(cfg)
    if from_db:
        title = "Read the tables and code from the database"
        intro = ("This project starts from the client's database: WishBridge reads the list of tables, columns and "
                 "data types and the **procedures, views, functions and triggers** stored in it — **no data is read "
                 "or copied**. It writes a Databricks `CREATE TABLE` script for every table.")
    else:
        title, intro = ui.database_text()  # per warehouse: ui/warehouses/
    with st.expander(title, expanded=from_db or bool(load_state(cfg).get("dbcode")) or ui.open_database_section(inv)):
        st.markdown(intro)
        how = st.radio("How to read them", ["Connect to the database", "Send a query to the client's DBA"],
                       horizontal=True, key="schema_how",
                       help="Connecting works for SQL Server - on the client's network or in the cloud as Azure SQL "
                            "Database or Azure Synapse. The DBA query works for every database WishBridge supports.")
        if how == "Connect to the database":
            where = st.radio("Where is the database?", ["local", "cloud"], horizontal=True, key="live_where",
                             format_func={"local": "On the client's network or this computer",
                                          "cloud": "In the cloud: Azure SQL Database / Azure Synapse"}.get)
            cloud = where == "cloud"
            c1, c2 = st.columns(2)
            server = c1.text_input("Server :red[*]", key="live_server",
                                   placeholder="myserver.database.windows.net  or  myworkspace.sql.azuresynapse.net"
                                   if cloud else "myserver\\SQLEXPRESS or myserver,1433")
            database = c2.text_input("Database :red[*]", key="live_db",
                                     placeholder="the warehouse database (Synapse: the dedicated SQL pool)" if cloud else "")
            kind = c1.selectbox("Database type", list(inv_mod.LIVE_DATABASES), key="live_type",
                                format_func={"sqlserver": "SQL Server / Azure SQL", "synapse": "Azure Synapse"}.get)
            logins = ({"entra": "Microsoft Entra ID (sign in in the browser)",
                       "entra-password": "Microsoft Entra ID user and password",
                       "sql": "SQL user and password"} if cloud else
                      {"windows": "Windows login", "sql": "User and password"})
            login = c2.radio("Login", list(logins), format_func=logins.get, horizontal=not cloud,
                             key=f"live_login_{where}",
                             help="Entra ID (Azure AD) opens Microsoft's sign-in window when you press Read - MFA "
                                  "works. The client gives you a read-only account." if cloud else None)
            user = password = ""
            if login in ("sql", "entra-password"):
                user = c1.text_input("User :red[*]", key="live_user",
                                     placeholder="name@client.com" if login == "entra-password" else "")
                password = c2.text_input("Password :red[*]", type="password", key="live_pw",
                                         help="Used once to read the tables; never saved.")
            elif login == "entra":
                user = c1.text_input("Your Entra ID e-mail (optional)", key="live_user_entra",
                                     placeholder="name@client.com",
                                     help="Pre-fills the Microsoft sign-in window that opens when you press Read.")
            if cloud:
                trust = False  # Azure has a trusted certificate: it is checked as usual
                st.caption("Azure's certificate is checked as usual. If the read is refused by the firewall, the client "
                           "adds this computer's IP address in the Azure portal (SQL server > Networking).")
                if server.strip() and not inv_mod.is_azure_host(server):
                    st.caption(":orange[That does not look like an Azure server name (...database.windows.net or "
                               "...sql.azuresynapse.net).]")
            else:
                trust = st.checkbox("Trust the server certificate", key="live_trust",
                                    help="Needed for servers with a self-signed certificate (common on local servers).")
                if inv_mod.is_azure_host(server):
                    st.info("This is an Azure server: choose **In the cloud** above to sign in with Microsoft Entra ID.")
            with_code = st.checkbox("Also read the code (procedures, views, functions, triggers)",
                                    value=from_db or ui.READ_CODE_DEFAULT,
                                    key="live_code", help="Saved as one .sql file per object. Encrypted objects "
                                                          "cannot be read; they are listed so the client can send them.")
            missing = [n for n, v in [("server", server), ("database", database)] if not v.strip()]
            if login in ("sql", "entra-password"):
                missing += [n for n, v in [("user", user), ("password", password)] if not v.strip()]
            if missing:
                st.caption(f":red[Fill in the {' and '.join(missing) if len(missing) < 3 else ', '.join(missing)} to read the tables.]")
            if st.button("🔍 Read the tables and code" if with_code else "🔍 Read the tables", type="primary",
                         disabled=bool(missing), key="live_read"):
                with st.spinner(f"Reading from {server} / {database}..."):
                    try:
                        inv_mod.read_live(cfg, server, database, login == "windows", user, password, trust, kind,
                                          with_code=with_code, auth=login)
                    except (RuntimeError, ValueError) as e:
                        st.error(str(e))
                    else:
                        ss.pop("live_pw", None)
                        if with_code and not (ui.ASSESS_TOGETHER and has_code_files(raw)):
                            if has_code_files(raw):
                                from wishbridge.warehouses.redshift import dbcode

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
                    (" and are created in Databricks first when you deploy." if cfg.phase != "assessment" else
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
        if cfg.phase != "assessment" and b2.button("Use these tables for the data copy"):
            new = dict(raw)
            new.setdefault("data", {})["tables"] = inv_mod.table_list(inv)
            h.save_raw(ss.project, new)
            st.success("Saved to Settings > Data. Check the mapping there.")
            st.rerun()
