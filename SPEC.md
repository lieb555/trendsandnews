# Atheria Law · AI Working Group Trend Tracker — Technical Specification

_Revision 3. Supersedes revision 2 of 2026-08-31. Owner: Nick Lieberknecht._

**What changed in this revision.** Revision 2 removed the backend: the artifact
runtime's `db` capability supplies authenticated, per-viewer-private storage, so
the Next.js + Postgres architecture of v1 was unnecessary and the plan became
pipeline-first.

Revision 3 rewrites §5 for scale. Revision 2 treated every source as a data
source, which works at eleven and fails at fifty-three, because the same
development appears in a dozen places and produces a dozen records at a dozen
times the curation cost. **The fix is to separate records from sightings**: a
tracker is a corroboration signal, not a data source. Deduplication then happens
on primary-source identity, enrichment is paid once per entity rather than once
per mention, and the number of independent trackers covering something becomes a
free relevance score. Overlap stops being waste and becomes the ranking
mechanism — which is what lets the source set grow to fifty-three while the
weekly review stays fixed at the twenty-five items the attorney budgeted.

---

## 1. Goals, in priority order

1. **Stay current with minimal recurring input.** Target steady state: ~20 minutes per week reviewing and merging an automated batch.
2. **Preserve the coverage overlay as the differentiator.** Every item carries lines of insurance implicated, likely trigger clauses, and likely exclusions in play. No competing tracker does this.
3. **Be cheap to operate and cheap to change.** Both in cash and in Claude credits.
4. **Serve two lenses on one corpus** — the litigator's view (theories, rulings, plaintiff-bar patterns) and the underwriter's view (coverage lines, triggers, exclusions).
5. **Support private and team annotation** without standing up infrastructure.

Non-goals for this revision: public microsite (see §7.3 for why it conflicts), multi-tenant SaaS, exhaustive corpus coverage.

---

## 2. Architecture

### 2.1 The shape

```
Git repo (private)                 GitHub Actions            Claude Artifact
┌────────────────────────┐        ┌────────────────┐        ┌──────────────────┐
│ data/items/YYYY.json   │───┐    │ weekly cron    │        │ Tracker UI       │
│ data/taxonomy.json     │   │    │                │        │                  │
│ data/landmarks.json    │   ├───▶│ 1. fetch       │──PR──▶ │ db capability:   │
│ data/sources.json      │   │    │ 2. dedupe      │ review │  ├ notes/shared  │
│ templates/index.html.j2│───┘    │ 3. triage      │ +merge │  └ data/users/   │
│ scripts/ingest/*.py    │        │ 4. enrich      │   │    │       {self}/    │
│ scripts/build.py       │        │ 5. open PR     │   │    │       notes/     │
└────────────────────────┘        └────────────────┘   │    └──────────────────┘
                                                        │             ▲
                                                        └── rebuild ──┘
```

Three planes, deliberately decoupled:

- **Data plane** — JSON in git. Versioned, diffable, reviewable. Never loaded wholesale.
- **Pipeline plane** — deterministic Python plus batched LLM calls, running on GitHub's runners.
- **Presentation plane** — a Jinja template compiled to a single HTML artifact.

### 2.2 Why this rather than an app

| Requirement | v1 answer | v2 answer |
|---|---|---|
| Authentication | Auth.js + session store | Artifact runtime — every viewer is a signed-in org member |
| Private notes | Postgres table + row-level auth | `db` at `data/users/{self}/notes/` — private even from the owner |
| Team notes | Postgres + permissions | `db` at `notes/shared/` with write gated to editors |
| Persistence | Managed Postgres, ~$19/mo | Included in the artifact runtime |
| Hosting | Vercel, ~$20/mo | Included |
| Version history | Application-level audit table | Git, free |
| Shareable filtered views | Server-rendered scoped routes | URL query params on a static page |

The backend earned its place in v1 only because of notes. With that requirement met by the runtime, nothing else in the product justifies the operational surface.

### 2.3 Repository layout

```
trendsandnews/
├── CLAUDE.md                      # schema, taxonomy, conventions, invariants
├── .claude/
│   ├── skills/
│   │   ├── add-source/            # recipe: wire up a new ingest source
│   │   ├── weekly-pulse/          # recipe: draft the editorial narrative
│   │   └── verify-batch/          # recipe: run verification on new items
│   └── settings.json              # permission allowlist for routine commands
├── data/
│   ├── items/
│   │   ├── 2023.json              # sharded by year — no file exceeds ~40KB
│   │   ├── 2024.json
│   │   ├── 2025.json
│   │   └── 2026.json
│   ├── intelligence/              # research & market publications — §5.5
│   │   └── 2026.json              #   separate corpus; not timeline-eligible
│   ├── projects/                  # data-centre project registry — §5.7
│   │   └── registry.json          #   the precondition for DC dispute matching
│   ├── taxonomy.json              # tracks, coverage lines, triggers, exclusions
│   ├── landmarks.json             # milestone timeline events
│   ├── sources.yml                # THE source registry — 53 rows, 4 adapters
│   └── sightings/                 # rolling window of raw observations
│       └── 2026-W35.jsonl         #   pruned after entities are merged
├── private/                       # gitignored — never enters a build
│   └── notes-export.json          # optional local backup of db notes
├── scripts/
│   ├── adapters/                  # FOUR of these, not one per source — §5.4
│   │   ├── api.py                 #   CourtListener, Fed Register, EUR-Lex…
│   │   ├── feed.py                #   RSS/Atom — most trackers
│   │   ├── sitemap.py             #   sitemap.xml diff
│   │   └── manual.py              #   Charlotin CSV, bulk imports
│   ├── resolve.py                 # extraction ladder → canonical keys — §5.2
│   ├── merge.py                   # sightings → entities; near-miss report
│   ├── score.py                   # salience — §5.3
│   ├── enrich.py                  # batched Anthropic API calls
│   └── build.py                   # data + template → dist/index.html
├── templates/
│   └── index.html.j2              # ~16KB: CSS, app JS, view shells
└── .github/workflows/
    ├── ingest.yml                 # weekly cron → opens PR
    └── deploy.yml                 # on merge to main → build + publish
```

The shape to notice is `scripts/adapters/` holding four files rather than
fifty-three. Source-specific knowledge lives in `data/sources.yml` as
configuration, so adding a source is a data change reviewable in a diff — not a
code change requiring a new module, new tests, and a new failure mode.

**Invariant: no file in `data/` or `templates/` exceeds ~50KB.** This is the credit-efficiency constraint that makes everything else affordable. The current single-file artifact is 550KB and costs ~137k tokens to open; after the split, a presentation change costs ~16k and a data change costs ~30k.

---

## 3. Data model

### 3.1 Item

One record per matter, statute, sanction, or data-center dispute.

```jsonc
{
  "id": "bartz-anthropic",              // stable slug, never reused
  "type": "case",                       // case | sanction | regulation | datacenter
  "caption": "Bartz v. Anthropic PBC",
  "court": "N.D. Cal.",
  "docket": "3:24-cv-05417",
  "jurisdiction": "US",                 // US | UK | EU | INT
  "region": "NDCA",
  "date": "2024-08-19",
  "status": "decided",
  "tracks": ["ip"],                     // keys into taxonomy.json
  "trackLabels": ["IP — training data & output"],
  "parties": { "plaintiff": "...", "defendant": "..." },
  "summary": "...",
  "significance": "...",
  "insurance": {
    "lines":      ["Tech E&O", "Media Liability"],
    "triggers":   ["Copyright infringement offense", "Wrongful act"],
    "exclusions": ["IP exclusion", "Willful infringement"]
  },
  "sources": [{ "label": "...", "url": "..." }],

  // Identity — the deduplication key. See §5.2.
  "identity": {
    "key": "cand:3:24-cv-05417",        // canonical; the merge target
    "aliases": ["Bartz v. Anthropic"]   // captions seen in the wild
  },

  // Sightings — every place this entity was observed, primary and signal.
  // Replaces v2's single `provenance.source`, which could not represent
  // the same development appearing across a dozen trackers.
  "sightings": [
    { "source": "courtlistener",   "role": "primary", "firstSeen": "2026-09-01T06:00:00Z",
      "url": "https://www.courtlistener.com/docket/..." },
    { "source": "chatgpt-eating",  "role": "signal",  "firstSeen": "2026-08-30T00:00:00Z",
      "url": "https://..." },
    { "source": "gwu-ai",          "role": "signal",  "firstSeen": "2026-08-31T00:00:00Z",
      "url": "https://..." }
  ],

  // Salience — computed, not authored. Drives review order. See §5.3.
  "salience": {
    "score": 14.5,
    "trackerCount": 6,
    "categories": ["content-copyright", "law-firm", "academic"],
    "velocity": 3,                      // sightings in the last 7 days
    "leadTime": "P2D"                   // signal preceded primary record by 2 days
  },

  "provenance": {
    "enrichedBy": "claude-sonnet-5",    // model that drafted summary/overlay
    "reviewStatus": "pending",          // unverified | pending | reviewed | verified
    "reviewedBy": null,
    "confidence": 0.82
  }
}
```

Three additions carry the v3 design. `identity.key` is what deduplication
merges on. `sightings` is a list rather than a scalar, because the same
development legitimately appears in many places and collapsing that to one
source throws away the most useful signal we have. `salience` is computed from
the sightings and determines what the attorney reads first.

`sources` (attorney-authored citations, shown in the UI) stays distinct from
`sightings` (machine-recorded observations, used for ranking and credit). They
serve different readers and should not be merged.

### 3.2 Review status ladder

| Status | How it got there | Timeline | Client-facing |
|---|---|---|---|
| `unverified` | Tracker sighting only; no primary record yet | No | No |
| `pending` | Primary record resolved; not yet read | Yes | No |
| `reviewed` | Attorney skimmed; caption and claims look right | Yes | Yes |
| `verified` | Checked against Westlaw or primary source | Yes | Yes, green check |

This replaces the blanket "verify first" disclaimer with a per-item signal,
which is both more honest and more useful. `unverified` is the v3 addition: it
lets a fast-moving development enter the corpus the day a tracker reports it,
while keeping it out of the timeline and anything client-facing until a primary
source confirms. Promotion to `pending` is automatic (§5.6).

### 3.3 Companion entities

- **Landmark** — milestone timeline event. Fields: `date`, `label`, `description`, optional `itemId` linking to a case record.
- **Source** — tracker directory entry. Fields: `name`, `url`, `desc`, `slice`, `format`, `status` (`active` | `wishlist`), `priority`.
- **Taxonomy** — the stable classification vocabulary: 14 litigation tracks, data-center sub-tracks, coverage lines, trigger clauses, exclusion categories. Single source of truth for both the UI filters and the LLM enrichment prompt.

---

## 4. Taxonomy

Unchanged from v1 in substance; now externalized to `data/taxonomy.json` so the UI and the enrichment prompt read the same file.

**Litigation tracks (14):** IP — training data & output · Privacy & biometrics · Defamation & hallucination · Discrimination & algorithmic bias · Product liability & chatbot harm · Consumer protection & AI-washing · Securities & D&O · Contract, indemnity, IP ownership · Data centers · AI-specific criminal & fraud · Judge-level analytics (cross-cutting) · Employment/labor & AI · Export controls & national security · Cross-border enforcement

**Data-center sub-tracks (4):** Construction & AEC E&O · Environmental & land use · Power & grid · Security & operational

**Regulation track:** formal (statutes, rules, enforcement) · voluntary commitments · standards & frameworks · insurer regulatory filings

**Coverage lines:** Tech E&O · Cyber · D&O · Media Liability · General Liability · Product Liability · EPL · Property · AEC Professional Liability · Lawyers' Professional Liability · Excess

---

## 5. Ingestion pipeline

### 5.0 The reframe: sources produce two different things

The v2 design treated every source as a data source. With 11 sources that was
merely inefficient. With the 53 in the Coverage Gaps queue it does not work at
all, because the same development appears in a dozen places: *NYT v. OpenAI*
is covered by ChatGPT Is Eating The World, the GWU tracker, the AI Copyright
Case Tracker, Debevoise, White & Case, and the Authors Guild. Ingesting each
separately produces twelve records of one case and twelve times the curation
cost.

The fix is to separate two things the old model conflated:

| | **Records** | **Sightings** |
|---|---|---|
| Produced by | Primary sources — CourtListener, Federal Register, EUR-Lex, BAILII, legislation.gov.uk | Trackers — law firm, academic, trade press, broker |
| What it is | The authoritative fact | "Someone noticed something" |
| Citable | Yes — this is what an item links to | No — credited, never republished |
| Answers | *What happened* | *That it happened, and that it matters* |

**A tracker is not a data source. It is a corroboration signal.** It tells us
something occurred and that a competent practitioner thought it worth writing
up. The record itself is always fetched from the primary source.

This resolves four problems in one move: no tracker prose is ever republished,
so the copyright and ToS exposure disappears; deduplication happens on
primary-source identity rather than on twelve different headlines; enrichment
is paid for once per entity instead of once per mention; and — the part that
turns the problem into an asset — **the number of independent trackers
covering something becomes a free relevance score.**

### 5.1 Three layers

```
  53 sources                ~400/wk              ~70/wk           ~25/wk
       │                       │                    │                │
   ┌───▼────┐            ┌─────▼─────┐        ┌─────▼─────┐    ┌─────▼─────┐
   │ WATCH  │───────────▶│  RESOLVE  │───────▶│  ENTITIES │───▶│  ENRICH   │
   │        │  sightings │           │        │           │    │           │
   │ no LLM │            │ ~85% regex│        │  deduped  │    │  Sonnet,  │
   │  free  │            │ 15% Haiku │        │  + ranked │    │  new only │
   └────────┘            └───────────┘        └───────────┘    └───────────┘
```

**Layer 1 — Watch.** Poll each source on its natural cadence. Emit a *sighting*:
`{sourceId, url, title, publishedAt, excerpt}`. No model calls, no judgment.
This layer is free and can be as wide as we like.

**Layer 2 — Resolve.** Extract identifiers from each sighting, map to a
canonical entity key, merge into an existing entity or create a new one. This
is where deduplication happens and it is the heart of the design. Roughly 85%
is deterministic pattern-matching; the ambiguous residue goes to Haiku.

**Layer 3 — Enrich.** The coverage overlay — lines, triggers, exclusions — the
thing no competing tracker has. Runs once per *new* entity, never per sighting.

The funnel is the cost control. Four hundred sightings a week collapse to about
seventy entities, of which roughly twenty-five are new. We pay model costs on
twenty-five, not four hundred.

### 5.2 Entity identity — the deduplication crux

Everything depends on different sources yielding the *same key* for the same
thing. These are the keys:

| Entity type | Key format | Example |
|---|---|---|
| US case | `{court}:{docket}` normalized | `cand:3:23-cv-03416` |
| US federal rule | FR document number or RIN | `fr:2024-12345` |
| US state bill | `{state}:{session}:{bill}` | `ca:2025:AB-1008` |
| EU instrument | CELEX number | `celex:32024R1689` |
| UK instrument | legislation.gov.uk URI | `uk:ukpga/2025/12` |
| German instrument | Gesetze-im-Internet short title | `de:KI-DurchfG` |
| Sanction order | `{case key}#{order date}` | `txed:4:23-cv-00281#2024-11-25` |
| Data-centre project | `{state}:{utility docket}` or slug | `va:PUR-2025-00042` |

CELEX is the model for why this works. A White & Case post about the EU AI Act,
a DLA Piper summary, an EU AI Office guidance page, and the EUR-Lex record all
resolve to `celex:32024R1689` — so they merge automatically, with no
similarity heuristics and no LLM.

**Extraction ladder**, cheapest first:

1. **Explicit identifier in the text.** Docket numbers (`\d:\d{2}-cv-\d{5}`),
   CELEX (`3\d{4}[A-Z]\d{4}`), FR document numbers, bill numbers. Free.
2. **Identifier in the URL.** Most primary sources put it there. Free.
3. **Caption match against the existing corpus.** Normalized party names
   against known entities. Free, and gets stronger as the corpus grows.
4. **Haiku extraction** on what survives — a short prompt over a title and
   two paragraphs, returning a structured identifier or `null`.

Anything the ladder cannot resolve becomes an `unresolved` sighting rather than
a bad merge. Unresolved sightings are reviewed in bulk, and each one that turns
out to matter suggests a pattern to add at step 1.

### 5.3 Salience — why 53 sources is cheaper than 11

Overlap is not waste. It is the ranking signal, and it is the mechanism that
keeps the weekly review at 25 items regardless of how many sources feed it.

```
salience =  1.0 × Σ(source weight across sightings)
          + 2.0 × (distinct source categories covering it)
          + 1.5 × (sightings in the last 7 days)
          + 3.0 × (a primary record exists)
```

Something six trackers across four categories flagged in three days leads the
queue. Something one law-firm blog mentioned in passing sits in the tail. The
attorney reads down the ranked list and stops when it stops being interesting
— and knows that what was skipped was skipped because *no one else thought it
mattered either*.

This is the answer to "won't more sources mean more work." More sources mean
better ranking, which means **fewer items need to be read to have the same
confidence nothing important was missed.** The review budget is a constant set
by the attorney (25/week), not a function of source count.

### 5.4 Adapters, not ingesters

Fifty-three sources do not mean fifty-three ingest modules. They mean **four
adapters and fifty-three configuration rows.** Adding a source is a YAML entry,
not code — this is the difference between a source set that grows and one that
ossifies.

`data/sources.yml`:

```yaml
- id: whitecase-ai-watch
  name: White & Case — AI Watch
  category: global-regulatory
  adapter: feed                 # api | feed | sitemap | manual
  url: https://www.whitecase.com/.../feed
  cadence: weekly
  role: signal                  # primary | signal
  weight: 0.9                   # feeds salience
  extract: [celex, uk_uri, us_state_bill]
```

| Adapter | Mechanism | Sources | Cost |
|---|---|---|---|
| `api` | Structured JSON, stable schema | CourtListener, Federal Register, Regulations.gov, EUR-Lex, legislation.gov.uk, Open States, arXiv, BAILII | Free, canonical |
| `feed` | RSS/Atom | Most law-firm trackers, trade press, academic centres, broker and reinsurer research | Free, needs extraction |
| `sitemap` | `sitemap.xml` diff against last run | Trackers with no feed | Cheap, moderately fragile |
| `manual` | Periodic bulk import | Charlotin CSV, DataCenterHawk, SERFF | Attorney-triggered |

Actual distribution across the 53: **9 `api`, 29 `feed`, 13 `sitemap`, 2
`manual`** — and by role, 18 `primary` to 35 `signal`. The registry resolves to
sixteen identifier extractors, but six of them (`us_docket`, `case_caption`,
`us_state_bill`, `dc_project`, `celex`, `puc_docket`) cover the large majority
of rows. Those six are the Phase 2-3 build; the remaining ten are long-tail.

**Sequencing rule:** primary sources first. A tracker sighting with no
resolvable key is nearly useless, so the identifier vocabulary has to exist
before the signal layer is worth switching on. Federal Register and EUR-Lex
before White & Case and DLA Piper.

### 5.5 Two corpora

Broker and reinsurer publications — Lloyd's Futureset, Swiss Re, Munich Re,
Marsh, Aon — and the academic centres do not produce discrete legal events.
Forcing them into a timeline built for filings and rulings would corrupt both.

They go into a second corpus:

| | `items` | `intelligence` |
|---|---|---|
| Contains | Cases, statutes, rules, sanctions, DC projects | Research, market reports, white papers |
| Has a date that means | Something happened | Something was published |
| Timeline | Yes | No |
| Feeds | Case File, charts, coverage analysis | The Pulse, client briefings, editorial |
| Enrichment | Full coverage overlay | Two-line summary and topic tags |

This is what makes the reinsurer view usable without distorting the litigation
picture. Munich Re's aggregate-limits paper is genuinely relevant to a coverage
conversation and genuinely not a legal event.

### 5.6 Entry rule: unverified is a status, not a gate

A single tracker sighting with no primary record enters immediately as
`unverified`. Speed matters more than purity at the front of the funnel,
provided the status is honest and the UI respects it.

| Status | How it got there | Timeline | Client-facing |
|---|---|---|---|
| `unverified` | Sighting only, no primary record | No | No |
| `pending` | Primary record resolved | Yes | No |
| `reviewed` | Attorney skimmed | Yes | Yes |
| `verified` | Checked against Westlaw or primary source | Yes | Yes, with check |

**Promotion from `unverified` to `pending` is automatic.** When the Federal
Register record arrives three days after the law-firm post and resolves to the
same key, the item promotes itself and the primary link attaches. No manual
work, and the lead time is captured — which is itself worth measuring, because
it tells us which trackers are genuinely fast and which are merely loud.

### 5.7 Data-centre lane: indicators first

The AEC professional-liability exposure appears years before any docket does.
By the time a construction-defect suit is filed, the interesting decisions are
long made — and the caption rarely says "data centre," which is why detection
from litigation alone is structurally late.

So the lane is built as a **project registry** rather than a case list:

| Signal | Source | Adapter | Lead time |
|---|---|---|---|
| Utility interconnection | PJM, ERCOT, MISO queues | `api`/`sitemap` | 2–4 years |
| Rate and siting dockets | Virginia SCC, Georgia PSC, Ohio PUCO, Texas PUC, Arizona ACC, Indiana IURC | `sitemap` | 1–3 years |
| Project announcements | Data Center Dynamics, Data Center Frontier | `feed` | 1–3 years |
| Community opposition | Same trade press | `feed` | 6mo–2 years |
| Litigation | CourtListener | `api` | at filing |

Six states cover most US hyperscale activity, which makes this tractable where
"all 3,000 county planning boards" was not.

The registry is also the precondition for dispute detection. Once projects are
entities with owners, contractors, and locations, a later suit can be matched
against them by party name and venue — which is the only reliable way to find
data-centre construction litigation, since the docket will not announce itself.
**Indicators first is not a sequencing preference; it is what makes the
dispute lane possible at all.**

### 5.8 Two detection modes — a calibration finding

A calibration run against our own 103 federal corpus items produced a result
that shaped the design above:

| Item type | Recall with docket-metadata filtering |
|---|---|
| Litigation (AI-as-subject) | **97%** — 35 of 36 |
| Sanctions (AI-as-conduct) | **15%** — 10 of 67 |

The gap is structural, not a tuning problem.

**AI-as-subject** matters announce themselves in docket metadata. *NYT v.
OpenAI* has an AI company as a party, AI in the caption, and a copyright
nature-of-suit code. Party and keyword filtering finds these reliably.

**AI-as-conduct** matters do not. *Gauthier v. Goodyear Tire*, *Frier v.
Hingiss*, and *Whaley v. Experian* are ordinary products, traffic, and
credit-reporting disputes in which counsel happened to file a brief containing
fabricated citations. Nothing in the docket indicates AI involvement — **the AI
fact exists only inside the judge's order.** No amount of widening the term list
fixes this, because the signal is not in the data being filtered.

Hence three streams:

| Stream | Source | Method | Status |
|---|---|---|---|
| 1 — AI-as-subject | CourtListener dockets (`type=r`) | Party + keyword + NOS filter | Validated: 43 hits/90d, ~100% precision |
| 2 — AI-as-conduct | CourtListener opinions (`type=o`) | Full-text sanctions language | Works: 93 all-time, but appellate-skewed |
| 3 — AI-as-conduct | Charlotin CSV export | Periodic import | ~1,980 cases, near-total recall |

Stream 3 obviates Stream 2 for now. Charlotin solves this comprehensively and
rebuilding an opinion-full-text sanctions detector duplicates work done well
elsewhere. Notably, Stream 2's hits were almost entirely *state appellate* —
Virginia Court of Appeals, Florida DCA, Pennsylvania Superior, Iowa Court of
Appeals — which is both a substantive signal about where fabricated-citation
practice is surfacing and the reason 93 sits so far below Charlotin's 1,980.

**The methodological point generalizes.** Recall failures are invisible in
production: a case the filter silently drops never appears to be noticed
missing. Precision failures announce themselves as noise in the review queue.
So every new source gets a recall check against known ground truth before it is
trusted, not after.

### 5.9 What CourtListener's limits taught us

Four calibration runs against the live service established constraints that
apply to every API source, not just this one:

- **The API shape held.** v4 `/search/`, `Token` auth, `type=r` dockets,
  `type=o` opinion full text, `filed_after`, cursor pagination.
- **Search is throttled at 5 requests/minute.** This is the binding constraint.
  Paging the docket firehose and filtering locally needs ~15 hours of polling
  to cover one 90-day window. **The filter must live server-side**, expressed
  as a query the service answers with matches only.
- **Quoted phrases do real phrase matching** — `"artificial intelligence"`
  returns 448 where the unquoted pair returns 7,172. Precision depends on this.
- **`caseName:Term` works; `caseName:("X v. Y")` returns nothing.** A run
  reporting 0/6 known cases as unreachable was measuring this bug, not the
  corpus.

The general lesson is the reason for `--syntax-only` mode in the calibration
script: **before trusting any source's zero, prove the query works on something
you know is there.** A false negative from a malformed query is
indistinguishable from an empty corpus, and it argues for abandoning approaches
that in fact work.

### 5.10 Stage design

Each stage is a pure function over files. Any stage can be re-run without side
effects.

1. **Watch** — per-adapter fetch, writes raw responses to scratch. Rate-limited,
   retried, cached by URL, `ETag`/`Last-Modified` honoured.
2. **Sight** — parse to sightings. No LLM.
3. **Resolve** — extraction ladder (§5.2) to canonical keys.
4. **Merge** — attach sightings to entities; create new ones. Near-misses are
   reported for human check rather than auto-merged.
5. **Score** — compute salience (§5.3). No LLM.
6. **Enrich** — Sonnet, batched, only on new entities above threshold: summary,
   significance, draft coverage overlay.
7. **Propose** — write to year shards, open a PR.

### 5.11 The weekly loop

**Monday 06:00 UTC, automated.** Stages 1–7 run. A PR opens titled e.g.
*"23 new: 6 IP · 5 regulation · 4 sanctions · 4 DC · 4 intelligence"*, body
listing each entity in salience order with its sighting count and which
trackers covered it.

**Monday morning, attorney.** Read down the ranked list. Fix or drop anything
wrong. Merge. Target: 20 minutes.

**On merge.** `deploy.yml` runs `build.py` and republishes the artifact.

Nothing publishes without a merge. The PR is the editorial gate.

### 5.12 Ingestion economics

Weekly, at 53 sources:

| Stage | Volume | Model | Cost/week |
|---|---|---|---|
| Watch | ~400 sightings | none | $0 |
| Resolve (residue) | ~60 items | Haiku 4.5 | ~$0.09 |
| Enrich | ~25 entities | Sonnet 5, Batch | ~$0.23 |
| Pulse draft | 1 call | Sonnet 5 | ~$0.05 |
| **Total** | | | **~$0.37/wk ≈ $19/yr** |

Haiku 4.5 is $1/$5 per MTok; Sonnet 5 is $2/$10; the Batch API halves both.
Even at triple these volumes the pipeline runs under $100/year, and GitHub
Actions' free tier covers the compute.

**The conclusion that matters for the partnership: adding 44 sources costs
single-digit dollars per year.** Cost is not the constraint on breadth. The
only real constraint is the attorney's review budget, and §5.3 is the mechanism
that holds it constant as sources grow.

---

## 6. Enrichment (the LLM layer)

### 6.1 Cost discipline

Four multiplicative savings, in order of impact:

1. **Pre-filter before any model call** (§5.2 stage 4) — free, removes ~80%.
2. **Tier the models.** Haiku for triage; Sonnet only for items that pass. Triage is classification; enrichment is analysis.
3. **Batch API.** Weekly ingestion is never latency-sensitive.
4. **Prompt caching.** The taxonomy prompt is large and completely stable across calls.

**Estimated steady-state runtime cost: low hundreds of dollars per year.** The v1 spec estimated $1,800–3,600/year; that figure assumed an unfiltered firehose with Sonnet on everything and no batching or caching. Verify current model pricing before restating externally.

### 6.2 What the model drafts vs. what the attorney owns

| Output | Drafted by | Owned by |
|---|---|---|
| Relevance and track classification | Haiku | Spot-checked |
| Case summary | Sonnet | Skimmed |
| Significance paragraph | Sonnet | Skimmed |
| **Coverage overlay** | Sonnet, from taxonomy | **Attorney edits — this is the moat** |
| Weekly Pulse narrative | Sonnet | **Attorney edits** |
| Verification against Westlaw | — | **Attorney, sampled not exhaustive** |

The coverage overlay is judgment work. LLM drafting from a stable taxonomy should land close, but the analysis is the product and it carries the attorney's name.

---

## 7. Notes, via the `db` capability

### 7.1 Two layers

```js
capabilities: {
  db: {
    rules: [
      { path: "",                    read: "interact", write: "admin"    },
      { path: "data/users/{self}",   write: "interact"                   }
    ]
  }
}
```

- **Team notes** — `notes/shared/<itemId>`. Readable by every viewer, writable by editors (`admin`). For working-group annotations that colleagues should see.
- **Private notes** — `data/users/{self}/notes/<itemId>`. Writable and readable only by that viewer. Private from the owner as a runtime guarantee, not a policy promise.

### 7.2 UI

The Case File modal gains a Notes section with two tabs — **Team** and **Private**. Both are plain textareas with debounced autosave. Private is the default tab.

### 7.3 The tradeoff to know about

Declaring `db` makes the artifact **organization-internal — it cannot be shared publicly.** That is acceptable for internal use and for sharing within the org, and it forecloses the public-microsite path *for this artifact*. If a public version is ever wanted, it is a second artifact built from the same data with the capability omitted and notes excluded. Cheap to add; noted here because it interacts with the commercial options in the partner memo.

### 7.4 Durability

`db` survives republishes and sessions and is not browser-local. Notes are not in git, so they are not covered by repo backups — `scripts/export_notes.py` writing to `private/notes-export.json` is a Phase 4 nicety, not a launch blocker.

---

## 8. Presentation

Current views are retained: **The Pulse** · **Cases & Regulation** · **Timeline** · **Source Trackers** · **What's Next** · **Coverage Gaps**.

The template becomes a Jinja file (~16KB) rendering from the data files. Two changes worth making during the split:

- **Review-status badges** surfaced in the case list and Case File, per §3.2.
- **Provenance line** in the Case File footer: which source, when fetched, which model drafted it, review state.

Deferred, unchanged from v1: the Flow (Sankey) and the Atlas (choropleth). Both are presentation upgrades, not capability upgrades, and belong after automation rather than before it.

---

## 9. The hard problems

Unchanged in substance from v1, with one softened judgment.

**SERFF filings.** 51 separate state implementations, almost no APIs, ~90% PDF, and ~2% of filings are AI-relevant. Building it well is 3–5 months of dedicated engineering. Pragmatic alternative: quarterly manual sweeps of 3–5 priority states.

**Data-center ingestion.** ~3,000 counties, no standardization, the relevant fact usually inside a scanned PDF rather than in the agenda title, opposition suits scattered across every state's e-filing system.

**Softened judgment:** v1 treated data-center ingestion as an all-or-nothing mega-project. Once the pipeline exists, adding scrapers for **6–8 hotspot counties** (Loudoun, Prince William, Fauquier VA; Licking OH; Coweta GA; Maricopa AZ) is incremental work — days, not months. It is *national* coverage that is out of scale, not *useful* coverage. Reclassify from deferred to Phase 5.

**Curation labor.** Irreducible. LLM drafting cuts weekly load meaningfully but never to zero, because the coverage overlay is judgment. This is a feature: it is what makes the product defensible against a pure-technology competitor.

---

## 10. Sequencing

| Phase | Delivers | Est. hours |
|---|---|---|
| **1 — Split** | Data out of HTML; `build.py`; `CLAUDE.md`; project skills. Nothing user-visible changes; every later session gets ~8× cheaper. | 8–12 |
| **2 — Records** | `api` adapter; CourtListener Stream 1 + Federal Register; `identity.key` resolution; `ingest.yml` cron; PR review loop. **Tool becomes self-updating.** | 12–16 |
| **3 — Signal** | `feed` + `sitemap` adapters; `sources.yml` with the Top 6; sightings, merge, salience scoring. **Review becomes ranked rather than chronological.** | 10–14 |
| **4 — Enrichment** | Batched drafting; review-status and sighting counts surfaced in UI. | 8–12 |
| **5 — Notes** | `db` capability; team and private notes in the Case File modal. | 6–10 |
| | **Self-updating, multi-source, ranked, annotated** | **44–64** |
| 6 — Breadth | Remaining sources to ~53; `intelligence` corpus; EUR-Lex, Open States, BAILII, arXiv. | 8–14 |
| 7 — DC registry | PUC/PSC dockets for 6 states; interconnection queues; project registry; dispute matching. | 12–20 |
| 8 — Presentation | Flow (Sankey), Atlas (choropleth), Network graph. | 12–20 |

**What changed from v2.** Phase 3 is new, and it is the phase that makes the
Coverage Gaps list affordable — without salience ranking, adding 44 sources
multiplies the review burden; with it, the burden is a constant the attorney
sets. Notes moved from 4 to 5, which costs nothing: it is the phase with no
dependents.

Phases 2 and 3 in that order matter. Records before signal, because a tracker
sighting with no resolvable key is nearly useless — the identifier vocabulary
has to exist before the corroboration layer has anything to corroborate
against.

At ~10 hrs/week, Phases 1–4 land in **8–12 weeks**. Order matters: the tool is self-updating after Phase 2, so everything later is improvement on a working system rather than a prerequisite.

---

## 11. Economics

### 11.1 Cash, year one

| Line | Amount |
|---|---|
| Hosting | $0 — artifact runtime |
| Database | $0 — `db` capability |
| Claude Code (build) | $0 incremental on existing Pro; ~$200–400 if Max for 2–3 peak months |
| Anthropic API (pipeline runtime) | Low hundreds — see §6.1 |
| GitHub Actions | $0 — free tier is ample |
| Data subscriptions (optional) | $0–4,000 depending on DataCenterHawk decision |
| **Total** | **~$400–1,000 without paid data feeds** |

### 11.2 Attorney time

| | Build phase | Steady state |
|---|---|---|
| Engineering | ~10 hrs/week for 8–12 weeks | ~1 hr/month maintenance |
| Curation | current ~3 hrs/week | ~20 min/week review |
| Editorial (Pulse) | — | ~15 min/week |

### 11.3 Credit efficiency

| Task | Now | After Phase 1 |
|---|---|---|
| Presentation change | ~137k tokens | ~16k |
| Data change | ~137k tokens | ~30k (one shard) |
| Add an ingest source | full re-derivation | one skill load |

`CLAUDE.md` and the project skills remove the per-session re-derivation of schema, taxonomy, and conventions, which has been a large share of historical spend.

---

## 12. Open questions

1. **Review-status backfill.** The existing 263 items have no `provenance`. Do we backfill them all as `pending`, or grandfather the hand-curated ones as `reviewed`?
2. **Charlotin refresh cadence.** The CSV is a manual export. Monthly? Quarterly? Or ask whether an API exists.
3. **Notes backup.** Is `db`-only durability acceptable, or is `export_notes.py` a Phase 4 requirement rather than a nicety?
4. **Team-notes membership.** Who gets editor access to the artifact, and does that list change how the shared-notes rules should be written?
5. **Public microsite.** Confirm it stays out of scope, given §7.3. If it is wanted later, plan for a second build target.
6. **Pulse cadence.** Weekly with the ingest batch, or monthly as a fuller briefing?
