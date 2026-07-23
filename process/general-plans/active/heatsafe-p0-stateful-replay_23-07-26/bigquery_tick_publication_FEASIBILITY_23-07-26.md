---
slug: bigquery-tick-publication
date: 2026-07-23
verdict: INCONCLUSIVE
originating-phase: pvl
---

## Hypothesis

A fenced BigQuery multi-statement transaction can allow exactly one concurrent tick owner to publish and can roll back every tick-visible table on injected failure.

## Mechanism Under Test

Conditional lease acquisition, owner/expiry revalidation inside a multi-table BigQuery transaction, transaction-conflict handling, historical-partition MERGE, and staging-table expiry.

## Probe Family

3 — tRPC / Prisma / DB query (cloud database query variant).

## Probe Cost Class

`needs-live-provider`. The safety gate was not met: this RESEARCH turn does not authorize creating a disposable BigQuery dataset or running billed/mutating cloud queries.

## Probe Method

The probe was not run. The execution gate will create an isolated disposable dataset, pre-create one coordinator and one tick row, release two clients through a barrier, inject a mid-publication failure, retry a historical partition, and inspect staging expiry and processed bytes.

## Evidence Captured

No live output was captured. Source inspection confirms current `merge_rows()` provides only independent per-table MERGE atomicity; official BigQuery documentation confirms multi-statement transactions and conflict cancellation, but not this application-specific fencing implementation.

## Verdict

INCONCLUSIVE

## Resulting Design Constraint

- **What this licenses:** Keep the fenced transaction as the candidate design and implement it only behind a disposable-dataset Hybrid gate.
- **What this forbids:** Do not claim concurrent publication, rollback, or delayed-retry safety from unit tests or documentation alone; do not run the probe against the shared demo dataset.
- **What remains uncertain (known-gap):** Exact conflict/error behavior, affected-row winner proof, processed bytes, and staging expiry under the final SQL.
