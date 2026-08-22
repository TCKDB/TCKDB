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
HERO_XYZ = """3
carbon dioxide
C   0.000000   0.000000   0.000000
O   0.000000   0.000000   1.162000
O   0.000000   0.000000  -1.162000"""

#: The frequency list deposited alongside :data:`HERO_XYZ`: the three
#: distinct wavenumbers a CO2 harmonic analysis prints. Three is 3N-6,
#: the count for a *bent* three-atom molecule; a linear one has 3N-5=4,
#: because the bend is doubly degenerate and a de-duplicating parser
#: reports it once.
HERO_FREQUENCIES = (667.0, 1333.0, 2349.0)

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

/* ---- the four roles ---- */
.roles { margin: 0; }
.roles > div {
  padding: 0.9rem 0;
  border-top: 1px solid var(--rule);
}
.roles > div:last-child { border-bottom: 1px solid var(--rule); }
.roles dt {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.6rem;
  font-family: var(--mono);
  font-size: var(--fs-body);
  font-weight: 700;
  letter-spacing: -0.01em;
}
.behaviour {
  font-family: var(--mono);
  font-size: var(--fs-label);
  font-weight: 700;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: var(--phase-pos);
}
.roles dd { margin: 0.3rem 0 0; max-width: 38rem; }

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

  <section aria-labelledby="roles-heading">
    <h2 id="roles-heading">Four roles, one per table</h2>
    <dl class="roles">
      <div>
        <dt>identity <span class="behaviour">deduped</span></dt>
        <dd>
          One row per distinct species, reaction or transition state, however
          often submitted.
        </dd>
      </div>
      <div>
        <dt>provenance <span class="behaviour">append-only</span></dt>
        <dd>
          The software, level of theory and literature behind a number, never
          rewritten.
        </dd>
      </div>
      <div>
        <dt>result <span class="behaviour">append-only</span></dt>
        <dd>
          The number itself. A better calculation adds a row; nothing already
          cited changes.
        </dd>
      </div>
      <div>
        <dt>curation <span class="behaviour">overlay</span></dt>
        <dd>
          Review state and release selections sit on top of results, never
          mutating them.
        </dd>
      </div>
    </dl>
  </section>

  <section aria-labelledby="exchange-heading">
    <h2 id="exchange-heading">What a check looks like</h2>
    <figure class="exchange-figure">
      <div class="exchange">
        <div class="panel deposit">
          <p class="panel-label">Deposited</p>
          <pre>__HERO_XYZ__</pre>
          <p class="panel-sub">frequencies / cm&#8315;&#185;</p>
          <pre>__HERO_FREQUENCIES__</pre>
        </div>
        <div class="panel verdict">
          <p class="panel-label">TCKDB replies</p>
          <code class="verdict-code">__HERO_CODE__</code>
          <p class="verdict-body">
            Linear geometry: 3N&minus;5 = 4 vibrations, but the list carries
            three &mdash; a degenerate bend, reported once by a de-duplicating
            parser. The record is accepted and flagged, not refused.
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
    var encoded = encodeURIComponent(ref);
    if (availability.has_thermo) {
      links.appendChild(anchor(THERMO + "?species_entry_ref=" + encoded, "pill", "thermo"));
    }
    if (availability.has_statmech) {
      links.appendChild(anchor(ENTRY + encoded + "/statmech", "pill", "statmech"));
    }
    if (availability.has_transport) {
      links.appendChild(anchor(ENTRY + encoded + "/transport", "pill", "transport"));
    }
    if (availability.has_conformers) {
      links.appendChild(anchor(CONFORMERS + "?species_entry_ref=" + encoded, "pill", "conformers"));
    }
    if (availability.calculation_count) {
      links.appendChild(anchor(
        CALCULATIONS + "?species_entry_ref=" + encoded,
        "pill",
        plural(availability.calculation_count, "calculation", "calculations")));
    }
    if (!links.firstChild) {
      links.appendChild(make("span", "pill pill-none", "no products yet"));
    }
    node.appendChild(links);
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
    refs.appendChild(anchor(searchUrl("species_ref", record.species_ref), null,
      record.species_ref || "record"));
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
      more.appendChild(anchor(searchUrl(field, value), null, "Open the full response"));
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
    frequencies = "   ".join(f"{value:.1f}" for value in HERO_FREQUENCIES)
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
        .replace("__HERO_XYZ__", HERO_XYZ)
        .replace("__HERO_FREQUENCIES__", frequencies)
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
    "HERO_XYZ",
    "ORIGIN_PLACEHOLDER",
    "REDOC_PATH",
    "REPO_URL",
    "SEARCH_EXAMPLES",
    "SEARCH_FIELDS",
    "SEED_FIELD",
    "SEED_VALUE",
    "SPECIES_SEARCH_PATH",
    "landing_router",
    "render_landing_page",
]
