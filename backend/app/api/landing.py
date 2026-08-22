"""The human-readable landing page served at ``/``.

Everything else this application serves is JSON for a machine. This one
page is for a person -- specifically the person who was handed the base
URL in a paper or a README, typed it, and until now received a bare
``404 application/json`` blob with no indication that anything was
there at all. A referee following a URL printed in a manuscript is the
worst possible audience for that, and was the one getting it.

The page is deliberately austere:

* **Self-contained.** No CDN, no external stylesheet, no web font, no
  analytics, no JavaScript. Every byte the browser renders arrives in
  this one response, so the page works on a locked-down network, from
  an archived copy, and with scripting disabled. The API reference at
  ``/redoc`` does *not* share this property -- see
  :func:`app.api.app.create_app` -- which is exactly why the landing
  page is not built on top of it.
* **Factual.** It states no record count, no uptime, no funder, no
  institution and no contributor beyond the authors already named in
  the repository's ``CITATION.cff``. A page that guesses at its own
  corpus size is worse than a page that omits it, because the reader
  cannot tell which numbers were guessed.

The worked example in the hero is not illustrative fiction. The
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

#: The geometry shown in the hero's left panel, and the input the test
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
               computational chemistry data. Every deposit is checked, and
               every objection carries a code and its reasoning.">
<style>
/*
 * Palette: molecular-orbital phase convention. Two signed colours,
 * because signed quantities carry meaning in this field -- orbital
 * phase, real versus imaginary frequencies, accepted versus flagged.
 * Indigo is the positive lobe and reads as "accepted / real";
 * orange-red is the negative lobe and appears essentially only on the
 * verdict.
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
header { padding: 4rem 0 0; }
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
  margin: 1.5rem 0 0;
  max-width: 34rem;
  font-size: var(--fs-lede);
}
nav.actions { margin-top: 1.75rem; }
nav.actions ul {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 0;
  padding: 0;
  list-style: none;
}
.button {
  display: inline-block;
  padding: 0.55rem 1.05rem;
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
section { margin-top: 4rem; }
h2 {
  font-family: var(--mono);
  font-size: var(--fs-section);
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 1rem;
}
h3 {
  font-family: var(--mono);
  font-size: var(--fs-body);
  font-weight: 700;
  letter-spacing: -0.01em;
  margin: 1.9rem 0 0.35rem;
}
p { margin: 0.85rem 0; max-width: 38rem; }

/* ---- the exchange ---- */
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
  padding: 1.1rem 1.25rem 1.35rem;
  min-width: 0;
}
.panel-label {
  font-family: var(--mono);
  font-size: var(--fs-label);
  font-weight: 700;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 0 0 0.85rem;
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
  padding: 0.75rem 0.85rem;
}
.panel pre + .panel-sub { margin-top: 1rem; }
.panel-sub {
  font-family: var(--mono);
  font-size: var(--fs-label);
  font-weight: 700;
  letter-spacing: 0.11em;
  text-transform: uppercase;
  color: var(--muted);
  margin: 1rem 0 0.5rem;
}
.verdict-code {
  display: block;
  font-family: var(--mono);
  /*
   * Sized to fit the 45-character code on one line at the widest
   * fallback mono face, because a code broken mid-token reads as
   * damage rather than emphasis. Its prominence comes from the
   * weight, the phase-negative colour and the rule beside it.
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
.verdict-body { margin: 0.9rem 0 0; font-size: 0.9375rem; max-width: none; }
.verdict-body:last-child { margin-bottom: 0; }
figcaption {
  margin-top: 1rem;
  color: var(--muted);
  font-size: 0.9375rem;
  max-width: 40rem;
}
/*
 * The single orchestrated moment: the reply arrives a beat after the
 * deposit, in the order a request and its response actually happen.
 * The resting state is fully visible and the keyframes run *from*
 * hidden, so a browser that never runs the animation -- reduced
 * motion, an old engine, a print stylesheet -- shows the panel rather
 * than an empty box.
 */
@keyframes reply-in {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}
.panel.verdict { animation: reply-in 420ms ease-out 380ms backwards; }
@media (prefers-reduced-motion: reduce) {
  .panel.verdict { animation: none; }
}

/* ---- the four roles ---- */
.roles { margin: 0; }
.roles > div {
  padding: 1.15rem 0;
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
.roles dd { margin: 0.4rem 0 0; max-width: 38rem; }

/* ---- code and prose data ---- */
code {
  font-family: var(--mono);
  font-size: 0.9em;
  background: var(--data-bg);
  padding: 0.08em 0.3em;
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
  margin-top: 4.5rem;
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
    Provenance-tracked computational chemistry data, served over HTTP.
    Every deposit is checked on the way in, and every objection carries a
    machine-readable code and the reasoning behind it.
  </p>
  <nav class="actions" aria-label="Primary">
    <ul>
      <li><a class="button primary" href="__DOCS_URL__">Documentation</a></li>
      <li><a class="button secondary" href="__REPO_URL__">Source repository</a></li>
    </ul>
  </nav>
</header>

<main id="main">

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
            This geometry is linear. A linear three-atom molecule has
            3N&minus;5 = 4 vibrations, and this list carries 3, the count for a
            bent molecule. One mode is missing.
          </p>
          <p class="verdict-body">
            The usual cause is a degenerate bending pair reported once: a
            linear molecule&rsquo;s bends are doubly degenerate, so a parser
            that de-duplicates equal frequencies drops one component and lands
            exactly here. The record is accepted and flagged, not refused.
          </p>
        </div>
      </div>
      <figcaption>
        A partial or frozen-atom Hessian produces the same short list
        honestly, so this check warns rather than refusing. Checks that can be
        settled from the data alone do refuse, with an HTTP 422 and the same
        kind of code and reasoning attached.
      </figcaption>
    </figure>
  </section>

  <section aria-labelledby="roles-heading">
    <h2 id="roles-heading">How it works</h2>
    <p>
      Every table plays exactly one of four roles, and the role fixes how
      that table may be written. Keeping them apart is what lets a stored
      number stay fixed while opinions about it change.
    </p>
    <dl class="roles">
      <div>
        <dt>identity <span class="behaviour">deduped</span></dt>
        <dd>
          What a thing is. One row per distinct species, reaction or
          transition state, however many times it is submitted, carrying no
          computed values and expressing no preference.
        </dd>
      </div>
      <div>
        <dt>provenance <span class="behaviour">append-only</span></dt>
        <dd>
          How it was produced. The software and release, level of theory,
          workflow tool and literature behind a number, recorded at deposit
          and never rewritten.
        </dd>
      </div>
      <div>
        <dt>result <span class="behaviour">append-only</span></dt>
        <dd>
          The number. A better calculation adds a row rather than replacing
          the earlier one, so nothing already cited changes underneath a
          reader.
        </dd>
      </div>
      <div>
        <dt>curation <span class="behaviour">overlay</span></dt>
        <dd>
          How far to trust it. Review state and release selections sit on top
          of the results and never mutate them; a read asks for every
          candidate with no recommendation, or for an attributed curated
          selection.
        </dd>
      </div>
    </dl>
  </section>

  <section aria-labelledby="citing-heading">
    <h2 id="citing-heading">Citing TCKDB</h2>
    <p>
      Two different things are citable here, and they are versioned
      independently. Upgrading the software must not change what a published
      dataset says, and re-curating a dataset must not require a code
      release.
    </p>

    <h3>The software</h3>
    <p>
      Cite the software when you use or extend the code. The metadata is in
      <code>CITATION.cff</code> at the root of the
      <a href="__REPO_URL__/blob/main/CITATION.cff">source repository</a>,
      authored by Calvin Pieters and Alon Grinberg Dana and released under
      the MIT licence. No DOI is minted for it yet: a DOI cannot be
      retracted, so one is registered when a paper tag is cut rather than
      speculatively.
    </p>

    <h3>A dataset release</h3>
    <p>
      Cite a dataset release when you use TCKDB&rsquo;s numbers. A release is
      a separate artifact with its own tag, an immutable SHA-256-checksummed
      manifest, an attributed selection ledger and its own citation string.
      Releases are not listed in the software changelog; they are
      discoverable at <code>GET /api/v1/scientific/releases</code>, and each
      one carries its own <code>changelog_entry</code>. A published release
      is never rewritten &mdash; withdrawing one keeps the row and its
      manifest readable, so an outstanding citation never dangles.
    </p>
  </section>

  <section aria-labelledby="api-heading">
    <h2 id="api-heading">Use the API</h2>
    <p>
      Everything machine-readable lives under the base path
      <code>/api/v1</code>. Reading the public scientific surface needs no
      account; depositing data does.
    </p>
    <p>__API-REFERENCE__</p>
    <p>
      A thin Python client, <code>tckdb-client</code>, handles
      authentication, payload encoding, retries and idempotency. It is
      published from this repository rather than to PyPI:
    </p>
    <pre class="command"><code>pip install "git+__REPO_URL__.git#subdirectory=clients/python"</code></pre>
    <p>
      Any HTTP client that can send JSON works just as well; the client
      package is a convenience, not a requirement.
    </p>
  </section>

</main>

<footer>
  <p>
    Operational status, including the database revision this deployment is
    running, is reported at
    <a href="/api/v1/status"><code>/api/v1/status</code></a>.
  </p>
</footer>

</body>
</html>
"""


def render_landing_page(*, api_reference_path: str | None) -> str:
    """Return the complete landing-page HTML document.

    *api_reference_path* is the path this deployment serves the ReDoc
    API reference on, or ``None`` when it serves none. It is not a
    cosmetic difference: a deployment with the reference switched off
    must not advertise a link that answers 404, which is the precise
    failure this page was added to remove.

    The template carries ``__PLACEHOLDER__`` markers rather than
    ``str.format`` fields, because the embedded stylesheet is full of
    braces and escaping every one of them would make the CSS unreadable
    for no gain.
    """
    if api_reference_path:
        reference_block = (
            "The full API reference is served at "
            f'<a href="{api_reference_path}"><code>{api_reference_path}</code></a>, '
            "rendered from this deployment's own OpenAPI document. The "
            f'<a href="{DOCS_URL}">documentation site</a> carries the query '
            "guides and worked examples."
        )
    else:
        reference_block = (
            "This deployment does not publish an API reference endpoint. The "
            f'<a href="{DOCS_URL}">documentation site</a> carries the endpoint '
            "reference, query guides and worked examples."
        )
    frequencies = "   ".join(f"{value:.1f}" for value in HERO_FREQUENCIES)
    return (
        _PAGE_TEMPLATE.replace("__DOCS_URL__", DOCS_URL)
        .replace("__REPO_URL__", REPO_URL)
        .replace("__HERO_XYZ__", HERO_XYZ)
        .replace("__HERO_FREQUENCIES__", frequencies)
        .replace("__HERO_CODE__", HERO_CODE)
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
    "REDOC_PATH",
    "REPO_URL",
    "landing_router",
    "render_landing_page",
]
