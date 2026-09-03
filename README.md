# Atheria Law · AI Working Group Trend Tracker

A tracker of AI litigation, regulation, and insurance coverage, built for a
practice spanning AI law, insurance policy wording, and professional liability
for architectural/engineering/construction firms building data centres.

**What makes it different from the public trackers it draws on:** every case
carries a coverage overlay — the lines of insurance likely to respond, the
likely trigger clauses, and the exclusions likely to be invoked. No competing
tracker captures that.

- **Live tracker:** https://claude.ai/code/artifact/7418b1b0-c46b-46b5-8363-03a098efd17e
- **State AI law exposure map:** [`laws.html`](laws.html) — 181 state measures scored
  from an insurance-coverage angle; see *The state-law tracker* below
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

## The state-law tracker

`laws.html` is a second, self-contained page: every AI statute on the books in
the states, scored and sorted the way an insurer or a broker reads it rather
than the way a legislature files it.

The source is a weekly research drop, `data/laws/INDEX.csv` — one row per
measure, 39 columns of primary-source findings (who is regulated, private right
of action, damages formula, cure period, safe harbour, criminal overlay,
territorial nexus, dates). The page adds three derived axes on top of it:

| Axis | What it answers |
|---|---|
| **Claim exposure**, 0–100 | How likely is this statute to produce a claim someone tenders? Private right of action (30), aggregation potential (20), damages quantum (20), fee-shifting (10), absence of a cure period or safe harbour (10), public enforcement (10). |
| **Insurability friction** | Once the claim arrives, what pushes it outside the policy? Criminal overlay, punitive damages, disgorgement, civil penalties, statutory bans on waiver and indemnity. |
| **Controls available** | What can a compliant insured actually point to? Cure periods, notice-before-suit, NIST AI RMF / ISO 42001 defences, size thresholds. |

Nothing is inferred by a model. Every derived field is arithmetic over columns
an attorney filled in, and each measure's brief shows the derivation component
by component so it can be checked against the statute.

Six views: an exposure cartogram (shadeable by peak exposure, volume, private
rights of action, pending duties, or measures with no off-ramp), a filterable
ledger, a commencement-date runway for renewal planning, a coverage-line pivot,
subject-adoption curves, and the method.

### Refreshing it

Drop the new CSV over `data/laws/INDEX.csv` and run:

```
python3 scripts/build_laws.py --inject
```

That rewrites `data/laws.json`, injects it into `laws.html`, and prints a
summary including the diff against the previous build — new rows, changed rows,
dropped rows. The diff is the week's review list; unchanged rows need no
re-reading. Pushing the CSV also triggers `.github/workflows/build-laws.yml`,
which does the same thing and commits the result.

No network calls, no API keys, no backend. Python 3.9+ standard library only.

### Exporting a standalone document

`laws.html` is already standalone — one file, no backend, opens from a thumb
drive offline (the Google Fonts link degrades to system faces). That covers
"send someone the page."

For something that goes in a claim file, an email attachment, or across a table
at a renewal meeting, `scripts/export_doc.py` renders the same data as a
paginated brief instead: cover, contents, the exposure map, coverage-line and
runway tables, the full ledger, then every measure written out in full with its
score decomposed, and a method appendix.

```
python3 scripts/export_doc.py                          # the whole index
python3 scripts/export_doc.py --state CA --state NY    # a two-state brief
python3 scripts/export_doc.py --band acute --pending    # what lands next, that matters
python3 scripts/export_doc.py --line "Insurance Co. PL / E&O"
python3 scripts/export_doc.py --pra --state IL
```

Filters combine with AND and repeat to widen, so `--state CA --state NY` means
California *or* New York. Whatever was applied is printed on the cover, so an
excerpt can never be mistaken for the whole index.

Output lands in `out/`. Open it and print to PDF from the browser — Chrome
paginates it correctly and needs nothing installed. `--pdf` does that step for
you if Playwright and Chromium happen to be available.

Rough sizes: the full index runs about 380 pages; a two-state brief about 40.
Each measure gets its own page, which is deliberate — it means a single statute
can be pulled out and handed over on its own.

### Fonts, and making a copy that never changes

Both pages link Google Fonts. Online that is fine and costs the viewer nothing.
Offline — a laptop in a deposition, a machine behind a firewall, an attachment
opened on a plane — the link fails silently and the page falls back to system
faces. Nothing breaks, but the metrics shift and the document is no longer the
document that was reviewed.

`scripts/embed_fonts.py` inlines the faces as base64 so the file renders
identically with no network at all:

```
python3 scripts/embed_fonts.py laws.html --out out/laws-offline.html
python3 scripts/export_doc.py --state CA --embed-fonts --pdf
```

Nine faces, 172 KB of woff2, about +230 KB on the page. Three things keep that
number down: only the nine weights the CSS actually sets are fetched (the
stylesheet asks for seventeen); faces come from the v1 endpoint, which returns
a static instance rather than the variable font — Newsreader alone drops from
90 KB to 22 KB; and only the `latin` subset is embedded, because the corpus's
eight non-ASCII characters (`§ — – ' × ½` and the two comparison operators) need
nothing more. The script checks that and names any character it cannot cover, so
a future drop that needs `latin-ext` says so rather than quietly falling back.

Fonts are cached in `data/fonts/` and committed, so the build is reproducible
without network access.

**Which to use.** Leave `laws.html` linking the fonts — online it loads fine, and
230 KB on every view is a real cost for a page that is already 190 KB over the
wire. Embed for anything meant to be archival: a PDF going into a claim file, a
copy on a thumb drive, an email attachment.

Archivo, Newsreader, IBM Plex Sans and IBM Plex Mono are all SIL Open Font
License 1.1, which permits embedding; the generated CSS carries the attribution.

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

### What the calibration runs established

Four runs against the live service, 31 Aug 2026. Each killed one wrong
assumption, which is cheaper than discovering them after building the
pipeline.

**The API shape was right** — v4 `/search/`, `Token` auth, `type=r` for RECAP
dockets, `type=o` for opinion full text, `filed_after`, cursor pagination.
None of this is guesswork any more.

**CourtListener throttles search at 5 requests/minute.** This is the binding
constraint on the whole design. Paging the docket firehose and filtering it
locally — the original plan — needs roughly 15 hours of continuous polling to
cover one 90-day window. Run #1 scanned 100 dockets and kept 0, which is the
arithmetic working correctly rather than the filter failing. **The filter
therefore lives server-side**, as a `q` query the service answers with matches
only: a handful of requests per run instead of thousands.

**Quoted phrases do real phrase matching**, and the pipeline's precision rests
on it:

```
"artificial intelligence"      448 matches
 artificial intelligence     7,172 matches      16x looser
```

**`caseName:Term` works; `caseName:("X v. Y")` returns nothing** — the "v."
defeats it. A run that reported 0/6 known cases as unreachable was measuring
this bug, not the corpus.

Lane by lane, over a 90-day window:

| Probe | Scope | Matches | Verdict |
|---|---|---|---|
| Known AI defendants by name | dockets | 43 | **Production-ready.** ~100% precision |
| AI-as-subject keywords | dockets | 1,265 | Too broad; needs a second predicate |
| Sanctions | opinion full text | 93 | Works — reaches what metadata cannot |
| Data centres | dockets | 149 | Poor precision; "data center" is too generic |

Two findings worth the attorney's attention rather than the engineer's. The
sanctions hits are almost entirely **state appellate** courts — Virginia Court
of Appeals, Florida DCA, Pennsylvania Superior, Iowa Court of Appeals — which
is both a signal about where this is surfacing and the reason 93 sits so far
below Charlotin's 1,980. And the recall gap between lanes remains structural:
docket metadata reaches AI-as-subject litigation at 97% and sanctions at 15%,
because in a sanctions case the AI fact exists nowhere in the metadata, only
inside the judge's written order. See [`SPEC.md` §5.1a](SPEC.md).

---

## Layout

```
index.html                          the litigation tracker (data currently inline)
laws.html                           the state-law exposure map (data injected at build)
SPEC.md                             technical specification, revision 2
data/
  laws/INDEX.csv                    the weekly research drop — source of truth
  fonts/                            cached woff2 faces, so builds need no network
  laws.json                         derived; rebuilt by build_laws.py
  sources.yml                       source-tracker registry
scripts/
  build_laws.py                     CSV -> derived JSON -> laws.html
  export_doc.py                     derived JSON -> paginated brief (HTML/PDF)
  embed_fonts.py                    inline the web fonts for offline fidelity
  calibrate_courtlistener.py        ingest calibration diagnostic
.github/workflows/
  build-laws.yml                    rebuilds laws.html when the CSV changes
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
