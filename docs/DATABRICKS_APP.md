# WishBridge as a Databricks App

WishBridge runs in two places, from the same code and with **the same screens**:

| | Option 1 · Laptop | Option 3 · Databricks App |
|---|---|---|
| Start | `Start WishBridge.cmd` (http://localhost:8501) | The app's link, with Databricks single sign-on |
| Databricks | A saved login (profile) | The app's own identity (service principal) |
| Projects | Folders under `C:\WishBridge` | One zip per project on a Unity Catalog volume, saved after every change |
| Assessment | Open a folder, read the database, analyze, convert - offline | Upload the code as a zip, analyze, convert (the code is processed in the workspace) |
| Code migration | ✓ | ✓ (opens the assessment zip; *Analyze and convert again* works too) |
| Data migration | Client's database (federation), exported files, a database on this computer | Client's database (federation), exported files |
| Converters | BladeBridge and Morpheus (Java) | BladeBridge (Morpheus needs Java: laptop) - automatic mode starts with BladeBridge |
| Claude | Claude Code, the workspace's model, or an API key | The workspace's model or an API key |

Every choice shows in both places. One that cannot work where you are says so - **"On the laptop only"** - and
what to use instead (for example *Upload the code (zip)* instead of a local folder).

## The usual flow

1. **Assessment on the laptop** at the client (offline, full conversion) → the assessment zip.
2. **Code migration in the app in Wishtree's workspace**: *Code migration › Open the assessment zip*, fix, deploy to
   a test schema, check.
3. When the client agrees, **deploy the app into the client's workspace**, open the project there (*Open the
   assessment zip* again), press **Move this project to …** in Settings if asked, deploy and copy the data.

Opening the same zip twice makes `name-2`, `name-3`: the first copy stays in *Open a project*.

The migration toolkit (analyzer and BladeBridge converter) is installed in the app from PyPI when the app first
starts - about a minute; **App check details** shows *Migration toolkit: installed*. The toolkit's own output for
each run is in the project at `output/logs/toolkit_analyze.log` and `toolkit_transpile.log`.

## Deploy it

On a laptop with the Databricks CLI and a login to the workspace (`databricks auth login --profile CLIENT`):

```powershell
.\deploy-databricks-app.ps1 -Profile CLIENT -WarehouseId <sql-warehouse-id> `
    -ProjectsVolume /Volumes/<catalog>/wishbridge/projects -TargetCatalogs <catalog-for-test-schemas> `
    -ShareWith <team-group>
```

The script creates the projects volume, creates the app with the SQL warehouse as its resource, grants the app's
identity what it needs, checks that the code compiles, uploads WishBridge (only the changed files on a later run)
and starts it, then prints the link. Run it again to deploy a new version; the projects stay on the volume.

Then give people access: **Compute › Apps › wishbridge › Permissions** (CAN USE).

Wishtree's test app: https://wishbridge-7474656385457319.aws.databricksapps.com (workspace dbc-fd31c11a-4da6,
projects in `/Volumes/workspace/wishbridge/projects`).

## What the app's identity needs

The script grants the first three; the rest depends on what the project does and is given by a workspace admin.

| Permission | For |
|---|---|
| CAN USE on the SQL warehouse (app resource) | Every statement |
| USE CATALOG, USE SCHEMA, READ VOLUME, WRITE VOLUME on the projects volume | Keeping the projects |
| USE CATALOG + CREATE SCHEMA on the target catalog(s) | Creating the test schema and its tables |
| USE SCHEMA + READ VOLUME on the client's export volume | Data copy from exported files |
| CREATE CONNECTION and CREATE CATALOG on the metastore | Data copy through Lakehouse Federation |
| CAN QUERY on a Claude serving endpoint (or an API key as an app secret) | *Ask Claude*, once the client agreed |

What the app creates belongs to the app's identity, so WishBridge gives **every privilege on each test schema to the
person using the app** (and to the `-ShareWith` groups). Notebooks go to `/Workspace/Shared/wishbridge/<project>`.

## Tested end to end in Wishtree's workspace (22 Oct 2026)

| Step in the app | Result |
|---|---|
| App check (identity, warehouse, projects volume) | ✅ |
| Open the assessment zip (made on the Windows laptop) | ✅ converted files shown, project saved on the volume |
| Settings: the app's workspace, catalog `test`, new schema `wishbridge_app_e2e` | ✅ created by the app, shared with the user |
| Deploy | ✅ 20/21 statements, 8 tables + 2 views + 3 notebooks (the 1 failure is the known file to fix) |
| Data migration from exported files: check the folder, map, Replace, copy, compare | ✅ 7/8 tables, 21,066 rows, all 7 match; copying again gives the same counts |
| Data report and downloads (3 reports, everything zip, sign-off) | ✅ |
| Assessment by uploading the code zip (23 Oct: with analyze and convert) | ✅ 3 files, 1.5 h, 2 review / 1 needs fix - **the same as the laptop** on the same code |
| Code migration from that assessment, with *Analyze and convert again* | ✅ analyze, convert, deploy 3/3, notebooks in /Workspace/Shared, schema shared with the user |
| Claude through the workspace | ⚠️ works, but this workspace serves no Claude model: use an API key or enable one in Serving |

The 8th table (`FactDailySales_Archive`) is empty in the source and has no exported file: the report says so.

## Good to know

- The app checks its access when it opens (**App check details** in the sidebar) and says what is missing.
- One person per project at a time: two people saving the same project overwrite each other's changes.
- Nothing leaves the workspace: no passwords are stored, the projects stay on the workspace's volume.
- A run that stops shows what it said last (*What the run said last*); its full log is
  `output/logs/pipeline.log` in the project.
