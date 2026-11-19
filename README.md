# recallnotice

A small offline data-processing service with a local HTTP fixture and SQLite storage. It is self-contained and does not require network access.

## Usage

```text
make run       # process the bundled source
make run_inc   # process using the incremental command-line mode
make verdicts  # review adjudications for the stored records
make test      # run the test suite
make mock      # start the local fixture service
```

The package exposes a Python entry point under `src/recallnotice`. The fixture service supports the source formats used by the package, and the command-line entry point writes results to the local SQLite database. Re-running commands is safe for an existing database.

## Adjudication

The same recall may arrive through multiple channels. Adjudication is a pure function of the distinct announcements stored, so arrival order, re-runs, source outages and late revisions never change a conclusion arbitrarily. Each product group receives exactly one verdict:

| state | meaning |
| --- | --- |
| `pending` | 尚待确认 — a single announcement, awaiting corroboration |
| `published` | 已发布 — independent announcements agree on the value |
| `conflict` | 相互矛盾 — announcements disagree; the earliest published value stays incumbent |
| `insufficient` | 资料不足 — no usable value on record |

Repetition never decides: identical re-publications collapse into one announcement, and a channel that sends more copies cannot outvote earlier history. Every verdict records the candidate announcements (`candidates`, with source record ids) and the `basis` for the decision; verdicts are persisted in the `verdicts` table and can be reviewed with `make verdicts`.

Records are stored as raw source content, with adjudication kept separately, so historical data and unchanged records remain usable across re-runs. Databases written by earlier versions are migrated automatically on the next run.
