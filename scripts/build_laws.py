#!/usr/bin/env python3
"""Turn the weekly INDEX.csv drop into the JSON the state-law tracker renders.

Run it after each Monday drop:

    python3 scripts/build_laws.py

It reads ``data/laws/INDEX.csv``, derives the coverage-facing fields that the
CSV does not carry (theme, regulated role, likely responding lines, the three
scored axes), diffs the result against the previous build so the page can show
what moved, and writes ``data/laws.json``.  ``--inject`` then writes that JSON
into ``laws.html`` between the DATA markers so the page stays a single
self-contained file.

Nothing here invents legal content.  Every derived field is a restatement of
columns already in the CSV, and the row keeps the raw text so an attorney can
check the derivation against the source.  Where the CSV says ``unconfirmed``
the derivation says ``unconfirmed`` too rather than guessing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_IN = ROOT / "data" / "laws" / "INDEX.csv"
JSON_OUT = ROOT / "data" / "laws.json"
HTML_OUT = ROOT / "laws.html"

DATA_BEGIN = "/* DATA:BEGIN */"
DATA_END = "/* DATA:END */"

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}

# Column-and-row position on the standard US grid cartogram.  Row 0 is the top.
GRID = {
    "AK": (0, 0), "ME": (0, 10),
    "VT": (1, 9), "NH": (1, 10),
    "WA": (2, 1), "ID": (2, 2), "MT": (2, 3), "ND": (2, 4), "MN": (2, 5),
    "IL": (2, 6), "WI": (2, 7), "MI": (2, 8), "NY": (2, 9), "RI": (2, 10), "MA": (2, 11),
    "OR": (3, 1), "NV": (3, 2), "WY": (3, 3), "SD": (3, 4), "IA": (3, 5),
    "IN": (3, 6), "OH": (3, 7), "PA": (3, 8), "NJ": (3, 9), "CT": (3, 10),
    "CA": (4, 1), "UT": (4, 2), "CO": (4, 3), "NE": (4, 4), "MO": (4, 5),
    "KY": (4, 6), "WV": (4, 7), "VA": (4, 8), "MD": (4, 9), "DE": (4, 10),
    "AZ": (5, 2), "NM": (5, 3), "KS": (5, 4), "AR": (5, 5), "TN": (5, 6),
    "NC": (5, 7), "SC": (5, 8), "DC": (5, 9),
    "OK": (6, 4), "LA": (6, 5), "MS": (6, 6), "AL": (6, 7), "GA": (6, 8),
    "HI": (7, 0), "TX": (7, 4), "FL": (7, 9), "PR": (7, 11),
}

# ---------------------------------------------------------------------------
# Field normalisation
# ---------------------------------------------------------------------------

# The CSV uses a controlled vocabulary for "nothing here": `silent` means the
# drafters said nothing, `none` means the answer is affirmatively no, `n/a`
# means the question does not arise, `unconfirmed` means the researcher could
# not verify.  Only the last one is a data-quality signal; the first three are
# findings.  Everything else is substantive text.
EMPTY_PREFIXES = ("silent", "none", "n/a", "na ", "no ", "not applicable")
UNCONFIRMED_PREFIXES = ("unconfirmed", "unknown", "confirm ", "tbd")


def state_of(field: str) -> str:
    """Classify a cell as present / silent / unconfirmed."""
    v = (field or "").strip().lower()
    if not v:
        return "silent"
    if any(v.startswith(p) for p in UNCONFIRMED_PREFIXES):
        return "unconfirmed"
    if v in ("n/a", "na", "none", "silent", "-", "—"):
        return "silent"
    if any(v.startswith(p) for p in EMPTY_PREFIXES):
        # "none in statute", "silent (none in chapter)", "no as to CORA" — all
        # findings of absence.  But "none identified" plus a real clause after
        # a dash is still absence, so prefix matching is the right test.
        return "silent"
    return "present"


def present(field: str) -> bool:
    return state_of(field) == "present"


DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
YEAR_RE = re.compile(r"\b(20\d{2})\b")


def parse_date(field: str):
    """Return (iso_date_or_None, year_or_None, confidence)."""
    raw = (field or "").strip()
    m = DATE_RE.search(raw)
    if m:
        iso = m.group(0)
        # A cell holding two dates ("2025-01-01 (SB 942); 2026-01-01 (AB 853)")
        # is a staged commencement.  The first is when duties first attach,
        # which is the one an underwriter needs.
        conf = "staged" if len(DATE_RE.findall(raw)) > 1 else "exact"
        return iso, int(m.group(1)), conf
    y = YEAR_RE.search(raw)
    if y:
        return f"{y.group(1)}-01-01", int(y.group(1)), "year-only"
    return None, None, "unparsed"


MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|m\b)?", re.I)


def max_dollars(*fields) -> int:
    """Largest dollar figure named anywhere in the given cells."""
    best = 0
    for f in fields:
        for amount, scale in MONEY_RE.findall(f or ""):
            try:
                v = float(amount.replace(",", ""))
            except ValueError:
                continue
            if scale:
                v *= 1_000_000
            best = max(best, int(v))
    return best


# ---------------------------------------------------------------------------
# Subject-matter themes
# ---------------------------------------------------------------------------

# Ordered by specificity: the first theme whose pattern matches wins.  Matching
# runs twice — first against the title and the regulatory hook alone, which is
# high-precision, then against the fuller text.  Without the two passes a
# deepfake statute that happens to mention emotional distress lands in the
# health cluster.
THEMES = [
    ("ncii", "NCII & sexual deepfakes", (
        r"\bintimate\b|sexually explicit|sexually expressive|sexual depiction|"
        r"altered sexual|explicit synthetic|revenge porn|\bncii\b|"
        r"child sexual|\bcsam\b|obscen|voyeur|nudif|private image")),
    ("election", "Election & political synthetic media", (
        r"election|candidate|political advertis|political communic|ballot|"
        r"electioneering|campaign")),
    ("publicity", "Digital replica & right of publicity", (
        r"digital replica|right of publicity|likeness|personality right|"
        r"digital voice|voice and image|deceased personality|"
        r"name, image|forged digital|synthetic performer")),
    ("insurance", "Insurance underwriting & pricing", (
        r"\becdis\b|insurance practice|algorithmic pricing|rent[- ]setting|"
        r"insurer.{0,40}(underwrit|pricing|rating|predictive model)")),
    ("ur", "Utilization review & prior auth", (
        r"utilization review|utilization management|prior auth|preauthoriz|"
        r"medical[- ]necessity|adverse determination|downcode|private review agent|"
        r"claim denial|health coverage|health benefit plan")),
    ("chatbot", "Companion chatbots & conversational AI", (
        r"companion chatbot|companion bot|\bai compan|artificial intelligence compan|"
        r"conversational ai|chatbot")),
    ("health", "Clinical & health-care AI", (
        r"health care|healthcare|clinical|patient|scribe|physician|nurs(e|ing)|"
        r"healing[- ]arts|mental[- ]health|behavioral[- ]health|therap|psycholog|"
        r"medical record|telehealth|health facilit")),
    ("biometric", "Biometric identifiers", (
        r"biometric identifier|biometric information|\bbipa\b|facial recognition|"
        r"face template|voiceprint|iris scan|\bbiometrics\b")),
    ("employment", "Employment & hiring ADS", (
        r"\bemploym|hiring|job applicant|video interview|\baedt\b|"
        r"human rights act|personnel decision|\bemployer")),
    ("govt", "Government & public-entity AI use", (
        r"public[- ]entit|public[- ]bod|state agenc|state government|governmental|"
        r"law enforcement|agency inventory|advisory council|task force|"
        r"procurement|criminal justice|state ads|division of artificial")),
    ("frontier", "Frontier models & developer duties", (
        r"frontier|foundation model|training data|catastrophic risk|"
        r"model weights|compute threshold|safety framework|safety measures|"
        r"whistleblow|\braise act\b")),
    ("deepfake", "Deepfakes, provenance & impersonation", (
        r"deep ?fake|synthetic media|synthetically created|digital imitation|"
        r"provenance|watermark|impersonat|identity fraud|forgery|"
        r"deceptive audio|altered media|authentic")),
    ("privacy", "Consumer privacy & profiling opt-out", (
        r"data privacy|data protection|profiling|consumer data|"
        r"personal data|opt[- ]out|\badmt\b|automated decision|"
        r"consequential decision")),
    ("consumer", "Consumer disclosure & UDAP", (
        r"consumer protection|consumer disclosure|deceptive|disclosure|supplier|"
        r"unfair.{0,20}practice|transparency|advertis")),
]

THEME_LABELS = {k: label for k, label, _ in THEMES}
THEME_ORDER = [k for k, _, _ in THEMES]
THEME_LABELS["other"] = "Other / general AI governance"
THEME_ORDER.append("other")


def classify_theme(row) -> str:
    """Match on the narrowest text first and widen only on a miss.

    The short title is what the legislature called the statute, so it is the
    most reliable signal; the hook and summary bring in adjacent language
    ("emotional distress", "health data") that would otherwise pull a statute
    into the wrong cluster.
    """
    title = row["short_title"].lower()
    hook = title + " " + row["hook"].lower()
    wide = hook + " " + row["who_regulated"].lower() + " " + row["summary"][:600].lower()
    for scope in (title, hook, wide):
        for key, _, pattern in THEMES:
            if re.search(pattern, scope):
                return key
    return "other"


# Lines of insurance a statute of this kind most plausibly touches.  These are
# the lines to *check*, not a coverage opinion — the page says so on its face.
THEME_LINES = {
    "ur":         ["Insurance Co. PL / E&O", "D&O", "Bad faith & extra-contractual", "Regulatory"],
    "insurance":  ["Insurance Co. PL / E&O", "D&O", "Regulatory", "Bad faith & extra-contractual"],
    "health":     ["Medical professional liability", "Allied health PL", "Tech E&O", "Cyber"],
    "chatbot":    ["Tech E&O", "Products liability", "Media liability", "GL — bodily injury"],
    "ncii":       ["Media liability", "Personal & advertising injury (Cov. B)", "Cyber", "Umbrella"],
    "election":   ["Media liability", "Personal & advertising injury (Cov. B)", "D&O"],
    "publicity":  ["Media liability", "Personal & advertising injury (Cov. B)", "Tech E&O"],
    "deepfake":   ["Media liability", "Tech E&O", "Crime / social engineering", "Cyber"],
    "employment": ["EPL", "D&O", "Tech E&O (vendor)", "Fiduciary"],
    "biometric":  ["Cyber", "Personal & advertising injury (Cov. B)", "Tech E&O", "EPL"],
    "privacy":    ["Cyber", "Tech E&O", "Media liability", "Regulatory"],
    "frontier":   ["Tech E&O", "D&O", "Reps & warranties", "Regulatory"],
    "govt":       ["Public officials liability", "Law enforcement liability", "Tech E&O (vendor)"],
    "consumer":   ["Tech E&O", "Media liability", "Regulatory", "D&O"],
    "other":      ["Tech E&O", "Regulatory"],
}

# Who the duty lands on.  A single statute can land on several.
ROLES = [
    ("insurer",   "Insurer / health plan",
     r"\binsurer|health carrier|health plan|health benefit plan|carrier|"
     r"knox-keene|utilization review (organi|agent)|private review agent|hmo|"
     r"health service plan|health care service plan"),
    ("developer", "AI developer",
     r"\bdevelop(er|ers|s|ing)\b|frontier developer|person that (creates|develops)|"
     r"covered genai provider|model provider"),
    ("deployer",  "AI deployer / business user",
     r"\bdeploy(er|ers)\b|controller|processor|supplier|business that|"
     r"person that uses|entity that uses|operator"),
    ("platform",  "Platform / hosting",
     r"platform|hosting|app store|social media|search engine|marketplace"),
    ("provider",  "Licensed professional / provider",
     r"physician|licensed|clinician|health facilit|practitioner|provider|"
     r"professional|attorney|therapist"),
    ("employer",  "Employer",
     r"employer|hiring|job applicant|employment agenc"),
    ("govt",      "Government body",
     r"state agenc|public entity|governmental|law enforcement|school|"
     r"public body|county|municipal"),
    ("individual", "Any person (criminal/tort)",
     r"any person|a person who|whoever|individual who"),
]

ROLE_LABELS = {k: label for k, label, _ in ROLES}
ROLE_ORDER = [k for k, _, _ in ROLES]


def classify_roles(row) -> list:
    blob = (row["who_regulated"] + " " + row["hook"]).lower()
    found = [k for k, _, pattern in ROLES if re.search(pattern, blob)]
    return found or ["individual"]


# ---------------------------------------------------------------------------
# Axis 1 — claim exposure
# ---------------------------------------------------------------------------

def score_private_action(row):
    """0–30.  Who can sue, and does the statute hand them a cause of action."""
    v = row["private_suit"].strip().lower()
    if state_of(row["private_suit"]) != "present":
        return 0, "No private right of action found"
    if re.search(r"\bcpa per se|unfair or deceptive act", v):
        return 26, "Per-se consumer-protection claim (state UDAP machinery)"
    if v.startswith("pra"):
        return 30, "Private right of action"
    if v.startswith("pag"):
        return 18, "Gated private action (agency charge or standing limit first)"
    if "private" in v or "may bring" in v or "civil action" in v:
        return 24, "Private civil action available"
    return 12, "Private remedy referenced but not a clean statutory PRA"


def score_aggregation(row):
    """0–20.  Can one incident become many claims."""
    pts, notes = 0, []
    together = row["plaintiffs_together"].lower()
    klass = row["class_collective"].lower()
    dmg = row["statutory_damages"].lower()
    if re.search(r"class action|class or collective|may proceed as a class", klass):
        pts += 12
        notes.append("class expressly contemplated")
    elif state_of(row["class_collective"]) == "present":
        pts += 6
        notes.append("class treatment addressed")
    if "stacking" in together:
        pts += 8
        notes.append("claims stack across plaintiffs")
    if re.search(r"per\s+(?:\w+\s+){0,2}violation|each\s+(?:\w+\s+){0,2}violation|"
                 r"per day|per image|per instance|per depiction|"
                 r"each day.{0,30}(discrete|separate)", dmg):
        pts += 8
        notes.append("damages accrue per violation or per day")
    # Silence on class treatment is not an absence of class risk: where the
    # statute creates a private action and says nothing about aggregation, the
    # ordinary class rules apply.  BIPA is the cautionary example — the text is
    # silent and the class litigation defines the exposure.
    if not pts and state_of(row["private_suit"]) == "present" and \
            klass.startswith("silent") and "no pra" not in klass:
        pts += 6
        notes.append("silent on class — default class rules apply")
    if not notes:
        notes.append("no aggregation mechanism on the face of the statute")
    return min(pts, 20), "; ".join(notes)


def score_quantum(row):
    """0–20.  Log-scaled on the largest sum the statute names."""
    amount = max_dollars(row["statutory_damages"])
    if amount <= 0:
        if state_of(row["statutory_damages"]) == "present":
            return 6, "Damages available but no figure named (actual damages / disgorgement)"
        return 0, "No statutory damages"
    # $500 -> ~6, $1k -> ~8, $10k -> ~12, $100k -> ~16, $1M+ -> 20
    pts = min(20, round(4 * math.log10(amount)))
    return max(pts, 4), f"Largest sum named: ${amount:,}"


def score_fees(row):
    v = row["fee_shifting"].lower()
    if state_of(row["fee_shifting"]) != "present":
        return 0, "No fee-shifting"
    if "prevailing plaintiff" in v or "injured person may recover" in v or v.startswith("yes"):
        return 10, "One-way fee-shifting to a prevailing plaintiff"
    if "prevailing party" in v:
        return 6, "Two-way fee-shifting"
    return 5, "Fees referenced"


def score_no_offramp(row):
    """0–10.  Absence of a cure period or safe harbour is an exposure, not a control."""
    pts, notes = 0, []
    if state_of(row["cure_period"]) != "present" and state_of(row["notice_cure"]) != "present":
        pts += 5
        notes.append("no cure period")
    if state_of(row["safe_harbor"]) != "present":
        pts += 5
        notes.append("no safe harbour")
    return pts, "; ".join(notes) if notes else "cure and/or safe harbour available"


def score_public_enforcement(row):
    """0–10.  Regulatory and criminal attention drives defence cost even without a PRA."""
    v = row["enforcer"].lower()
    pts, notes = 0, []
    if re.search(r"attorney general|\bag\b|prosecut|commission|department|division|"
                 r"board|bureau|superintendent|secretary of state", v):
        pts += 4
        notes.append("public enforcer")
    enforcers = len(re.findall(r"[,;]|\bor\b", v))
    if enforcers >= 2:
        pts += 3
        notes.append("multiple enforcers (incl. local)")
    if re.search(r"crime|criminal|felony|misdemeanor", row["criminal_overlay"].lower()):
        pts += 3
        notes.append("criminal overlay")
    return min(pts, 10), "; ".join(notes) if notes else "no public enforcer identified"


EXPOSURE_PARTS = [
    ("pra", "Private right of action", 30, score_private_action),
    ("aggregation", "Aggregation potential", 20, score_aggregation),
    ("quantum", "Damages quantum", 20, score_quantum),
    ("fees", "Fee-shifting", 10, score_fees),
    ("offramp", "No cure / no safe harbour", 10, score_no_offramp),
    ("public", "Public enforcement", 10, score_public_enforcement),
]


def exposure_band(score: int) -> str:
    if score >= 62:
        return "acute"
    if score >= 42:
        return "elevated"
    if score >= 22:
        return "moderate"
    return "low"


# ---------------------------------------------------------------------------
# Axis 2 — insurability friction
# ---------------------------------------------------------------------------

def insurability_flags(row):
    """Features that tend to put a loss outside the four corners of a policy."""
    flags = []
    crim = row["criminal_overlay"].lower()
    if re.search(r"crime-only|category [a-e] felony|felony|misdemeanor", crim) and "civil-only" not in crim:
        flags.append({
            "key": "criminal",
            "label": "Criminal exposure",
            "why": "Criminal-acts and intentional-acts exclusions; defence-cost-only questions.",
            "cite": row["criminal_overlay"][:220],
        })
    elif "also crime" in crim:
        flags.append({
            "key": "criminal",
            "label": "Parallel criminal statute",
            "why": "A parallel prosecution can trigger the conduct exclusions on the civil claim.",
            "cite": row["criminal_overlay"][:220],
        })
    dmg = row["statutory_damages"].lower()
    if "punitive" in dmg or "exemplary" in dmg:
        flags.append({
            "key": "punitive",
            "label": "Punitive damages",
            "why": "Uninsurable by statute or public policy in a number of states; check the most-favoured-venue wording.",
            "cite": row["statutory_damages"][:220],
        })
    if re.search(r"disgorge|restitution|profits attributable|unjust enrichment", dmg):
        flags.append({
            "key": "disgorgement",
            "label": "Disgorgement / restitution",
            "why": "Often held not to be 'damages' or 'loss'; restitutionary relief is commonly carved out.",
            "cite": row["statutory_damages"][:220],
        })
    if re.search(r"civil penalt|fine|administrative penalt", dmg) or \
       re.search(r"civil penalt|fine", row["enforcer"].lower()):
        flags.append({
            "key": "penalty",
            "label": "Civil penalties / fines",
            "why": "Typically sublimited where insurable at all, and insurable-where-permitted wording controls.",
            "cite": (row["statutory_damages"] or row["enforcer"])[:220],
        })
    if state_of(row["waiver_indemnity_ban"]) == "present":
        flags.append({
            "key": "indemnity_ban",
            "label": "Waiver or indemnity restricted",
            "why": "The statute limits contracting out — risk cannot be pushed down the vendor chain.",
            "cite": row["waiver_indemnity_ban"][:220],
        })
    if state_of(row["impact_assessment_discoverable"]) == "present" and \
       not row["impact_assessment_discoverable"].lower().startswith("no"):
        flags.append({
            "key": "discovery",
            "label": "Assessment discoverability addressed",
            "why": "Impact assessments become claim documents; affects defensibility and reserving.",
            "cite": row["impact_assessment_discoverable"][:220],
        })
    return flags


# ---------------------------------------------------------------------------
# Axis 3 — controls the insured can actually pull
# ---------------------------------------------------------------------------

def controls(row):
    """Underwriting levers: what the statute lets a compliant insured do."""
    out = []
    if state_of(row["cure_period"]) == "present":
        out.append({"key": "cure", "label": "Cure period", "cite": row["cure_period"][:220]})
    if state_of(row["notice_cure"]) == "present":
        out.append({"key": "notice", "label": "Notice before suit", "cite": row["notice_cure"][:220]})
    if state_of(row["safe_harbor"]) == "present":
        sh = row["safe_harbor"]
        framework = bool(re.search(r"nist|iso[ /]?42001|ai rmf", sh, re.I))
        out.append({
            "key": "framework" if framework else "safe_harbor",
            "label": "Recognised framework defence" if framework else "Safe harbour",
            "cite": sh[:220],
        })
    if state_of(row["thresholds"]) == "present":
        out.append({"key": "threshold", "label": "Size / scale threshold", "cite": row["thresholds"][:220]})
    if state_of(row["record_retention"]) == "present":
        out.append({"key": "retention", "label": "Record-retention duty", "cite": row["record_retention"][:220]})
    if state_of(row["limitations_period"]) == "present":
        out.append({"key": "limitations", "label": "Limitations period stated", "cite": row["limitations_period"][:220]})
    return out


# ---------------------------------------------------------------------------
# Territorial reach — coverage-territory relevance for a national book
# ---------------------------------------------------------------------------

def reach(row):
    out = {"level": "in-state", "notes": []}
    if state_of(row["out_of_state_actor"]) == "present":
        out["level"] = "long-arm"
        out["notes"].append(row["out_of_state_actor"][:200])
    if state_of(row["nexus"]) == "present":
        if out["level"] != "long-arm":
            out["level"] = "nexus-defined"
        out["notes"].append(row["nexus"][:200])
    if state_of(row["no_extraterritorial_clause"]) == "present":
        out["level"] = "limited"
        out["notes"].append(row["no_extraterritorial_clause"][:200])
    if state_of(row["choice_of_law_forum"]) == "present":
        out["notes"].append(row["choice_of_law_forum"][:200])
    return out


def status_bucket(row, today: date) -> str:
    v = row["status"].strip().lower()
    if v.startswith("sunset") or "repealed" in v:
        return "sunset"
    eff_iso, _, _ = parse_date(row["effective_date"])
    op_iso, _, _ = parse_date(row["operative_date"])
    attach = op_iso if (op_iso and (not eff_iso or op_iso > eff_iso)) else eff_iso
    if v.startswith("in force") and "delayed" not in v:
        return "in-force"
    if attach and attach > today.isoformat():
        return "pending"
    if v.startswith("delayed"):
        return "pending"
    if v.startswith("in force"):
        return "in-force-partial"
    return "enacted"


def attach_date(row):
    """When duties first bite — the date an underwriter prices against."""
    eff_iso, eff_y, eff_c = parse_date(row["effective_date"])
    op_raw = row["operative_date"].strip().lower()
    if op_raw.startswith("same") or op_raw.startswith("unconfirmed") or not op_raw:
        return eff_iso, eff_y, eff_c
    op_iso, op_y, op_c = parse_date(row["operative_date"])
    if op_iso and eff_iso:
        return (op_iso, op_y, op_c) if op_iso > eff_iso else (eff_iso, eff_y, eff_c)
    return (op_iso, op_y, op_c) if op_iso else (eff_iso, eff_y, eff_c)


def row_fingerprint(row) -> str:
    """Hash of the substantive columns, so a re-check that only bumps
    last_checked does not register as a change."""
    payload = "|".join(
        (row.get(c) or "") for c in row.keys() if c != "last_checked"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build(csv_path: Path, previous: dict | None, today: date):
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        raw_rows = list(csv.DictReader(fh))

    prev_index = {r["id"]: r for r in (previous or {}).get("laws", [])}
    laws, problems = [], []
    seen_ids = Counter()

    for i, row in enumerate(raw_rows):
        row = {k: (v or "").strip() for k, v in row.items()}
        juris = row["jurisdiction"].upper()
        if juris not in STATE_NAMES:
            problems.append(f"row {i + 2}: unknown jurisdiction {juris!r}")

        # Stable id from jurisdiction + citation, so a row keeps its identity
        # across drops even when it moves position in the file.
        base = f"{juris}-" + re.sub(r"[^a-z0-9]+", "-", row["citation_bill"].lower())[:60].strip("-")
        seen_ids[base] += 1
        law_id = base if seen_ids[base] == 1 else f"{base}-{seen_ids[base]}"

        parts, total = {}, 0
        for key, label, cap, fn in EXPOSURE_PARTS:
            pts, note = fn(row)
            pts = max(0, min(pts, cap))
            parts[key] = {"label": label, "points": pts, "max": cap, "note": note}
            total += pts

        eff_iso, eff_year, eff_conf = parse_date(row["effective_date"])
        enact_iso, enact_year, enact_conf = parse_date(row["enacted_date"])
        at_iso, at_year, at_conf = attach_date(row)

        flags = insurability_flags(row)
        ctrls = controls(row)
        theme = classify_theme(row)
        unconfirmed = [
            c for c in ("private_suit", "statutory_damages", "enforcer", "effective_date",
                        "cure_period", "safe_harbor", "criminal_overlay")
            if state_of(row[c]) == "unconfirmed"
        ]

        law = {
            "id": law_id,
            "jurisdiction": juris,
            "state_name": STATE_NAMES.get(juris, juris),
            "citation": row["citation_bill"],
            "title": row["short_title"],
            "summary": row["summary"],
            "url": row["primary_url"],
            "status_raw": row["status"],
            "status": status_bucket(row, today),
            "theme": theme,
            "theme_label": THEME_LABELS[theme],
            "lines": THEME_LINES[theme],
            "roles": classify_roles(row),
            "who_regulated": row["who_regulated"],
            "hook": row["hook"],
            "carve_outs": row["carve_outs"],
            "enforcer": row["enforcer"],
            "enacted": enact_iso, "enacted_year": enact_year, "enacted_conf": enact_conf,
            "effective": eff_iso, "effective_year": eff_year, "effective_conf": eff_conf,
            "attaches": at_iso, "attaches_year": at_year, "attaches_conf": at_conf,
            "effective_raw": row["effective_date"],
            "operative_raw": row["operative_date"],
            "exposure": total,
            "band": exposure_band(total),
            "parts": parts,
            "flags": flags,
            "controls": ctrls,
            "reach": reach(row),
            "max_dollars": max_dollars(row["statutory_damages"]),
            "private_suit_raw": row["private_suit"],
            "statutory_damages_raw": row["statutory_damages"],
            "fee_shifting_raw": row["fee_shifting"],
            "class_raw": row["class_collective"],
            "criminal_raw": row["criminal_overlay"],
            "insurance_situs": row["insurance_situs"],
            "preemption": {
                "local": row["local_preemption"],
                "federal": row["federal_preemption"],
            },
            "ai_definition": row["ai_definition"],
            "retroactivity": row["retroactivity"],
            "limitations": row["limitations_period"],
            "last_checked": row["last_checked"],
            "unconfirmed": unconfirmed,
            "fp": row_fingerprint(row),
        }

        prior = prev_index.get(law_id)
        if prior is None:
            law["delta"] = "new"
        elif prior.get("fp") != law["fp"]:
            law["delta"] = "changed"
            law["delta_from"] = {
                "exposure": prior.get("exposure"),
                "status": prior.get("status"),
            }
        else:
            law["delta"] = "same"

        laws.append(law)

    laws.sort(key=lambda x: (-x["exposure"], x["jurisdiction"], x["title"]))

    dropped = [lid for lid in prev_index if lid not in {x["id"] for x in laws}]

    meta = {
        "generated": today.isoformat(),
        "source_file": csv_path.name,
        "source_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest()[:16],
        "count": len(laws),
        "jurisdictions": len({x["jurisdiction"] for x in laws}),
        "last_checked": laws[0]["last_checked"] if laws else None,
        "previous_generated": (previous or {}).get("meta", {}).get("generated"),
        "delta": {
            "new": sum(1 for x in laws if x["delta"] == "new"),
            "changed": sum(1 for x in laws if x["delta"] == "changed"),
            "dropped": len(dropped),
            "dropped_ids": dropped,
            "first_build": previous is None,
        },
        "problems": problems,
        "theme_labels": THEME_LABELS,
        "theme_order": THEME_ORDER,
        "role_labels": ROLE_LABELS,
        "role_order": ROLE_ORDER,
        "grid": GRID,
        "state_names": STATE_NAMES,
        "exposure_parts": [
            {"key": k, "label": label, "max": cap} for k, label, cap, _ in EXPOSURE_PARTS
        ],
    }
    return {"meta": meta, "laws": laws}


def inject(payload: dict, html_path: Path) -> None:
    html = html_path.read_text(encoding="utf-8")
    start = html.index(DATA_BEGIN) + len(DATA_BEGIN)
    end = html.index(DATA_END)
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    html_path.write_text(html[:start] + "\nconst DATA = " + blob + ";\n" + html[end:],
                         encoding="utf-8")


def report(payload: dict) -> None:
    meta, laws = payload["meta"], payload["laws"]
    print(f"{meta['count']} laws across {meta['jurisdictions']} jurisdictions "
          f"(source {meta['source_file']} @ {meta['source_sha256']})")
    bands = Counter(x["band"] for x in laws)
    print("  bands:      " + ", ".join(f"{k} {bands[k]}" for k in
                                       ("acute", "elevated", "moderate", "low")))
    themes = Counter(x["theme"] for x in laws)
    print("  themes:     " + ", ".join(f"{THEME_LABELS[k]} {n}"
                                       for k, n in themes.most_common(6)))
    statuses = Counter(x["status"] for x in laws)
    print("  status:     " + ", ".join(f"{k} {n}" for k, n in statuses.most_common()))
    pra = sum(1 for x in laws if x["parts"]["pra"]["points"] >= 18)
    print(f"  private actions: {pra}")
    d = meta["delta"]
    if d["first_build"]:
        print("  delta:      first build — everything reads as new")
    else:
        print(f"  delta:      {d['new']} new, {d['changed']} changed, {d['dropped']} dropped")
    unconf = sum(1 for x in laws if x["unconfirmed"])
    print(f"  rows carrying an unconfirmed field: {unconf}")
    for p in meta["problems"]:
        print(f"  ! {p}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, default=CSV_IN)
    ap.add_argument("--out", type=Path, default=JSON_OUT)
    ap.add_argument("--html", type=Path, default=HTML_OUT)
    ap.add_argument("--inject", action="store_true",
                    help="also write the payload into laws.html")
    ap.add_argument("--today", default=None, help="override the build date (YYYY-MM-DD)")
    args = ap.parse_args()

    if not args.csv.exists():
        print(f"no CSV at {args.csv}", file=sys.stderr)
        return 1

    previous = None
    if args.out.exists():
        try:
            previous = json.loads(args.out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("previous build unreadable; treating this as a first build", file=sys.stderr)

    today = date.fromisoformat(args.today) if args.today else date.today()
    payload = build(args.csv, previous, today)
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    report(payload)
    print(f"  wrote {args.out.relative_to(ROOT)}")

    if args.inject:
        if not args.html.exists():
            print(f"no HTML at {args.html}", file=sys.stderr)
            return 1
        inject(payload, args.html)
        print(f"  injected into {args.html.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
