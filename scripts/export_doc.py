#!/usr/bin/env python3
"""Render the state-law index as a paginated document rather than a web page.

The tracker is built to be filtered and clicked. A brief that goes to a client,
into a claim file, or across a table at a renewal meeting has to hold still: one
ordering, every measure spelled out, page numbers, and a scope line saying what
was left out.

    python3 scripts/export_doc.py                      # the whole index
    python3 scripts/export_doc.py --state CA --state NY
    python3 scripts/export_doc.py --band acute --pending
    python3 scripts/export_doc.py --line "Insurance Co. PL / E&O"
    python3 scripts/export_doc.py --pdf                # also render the PDF

Writes ``out/laws-brief.html``. Open it and print to PDF from the browser —
Chrome's engine paginates it correctly and needs nothing installed. ``--pdf``
does that step here instead, if Playwright and Chromium happen to be available.

Filters combine with AND, and repeat to widen: ``--state CA --state NY`` is
California *or* New York. Whatever is applied gets printed on the cover, so a
reader can never mistake an excerpt for the whole index.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_IN = ROOT / "data" / "laws.json"
OUT_DIR = ROOT / "out"

E = html.escape
BANDS = ["acute", "elevated", "moderate", "low"]
BAND_LABEL = {"acute": "Acute", "elevated": "Elevated", "moderate": "Moderate", "low": "Low"}
# Same ramp the web page uses, validated as an ordinal ramp. Monotone lightness,
# so it survives a greyscale printer.
SEQ = ["#6fc199", "#2ea56f", "#0c8149", "#08532f"]


def money(n: int) -> str:
    if n >= 1_000_000:
        return f"${n / 1_000_000:.0f}M" if n % 1_000_000 == 0 else f"${n / 1_000_000:.1f}M"
    return f"${n:,}"


def who_can_sue(l: dict) -> str:
    p = l["parts"]["pra"]["points"]
    if p >= 30:
        return "Private plaintiff"
    if p >= 24:
        return "Private plaintiff (via UDAP)"
    if p >= 18:
        return "Private, gated"
    if p > 0:
        return "Private remedy, unclear"
    return "Regulator only" if l["parts"]["public"]["points"] else "No enforcer identified"


def step_for(score: int) -> int:
    return 4 if score >= 62 else 3 if score >= 42 else 2 if score >= 22 else 1 if score else 0


def select(laws, args, today):
    """Apply the filters and describe what survived, for the cover."""
    out, scope = laws, []
    if args.state:
        want = {s.upper() for s in args.state}
        out = [l for l in out if l["jurisdiction"] in want]
        scope.append("Jurisdictions: " + ", ".join(sorted(want)))
    if args.band:
        want = {b.lower() for b in args.band}
        out = [l for l in out if l["band"] in want]
        scope.append("Exposure band: " + ", ".join(BAND_LABEL[b] for b in BANDS if b in want))
    if args.line:
        want = {x.lower() for x in args.line}
        out = [l for l in out if any(x.lower() in want for x in l["lines"])]
        scope.append("Lines: " + ", ".join(args.line))
    if args.theme:
        want = {t.lower() for t in args.theme}
        out = [l for l in out if l["theme"] in want or l["theme_label"].lower() in want]
        scope.append("Subjects: " + ", ".join(sorted({l["theme_label"] for l in out})))
    if args.pra:
        out = [l for l in out if l["parts"]["pra"]["points"] >= 18]
        scope.append("Restricted to measures carrying a private right of action")
    if args.pending:
        out = [l for l in out if l["attaches"] and l["attaches"] > today]
        scope.append("Restricted to measures whose duties have not yet attached")
    return out, scope


# --------------------------------------------------------------------------
# Fragments
# --------------------------------------------------------------------------

def cartogram(laws, meta) -> str:
    """A static SVG grid map. Drawn rather than styled so it survives the PDF."""
    grid, names = meta["grid"], meta["state_names"]
    peak = {}
    counts = Counter()
    for l in laws:
        counts[l["jurisdiction"]] += 1
        peak[l["jurisdiction"]] = max(peak.get(l["jurisdiction"], 0), l["exposure"])
    cw, gap = 42, 3
    rows = max(r for r, _ in grid.values()) + 1
    cols = max(c for _, c in grid.values()) + 1
    w, h = cols * (cw + gap), rows * (cw + gap)
    parts = [f'<svg class="carto" viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="Peak claim exposure by state">']
    for code, (r, c) in sorted(grid.items(), key=lambda kv: (kv[1][0], kv[1][1])):
        x, y = c * (cw + gap), r * (cw + gap)
        s = step_for(peak.get(code, 0))
        fill = SEQ[s - 1] if s else "#eeeeeb"
        ink = "#ffffff" if s >= 2 else "#0a0a0a"
        parts.append(f'<rect x="{x}" y="{y}" width="{cw}" height="{cw}" rx="2" fill="{fill}"/>')
        parts.append(f'<text x="{x + cw / 2}" y="{y + cw / 2 - 1}" text-anchor="middle" '
                     f'font-size="12" font-weight="600" fill="{ink}">{code}</text>')
        if counts[code]:
            parts.append(f'<text x="{x + cw / 2}" y="{y + cw / 2 + 12}" text-anchor="middle" '
                         f'font-size="9" fill="{ink}" opacity="0.85">{counts[code]}</text>')
        elif code not in names:
            parts.append("")
    parts.append("</svg>")
    legend = "".join(
        f'<span class="key"><span class="sw" style="background:{SEQ[i]}"></span>{lab}</span>'
        for i, lab in enumerate(["Low 1–21", "Moderate 22–41", "Elevated 42–61", "Acute 62–100"]))
    return ("".join(parts)
            + f'<div class="legend"><span class="key"><span class="sw" '
              f'style="background:#eeeeeb"></span>None tracked</span>{legend}</div>')


def summary_block(laws, today) -> str:
    pra = [l for l in laws if l["parts"]["pra"]["points"] >= 18]
    acute = [l for l in laws if l["band"] == "acute"]
    pending = [l for l in laws if l["attaches"] and l["attaches"] > today]
    nocontrol = [l for l in laws if not l["controls"]]
    crim = [l for l in laws if any(f["key"] == "criminal" for f in l["flags"])]
    biggest = max(laws, key=lambda l: l["max_dollars"], default=None)
    states = {l["jurisdiction"] for l in laws}
    tiles = [
        ("Measures in scope", len(laws), f"across {len(states)} jurisdictions"),
        ("Private right of action", len(pra),
         f"{round(len(pra) / len(laws) * 100)}% — the measures that reach a liability tower"),
        ("Acute band", len(acute), "private action, aggregation and fee-shifting together"),
        ("Duties not yet attached", len(pending),
         f"next on {min((l['attaches'] for l in pending), default='—')}" if pending else "none pending"),
        ("No cure or safe harbour", len(nocontrol), "nothing a compliant insured can point to"),
        ("Criminal overlay", len(crim), "engages the conduct exclusions on the civil claim"),
    ]
    cells = "".join(
        f'<div class="tile"><span class="tl">{E(t)}</span>'
        f'<span class="tv">{E(str(v))}</span><span class="ts">{E(s)}</span></div>'
        for t, v, s in tiles)
    big = ""
    if biggest and biggest["max_dollars"]:
        big = (f'<p class="lede">The largest single sum named anywhere in scope is '
               f'<b>{money(biggest["max_dollars"])}</b> — {E(biggest["state_name"])}, '
               f'{E(biggest["title"])}.</p>')
    return f'<div class="tiles">{cells}</div>{big}'


def ledger_table(laws) -> str:
    rows = "".join(
        f'<tr><td class="mono">{E(l["jurisdiction"])}</td>'
        f'<td><b>{E(l["title"])}</b><span class="cite">{E(l["citation"])}</span></td>'
        f'<td class="num">{l["exposure"]}</td>'
        f'<td>{BAND_LABEL[l["band"]]}</td>'
        f'<td>{E(who_can_sue(l))}</td>'
        f'<td class="num">{money(l["max_dollars"]) if l["max_dollars"] else "—"}</td>'
        f'<td class="mono num">{E(l["attaches"] or "—")}</td></tr>'
        for l in laws)
    return (
        '<table class="ledger"><thead><tr><th>St</th><th>Measure</th><th>Exp.</th>'
        '<th>Band</th><th>Who can sue</th><th>Max sum</th><th>Attaches</th>'
        f'</tr></thead><tbody>{rows}</tbody></table>')


def lines_table(laws) -> str:
    agg = defaultdict(lambda: {"n": 0, "bands": Counter(), "states": set(), "sum": 0})
    for l in laws:
        for x in l["lines"]:
            a = agg[x]
            a["n"] += 1
            a["bands"][l["band"]] += 1
            a["states"].add(l["jurisdiction"])
            a["sum"] += l["exposure"]
    rows = "".join(
        f'<tr><td><b>{E(name)}</b></td><td class="num">{a["n"]}</td>'
        f'<td class="num">{len(a["states"])}</td>'
        f'<td class="num">{round(a["sum"] / a["n"])}</td>'
        f'<td class="num">{a["bands"]["acute"]}</td>'
        f'<td class="num">{a["bands"]["elevated"]}</td></tr>'
        for name, a in sorted(agg.items(), key=lambda kv: -kv[1]["n"]))
    return ('<table class="ledger"><thead><tr><th>Line of insurance</th><th>Measures</th>'
            '<th>States</th><th>Mean exp.</th><th>Acute</th><th>Elevated</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


def runway_section(laws, today) -> str:
    pending = sorted((l for l in laws if l["attaches"] and l["attaches"] > today),
                     key=lambda l: (l["attaches"], -l["exposure"]))
    if not pending:
        return '<p class="lede">No measure in scope has duties still to attach.</p>'
    groups = defaultdict(list)
    for l in pending:
        q = f'{l["attaches"][:4]} Q{(int(l["attaches"][5:7]) - 1) // 3 + 1}'
        groups[q].append(l)
    out = []
    for q in sorted(groups):
        g = groups[q]
        n_pra = sum(1 for l in g if l["parts"]["pra"]["points"] >= 18)
        rows = "".join(
            f'<tr><td class="mono">{E(l["attaches"])}</td><td class="mono">{E(l["jurisdiction"])}</td>'
            f'<td><b>{E(l["title"])}</b><span class="cite">{E(l["theme_label"])} · '
            f'{E(", ".join(l["lines"][:2]))}</span></td>'
            f'<td class="num">{l["exposure"]}</td><td>{BAND_LABEL[l["band"]]}</td></tr>'
            for l in g)
        out.append(
            f'<h3 class="qh">{q} <span class="qn">{len(g)} measure'
            f'{"" if len(g) == 1 else "s"} · {n_pra} with a private action</span></h3>'
            f'<table class="ledger"><tbody>{rows}</tbody></table>')
    return "".join(out)


def brief(l: dict) -> str:
    """One measure, spelled out. The web drawer, flattened for the page."""
    parts = "".join(
        f'<tr><td>{E(c["label"])}</td><td class="num">{c["points"]}/{c["max"]}</td>'
        f'<td class="note">{E(c["note"])}</td></tr>'
        for c in l["parts"].values())
    flags = "".join(
        f'<li><b>{E(f["label"])}.</b> {E(f["why"])} <span class="cite">{E(f["cite"])}</span></li>'
        for f in l["flags"]) or "<li>None identified on the face of the statute.</li>"
    ctrls = "".join(
        f'<li><b>{E(c["label"])}.</b> <span class="cite">{E(c["cite"])}</span></li>'
        for c in l["controls"]) or (
        "<li>None — no cure period, no safe harbour, no size threshold. There is nothing "
        "for a compliant insured to point to between breach and complaint.</li>")
    unconf = (f'<p class="warn"><b>Unverified.</b> These columns were not confirmed against '
              f'the enrolled text on the last pass: {E(", ".join(l["unconfirmed"]))}. '
              f'The derived score is provisional to that extent.</p>') if l["unconfirmed"] else ""

    def kv(label, value):
        return f'<tr><td>{E(label)}</td><td colspan="2">{E(value)}</td></tr>' if value else ""

    return f"""
<section class="brief">
  <h3>{E(l["state_name"])} — {E(l["title"])}</h3>
  <p class="cite head">{E(l["citation"])}</p>
  <p class="chips"><span class="chip band-{l["band"]}">{BAND_LABEL[l["band"]]} · exposure
    {l["exposure"]}/100</span><span class="chip">{E(l["theme_label"])}</span>
    {"".join(f'<span class="chip line">{E(x)}</span>' for x in l["lines"])}</p>
  <p>{E(l["summary"])}</p>
  {unconf}
  <table class="kv">
    <tr><td>Who can sue</td><td colspan="2">{E(who_can_sue(l))} — <span class="cite">index column
      reads: {E(l["private_suit_raw"])}</span></td></tr>
    {kv("Damages", l["statutory_damages_raw"])}
    {kv("Fee-shifting", l["fee_shifting_raw"])}
    {kv("Class treatment", l["class_raw"])}
    {kv("Enforcer", l["enforcer"])}
    {kv("Who is regulated", l["who_regulated"])}
    {kv("Regulatory hook", l["hook"])}
    {kv("Carve-outs", l["carve_outs"])}
    {kv("Status", l["status_raw"])}
    {kv("Effective", l["effective_raw"])}
    {kv("Operative", l["operative_raw"])}
    {kv("Retroactivity", l["retroactivity"])}
    {kv("Limitations", l["limitations"])}
    <tr><td>Territorial reach</td><td colspan="2">{E(l["reach"]["level"])}
      {E(" — " + l["reach"]["notes"][0] if l["reach"]["notes"] else "")}</td></tr>
  </table>
  <h4>How the {l["exposure"]} is made up</h4>
  <table class="kv parts">{parts}</table>
  <h4>Insurability friction</h4><ul class="tight">{flags}</ul>
  <h4>Controls available to the insured</h4><ul class="tight">{ctrls}</ul>
  <p class="cite">Primary source: {E(l["url"])}<br>Last checked {E(l["last_checked"])}.</p>
</section>"""


CSS = """
@page { size: Letter; margin: 0.75in 0.7in; }
:root {
  --ink:#111; --muted:#5a5a5a; --faint:#8a8a8a; --rule:#d8d8d5;
  --accent:#00753a; --warn:#8a5f10; --surface:#f7f7f5;
}
* { box-sizing: border-box; }
body {
  margin:0; color:var(--ink); background:#fff;
  font-family:"IBM Plex Sans",-apple-system,"Segoe UI",system-ui,sans-serif;
  font-size:9.6pt; line-height:1.5;
}
h1,h2,h3,h4 { font-family:"Newsreader",Georgia,"Times New Roman",serif; font-weight:600; }
.mono,.cite.head { font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace; }
.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
a { color:inherit; text-decoration:none; }

/* cover */
.cover { height:9.1in; display:flex; flex-direction:column; break-after:page; }
.rule-top { border-top:3px solid var(--accent); padding-top:10px; }
.mark { font-family:"Archivo",sans-serif; font-weight:800; font-size:15pt;
        letter-spacing:.05em; text-transform:uppercase; }
.mark span { color:var(--muted); font-weight:600; }
.wg { font-family:"Newsreader",serif; font-style:italic; color:var(--muted); font-size:11pt; }
.cover h1 { font-size:31pt; line-height:1.08; letter-spacing:-.015em; margin:auto 0 0;
            max-width:16.5cm; font-weight:400; text-wrap:balance; }
.cover .sub { font-size:12pt; color:var(--muted); margin:14px 0 0; max-width:14cm;
              font-family:"Newsreader",serif; }
.cover .meta { margin-top:26px; padding-top:14px; border-top:1px solid var(--rule);
               display:flex; gap:40px; font-size:9pt; color:var(--muted); }
.cover .meta b { display:block; color:var(--ink); font-size:11pt; font-weight:600; }
.scope { margin-top:20px; padding:12px 14px; background:var(--surface);
         border-left:3px solid var(--accent); font-size:9pt; }
.scope b { display:block; text-transform:uppercase; letter-spacing:.08em; font-size:7.5pt;
           color:var(--accent); margin-bottom:5px; }
.scope ul { margin:0; padding-left:16px; }
.disclaim { margin-top:14px; padding:12px 14px; border:1px solid #e3d5b4;
            background:#fbf7ec; font-size:8.4pt; line-height:1.55; }
.disclaim b { color:var(--warn); text-transform:uppercase; letter-spacing:.07em; font-size:7.5pt; }

/* sections */
section.major { break-before:page; }
h2 { font-size:19pt; margin:0 0 4px; font-weight:400; letter-spacing:-.01em; }
.eyebrow { text-transform:uppercase; letter-spacing:.12em; font-size:7.5pt; color:var(--accent);
           font-weight:600; }
.lede { color:var(--muted); max-width:16cm; margin:6px 0 16px; }
h3 { font-size:11.5pt; margin:16px 0 4px; }
h4 { font-family:"IBM Plex Sans",sans-serif; font-size:7.8pt; text-transform:uppercase;
     letter-spacing:.08em; color:var(--muted); margin:12px 0 4px;
     padding-bottom:3px; border-bottom:1px solid var(--rule); }

/* tiles */
.tiles { display:grid; grid-template-columns:repeat(3,1fr); gap:1px; background:var(--rule);
         border:1px solid var(--rule); margin-bottom:14px; }
.tile { background:#fff; padding:10px 12px; }
.tl { display:block; font-size:7.3pt; text-transform:uppercase; letter-spacing:.08em;
      color:var(--muted); }
.tv { display:block; font-family:"Archivo",sans-serif; font-size:21pt; font-weight:700;
      line-height:1.1; margin-top:4px; font-variant-numeric:tabular-nums; }
.ts { display:block; font-size:7.6pt; color:var(--faint); line-height:1.4; margin-top:3px; }

/* map */
.carto { width:100%; max-width:15.5cm; height:auto; display:block; margin:6px 0 10px; }
.legend { display:flex; flex-wrap:wrap; gap:14px; font-size:8pt; color:var(--muted); }
.key { display:flex; align-items:center; gap:5px; }
.sw { width:11px; height:11px; border:1px solid rgba(0,0,0,.12); flex:none; }

/* tables */
table { width:100%; border-collapse:collapse; font-size:8.4pt; }
table.ledger th { text-align:left; font-size:7.2pt; text-transform:uppercase;
  letter-spacing:.07em; color:var(--muted); border-bottom:1.5px solid #b8b8b4;
  padding:5px 6px; }
table.ledger td { padding:4px 6px; border-bottom:1px solid var(--rule); vertical-align:top; }
table.ledger thead { display:table-header-group; }
table.ledger tr { break-inside:avoid; }
.cite { display:block; color:var(--faint); font-size:7.5pt; line-height:1.35; }
.cite.head { color:var(--muted); font-size:8pt; margin:2px 0 8px; }
.qh { font-size:10pt; margin:14px 0 3px; }
.qn { font-family:"IBM Plex Sans",sans-serif; font-size:8pt; color:var(--faint);
      font-weight:400; letter-spacing:0; }

/* per-measure briefs */
.brief { break-inside:avoid; padding:12px 0 10px; border-bottom:1px solid var(--rule); }
.brief h3 { margin:0; font-size:12pt; }
.chips { margin:6px 0 8px; display:flex; flex-wrap:wrap; gap:4px; }
.chip { font-size:7.2pt; border:1px solid var(--rule); padding:1px 6px; color:var(--muted);
        text-transform:uppercase; letter-spacing:.05em; }
.chip.line { border-color:#a9d5bd; color:var(--accent); }
.chip.band-acute { border-color:#d9a3a3; color:#8f2626; }
.chip.band-elevated { border-color:#dfc188; color:var(--warn); }
.chip.band-moderate { border-color:#a8c2d4; color:#245277; }
table.kv td { padding:2.5px 6px 2.5px 0; border-bottom:1px solid #efefec; vertical-align:top; }
table.kv td:first-child { width:4.4cm; color:var(--muted); font-size:7.6pt;
  text-transform:uppercase; letter-spacing:.04em; }
table.parts td:nth-child(2) { width:1.6cm; }
.note { color:var(--faint); font-size:7.8pt; }
ul.tight { margin:2px 0; padding-left:15px; font-size:8.6pt; }
ul.tight li { margin-bottom:3px; }
.warn { background:#fbf7ec; border-left:3px solid #d9b45f; padding:7px 10px; font-size:8.2pt;
        margin:8px 0; }
.toc { font-size:9.4pt; }
.toc li { margin-bottom:3px; }
"""


def build(laws, meta, scope, args, today) -> str:
    laws = sorted(laws, key=lambda l: (-l["exposure"], l["jurisdiction"], l["title"]))
    by_state = defaultdict(list)
    for l in laws:
        by_state[l["jurisdiction"]].append(l)
    scope_html = (
        f'<div class="scope"><b>Scope of this brief</b><ul>'
        + "".join(f"<li>{E(s)}</li>" for s in scope)
        + f'<li>{len(laws)} of {meta["count"]} measures in the full index.</li></ul></div>'
        if scope else
        f'<div class="scope"><b>Scope of this brief</b>'
        f'<p style="margin:0">The complete index — all {len(laws)} measures, no filter '
        f'applied.</p></div>')

    state_sections = "".join(
        f'<h3 class="qh">{E(meta["state_names"].get(code, code))} '
        f'<span class="qn">{len(rows)} measure{"" if len(rows) == 1 else "s"} · '
        f'peak exposure {max(l["exposure"] for l in rows)}</span></h3>'
        + "".join(brief(l) for l in rows)
        for code, rows in sorted(by_state.items(),
                                 key=lambda kv: (-max(l["exposure"] for l in kv[1]), kv[0])))

    method_rows = "".join(
        f'<tr><td><b>{E(p["label"])}</b></td><td class="num">{p["max"]}</td>'
        f'<td class="note">{E(n)}</td></tr>'
        for p, n in zip(meta["exposure_parts"], [
            "The single strongest predictor that a statute produces a tendered claim. A "
            "regulator-enforced duty rarely reaches a liability tower; a private right of "
            "action does.",
            "Whether one incident becomes one claim or ten thousand. Express class language, "
            "per-violation accrual and stacking all count. Silence still scores where a private "
            "action exists, because silence leaves the ordinary class rules in place.",
            "Log-scaled on the largest sum the statute names, so a $1,000 floor and a "
            "$1,000,000 ceiling are not treated as the same fact.",
            "One-way fee-shifting changes the economics of a small claim, and is the reason "
            "nuisance-value suits get filed.",
            "Scored on absence. A statute with no cure period and no safe harbour gives a "
            "defendant nothing to do between the breach and the complaint.",
            "Regulatory and criminal attention drives defence cost and reputational loss even "
            "where no private plaintiff appears.",
        ]))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>State AI Law Exposure Brief — {today}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;800&family=Newsreader:opsz,wght@6..72,400;6..72,600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>

<div class="cover">
  <div class="rule-top">
    <div class="mark">Atheria<span> Law</span></div>
    <div class="wg">Artificial Intelligence Working Group</div>
  </div>
  <h1>State AI law: where the exposure is, and which policy answers for it.</h1>
  <p class="sub">An index of state artificial-intelligence statutes, scored by the features that
    decide whether a measure produces a claim an insurer is asked to respond to.</p>
  <div class="meta">
    <div>Measures in scope<b>{len(laws)}</b></div>
    <div>Jurisdictions<b>{len({l["jurisdiction"] for l in laws})}</b></div>
    <div>Research current to<b>{E(meta.get("last_checked") or today)}</b></div>
    <div>Brief generated<b>{today}</b></div>
  </div>
  {scope_html}
  <div class="disclaim"><b>Verify first.</b><br>
    Compiled from primary legislative sources; every measure carries its citation and source
    URL. The scoring is attorney work-product for triage — it ranks statutes by the features
    that tend to generate insured claims. It is not a coverage opinion, not underwriting
    advice, and not a substitute for reading the policy and the statute. Fields marked
    <i>unverified</i> have not been checked against the enrolled text.</div>
</div>

<section class="major">
  <span class="eyebrow">Contents</span>
  <h2>What is in this brief</h2>
  <ol class="toc">
    <li><b>The national picture</b> — peak exposure by state, and the headline figures.</li>
    <li><b>Coverage lines</b> — which policies this body of law lands on.</li>
    <li><b>Runway</b> — duties that have not yet attached, by quarter.</li>
    <li><b>The ledger</b> — every measure in scope, highest exposure first.</li>
    <li><b>Measure briefs</b> — each statute in full, grouped by state.</li>
    <li><b>Method</b> — how the score is built and what it cannot tell you.</li>
  </ol>
</section>

<section class="major">
  <span class="eyebrow">1 · The national picture</span>
  <h2>Peak claim exposure by state</h2>
  <p class="lede">Each state is shaded by its single highest-scoring measure — the worst case an
    insured operating there faces — rather than an average, which would let a volume of low-risk
    disclosure duties dilute one live private right of action. The figure beneath each
    abbreviation is the number of measures in scope for that state.</p>
  {cartogram(laws, meta)}
  {summary_block(laws, today)}
</section>

<section class="major">
  <span class="eyebrow">2 · Coverage lines</span>
  <h2>Which of your policies this lands on</h2>
  <p class="lede">Each measure is mapped to the lines most plausibly asked to respond, derived
    from the harm the statute creates and who it regulates. This is a checklist for a coverage
    review, not a determination — the wording governs.</p>
  {lines_table(laws)}
</section>

<section class="major">
  <span class="eyebrow">3 · Runway</span>
  <h2>Duties that attach inside the period you are pricing</h2>
  <p class="lede">Measures are placed on the quarter their duties first bite — the operative date
    where it differs from the effective date, because that is when an exposure actually starts
    running.</p>
  {runway_section(laws, today)}
</section>

<section class="major">
  <span class="eyebrow">4 · The ledger</span>
  <h2>Every measure in scope</h2>
  <p class="lede">Sorted by claim exposure, highest first. Full detail for each measure follows
    in section 5, grouped by state.</p>
  {ledger_table(laws)}
</section>

<section class="major">
  <span class="eyebrow">5 · Measure briefs</span>
  <h2>Each statute in full</h2>
  <p class="lede">Grouped by state, states ordered by peak exposure. Every score is shown
    component by component so the derivation can be checked against the statute.</p>
  {state_sections}
</section>

<section class="major">
  <span class="eyebrow">6 · Method</span>
  <h2>How the score is built</h2>
  <p class="lede">Claim exposure is a 0–100 sum of six components, weighted by how strongly each
    predicts that a statute produces a claim tendered to an insurer. Nothing is inferred by a
    model: every component is arithmetic over a column an attorney filled in from the primary
    source.</p>
  <table class="kv parts">{method_rows}</table>
  <h4>What it cannot tell you</h4>
  <ul class="tight">
    <li><b>Silence is not safety.</b> A statute silent on class treatment leaves the ordinary
      class rules in place. Illinois' BIPA says nothing about classes and is the largest source
      of AI-adjacent class exposure in the country.</li>
    <li><b>The score has no view on frequency.</b> It measures what the statute permits, not how
      often anyone has used it. A high-scoring statute nobody has sued on still scores high.</li>
    <li><b>Line mapping is a checklist.</b> Derived from the subject of the statute, not from any
      policy. The wording, the retro date and the exclusions govern.</li>
    <li><b>Dates are the researcher's reading.</b> Where a cell records a staged commencement the
      earliest date is used. Rows marked unverified have not been checked against the enrolled
      text.</li>
    <li><b>No federal layer.</b> State measures only. Federal preemption is recorded where a
      statute addresses it and is otherwise an open question.</li>
  </ul>
  <p class="cite" style="margin-top:18px">Source file {E(meta.get("source_file", "INDEX.csv"))},
    checksum {E(meta.get("source_sha256", "—"))}. Generated {today} by
    scripts/export_doc.py from data/laws.json.</p>
</section>
</body></html>"""


def rel(p: Path) -> str:
    """Repo-relative where that reads better, absolute where it does not."""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def render_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Best-effort PDF. The browser is the supported path; this just saves a step."""
    script = f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto('file://{html_path}', {{ waitUntil: 'networkidle' }});
  await p.pdf({{
    path: '{pdf_path}', format: 'Letter', printBackground: true,
    displayHeaderFooter: true, margin: {{ top: '0.75in', bottom: '0.8in', left: '0', right: '0' }},
    headerTemplate: '<div style="font:8px \\'IBM Plex Sans\\',sans-serif;color:#8a8a8a;'
      + 'width:100%;padding:0 0.7in;display:flex;justify-content:space-between">'
      + '<span>Atheria Law · AI Working Group</span>'
      + '<span>State AI Law Exposure Brief</span></div>',
    footerTemplate: '<div style="font:8px \\'IBM Plex Sans\\',sans-serif;color:#8a8a8a;'
      + 'width:100%;padding:0 0.7in;display:flex;justify-content:space-between">'
      + '<span>Attorney work-product — verify against the primary source before use.</span>'
      + '<span class="pageNumber"></span></div>',
  }});
  await b.close();
}})();
"""
    tmp = html_path.with_suffix(".render.js")
    tmp.write_text(script)
    try:
        subprocess.run(["node", str(tmp)], check=True, capture_output=True, text=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        print(f"PDF step skipped — {detail.strip().splitlines()[-1] if detail.strip() else exc}",
              file=sys.stderr)
        print("Open the HTML and print to PDF from the browser instead.", file=sys.stderr)
        return False
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=Path, default=JSON_IN)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--state", action="append", help="two-letter code; repeat to widen")
    ap.add_argument("--band", action="append", choices=BANDS, help="repeat to widen")
    ap.add_argument("--line", action="append", help="line of insurance, exact label")
    ap.add_argument("--theme", action="append", help="subject key or label")
    ap.add_argument("--pra", action="store_true", help="only measures with a private action")
    ap.add_argument("--pending", action="store_true", help="only duties not yet attached")
    ap.add_argument("--pdf", action="store_true", help="also render a PDF, if it can")
    ap.add_argument("--embed-fonts", action="store_true",
                    help="inline the web fonts so the brief renders identically "
                         "with no network (adds roughly 230 KB)")
    ap.add_argument("--today", default=None)
    args = ap.parse_args()

    if not args.json.exists():
        print(f"no data at {args.json} — run scripts/build_laws.py first", file=sys.stderr)
        return 1
    payload = json.loads(args.json.read_text(encoding="utf-8"))
    today = args.today or payload["meta"].get("generated") or date.today().isoformat()

    laws, scope = select(payload["laws"], args, today)
    if not laws:
        print("No measure matches those filters.", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(exist_ok=True)
    stem = "laws-brief"
    if args.state:
        stem += "-" + "".join(sorted(s.upper() for s in args.state))
    if args.band:
        stem += "-" + "".join(sorted(args.band))
    out = (args.out or OUT_DIR / f"{stem}.html").resolve()
    doc = build(laws, payload["meta"], scope, args, today)

    if args.embed_fonts:
        # A brief that goes in a claim file has to look the same on a machine
        # with no network as it did on the one it was reviewed on.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import embed_fonts
            doc, raw, _, _ = embed_fonts.embed(doc, embed_fonts.DEFAULT_SUBSETS)
            print(f"  embedded {raw / 1024:.0f} KB of fonts — renders offline")
        except Exception as exc:  # network down, or Google changed the endpoint
            print(f"  font embedding skipped: {exc}", file=sys.stderr)

    out.write_text(doc, encoding="utf-8")

    states = len({l["jurisdiction"] for l in laws})
    print(f"{len(laws)} measures across {states} jurisdictions -> {rel(out)}")
    for s in scope:
        print(f"  scope: {s}")
    print("  open it and print to PDF from the browser (Chrome paginates it correctly)")

    if args.pdf and render_pdf(out.resolve(), out.with_suffix(".pdf").resolve()):
        pdf = out.with_suffix(".pdf")
        print(f"  wrote {rel(pdf)} ({pdf.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
