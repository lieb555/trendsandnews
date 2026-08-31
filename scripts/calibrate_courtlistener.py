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

WHAT RUN #1 (2026-08-31) ESTABLISHED, and why this script changed shape:

  1. The API shape was right. v4 /search/, `Token` auth, type=r, filed_after,
     cursor pagination — all confirmed against the live service.

  2. CourtListener throttles search at 5 requests/minute. This is the
     binding constraint on the entire ingest design. The original approach —
     page through recent federal dockets and filter them in Python — needs
     roughly 15 hours of continuous polling to cover a single 90-day window,
     because federal courts take in far more filings than 100/minute. It
     scanned 100 dockets and kept 0, which is the arithmetic working
     correctly, not the filter failing.

  So the filter moved server-side. Instead of fetching everything and
  discarding almost all of it locally, each probe below is one query the
  server answers with matches only: a handful of requests per run instead
  of thousands.

  3. Docket-metadata matching recalls 97% of AI-as-subject litigation and
     15% of sanctions cases. That gap is structural — in a sanctions case
     the AI fact appears nowhere in the docket metadata, only inside the
     judge's written order. So this version adds full-text opinion search
     (type=o) and tests it directly against named cases the metadata filter
     missed. That test, probe_recall(), is the point of run #2.
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

# CourtListener throttles the search endpoint at 5 requests/minute. That
# number is the single most important constraint on the whole ingest design:
# it makes scanning the docket firehose and filtering client-side impossible
# (see the header note), and it means every request has to earn its place.
# Confirmed empirically on 2026-08-31 by run #1, which got:
#   {"detail":"Request was throttled. Rate limit exceeded: 5/min."}
RATE_LIMIT_PER_MIN = 5
MIN_INTERVAL = 60.0 / RATE_LIMIT_PER_MIN + 1.0   # 13s, with a second of slack

_last_request_at = [0.0]


def _pace():
    """Block until enough time has passed to stay under the published limit."""
    wait = MIN_INTERVAL - (time.monotonic() - _last_request_at[0])
    if wait > 0:
        time.sleep(wait)
    _last_request_at[0] = time.monotonic()


def api_get(token, params, debug=False, _retries=2):
    """
    One paced call to the v4 search endpoint.

    Run #1 confirmed the API shape: v4 /search/, `Token` auth, `type=r` for
    RECAP dockets, `filed_after` as YYYY-MM-DD, cursor pagination via `next`.
    Those are no longer assumptions. What run #1 did NOT confirm is the `q`
    query syntax, which is what this version leans on — if a probe returns
    zero across the board, suspect the query grammar first.
    """
    url = f"{API_BASE}/search/?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Token {token}",
        "User-Agent": "atheria-ai-tracker-calibration/0.2",
        "Accept": "application/json",
    })
    _pace()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        if e.code == 429 and _retries > 0:
            # Honour the hint in the body if there is one, else back off a
            # full window. A 429 is not a failure — it is the API telling us
            # to slow down, and the correct response is to slow down.
            m = re.search(r"available in (\d+) second", detail)
            backoff = int(m.group(1)) + 2 if m else 65
            print(f"    (throttled; waiting {backoff}s)", flush=True)
            time.sleep(backoff)
            return api_get(token, params, debug, _retries - 1)
        print(f"\n  HTTP {e.code} from CourtListener", file=sys.stderr)
        print(f"  URL: {url}", file=sys.stderr)
        print(f"  Body: {detail}\n", file=sys.stderr)
        if e.code == 401:
            print("  -> token rejected. Check COURTLISTENER_TOKEN.", file=sys.stderr)
        if e.code == 400:
            print("  -> query syntax rejected. Correct the probe's `q`.", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"\n  Network error: {e.reason}", file=sys.stderr)
        return None

    data = json.loads(body)
    if debug:
        print("\n--- RAW RESPONSE (keys) ---")
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

# Each probe is one server-side query. The point of expressing the filter as
# a `q` string rather than as Python running over fetched pages is that the
# server does the discarding, so a probe costs one request instead of the
# thousands that scanning the firehose would cost.
#
# `type=r` searches RECAP docket metadata — case names, parties, docket text.
# `type=o` searches the full text of written opinions, which is the only
# place the AI fact exists for the sanctions lane: Gauthier, Frier and Whaley
# are ordinary disputes whose docket metadata says nothing about AI at all.
# Testing whether `type=o` reaches them is the whole reason this run exists.
PROBES = [
    {
        "id": "P1-party-dockets",
        "lane": "litigation",
        "type": "r",
        "windowed": True,
        "q": ('caseName:("OpenAI" OR "Anthropic" OR "Stability AI" OR '
              '"Midjourney" OR "Perplexity" OR "Character Technologies" OR '
              '"NVIDIA" OR "Clearview AI")'),
        "why": "known AI defendants by name — the high-precision baseline",
    },
    {
        "id": "P2-keyword-dockets",
        "lane": "litigation",
        "type": "r",
        "windowed": True,
        "q": ('"artificial intelligence" OR "machine learning" OR '
              '"large language model" OR "generative AI" OR "deepfake" OR '
              '"facial recognition"'),
        "why": "AI-as-subject suits against defendants we have never heard of",
    },
    {
        "id": "P3-sanctions-opinions",
        "lane": "sanctions",
        "type": "o",
        "windowed": False,
        "q": ('("artificial intelligence" OR "ChatGPT" OR "generative AI") AND '
              '("fabricated citation" OR "nonexistent case" OR '
              '"non-existent case" OR "hallucinated")'),
        "why": "THE KEY PROBE — can full-text opinion search reach the 15% lane?",
    },
    {
        "id": "P4-datacenter-dockets",
        "lane": "data-centre",
        "type": "r",
        "windowed": True,
        "q": ('"data center" OR "data centre" OR "hyperscale"'),
        "why": "the AEC professional-liability lane, currently hand-curated",
    },
]

# A hand-picked sample of the sanctions cases the docket-metadata filter
# missed. Querying each by name answers the question the aggregate numbers
# cannot: is the AI fact *findable* by full-text search, or is it genuinely
# out of reach of any automated ingest?
KNOWN_MISSES = [
    "Gauthier v. Goodyear",
    "Frier v. Hingiss",
    "Whaley v. Experian",
    "Morgan v. Community Against Violence",
    "Wadsworth v. Walmart",
    "Mata v. Avianca",
]


def run_probe(token, probe, days, max_pages, debug=False):
    """Run one server-side query and report what came back."""
    params = {"type": probe["type"], "q": probe["q"], "order_by": "dateFiled desc"}
    if probe["windowed"]:
        params["filed_after"] = (date.today() - timedelta(days=days)).isoformat()

    print(f"  [{probe['id']}]  {probe['why']}")
    scope = f"last {days}d" if probe["windowed"] else "all time"
    print(f"    type={probe['type']} · {scope}")

    collected, page, total = [], 0, None
    while page < max_pages:
        data = api_get(token, params, debug=(debug and page == 0))
        if data is None:
            print("    -> request failed; see the error above")
            return probe, [], None
        total = data.get("count")
        results = data.get("results") or []
        for raw in results:
            collected.append(normalize_result(raw))
        page += 1
        nxt = data.get("next")
        if not nxt or not results:
            break
        # `next` is an absolute URL; re-derive params from it for the pacer.
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(nxt).query))

    print(f"    -> {total if total is not None else '?'} total matches; "
          f"pulled {len(collected)}")
    return probe, collected, total


def syntax_check(token):
    """
    Run #2 produced a self-contradiction: P3 found 93 AI-sanctions opinions
    by full-text search, but probe_recall() found 0 of 6 named cases —
    including Mata v. Avianca, which is among the most-discussed opinions of
    2023 and is certainly in CourtListener's corpus. A mechanism that works
    in P3 cannot be genuinely blind in probe B, so the difference is in how
    probe B asks, not in what the corpus holds.

    This isolates the query grammar by asking for the same case six ways.
    Whichever forms return hits tell us what the `q` syntax actually is,
    which matters far beyond this one probe: every stream in the production
    pipeline is a `q` string.
    """
    print("=" * 72)
    print("SYNTAX CHECK — how does CourtListener want to be asked?")
    print("=" * 72)
    print("  Same case, six query forms. Any form returning 0 for a case this")
    print("  famous is a broken query, not an empty corpus.")
    print()

    variants = [
        ("bare term, opinions",        {"type": "o", "q": "Avianca"}),
        ("quoted phrase, opinions",    {"type": "o", "q": '"Mata v. Avianca"'}),
        ("two bare terms, opinions",   {"type": "o", "q": "Mata Avianca"}),
        ("caseName bare, opinions",    {"type": "o", "q": "caseName:Avianca"}),
        ("caseName quoted (probe B)",  {"type": "o", "q": 'caseName:("Mata v. Avianca")'}),
        ("bare term, dockets",         {"type": "r", "q": "Avianca"}),
    ]

    results = []
    for label, params in variants:
        data = api_get(token, params)
        if data is None:
            print(f"    {label:30s} REQUEST FAILED")
            results.append((label, params["q"], None))
            continue
        count = data.get("count")
        hits = data.get("results") or []
        flag = "  <-- probe B used this" if "probe B" in label else ""
        print(f"    {label:30s} {str(count):>6} matches{flag}")
        if hits:
            print(f"        top: {normalize_result(hits[0])['caseName'][:60]}")
        results.append((label, params["q"], count))
    print()
    working = [r for r in results if r[2]]
    if working:
        print(f"  Query forms that work: {len(working)}/{len(variants)}")
        print(f"  -> use `{working[0][1]}`-style queries in the pipeline")
    else:
        print("  No form returned anything. The problem is not the grammar —")
        print("  re-check the endpoint and the corpus coverage.")
    print()
    return results


def probe_recall(token, max_pages, debug=False):
    """
    The decisive experiment. For each sanctions case the metadata filter
    missed, ask whether full-text opinion search can find it at all.
    """
    print("=" * 72)
    print("PROBE B — can full-text search reach the cases metadata missed?")
    print("=" * 72)
    print("  Each of these is a case the docket-metadata filter dropped. If")
    print("  full-text opinion search finds them, the 15% sanctions recall is")
    print("  a wrong-endpoint problem, not a ceiling.")
    print()

    found = 0
    for name in KNOWN_MISSES:
        params = {"type": "o", "q": f'caseName:("{name}")', "order_by": "dateFiled desc"}
        data = api_get(token, params, debug=False)
        if data is None:
            print(f"    {name:42s} request failed")
            continue
        results = data.get("results") or []
        if results:
            found += 1
            top = normalize_result(results[0])
            snippet = re.sub(r"<[^>]+>", "", top.get("snippet") or "")[:90]
            print(f"    {name:42s} FOUND  ({data.get('count')} hits)")
            print(f"        {top['caseName'][:70]}")
            if snippet:
                print(f"        …{snippet}…")
        else:
            print(f"    {name:42s} not found")
    print()
    pct = 100 * found / len(KNOWN_MISSES) if KNOWN_MISSES else 0
    print(f"  Reachable by full-text opinion search: {found}/{len(KNOWN_MISSES)}"
          f"  ({pct:.0f}%)")
    print()
    return found, len(KNOWN_MISSES)


def live_run(token, days, max_pages, debug, syntax_only=False):
    # The syntax check runs first because it gates how everything after it
    # should be read: if the query grammar is wrong, a zero result anywhere
    # below means nothing at all.
    syntax_check(token)
    if syntax_only:
        return

    print("=" * 72)
    print("PROBE A — server-side queries")
    print("=" * 72)
    print(f"  Paced at {RATE_LIMIT_PER_MIN}/min ({MIN_INTERVAL:.0f}s between calls);")
    print("  this run will take a few minutes and that is expected.")
    print()

    all_hits = []
    for probe in PROBES:
        p, collected, total = run_probe(token, probe, days, max_pages, debug)
        all_hits.append({"probe": p["id"], "lane": p["lane"], "total": total,
                         "pulled": len(collected), "items": collected})
        for item in collected[:5]:
            print(f"       · {item['caseName'][:66]}")
            print(f"         {item['court']} | {item['dateFiled']}")
        print()

    probe_recall(token, max_pages, debug)

    outdir = REPO / "out"
    outdir.mkdir(exist_ok=True)
    outfile = outdir / f"calibration-{date.today().isoformat()}.json"
    outfile.write_text(json.dumps(all_hits, indent=2))
    print(f"  Full results written to {outfile.relative_to(REPO)}")
    print("  (out/ is gitignored; the workflow uploads it as an artifact)")
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
    ap.add_argument("--syntax-only", action="store_true",
                    help="only run the query-grammar check (6 requests, ~1 minute)")
    args = ap.parse_args()

    print()
    if not args.syntax_only:
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

    live_run(token, args.days, args.max_pages, args.debug, args.syntax_only)


if __name__ == "__main__":
    main()
