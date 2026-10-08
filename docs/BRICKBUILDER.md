# Path to a Databricks Brickbuilder Solution: Wishtree WishBridge

Brickbuilder Solutions are partner-built migration and industry solutions that Databricks reviews and
features on databricks.com. **They are granted to a Databricks consulting partner, not to a piece of
software.** The tool is one part of the submission; your company's partner status and customer track record
are the rest. Requirements change. Confirm the current criteria with your Databricks Partner Manager or in
the Databricks Partner Portal before you plan dates.

## What Databricks typically looks for

| Area | Typical expectation | Status for WishBridge |
|---|---|---|
| Partner status | Registered Databricks Consulting & SI partner, at a tier that allows Brickbuilder submissions | ☐ Confirm with Partner Manager |
| Certified people | Databricks-certified engineers/architects on the team delivering the solution | ☐ Check counts in Partner Portal |
| Customer proof | Real customer implementations of the solution, with referenceable outcomes (time saved, objects migrated) | ☐ None yet. **This is the main gap** |
| Repeatable solution | Documented method + accelerators that make the delivery faster and lower-risk | ☑ WishBridge v1.0 (this repo) |
| Technical validation | Databricks reviews architecture and a working demo on Databricks | ☑ Full pipeline demonstrated on a Databricks workspace (deploy, CALL of migrated procedures, load, reconcile); ◐ not yet against a live SQL Server through Lakehouse Federation |
| Go-to-market assets | Solution brief, architecture diagram, demo video, pricing/packaging of the offer | ☐ To create |

## Gap plan

### 0–30 days: make the product solid
- [x] Run `wishbridge deploy`, `load --execute` and `reconcile` end-to-end on a dev workspace (done with a
      stand-in source schema; see the README "Tested end to end").
- [ ] Repeat with a real SQL Server source over Lakehouse Federation.
- [ ] Grow the rule set from real customer code (every manual fix you make → a rule + a test).
- [x] Add rules for Snowflake, Oracle and Teradata.
- [ ] Try `wishbridge convert --ai` with a Claude API key on real code and review the suggestions.
- [ ] Do a trademark search on the name "WishBridge" before using it publicly; rename if it conflicts.
- [ ] Decide the licence for WishBridge itself (internal-only, or source-available to customers).

### 30–90 days: get customer proof
- [ ] Use WishBridge on 1–2 real (or pilot) migrations. Record the numbers the report already captures:
      files, % ready without manual work, issues auto-fixed, statements validated, tables reconciled, hours spent vs estimate.
- [ ] Turn them into case studies with customer approval.

### Submission
- [ ] Solution brief (1–2 pages): problem, approach, architecture, outcomes.
- [ ] Architecture diagram: LakeBridge analyze/transpile → WishBridge rules/AI → SQL warehouse → Federation/COPY INTO → reconcile.
- [ ] 3–5 minute demo video: `wishbridge run` on the example project, then the HTML report.
- [ ] Submit through your Partner Manager / Partner Portal and schedule the technical review.

## Rules while you wait
- Don't say or imply that Databricks endorses, certifies or approved WishBridge until it is accepted.
- Use Databricks names and logos only as the partner brand guidelines allow. Describing WishBridge as
  "built on Databricks Labs LakeBridge" is a factual statement and is fine.
- Keep the LakeBridge licence terms: WishBridge is used only for migrations to Databricks, and calls LakeBridge
  rather than redistributing it.

## Talking points (from what the tool measures)
- LakeBridge converts the bulk of the code. WishBridge makes that output **deployable and verifiable**:
  it auto-fixes mechanical leftovers, catches mis-conversions the transpiler doesn't flag (for example an
  `UPDATE … FROM … JOIN` turned into a `MERGE INTO <alias>`), validates every statement on a SQL warehouse,
  moves the data and proves it matches.
- WishBridge tells the client **what should move**: its fit check separates reporting / ETL logic (move to
  Databricks) from application logic (keep with the app, feed the data via Lakehouse Federation, Lakeflow Connect or CDC).
  LakeBridge converts whatever it is given; this advice avoids migrating a live application's database by mistake.
- Every run produces a client-ready report with the numbers above. Those same numbers become your case-study metrics.
