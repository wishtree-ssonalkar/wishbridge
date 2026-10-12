# WishBridge for Amazon Redshift

This folder is the complete, independent WishBridge for **Amazon Redshift** projects
(project.yml `source:` `redshift`). Change anything here and only these projects are
affected; the other warehouses have their own copies.

| Where | What |
|---|---|
| `sources/redshift.py` (and `sources/` around it) | What WishBridge knows about the warehouse: table-list query, types, connection, fixes and checks |
| `analysis.py`, `convert.py`, `rules.py`, `deploy.py`, `data.py`, `mapping.py`, `reconcile.py`, `report.py`, `package.py`, ... | The pipeline: analyze, convert, deploy, copy the data, reconcile, report, zip |
| `ui/main.py` | The step bar; `ui/steps/` one file per step; `ui/screens/` the screens that differ per warehouse |
| `cli.py` | The project commands (`wishbridge convert -c project.yml`, ...) for these projects |

The shared main screen and `wishbridge` command are in `src/wishbridge/` (`ui/app.py`, `cli.py`); they open the
project and hand over to this folder.
