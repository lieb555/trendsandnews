# Atheria Law · AI Working Group Trend Tracker

A tracker of AI litigation, regulation, and insurance coverage, built for a
practice spanning AI law, insurance policy wording, and professional liability
for architectural/engineering/construction firms building data centres.

**What makes it different from the public trackers it draws on:** every case
carries a coverage overlay — the lines of insurance likely to respond, the
likely trigger clauses, and the exclusions likely to be invoked. No competing
tracker captures that.

- **Live tracker:** https://claude.ai/code/artifact/7418b1b0-c46b-46b5-8363-03a098efd17e
- **Technical specification:** [`SPEC.md`](SPEC.md)
- **Investment memo for partners:** the *What's Next* tab in the tracker

---

## Current state

| | |
|---|---|
| Corpus | 263 items — litigation, regulation, sanctions, data-centre disputes |
| Curation | Manual. Automation is Phase 2; see [`SPEC.md` §10](SPEC.md) |
| Presentation | Single `index.html`, published as a Claude artifact |
| Hosting cost | $0 — the artifact runtime provides hosting, auth, and storage |

---

## Running the ingest calibration

A one-time diagnostic that answers *"is the pre-filter good enough to build
on?"* before committing the hours. No local software required — it runs on
GitHub Actions.

### One-time setup

1. Generate a CourtListener API token at
   https://www.courtlistener.com/profile/api/
2. In this repo: **Settings → Secrets and variables → Actions →
   New repository secret**
   - Name: `COURTLISTENER_TOKEN`
   - Value: your token

The token is encrypted, never printed in logs, and never committed.

### Running it

**Actions** tab → **Calibrate ingest filter** → **Run workflow**. Adjust the
lookback window if you like, then run. Results appear on the run summary page;
the full candidate list downloads as a workflow artifact.

If the run fails, re-run with **debug** ticked — that dumps the raw API
response so the fix is usually a one-line correction to `fetch_page()` or
`normalize_result()` in the script.

### What it measures

- **Recall** — of the AI cases we already know about, how many would the filter
  catch? Measured automatically against our own corpus. This is the number that
  matters most, because recall failures are invisible in production: a case the
  filter silently drops never appears to be noticed missing.
- **Precision** — of what the filter surfaces, how much is genuinely relevant?
  Judged by reading the sample it prints.

### Result so far

Run against our 103 federal corpus items:

```
litigation (AI-as-subject)   97% recall   35/36
sanctions  (AI-as-conduct)   15% recall   10/67
```

The gap is structural, not a tuning problem, and it changed the ingestion
design from one stream to three. See [`SPEC.md` §5.1a](SPEC.md).

---

## Layout

```
index.html                          the tracker (data currently inline)
SPEC.md                             technical specification, revision 2
scripts/
  calibrate_courtlistener.py        ingest calibration diagnostic
.github/workflows/
  calibrate.yml                     manual-trigger calibration run
.env.example                        template; copy to .env for local use
```

`.env`, `out/`, and `private/` are gitignored and never enter a build.

---

## Where this is going

Phase 1 splits the case data out of `index.html` into versioned files with a
build script — unglamorous, and the highest-leverage step available, because it
makes every later change roughly eight times cheaper. Phase 2 adds automated
weekly ingestion with a pull-request review gate, at which point the tracker
maintains itself on about twenty minutes of attorney time per week.

Full sequencing, effort estimates, and cost analysis in [`SPEC.md`](SPEC.md).
