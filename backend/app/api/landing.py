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

There are two such searches -- species and reactions -- behind a
switch rather than stacked, because a reader who wants the second
should not have to scroll past a page of the first one's results to
find it. The switch is built by the script; with scripting off both
panels are simply on the page, each under its own heading, each a
working ``<form method="get">``.

A reaction query is a **set per side**, and that governs the design of
the whole reaction control. ``?reactants=A&reactants=B`` asks for the
reactions whose reactants include both A and B; ``?reactants=A,B``
asks for a species whose name is the three characters ``A,B``, which
is not an error and matches nothing -- the worst answer an API can
give. So no part of this page ever holds a side as one joined string:
the markup is one ``<input>`` per species, the add button appends
another input, and both the script's URL builder and
:func:`reaction_query` emit one parameter per element. The
``<noscript>`` forms carry repeated same-named inputs, which a plain
HTML form submits as repeated parameters with no script at all.

Leading somewhere is also a claim about where. Every product a result
has expands *in place* into the few fields that say what is in it,
rather than navigating to the nested JSON document the endpoint
answers with -- correct for a program, a wall of keys for a person who
clicked a link on a landing page. The raw document is still one click
away from inside each panel, under a label that says it is raw JSON.
Nothing on this page takes a reader somewhere they did not expect.

A result is rendered as a page, not as a payload with better fonts.
That distinction is the difference between a landing page and a JSON
viewer, and it comes down to four things the earlier version of these
cards got wrong. Field names were column names: ``inchi_key``,
``h298_kj_mol``, ``multiplicity``. Units were either absent or spelled
into the label, which is what a fixed-unit column name is *for* --
``h298_kj_mol`` carries kJ/mol in its spelling precisely so the reader
can be shown the unit properly, and printing the column name shows
them neither. Values were printed at whatever precision a double
happened to hold. And everything -- molecule, charge, provenance,
public ref -- was set at one size, which leaves the reader to supply
the hierarchy themselves.

So: the molecule or the equation is the only thing on a card set
larger than body text; charge and spin state qualify it and sit with
it; the quantities a panel was opened for are drawn with their units
beside them; provenance is quieter and comes after; and the public
ref is last, smallest, monospace and click-to-copy -- present because
it is what a citation needs, demoted because it is not what a reader
reads. A value the record does not carry says so rather than
vanishing, since a skipped row is indistinguishable from a row nobody
thought to show.

The chemistry the page renders it renders properly and stops there.
Multiplicity gains its spin word (2S+1, so 2 is a doublet) *beside*
the number rather than instead of it; charge gains a sign and a real
U+2212; a formula gains subscripts only when it round-trips through
the reconstruction, and is printed exactly as it arrived when it does
not. Nothing is inferred past that: no record is called a radical, a
transition state or anything else the record does not itself say, and
no quantity is derived from another.

An expanded panel also says **how far its evidence has been checked**,
which is the claim that distinguishes this database from a folder of
output files. Each card carries the deterministic rubric's verdict --
its label, its passed-of-possible check counts, and the named checks
that are and are not present, collapsed -- alongside, and never in
place of, the record's human review state. The verdict is opt-in and
is asked for by name: ``include=trust`` is deliberately outside
``include=all`` on every surface, so the convenience token would pay
for a large eager-load chain and deliver no verdict at all. The two
lists that do not offer the token are not asked, and say so.

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
from app.services.trust.rubrics import RUBRIC_REGISTRY

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

#: The other read endpoint the hero searches. Public, no key, same
#: origin, exactly like the species one.
REACTION_SEARCH_PATH = "/api/v1/scientific/reactions/search"

#: The two sides of a reaction search, as ``(query parameter, label,
#: singular noun)``. Both are **repeated** parameters: the endpoint
#: reads ``?reactants=A&reactants=B`` as the set {A, B} and
#: ``?reactants=A,B`` as one species literally named ``A,B``, which
#: matches nothing. Every URL this page builds -- in the markup, in the
#: script and in the ``curl`` example -- therefore emits one parameter
#: per species and never joins them.
REACTION_SIDES = (
    ("reactants", "Reactants", "Reactant"),
    ("products", "Products", "Product"),
)

#: What the reaction boxes are pre-filled with. Two-sided on purpose:
#: it returns records, it shows the shape of the question, and it means
#: the plain form has no empty field to submit (see
#: ``REACTION_SIDES`` and the ``required`` attributes in the template).
SEED_REACTANTS = ("NN",)
SEED_PRODUCTS = ("[H][H]",)

#: Offered under the reaction boxes and again in its empty state, as
#: ``(reactants, products)``. The middle one constrains only products
#: and carries two of them, so the example a reader is most likely to
#: click is itself the repeated-parameter form.
REACTION_EXAMPLES = (
    (("NN",), ()),
    ((), ("[H][H]", "N=N")),
    (("[NH2]", "[NH2]"), ()),
)

#: How many reaction rows the script renders before handing off to the
#: raw document. One chemical reaction can have many entries -- the
#: same equation deposited by several submissions -- so an uncapped
#: render answers a landing-page search with a column of near-identical
#: cards. The count above them is the endpoint's own total, so the cap
#: shortens the reading and never the answer.
REACTION_RENDER_LIMIT = 10

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

#: Tokens inside a check name that are initialisms rather than words.
#:
#: **Casing only.** Every entry is the same token the API sent, spelled the
#: way this project spells it everywhere else -- ``ts`` is a transition
#: state, ``lot`` a level of theory, ``sp`` a single point. Nothing here
#: expands an abbreviation: ``opt`` and ``freq`` are shortenings a reader
#: reads without help, so they arrive and leave as ``opt`` and ``freq``. A
#: token with no entry is printed exactly as it arrived, which is why an
#: incomplete table here is safe and a wrong entry is not.
TRUST_CHECK_INITIALISMS: dict[str, str] = {
    "irc": "IRC",
    "lj": "LJ",
    "lot": "LOT",
    "nasa": "NASA",
    "scf": "SCF",
    "smiles": "SMILES",
    "sp": "SP",
    "ts": "TS",
}

#: The word a check name ends in when the name's whole claim is presence.
_PRESENT = "present"


def trust_check_label(name: str) -> str:
    """Return how a rubric check's name is printed under an outcome heading.

    The panel groups checks under "present" and "not present" and then
    printed the raw name, so a reader got the outcome twice --
    "present: charge present" -- and, under the other heading, an
    apparent contradiction: "not present: path search evidence present".
    The suffix is redundant *because the heading states the outcome*, so
    the redundancy is removed here, on the page, and nowhere else. The
    API still names the check ``charge_present``; ``evidence.checks``
    is untouched; no vocabulary is renamed.

    **A trailing ``_present`` is dropped only when no other ``present``
    remains.** Measured over the registry: 100 checks, 66 ending in the
    word, 63 shortened. The three held back are the conditionals that
    also end in it -- ``dipole_source_present_if_dipole_present`` reads
    "a dipole source is present *if a dipole is present*", one of twelve
    checks whose name carries a condition. Their second presence is a **condition**, not the outcome
    the heading states, so stripping it would leave "dipole source
    present if dipole", which is broken English and a changed claim. One
    wrong entry is worse than twelve slightly long ones, so the whole
    name is kept whenever the stem still says ``present``.

    The comparison is over whole tokens, never a substring. The word
    ``present`` is spelled inside the word "representation", so a
    substring guard would decide that
    ``at_least_one_thermo_representation_present`` is conditional and
    keep a suffix that really is redundant.

    A name that makes no presence claim at all (``multiplicity_valid``,
    ``ts_status_recorded``) loses nothing -- the heading does not repeat
    it, so there is nothing to remove.

    What is left is spaced and sentence-cased, the same transcription the
    page gives every other underscored token, with the initialisms in
    :data:`TRUST_CHECK_INITIALISMS` cased. No word is ever expanded,
    translated or invented.
    """
    tokens = [token for token in name.split("_") if token]
    if not tokens:
        return name
    if tokens[-1] == _PRESENT and _PRESENT not in tokens[:-1] and len(tokens) > 1:
        tokens = tokens[:-1]
    words = [TRUST_CHECK_INITIALISMS.get(token, token) for token in tokens]
    label = " ".join(words)
    if label[:1].islower():
        label = label[0].upper() + label[1:]
    return label


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
/*
 * ``max-width`` and ``flex-wrap`` because a fallback form can carry
 * two fields: two inputs at their default size plus a button is wider
 * than a 360px screen, and an <input> does not shrink below its size
 * attribute unless it is told it may.
 */
.fallback form {
  display: inline-flex;
  flex-wrap: wrap;
  max-width: 100%;
  gap: 0.35rem;
  margin: 0 0.5rem 0.4rem 0;
}
.fallback input { flex: 1 1 8rem; min-width: 0; }
.fallback input, .fallback button {
  font-family: var(--mono);
  font-size: var(--fs-data);
  padding: 0.4rem 0.55rem;
  border: 1px solid var(--rule);
  background: var(--paper);
  color: var(--ink);
}
.fallback-label {
  display: block;
  font-family: var(--mono);
  font-size: var(--fs-label);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0.6rem 0 0.2rem;
}

/* ---- the species / reactions switch ---- */
/*
 * Two searches, one hero. Stacking them would push the second below a
 * page of the first one's results, and the second is the one this
 * deployment was most recently asked for; the switch keeps both at the
 * top and the page the length it was.
 *
 * The buttons ship ``hidden`` and the script unhides them, because a
 * tab that cannot change what is displayed is worse than no tab. With
 * scripting off there is no switch and both panels are simply on the
 * page, each under its own heading, each a working form.
 */
.modes {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin: 1.1rem 0 0;
}
/*
 * ``display: flex`` above beats the user agent's ``[hidden]`` rule, so
 * without this the switch would be visible to exactly the readers who
 * cannot use it.
 */
.modes[hidden] { display: none; }
.mode {
  font-family: var(--mono);
  font-size: var(--fs-data);
  font-weight: 700;
  letter-spacing: -0.01em;
  padding: 0.5rem 1rem;
  min-height: 2.5rem;
  cursor: pointer;
  border: 1.5px solid var(--rule);
  background: var(--surface);
  color: var(--phase-pos);
}
.mode[aria-selected="true"] {
  background: var(--phase-pos);
  color: var(--surface);
  border-color: var(--phase-pos);
}
.search-panel + .search-panel { margin-top: 2rem; }

/* ---- reaction search ---- */
/*
 * One input per species, never one input holding several. The endpoint
 * reads repeated ``reactants=`` as a set and a comma-joined string as
 * one species named after the whole string, so the control that
 * collects them is a list of fields and the "add" button appends
 * another real field rather than a separator to a shared box.
 */
.rxn-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.5rem 0.75rem;
}
.rxn-side { flex: 1 1 11rem; min-width: 0; }
.rxn-label {
  display: block;
  font-family: var(--mono);
  font-size: var(--fs-label);
  font-weight: 700;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 0.3rem;
}
.rxn-inputs { display: flex; flex-wrap: wrap; gap: 0.4rem; }
.rxn-inputs input { flex: 1 1 7rem; }
/*
 * Narrow first: the two sides cannot sit side by side inside 360px, so
 * the arrow takes its own centred line between them rather than being
 * squeezed onto the end of the reactants row, where it reads as a
 * label for that box instead of as the relation between two.
 */
.rxn-arrow {
  flex: 1 0 100%;
  text-align: center;
  font-family: var(--mono);
  font-size: var(--fs-data);
  color: var(--muted);
  padding: 0.1rem 0;
}
@media (min-width: 34rem) {
  .rxn-arrow {
    flex: 0 0 auto;
    text-align: left;
    padding: 0 0 0.7rem;
  }
}
/*
 * Higher specificity than ``.search button`` on purpose -- that rule is
 * the big filled submit button, and this one is a quiet secondary
 * control sitting under the fields it adds to.
 */
.search .rxn-add {
  margin-top: 0.4rem;
  font-family: var(--mono);
  font-size: var(--fs-label);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  font-weight: 700;
  cursor: pointer;
  padding: 0.3rem 0.55rem;
  min-height: 0;
  border: 1px solid var(--rule);
  background: var(--paper);
  color: var(--phase-pos);
}
.search .rxn-add:hover { border-color: var(--phase-pos); }

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
/*
 * ---- the reading order of a result ----
 *
 * Four levels, and the sizes are the whole argument. The molecule or
 * the equation is what the reader came for and is the only thing on a
 * card set larger than body text. The state it is in -- charge, spin
 * -- qualifies the structure and sits with it. Provenance is quieter
 * and comes after. Identifiers are last and smallest: an InChIKey and
 * a public ref are what a machine and a citation need, they must be
 * on the card, and they must never be the first thing the eye lands
 * on. Given equal weight, as they had, a reader has to supply the
 * hierarchy themselves -- which is the work a JSON viewer leaves them
 * to do, and the reason a page full of real data still read as one.
 */
.record-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.4rem 1.25rem;
}
.molecule {
  margin: 0;
  max-width: none;
  flex: 1 1 12rem;
  min-width: 0;
}
.smiles,
.equation {
  font-family: var(--mono);
  font-size: 1.25rem;
  font-weight: 700;
  letter-spacing: -0.01em;
  overflow-wrap: anywhere;
}
.state {
  display: flex;
  flex-wrap: wrap;
  gap: 0.2rem 1.25rem;
}
.state-cell {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.state-label,
.quantity-label,
.group-label,
.idents dt {
  font-family: var(--mono);
  font-size: var(--fs-label);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
.state-value {
  font-size: var(--fs-data);
  font-weight: 700;
  overflow-wrap: anywhere;
}
.state-aside {
  font-family: var(--mono);
  font-weight: 400;
  color: var(--muted);
  margin-left: 0.3rem;
}
/*
 * A value the record does not carry is drawn, not skipped. It is set
 * in the body face rather than the data face precisely so that it
 * cannot be misread as a value: "not recorded" is a sentence about
 * the record, not a reading from it.
 */
.absent {
  font-family: var(--sans);
  font-style: italic;
  font-weight: 400;
  color: var(--muted);
}
.state-value.absent,
.quantity-value.absent { font-size: var(--fs-data); }
/*
 * Identifiers: present, monospace, small, copyable, secondary.
 */
.idents {
  display: grid;
  grid-template-columns: minmax(0, auto) minmax(0, 1fr);
  gap: 0.1rem 0.75rem;
  margin: 0.7rem 0 0;
  align-items: baseline;
}
.idents dd {
  margin: 0;
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.3rem;
  font-size: var(--fs-label);
}
/*
 * A ref is twenty-eight characters of base32 and a label column takes
 * a third of a 360px screen away from it, breaking every ref across
 * two lines. Below that width the label goes above its value instead.
 */
@media (max-width: 29rem) {
  .idents { grid-template-columns: minmax(0, 1fr); gap: 0; }
  .idents dt { margin-top: 0.4rem; }
}
.ident-value,
.formula {
  font-family: var(--mono);
  font-size: var(--fs-data);
  color: var(--muted);
  overflow-wrap: anywhere;
}
.formula { color: var(--ink); }
.copy {
  font-family: var(--mono);
  font-size: var(--fs-label);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--phase-pos);
  background: transparent;
  border: 1px solid var(--rule);
  padding: 0.05rem 0.35rem;
  line-height: 1.6;
  cursor: pointer;
}
.copy:hover { border-color: var(--phase-pos); }
.record-raw {
  margin: 0.85rem 0 0;
  max-width: none;
  font-size: var(--fs-data);
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
  font-size: var(--fs-data);
  font-weight: 700;
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
  font-size: var(--fs-body);
  font-weight: 700;
  letter-spacing: -0.01em;
  overflow-wrap: anywhere;
}
/*
 * The quantities a reader opened the panel for. The label sits above
 * its value rather than beside it, so a row of them reads as
 * measurements rather than as a table of keys, and the unit is its own
 * element at its own weight -- never spelled into the label, which is
 * what a column name like ``h298_kj_mol`` does and why it reads as
 * machine output.
 */
.quantities {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1.75rem;
  margin-top: 0.6rem;
}
.quantity {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.quantity-value {
  font-family: var(--mono);
  font-size: 1.0625rem;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.quantity-value .unit { font-size: var(--fs-data); font-weight: 400; }
.unit {
  color: var(--muted);
  margin-left: 0.35rem;
}
sub, sup { line-height: 0; font-size: 0.75em; }
.group-label { margin: 0.8rem 0 0; max-width: none; }
.fields {
  display: grid;
  grid-template-columns: minmax(0, auto) minmax(0, 1fr);
  gap: 0.15rem 0.8rem;
  margin: 0.5rem 0 0;
  font-size: var(--fs-data);
  align-items: baseline;
}
.fields dt { color: var(--muted); }
.fields dd {
  margin: 0;
  font-family: var(--mono);
  overflow-wrap: anywhere;
}
.fields-quiet { margin-top: 0.25rem; }
.fields-quiet dd { color: var(--muted); }
.detail-card .idents { margin-top: 0.8rem; }
/*
 * ---- how far the evidence behind a record has been checked ----
 *
 * Two independent facts share one card and must never be read as one.
 * The badge in the card head says whether a *person* has reviewed the
 * record. This block says how much of the evidence a deterministic
 * rubric expects is actually present. A record is routinely
 * "well supported" and "under review" at the same time, so neither is
 * ever drawn as a stand-in for the other.
 *
 * Every grade is drawn in the same neutral chip, on purpose. The API
 * publishes a *set* of named verdicts -- well_supported,
 * mostly_supported, partial, sparse, unsupported, hard_failed -- and
 * does not publish a rank, a score or a severity. Painting them red to
 * green, or as stars, would assert an ordering that is not in the
 * contract. The word is the verdict; the named checks inside the
 * disclosure are the reason for it.
 */
.trust {
  margin-top: 0.6rem;
  font-size: var(--fs-data);
}
.trust-line {
  margin: 0;
  max-width: none;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.25rem 0.5rem;
}
.trust-label {
  font-size: var(--fs-label);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
.trust-grade {
  font-family: var(--mono);
  font-weight: 700;
  border: 1px solid var(--rule);
  background: var(--data-bg);
  padding: 0.05rem 0.45rem;
}
.trust-tally,
.trust-rubric,
.trust-none {
  font-family: var(--mono);
  color: var(--muted);
  overflow-wrap: anywhere;
}
.trust-none { margin: 0; max-width: none; }
.trust-why { margin-top: 0.45rem; }
.trust-why > summary {
  cursor: pointer;
  color: var(--phase-pos);
  width: fit-content;
}
.trust-checks { margin-top: 0.5rem; }
.trust-checks-title {
  margin: 0;
  max-width: none;
  font-size: var(--fs-label);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
.trust-check-list {
  margin: 0.15rem 0 0;
  padding-left: 1.15rem;
  font-family: var(--mono);
}
.trust-check-list li { overflow-wrap: anywhere; }
.trust-explains {
  margin: 0.5rem 0 0;
  max-width: none;
  color: var(--muted);
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
    <h2 id="search-heading">Search</h2>
    <p class="note">
      Records under review are served, not withheld; every result says how
      far it has been checked.
    </p>

    <div class="modes" id="modes" hidden>
      <button type="button" class="mode" id="mode-species"
              aria-controls="panel-species">Species</button>
      <button type="button" class="mode" id="mode-reactions"
              aria-controls="panel-reactions">Reactions</button>
    </div>

    <div class="search-panel" id="panel-species" aria-labelledby="title-species">
    <h3 class="panel-title" id="title-species">Species</h3>

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
    </div>

    <div class="search-panel" id="panel-reactions" aria-labelledby="title-reactions">
    <h3 class="panel-title" id="title-reactions">Reactions</h3>
    <p class="note">
      Either side alone works. <em>Matched in reverse</em> is the equation
      read right to left.
    </p>

    <form id="rxn-form" class="search" method="get" action="__REACTION_SEARCH_PATH__">
      <div class="rxn-row">
        <div class="rxn-side" role="group" aria-labelledby="rxn-reactants-label">
          <span class="rxn-label" id="rxn-reactants-label">Reactants</span>
          <div class="rxn-inputs" id="rxn-reactants">
__SEED_REACTANT_INPUTS__
          </div>
          <button type="button" class="rxn-add" id="rxn-add-reactants" hidden>+ reactant</button>
        </div>
        <span class="rxn-arrow" aria-hidden="true">&lt;=&gt;</span>
        <div class="rxn-side" role="group" aria-labelledby="rxn-products-label">
          <span class="rxn-label" id="rxn-products-label">Products</span>
          <div class="rxn-inputs" id="rxn-products">
__SEED_PRODUCT_INPUTS__
          </div>
          <button type="button" class="rxn-add" id="rxn-add-products" hidden>+ product</button>
        </div>
        <button type="submit">Search</button>
      </div>
    </form>

    <p class="examples" id="rxn-examples">
      <span>Try</span>
__REACTION_EXAMPLE_CHIPS__
    </p>

    <noscript>
      <div class="fallback">
        <p>
          JavaScript is off, so the form above submits as a plain form and
          needs both sides filled. These ask about one side, and about two
          species on one side:
        </p>
__REACTION_FALLBACK_FORMS__
      </div>
    </noscript>

    <div id="rxn-results" class="results" aria-live="polite">
      <!--
        Shorter than the species one on purpose: the reader who needs
        the "without JavaScript this goes to the API's JSON" sentence
        is the reader whose browser is about to render the
        ``<noscript>`` block above, which says it.
      -->
      <p class="results-status">Results land here.</p>
    </div>
    </div>
  </section>

  <section aria-labelledby="quickstart-heading">
    <h2 id="quickstart-heading">Quickstart</h2>
    <p>
      Everything machine-readable is under <code>/api/v1</code>. Reads are
      open; depositing needs an account.
    </p>
    <pre class="command"><code>TCKDB=<span id="curl-origin">__ORIGIN_PLACEHOLDER__</span>
curl -sg "$TCKDB__SEARCH_PATH__?smiles=[OH]"
curl -sg "$TCKDB__REACTION_SEARCH_PATH__?products=[H][H]&amp;products=N=N"</code></pre>
    <p class="note">
      <code>-g</code> so bracketed SMILES like <code>[OH]</code> are not read
      as shell globs, and one <code>products=</code> per species: a
      comma-joined list is one species name, and matches nothing.
    </p>
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

  var rxnForm = doc.getElementById("rxn-form");
  var rxnOut = doc.getElementById("rxn-results");

  var SEARCH = "__SEARCH_PATH__";
  var ENTRY = "/api/v1/scientific/species-entries/";
  var THERMO = "/api/v1/scientific/thermo/search";
  var CONFORMERS = "/api/v1/scientific/conformers/search";
  var CALCULATIONS = "/api/v1/scientific/species-calculations/search";
  var RXN_SEARCH = "__REACTION_SEARCH_PATH__";
  var KINETICS = "/api/v1/scientific/reaction-entries/";
  var TRANSITION_STATES = "/api/v1/scientific/transition-states/search";

  var PLACEHOLDERS = __PLACEHOLDERS_JSON__;
  var EXAMPLES = __EXAMPLES_JSON__;
  var RXN_EXAMPLES = __REACTION_EXAMPLES_JSON__;
  var RXN_LIMIT = __REACTION_RENDER_LIMIT__;
  /* ``[[query parameter, singular noun], ...]``, in form order. */
  var RXN_SIDES = __REACTION_SIDES_JSON__;
  var SIDE_WORDS = {};
  for (var sideIndex = 0; sideIndex < RXN_SIDES.length; sideIndex += 1) {
    SIDE_WORDS[RXN_SIDES[sideIndex][0]] = RXN_SIDES[sideIndex][1];
  }
  var REVIEW_WORDS = {
    approved: "approved",
    under_review: "under review",
    not_reviewed: "not reviewed",
    deprecated: "deprecated",
    rejected: "rejected"
  };
  /*
   * ``matched_direction`` says which way round the equation had to be
   * read for the query to match it, and "reverse" is information a
   * reader needs: their reactants are that reaction's products. It is
   * on every row rather than only on the reverse ones, so that its
   * absence never has to be interpreted.
   */
  var DIRECTION_WORDS = {
    forward: "matched forward",
    reverse: "matched in reverse",
    either: "matched either way"
  };
  /*
   * ---- the trust verdict -------------------------------------------
   *
   * What a record *has* is one question; how far what it has has been
   * checked is another, and the second one is the reason this database
   * exists rather than a folder of output files. The read API answers
   * it under ``trust`` on the record.
   *
   * It is opt-in and is spelled out. ``include=trust`` is deliberately
   * NOT part of ``include=all`` on any surface, because evaluating a
   * verdict pulls a large eager-load chain -- nine to twenty-three
   * entries depending on the surface, up to four hops, one of them
   * rooted on a collection. A page that reached for the convenience
   * token would buy that whole graph on every panel, including the
   * panels that cannot show a verdict at all. So every URL below names
   * the one token it wants, and none of them names ``all``.
   *
   * Two surfaces the page already opens -- the conformer list and the
   * calculation list -- do not offer the token (asking answers
   * ``unknown_include_token``). Those panels say the verdict is not
   * assessed there rather than implying a bad one.
   */
  var TRUST_PARAM = "include=trust";

  /*
   * The rubric's own label set, given readable wording and nothing
   * else. These are the six values of ``EvidenceBadge`` with the
   * underscores taken out: no stars, no percentage, no traffic light.
   * The API publishes named verdicts, not a scale, and a page that
   * invented one would be asserting an ordering the contract does not
   * make. Anything unrecognised is printed as it arrived rather than
   * being bucketed into the nearest known word.
   */
  var TRUST_WORDS = {
    well_supported: "well supported",
    mostly_supported: "mostly supported",
    partial: "partial",
    sparse: "sparse",
    unsupported: "unsupported",
    hard_failed: "hard failed"
  };

  /*
   * How each named check is printed, keyed by the name the API sends.
   * Built on the server from the rubric registry itself -- the same
   * declarations that produce ``evidence.checks`` -- so a check added
   * tomorrow arrives here with its label already written, and the suite
   * fails if one ever does not.
   *
   * The labels drop a trailing "present" (the heading above the list
   * already says "present" or "not present", and saying it twice reads
   * as "present: charge present", or worse, as the contradiction "not
   * present: path search evidence present"). A conditional check keeps
   * both of its presences, because the second one is the condition and
   * not the outcome. The rule, and why it is trailing-only, is written
   * out at ``trust_check_label`` in this module.
   */
  var CHECK_WORDS = __CHECK_WORDS_JSON__;
  /*
   * Absence is a third state and reads as one. A list that carries no
   * verdict, and a record whose verdict did not arrive, are both "not
   * assessed" -- never a low grade, which is what silence would look
   * like next to the cards that do carry one.
   */
  var TRUST_NOT_ASSESSED = "evidence completeness not assessed for this record";
  var TRUST_NOT_ON_LIST =
    "This list does not carry an evidence verdict, so how far these " +
    "records have been checked is not assessed here.";
  var TRUST_VS_REVIEW =
    "This verdict counts evidence; it is not a review. The badge above " +
    "says whether a person has looked at the record. The two are " +
    "independent: a well supported record can be under review, and on " +
    "this deployment most are.";
  var MODES = [
    ["mode-species", "panel-species", "title-species", function () {}],
    ["mode-reactions", "panel-reactions", "title-reactions", submitRxn]
  ];

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

  /*
   * ---- the public ref ----------------------------------------------
   *
   * ``spc_atp56uqux2ajao7hvckx7gx7ca`` is what a paper cites and what
   * a query takes, and it is not something anyone reads. It belongs on
   * the card -- leaving it off would make the record uncitable -- but
   * small, monospace and below the thing it identifies, never
   * competing with the molecule for the eye.
   *
   * It is also the one string on a card a reader actually wants to
   * lift out, and selecting thirty characters of base32 by hand is
   * where that goes wrong. Hence the button. Everything here is
   * script-built, like the rest of the results, so there is nothing
   * for a scripting-off reader to find broken.
   */
  function selectContents(node) {
    var selection = window.getSelection ? window.getSelection() : null;
    if (!selection || !doc.createRange) { return false; }
    var range = doc.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  }

  function copyButton(text, what, target) {
    var button = make("button", "copy", "copy");
    button.type = "button";
    button.setAttribute("aria-label", "Copy the " + what);
    var settle = function (word) {
      button.textContent = word;
      window.setTimeout(function () { button.textContent = "copy"; }, 1500);
    };
    button.addEventListener("click", function () {
      /*
       * The clipboard API is unavailable outside a secure context, so
       * the fallback selects the ref instead and says it did. A button
       * that quietly does nothing would be worse than no button.
       */
      if (window.navigator && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
          settle("copied");
        }, function () {
          settle(selectContents(target) ? "selected" : "select it");
        });
        return;
      }
      settle(selectContents(target) ? "selected" : "select it");
    });
    return button;
  }

  /*
   * ``[["InChIKey", value, copyable], ...]``. A missing identifier
   * says it is missing: a card that shows an InChIKey for one record
   * and nothing at all for the next leaves the reader unable to tell
   * "this record has none" from "this page forgot".
   */
  function identList(rows) {
    var list = make("dl", "idents");
    for (var i = 0; i < rows.length; i += 1) {
      var label = rows[i][0];
      var value = rows[i][1];
      var cell = make("dd", null);
      if (value === null || value === undefined || value === "") {
        cell.className = "absent";
        cell.appendChild(doc.createTextNode(ABSENT));
      } else if (typeof value === "object") {
        cell.appendChild(value);
      } else {
        var code = make("code", "ident-value", value);
        cell.appendChild(code);
        if (rows[i][2]) { cell.appendChild(copyButton(value, label, code)); }
      }
      list.appendChild(make("dt", null, label));
      list.appendChild(cell);
    }
    return list;
  }

  /*
   * A molecular formula, with the counts as subscripts -- but only
   * when the string is unambiguously a sequence of element symbols and
   * counts. The reconstruction is compared against the original and
   * anything that does not round-trip is printed exactly as it
   * arrived. A formula rendered wrongly is far worse than a formula
   * rendered plainly, and this page does not guess at chemistry.
   */
  function formulaNode(formula) {
    if (!formula) { return null; }
    var text = String(formula);
    var node = make("span", "formula");
    var parts = text.match(/[A-Z][a-z]?[0-9]*/g);
    if (!parts || parts.join("") !== text) {
      node.textContent = text;
      return node;
    }
    for (var i = 0; i < parts.length; i += 1) {
      var split = /^([A-Z][a-z]?)([0-9]*)$/.exec(parts[i]);
      node.appendChild(doc.createTextNode(split[1]));
      if (split[2]) { node.appendChild(make("sub", null, split[2])); }
    }
    return node;
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

  /*
   * ---- a value a person reads, not a value a parser reads ----------
   *
   * Three rules separate a rendered page from a JSON viewer with nicer
   * fonts, and all three are about the same thing: a field name is the
   * API's business, not the reader's.
   *
   * A quantity is a *number and a unit*, and the unit belongs on
   * screen in its own element rather than spelled into the label.
   * ``h298_kj_mol`` is a column name that carries its unit in its
   * spelling precisely so that a reader can be shown the unit
   * properly; printing the column name shows them neither the
   * quantity nor the unit.
   *
   * A number is rounded to what the quantity supports. The API answers
   * 143.8942674605864 kJ/mol because that is what a double holds; past
   * the second decimal it is float noise, and printing it is the
   * clearest possible signal that nobody looked at the output.
   *
   * A value that is not there says so. Skipping a null row silently
   * makes "this record does not carry an uncertainty" look identical
   * to "this page never thought to show one", and the reader cannot
   * tell which they are looking at.
   */
  var ABSENT = "not recorded";
  var MINUS = "\u2212";
  /* What the API calls a ``*_ref``, in the words the docs use. */
  var REF_WORD = "Public ref";

  function fixed(value, digits, unit) {
    if (value === null || value === undefined) { return null; }
    return { value: Number(value).toFixed(digits), unit: unit || null };
  }

  /*
   * A pre-exponential factor is 1.2e+13 in the payload and reads as
   * one there. On a page it is 1.2 x 10^13, which is how it is written
   * everywhere else in the field. The mantissa/exponent split is taken
   * from ``toExponential`` rather than from string surgery on the
   * original, so there is no arithmetic here to get wrong; a value
   * that does not match the expected shape falls through to plain
   * digits rather than being reshaped on a guess.
   */
  function scientific(value, digits, unit) {
    if (value === null || value === undefined) { return null; }
    var number = Number(value);
    if (!isFinite(number)) { return null; }
    if (number === 0) { return { value: "0", unit: unit || null }; }
    var magnitude = Math.abs(number);
    if (magnitude >= 1e4 || magnitude < 1e-2) {
      var parts = /^(-?[0-9](?:\\.[0-9]+)?)e([+-][0-9]+)$/.exec(
        number.toExponential(digits - 1));
      if (parts) {
        return {
          value: parts[1],
          exponent: parts[2].charAt(0) === "-" ? MINUS + parts[2].slice(1) : parts[2].slice(1),
          unit: unit || null
        };
      }
    }
    return { value: String(Number(number.toPrecision(digits))), unit: unit || null };
  }

  /*
   * Multiplicity is 2S+1, and the word is what a chemist reads first:
   * "doublet" says one unpaired electron in a way that "2" does not.
   * The number stays next to the word rather than being replaced by
   * it -- the number is what the API carries and what a reader types
   * back into a query -- so this adds a reading instead of removing
   * one. Nothing beyond the arithmetic is claimed here: the page does
   * not go on to call a doublet a radical, because the record does not
   * say that and this page does not infer chemistry.
   */
  var SPIN_WORDS = {
    "1": "singlet",
    "2": "doublet",
    "3": "triplet",
    "4": "quartet",
    "5": "quintet",
    "6": "sextet",
    "7": "septet",
    "8": "octet"
  };

  function spinWord(multiplicity) {
    if (multiplicity === null || multiplicity === undefined) { return null; }
    return SPIN_WORDS[String(multiplicity)] || null;
  }

  /*
   * A charge is a signed quantity and is written as one. The neutral
   * case stays a bare zero; a negative one gets U+2212, not the hyphen
   * that a column of integers would leave there.
   */
  function chargeText(charge) {
    if (charge === null || charge === undefined) { return null; }
    var number = Number(charge);
    if (number > 0) { return "+" + number; }
    if (number < 0) { return MINUS + Math.abs(number); }
    return "0";
  }

  /*
   * ``opt`` and ``freq`` are the API's tokens for jobs that have
   * names. Every member of the calculation-type enum is here and the
   * test suite fails if one is added without a word, because a card
   * titled with a three-letter token is the JSON view again.
   */
  var CALCULATION_WORDS = {
    opt: "geometry optimisation",
    freq: "frequencies",
    sp: "single-point energy",
    irc: "intrinsic reaction coordinate",
    scan: "coordinate scan",
    path_search: "reaction path search",
    conf: "conformer search"
  };

  /*
   * Model names, capitalised as their own literature capitalises them.
   * Every member of the two model enums the read API publishes is
   * here, and the suite fails if one is added without a name, because
   * "nasa" and "sri" are the API's spelling of NASA and SRI and
   * printing them lower case is printing the payload.
   */
  var THERMO_MODEL_WORDS = {
    nasa: "NASA-7 polynomial",
    nasa9: "NASA-9 polynomial",
    wilhoit: "Wilhoit",
    points: "tabulated points",
    scalar: "scalar values only"
  };
  var KINETICS_MODEL_WORDS = {
    arrhenius: "Arrhenius",
    modified_arrhenius: "modified Arrhenius",
    multi_arrhenius: "multiple Arrhenius",
    lindemann: "Lindemann",
    troe: "Troe",
    sri: "SRI",
    plog: "PLOG",
    chebyshev: "Chebyshev"
  };

  /*
   * Arrhenius A units, typeset. Each is a direct transcription of the
   * enum token it is keyed by -- ``cm3_mol_s`` is cubic centimetres
   * per mole per second and nothing else -- so no judgement is being
   * exercised, and the suite checks the enum against this map so a new
   * member cannot arrive without one.
   */
  var A_UNITS = {
    per_s: "s⁻¹",
    cm3_mol_s: "cm³ mol⁻¹ s⁻¹",
    cm3_molecule_s: "cm³ molecule⁻¹ s⁻¹",
    m3_mol_s: "m³ mol⁻¹ s⁻¹",
    cm6_mol2_s: "cm⁶ mol⁻² s⁻¹",
    cm6_molecule2_s: "cm⁶ molecule⁻² s⁻¹",
    m6_mol2_s: "m⁶ mol⁻² s⁻¹"
  };

  /*
   * Anything else the API spells with underscores. This is a
   * transcription, never a translation: ``asymmetric_top`` becomes
   * "asymmetric top" and stops there. Inventing an expansion for a
   * token this page has never seen would be inventing chemistry.
   */
  function words(token) {
    if (token === null || token === undefined || token === "") { return null; }
    return String(token).replace(/_/g, " ");
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

  /*
   * A workflow tool release is the same {name, version} pair under two
   * different key names, so it borrows the label rule above rather
   * than repeating it and drifting from it.
   */
  function toolRelease(release) {
    if (!release) { return null; }
    return { software: release.workflow_tool, version: release.version };
  }

  function levelLabel(level) {
    if (!level) { return null; }
    if (level.label) { return level.label; }
    if (!level.method) { return null; }
    return level.method + (level.basis ? "/" + level.basis : "");
  }

  /*
   * ---- what a card is, once it stops being a field list ------------
   *
   * A thermo record is not fifteen equal fields. It is one or two
   * quantities a reader came for, a model that says over what range
   * they are valid, and a provenance trail that says where they came
   * from -- three different kinds of thing, which a flat key/value
   * list flattens into one. Each view therefore returns:
   *
   *   headline    the quantities, rendered large with their units
   *   facts       what shape the record is and over what range
   *   provenance  what produced it, rendered quiet and last
   *
   * The three groups are what gives a card a reading order. Without
   * them every line is the same size and the reader has to supply the
   * hierarchy themselves, which is exactly the work a JSON viewer
   * leaves them to do.
   */
  function thermoView(record) {
    var thermo = record.thermo || {};
    var coverage = thermo.temperature_coverage || {};
    /*
     * ``provenance`` sits on the thermo half of the row, not on the
     * row: ``ThermoSearchRecord`` is ``{species, thermo}`` and the
     * block is ``ThermoRecord.provenance``. Read off the row it is
     * always undefined, so the level of theory silently never
     * appeared -- the failure mode this page now refuses to have.
     */
    var provenance = thermo.provenance || {};
    var primary = provenance.primary_calculation || {};
    var span = null;
    if (coverage.record_min_k !== null && coverage.record_min_k !== undefined
      && coverage.record_max_k !== null && coverage.record_max_k !== undefined) {
      span = {
        value: Number(coverage.record_min_k).toFixed(0) + "\u2013"
          + Number(coverage.record_max_k).toFixed(0),
        unit: "K"
      };
    }
    return {
      title: "Thermodynamic properties",
      ref: thermo.thermo_ref,
      status: thermo.review ? thermo.review.status : null,
      headline: [
        ["ΔH°(298 K)", fixed(thermo.h298_kj_mol, 2, "kJ/mol")],
        ["S°(298 K)", fixed(thermo.s298_j_mol_k, 2, "J/mol·K")]
      ],
      facts: [
        ["Model", THERMO_MODEL_WORDS[thermo.model_kind] || words(thermo.model_kind)],
        ["Valid over", span],
        ["Uncertainty in ΔH°", fixed(thermo.h298_uncertainty_kj_mol, 2, "kJ/mol")],
        ["Uncertainty in S°", fixed(thermo.s298_uncertainty_j_mol_k, 2, "J/mol·K")]
      ],
      provenance: [
        ["Level of theory",
          levelLabel(primary.level_of_theory || provenance.level_of_theory)],
        ["Software", softwareLabel(primary.software || provenance.software)],
        ["Origin", words(thermo.scientific_origin)]
      ]
    };
  }

  function statmechView(record) {
    var statmech = record.statmech || {};
    var linear = statmech.is_linear;
    var release = record.software_release || {};
    return {
      title: "Statistical-mechanics treatment",
      ref: statmech.statmech_ref,
      status: statmech.review ? statmech.review.status : null,
      headline: [
        ["Point group", statmech.point_group],
        ["Symmetry number", statmech.external_symmetry]
      ],
      facts: [
        ["Treatment", words(statmech.statmech_treatment)],
        ["Rotor", words(statmech.rigid_rotor_kind)],
        ["Linear", linear === null || linear === undefined ? null : (linear ? "yes" : "no")],
        ["Optical isomers", statmech.optical_isomers],
        ["Frequency scale factor", fixed(statmech.frequency_scale_factor_value, 4)]
      ],
      provenance: [
        ["Software", softwareLabel(release)],
        ["Origin", words(statmech.scientific_origin)]
      ]
    };
  }

  function transportView(record) {
    var transport = record.transport || {};
    var evidence = record.evidence_summary || {};
    return {
      title: "Transport properties",
      ref: transport.transport_ref,
      status: transport.review ? transport.review.status : null,
      headline: [
        ["Collision diameter \u03C3", fixed(transport.sigma_angstrom, 3, "Å")],
        ["Well depth ε/k", fixed(transport.epsilon_over_k_k, 1, "K")]
      ],
      facts: [
        ["Dipole moment", fixed(transport.dipole_debye, 3, "D")],
        ["Calculations behind it", evidence.source_calculation_count]
      ],
      provenance: [
        ["Origin", words(transport.scientific_origin)]
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
      headline: [
        ["Deposited as", seen === null || seen === undefined
          ? null
          : plural(seen, "observation", "observations") + " of this one group"]
      ],
      facts: [
        ["Calculations behind it", evidence.calculation_count],
        ["Distinct geometries", evidence.geometry_count]
      ]
    };
  }

  function calculationView(record) {
    var calculation = record.calculation || {};
    var energy = record.energy || {};
    var type = calculation.calculation_type;
    return {
      title: CALCULATION_WORDS[type] || words(type) || "calculation",
      ref: calculation.calculation_ref,
      status: calculation.review ? calculation.review.status : null,
      headline: [
        ["Electronic energy", fixed(energy.energy_hartree, 6, "hartree")]
      ],
      facts: [
        ["Quality", words(calculation.calculation_quality)]
      ],
      provenance: [
        ["Level of theory", levelLabel(record.level_of_theory)],
        ["Software", softwareLabel(record.software_release)],
        ["Workflow tool", softwareLabel(toolRelease(record.workflow_tool_release))]
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
      url: function (ref) {
        return THERMO + "?species_entry_ref=" + encodeURIComponent(ref) + "&" + TRUST_PARAM;
      },
      /*
       * The one surface that nests it. A thermo search row is
       * ``{species, thermo}`` and the verdict rides on the thermo half;
       * everywhere else the fragment sits on the record itself.
       */
      trust: function (record) { return (record.thermo || {}).trust; },
      view: thermoView
    },
    {
      key: "statmech",
      label: "statmech",
      one: "statmech record",
      many: "statmech records",
      shown: function (a) { return !!a.has_statmech; },
      url: function (ref) {
        return ENTRY + encodeURIComponent(ref) + "/statmech?" + TRUST_PARAM;
      },
      trust: function (record) { return record.trust; },
      view: statmechView
    },
    {
      key: "transport",
      label: "transport",
      one: "transport record",
      many: "transport records",
      shown: function (a) { return !!a.has_transport; },
      url: function (ref) {
        return ENTRY + encodeURIComponent(ref) + "/transport?" + TRUST_PARAM;
      },
      trust: function (record) { return record.trust; },
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

  /*
   * One place decides what a value looks like, so a quantity cannot be
   * formatted one way in a headline and another in a list. A value is
   * either a plain string, a ``{value, unit, exponent}`` quantity, or
   * absent -- and absent is a rendering, never a skipped row.
   */
  function fillValue(node, value) {
    if (value === null || value === undefined || value === "") {
      node.className = node.className ? node.className + " absent" : "absent";
      node.appendChild(doc.createTextNode(ABSENT));
      return node;
    }
    if (typeof value === "object") {
      node.appendChild(doc.createTextNode(value.value));
      if (value.exponent) {
        node.appendChild(doc.createTextNode(" \u00D7 10"));
        node.appendChild(make("sup", null, value.exponent));
      }
      if (value.unit) { node.appendChild(make("span", "unit", value.unit)); }
      return node;
    }
    node.appendChild(doc.createTextNode(String(value)));
    return node;
  }

  /*
   * A real ``<dl>``, because these really are term/description pairs
   * and a grid of ``<span>``s would say so to nobody using a screen
   * reader.
   */
  function fieldList(pairs, cls) {
    var list = make("dl", cls || "fields");
    for (var i = 0; i < pairs.length; i += 1) {
      list.appendChild(make("dt", null, pairs[i][0]));
      list.appendChild(fillValue(make("dd", null), pairs[i][1]));
    }
    return list;
  }

  /*
   * The quantities a reader opened the card for, drawn at a size that
   * says so. The label sits above its own value rather than beside it,
   * so a column of them reads as measurements and not as a table of
   * keys.
   */
  function quantityBlock(pairs) {
    var node = make("div", "quantities");
    for (var i = 0; i < pairs.length; i += 1) {
      var cell = make("div", "quantity");
      cell.appendChild(make("span", "quantity-label", pairs[i][0]));
      cell.appendChild(fillValue(make("span", "quantity-value"), pairs[i][1]));
      node.appendChild(cell);
    }
    return node;
  }

  function groupLabel(text) {
    return make("p", "group-label", text);
  }

  function checksWithOutcome(checks, outcome) {
    var names = [];
    if (!checks) { return names; }
    for (var name in checks) {
      if (Object.prototype.hasOwnProperty.call(checks, name)
        && checks[name] === outcome) {
        names.push(name);
      }
    }
    return names;
  }

  /*
   * A check name, printed. The lookup is the whole rule: the judgement
   * lives on the server beside the vocabulary it is judging, and this
   * page is the transcription. ``hasOwnProperty`` rather than a bare
   * lookup, so a name that happens to spell an inherited property of
   * ``Object`` cannot return a function.
   *
   * A name with no entry falls back to plain de-underscoring -- the
   * same "transcription, never translation" ``words`` does everywhere
   * else on this page. It is unreachable while the map is built from
   * the registry the API evaluates, and it is here so that if it ever
   * becomes reachable the reader gets the name rather than nothing.
   */
  function checkLabel(name) {
    if (Object.prototype.hasOwnProperty.call(CHECK_WORDS, name)) {
      return CHECK_WORDS[name];
    }
    return words(name);
  }

  /*
   * The named checks behind a verdict. ``ts_single_point_present``
   * carrying the outcome ``missing`` is the whole point: it turns "why
   * is this only mostly supported?" from a guess into a list. It ships
   * collapsed because a card that opens into thirty check names has
   * traded one wall of text for another.
   *
   * Each item prints the label and carries the raw name in ``title``,
   * so the wire token a reader needs to quote in a bug report, or to
   * find in the rubric, is one hover away and is also in the raw JSON
   * every card links to.
   */
  function checkList(title, names) {
    var node = make("div", "trust-checks");
    node.appendChild(make("p", "trust-checks-title", title));
    var list = make("ul", "trust-check-list");
    for (var i = 0; i < names.length; i += 1) {
      var item = make("li", null, checkLabel(names[i]));
      item.setAttribute("title", names[i]);
      list.appendChild(item);
    }
    node.appendChild(list);
    return node;
  }

  function trustNode(trust) {
    var node = make("div", "trust");
    if (!trust || !trust.trust_status) {
      node.appendChild(make("p", "trust-none", TRUST_NOT_ASSESSED));
      return node;
    }
    var evidence = trust.evidence || {};
    var line = make("p", "trust-line");
    line.appendChild(make("span", "trust-label", "evidence"));
    line.appendChild(make("span", "trust-grade",
      TRUST_WORDS[trust.trust_status] || String(trust.trust_status)));
    /*
     * The rubric's own two counts, and never a percentage: the spec
     * says the completeness ratio is not to be restated as one, and a
     * count of named checks is the thing the reader can go and read.
     */
    if (evidence.possible_count) {
      line.appendChild(make("span", "trust-tally",
        evidence.passed_count + " of " + evidence.possible_count + " checks present"));
    }
    if (evidence.rubric) { line.appendChild(make("span", "trust-rubric", evidence.rubric)); }
    node.appendChild(line);
    if (evidence.hard_fail_reason) {
      node.appendChild(make("p", "trust-explains",
        "A structural check failed outright: " + evidence.hard_fail_reason));
    }
    /*
     * ``evidence.checks`` is an ordered {name: outcome} map, so the two
     * lists are selections from it rather than two fields. Iterating the
     * object preserves the rubric's declared check order, which is what
     * keeps two cards comparable line for line.
     */
    var missing = checksWithOutcome(evidence.checks, "missing");
    var passed = checksWithOutcome(evidence.checks, "passed");
    if (missing.length || passed.length) {
      var why = make("details", "trust-why");
      why.appendChild(make("summary", null, "What the rubric checked"));
      if (missing.length) { why.appendChild(checkList("not present", missing)); }
      if (passed.length) { why.appendChild(checkList("present", passed)); }
      why.appendChild(make("p", "trust-explains", TRUST_VS_REVIEW));
      node.appendChild(why);
    }
    return node;
  }

  function detailBody(section, ref, payload) {
    var fragment = doc.createDocumentFragment();
    var records = payload.records || [];
    var pagination = payload.pagination || {};
    var total = pagination.total;
    if (total === null || total === undefined) { total = records.length; }
    fragment.appendChild(make("p", "detail-count", plural(total, section.one, section.many)));
    if (section.note) { fragment.appendChild(make("p", "detail-note", section.note)); }
    if (!section.trust) { fragment.appendChild(make("p", "detail-note", TRUST_NOT_ON_LIST)); }
    var shown = Math.min(records.length, CARD_LIMIT);
    for (var i = 0; i < shown; i += 1) {
      var trust = section.trust ? section.trust(records[i]) : null;
      var view = section.view(records[i]);
      var card = make("div", "detail-card");
      var head = make("div", "detail-head");
      head.appendChild(make("span", "detail-title", view.title));
      /*
       * The review badge stays in the head, where it has always been,
       * and stays the record's own review state. The verdict below is
       * a separate sentence about a separate thing. Where a record
       * carries no review block of its own the fragment's copy is used
       * rather than leaving the head silent.
       */
      var reviewed = view.status || (trust ? trust.review_status : null);
      if (reviewed) { head.appendChild(reviewBadge(reviewed)); }
      card.appendChild(head);
      if (view.headline && view.headline.length) {
        card.appendChild(quantityBlock(view.headline));
      }
      if (view.facts && view.facts.length) { card.appendChild(fieldList(view.facts)); }
      if (view.provenance && view.provenance.length) {
        card.appendChild(groupLabel("how it was produced"));
        card.appendChild(fieldList(view.provenance, "fields fields-quiet"));
      }
      if (section.trust) { card.appendChild(trustNode(trust)); }
      if (view.ref) {
        card.appendChild(identList([[REF_WORD, view.ref, true]]));
      }
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

  /*
   * ``minimum`` / ``ground`` are the API's tokens for two facts about
   * a stationary point. Spelled out they are a sentence a reader
   * understands without knowing the enum; unrecognised tokens fall
   * through to their own spelling rather than being mapped onto the
   * nearest known one.
   */
  var ENTRY_KIND_WORDS = {
    minimum: "energy minimum",
    vdw_complex: "van der Waals complex"
  };
  var STATE_KIND_WORDS = {
    ground: "ground electronic state",
    excited: "excited electronic state"
  };

  function entryNode(entry) {
    var ref = entry.species_entry_ref || "";
    var availability = entry.availability || {};
    var kind = entry.species_entry_kind;
    var state = entry.electronic_state_kind;
    var node = make("div", "entry");
    var head = make("div", "entry-head");
    head.appendChild(make("span", "entry-kind",
      (ENTRY_KIND_WORDS[kind] || words(kind) || "entry") + ", " +
      (STATE_KIND_WORDS[state] || words(state) || "electronic state")));
    head.appendChild(reviewBadge(entry.review ? entry.review.status : null));
    node.appendChild(head);
    node.appendChild(identList([["Entry ref", ref, true]]));

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

  /*
   * The state a molecule is in, as a qualifier on the molecule rather
   * than as two more rows of a key/value list. Charge and spin are
   * what distinguish one entry of a species from another, so they sit
   * with the structure and above everything else.
   */
  function stateStrip(charge, multiplicity) {
    var node = make("div", "state");
    var spin = spinWord(multiplicity);
    node.appendChild(stateCell("charge", chargeText(charge), null));
    node.appendChild(stateCell("spin state", spin,
      multiplicity === null || multiplicity === undefined ? null : String(multiplicity)));
    return node;
  }

  function stateCell(label, value, aside) {
    var cell = make("div", "state-cell");
    cell.appendChild(make("span", "state-label", label));
    var box = make("span", "state-value");
    if (value === null || value === undefined) {
      box.className = "state-value absent";
      box.appendChild(doc.createTextNode(aside === null || aside === undefined ? ABSENT : aside));
    } else {
      box.appendChild(doc.createTextNode(value));
      /*
       * The number stays beside the word. "doublet" is what a chemist
       * reads; 2 is what the API carries and what goes back into a
       * query, and dropping either one costs a different reader.
       */
      if (aside) { box.appendChild(make("span", "state-aside", "(" + aside + ")")); }
    }
    cell.appendChild(box);
    return cell;
  }

  function recordNode(record) {
    var node = make("article", "record");
    var head = make("div", "record-head");
    var molecule = make("p", "molecule");
    molecule.appendChild(make("span", "smiles", record.canonical_smiles || "(no SMILES)"));
    head.appendChild(molecule);
    head.appendChild(stateStrip(record.charge, record.multiplicity));
    node.appendChild(head);

    node.appendChild(identList([
      ["Formula", formulaNode(record.formula), false],
      ["InChIKey", record.inchi_key, true],
      ["Species ref", record.species_ref, true]
    ]));

    var entries = record.entries || [];
    for (var i = 0; i < entries.length; i += 1) {
      node.appendChild(entryNode(entries[i]));
    }
    if (!entries.length) {
      var none = make("div", "entry");
      none.appendChild(make("span", "entry-kind", "no entries yet"));
      node.appendChild(none);
    }
    /*
     * The machine view, demoted rather than deleted. Someone who wants
     * the real payload must always be able to get to it from the
     * record they are looking at, and must always know that is where
     * the link goes.
     */
    var raw = make("p", "record-raw");
    raw.appendChild(anchor(searchUrl("species_ref", record.species_ref), "raw-link",
      "this record as raw JSON"));
    node.appendChild(raw);
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

  /*
   * ---- reaction search ---------------------------------------------
   *
   * The same machinery as above, pointed at the other public search
   * endpoint, with one difference that governs the whole design: a
   * reaction query is a *set* per side. The endpoint reads repeated
   * ``reactants=`` parameters as that set and reads
   * ``?reactants=A,B`` as one species whose name is the six characters
   * "A,B" -- a query that is not an error and matches nothing, which is
   * the worst kind. So there is no place in this page where a side is
   * ever a single joined string: the markup is one input per species,
   * the add button appends another input, and rxnUrl emits one
   * parameter per element.
   */

  function ask(url) {
    return fetch(url, { headers: { "Accept": "application/json" } }).then(function (response) {
      return response.json().then(function (body) {
        return { ok: response.ok, status: response.status, body: body };
      }, function () {
        return { ok: false, status: response.status, body: null };
      });
    });
  }

  function trim(value) {
    return String(value).replace(/^\\s+|\\s+$/g, "");
  }

  function rxnUrl(reactants, products, limit) {
    var parts = [];
    var i;
    for (i = 0; i < reactants.length; i += 1) {
      parts.push("reactants=" + encodeURIComponent(reactants[i]));
    }
    for (i = 0; i < products.length; i += 1) {
      parts.push("products=" + encodeURIComponent(products[i]));
    }
    if (limit) { parts.push("limit=" + limit); }
    /*
     * No participant and no ref is a real 422 from the endpoint --
     * missing_reaction_search_filter -- and asking for it is how the
     * reader is told what the endpoint needs, in the endpoint's own
     * words, instead of being second-guessed here.
     */
    if (!parts.length) { return RXN_SEARCH; }
    return RXN_SEARCH + "?" + parts.join("&");
  }

  function rxnEntryUrl(ref) {
    return RXN_SEARCH + "?reaction_entry_ref=" + encodeURIComponent(ref);
  }

  function sideBox(side) {
    return doc.getElementById("rxn-" + side);
  }

  function sideValues(side) {
    var nodes = sideBox(side).getElementsByTagName("input");
    var values = [];
    for (var i = 0; i < nodes.length; i += 1) {
      var value = trim(nodes[i].value);
      if (value) { values.push(value); }
    }
    return values;
  }

  function relabel(side) {
    var nodes = sideBox(side).getElementsByTagName("input");
    var word = SIDE_WORDS[side];
    for (var i = 0; i < nodes.length; i += 1) {
      nodes[i].setAttribute("aria-label", nodes.length === 1 ? word : word + " " + (i + 1));
    }
  }

  function rxnField(side, value) {
    var node = make("input");
    node.type = "search";
    node.name = side;
    node.value = value || "";
    node.setAttribute("autocomplete", "off");
    node.setAttribute("autocapitalize", "off");
    node.setAttribute("spellcheck", "false");
    return node;
  }

  function setSide(side, values) {
    var box = sideBox(side);
    clear(box);
    var list = values && values.length ? values : [""];
    for (var i = 0; i < list.length; i += 1) {
      box.appendChild(rxnField(side, list[i]));
    }
    relabel(side);
  }

  function addField(side) {
    var node = rxnField(side, "");
    sideBox(side).appendChild(node);
    relabel(side);
    node.focus();
  }

  function sidePhrase(word, values) {
    return word + " " + values.join(" + ");
  }

  function rxnLabel(reactants, products) {
    var parts = [];
    if (reactants.length) { parts.push(sidePhrase("reactants", reactants)); }
    if (products.length) { parts.push(sidePhrase("products", products)); }
    return parts.join("; ");
  }

  function rxnChip(index) {
    var pair = RXN_EXAMPLES[index];
    var node = anchor(rxnUrl(pair[0], pair[1]), "chip rxn-chip", rxnLabel(pair[0], pair[1]));
    node.setAttribute("data-example", String(index));
    node.addEventListener("click", function (event) {
      event.preventDefault();
      applyExample(pair);
    });
    return node;
  }

  function rxnExampleList(intro) {
    var node = make("p", "examples");
    node.appendChild(make("span", null, intro));
    for (var i = 0; i < RXN_EXAMPLES.length; i += 1) {
      node.appendChild(rxnChip(i));
    }
    return node;
  }

  function kineticsView(record) {
    var params = record.parameters || {};
    var coverage = record.temperature_coverage || {};
    var span = null;
    if (coverage.record_min_k !== null && coverage.record_min_k !== undefined
      && coverage.record_max_k !== null && coverage.record_max_k !== undefined) {
      span = {
        value: Number(coverage.record_min_k).toFixed(0) + "\u2013"
          + Number(coverage.record_max_k).toFixed(0),
        unit: "K"
      };
    }
    /*
     * The A units are an enum token in the payload and a typeset unit
     * on screen. An unrecognised token is printed as it arrived rather
     * than dropped: a rate constant whose units are not shown is worse
     * than one whose units are shown in the API's own spelling.
     */
    var prefactor = scientific(params.A, 3,
      params.A_units ? (A_UNITS[params.A_units] || words(params.A_units)) : null);
    return {
      title: "Rate coefficient",
      ref: record.kinetics_ref,
      status: record.review ? record.review.status : null,
      headline: [
        ["Pre-exponential A", prefactor],
        ["Activation energy Ea", fixed(params.Ea_kj_mol, 2, "kJ/mol")]
      ],
      facts: [
        ["Temperature exponent n", scientific(params.n, 4)],
        ["Model", KINETICS_MODEL_WORDS[record.model_kind] || words(record.model_kind)],
        ["Valid over", span],
        ["Direction", words(record.direction)]
      ],
      provenance: [
        ["Origin", words(record.scientific_origin)]
      ]
    };
  }

  function transitionStateView(record) {
    var entry = record.transition_state_entry || {};
    var state = record.transition_state || {};
    var evidence = record.evidence_summary || {};
    var spin = spinWord(entry.multiplicity);
    var multiplicity = entry.multiplicity;
    if (multiplicity !== null && multiplicity !== undefined) {
      multiplicity = spin ? spin + " (" + multiplicity + ")" : String(multiplicity);
    }
    return {
      title: state.label || "transition state",
      ref: entry.transition_state_entry_ref,
      status: entry.review ? entry.review.status : null,
      headline: [
        ["Charge", chargeText(entry.charge)],
        ["Spin state", multiplicity]
      ],
      facts: [
        ["Saddle point", words(entry.status)],
        ["Calculations behind it", evidence.calculation_count]
      ]
    };
  }

  var RXN_SECTIONS = [
    {
      key: "kinetics",
      label: "kinetics",
      one: "kinetics record",
      many: "kinetics records",
      count: function (a) { return a.kinetics_count; },
      shown: function (a) { return !!a.has_kinetics || !!a.kinetics_count; },
      url: function (ref) {
        return KINETICS + encodeURIComponent(ref) + "/kinetics?" + TRUST_PARAM;
      },
      trust: function (record) { return record.trust; },
      view: kineticsView
    },
    {
      key: "transition-state",
      label: "transition state",
      one: "transition state",
      many: "transition states",
      shown: function (a) { return !!a.has_transition_state; },
      url: function (ref) {
        return TRANSITION_STATES + "?reaction_entry_ref=" + encodeURIComponent(ref) +
          "&" + TRUST_PARAM;
      },
      trust: function (record) { return record.trust; },
      view: transitionStateView
    }
  ];

  /*
   * The equation as chemistry rather than as ASCII. ``<=>`` and ``=>``
   * are the API's spellings of the two arrows and are replaced only
   * where they stand alone between spaces, so nothing inside a SMILES
   * can be touched; anything else is left exactly as it arrived.
   */
  function equationText(equation) {
    if (!equation) { return null; }
    return String(equation).split(" <=> ").join(" ⇌ ").split(" => ").join(" → ");
  }

  function rxnRecordNode(record) {
    var ref = record.reaction_entry_ref || "";
    var availability = record.availability || {};
    var node = make("article", "record");

    var head = make("div", "record-head");
    var equation = make("p", "molecule");
    equation.appendChild(make("span", "equation",
      equationText(record.equation) || "(no equation)"));
    head.appendChild(equation);
    var state = make("div", "state");
    state.appendChild(stateCell("query",
      DIRECTION_WORDS[record.matched_direction] || words(record.matched_direction), null));
    state.appendChild(stateCell("reversible",
      record.reversible === null || record.reversible === undefined
        ? null
        : (record.reversible ? "yes" : "no"), null));
    head.appendChild(state);
    head.appendChild(reviewBadge(record.review ? record.review.status : null));
    node.appendChild(head);

    node.appendChild(identList([
      ["Reaction family", record.family, false],
      ["Entry ref", ref, true]
    ]));

    var links = make("div", "entry-links");
    var panels = make("div", "entry-details");
    for (var i = 0; i < RXN_SECTIONS.length; i += 1) {
      if (!RXN_SECTIONS[i].shown(availability)) { continue; }
      var pair = disclosure(RXN_SECTIONS[i], ref, availability);
      links.appendChild(pair.button);
      panels.appendChild(pair.panel);
    }
    if (!links.firstChild) {
      links.appendChild(make("span", "pill pill-none", "no kinetics or transition state yet"));
    }
    node.appendChild(links);
    node.appendChild(panels);
    var raw = make("p", "record-raw");
    raw.appendChild(anchor(rxnEntryUrl(ref), "raw-link", "this record as raw JSON"));
    node.appendChild(raw);
    return node;
  }

  function rxnEmptyNode(reactants, products) {
    var node = make("div", "empty");
    var asked = rxnLabel(reactants, products);
    node.appendChild(make("p", null,
      asked ? "Nothing matched " + asked + "." : "Nothing matched."));
    node.appendChild(rxnExampleList("These return records:"));
    return node;
  }

  function rxnRender(payload, reactants, products) {
    var records = payload.records || [];
    if (!records.length) {
      rxnOut.appendChild(rxnEmptyNode(reactants, products));
      return;
    }
    rxnOut.appendChild(summaryNode(payload));
    for (var i = 0; i < records.length; i += 1) {
      rxnOut.appendChild(rxnRecordNode(records[i]));
    }
    var pagination = payload.pagination || {};
    if (pagination.total > records.length) {
      var more = make("p", "results-status", "Showing the first page.");
      more.appendChild(doc.createTextNode(" "));
      more.appendChild(anchor(rxnUrl(reactants, products), "raw-link",
        "Open the full response as raw JSON"));
      rxnOut.appendChild(more);
    }
  }

  function runRxn(reactants, products) {
    rxnOut.setAttribute("aria-busy", "true");
    clear(rxnOut);
    rxnOut.appendChild(make("p", "results-status", "Searching\\u2026"));
    ask(rxnUrl(reactants, products, RXN_LIMIT)).then(function (result) {
      clear(rxnOut);
      if (result.ok) {
        rxnRender(result.body || {}, reactants, products);
      } else {
        rxnOut.appendChild(failureNode(result.status, result.body));
      }
    }, function () {
      clear(rxnOut);
      var node = make("div", "failure");
      node.appendChild(make("p", "failure-detail",
        "The browser could not reach this deployment's API."));
      rxnOut.appendChild(node);
    }).then(function () {
      rxnOut.setAttribute("aria-busy", "false");
    });
  }

  function submitRxn() {
    runRxn(sideValues("reactants"), sideValues("products"));
  }

  function applyExample(pair) {
    setSide("reactants", pair[0]);
    setSide("products", pair[1]);
    runRxn(pair[0], pair[1]);
  }

  /*
   * ---- the species / reactions switch -------------------------------
   *
   * Built here rather than in the markup because a tab control that
   * cannot change what is shown is a lie. With scripting off the
   * buttons stay hidden and both panels stay on the page, each under
   * its own heading and each a working form; this promotes them to a
   * tablist and takes the now-duplicated headings out.
   */
  function setupModes() {
    var modes = doc.getElementById("modes");
    if (!modes) { return; }
    var tabs = [];
    var panels = [];
    var i;
    for (i = 0; i < MODES.length; i += 1) {
      var tab = doc.getElementById(MODES[i][0]);
      var panel = doc.getElementById(MODES[i][1]);
      if (!tab || !panel) { return; }
      tabs.push(tab);
      panels.push(panel);
    }
    var loaded = [true, false];

    function select(index, focus) {
      for (var j = 0; j < tabs.length; j += 1) {
        var on = j === index;
        tabs[j].setAttribute("aria-selected", on ? "true" : "false");
        tabs[j].setAttribute("tabindex", on ? "0" : "-1");
        panels[j].hidden = !on;
      }
      if (focus) { tabs[index].focus(); }
      if (!loaded[index]) {
        loaded[index] = true;
        MODES[index][3]();
      }
    }

    modes.hidden = false;
    modes.setAttribute("role", "tablist");
    for (i = 0; i < tabs.length; i += 1) {
      tabs[i].setAttribute("role", "tab");
      panels[i].setAttribute("role", "tabpanel");
      panels[i].setAttribute("tabindex", "0");
      panels[i].setAttribute("aria-labelledby", tabs[i].id);
      var title = doc.getElementById(MODES[i][2]);
      if (title) { title.hidden = true; }
      (function (index) {
        tabs[index].addEventListener("click", function () { select(index, false); });
        tabs[index].addEventListener("keydown", function (event) {
          if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") { return; }
          event.preventDefault();
          var step = event.key === "ArrowRight" ? 1 : tabs.length - 1;
          select((index + step) % tabs.length, true);
        });
      })(i);
    }
    select(0, false);
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

  if (rxnForm && rxnOut) {
    /*
     * The markup marks every reaction field ``required`` so that a
     * plain no-script submit can never send ``products=`` empty --
     * which the endpoint AND-combines and answers with nothing. The
     * script skips blank fields itself and must therefore let a side
     * be cleared, so it takes the browser's validation off the form it
     * has just taken over.
     */
    rxnForm.noValidate = true;
    rxnForm.addEventListener("submit", function (event) {
      event.preventDefault();
      submitRxn();
    });

    for (var s = 0; s < RXN_SIDES.length; s += 1) {
      (function (name) {
        var button = doc.getElementById("rxn-add-" + name);
        if (!button) { return; }
        button.hidden = false;
        button.addEventListener("click", function () { addField(name); });
      })(RXN_SIDES[s][0]);
    }

    var rxnSeeded = doc.getElementById("rxn-examples");
    if (rxnSeeded) {
      var rxnLinks = rxnSeeded.getElementsByTagName("a");
      for (var r = 0; r < rxnLinks.length; r += 1) {
        (function (node) {
          node.addEventListener("click", function (event) {
            event.preventDefault();
            applyExample(RXN_EXAMPLES[Number(node.getAttribute("data-example"))]);
          });
        })(rxnLinks[r]);
      }
    }

    setupModes();
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


def reaction_query(reactants: tuple[str, ...], products: tuple[str, ...]) -> str:
    """The reaction-search URL for one query, as repeated parameters.

    One ``reactants=`` per reactant and one ``products=`` per product.
    Never ``reactants=A,B``: the endpoint takes each value as one whole
    species name, so a joined list asks for a species called ``A,B``,
    which is not an error and matches nothing.

    Used for the example links in the markup, which have to run the
    same query with scripting off that the script runs with it on.
    """
    parts = [f"reactants={_url_quote(value)}" for value in reactants]
    parts += [f"products={_url_quote(value)}" for value in products]
    if not parts:
        return REACTION_SEARCH_PATH
    return REACTION_SEARCH_PATH + "?" + "&".join(parts)


def reaction_label(reactants: tuple[str, ...], products: tuple[str, ...]) -> str:
    """How one reaction query reads on a chip: ``products A + B``.

    The ``+`` here is a separator a person reads, never one that
    reaches a URL; :func:`reaction_query` is what builds the URL. The
    script has the same function so a chip and the empty state label
    the same query the same way.
    """
    parts = []
    if reactants:
        parts.append("reactants " + " + ".join(reactants))
    if products:
        parts.append("products " + " + ".join(products))
    return "; ".join(parts)


def _side_word(side: str) -> str:
    """The singular noun for one side, from :data:`REACTION_SIDES`."""
    return next(word for key, _, word in REACTION_SIDES if key == side)


def _reaction_inputs(side: str, values: tuple[str, ...], indent: str) -> str:
    """One ``<input>`` per species, all ``required``.

    ``required`` is what makes the no-script path correct rather than
    merely present: the browser refuses to submit a blank field, so a
    plain GET from this form can never carry ``products=`` with nothing
    after it -- which the endpoint reads as a filter that matches
    nothing rather than as an absent one. The script removes the
    constraint when it takes the form over, because it can skip blank
    fields properly.
    """
    word = _side_word(side)
    lines = []
    for index, value in enumerate(values, start=1):
        label = word if len(values) == 1 else f"{word} {index}"
        lines.append(
            f'{indent}<input name="{side}" type="search" value="{_html_escape(value)}"'
            f' required aria-label="{label}"'
            ' autocomplete="off" autocapitalize="off" spellcheck="false">'
        )
    return "\n".join(lines)


def _reaction_example_chips() -> str:
    lines = []
    for index, (reactants, products) in enumerate(REACTION_EXAMPLES):
        href = _html_escape(reaction_query(reactants, products))
        label = _html_escape(reaction_label(reactants, products))
        lines.append(
            f'      <a class="chip rxn-chip" data-example="{index}" href="{href}">'
            f"{label}</a>"
        )
    return "\n".join(lines)


def _pair_placeholders(side: str) -> tuple[str, str]:
    """Two real values for the two-deep fallback form's hints.

    Taken from whichever offered example really carries two species on
    that side, so the hint is a query that returns records rather than
    the same value typed twice.
    """
    for reactants, products in REACTION_EXAMPLES:
        values = reactants if side == "reactants" else products
        if len(values) == 2:
            return (values[0], values[1])
    seed = SEED_REACTANTS if side == "reactants" else SEED_PRODUCTS
    return (seed[0], seed[0])


def _reaction_fallback_forms() -> str:
    """The scripting-off reaction forms: one side each, one and two deep.

    Separate forms per shape for the same reason the species fallback
    has one form per identifier -- an empty sibling parameter is not an
    absent one, and a single form covering every shape would always
    submit some field blank. Every input is ``required``, so the
    browser will not let one through empty.

    The two-deep forms are the point of this block: a plain HTML form
    emits repeated same-named inputs as repeated query parameters, so
    ``reactants=A&reactants=B`` is reachable with no script at all.
    """
    blocks = []
    for side, plural_word, word in REACTION_SIDES:
        seed = SEED_REACTANTS if side == "reactants" else SEED_PRODUCTS
        placeholder = _html_escape(seed[0])
        pair = _pair_placeholders(side)
        singles = (
            f'        <span class="fallback-label">one {word.lower()}</span>\n'
            f'        <form method="get" action="{REACTION_SEARCH_PATH}">\n'
            f'          <label class="sr-only" for="fallback-{side}">{word}</label>\n'
            f'          <input id="fallback-{side}" name="{side}" type="search"'
            f' placeholder="{placeholder}" required>\n'
            "          <button type=\"submit\">Search</button>\n"
            "        </form>"
        )
        pair_inputs = "\n".join(
            f'          <label class="sr-only" for="fallback-{side}-{n}">{word} {n}</label>\n'
            f'          <input id="fallback-{side}-{n}" name="{side}" type="search"'
            f' placeholder="{_html_escape(pair[n - 1])}" required>'
            for n in (1, 2)
        )
        pairs = (
            f'        <span class="fallback-label">two {plural_word.lower()}</span>\n'
            f'        <form method="get" action="{REACTION_SEARCH_PATH}">\n'
            f"{pair_inputs}\n"
            "          <button type=\"submit\">Search</button>\n"
            "        </form>"
        )
        blocks.append(singles)
        blocks.append(pairs)
    return "\n".join(blocks)


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


def _reaction_examples_json() -> str:
    """The example queries as ``[[reactants, products], ...]``.

    Lists, not a joined string, all the way from here into the script:
    the shape the page carries a multi-species side in is the shape it
    sends, so there is nowhere for a comma to be introduced.
    """
    return json.dumps([[list(reactants), list(products)] for reactants, products in REACTION_EXAMPLES])


def _reaction_sides_json() -> str:
    """``[[query parameter, singular noun], ...]`` in form order."""
    return json.dumps([[side, word] for side, _, word in REACTION_SIDES])


def _placeholders_json() -> str:
    """Per-identifier ``placeholder`` text, keyed by query parameter.

    Each one is a value that really is in the shape that field wants, so
    the hint doubles as a worked example of the syntax.
    """
    return json.dumps(dict(SEARCH_PLACEHOLDERS))


def trust_check_names() -> tuple[str, ...]:
    """Every check name the rubrics can put in ``evidence.checks``, sorted.

    Read out of the registry rather than listed here, so the page cannot
    fall behind the rubrics it renders.
    """
    return tuple(
        sorted({check.name for rubric in RUBRIC_REGISTRY.values() for check in rubric.checks})
    )


def _check_words_json() -> str:
    """``{check name: printed label}`` for the whole rubric vocabulary."""
    return json.dumps({name: trust_check_label(name) for name in trust_check_names()})


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
        # Reaction placeholders first: every one of them is a longer
        # spelling of a species placeholder, and substituting the short
        # name first would eat the tail of the long one.
        .replace("__REACTION_SEARCH_PATH__", REACTION_SEARCH_PATH)
        .replace("__REACTION_EXAMPLE_CHIPS__", _reaction_example_chips())
        .replace("__REACTION_EXAMPLES_JSON__", _reaction_examples_json())
        .replace("__REACTION_FALLBACK_FORMS__", _reaction_fallback_forms())
        .replace("__REACTION_RENDER_LIMIT__", str(REACTION_RENDER_LIMIT))
        .replace("__REACTION_SIDES_JSON__", _reaction_sides_json())
        .replace(
            "__SEED_REACTANT_INPUTS__",
            _reaction_inputs("reactants", SEED_REACTANTS, " " * 12),
        )
        .replace(
            "__SEED_PRODUCT_INPUTS__",
            _reaction_inputs("products", SEED_PRODUCTS, " " * 12),
        )
        .replace("__SEARCH_PATH__", SPECIES_SEARCH_PATH)
        .replace("__FIELD_OPTIONS__", _field_options(SEED_FIELD))
        .replace("__EXAMPLE_CHIPS__", _example_chips())
        .replace("__PLACEHOLDERS_JSON__", _placeholders_json())
        .replace("__CHECK_WORDS_JSON__", _check_words_json())
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
    "REACTION_EXAMPLES",
    "REACTION_RENDER_LIMIT",
    "REACTION_SEARCH_PATH",
    "REACTION_SIDES",
    "REDOC_PATH",
    "REPO_URL",
    "SEARCH_EXAMPLES",
    "SEARCH_FIELDS",
    "SEED_FIELD",
    "SEED_PRODUCTS",
    "SEED_REACTANTS",
    "SEED_VALUE",
    "SPECIES_SEARCH_PATH",
    "TRUST_CHECK_INITIALISMS",
    "hero_atom_lines",
    "landing_router",
    "reaction_label",
    "reaction_query",
    "render_landing_page",
    "trust_check_label",
    "trust_check_names",
]
