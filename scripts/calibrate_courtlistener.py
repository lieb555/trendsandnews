#!/usr/bin/env python3
"""
Calibration run — does deterministic pre-filtering actually find AI litigation
in the CourtListener firehose without drowning us in noise?

This is a THROWAWAY DIAGNOSTIC, not part of the production pipeline. Its only
job is to answer one question before we commit 20-28 hours to building the
real ingest: is the pre-filter good enough that the weekly review stays at
~20 minutes?

It measures two things that matter:

  PRECISION  - of what the filter surfaces, how much is actually relevant?
               (you judge this by reading the sample it prints)

  RECALL     - of the AI cases we already know about, how many would the
               filter have caught? (measured automatically against our
               existing corpus - this is the number that matters most,
               because silent misses are the failure mode we can't see
               in production)

Usage:
    export COURTLISTENER_TOKEN=...          # or put it in .env
    python3 scripts/calibrate_courtlistener.py --days 90
    python3 scripts/calibrate_courtlistener.py --recall-only   # no API needed

NOTE ON API ASSUMPTIONS: this was written without network access to
CourtListener's docs. Every assumption about their API shape is isolated in
fetch_page() and normalize_result() and flagged with ASSUMPTION comments.
If the first run fails, --debug prints the raw response so the fix is a
one-line correction rather than a rewrite.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API_BASE = "https://www.courtlistener.com/api/rest/v4"

# ---------------------------------------------------------------------------
# Filter definitions — the substance of what we're calibrating
# ---------------------------------------------------------------------------

# Strategy A: party-name matching. High precision, low recall. Catches the
# known defendants but misses new entrants and deployer-side suits.
AI_PARTIES = [
    "openai", "anthropic", "stability ai", "midjourney", "perplexity",
    "character technologies", "character.ai", "uncharted labs", "suno",
    "udio", "runway ai", "runwayml", "deviantart", "cohere", "inflection ai",
    "mistral ai", "x.ai", "xai corp", "scale ai", "hugging face",
    "clearview ai", "workday", "hirevue", "nvidia", "databricks",
    "meta platforms", "alphabet inc", "google llc", "microsoft corp",
    "ross intelligence", "thomson reuters",
]

# Strategy B: keyword matching on case caption and available text.
# Higher recall, more false positives. "algorithmic" and "automated decision"
# in particular will pull in non-AI matters.
AI_KEYWORDS = [
    "artificial intelligence", "machine learning", "large language model",
    "generative ai", "generative artificial intelligence", "neural network",
    "training data", "foundation model", "diffusion model", "text-to-image",
    "chatgpt", "gpt-4", "copilot", "deepfake", "synthetic media",
    "voice clone", "facial recognition", "biometric identifier",
    "automated decision", "algorithmic discrimination", "chatbot",
]

# Strategy C: nature-of-suit codes. Structural signal, no text needed.
# Used to NARROW keyword hits, never alone — 820 alone is every copyright
# case in the country.
NOS_OF_INTEREST = {
    "820": "Copyright",
    "830": "Patent",
    "840": "Trademark",
    "442": "Civil Rights - Employment",
    "445": "Civil Rights - ADA Employment",
    "890": "Other Statutory Actions",
    "365": "Personal Injury - Product Liability",
    "360": "Other Personal Injury",
}

# Courts where AI litigation concentrates. Used for reporting, not filtering —
# filtering on court would bake in today's geography and miss new venues.
PRIORITY_COURTS = ["nysd", "cand", "ded", "mad", "ilnd", "txwd", "cacd", "dcd"]


# ---------------------------------------------------------------------------
# Filter logic
# ---------------------------------------------------------------------------

def haystack(item):
    """All text we can match against, lowercased."""
    parts = [
        item.get("caseName") or "",
        item.get("suitNature") or "",
        item.get("snippet") or "",
        " ".join(item.get("party") or []) if isinstance(item.get("party"), list) else "",
    ]
    return " ".join(parts).lower()


def match_party(item):
    hay = haystack(item)
    return sorted({p for p in AI_PARTIES if p in hay})


def match_keyword(item):
    hay = haystack(item)
    return sorted({k for k in AI_KEYWORDS if k in hay})


def match_nos(item):
    nos = str(item.get("suitNature") or "")
    code = re.match(r"^\s*(\d{3})", nos)
    if code and code.group(1) in NOS_OF_INTEREST:
        return code.group(1)
    for c, label in NOS_OF_INTEREST.items():
        if label.lower() in nos.lower():
            return c
    return None


def classify(item):
    """Return the set of strategies that fire for this item."""
    hits = set()
    parties = match_party(item)
    keywords = match_keyword(item)
    nos = match_nos(item)

    if parties:
        hits.add("A_party")
    if keywords:
        hits.add("B_keyword")
    if nos and keywords:
        hits.add("C_nos_keyword")
    return hits, parties, keywords, nos


# ---------------------------------------------------------------------------
# CourtListener fetch
# ---------------------------------------------------------------------------

def fetch_page(token, filed_after, cursor=None, debug=False):
    """
    ASSUMPTION: v4 search endpoint, RECAP docket type, Token auth header.
    If this 401s/404s, the fix is here. Run with --debug to see the raw body.
    """
    params = {
        "type": "r",                 # ASSUMPTION: 'r' = RECAP dockets
        "filed_after": filed_after,  # ASSUMPTION: accepts YYYY-MM-DD
        "order_by": "dateFiled desc",
    }
    url = f"{API_BASE}/search/?{urllib.parse.urlencode(params)}"
    if cursor:
        url = cursor

    req = urllib.request.Request(url, headers={
        "Authorization": f"Token {token}",   # ASSUMPTION: 'Token', not 'Bearer'
        "User-Agent": "atheria-ai-tracker-calibration/0.1",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        print(f"\n  HTTP {e.code} from CourtListener", file=sys.stderr)
        print(f"  URL: {url}", file=sys.stderr)
        print(f"  Body: {detail}\n", file=sys.stderr)
        if e.code == 401:
            print("  -> token rejected. Check COURTLISTENER_TOKEN.", file=sys.stderr)
        if e.code == 404:
            print("  -> endpoint shape wrong. Correct fetch_page().", file=sys.stderr)
        raise SystemExit(2)
    except urllib.error.URLError as e:
        print(f"\n  Network error: {e.reason}", file=sys.stderr)
        print("  If you are running this inside a sandbox, CourtListener may be", file=sys.stderr)
        print("  blocked. Run it on your own machine or in GitHub Actions.\n", file=sys.stderr)
        raise SystemExit(2)

    data = json.loads(body)
    if debug:
        print("\n--- RAW FIRST RESPONSE (keys) ---")
        print(json.dumps({k: type(v).__name__ for k, v in data.items()}, indent=2))
        results = data.get("results") or []
        if results:
            print("\n--- FIRST RESULT ---")
            print(json.dumps(results[0], indent=2)[:2500])
        print("--- END DEBUG ---\n")
    return data


def normalize_result(raw):
    """
    ASSUMPTION about field names. Correct here if --debug shows different keys.
    """
    return {
        "caseName": raw.get("caseName") or raw.get("case_name") or "",
        "court": raw.get("court_id") or raw.get("court") or "",
        "dateFiled": raw.get("dateFiled") or raw.get("date_filed") or "",
        "docketNumber": raw.get("docketNumber") or raw.get("docket_number") or "",
        "suitNature": raw.get("suitNature") or raw.get("nature_of_suit") or "",
        "snippet": raw.get("snippet") or "",
        "party": raw.get("party") or [],
        "absolute_url": raw.get("absolute_url") or "",
    }


# ---------------------------------------------------------------------------
# Recall check against the corpus we already have
# ---------------------------------------------------------------------------

def load_known_corpus():
    """Pull itemsData out of index.html. Works pre-split; after the Phase 1
    split this reads data/items/*.json instead."""
    split = REPO / "data" / "items"
    if split.is_dir():
        items = []
        for f in sorted(split.glob("*.json")):
            items.extend(json.loads(f.read_text()))
        return items

    html = (REPO / "index.html").read_text()
    m = re.search(r'<script id="itemsData"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    return json.loads(m.group(1))


def recall_check():
    """Would the filter have caught the federal cases we already know about?

    This is the most important number in the whole run. Precision problems
    are visible (you see the junk). Recall problems are invisible in
    production — a case the filter silently drops never shows up to be
    noticed. So we measure recall against ground truth we control.
    """
    corpus = load_known_corpus()
    federal = [
        it for it in corpus
        if it.get("type") in ("case", "sanction")
        and it.get("jurisdiction") == "US"
        and re.search(r"\b(D\.|Cir\.|S\.D\.|N\.D\.|E\.D\.|W\.D\.|C\.D\.)", it.get("court", ""))
    ]

    caught, missed = [], []
    for it in federal:
        probe = {
            "caseName": it.get("caption", ""),
            "suitNature": " ".join(it.get("trackLabels") or []),
            "snippet": (it.get("summary") or "")[:400],
            "party": [
                (it.get("parties") or {}).get("plaintiff", ""),
                (it.get("parties") or {}).get("defendant", ""),
            ],
        }
        hits, parties, keywords, nos = classify(probe)
        (caught if hits else missed).append((it, hits, parties, keywords))

    total = len(federal)
    print("=" * 72)
    print("RECALL CHECK — against the corpus we already curated by hand")
    print("=" * 72)
    if not total:
        print("  No federal items found in corpus. Nothing to check.")
        return
    pct = 100 * len(caught) / total
    print(f"  Federal items in corpus:  {total}")
    print(f"  Filter would catch:       {len(caught)}  ({pct:.1f}%)")
    print(f"  Filter would MISS:        {len(missed)}  ({100-pct:.1f}%)")
    print()

    if pct >= 90:
        print("  VERDICT: filter looks sound. Proceed to build the pipeline.")
    elif pct >= 75:
        print("  VERDICT: usable but leaky. Widen the term lists before building.")
    else:
        print("  VERDICT: too leaky. Redesign the filter before committing hours.")
    print()

    by_strategy = Counter()
    for _, hits, _, _ in caught:
        for h in hits:
            by_strategy[h] += 1
    print("  Which strategy caught them:")
    for strat, n in by_strategy.most_common():
        print(f"    {strat:16s} {n:4d}")
    print()

    if missed:
        print(f"  MISSED ({min(len(missed), 25)} of {len(missed)} shown) — these are")
        print("  the cases the pipeline would silently drop. Read them; each one")
        print("  should suggest a term to add:")
        for it, _, _, _ in missed[:25]:
            print(f"    - {it.get('caption','')[:78]}")
            print(f"      {it.get('court','')} | {it.get('date','')}")
    print()


# ---------------------------------------------------------------------------
# Live run
# ---------------------------------------------------------------------------

def live_run(token, days, max_pages, debug):
    filed_after = (date.today() - timedelta(days=days)).isoformat()
    print("=" * 72)
    print(f"LIVE RUN — federal filings since {filed_after}")
    print("=" * 72)

    scanned, kept = 0, []
    cursor, page = None, 0
    while page < max_pages:
        data = fetch_page(token, filed_after, cursor, debug=(debug and page == 0))
        results = data.get("results") or []
        if not results:
            break
        for raw in results:
            item = normalize_result(raw)
            scanned += 1
            hits, parties, keywords, nos = classify(item)
            if hits:
                kept.append((item, hits, parties, keywords, nos))
        cursor = data.get("next")
        page += 1
        print(f"  page {page}: scanned {scanned}, kept {len(kept)}")
        if not cursor:
            break
        time.sleep(1.0)  # be polite

    print()
    print(f"  Total scanned:  {scanned}")
    print(f"  Passed filter:  {len(kept)}")
    if scanned:
        print(f"  Pass rate:      {100*len(kept)/scanned:.1f}%  "
              f"(target: low single digits — high means the filter is too loose)")
    print()

    strat = Counter()
    courts = Counter()
    for item, hits, *_ in kept:
        for h in hits:
            strat[h] += 1
        courts[item["court"]] += 1
    print("  By strategy:")
    for s, n in strat.most_common():
        print(f"    {s:16s} {n:4d}")
    print()
    print("  By court (top 10):")
    for c, n in courts.most_common(10):
        star = " *" if c in PRIORITY_COURTS else ""
        print(f"    {c:10s} {n:4d}{star}")
    print()

    print("  SAMPLE — read these and judge precision yourself:")
    print("  (anything obviously not AI-related is a false positive)")
    print()
    for item, hits, parties, keywords, nos in kept[:30]:
        print(f"  - {item['caseName'][:80]}")
        print(f"    {item['court']} | {item['dateFiled']} | {item['docketNumber']}")
        why = []
        if parties:
            why.append(f"party={','.join(parties[:3])}")
        if keywords:
            why.append(f"kw={','.join(keywords[:3])}")
        if nos:
            why.append(f"nos={nos}")
        print(f"    matched: {'; '.join(why)}")
        print()

    outdir = REPO / "out"
    outdir.mkdir(exist_ok=True)
    outfile = outdir / f"calibration-{date.today().isoformat()}.json"
    outfile.write_text(json.dumps([
        {"item": i, "strategies": sorted(h), "parties": p, "keywords": k, "nos": n}
        for i, h, p, k, n in kept
    ], indent=2))
    print(f"  Full candidate list written to {outfile.relative_to(REPO)}")
    print("  (out/ is gitignored)")
    print()


def load_dotenv():
    f = REPO / ".env"
    if f.is_file():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=90, help="lookback window (default 90)")
    ap.add_argument("--max-pages", type=int, default=10, help="page cap (default 10)")
    ap.add_argument("--recall-only", action="store_true",
                    help="skip the API entirely; just measure recall against our corpus")
    ap.add_argument("--debug", action="store_true",
                    help="dump the raw first API response to diagnose shape mismatches")
    args = ap.parse_args()

    print()
    recall_check()

    if args.recall_only:
        print("(--recall-only: skipping live API run)")
        return

    load_dotenv()
    token = os.environ.get("COURTLISTENER_TOKEN")
    if not token:
        print("=" * 72)
        print("No COURTLISTENER_TOKEN set — skipping the live run.")
        print("  export COURTLISTENER_TOKEN=...   (or put it in .env)")
        print("  Generate one at https://www.courtlistener.com/profile/api/")
        print("=" * 72)
        return

    live_run(token, args.days, args.max_pages, args.debug)


if __name__ == "__main__":
    main()
