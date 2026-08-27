# AI Litigation & Insurance Trends Tracker — v1 Specification

_Draft for review. Written 2026-08-27. Owner: lieberknecht@gmail.com._

## 1. One-line description

A dynamic, interactive tracker of AI-related litigation, regulation, and insurance developments — designed for a practicing attorney whose work spans AI law, professional liability for architectural/engineering/construction firms building data centers, and insurance policy wording for a US, UK, and German client base.

## 2. Goals

- Surface trends fast enough to inform live client advice.
- Support two lenses on the same dataset: the **litigator's view** (theories, rulings, plaintiff-bar patterns) and the **underwriter's view** (coverage lines implicated, likely triggers, likely exclusions).
- Present information dynamically and interactively — not as static reports.
- Start private (authenticated), keep a path open to a public-facing microsite later.

## 3. Users & primary use cases

**Primary user (v1):** the attorney, working alone.

**Read patterns:**
- Daily: "What moved this week?" (open The Pulse view first).
- Ad hoc: research memos, client questions, wording-review support (drill into individual cases and coverage analysis).
- Monthly: recurring client briefings (export a filtered dashboard slice as a shareable read-only URL, or grab chart snippets for a client update deck).

**Future users (out of scope for v1 but designed-for):**
- Small internal team, shared workspace with private notes.
- Selectively client-shareable read-only views.
- Public microsite for thought leadership.

## 4. Topic taxonomy

### 4.1 Litigation tracks (14)

Each track is a first-class filter across every view.

1. **IP — training data & output** — copyright infringement in training corpora, output infringement, DMCA §1202 CMI, right of publicity/deepfakes, database rights (EU), TDM exceptions.
2. **Privacy & biometrics** — BIPA (Illinois), GDPR, UK DPA, CCPA/CPRA, wiretap and two-party consent (transcription, session replay), voice cloning.
3. **Defamation & hallucination** — false statements from generative output, false light, negligent misstatement (UK).
4. **Discrimination & algorithmic bias** — hiring, lending, insurance underwriting, EEOC, NYC Local Law 144, Colorado AI Act, EU AI Act high-risk classification disputes.
5. **Product liability & negligence** — AI-as-defective-product, chatbot harm, autonomous system defects.
6. **Consumer protection & AI-washing** — FTC §5, state UDAP, SEC AI-washing enforcement, ASA (UK).
7. **Securities & D&O** — 10b-5 on AI disclosures, board oversight failures, derivative suits.
8. **Contract, indemnity, IP ownership** — SaaS/model licensing disputes, vendor↔customer indemnity fights, ownership of AI-generated work.
9. **Data centers** — sub-tracked below (§4.2) given practice focus.
10. **AI-specific criminal & fraud** — deepfake fraud, voice-clone extortion, election interference.
11. **Judge-level analytics** — motion-outcome patterns by judge (venue-selection intelligence). Not its own claim type; a cross-cutting analytic layer.
12. **Employment/labor & AI** — unionization tied to AI (WGA/SAG-style), worker-displacement claims, workplace surveillance.
13. **Export controls & national security** — BIS entity list updates, CFIUS reviews of AI transactions, sanctions.
14. **Cross-border enforcement** — EU AI Act extraterritoriality, UK regulator action against US-headquartered providers.

### 4.2 Data-center sub-tracks

Given the practice focus on AEC professional liability, this vertical carries its own sub-taxonomy:

- **Construction & AEC E&O** — defect claims, delay disputes, professional liability against architects/engineers/contractors on hyperscale builds.
- **Environmental & land use** — water usage, cooling, noise, air permits, NEPA/CEQA, community opposition, zoning appeals.
- **Power & grid disputes** — PPA fights, interconnection queue litigation, curtailment, colocation with generation, nuclear restarts.
- **Security & operational** — physical and cyber incidents at data centers, SLA disputes, tenant-vs-operator liability.

### 4.3 Regulation & self-regulation (parallel track)

- **Formal** — statutes, agency rules, enforcement actions (US federal + state; UK; EU with Germany explicitly).
- **Voluntary commitments** — White House voluntary commitments, Frontier Model Forum outputs, Seoul/Bletchley commitments, model spec disclosures.
- **Standards & frameworks** — NIST AI RMF, ISO 42001, CEN-CENELEC harmonized standards under EU AI Act.
- **Insurer regulatory filings** — state DOI form/rate filings for AI exclusions and endorsements (SERFF), Lloyd's market bulletins.

### 4.4 Signal layer (context annotations)

Adjacent events that annotate the litigation and regulation timelines without being cases themselves:

- Regulator investigations (FTC CIDs, SEC comment letters, EU AI Office inquiries, state AG civil investigative demands).
- Cease-and-desist and pre-suit demand letters.
- Corporate signals — funding rounds, M&A, model releases, layoffs, 10-K/20-F risk-factor changes on AI.
- Academic & policy papers — working papers, law review articles, think-tank reports as leading indicators of legal theories.

## 5. Data model

### 5.1 Core entity: `Case`

Rich structured metadata, one record per matter:

- **Identity** — caption, docket number, court, jurisdiction, filing date, status.
- **Parties** — plaintiff(s), defendant(s), plaintiff firm(s), defense firm(s), industry classification of each defendant.
- **Judge** — assigned judge (feeds judge-level analytics).
- **Claims** — array of claim tags drawn from the taxonomy (§4.1), each with claim-specific fields (e.g., BIPA §15(a)/(b)/(c)/(d) subsection tags).
- **Relief sought** — damages, injunctive, deletion/disgorgement of model, class certification.
- **Motion history** — MTD, class cert, summary judgment, etc., each with date, outcome, and one-paragraph note.
- **Damages theory** — statutory, actual, disgorgement, willful multiplier.
- **Insurance analysis** — for each case, structured:
  - Coverage line(s) likely to respond (Tech E&O, Cyber, D&O, GL, Media, EPL, Property, AEC PL).
  - Likely trigger clause(s) (e.g., "wrongful act", "personal injury offense", "security failure").
  - Likely exclusions in play (bodily injury, IP, contractual liability, war/terrorism, silent AI).
  - Free-text underwriting analysis.
- **Source docs** — links to complaint, key motions, key rulings (CourtListener/RECAP where available).
- **Private notes** — attorney work-product, unlimited free text.
- **Related** — linked case IDs for consolidated / follow-on / co-defendant relationships.

### 5.2 Companion entities

- **Regulation** — statute, agency guidance, enforcement action, voluntary commitment, standard, or insurer filing. Fields: jurisdiction, issuer, date, type, one-paragraph summary, source link, tags cross-referencing claim tracks.
- **Signal** — a corporate event, investigation, C&D, or academic paper. Fields: date, type, actor, one-paragraph summary, source link, related-case links.
- **Firm** — plaintiff and defense firms, normalized (feeds the plaintiff-bar campaign analytics).
- **Company** — model developers, deployers, data-center operators, with metadata for funding, HQ, sector (feeds geographic overlay).

### 5.3 Cross-cutting tags

- Jurisdiction (federal circuit, state, country).
- Industry defendant type (foundation model, deployer-by-sector, data-center operator, AEC).
- Coverage line implicated (see §5.1).
- Priority flag (my watchlist).
- Client-relevance flag — deferred pending confidentiality decision (§8).

## 6. Presentation — the five hero views

### 6.1 The Pulse (default landing view)

**Question answered:** "What moved this week?"

- Algorithmic anomaly detection (filing velocity spikes, first-of-kind claim types, plaintiff-firm expansion into new theories) feeds a candidate list.
- LLM drafts a narrative summary of the week's shifts.
- I do final human curation before it publishes (a lightweight editor UI: accept/edit/discard each candidate).
- Output: top-5 things that moved this week — new filings of note, novel theories, high-signal rulings, regulator moves, notable settlements. Single scroll.
- Toggleable "past four weeks" and "past twelve weeks" retrospective views.

### 6.2 The Atlas (map view)

**Question answered:** "Where is risk concentrated relative to capital?"

- Choropleth of US states + UK/EU countries.
- Toggleable layers:
  - Filing density (by claim track).
  - Funded AI-company density.
  - Hyperscale data-center locations.
  - State- and country-level AI legislation status.
- Click a region → drill into its cases, filings, regulatory activity.

### 6.3 The Flow (Sankey diagram) — the underwriter's hero view

**Question answered:** "Where does litigation load onto policy lines, and where are the coverage fights?"

- Sankey: claim type → industry defendant → coverage line → likely trigger → likely exclusion.
- Widths proportional to case counts.
- Filter by jurisdiction, date range, case status.
- Click any node → filtered case list.

### 6.4 The Timeline (annotated trend chart) — the litigator's hero view

**Question answered:** "How are legal theories heating up or cooling down against the regulatory backdrop?"

- Filings-per-month per claim type (stacked or overlaid).
- Vertical annotations for landmark rulings, statutes, regulatory events.
- Toggle regulation as an overlay track.
- Hover for annotation detail; click through to source.

### 6.5 The Case File (drill-down)

**Question answered:** "Everything about this one matter."

- Full metadata (§5.1).
- Motion timeline visualized.
- Insurance analysis panel (line + trigger + exclusions).
- Related-cases panel driven by shared claim types, firms, industries.
- Private notes editor.
- Source-doc links.

### 6.6 What's Next page

A first-class page in the app describing planned but not-yet-built features (see §10.4). Kept visible on the shared read-only views so clients and colleagues can see where the tool is headed, and so we have a public accountability marker for roadmap items.

## 7. Sourcing plan

### 7.1 Automated feeds

- **CourtListener / RECAP API** — federal docket coverage, free, well-documented.
- **State court RSS** — where available (Illinois for BIPA, California, New York, Delaware, Texas prioritized).
- **EDGAR** — securities filings and disclosures relevant to AI disclosures and 10-K risk-factor tracking.
- **EUR-Lex** — EU legislative and enforcement documents.
- **UK courts and tribunals** — public feeds; supplement with BAILII.
- **SERFF (state-by-state)** — insurer form and rate filings; scraping is uneven, some manual entry expected.
- **PACER** — for federal filings that don't reach CourtListener quickly enough (small per-page cost).

### 7.2 Curated inputs

- Newsletters and trackers already followed (to be listed by you; I'll build ingestion for the ones with usable feeds).
- Manual case entry through the admin UI for anything above.
- Westlaw — remains your deep-reading environment; not scraped (ToS).

### 7.3 Coverage & completeness posture

- v1 does not attempt to be exhaustive. It aims to be **reliably current on the tracks you care about**, with completeness improving over time.
- Every record shows its source and last-updated timestamp so you can trust what's on the page.

## 8. Confidentiality & auth

**Decision made:** private notes live in the tool from day one → auth required from day one.

Architecture:

- Single-tenant authenticated app.
- Private data (notes, private tags, watchlists) never leaves the authenticated area.
- Shareable read-only URLs generate scoped public views that exclude private fields.
- **Deferred:** whether to add per-case "client interest" tagging (would require an additional confidentiality layer — private area within the private area — to protect client-identity information under attorney-client privilege). Design placeholders for this now; implement when needed.

## 9. Tech stack

**Chosen path:** Option C, staged — Next.js + Postgres, deployed to Vercel or Fly.io.

Reasoning:

- Every v1 requirement (auth, private notes, coverage-line tagging with triggers/exclusions, algorithmic + AI-drafted Pulse, shareable-URL feature, path to public microsite) needs a real backend.
- Static-site and no-code paths would each need a rebuild to grow into these requirements.
- Estimated hosting cost at low volume: $20–40/month.

Concrete choices for v1:

- **Framework:** Next.js (App Router) — TypeScript throughout.
- **Database:** Postgres (Neon or Supabase for managed hosting).
- **ORM:** Drizzle or Prisma.
- **Auth:** Auth.js (NextAuth) with email/password or magic-link.
- **Visualization:** D3 for the Sankey and choropleth (custom); Recharts for the timeline/trend charts.
- **AI-drafted Pulse narrative:** Anthropic Messages API with prompt caching.

## 10. Staged build plan

Release milestones are defined by what you can use, not by calendar. Estimated calendar duration in parentheses assumes single-developer effort.

### 10.1 MVP — end of Sprint 2 (~4 weeks)

The tool is usable for daily practice. Case entry, trend visualization over time, drill-down with insurance analysis, shareable client views.

**Sprint 1 — Foundation & first hero view (~2 weeks).**
- Repository scaffold, Next.js app, Postgres schema for Case + Regulation + Signal + Firm + Company.
- Admin UI for case entry (create/edit/delete, all fields from §5.1).
- Auth (single user for now).
- Import routines for CourtListener and EDGAR.
- The Pulse view rendering from real data — top-5 selection curated manually (AI narrative comes later).
- Backfill: since post-ChatGPT (Nov 2022), highest-priority tracks first (IP — training data; Privacy & biometrics; Data centers).

**Sprint 2 — Insurance layer & trend view (~2 weeks).**
- The Timeline (annotated trend chart).
- The Case File drill-down.
- Insurance-analysis fields wired into the schema and Case File (line + trigger + exclusions).
- Regulation tracker entity implemented, cross-linked to cases.
- Shareable read-only URL feature (scoped views excluding private data).

### 10.2 v1.0 — end of Sprint 3 (~5 weeks)

Adds the two custom visualizations that unlock geographic and coverage-flow analysis.

**Sprint 3 — Custom hero visualizations (~1 week).**
- The Flow (Sankey: claim type → industry → coverage line → trigger → exclusion).
- The Atlas (choropleth with toggleable layers: filing density, funded AI-company density, hyperscale DC locations, AI legislation status).

### 10.3 v1.1 — end of Sprint 4 (~7 weeks)

Adds the intelligence layer that turns The Pulse from manual-curation into semi-automated weekly narrative.

**Sprint 4 — Intelligence (~2 weeks).**
- Algorithmic anomaly detection (filing velocity spikes, first-of-kind claim types, plaintiff-firm expansion).
- LLM-drafted Pulse narrative with human-in-the-loop editor.
- Corporate signal ingestion (funding, M&A, 10-K risk-factor changes).

### 10.4 "What's Next" — v1.2+ roadmap

These are the features I've assessed as high-value but deferred to keep the MVP path clean. The app will surface a **What's Next** page describing this roadmap so users (and clients viewing shared links) see where the tool is headed.

- **SERFF ingestion.** Insurer form/rate filings tracker across priority states (NY, CA, IL, TX, FL first), with extracted filed form language, filing type, carrier, line, regulator status. Cross-linked into The Flow so litigation activity in a state can be paired with subsequent insurer wording responses. See §7.1 for why this is expensive: state-by-state variation, mostly PDF, weak APIs, poor signal-to-noise. Highest differentiator for a policy-wording practice — but only pays off with meaningful state coverage, so it is treated as a phased add-on rather than a v1 blocker.
- **Data-center automated ingestion.** Pipelines pulling data-center project activity from county planning boards, utility interconnection queues, and trade journalism across hotspot states (VA, TX, GA, OH, IN, MO, AZ first). Data-center *content* is already a v1 filter track with hand-curated seed items; what's deferred is *automation* of that ingest. The underlying data is more scattered than SERFF — thousands of counties without APIs, PDF-heavy meeting minutes, and no unified data model — so the pipeline is a separate multi-sprint project. Cross-linked into the Atlas map (project locations, opposition density) and the Flow (AEC PL / environmental / public-officials coverage lines).
- **Judge-level analytics.** Motion-outcome rollups by judge, for venue-selection intelligence and defense-strategy pattern-finding. Depends on rigorous motion tagging in the case entry workflow, so it also benefits from waiting until the case corpus is thicker.
- **The Network graph** — force-directed graph of plaintiff firms ↔ defendants ↔ theories ↔ judges. High analytic value, visually noisy; better designed once you know which relationships you actually want to see.
- **Weekly briefing email** — automated Monday digest built from the same Pulse candidates, with your final editorial pass before send.
- **Client-shareable branded PDF export** — pick a slice, generate a branded briefing.
- **Public-facing microsite** — separate deployment of the public-safe views for thought-leadership use.
- **Multi-user shared team space** — internal team access with role-based permissions on private notes.
- **Client-interest tagging** (§8) — private-within-private layer for client-identity information under attorney-client privilege.

## 11. Open questions to close before Sprint 1

1. **The "something else" additional topic.** You selected "Something else" on the additional-topics question — what is it? (I want to name it explicitly in the taxonomy before building the schema.)
2. **Curated inputs.** Which newsletters and trackers do you rely on today? I'll assess which have usable feeds for ingestion vs. which we treat as human-curated inputs.
3. **Hosting choice.** Vercel or Fly.io for the app; Neon or Supabase for Postgres. Happy to pick if you have no preference — Vercel + Neon is the lowest-friction default.
4. **Domain.** Do you want to point a domain at this from day one (even while it's private), or run it under a Vercel/Fly subdomain until it opens up?
5. **AI-summary source-of-record.** When the AI drafts the Pulse narrative, do you want to see and edit its citations/sources at the sentence level (higher trust, slower), or accept/edit at the paragraph level (faster, less granular auditability)?
6. **Client-interest tagging (§8, deferred).** Whether to design the extra confidentiality layer now or defer — my recommendation is design placeholders now, don't implement until you need it.

## 12. Non-goals for v1

- Exhaustive coverage. Reliability on the priority tracks first; the long tail fills in over time.
- Substitute for Westlaw. This is a trends and coverage tool; deep case reading still happens in Westlaw.
- Automated legal advice. AI-drafted summaries are Pulse-only, always human-reviewed, never client-facing without your review.
- Public-facing microsite (v2).
- Multi-user team space (v2).
