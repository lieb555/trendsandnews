# Atheria Law · AI Working Group Trend Tracker — Technical Specification

_Revision 2. Supersedes the v1 spec of 2026-08-27. Owner: Nick Lieberknecht._

**What changed in this revision.** v1 specified a Next.js + Postgres application built in presentation-first tiers. That architecture was driven by one requirement — private notes attached to case records — which implied authentication, which implied a backend. The Claude artifact runtime's `db` capability provides authenticated, per-viewer-private persistent storage directly, so the backend requirement is removed. This revision replaces the app-first plan with a **pipeline-first** architecture that reaches self-updating status in roughly a third of the effort.

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
│   ├── taxonomy.json              # tracks, coverage lines, triggers, exclusions
│   ├── landmarks.json             # milestone timeline events
│   └── sources.json               # source-tracker directory + wishlist
├── private/                       # gitignored — never enters a build
│   └── notes-export.json          # optional local backup of db notes
├── scripts/
│   ├── ingest/
│   │   ├── courtlistener.py
│   │   ├── federal_register.py
│   │   ├── openstates.py
│   │   └── charlotin.py
│   ├── dedupe.py
│   ├── enrich.py                  # batched Anthropic API calls
│   └── build.py                   # data + template → dist/index.html
├── templates/
│   └── index.html.j2              # ~16KB: CSS, app JS, view shells
└── .github/workflows/
    ├── ingest.yml                 # weekly cron → opens PR
    └── deploy.yml                 # on merge to main → build + publish
```

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

  // Provenance — new in v2, required for every pipeline-ingested item
  "provenance": {
    "source": "courtlistener",          // which ingest module produced it
    "fetchedAt": "2026-09-01T06:00:00Z",
    "enrichedBy": "claude-haiku-4-5",   // model that drafted summary/overlay
    "reviewStatus": "pending",          // pending | reviewed | verified
    "reviewedBy": null,
    "confidence": 0.82                  // triage confidence, 0–1
  }
}
```

`provenance` is the key addition. It lets the UI distinguish machine-drafted items from attorney-verified ones, and lets the review workflow track what still needs eyes.

### 3.2 Review status ladder

| Status | Meaning | UI treatment |
|---|---|---|
| `pending` | Machine-ingested, not yet reviewed | Amber badge; excluded from client-shareable views |
| `reviewed` | Attorney skimmed; caption and claims look right | No badge |
| `verified` | Checked against Westlaw or primary source | Green check |

This replaces the blanket "verify first" disclaimer with a per-item signal, which is both more honest and more useful.

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

### 5.1 Sources, by automation feasibility

| Source | Access | Automatable | Phase |
|---|---|---|---|
| CourtListener / RECAP | Free API | Yes | 2 |
| Federal Register | Free API | Yes | 2 |
| Regulations.gov | Free API | Yes | 3 |
| EUR-Lex | Free API | Yes | 3 |
| legislation.gov.uk | Free API | Yes | 3 |
| Open States | Free API | Yes | 3 |
| EDGAR | Free, structured | Yes | 3 |
| Charlotin hallucinations | CSV export | Semi — periodic manual export | 2 |
| Curated trackers (GWU, Orrick, White & Case…) | Web pages | No — used as discovery signal | ongoing |
| SERFF | State-by-state, PDF-heavy | Hard — see §9 | deferred |
| County planning boards | ~3,000 sites, PDF | Hard — see §9 | deferred |

### 5.1a Two detection modes — a calibration finding

A calibration run against our own 103 federal corpus items produced a result that changes the ingestion design:

| Item type | Recall with docket-metadata filtering |
|---|---|
| Litigation (AI-as-subject) | **97%** — 35 of 36 |
| Sanctions (AI-as-conduct) | **15%** — 10 of 67 |

The gap is structural, not a tuning problem.

**AI-as-subject** matters announce themselves in docket metadata. *NYT v. OpenAI* has an AI company as a party, AI in the caption, and a copyright nature-of-suit code. Party and keyword filtering finds these reliably.

**AI-as-conduct** matters do not. *Gauthier v. Goodyear Tire*, *Frier v. Hingiss*, *Whaley v. Experian* are ordinary products, traffic, and credit-reporting disputes in which counsel happened to file a brief containing fabricated citations. Nothing in the docket indicates AI involvement — **the AI fact exists only inside the judge's order.** No amount of widening the term list fixes this, because the signal is not in the data being filtered.

Consequently the pipeline needs three streams, not one:

| Stream | Source | Method | Status |
|---|---|---|---|
| 1 — AI-as-subject | CourtListener dockets (`type=r`) | Party + keyword + NOS filter | Validated at 97% recall |
| 2 — AI-as-conduct | CourtListener opinions (`type=o`) | Full-text search for sanctions language (*hallucinat\**, *fabricated citation*, *nonexistent case*) | Designed, not yet calibrated |
| 3 — AI-as-conduct | Charlotin CSV export | Periodic import | Working; ~1,980 cases, near-total recall |

Stream 3 likely obviates Stream 2 for the foreseeable future. Charlotin is already solving this problem comprehensively, and rebuilding an opinion-full-text sanctions detector duplicates work that is done well elsewhere. Build Stream 1 first; keep Stream 3 as a periodic manual import; revisit Stream 2 only if Charlotin's coverage lapses.

**The methodological point generalizes.** Recall failures are invisible in production — a case the filter silently drops never appears to be noticed missing. Precision failures announce themselves as noise in the review queue. So every new source gets a recall check against known ground truth before it is trusted, not after.

### 5.2 Stage design

Each stage is a pure function over files. Any stage can be re-run without side effects.

1. **Fetch** — per-source module writes raw responses to a scratch directory. Rate-limited, retried, cached by URL.
2. **Normalize** — map source-specific shapes to the item schema. No LLM.
3. **Dedupe** — match against existing corpus on docket number, then normalized caption + court + date. Report near-misses for human check rather than auto-merging.
4. **Pre-filter** — deterministic rules (source, keyword, date window, court) drop roughly 80% of candidates before any model call. **This is the single largest cost lever in the pipeline.**
5. **Triage** — Haiku, batched: is this AI-related and significant, which track, confidence score. Cheap classification.
6. **Enrich** — Sonnet, batched, only on triage survivors: summary, significance, draft coverage overlay.
7. **Propose** — write new items to their year shard, open a PR.

### 5.3 The weekly loop

**Monday 06:00 UTC, automated.** Stages 1–7 run. PR opens titled e.g. *"12 new items: 4 IP · 3 sanctions · 2 data-center · 3 regulation"*, body summarizing each item with its triage confidence.

**Monday morning, attorney.** Review the diff. Fix or drop anything wrong. Merge. Target: 20 minutes.

**On merge.** `deploy.yml` runs `build.py` and republishes the artifact.

Nothing publishes without a merge. The PR is the editorial gate.

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
| **2 — Pipeline** | CourtListener + Federal Register ingest; dedupe; `ingest.yml` cron; PR review loop. **Tool becomes self-updating.** | 12–16 |
| **3 — Enrichment** | Batched triage + drafting; provenance and review-status surfaced in UI. | 8–12 |
| **4 — Notes** | `db` capability; team and private notes in the Case File modal. | 6–10 |
| | **Self-updating tracker with annotation** | **35–50** |
| 5 — Breadth | More sources (Regulations.gov, EUR-Lex, Open States, EDGAR); hotspot-county DC scrapers. | 10–20 |
| 6 — Presentation | Flow (Sankey), Atlas (choropleth), Network graph. | 12–20 |

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
