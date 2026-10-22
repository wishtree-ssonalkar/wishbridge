# WishBridge as a Databricks App

WishBridge runs in two places, from the same code:

| | Option 1 · Laptop | Option 3 · Databricks App |
|---|---|---|
| Used for | The **assessment**, at the client, offline | **Code migration** and **data migration** |
| Runs on | The consultant's laptop (`Start WishBridge.cmd`) | Inside a Databricks workspace (Wishtree's for testing, then the client's) |
| Sign-in | A saved Databricks login (profile) | Databricks single sign-on; the app works with its own identity (service principal) |
| Projects | Folders under `C:\WishBridge` | One zip per project on a Unity Catalog volume, saved after every change |
| Claude | Claude Code, the Databricks workspace's model, or an API key | The workspace's Claude model (or an API key) |

## The flow

1. **Assessment on the laptop** at the client: read the code and the database, convert, report, *Assess and save
   everything* → the assessment zip.
2. **Code migration in the app in Wishtree's workspace**: *Code migration › Open the assessment zip*, fix, deploy
   to a test schema, test.
3. When the client agrees, **deploy the app into the client's workspace** (below), open the same project there
   (*Open the assessment zip* again, or copy the project's zip to the client's volume), press **Move this project
   to …** in Settings, then deploy and copy the data there.

What the app does not do (it runs in the workspace, not at the client): the assessment, converting code again
(it uses the conversion from the assessment zip), and copying from a database on someone's computer. To convert
again, assess again on the laptop and open the new zip in the app.

## Deploy it

On a laptop with the Databricks CLI and a login to the workspace (`databricks auth login --profile CLIENT`):

```powershell
.\deploy-databricks-app.ps1 -Profile CLIENT -WarehouseId <sql-warehouse-id> `
    -ProjectsVolume /Volumes/<catalog>/wishbridge/projects -TargetCatalogs <catalog-for-test-schemas>
```

The script creates the projects volume, creates the app with the SQL warehouse as its resource, grants the app's
service principal what it needs, uploads WishBridge and starts it, and prints the app's URL. Run it again to
deploy a new version; the projects stay on the volume.

Then give people access: **Compute › Apps › wishbridge › Permissions** (CAN USE).

## What the app's identity needs

The script grants the first three; the rest depends on what the project does and is given by a workspace admin.

| Permission | For |
|---|---|
| CAN USE on the SQL warehouse (app resource) | Every statement |
| USE CATALOG, USE SCHEMA, READ VOLUME, WRITE VOLUME on the projects volume | Keeping the projects |
| USE CATALOG + CREATE SCHEMA on the target catalog(s) | Creating the test schema and its tables |
| CREATE CONNECTION and CREATE CATALOG on the metastore | Data copy through Lakehouse Federation |
| READ VOLUME on the client's export volume | Data copy from exported files |
| CAN QUERY on the Claude serving endpoint | *Ask Claude* (only when the client agreed) |

Notebooks are created in the app's own workspace folder (`/Workspace/Users/<app id>/wishbridge/<project>`).

## Good to know

- The app checks its access when it opens (**App check details** in the sidebar) and says what is missing.
- One person per project at a time: two people saving the same project overwrite each other's changes.
- Nothing is sent outside the workspace: no passwords are stored, the projects stay on the client's volume.
