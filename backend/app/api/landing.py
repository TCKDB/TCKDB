"""The human-readable landing page served at ``/``.

Everything else this application serves is JSON for a machine. This one
page is for a person -- specifically the person who was handed the base
URL in a paper or a README, typed it, and until now received a bare
``404 application/json`` blob with no indication that anything was
there at all.

The page's job is to *lead somewhere*. An earlier version described the
database in six hundred words and ended in three links, which is a dead
end dressed as an introduction. The hero is now a search box that
queries this deployment's own public read API and renders the records
it gets back, so the first thing a visitor does is get real data out.

Leading somewhere is also a claim about where. Every product a result
has expands *in place* into the few fields that say what is in it,
rather than navigating to the nested JSON document the endpoint
answers with -- correct for a program, a wall of keys for a person who
clicked a link on a landing page. The raw document is still one click
away from inside each panel, under a label that says it is raw JSON.
Nothing on this page takes a reader somewhere they did not expect.

Design constraints, in the order they constrain things:

* **Self-contained.** No CDN, no external stylesheet, no web font, no
  remote image, no analytics. Every byte the browser renders arrives in
  this one response, so the page works on a locked-down network and
  from an archived copy. The one script on the page is inline and talks
  only to this same origin, under ``/api/v1``; there is no subresource
  to fetch and no third party to trust.
* **Works without JavaScript.** The search form is a real
  ``<form method="get">`` whose action is the API endpoint itself, so
  with scripting off it still searches -- the browser simply lands on
  the JSON instead of a rendered list. The example queries are real
  links to the same endpoint for the same reason. JavaScript upgrades
  that form to an in-page search; it is not what makes it work. The
  identifier picker is the one control that genuinely cannot function
  without script (an HTML form cannot rename its own field), so it
  ships ``disabled`` and a ``<noscript>`` block offers a plain form per
  identifier instead of silently searching the wrong field.
* **Factual.** It states no record count, no uptime, no funder, no
  institution and no contributor beyond the authors already named in
  the repository's ``CITATION.cff``. Every number a visitor sees in the
  results came back from a live request they just made -- including the
  review counts, which are displayed rather than apologised for.
  Under-review records are public here and are served; the label says
  how far a record has been checked, and that is information, not a
  disclaimer.

The worked example lower down the page is not illustrative fiction. The
frequency list, the geometry, the code and the sentence are the real
output of :mod:`app.services.frequency_geometry_linearity` for that
input, transcribed from an actual run; ``tests/api/test_landing_page.py``
re-runs the check and fails if the page and the checker ever disagree.
An invented example on a page whose entire argument is "this system
does not invent things" would be self-refuting.

That example has to *show* its defect, not assert it. An earlier
version printed the three deposited frequencies and a sentence saying
a mode was missing, which is only legible to a reader who already
knows carbon dioxide's spectrum is 667, 667, 1333, 2349 -- the whole
point being the 667 that appears twice. Nothing on screen was wrong to
look at. The panel now prints the deposit above the spectrum the
molecule has, one frequency per column, with the absent slot drawn as
an empty cell directly above the frequency that belongs in it. The
prose is then an explanation of something the reader has already seen
rather than a substitute for seeing it.

The markup lives in this module as a string rather than as a sibling
``.html`` file on purpose: ``backend/pyproject.toml`` declares no
``package-data``, so a non-editable wheel build would ship the module
and silently drop the file, and the failure would appear only in the
one deployment shape nobody tests locally.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.config import settings

#: Published documentation site, built from ``docs/`` by
#: ``.github/workflows/docs.yml``.
DOCS_URL = "https://tckdb.github.io/TCKDB/"

#: Canonical source repository.
REPO_URL = "https://github.com/TCKDB/TCKDB"

#: Path the ReDoc API reference is registered on when this deployment
#: serves one. FastAPI's default, kept as a constant so the page and the
#: application factory cannot drift apart.
REDOC_PATH = "/redoc"

#: The read endpoint the hero searches. Public: no key, no account, and
#: same-origin from this page, so the browser needs neither CORS nor a
#: proxy to reach it.
SPECIES_SEARCH_PATH = "/api/v1/scientific/species/search"

#: The identifiers the picker offers, as ``(query parameter, label)``.
#: An explicit choice rather than a guess: ``O`` is a valid formula
#: *and* a valid SMILES meaning two different things, and guessing
#: wrong returns a confusing empty result instead of an answer.
SEARCH_FIELDS = (
    ("formula", "formula"),
    ("smiles", "SMILES"),
    ("inchi_key", "InChIKey"),
)

#: What the box is pre-filled with, and the query the page runs on load
#: so that nobody's first sight of the search is an empty box.
SEED_FIELD = "formula"
SEED_VALUE = "CH3"

#: Offered under the box and again in the empty state. Each is a real
#: link to the API, so they work with scripting disabled.
SEARCH_EXAMPLES = (
    ("formula", "CH3"),
    ("formula", "H2O"),
    ("smiles", "[OH]"),
)

#: Placeholder text per identifier, shown once the picker is live. Each
#: is a real value in that field's syntax, so the hint doubles as a
#: worked example of what the field expects.
SEARCH_PLACEHOLDERS = (
    ("formula", "CH3"),
    ("smiles", "[OH]"),
    ("inchi_key", "WCYWZMWISLQXQU-UHFFFAOYSA-N"),
)

#: The geometry shown in the worked example, and the input the test
#: feeds back through the real checker. Carbon dioxide, collinear along
#: z at a 1.162 A bond length.
#:
#: The first two lines are the XYZ format's own header -- an atom count
#: and a comment. They belong to the file the checker parses, so they
#: live here; the panel renders :func:`hero_atom_lines` instead. A bare
#: ``3`` alone above three coordinate rows is unremarkable inside a
#: file and reads on a web page as a stray number.
HERO_XYZ = """3
carbon dioxide
C   0.000000   0.000000   0.000000
O   0.000000   0.000000   1.162000
O   0.000000   0.000000  -1.162000"""

#: The vibrational spectrum carbon dioxide actually has. Linear, three
#: atoms, so 3N-5 = 4 vibrations: the symmetric stretch (~1333), the
#: asymmetric stretch (~2349), and the bend (~667), which is **doubly
#: degenerate** -- one bend, in two perpendicular planes, at one
#: wavenumber -- and is therefore counted twice.
HERO_TRUE_FREQUENCIES = (667.0, 667.0, 1333.0, 2349.0)

#: The frequency list actually deposited alongside :data:`HERO_XYZ`.
#:
#: Derived from the true spectrum by performing exactly the mistake the
#: example is about -- de-duplicating equal wavenumbers, which drops one
#: component of the degenerate pair and lands the count on 3N-6, the
#: count for a *bent* three-atom molecule. Derived rather than typed
#: because the panel's whole claim is that one of these four modes is
#: absent from the deposit: computing the deposit from the spectrum
#: makes that claim a property of the module instead of a coincidence
#: between two hand-maintained tuples.
HERO_FREQUENCIES = tuple(dict.fromkeys(HERO_TRUE_FREQUENCIES))

#: The code the checker attaches to that deposit. Advisory: the record
#: is accepted and annotated, not refused.
HERO_CODE = "freq_list_bent_mode_count_for_linear_geometry"

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TCKDB &mdash; Thermochemical &amp; Kinetics Database</title>
<meta name="description"
      content="TCKDB is a provenance-tracked database and HTTP API for
               computational chemistry data. Search it from the landing
               page; every result carries its review state.">
<style>
/*
 * Palette: molecular-orbital phase convention. Two signed colours,
 * because signed quantities carry meaning in this field -- orbital
 * phase, real versus imaginary frequencies, accepted versus flagged.
 * Indigo is the positive lobe and reads as "accepted / real";
 * orange-red is the negative lobe and appears essentially only on the
 * verdict and on a rejected review state.
 *
 * --phase-neg-text is the same hue darkened until it clears WCAG AA
 * against --paper (4.9:1; the display value is 4.0:1, which is a
 * large-text pass only). The relationship is preserved: one colour for
 * the mark, one for the same colour set as prose.
 */
:root {
  color-scheme: light dark;
  --paper: #eef1f5;
  --surface: #ffffff;
  --ink: #10131c;
  --muted: #5c6674;
  --rule: #cbd3de;
  --phase-pos: #2b4fa8;
  --phase-neg: #cf4a26;
  --phase-neg-text: #b8401e;
  --data-bg: #e4e9f0;

  --mono: ui-monospace, "SF Mono", "Cascadia Mono", "JetBrains Mono", Menlo,
          Consolas, monospace;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial,
          sans-serif;

  /* Type scale, 1.25 from a 17px body, with deliberate steps. */
  --fs-display: clamp(2rem, 1.3rem + 3.1vw, 3.25rem);
  --fs-lede: clamp(1.0625rem, 1rem + 0.35vw, 1.1875rem);
  --fs-section: 1.3125rem;
  --fs-body: 1.0625rem;
  --fs-data: 0.8125rem;
  --fs-label: 0.6875rem;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper: #0f121a;
    --surface: #171b26;
    --ink: #e6eaf2;
    --muted: #99a3b4;
    --rule: #2b323f;
    --phase-pos: #93b0f5;
    --phase-neg: #ef8a63;
    --phase-neg-text: #ef8a63;
    --data-bg: #10141d;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  padding: 0 1.25rem 4.5rem;
  background: var(--paper);
  color: var(--ink);
  font-family: var(--sans);
  font-size: var(--fs-body);
  line-height: 1.62;
}
header, main, footer { max-width: 52rem; margin-inline: auto; }
a { color: var(--phase-pos); text-underline-offset: 0.15em; }
a:hover { text-decoration-thickness: 2px; }
:focus-visible {
  outline: 3px solid var(--phase-neg);
  outline-offset: 3px;
  border-radius: 2px;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
.skip {
  position: absolute;
  left: -9999px;
  background: var(--surface);
  color: var(--ink);
  padding: 0.6rem 1rem;
  border: 1px solid var(--ink);
  font-family: var(--mono);
}
.skip:focus { left: 0.75rem; top: 0.75rem; z-index: 10; }

/* ---- header ---- */
header { padding: 3rem 0 0; }
h1 { margin: 0; font-weight: 400; }
.wordmark {
  display: block;
  font-family: var(--mono);
  font-size: var(--fs-display);
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1;
}
.fullname {
  display: block;
  margin-top: 0.5rem;
  font-size: var(--fs-lede);
  font-weight: 400;
  color: var(--muted);
}
.lede {
  margin: 1.25rem 0 0;
  max-width: 40rem;
  font-size: var(--fs-lede);
}
nav.actions { margin-top: 1.25rem; }
nav.actions ul {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.button {
  display: inline-block;
  padding: 0.5rem 1rem;
  font-family: var(--mono);
  font-size: var(--fs-data);
  font-weight: 700;
  letter-spacing: -0.01em;
  text-decoration: none;
  border: 1.5px solid var(--phase-pos);
}
.button.primary { background: var(--phase-pos); color: var(--surface); }
.button.secondary { background: transparent; color: var(--phase-pos); }

/* ---- sections ---- */
section { margin-top: 3.25rem; }
section.hero { margin-top: 2.5rem; }
h2 {
  font-family: var(--mono);
  font-size: var(--fs-section);
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 0.6rem;
}
h3 {
  font-family: var(--mono);
  font-size: var(--fs-body);
  font-weight: 700;
  letter-spacing: -0.01em;
  margin: 1.4rem 0 0.35rem;
}
p { margin: 0.75rem 0; max-width: 38rem; }
.note { color: var(--muted); font-size: 0.9375rem; }

/* ---- search ---- */
.search { margin: 1rem 0 0; }
.search-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: stretch;
}
.search select,
.search input,
.search button {
  font-family: var(--mono);
  font-size: var(--fs-data);
  padding: 0.6rem 0.7rem;
  border: 1.5px solid var(--rule);
  background: var(--surface);
  color: var(--ink);
  line-height: 1.3;
  min-height: 2.75rem;
}
.search select { flex: 0 0 auto; }
.search select:disabled { color: var(--muted); }
.search input {
  flex: 1 1 11rem;
  min-width: 0;
  border-color: var(--phase-pos);
}
.search button {
  flex: 0 0 auto;
  cursor: pointer;
  font-weight: 700;
  background: var(--phase-pos);
  color: var(--surface);
  border-color: var(--phase-pos);
}
.examples {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem;
  margin: 0.7rem 0 0;
  max-width: none;
  font-size: var(--fs-data);
  color: var(--muted);
}
.chip {
  font-family: var(--mono);
  font-size: var(--fs-data);
  text-decoration: none;
  border: 1px solid var(--rule);
  background: var(--surface);
  color: var(--phase-pos);
  padding: 0.2rem 0.5rem;
  overflow-wrap: anywhere;
}
.chip:hover { border-color: var(--phase-pos); }
noscript { display: block; margin-top: 1rem; }
.fallback {
  border: 1px solid var(--rule);
  padding: 0.9rem 1rem;
  background: var(--surface);
}
.fallback p { margin: 0 0 0.6rem; font-size: 0.9375rem; }
.fallback form { display: inline-flex; gap: 0.35rem; margin: 0 0.5rem 0.4rem 0; }
.fallback input, .fallback button {
  font-family: var(--mono);
  font-size: var(--fs-data);
  padding: 0.4rem 0.55rem;
  border: 1px solid var(--rule);
  background: var(--paper);
  color: var(--ink);
}

/* ---- results ---- */
.results { margin-top: 1.25rem; }
.results-status {
  font-family: var(--mono);
  font-size: var(--fs-data);
  color: var(--muted);
  margin: 0;
}
.results-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  margin: 0 0 0.75rem;
  max-width: none;
  font-family: var(--mono);
  font-size: var(--fs-data);
  color: var(--muted);
}
.results-count { color: var(--ink); font-weight: 700; }
.record {
  border: 1px solid var(--rule);
  background: var(--surface);
  padding: 0.9rem 1rem 1rem;
  margin-top: 0.6rem;
}
.record-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem 0.75rem;
}
.smiles {
  font-family: var(--mono);
  font-size: 1.0625rem;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.record-meta {
  font-family: var(--mono);
  font-size: var(--fs-data);
  color: var(--muted);
  overflow-wrap: anywhere;
}
.record-refs {
  margin: 0.5rem 0 0;
  font-family: var(--mono);
  font-size: var(--fs-data);
  color: var(--muted);
  overflow-wrap: anywhere;
  max-width: none;
}
.entry {
  margin-top: 0.8rem;
  padding-top: 0.8rem;
  border-top: 1px solid var(--rule);
}
.entry-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.75rem;
}
.entry-kind {
  font-family: var(--mono);
  font-size: var(--fs-data);
  color: var(--muted);
}
.entry-links {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin-top: 0.6rem;
}
.pill {
  font-family: var(--mono);
  font-size: var(--fs-label);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  text-decoration: none;
  border: 1px solid var(--rule);
  color: var(--phase-pos);
  background: var(--paper);
  padding: 0.25rem 0.5rem;
}
.pill:hover { border-color: var(--phase-pos); }
.pill-none { color: var(--muted); }
/*
 * The review state, on every record. Under review is a normal, honest
 * state -- most of what is served is in it -- so it is drawn as
 * information: the same neutral chip as everything else, distinguished
 * from approved by a hollow rather than filled dot, and always carrying
 * a word so the state never depends on colour alone. Only deprecated
 * and rejected borrow the negative phase colour.
 */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-family: var(--mono);
  font-size: var(--fs-data);
  border: 1px solid var(--rule);
  background: var(--data-bg);
  color: var(--ink);
  padding: 0.15rem 0.5rem;
  white-space: nowrap;
}
.badge .dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: 50%;
  border: 1.5px solid var(--phase-pos);
  background: transparent;
}
.badge-approved .dot { background: var(--phase-pos); }
.badge-not_reviewed .dot { border-color: var(--muted); }
.badge-deprecated .dot,
.badge-rejected .dot {
  border-color: var(--phase-neg);
  background: var(--phase-neg);
}
/*
 * A product's own records, rendered where the reader already is.
 *
 * Following one of these pills used to navigate to a nested JSON
 * document: the right answer for a program, and an unreadable one for
 * a person who clicked a link on a landing page. The pill is now a
 * disclosure that expands in place and shows the few fields that
 * answer *what is in here*, and every route to the raw document is
 * labelled as one, so nobody arrives at JSON by surprise.
 */
button.pill {
  cursor: pointer;
  line-height: inherit;
}
.pill[aria-expanded="true"] {
  border-color: var(--phase-pos);
  background: var(--surface);
}
.pill[aria-expanded]::after { content: " +"; }
.pill[aria-expanded="true"]::after { content: " -"; }
.entry-details:empty { display: none; }
.detail {
  margin-top: 0.7rem;
  border: 1px solid var(--rule);
  border-left: 3px solid var(--phase-pos);
  background: var(--paper);
  padding: 0.75rem 0.9rem;
}
.detail-count {
  margin: 0;
  max-width: none;
  font-family: var(--mono);
  font-size: var(--fs-data);
  font-weight: 700;
}
.detail-note,
.detail-status {
  margin: 0.4rem 0 0;
  max-width: none;
  font-size: 0.9375rem;
  color: var(--muted);
}
.detail-card {
  margin-top: 0.7rem;
  padding-top: 0.7rem;
  border-top: 1px solid var(--rule);
}
.detail-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem 0.6rem;
}
.detail-title {
  font-family: var(--mono);
  font-size: var(--fs-data);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.detail-ref {
  font-family: var(--mono);
  font-size: var(--fs-data);
  color: var(--muted);
  overflow-wrap: anywhere;
}
.fields {
  display: grid;
  grid-template-columns: minmax(0, auto) minmax(0, 1fr);
  gap: 0.15rem 0.8rem;
  margin: 0.5rem 0 0;
  font-size: var(--fs-data);
}
.fields dt { color: var(--muted); }
.fields dd {
  margin: 0;
  font-family: var(--mono);
  overflow-wrap: anywhere;
}
.detail-raw,
.raw-link {
  overflow-wrap: anywhere;
}
.detail-raw {
  margin: 0.7rem 0 0;
  max-width: none;
  font-size: 0.9375rem;
}
.empty, .failure {
  border: 1px solid var(--rule);
  border-left: 3px solid var(--phase-pos);
  background: var(--surface);
  padding: 0.9rem 1rem;
}
.failure { border-left-color: var(--phase-neg); }
.empty p, .failure p { margin: 0 0 0.5rem; max-width: none; }
.empty p:last-child, .failure p:last-child { margin-bottom: 0; }
.failure-code {
  font-family: var(--mono);
  font-size: var(--fs-data);
  font-weight: 700;
  color: var(--phase-neg-text);
}
.failure-detail { font-family: var(--mono); font-size: var(--fs-data); overflow-wrap: anywhere; }

/* ---- the worked example ---- */
/*
 * <figure> carries a 40px side margin by UA default, which on a 360px
 * screen spends 80px of the viewport on nothing and squeezes the
 * coordinates the panel exists to show.
 */
.exchange-figure { margin: 0; }
/*
 * ``minmax(0, 1fr)`` rather than ``1fr``: a grid item's automatic
 * minimum size is its content, so the wide <pre> of coordinates would
 * otherwise push the track past the viewport and scroll the whole
 * document sideways at 360px instead of scrolling inside its own box.
 */
.exchange {
  margin: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 1px;
  background: var(--rule);
  border: 1px solid var(--rule);
}
@media (min-width: 46rem) {
  .exchange { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
}
.panel {
  background: var(--surface);
  padding: 1rem 1.1rem 1.15rem;
  min-width: 0;
}
.panel-label {
  font-family: var(--mono);
  font-size: var(--fs-label);
  font-weight: 700;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 0.75rem;
}
.panel.verdict .panel-label { color: var(--phase-neg-text); }
.panel pre {
  margin: 0;
  font-family: var(--mono);
  /*
   * Shrinks just enough that all four XYZ columns fit inside a 360px
   * viewport rather than scrolling two of them out of sight. The
   * coordinates are the point of the panel; a reader who has to swipe
   * to see the z column has not been shown the geometry.
   */
  font-size: clamp(0.6875rem, 0.55rem + 0.5vw, var(--fs-data));
  line-height: 1.55;
  white-space: pre;
  overflow-x: auto;
  background: var(--data-bg);
  padding: 0.7rem 0.8rem;
}
/*
 * The deposited list above the spectrum the molecule really has, one
 * frequency per column. The panel's entire job is "look what TCKDB
 * caught", so the gap has to be findable by eye before any prose
 * explains it: the absent mode is an empty dashed cell sitting
 * directly above the frequency that belongs in it, both drawn in the
 * negative phase colour, which appears nowhere else in this panel.
 *
 * A table rather than an aligned <pre>: two rows of numbers with a row
 * header each is what a table is, column alignment survives every mono
 * fallback face, and a screen reader is told which column a value sits
 * in instead of being read a run of digits.
 */
.spectrum-wrap { overflow-x: auto; }
.spectrum {
  border-collapse: collapse;
  margin: 0;
  width: 100%;
  font-family: var(--mono);
  /*
   * Floors lower than the coordinate block above it. Five columns of
   * nowrap monospace is the widest thing on the page, and the whole
   * point is that both rows are in view at once: a 360px reader who
   * has to swipe the last column into sight loses the comparison the
   * panel is making.
   */
  font-size: clamp(0.625rem, 0.5rem + 0.5vw, var(--fs-data));
  background: var(--data-bg);
}
.spectrum th,
.spectrum td {
  padding: 0.4rem 0.25rem;
  font-weight: 400;
  text-align: right;
  white-space: nowrap;
}
.spectrum th[scope="row"] {
  padding-left: 0.5rem;
  text-align: left;
  color: var(--muted);
}
.spectrum td:last-child { padding-right: 0.5rem; }
.spectrum tr:first-child > * { padding-top: 0.7rem; }
.spectrum tr:last-child > * { padding-bottom: 0.7rem; }
.spectrum .gap { padding: 0.3rem 0.3rem; }
.gap-mark {
  display: block;
  border: 1.5px dashed var(--phase-neg);
  padding: 0.1rem 0.2rem;
  color: var(--phase-neg-text);
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.spectrum .gap-source {
  border: 1.5px solid var(--phase-neg);
  color: var(--phase-neg-text);
  font-weight: 700;
}
.spectrum-caption {
  margin: 0.6rem 0 0;
  font-size: 0.9375rem;
  max-width: none;
}
.spectrum-caption b { color: var(--phase-neg-text); }
.panel-sub {
  font-family: var(--mono);
  font-size: var(--fs-label);
  font-weight: 700;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0.9rem 0 0.45rem;
}
.verdict-code {
  display: block;
  font-family: var(--mono);
  /*
   * Sized to fit the 45-character code on one line at the widest
   * fallback mono face, because a code broken mid-token reads as
   * damage rather than emphasis.
   */
  font-size: var(--fs-data);
  font-weight: 700;
  line-height: 1.45;
  letter-spacing: -0.01em;
  color: var(--phase-neg-text);
  overflow-wrap: anywhere;
  border-left: 3px solid var(--phase-neg);
  padding-left: 0.7rem;
}
.verdict-body { margin: 0.85rem 0 0; font-size: 0.9375rem; max-width: none; }
.verdict-body:last-child { margin-bottom: 0; }

/* ---- code and prose data ---- */
code {
  font-family: var(--mono);
  font-size: 0.9em;
  background: var(--data-bg);
  padding: 0.08em 0.3em;
  overflow-wrap: anywhere;
}
pre.command {
  font-family: var(--mono);
  font-size: var(--fs-data);
  background: var(--surface);
  border: 1px solid var(--rule);
  border-left: 3px solid var(--phase-pos);
  padding: 0.8rem 1rem;
  overflow-x: auto;
  max-width: 38rem;
}
pre.command code { background: none; padding: 0; }

/* ---- footer ---- */
footer {
  margin-top: 3.5rem;
  padding-top: 1.25rem;
  border-top: 1px solid var(--rule);
  color: var(--muted);
  font-size: 0.9375rem;
}
footer p { max-width: none; margin: 0; }
</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>

<header>
  <h1>
    <span class="wordmark">TCKDB</span>
    <span class="fullname">Thermochemical &amp; Kinetics Database</span>
  </h1>
  <p class="lede">
    Provenance-tracked computational chemistry data over a public HTTP API.
    The reads below need no account and no key.
  </p>
  <nav class="actions" aria-label="Primary">
    <ul>
      __REFERENCE_BUTTON__
      <li><a class="button __DOCS_BUTTON__" href="__DOCS_URL__">Documentation</a></li>
      <li><a class="button secondary" href="__REPO_URL__">Source</a></li>
    </ul>
  </nav>
</header>

<main id="main">

  <section class="hero" aria-labelledby="search-heading">
    <h2 id="search-heading">Search species</h2>
    <p class="note">
      Records under review are served, not withheld; every result says how
      far it has been checked.
    </p>

    <form id="search-form" class="search" method="get" action="__SEARCH_PATH__">
      <div class="search-row">
        <label class="sr-only" for="search-field">Identifier</label>
        <select id="search-field" disabled>
__FIELD_OPTIONS__
        </select>
        <label class="sr-only" for="search-input">Search value</label>
        <input id="search-input" name="__SEED_FIELD__" type="search"
               value="__SEED_VALUE__" autocomplete="off" autocapitalize="off"
               spellcheck="false">
        <button type="submit">Search</button>
      </div>
    </form>

    <p class="examples" id="examples">
      <span>Try</span>
__EXAMPLE_CHIPS__
    </p>

    <noscript>
      <div class="fallback">
        <p>
          JavaScript is off, so the box above submits as a plain form and
          searches by formula. These search the other identifiers:
        </p>
        <form method="get" action="__SEARCH_PATH__">
          <label class="sr-only" for="fallback-smiles">SMILES</label>
          <input id="fallback-smiles" name="smiles" type="search" placeholder="SMILES">
          <button type="submit">Search</button>
        </form>
        <form method="get" action="__SEARCH_PATH__">
          <label class="sr-only" for="fallback-inchi-key">InChIKey</label>
          <input id="fallback-inchi-key" name="inchi_key" type="search" placeholder="InChIKey">
          <button type="submit">Search</button>
        </form>
      </div>
    </noscript>

    <div id="results" class="results" aria-live="polite">
      <p class="results-status">
        Results land here. Without JavaScript the box and examples go
        straight to the API's JSON.
      </p>
    </div>
  </section>

  <section aria-labelledby="quickstart-heading">
    <h2 id="quickstart-heading">Quickstart</h2>
    <p>
      Everything machine-readable is under <code>/api/v1</code>. Reads are
      open; depositing needs an account.
    </p>
    <pre class="command"><code>TCKDB=<span id="curl-origin">__ORIGIN_PLACEHOLDER__</span>
curl -s "$TCKDB__SEARCH_PATH__?formula=CH3"</code></pre>
    <p>__API-REFERENCE__</p>
    <p>
      A Python client, <code>tckdb-client</code>, handles auth, retries and
      idempotency:
    </p>
    <pre class="command"><code>pip install "git+__REPO_URL__.git#subdirectory=clients/python"</code></pre>
  </section>

  <section aria-labelledby="exchange-heading">
    <h2 id="exchange-heading">What a check looks like</h2>
    <figure class="exchange-figure">
      <div class="exchange">
        <div class="panel deposit">
          <p class="panel-label">Deposited</p>
          <p class="panel-sub">carbon dioxide, geometry / &#197;</p>
          <pre>__HERO_GEOMETRY__</pre>
          <p class="panel-sub">frequencies / cm&#8315;&#185;</p>
          <div class="spectrum-wrap">
__HERO_SPECTRUM__
          </div>
          <p class="spectrum-caption">
            The top row is what was deposited. The bottom row is the spectrum
            carbon dioxide has. <b>667 cm&#8315;&#185; belongs in it twice</b>,
            and the deposit carries it once.
          </p>
        </div>
        <div class="panel verdict">
          <p class="panel-label">TCKDB replies</p>
          <code class="verdict-code">__HERO_CODE__</code>
          <p class="verdict-body">
            Carbon dioxide is linear, so it has 3N&minus;5 = 4 vibrations:
            a symmetric stretch, an asymmetric stretch, and a bend.
          </p>
          <p class="verdict-body">
            The bend is <em>doubly degenerate</em>. The molecule bends in two
            perpendicular planes, and both cost the same energy, so one bend
            occupies two of the four modes at the same wavenumber.
          </p>
          <p class="verdict-body">
            This list carries that wavenumber once. The usual cause is a
            parser that de-duplicates equal frequencies, which leaves exactly
            three &mdash; 3N&minus;6, the vibrational count of a <em>bent</em>
            three-atom molecule, on a geometry that is not bent.
          </p>
          <p class="verdict-body">
            The record is accepted and flagged, not refused. TCKDB stores the
            deposit and attaches the code above to it.
          </p>
        </div>
      </div>
    </figure>
  </section>

  <section aria-labelledby="citing-heading">
    <h2 id="citing-heading">Citing</h2>
    <p>
      Two citable things, versioned independently. The software:
      <a href="__REPO_URL__/blob/main/CITATION.cff"><code>CITATION.cff</code></a>
      in the repository, MIT, no DOI until a paper tag is cut. A dataset
      release: its own tag, checksummed manifest and
      <code>changelog_entry</code>, listed at
      <code>GET /api/v1/scientific/releases</code>.
    </p>
  </section>

</main>

<footer>
  <p>
    Database revision and health:
    <a href="/api/v1/status"><code>/api/v1/status</code></a>.
  </p>
</footer>

<script>
/*
 * Inline, same-origin, no dependencies. Everything below only upgrades
 * markup that already works: the form is a real GET form aimed at the
 * API, and the example chips are real links to it. If this script never
 * runs -- scripting off, an old engine, a parse error -- the page keeps
 * its behaviour and loses only the in-page rendering.
 */
(function () {
  "use strict";

  if (!window.fetch || !window.Promise) { return; }

  var doc = document;
  var form = doc.getElementById("search-form");
  var input = doc.getElementById("search-input");
  var picker = doc.getElementById("search-field");
  var out = doc.getElementById("results");
  if (!form || !input || !picker || !out) { return; }

  var SEARCH = "__SEARCH_PATH__";
  var ENTRY = "/api/v1/scientific/species-entries/";
  var THERMO = "/api/v1/scientific/thermo/search";
  var CONFORMERS = "/api/v1/scientific/conformers/search";
  var CALCULATIONS = "/api/v1/scientific/species-calculations/search";

  var PLACEHOLDERS = __PLACEHOLDERS_JSON__;
  var EXAMPLES = __EXAMPLES_JSON__;
  var REVIEW_WORDS = {
    approved: "approved",
    under_review: "under review",
    not_reviewed: "not reviewed",
    deprecated: "deprecated",
    rejected: "rejected"
  };

  function make(tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }

  function anchor(href, cls, text) {
    var node = make("a", cls, text);
    node.setAttribute("href", href);
    return node;
  }

  function clear(node) {
    while (node.firstChild) { node.removeChild(node.firstChild); }
  }

  function plural(n, one, many) {
    return n + " " + (n === 1 ? one : many);
  }

  function searchUrl(field, value) {
    if (!value) { return SEARCH; }
    return SEARCH + "?" + encodeURIComponent(field) + "=" + encodeURIComponent(value);
  }

  function reviewBadge(status, count) {
    var key = REVIEW_WORDS[status] ? status : "not_reviewed";
    var word = REVIEW_WORDS[status] || String(status);
    var node = make("span", "badge badge-" + key);
    node.appendChild(make("span", "dot"));
    node.appendChild(make("span", null, count === undefined ? word : count + " " + word));
    return node;
  }

  function chip(field, value) {
    var node = anchor(searchUrl(field, value), "chip", value);
    node.setAttribute("data-field", field);
    node.addEventListener("click", function (event) {
      event.preventDefault();
      picker.value = field;
      syncField();
      input.value = value;
      run(field, value);
    });
    return node;
  }

  function exampleList(intro) {
    var node = make("p", "examples");
    node.appendChild(make("span", null, intro));
    for (var i = 0; i < EXAMPLES.length; i += 1) {
      node.appendChild(chip(EXAMPLES[i][0], EXAMPLES[i][1]));
    }
    return node;
  }

  /*
   * ---- what a result leads to -------------------------------------
   *
   * Every product a record has becomes a disclosure that opens in
   * place. The panel names the handful of fields that answer what is
   * in here, and ends with the same endpoint labelled as raw JSON, so
   * the machine-readable document stays one click away and is never
   * somewhere a reader lands unawares.
   *
   * The numbers shown are the payload own pagination total and the
   * per-record count fields. Deliberately never the group-scope
   * has_* booleans in the conformer payload: each is an OR across
   * every observation in the group, so it reads as a fact about the
   * conformer while being a fact about the union. Those are being
   * replaced by counts; until they are, this page shows none of them.
   */

  function fixed(value, digits, unit) {
    if (value === null || value === undefined) { return null; }
    var text = Number(value).toFixed(digits);
    return unit ? text + " " + unit : text;
  }

  /*
   * A software release prints its own name in some deployments
   * (Gaussian 16, Revision C.02) and not in others (16). Concatenating
   * unconditionally gives one of those a stutter and the other a bare
   * number with no product attached, so the name is prepended only
   * when the version does not already open with it.
   */
  function softwareLabel(release) {
    if (!release) { return null; }
    var name = release.software;
    var version = release.version;
    if (!name) { return version || null; }
    if (!version) { return name; }
    return version.indexOf(name) === 0 ? version : name + " " + version;
  }

  function levelLabel(level) {
    if (!level) { return null; }
    if (level.label) { return level.label; }
    if (!level.method) { return null; }
    return level.method + (level.basis ? "/" + level.basis : "");
  }

  function thermoView(record) {
    var thermo = record.thermo || {};
    var coverage = thermo.temperature_coverage || {};
    var primary = (record.provenance || {}).primary_calculation || {};
    var span = null;
    if (coverage.record_min_k !== null && coverage.record_min_k !== undefined) {
      span = fixed(coverage.record_min_k, 0) + "\u2013" + fixed(coverage.record_max_k, 0, "K");
    }
    return {
      title: (thermo.model_kind || "thermo") + " fit",
      ref: thermo.thermo_ref,
      status: thermo.review ? thermo.review.status : null,
      fields: [
        ["enthalpy at 298 K", fixed(thermo.h298_kj_mol, 2, "kJ/mol")],
        ["entropy at 298 K", fixed(thermo.s298_j_mol_k, 2, "J/mol/K")],
        ["fitted over", span],
        ["level of theory", levelLabel(primary.level_of_theory)],
        ["origin", thermo.scientific_origin]
      ]
    };
  }

  function statmechView(record) {
    var statmech = record.statmech || {};
    var linear = statmech.is_linear;
    return {
      title: (statmech.statmech_treatment || "statmech") +
        (statmech.rigid_rotor_kind ? " / " + statmech.rigid_rotor_kind + " rotor" : ""),
      ref: statmech.statmech_ref,
      status: statmech.review ? statmech.review.status : null,
      fields: [
        ["point group", statmech.point_group],
        ["symmetry number", statmech.external_symmetry],
        ["optical isomers", statmech.optical_isomers],
        ["linear", linear === null || linear === undefined ? null : (linear ? "yes" : "no")],
        ["frequency scale factor", fixed(statmech.frequency_scale_factor_value, 4)],
        ["origin", statmech.scientific_origin]
      ]
    };
  }

  function transportView(record) {
    var transport = record.transport || {};
    var evidence = record.evidence_summary || {};
    return {
      title: "Lennard-Jones parameters",
      ref: transport.transport_ref,
      status: transport.review ? transport.review.status : null,
      fields: [
        ["collision diameter", fixed(transport.sigma_angstrom, 3, "\u00C5")],
        ["well depth", fixed(transport.epsilon_over_k_k, 1, "K")],
        ["dipole moment", fixed(transport.dipole_debye, 3, "D")],
        ["source calculations", evidence.source_calculation_count],
        ["origin", transport.scientific_origin]
      ]
    };
  }

  function conformerView(record) {
    var group = record.conformer_group || {};
    var evidence = record.evidence_summary || {};
    var observations = record.observations_summary || {};
    var seen = evidence.observation_count;
    if (seen === null || seen === undefined) { seen = observations.total; }
    return {
      title: group.label || "conformer group",
      ref: group.conformer_group_ref,
      status: group.review ? group.review.status : null,
      fields: [
        ["deposited as", seen === null || seen === undefined
          ? null
          : plural(seen, "observation", "observations") + " of this one group"],
        ["calculations behind it", evidence.calculation_count],
        ["distinct geometries", evidence.geometry_count]
      ]
    };
  }

  function calculationView(record) {
    var calculation = record.calculation || {};
    var energy = record.energy || {};
    var software = record.software_release || {};
    var hartree = energy.energy_hartree;
    return {
      title: calculation.calculation_type || "calculation",
      ref: calculation.calculation_ref,
      status: calculation.review ? calculation.review.status : null,
      fields: [
        ["level of theory", levelLabel(record.level_of_theory)],
        ["software", softwareLabel(software)],
        ["energy", hartree === null || hartree === undefined
          ? null
          : fixed(hartree, 6, "hartree")],
        ["quality", calculation.calculation_quality]
      ]
    };
  }

  var SECTIONS = [
    {
      key: "thermo",
      label: "thermo",
      one: "thermo record",
      many: "thermo records",
      shown: function (a) { return !!a.has_thermo; },
      url: function (ref) { return THERMO + "?species_entry_ref=" + encodeURIComponent(ref); },
      view: thermoView
    },
    {
      key: "statmech",
      label: "statmech",
      one: "statmech record",
      many: "statmech records",
      shown: function (a) { return !!a.has_statmech; },
      url: function (ref) { return ENTRY + encodeURIComponent(ref) + "/statmech"; },
      view: statmechView
    },
    {
      key: "transport",
      label: "transport",
      one: "transport record",
      many: "transport records",
      shown: function (a) { return !!a.has_transport; },
      url: function (ref) { return ENTRY + encodeURIComponent(ref) + "/transport"; },
      view: transportView
    },
    {
      key: "conformers",
      label: "conformers",
      one: "conformer group",
      many: "conformer groups",
      note: "A conformer group is one torsional basin -- one conformer. Its " +
        "observations are the deposited instances assigned to that basin, so a " +
        "group with five observations is one conformer seen five times, not " +
        "five conformers.",
      shown: function (a) { return !!a.has_conformers; },
      url: function (ref) { return CONFORMERS + "?species_entry_ref=" + encodeURIComponent(ref); },
      view: conformerView
    },
    {
      key: "calculations",
      label: "calculations",
      one: "calculation",
      many: "calculations",
      count: function (a) { return a.calculation_count; },
      shown: function (a) { return !!a.calculation_count; },
      url: function (ref) {
        return CALCULATIONS + "?species_entry_ref=" + encodeURIComponent(ref);
      },
      view: calculationView
    }
  ];

  /*
   * How many records a panel draws before deferring to the raw
   * document. An entry can carry dozens of calculations, and a landing
   * page that answers a click with fifty cards has swapped one wall of
   * text for another. The count above the cards is the real total, so
   * the cap shortens the reading and never the answer.
   */
  var CARD_LIMIT = 5;

  var panelSeq = 0;

  function pillLabel(section, availability) {
    if (section.count) {
      return plural(section.count(availability), section.one, section.many);
    }
    return section.label;
  }

  function rawLink(section, ref) {
    var node = make("p", "detail-raw");
    node.appendChild(anchor(section.url(ref), "raw-link", "Open this list as raw JSON"));
    return node;
  }

  function detailFields(view) {
    var list = make("dl", "fields");
    for (var i = 0; i < view.fields.length; i += 1) {
      var pair = view.fields[i];
      if (pair[1] === null || pair[1] === undefined || pair[1] === "") { continue; }
      list.appendChild(make("dt", null, pair[0]));
      list.appendChild(make("dd", null, pair[1]));
    }
    return list;
  }

  function detailBody(section, ref, payload) {
    var fragment = doc.createDocumentFragment();
    var records = payload.records || [];
    var pagination = payload.pagination || {};
    var total = pagination.total;
    if (total === null || total === undefined) { total = records.length; }
    fragment.appendChild(make("p", "detail-count", plural(total, section.one, section.many)));
    if (section.note) { fragment.appendChild(make("p", "detail-note", section.note)); }
    var shown = Math.min(records.length, CARD_LIMIT);
    for (var i = 0; i < shown; i += 1) {
      var view = section.view(records[i]);
      var card = make("div", "detail-card");
      var head = make("div", "detail-head");
      head.appendChild(make("span", "detail-title", view.title));
      if (view.status) { head.appendChild(reviewBadge(view.status)); }
      if (view.ref) { head.appendChild(make("span", "detail-ref", view.ref)); }
      card.appendChild(head);
      card.appendChild(detailFields(view));
      fragment.appendChild(card);
    }
    if (total > shown) {
      fragment.appendChild(make("p", "detail-note",
        "Showing the first " + plural(shown, section.one, section.many) + " of " + total + "."));
    }
    fragment.appendChild(rawLink(section, ref));
    return fragment;
  }

  function loadDetail(panel, section, ref) {
    clear(panel);
    panel.appendChild(make("p", "detail-status", "Loading\u2026"));
    fetch(section.url(ref), { headers: { "Accept": "application/json" } })
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        }, function () {
          return { ok: false, body: null };
        });
      })
      .then(function (result) {
        clear(panel);
        if (result.ok) {
          panel.appendChild(detailBody(section, ref, result.body || {}));
          return;
        }
        panel.appendChild(make("p", "detail-status", "This list could not be read."));
        panel.appendChild(rawLink(section, ref));
      }, function () {
        clear(panel);
        panel.appendChild(make("p", "detail-status",
          "The browser could not reach this deployment's API."));
      });
  }

  function disclosure(section, ref, availability) {
    panelSeq += 1;
    var id = "detail-" + section.key + "-" + panelSeq;
    var button = make("button", "pill", pillLabel(section, availability));
    button.setAttribute("type", "button");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-controls", id);
    var panel = make("div", "detail");
    panel.id = id;
    panel.hidden = true;
    var loaded = false;
    button.addEventListener("click", function () {
      var open = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", open ? "false" : "true");
      panel.hidden = open;
      if (!open && !loaded) {
        loaded = true;
        loadDetail(panel, section, ref);
      }
    });
    return { button: button, panel: panel };
  }

  function entryNode(entry) {
    var ref = entry.species_entry_ref || "";
    var availability = entry.availability || {};
    var node = make("div", "entry");
    var head = make("div", "entry-head");
    head.appendChild(reviewBadge(entry.review ? entry.review.status : null));
    head.appendChild(make("span", "entry-kind",
      (entry.species_entry_kind || "entry") + " / " +
      (entry.electronic_state_kind || "state")));
    head.appendChild(make("span", "entry-kind", ref));
    node.appendChild(head);

    var links = make("div", "entry-links");
    var panels = make("div", "entry-details");
    for (var i = 0; i < SECTIONS.length; i += 1) {
      if (!SECTIONS[i].shown(availability)) { continue; }
      var pair = disclosure(SECTIONS[i], ref, availability);
      links.appendChild(pair.button);
      panels.appendChild(pair.panel);
    }
    if (!links.firstChild) {
      links.appendChild(make("span", "pill pill-none", "no products yet"));
    }
    node.appendChild(links);
    node.appendChild(panels);
    return node;
  }

  function recordNode(record) {
    var node = make("article", "record");
    var head = make("div", "record-head");
    head.appendChild(make("span", "smiles", record.canonical_smiles || "(no SMILES)"));
    head.appendChild(make("span", "record-meta",
      "charge " + record.charge + ", multiplicity " + record.multiplicity));
    node.appendChild(head);

    var refs = make("p", "record-refs");
    refs.appendChild(make("span", null, record.inchi_key || ""));
    refs.appendChild(doc.createTextNode(" \\u00B7 "));
    refs.appendChild(make("span", null, record.species_ref || ""));
    refs.appendChild(doc.createTextNode(" \u00B7 "));
    refs.appendChild(anchor(searchUrl("species_ref", record.species_ref), "raw-link",
      "this record as raw JSON"));
    node.appendChild(refs);

    var entries = record.entries || [];
    for (var i = 0; i < entries.length; i += 1) {
      node.appendChild(entryNode(entries[i]));
    }
    if (!entries.length) {
      var none = make("div", "entry");
      none.appendChild(make("span", "entry-kind", "no entries yet"));
      node.appendChild(none);
    }
    return node;
  }

  function summaryNode(payload) {
    var pagination = payload.pagination || {};
    var review = payload.review_summary || {};
    var node = make("p", "results-summary");
    var total = pagination.total || 0;
    node.appendChild(make("span", "results-count", plural(total, "match", "matches")));
    var order = ["approved", "under_review", "not_reviewed", "deprecated", "rejected"];
    for (var i = 0; i < order.length; i += 1) {
      if (review[order[i]]) {
        node.appendChild(reviewBadge(order[i], review[order[i]]));
      }
    }
    return node;
  }

  function emptyNode(field, value) {
    var node = make("div", "empty");
    node.appendChild(make("p", null,
      value ? "Nothing matched " + field + " = " + value + "." : "Nothing matched."));
    node.appendChild(exampleList("These return records:"));
    return node;
  }

  function detailText(body) {
    if (!body) { return ""; }
    var detail = body.detail;
    if (typeof detail === "string") { return detail; }
    if (Object.prototype.toString.call(detail) === "[object Array]") {
      var parts = [];
      for (var i = 0; i < detail.length; i += 1) {
        parts.push(detail[i] && detail[i].msg ? detail[i].msg : JSON.stringify(detail[i]));
      }
      return parts.join("; ");
    }
    if (detail) { return JSON.stringify(detail); }
    return "";
  }

  function failureNode(status, body) {
    var node = make("div", "failure");
    var code = body && body.code ? body.code : "http_" + status;
    node.appendChild(make("p", "failure-code", code));
    var detail = detailText(body);
    node.appendChild(make("p", "failure-detail",
      detail || "The API answered with status " + status + "."));
    return node;
  }

  function render(payload, field, value) {
    var records = payload.records || [];
    if (!records.length) {
      out.appendChild(emptyNode(field, value));
      return;
    }
    out.appendChild(summaryNode(payload));
    for (var i = 0; i < records.length; i += 1) {
      out.appendChild(recordNode(records[i]));
    }
    var pagination = payload.pagination || {};
    if (pagination.total > records.length) {
      var more = make("p", "results-status", "Showing the first page.");
      more.appendChild(doc.createTextNode(" "));
      more.appendChild(anchor(searchUrl(field, value), "raw-link",
        "Open the full response as raw JSON"));
      out.appendChild(more);
    }
  }

  function run(field, value) {
    var url = searchUrl(field, value);
    out.setAttribute("aria-busy", "true");
    clear(out);
    out.appendChild(make("p", "results-status", "Searching\\u2026"));
    fetch(url, { headers: { "Accept": "application/json" } }).then(function (response) {
      return response.json().then(function (body) {
        return { ok: response.ok, status: response.status, body: body };
      }, function () {
        return { ok: false, status: response.status, body: null };
      });
    }).then(function (result) {
      clear(out);
      if (result.ok) {
        render(result.body || {}, field, value);
      } else {
        out.appendChild(failureNode(result.status, result.body));
      }
    }, function () {
      clear(out);
      var node = make("div", "failure");
      node.appendChild(make("p", "failure-detail",
        "The browser could not reach this deployment's API."));
      out.appendChild(node);
    }).then(function () {
      out.setAttribute("aria-busy", "false");
    });
  }

  function syncField() {
    input.name = picker.value;
    input.setAttribute("placeholder", PLACEHOLDERS[picker.value] || "");
  }

  picker.disabled = false;
  picker.addEventListener("change", function () {
    syncField();
    input.focus();
  });
  syncField();

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    run(picker.value, input.value.replace(/^\\s+|\\s+$/g, ""));
  });

  var seeded = doc.getElementById("examples");
  if (seeded) {
    var links = seeded.getElementsByTagName("a");
    for (var i = 0; i < links.length; i += 1) {
      (function (node) {
        node.addEventListener("click", function (event) {
          event.preventDefault();
          var field = node.getAttribute("data-field");
          var value = node.textContent;
          picker.value = field;
          syncField();
          input.value = value;
          run(field, value);
        });
      })(links[i]);
    }
  }

  var origin = doc.getElementById("curl-origin");
  if (origin && window.location && window.location.origin) {
    origin.textContent = window.location.origin;
  }

  run(picker.value, input.value.replace(/^\\s+|\\s+$/g, ""));
})();
</script>

</body>
</html>
"""

#: Shown in the ``curl`` example until the script replaces it with the
#: origin the browser is actually on. It is not a URL, deliberately: a
#: hard-coded hostname would be wrong for every deployment but one, and
#: the page has no trustworthy way to learn its own public scheme from
#: the request (nothing in front of it sets forwarded headers this
#: application is configured to believe).
ORIGIN_PLACEHOLDER = "&lt;this deployment&rsquo;s base URL&gt;"


def _field_options(selected: str) -> str:
    lines = []
    for value, label in SEARCH_FIELDS:
        flag = " selected" if value == selected else ""
        lines.append(f'          <option value="{value}"{flag}>{label}</option>')
    return "\n".join(lines)


def _example_chips() -> str:
    lines = []
    for field, value in SEARCH_EXAMPLES:
        href = f"{SPECIES_SEARCH_PATH}?{field}={_url_quote(value)}"
        lines.append(
            f'      <a class="chip" data-field="{field}" href="{href}">'
            f"{_html_escape(value)}</a>"
        )
    return "\n".join(lines)


def hero_atom_lines() -> tuple[str, ...]:
    """The coordinate rows of :data:`HERO_XYZ`, without the XYZ header.

    The header is real and the checker reads it; it is simply not
    something to put on a web page on its own. See :data:`HERO_XYZ`.
    """
    return tuple(HERO_XYZ.splitlines()[2:])


def _spectrum_table() -> str:
    """The deposited frequency list above the true spectrum, gap marked.

    Built from :data:`HERO_TRUE_FREQUENCIES` and :data:`HERO_FREQUENCIES`
    rather than written out, so the cell drawn as *missing* is the cell
    the two tuples really disagree about. A hand-written table could
    mark the wrong column, or keep marking one after the numbers moved,
    and would look equally convincing either way.

    The comparison is a multiset one: a degenerate pair is two entries
    with the same value, so matching by value alone would call both of
    CO2's 667 cm-1 modes present and mark nothing.
    """
    remaining = list(HERO_FREQUENCIES)
    deposited: list[str] = []
    spectrum: list[str] = []
    for value in HERO_TRUE_FREQUENCIES:
        cell = _html_escape(f"{value:.1f}")
        if value in remaining:
            remaining.remove(value)
            deposited.append(f"<td>{cell}</td>")
            spectrum.append(f"<td>{cell}</td>")
        else:
            deposited.append('<td class="gap"><span class="gap-mark">missing</span></td>')
            spectrum.append(f'<td class="gap-source">{cell}</td>')
    if remaining:  # pragma: no cover - a constant-only mistake, caught in tests
        raise ValueError(
            "the deposited frequency list carries a mode the true spectrum does not: "
            f"{remaining}"
        )
    indent = " " * 12
    lines = [
        f'{indent}<table class="spectrum">',
        f"{indent}<caption class=\"sr-only\">Carbon dioxide vibrational "
        "frequencies, in reciprocal centimetres: the deposited list above the "
        'spectrum the molecule has.</caption>',
        f"{indent}<tr>",
        f'{indent}<th scope="row">deposited</th>',
        *(f"{indent}{cell}" for cell in deposited),
        f"{indent}</tr>",
        f"{indent}<tr>",
        f'{indent}<th scope="row">CO<sub>2</sub> has</th>',
        *(f"{indent}{cell}" for cell in spectrum),
        f"{indent}</tr>",
        f"{indent}</table>",
    ]
    return "\n".join(lines)


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _url_quote(value: str) -> str:
    return quote(value, safe="")


def _json_pairs(pairs: tuple[tuple[str, str], ...]) -> str:
    return json.dumps([list(pair) for pair in pairs])


def _placeholders_json() -> str:
    """Per-identifier ``placeholder`` text, keyed by query parameter.

    Each one is a value that really is in the shape that field wants, so
    the hint doubles as a worked example of the syntax.
    """
    return json.dumps(dict(SEARCH_PLACEHOLDERS))


def render_landing_page(*, api_reference_path: str | None) -> str:
    """Return the complete landing-page HTML document.

    *api_reference_path* is the path this deployment serves the ReDoc
    API reference on, or ``None`` when it serves none. It is not a
    cosmetic difference: a deployment with the reference switched off
    must not advertise a link that answers 404, which is the precise
    failure this page was added to remove.

    The template carries ``__PLACEHOLDER__`` markers rather than
    ``str.format`` fields, because the embedded stylesheet and script
    are full of braces and escaping every one of them would make both
    unreadable for no gain.
    """
    if api_reference_path:
        reference_button = (
            f'<li><a class="button primary" href="{api_reference_path}">API reference</a></li>'
        )
        docs_button = "secondary"
        reference_block = (
            "The full reference is at "
            f'<a href="{api_reference_path}"><code>{api_reference_path}</code></a>, '
            "generated from this deployment's OpenAPI document."
        )
    else:
        reference_button = ""
        docs_button = "primary"
        reference_block = (
            "This deployment does not publish an API reference endpoint. The "
            f'<a href="{DOCS_URL}">documentation site</a> carries the endpoint '
            "reference and query guides."
        )
    return (
        _PAGE_TEMPLATE.replace("__DOCS_URL__", DOCS_URL)
        .replace("__REPO_URL__", REPO_URL)
        .replace("__SEARCH_PATH__", SPECIES_SEARCH_PATH)
        .replace("__FIELD_OPTIONS__", _field_options(SEED_FIELD))
        .replace("__EXAMPLE_CHIPS__", _example_chips())
        .replace("__PLACEHOLDERS_JSON__", _placeholders_json())
        .replace("__EXAMPLES_JSON__", _json_pairs(SEARCH_EXAMPLES))
        .replace("__ORIGIN_PLACEHOLDER__", ORIGIN_PLACEHOLDER)
        .replace("__SEED_FIELD__", SEED_FIELD)
        .replace("__SEED_VALUE__", _html_escape(SEED_VALUE))
        .replace("__HERO_GEOMETRY__", "\n".join(hero_atom_lines()))
        .replace("__HERO_SPECTRUM__", _spectrum_table())
        .replace("__HERO_CODE__", HERO_CODE)
        .replace("__REFERENCE_BUTTON__", reference_button)
        .replace("__DOCS_BUTTON__", docs_button)
        .replace("__API-REFERENCE__", reference_block)
    )


landing_router = APIRouter()


@landing_router.get(
    "/",
    include_in_schema=False,
    response_class=HTMLResponse,
    summary="Human-readable landing page",
)
def landing_page() -> HTMLResponse:
    """Serve the landing page for whoever typed the bare base URL.

    Excluded from the OpenAPI document deliberately. It is a page, not
    an operation: nothing generates a client method for it, and listing
    it would put an HTML response in a schema every consumer reads as
    the machine contract.
    """
    serves_reference = settings.expose_api_docs or settings.expose_api_reference
    return HTMLResponse(
        content=render_landing_page(api_reference_path=REDOC_PATH if serves_reference else None)
    )


__all__ = [
    "DOCS_URL",
    "HERO_CODE",
    "HERO_FREQUENCIES",
    "HERO_TRUE_FREQUENCIES",
    "HERO_XYZ",
    "ORIGIN_PLACEHOLDER",
    "REDOC_PATH",
    "REPO_URL",
    "SEARCH_EXAMPLES",
    "SEARCH_FIELDS",
    "SEED_FIELD",
    "SEED_VALUE",
    "SPECIES_SEARCH_PATH",
    "hero_atom_lines",
    "landing_router",
    "render_landing_page",
]
