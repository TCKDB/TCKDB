"""The landing page at ``/`` and the read-only API-reference switch.

Two changes are covered here, because they answer one question: what a
*person* gets when they type the base URL a paper printed. Before this,
the answer was ``404 application/json`` at ``/``, ``/docs``, ``/redoc``
and ``/openapi.json`` alike -- a bare error blob with nothing in it to
say a database was even there.

The tests are grouped as:

* :class:`TestLandingPageResponse` -- it is served, it is HTML, and it
  carries the facts it is supposed to carry.
* :class:`TestLandingPageIsSelfContained` -- it fetches nothing. A page
  whose whole argument is that it works on a locked-down network must
  not quietly grow a CDN link.
* :class:`TestLiveSearch` -- the hero queries this deployment's own
  public read API and renders what comes back. The structural half is
  checked against a parsed document rather than a substring: the form
  is real, aimed at the real endpoint, and works before any script
  runs.
* :class:`TestSearchDegradesWithoutJavaScript` -- with scripting off
  the page still searches. The one control that cannot work without
  script ships disabled rather than silently searching the wrong
  field, and ``<noscript>`` offers a plain form per identifier.
* :class:`TestReactionSearchTakesASetPerSide` -- the other search. A
  reaction query is a set per side, spelled as repeated query
  parameters; ``?reactants=A,B`` asks for a species named ``A,B`` and
  answers ``200`` with nothing, so these check the shape of every
  reaction URL the page builds rather than checking that a box exists.
* :class:`TestBothSearchesSurviveWithoutTheSwitch` -- the species /
  reactions switch is script-built. Both panels are markup, and the
  tab strip ships hidden rather than shipping inert.
* :class:`TestReviewStateIsInformation` -- ``under_review`` is the
  ordinary state of a served record and must not be drawn in the
  colour reserved for a rejected one.
* :class:`TestShownCommandsRunAsPrinted` -- ``curl`` reads ``[...]``
  as a glob, so a reader who substitutes ``[OH]`` into a printed
  command gets a client-side failure that reads as a broken API.
  Every shown command carries ``-g``.
* :class:`TestResultsExpandInPlace` -- following a result must not
  drop the reader into a nested JSON document without warning. Each
  product is a disclosure that opens on the page, and every route to
  the raw document says that is what it is.
* :class:`TestResultsReadAsAPageNotAPayload` -- a result card must
  read as a rendered record rather than as the payload behind it:
  human labels, units rendered as units, numbers rounded to what the
  quantity supports, absent values that say they are absent, and a
  size hierarchy that puts the molecule first and the public ref last.
* :class:`TestACheckNameDoesNotRepeatItsHeading` -- the named checks sit
  under a heading that already states the outcome, so a check called
  ``charge_present`` prints as "Charge". Trailing only, and only where
  the rest of the name does not say "present" as well: the twelve
  conditional checks keep both presences, because the second one is a
  condition and not the outcome.
* :class:`TestHeroExampleIsReal` -- the worked example in the hero is
  re-run through the real checker on every test run. If the page and
  ``app.services.frequency_geometry_linearity`` ever disagree, this
  fails rather than leaving a plausible-looking fiction on a public
  page. It also has to *show* the defect it describes: the deposited
  list and the true spectrum are both on screen, with the missing mode
  marked, so the reader sees the gap before reading about it.
* :class:`TestDocSurfaceExposure` -- the three-way matrix of
  ``EXPOSE_API_DOCS`` x ``EXPOSE_API_REFERENCE``, including the one
  that matters for the hosted deployment: ReDoc on, Swagger still 404.
* :class:`TestLandingPageChangesNoExistingRoute` -- the invariant that
  adding ``/`` shadowed and reordered nothing, proved by building the
  same application with the landing router removed and diffing the
  ordered route table.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlparse

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import app as app_module
from app.api.app import create_app
from app.api.config import Settings, settings
from app.api.deps import get_db, get_write_db
from app.api.landing import (
    DOCS_URL,
    HERO_CODE,
    HERO_FREQUENCIES,
    HERO_TRUE_FREQUENCIES,
    HERO_XYZ,
    REACTION_EXAMPLES,
    REACTION_SEARCH_PATH,
    REACTION_SIDES,
    REPO_URL,
    SEARCH_EXAMPLES,
    SEARCH_FIELDS,
    SEED_FIELD,
    SEED_PRODUCTS,
    SEED_REACTANTS,
    SEED_VALUE,
    SPECIES_SEARCH_PATH,
    VOCABULARY_DIRECTION_FRAGMENT,
    VOCABULARY_PATH,
    VOCABULARY_TRUST_FRAGMENT,
    VOCABULARY_URL,
    hero_atom_lines,
    reaction_family_display_names,
    reaction_query,
    render_landing_page,
    trust_check_label,
    trust_check_names,
)
from app.api.startup_checks import validate_deployment_safety
from app.chemistry.reaction_family_display import (
    is_unresolved_reaction_family,
    reaction_family_display_name,
)
from app.db.models.common import ArrheniusAUnits, CalculationType, KineticsModelKind
from app.schemas.reaction_family import CANONICAL_REACTION_FAMILIES
from app.schemas.reads.scientific_conformer import (
    ConformerEvidenceCoverage,
    ConformerGroupEvidenceSummary,
)
from app.schemas.reads.scientific_species_calculations import (
    CalculationEnergyBlock,
    ConformerContextBlock,
    SpeciesCalculationsSearchRecord,
)
from app.schemas.reads.scientific_statmech import StatmechEvidenceSummary
from app.schemas.reads.scientific_thermo import ThermoModelKindQuery, ThermoRecord
from app.schemas.reads.scientific_thermo_search import ThermoSearchRecord
from app.schemas.reads.scientific_transition_state import (
    TransitionStateEntryEvidenceSummary,
)
from app.schemas.reads.scientific_transport import TransportEvidenceSummary
from app.services.frequency_geometry_linearity import (
    evaluate_frequency_list_linearity,
)
from app.services.trust.models import EvidenceBadge

#: Every review state the API can put on a record. All five must have a
#: human word on the page: a badge that falls through to a raw enum name
#: is a badge nobody reads.
REVIEW_STATES = (
    "approved",
    "under review",
    "not reviewed",
    "deprecated",
    "rejected",
)

_VOID_TAGS = frozenset({"area", "base", "br", "col", "hr", "img", "input", "link", "meta"})


class _Document(HTMLParser):
    """Every start tag as ``(tag, attrs, ancestors)``.

    Substring assertions on a page this size stop meaning anything --
    ``'name="smiles"' in body`` passes whether or not that input is
    inside a form, and whether or not the form points anywhere useful.
    Parsing lets a test say what it actually means: *this* input, in
    *that* form, whose action is the search endpoint.

    ``HTMLParser`` reads ``<script>`` and ``<style>`` as raw text, so
    the comparison operators in the inline script are not mistaken for
    markup.
    """

    def __init__(self, markup: str) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str | None], tuple[str, ...]]] = []
        self._open: list[str] = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs), tuple(self._open)))
        if tag not in _VOID_TAGS:
            self._open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.elements.append((tag, dict(attrs), tuple(self._open)))

    def handle_endtag(self, tag):
        if tag in self._open:
            while self._open and self._open.pop() != tag:
                pass

    def find(self, tag: str, **attrs: str):
        """All ``tag`` elements whose attributes match, as (attrs, ancestors)."""
        found = []
        for name, element_attrs, ancestors in self.elements:
            if name != tag:
                continue
            if all(element_attrs.get(key) == value for key, value in attrs.items()):
                found.append((element_attrs, ancestors))
        return found


def _noscript_forms(document: "_Document", action: str) -> list[dict[str, str | None]]:
    """The ``<noscript>`` forms aimed at one endpoint.

    The page has two searches and therefore two sets of fallback
    forms. Scoping by action keeps each set's assertions about that
    set: without it, "every fallback form points at species search"
    would be false the moment reaction search existed, and the honest
    repair is to say which forms are being counted rather than to
    loosen what is required of them.
    """
    return [
        attrs
        for attrs, ancestors in document.find("form")
        if "noscript" in ancestors and attrs["action"] == action
    ]


def _stylesheet(page: str) -> str:
    """The embedded stylesheet's source, on its own."""
    match = re.search(r"<style>(.*?)</style>", page, flags=re.DOTALL | re.IGNORECASE)
    assert match is not None, "the page carries no stylesheet"
    return match.group(1)


def _css_declaration(css: str, selector: str, prop: str) -> str:
    """One declaration out of one rule, as it will render.

    Reading a size out of the stylesheet is how an assertion about
    *hierarchy* stays an assertion about what a reader sees. Comparing
    two class names would say nothing about which is bigger.
    """
    block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert block is not None, selector
    declaration = re.search(re.escape(prop) + r"\s*:\s*([^;]+);", block.group(1))
    assert declaration is not None, (selector, prop)
    return declaration.group(1).strip()


def _js_function(script: str, signature: str) -> str:
    """One top-level function body out of the inline script."""
    body = re.search(
        r"function " + re.escape(signature) + r" \{(.*?)\n  \}", script, flags=re.DOTALL
    )
    assert body is not None, signature
    return body.group(1)


def _js_code(body: str) -> str:
    """One function body with its block comments removed.

    The script is heavily commented and several comments name the very
    fields the code beside them must not touch. A ban asserted over the
    raw text would therefore be a ban on explaining the rule, so the
    bans below are asserted over the code.
    """
    return re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)


def _js_map(script: str, name: str) -> dict[str, str]:
    """One ``var NAME = {...};`` object literal, as a dict.

    Parsed rather than substring-matched, so that a test comparing a
    map against the enum it mirrors fails when an entry is removed,
    renamed or given the wrong word -- not only when the whole block
    disappears.
    """
    block = re.search(r"var " + re.escape(name) + r" = \{(.*?)\n  \};", script, flags=re.DOTALL)
    assert block is not None, name
    pairs = re.findall(r'([A-Za-z0-9_]+|"[^"]+")\s*:\s*"([^"]*)"', block.group(1))
    assert pairs, name
    return {key.strip('"'): value for key, value in pairs}


@pytest.fixture(scope="module")
def page() -> str:
    """The page as a deployment serving a reference renders it."""
    return render_landing_page(api_reference_path="/redoc")


@pytest.fixture(scope="module")
def document(page: str) -> _Document:
    return _Document(page)


@pytest.fixture(scope="module")
def script(page: str) -> str:
    """The inline script's source, on its own."""
    match = re.search(r"<script>(.*?)</script>", page, flags=re.DOTALL | re.IGNORECASE)
    assert match is not None, "the page carries no inline script"
    return match.group(1)


@pytest.fixture
def client_factory(db_session: Session):
    """Build a fresh app per test so ``create_app`` re-reads settings."""

    def _build() -> TestClient:
        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_write_db] = lambda: db_session
        return TestClient(app)

    return _build


class TestLandingPageResponse:
    def test_root_serves_html_not_a_json_error(self, client_factory):
        with client_factory() as c:
            response = c.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<!DOCTYPE html>" in response.text

    def test_page_links_the_documentation_site_and_the_repository(self, client_factory):
        with client_factory() as c:
            body = c.get("/").text
        assert f'href="{DOCS_URL}"' in body
        assert f'href="{REPO_URL}"' in body

    def test_the_page_does_not_teach_the_table_role_taxonomy(self, client_factory):
        """Schema philosophy is documentation, not landing-page content.

        The identity / provenance / result / curation split is how this
        repository is organised and it is on the documentation site. It
        answers no question a visitor to the base URL is asking, and a
        section of it sat between the search box and the worked example
        for exactly as long as it took someone to read the page.

        This is not a weakened version of the test that used to check
        the section's wording: that section is deliberately gone, and
        what replaces its test is the assertion that it stays gone.
        """
        with client_factory() as c:
            body = c.get("/").text
        assert "append-only" not in body
        assert "roles-heading" not in body
        assert 'class="behaviour"' not in body
        assert re.search(r"<dt>(identity|provenance|result|curation)\b", body) is None

    def test_page_separates_citing_the_software_from_citing_a_release(self, client_factory):
        with client_factory() as c:
            body = c.get("/").text
        assert "CITATION.cff" in body
        assert "GET /api/v1/scientific/releases" in body
        assert "changelog_entry" in body

    def test_page_points_at_the_api_base_path_the_client_and_the_status_route(self, client_factory):
        with client_factory() as c:
            body = c.get("/").text
        assert "<code>/api/v1</code>" in body
        assert "tckdb-client" in body
        assert 'href="/api/v1/status"' in body

    def test_page_states_no_corpus_size(self, client_factory):
        """No invented counts. Every number on the page is from a file.

        The only digits the page may carry are the ones in the worked
        example (a real check's real input and output) and the version
        path ``/api/v1``. A record count, a species count or an uptime
        figure would be a number nobody could source.
        """
        with client_factory() as c:
            body = c.get("/").text
        prose = re.sub(r"<style>.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
        prose = re.sub(r"<[^>]+>", " ", prose)
        forbidden = re.compile(
            r"\b\d[\d,]*\s*(records?|species|reactions?|calculations?|users?|"
            r"uptime|downloads?)\b",
            re.IGNORECASE,
        )
        assert forbidden.search(prose) is None


class TestLandingPageIsSelfContained:
    """No CDN, no external stylesheet, no web font, no remote anything."""

    def test_no_element_fetches_a_subresource(self, client_factory):
        """Nothing on the page causes a second request to anywhere.

        The page carries one inline ``<script>`` -- the live search --
        which is why this no longer forbids the tag outright. An inline
        script fetches nothing at parse time, so the property under
        test is unchanged; what it now pins is *which* script shapes
        are allowed. Exactly one, carrying no attributes at all, so
        ``src``, ``type="module"``, ``integrity`` and every other route
        to an off-host fetch are excluded by the same assertion.
        """
        with client_factory() as c:
            body = c.get("/").text
        assert re.search(r"<(link|img|iframe|source|object|embed)\b", body, re.IGNORECASE) is None
        assert re.findall(r"<script\b([^>]*)>", body, re.IGNORECASE) == [""]

    def test_the_inline_script_reaches_only_this_origin(self, client_factory):
        """Same-origin paths only: the API is served by this same app.

        A ``fetch`` to another host would defeat every other guarantee
        on this page while leaving the markup looking self-contained,
        so the URLs the script builds are checked directly.
        """
        with client_factory() as c:
            body = c.get("/").text
        source = re.search(r"<script>(.*?)</script>", body, flags=re.DOTALL | re.IGNORECASE).group(1)
        literals = re.findall(r"\"([^\"]*)\"", source)
        assert literals, "no string literals found -- the script did not parse as expected"
        for literal in literals:
            assert "://" not in literal, literal
            assert not literal.startswith("//"), literal
        assert SPECIES_SEARCH_PATH in literals

    def test_no_stylesheet_import_or_url_reaches_off_host(self, client_factory):
        with client_factory() as c:
            body = c.get("/").text
        style = re.search(r"<style>(.*?)</style>", body, flags=re.DOTALL | re.IGNORECASE)
        assert style is not None
        css = style.group(1)
        assert "@import" not in css
        assert "url(" not in css

    def test_every_absolute_url_is_a_link_a_reader_clicks(self, client_factory):
        """External URLs are allowed as destinations, never as resources.

        A page may link to the documentation site. It may not *load*
        anything from it -- that is the difference between working on an
        air-gapped laptop and rendering blank.
        """
        with client_factory() as c:
            body = c.get("/").text
        allowed_prefixes = (DOCS_URL, REPO_URL)
        for url in set(re.findall(r"https?://[^\"'\s<>)]+", body)):
            assert url.startswith(allowed_prefixes), url


class TestLiveSearch:
    """The hero queries this deployment and renders what comes back.

    The page's whole purpose is to lead somewhere, and everything here
    is a way of asking "does it?". The endpoint is the public read API
    on this same origin, so no key, no CORS and no proxy are involved.
    """

    def test_the_search_is_a_real_form_aimed_at_the_public_read_endpoint(self, document):
        """Two searches, two real GET forms, and nothing else.

        This asserted one form until reaction search was added. It is
        still an exhaustive assertion -- the count is pinned and both
        actions are named -- so a third form, or a form aimed anywhere
        but a public read endpoint, still fails here.
        """
        forms = [
            attrs
            for attrs, ancestors in document.find("form")
            if "noscript" not in ancestors
        ]
        assert [attrs["action"] for attrs in forms] == [
            SPECIES_SEARCH_PATH,
            REACTION_SEARCH_PATH,
        ]
        for attrs in forms:
            assert attrs["method"] == "get"

    def test_the_box_is_seeded_with_a_query_that_returns_a_record(self, document):
        """Nobody's first sight of the search is an empty box.

        The seed is a real identifier with real records behind it, and
        it is in the markup rather than only in the script, so the
        no-script form is pre-filled with it too.
        """
        seeded = document.find("input", id="search-input")
        assert len(seeded) == 1
        attrs, _ = seeded[0]
        assert attrs["name"] == SEED_FIELD
        assert attrs["value"] == SEED_VALUE

    def test_the_identifier_is_chosen_explicitly_and_never_guessed(self, document):
        """``O`` is a formula and a SMILES, meaning two different things.

        Sniffing the input would answer one of the two questions the
        visitor might have asked and silently discard the other, so the
        field is picked, defaults to formula, and every offered field is
        a query parameter the endpoint really accepts.
        """
        options = [attrs for attrs, ancestors in document.find("option")]
        assert [attrs["value"] for attrs in options] == [field for field, _ in SEARCH_FIELDS]
        assert options[0]["value"] == SEED_FIELD
        assert "selected" in options[0]
        for attrs in options[1:]:
            assert "selected" not in attrs

    def test_every_offered_example_is_a_link_that_runs_the_query(self, document):
        """The examples work before any script does, and after it.

        Each is an ordinary link to the endpoint -- so it is useful with
        scripting off -- and carries the field it means, so the script
        can run it in place instead of navigating away.
        """
        chips = document.find("a", **{"class": "chip"})
        assert len(chips) == len(SEARCH_EXAMPLES)
        for (attrs, _), (field, value) in zip(chips, SEARCH_EXAMPLES, strict=True):
            assert attrs["data-field"] == field
            assert attrs["href"] == f"{SPECIES_SEARCH_PATH}?{field}={quote(value, safe='')}"

    def test_the_results_area_is_announced_when_it_changes(self, document):
        regions = document.find("div", id="results")
        assert len(regions) == 1
        assert regions[0][0]["aria-live"] == "polite"

    def test_every_review_state_has_a_word_a_reader_understands(self, script):
        """Under review is displayed, never hidden and never dressed up.

        Serving records that have not been approved yet, with the label
        on them, is the point of the review model. All five states are
        given a human wording so no badge can fall through to a raw
        enum name.
        """
        for state in REVIEW_STATES:
            assert f'"{state}"' in script, state

    def test_a_result_shows_the_review_state_of_each_entry(self, script):
        """The badge is built from the record's own review block.

        Not from a default, and not only from the summary counts: a
        result that renders without saying how far it was checked is
        the failure this asserts against.
        """
        entry_renderer = re.search(
            r"function entryNode\(entry\) \{(.*?)\n  \}", script, flags=re.DOTALL
        )
        assert entry_renderer is not None
        assert "reviewBadge(entry.review" in entry_renderer.group(1)

    def test_a_result_leads_onward_to_every_product_it_has(self, script):
        """A landing page that leads nowhere is the defect being fixed.

        Every product the search says a record has gets its own control
        aimed at the endpoint that serves it, so a visitor can follow a
        result into the data rather than reading about it.
        """
        for path in (
            "/api/v1/scientific/thermo/search",
            "/api/v1/scientific/species-entries/",
            "/api/v1/scientific/conformers/search",
            "/api/v1/scientific/species-calculations/search",
        ):
            assert f'"{path}"' in script, path
        assert 'searchUrl("species_ref"' in script

    def test_an_empty_result_says_so_and_offers_queries_that_work(self, script):
        """Most searches will miss. None of them may look broken.

        The corpus is small, so the empty state is a normal outcome: it
        says plainly that nothing matched and hands back the same
        examples that do return records.
        """
        empty = re.search(r"function emptyNode\(field, value\) \{(.*?)\n  \}", script, re.DOTALL)
        assert empty is not None
        assert "Nothing matched" in empty.group(1)
        assert "exampleList(" in empty.group(1)

    def test_a_submission_with_no_identifier_asks_the_api_and_shows_its_answer(self, script):
        """The endpoint answers ``missing_identifier`` -- so show that.

        An empty value is deliberately *not* sent as ``?formula=``,
        which the endpoint reads as a present-but-empty filter and
        answers with zero matches. Omitting the parameter is what
        produces the real 422, whose own ``detail`` is then rendered
        rather than a swallowed error.
        """
        assert re.search(r"if \(!value\) \{ return SEARCH; \}", script)
        assert "body.detail" in script
        assert "failure-code" in script
        assert "body.code" in script


class TestSearchDegradesWithoutJavaScript:
    """Scripting off must leave a page that still searches."""

    def test_the_picker_ships_disabled_rather_than_lying_about_the_field(self, document):
        """An HTML form cannot rename its own field.

        A live-looking picker that cannot change what is submitted
        would search formula while claiming to search SMILES, which is
        worse than not offering the control. It is enabled by the
        script, and ``<noscript>`` explains what to use instead.
        """
        pickers = document.find("select", id="search-field")
        assert len(pickers) == 1
        assert "disabled" in pickers[0][0]
        assert "picker.disabled = false" in render_landing_page(api_reference_path=None)

    def test_noscript_carries_a_plain_form_for_each_other_identifier(self, document):
        """One parameter per request, because empty siblings are not absent.

        ``?formula=CH3&smiles=`` is not "search CH3": the endpoint
        AND-combines the empty ``smiles`` and returns nothing. So the
        fallback is one single-field form per identifier rather than
        one form carrying all three.
        """
        fallback_forms = _noscript_forms(document, SPECIES_SEARCH_PATH)
        assert fallback_forms
        for attrs in fallback_forms:
            assert attrs["method"] == "get"

        named = {
            attrs["name"]
            for attrs, ancestors in document.find("input")
            if "noscript" in ancestors
            and attrs.get("name")
            and attrs["name"] not in {side for side, _, _ in REACTION_SIDES}
        }
        offered = {field for field, _ in SEARCH_FIELDS}
        assert named == offered - {SEED_FIELD}
        assert len(fallback_forms) == len(named)

    def test_nothing_but_the_rendering_depends_on_the_script(self, document):
        """The form and the examples are markup, not script output.

        They are asserted here as elements of the parsed document, so a
        refactor that moved the search UI into JavaScript -- leaving a
        blank box for anyone with scripting off -- fails rather than
        passing quietly.
        """
        assert document.find("input", id="search-input")
        assert document.find("button", type="submit")
        assert document.find("a", **{"class": "chip"})


class TestReactionSearchTakesASetPerSide:
    """The half of the hero that searches reactions, not species.

    One property dominates this class and is worth stating once. A
    reaction query is a *set* per side, and the endpoint spells a set
    as repeated query parameters::

        ?reactants=NN&reactants=[OH]     the reactions with both among
                                         their reactants
        ?reactants=NN,[OH]               one species literally named
                                         "NN,[OH]"

    The second is not an error. It returns ``200`` with nothing in it,
    which is the least debuggable answer an API can give -- so the
    tests below check the *shape of every URL the page builds*, in the
    markup, in the script, and in the ``curl`` example, rather than
    checking that a reaction box exists.
    """

    def test_two_species_on_one_side_become_two_parameters(self):
        """The mistake this whole design exists to avoid.

        Joining the side with a comma would leave this URL parsing to
        ``{"reactants": ["[NH2],NN"]}`` -- one value, one species name,
        nothing matched -- so this fails on exactly that regression and
        on nothing else.
        """
        url = reaction_query(("[NH2]", "NN"), ())
        assert parse_qs(urlparse(url).query) == {"reactants": ["[NH2]", "NN"]}
        assert url.count("reactants=") == 2
        assert "," not in url

    def test_both_sides_repeat_independently(self):
        url = reaction_query(("NN", "[OH]"), ("N=N", "O"))
        assert parse_qs(urlparse(url).query) == {
            "reactants": ["NN", "[OH]"],
            "products": ["N=N", "O"],
        }

    def test_a_side_left_out_is_left_out_rather_than_sent_empty(self):
        """An empty sibling is not an absent one.

        ``?reactants=NN&products=`` is AND-combined by the endpoint and
        returns nothing, so "unconstrained on the other side" has to be
        the *absence* of the parameter, never a present-but-blank one.
        """
        url = reaction_query(("NN",), ())
        assert parse_qs(urlparse(url).query, keep_blank_values=True) == {"reactants": ["NN"]}
        assert "products" not in url

    def test_no_reaction_url_anywhere_on_the_page_joins_a_side(self, page):
        """Every link, chip and shown command, checked the same way.

        A builder can be correct while one hand-written ``href`` on the
        page is not, and that ``href`` is what a reader with scripting
        off actually follows. So this parses every reaction-search URL
        in the rendered document and asserts no single value carries a
        separator -- and that at least one of them really does repeat a
        parameter, or the assertion above it would be vacuous.
        """
        urls = re.findall(r'href="([^"]*' + re.escape(REACTION_SEARCH_PATH) + r'[^"]*)"', page)
        assert urls
        repeated = 0
        for raw in urls:
            query = parse_qs(urlparse(html.unescape(raw)).query, keep_blank_values=True)
            for name, values in query.items():
                assert name in {side for side, _, _ in REACTION_SIDES}, name
                if len(values) > 1:
                    repeated += 1
                for value in values:
                    assert value, "a blank side is an absent side, not an empty parameter"
                    assert "," not in value, value
                    assert " " not in value, value
        assert repeated, "no offered link exercises the repeated-parameter form"

    def test_the_script_builds_one_parameter_per_species(self, script):
        """The same property, in the code the browser actually runs.

        Checked structurally because the assertion is about a URL the
        test suite cannot execute: the loop that pushes one
        ``reactants=`` per element, and the absence of any join with a
        separator anywhere in the script.
        """
        builder = re.search(r"function rxnUrl\(.*?\n  \}", script, flags=re.DOTALL)
        assert builder is not None
        source = builder.group(0)
        for side, _, _ in REACTION_SIDES:
            assert f'parts.push("{side}=" + encodeURIComponent({side}[i]))' in source, side
        assert 'parts.join("&")' in source
        assert 'join(",")' not in script
        assert 'join(", ")' not in script

    def test_the_chips_and_the_builder_cannot_drift(self, document):
        """Each offered example is a real link to the query it names."""
        chips = document.find("a", **{"class": "chip rxn-chip"})
        assert len(chips) == len(REACTION_EXAMPLES)
        for index, ((attrs, _), (reactants, products)) in enumerate(
            zip(chips, REACTION_EXAMPLES, strict=True)
        ):
            assert attrs["data-example"] == str(index)
            assert html.unescape(attrs["href"]) == reaction_query(reactants, products)
        assert any(len(reactants) > 1 or len(products) > 1 for reactants, products in REACTION_EXAMPLES), (
            "one offered example must carry two species on a side"
        )

    def test_the_form_is_seeded_with_a_query_that_returns_records(self, document):
        """Nobody's first sight of reaction search is an empty box.

        Both sides are seeded, and every field is ``required``: a plain
        no-script submit therefore cannot send a blank side, which the
        endpoint would read as a filter matching nothing.
        """
        for side, values in (("reactants", SEED_REACTANTS), ("products", SEED_PRODUCTS)):
            fields = [
                attrs
                for attrs, ancestors in document.find("input")
                if attrs.get("name") == side and "noscript" not in ancestors
            ]
            assert [attrs["value"] for attrs in fields] == list(values)
            for attrs in fields:
                assert "required" in attrs
                assert attrs.get("aria-label")
        assert SEED_REACTANTS and SEED_PRODUCTS

    def test_the_script_takes_the_validation_off_the_form_it_took_over(self, script):
        """``required`` is for the no-script path and only that path.

        With the script running, blank fields are skipped rather than
        submitted, so leaving a side empty must be allowed; the browser
        would otherwise refuse the submit before the handler saw it.
        """
        assert "rxnForm.noValidate = true" in script
        assert "if (value) { values.push(value); }" in script

    def test_noscript_can_still_ask_for_two_species_on_a_side(self, document):
        """A plain form emits repeated same-named inputs. Use that.

        This is the whole reason the fallback is a set of forms rather
        than a sentence apologising: without a script, HTML alone can
        express ``reactants=A&reactants=B``.
        """
        forms = _noscript_forms(document, REACTION_SEARCH_PATH)
        assert forms
        counted = {}
        for side, _, _ in REACTION_SIDES:
            names = [
                attrs
                for attrs, ancestors in document.find("input")
                if "noscript" in ancestors and attrs.get("name") == side
            ]
            assert names, side
            for attrs in names:
                assert "required" in attrs, "an empty sibling parameter matches nothing"
            counted[side] = len(names)
        # One single-field form and one two-field form per side.
        assert counted == {side: 3 for side, _, _ in REACTION_SIDES}
        assert len(forms) == 2 * len(REACTION_SIDES)

    def test_a_result_shows_the_equation_and_how_far_it_was_checked(self, script):
        """The equation is the legible thing; the review state is honest.

        ``under_review`` is a normal state for a served record and is
        drawn with the same neutral badge as everything else -- the
        assertion for that is on the stylesheet, in
        :meth:`TestReviewStateIsInformation.test_only_deprecated_and_rejected_use_the_negative_phase`.

        The equation element is now filled through ``equationText``,
        which typesets the API's ``<=>`` as an arrow and touches
        nothing else. What is pinned here is unchanged: the element
        exists, and the only record field it is built from is
        ``record.equation``. ``equationText`` itself is checked in
        :class:`TestResultsReadAsAPageNotAPayload`.
        """
        renderer = re.search(r"function rxnRecordNode\(record\) \{.*?\n  \}", script, re.DOTALL)
        assert renderer is not None
        source = renderer.group(0)
        assert 'make("span", "equation",' in source
        assert "equationText(record.equation)" in source
        assert "reviewBadge(record.review" in source

    def test_a_result_says_which_way_round_the_query_matched(self, script):
        """``reverse`` is information: the reactants asked for are its products."""
        assert "DIRECTION_WORDS[record.matched_direction]" in script
        for word in ("matched forward", "matched in reverse", "matched either way"):
            assert f'"{word}"' in script, word

    def test_a_result_leads_onward_to_the_evidence_it_has(self, script):
        """A landing page that leads nowhere is the defect being fixed."""
        for path in (
            "/api/v1/scientific/reaction-entries/",
            "/api/v1/scientific/transition-states/search",
        ):
            assert f'"{path}"' in script, path
        sections = re.search(r"var RXN_SECTIONS = \[.*?\n  \];", script, flags=re.DOTALL)
        assert sections is not None
        assert "a.has_transition_state" in sections.group(0)
        assert "a.kinetics_count" in sections.group(0)
        assert "no kinetics or transition state yet" in script

    def test_an_empty_result_reads_as_an_outcome_and_offers_one_that_works(self, script):
        empty = re.search(
            r"function rxnEmptyNode\(reactants, products\) \{.*?\n  \}", script, re.DOTALL
        )
        assert empty is not None
        assert "Nothing matched" in empty.group(0)
        assert "rxnExampleList(" in empty.group(0)
        assert '"These return records:"' in script

    def test_a_query_with_no_filter_shows_the_endpoints_own_refusal(self, script, page):
        """The 422 is ``missing_reaction_search_filter``, so show that.

        Clearing both sides omits every participant parameter, which is
        what produces the real refusal; its ``code`` and ``detail`` are
        then rendered. The code is deliberately not written anywhere on
        this page -- what the reader sees is what the endpoint said.
        """
        assert re.search(r"if \(!parts\.length\) \{ return RXN_SEARCH; \}", script)
        assert "body.code" in script
        assert "body.detail" in script
        assert not [
            literal for literal in re.findall(r'"([^"]*)"', script)
            if "missing_reaction_search_filter" in literal
        ], "the code is the endpoint's to state, not this page's to guess"
        prose = re.sub(r"<script>.*?</script>", "", page, flags=re.DOTALL | re.IGNORECASE)
        prose = re.sub(r"<style>.*?</style>", "", prose, flags=re.DOTALL | re.IGNORECASE)
        assert "missing_reaction_search_filter" not in prose

    def test_the_render_is_capped_and_never_lies_about_the_total(self, script):
        """The count is the endpoint's; the cap is only how much is drawn."""
        assert "RXN_LIMIT" in script
        assert 'parts.push("limit=" + limit)' in script
        assert "rxnUrl(reactants, products, RXN_LIMIT)" in script
        # The "more than fits" line links the *uncapped* URL.
        assert "rxnUrl(reactants, products)" in script
        assert "Open the full response as raw JSON" in script

    def test_the_results_area_is_announced_when_it_changes(self, document):
        regions = document.find("div", id="rxn-results")
        assert len(regions) == 1
        assert regions[0][0]["aria-live"] == "polite"


class TestBothSearchesSurviveWithoutTheSwitch:
    """The species/reactions switch is an enhancement, never a gate."""

    def test_both_panels_are_markup_before_any_script_runs(self, document):
        for panel, action in (
            ("panel-species", SPECIES_SEARCH_PATH),
            ("panel-reactions", REACTION_SEARCH_PATH),
        ):
            found = document.find("div", id=panel)
            assert len(found) == 1, panel
            assert "hidden" not in found[0][0], panel
            assert [
                attrs
                for attrs, ancestors in document.find("form")
                if attrs["action"] == action and "noscript" not in ancestors
            ], action

    def test_the_switch_ships_hidden_and_the_script_builds_it(self, document, page, script):
        """A tab that cannot change what is shown is worse than no tab.

        And ``.modes`` sets ``display: flex``, which beats the user
        agent's ``[hidden]`` rule -- so the stylesheet has to turn it
        off explicitly or the control would be visible to exactly the
        readers who cannot use it.
        """
        modes = document.find("div", id="modes")
        assert len(modes) == 1
        assert "hidden" in modes[0][0]
        assert ".modes[hidden] { display: none; }" in page
        assert "modes.hidden = false" in script
        assert 'modes.setAttribute("role", "tablist")' in script
        assert 'setAttribute("role", "tabpanel")' in script

    def test_the_page_keeps_exactly_one_top_level_heading(self, document):
        assert len(document.find("h1")) == 1

    def test_a_two_field_fallback_form_is_allowed_to_wrap(self, page):
        """Measured, not guessed: this regressed and was caught in a browser.

        A reaction fallback form carries two inputs and a button. At
        their default size that is 542px of content inside a 360px
        viewport, and an ``<input>`` will not shrink below its size
        attribute unless it is told it may -- so with scripting off the
        whole document scrolled sideways, which is the one thing the
        narrow layout is not allowed to do.
        """
        css = re.search(r"<style>(.*?)</style>", page, flags=re.DOTALL | re.IGNORECASE).group(1)
        form_rule = re.search(r"\.fallback form \{([^}]*)\}", css)
        assert form_rule is not None
        assert "flex-wrap: wrap" in form_rule.group(1)
        assert "max-width: 100%" in form_rule.group(1)
        input_rule = re.search(r"\.fallback input \{([^}]*)\}", css)
        assert input_rule is not None
        assert "min-width: 0" in input_rule.group(1)


class TestReviewStateIsInformation:
    """Under review is a state, not a warning, on every result."""

    def test_only_deprecated_and_rejected_use_the_negative_phase(self, page):
        """Most of what this deployment serves is under review.

        Drawing that in the colour reserved for a rejected record would
        put a warning on the ordinary case, which is both wrong and the
        opposite of what the review model is for.
        """
        css = re.search(r"<style>(.*?)</style>", page, flags=re.DOTALL | re.IGNORECASE).group(1)
        flagged = set()
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
            if "--phase-neg" not in body:
                continue
            flagged.update(re.findall(r"\.badge-(\w+)", selectors))
        assert flagged == {"deprecated", "rejected"}


class TestShownCommandsRunAsPrinted:
    """A command a reader pastes has to work when they change the value."""

    def test_every_curl_disables_globbing(self, page):
        """``curl`` reads ``[...]`` as a glob, and SMILES are full of it.

        ``curl -s ".../search?smiles=[OH]"`` fails in the client before
        it reaches any server, with an error about a bad range -- which
        reads as the API being broken. ``-g`` turns globbing off, and
        every command on the page carries it because the page invites
        substitution.
        """
        commands = re.findall(r"curl [^\n<]*", page)
        assert commands
        for command in commands:
            flags = re.match(r"curl\s+(-[A-Za-z]+)", command)
            assert flags is not None, command
            assert "g" in flags.group(1), command
        assert any("[" in command for command in commands), (
            "no shown command contains a bracketed SMILES, so -g would be untested"
        )

    def test_the_page_says_why_the_flag_is_there(self, page):
        assert "shell globs" in page
        assert "<code>-g</code>" in page

    def test_the_reaction_command_shows_the_repeated_parameter_form(self, page):
        """The shape is the lesson, and one shown command teaches it.

        Pasted with a comma instead -- ``?products=[H][H],N=N`` -- the
        same command returns ``200`` and nothing, so the example has to
        be the correct shape rather than merely a working URL.
        """
        commands = [c for c in re.findall(r"curl [^\n<]*", page) if REACTION_SEARCH_PATH in c]
        assert len(commands) == 1
        command = html.unescape(commands[0])
        query = parse_qs(command.split("?", 1)[1].rstrip('"'))
        assert len(query["products"]) == 2, command
        for value in query["products"]:
            assert "," not in value


class TestResultsExpandInPlace:
    """Following a result must not surprise the reader with raw JSON.

    The endpoints behind a result answer with a nested JSON document.
    That is the right shape for a client and an unreadable one for a
    person who clicked a link on a landing page -- and it misleads: an
    array index reads as a count, an observation total reads as a
    conformer count. So each product opens on the page instead, and
    every remaining route to the raw document is labelled as one.
    """

    def test_each_product_is_a_disclosure_rather_than_a_navigation(self, script):
        """The pill expands the card; it does not leave the page.

        A ``<button>`` with ``aria-expanded`` rather than an ``<a>``,
        because an anchor pointing at the JSON is a middle-click away
        from the wall of keys however its click handler behaves.
        """
        assert 'make("button", "pill"' in script
        assert 'button.setAttribute("aria-expanded", "false")' in script
        assert 'button.setAttribute("aria-controls", id)' in script
        assert "loadDetail(panel, section, ref)" in script
        # No product control is an anchor any more.
        assert 'anchor(THERMO' not in script
        assert 'anchor(CONFORMERS' not in script
        assert 'anchor(CALCULATIONS' not in script

    def test_every_route_to_the_raw_document_says_it_is_raw_json(self, script):
        """Nobody may be surprised by where a link takes them.

        Three anchors still point at an API document -- the per-product
        link inside an expanded panel, the per-record one, and the
        "there are more pages" one -- and each names its destination.
        The assertion is on the anchors the script builds rather than
        on a count of the phrase, so an anchor added later without a
        label fails here.

        The example chips are the one exception and are excluded by
        name. They are anchors *because* they must work with scripting
        off, which is the whole reason their ``href`` is the endpoint;
        with the script running their click is intercepted and never
        navigates, and the results area says in prose that without
        JavaScript the examples go to the API's JSON.
        """
        anchors = re.findall(r"anchor\((.*?)\);", script, flags=re.DOTALL)
        json_anchors = [
            call
            for call in anchors
            if ("section.url(" in call or "searchUrl(" in call) and '"chip"' not in call
        ]
        assert len(json_anchors) >= 3
        for call in json_anchors:
            assert "raw JSON" in call, call
        assert "the box and examples go" in render_landing_page(api_reference_path=None)

    def test_a_panel_reports_counts_and_never_the_group_scope_booleans(self, script):
        """``has_*`` on a conformer group is an OR across observations.

        A group-scoped ``has_scf_stability`` says *some* observation in
        the group has it, while reading as a fact about the conformer.
        Those fields are being replaced with counts in a separate
        change; presenting them here would put a wrong sentence on a
        public page and would still be wrong after the fix lands. The
        panel shows counts the payload states outright instead.
        """
        for field in (
            "observation_count",
            "calculation_count",
            "geometry_count",
            "source_calculation_count",
        ):
            assert field in script, field
        for boolean in ("has_scf_stability", "has_opt", "has_freq", "has_sp"):
            assert boolean not in script, boolean

    def test_an_observation_count_is_never_called_a_conformer_count(self, script):
        """Five observations are one conformer seen five times.

        A conformer group is one torsional basin -- identity, deduped.
        An observation is one deposited instance assigned to that basin
        -- provenance, append-only. Labelling the second as the first
        is the specific misreading this panel exists to prevent, so the
        count line counts groups and the observation line says what it
        is counting.
        """
        conformers = re.search(
            r'\{\s*key: "conformers",(.*?)\n    \}', script, flags=re.DOTALL
        )
        assert conformers is not None
        block = conformers.group(1)
        assert 'one: "conformer group"' in block
        assert 'many: "conformer groups"' in block
        assert "torsional basin" in block
        assert "one conformer seen five times" in block
        assert "five conformers" in block

        view = re.search(
            r"function conformerView\(record\) \{(.*?)\n  \}", script, flags=re.DOTALL
        )
        assert view is not None
        assert '"observation", "observations"' in view.group(1)
        assert "of this one group" in view.group(1)

    def test_the_no_script_fallback_is_untouched_by_the_expansion(self, document):
        """None of the above may cost the scripting-off reader anything.

        The expansion is script-only by construction: it renders search
        results, which only exist once a fetch has run. What has to
        survive is everything that works before one does.
        """
        assert len(_noscript_forms(document, SPECIES_SEARCH_PATH)) == len(SEARCH_FIELDS) - 1
        assert document.find("input", id="search-input")
        assert document.find("a", **{"class": "chip"})


class TestResultsReadAsAPageNotAPayload:
    """A result card must read as a rendered record, not as a payload.

    The search worked and the data was real long before this class
    existed; what the cards still looked like was a JSON viewer with
    nicer fonts. Four things did that, and each has a test here.

    * **Column names as labels.** ``inchi_key``, ``h298_kj_mol`` and
      ``multiplicity`` are what the database calls its columns. A
      reader wants "InChIKey" and "ΔH°(298 K)".
    * **Units missing, or spelled into the label.** A fixed-unit
      column carries its unit in its *name* -- that is the point of
      ``docs/unit_policy.md`` -- so that the reader can be shown the
      unit properly. Printing the column name shows them neither the
      quantity nor the unit.
    * **Nulls that vanish.** A skipped row is indistinguishable from a
      row nobody thought to render, so the reader cannot tell "this
      record carries no uncertainty" from "this page forgot to show
      one".
    * **No hierarchy.** Molecule, charge, provenance and public ref set
      at one size leaves the reader to work out the reading order
      themselves, which is exactly the work a JSON document leaves
      them to do.

    Two chemistry renderings are pinned as well, both of which are
    arithmetic on a value the record states and neither of which
    infers anything past it: multiplicity gains its spin word beside
    the number, and charge gains a sign.
    """

    #: The value renderers, and the record-level renderers around them.
    VIEWS = (
        "thermoView(record)",
        "statmechView(record)",
        "transportView(record)",
        "conformerView(record)",
        "calculationView(record)",
        "kineticsView(record)",
        "transitionStateView(record)",
    )

    #: Units this page is allowed to print, and the only ones it does.
    #: A unit that is not on this list is either a typo or a quantity
    #: nobody checked, and both should fail loudly rather than reach a
    #: public page.
    UNITS = frozenset({"kJ/mol", "J/mol·K", "K", "Å", "D", "hartree"})

    @staticmethod
    def _labels(body: str) -> list[str]:
        """Every field label a view puts on screen.

        Labels are the first element of each ``["...", value]`` pair in
        a view's ``headline`` / ``facts`` / ``provenance`` arrays.
        """
        return re.findall(r'\[\s*"([^"]+)",', body)

    def test_no_label_a_reader_sees_is_a_database_column_name(self, script):
        """``h298_kj_mol`` is a column. "ΔH°(298 K)" is what it means.

        Checked over every label every view emits rather than over a
        list of the ones that used to be wrong, so a view added later
        with a raw key in it fails here.
        """
        seen = 0
        for signature in self.VIEWS:
            body = _js_function(script, signature)
            labels = self._labels(body)
            assert labels, signature
            for label in labels:
                seen += 1
                assert "_" not in label, (signature, label)
                assert label[0].isupper() or not label[0].isascii(), (signature, label)
        assert seen >= 25, seen
        # The record renderers label two more things: the identifier
        # rows and the state chips beside the molecule.
        renderers = ("recordNode(record)", "entryNode(entry)", "rxnRecordNode(record)")
        for signature in renderers:
            body = _js_function(script, signature)
            for label in self._labels(body) + re.findall(r'stateCell\(\s*"([^"]+)"', body):
                seen += 1
                assert "_" not in label, (signature, label)
        for label in ("Formula", "InChIKey", "Species ref", "Entry ref"):
            assert f'"{label}"' in script, label
        # The column names those rows replaced. They are still legal
        # elsewhere in the script -- ``inchi_key`` is a real query
        # parameter and the identifier picker sends it -- so this is
        # scoped to the renderers rather than to the whole page.
        rendered = "".join(_js_function(script, s) for s in self.VIEWS + renderers)
        for column in ("inchi_key", "species_entry_ref", "h298_kj_mol", "s298_j_mol_k"):
            assert f'"{column}"' not in rendered, column

    def test_a_quantity_is_a_number_and_a_unit_in_its_own_element(self, script, page):
        """The unit is rendered, and rendered as a unit.

        Two halves. Every unit the views pass is one of the units this
        page knows how to print -- so a column name cannot be smuggled
        in as a unit string -- and the renderer puts it in its own
        element with its own style, rather than gluing it onto the
        number as text.
        """
        units = set()
        for signature in self.VIEWS:
            body = _js_function(script, signature)
            for unit in re.findall(r'(?:fixed|scientific)\([^()]*,\s*\d+,\s*"([^"]+)"\)', body):
                units.add(unit)
            # A span built as an object literal rather than through a
            # formatter still names a unit, and is checked the same way.
            for unit in re.findall(r'unit:\s*"([^"]+)"', body):
                units.add(unit)
        assert units, "no view passes a unit at all"
        assert units <= self.UNITS, units - self.UNITS
        assert {"kJ/mol", "J/mol·K"} <= units
        filler = _js_function(script, "fillValue(node, value)")
        assert 'make("span", "unit", value.unit)' in filler
        css = _stylesheet(page)
        assert re.search(r"\.unit\s*\{[^}]*color:\s*var\(--muted\)", css) is not None

    def test_an_arrhenius_prefactor_carries_the_units_the_enum_names(self, script):
        """``cm3_mol_s`` is an enum token; cm³ mol⁻¹ s⁻¹ is the unit.

        Asserted against the enum itself, so a member added to
        :class:`ArrheniusAUnits` without a typeset form fails here
        rather than reaching the page as an underscored token.
        """
        typeset = _js_map(script, "A_UNITS")
        assert set(typeset) == {member.value for member in ArrheniusAUnits}
        for token, unit in typeset.items():
            assert "_" not in unit, (token, unit)
        assert typeset["per_s"].startswith("s")
        assert typeset["cm3_mol_s"].startswith("cm³")

    def test_a_value_the_record_does_not_carry_says_so(self, script):
        """A null renders "not recorded"; it does not disappear.

        The renderer used to ``continue`` past a null pair. That is the
        one behaviour this test exists to keep out: it makes an absent
        value and an unrendered one look identical on screen.
        """
        assert 'var ABSENT = "not recorded";' in script
        filler = _js_function(script, "fillValue(node, value)")
        assert "value === null || value === undefined" in filler
        assert "ABSENT" in filler
        fields = _js_function(script, "fieldList(pairs, cls)")
        assert "continue" not in fields
        assert "fillValue(" in fields
        quantities = _js_function(script, "quantityBlock(pairs)")
        assert "continue" not in quantities
        assert "fillValue(" in quantities

    def test_multiplicity_is_shown_as_a_spin_state_and_as_the_number(self, script):
        """2 is a doublet, and both readings stay on the card.

        Multiplicity is 2S+1: the word is what a chemist reads and
        carries "one unpaired electron" with it; the number is what the
        API holds and what goes back into a query. Replacing either
        with the other costs a different reader, so the card shows the
        word with the number beside it.
        """
        spins = _js_map(script, "SPIN_WORDS")
        assert spins["1"] == "singlet"
        assert spins["2"] == "doublet"
        assert spins["3"] == "triplet"
        assert spins["4"] == "quartet"
        # 2S+1 has no zero and no fractions: the keys are the integers.
        assert set(spins) == {str(n) for n in range(1, len(spins) + 1)}
        strip = _js_function(script, "stateStrip(charge, multiplicity)")
        assert 'stateCell("spin state", spin,' in strip
        assert "String(multiplicity)" in strip
        cell = _js_function(script, "stateCell(label, value, aside)")
        assert 'make("span", "state-aside", "(" + aside + ")")' in cell
        # The same pairing inside a transition-state card.
        ts = _js_function(script, "transitionStateView(record)")
        assert 'spin + " (" + multiplicity + ")"' in ts

    def test_charge_is_rendered_as_a_sign_with_a_real_minus(self, script):
        """``-1`` is a hyphen and an integer. −1 is a charge.

        The minus is U+2212, not the ASCII hyphen a column of integers
        would leave behind, and the assertion checks the codepoint
        rather than the glyph so the two cannot be confused here
        either.
        """
        assert 'var MINUS = "' + chr(0x2212) + '";' in script
        charge = _js_function(script, "chargeText(charge)")
        assert 'return "+" + number;' in charge
        assert "return MINUS + Math.abs(number);" in charge
        assert 'return "0";' in charge
        assert "-" not in charge, "an ASCII hyphen is building a charge somewhere"
        strip = _js_function(script, "stateStrip(charge, multiplicity)")
        assert 'stateCell("charge", chargeText(charge)' in strip

    def test_a_formula_is_subscripted_only_when_it_round_trips(self, script):
        """A wrong formula is far worse than an unsubscripted one.

        The parts are reassembled and compared against the string that
        arrived; anything that does not match exactly is printed as it
        arrived. Without the comparison this would silently reshape a
        formula it had misread.
        """
        body = _js_function(script, "formulaNode(formula)")
        assert 'parts.join("") !== text' in body
        assert "node.textContent = text;" in body
        assert 'make("sub", split[2])' in body or 'make("sub", null, split[2])' in body

    def test_a_public_ref_is_present_copyable_and_not_the_headline(self, script, page):
        """It is what a citation needs and not what a reader reads.

        Three properties, and the third is the one that was wrong: the
        ref is on the card, it can be lifted out in one click, and it
        is set smaller than the molecule rather than beside it at the
        same weight. The size comparison is read out of the stylesheet
        so that it is a fact about what renders.
        """
        assert 'var REF_WORD = "Public ref";' in script
        idents = _js_function(script, "identList(rows)")
        assert 'make("code", "ident-value", value)' in idents
        assert "copyButton(value, label, code)" in idents
        css = _stylesheet(page)
        molecule = _css_declaration(css, ".smiles,\n.equation", "font-size")
        ident = _css_declaration(css, ".ident-value,\n.formula", "font-size")
        assert molecule == "1.25rem", molecule
        assert ident == "var(--fs-data)", ident
        data = float(_css_declaration(css, ":root", "--fs-data").removesuffix("rem"))
        assert data < float(molecule.removesuffix("rem"))

    def test_the_thermo_card_reads_provenance_from_the_shape_the_api_sends(self, script):
        """The level of theory used to be read off the wrong object.

        A thermo search row is ``{species, thermo}`` and the provenance
        block hangs off the ``thermo`` half. Read off the row it is
        always ``undefined``, so the level of theory was null on every
        card and the row was then silently dropped -- a field that
        looked absent because the page was looking in the wrong place.
        Both halves of that are asserted: the schema shape, from the
        models themselves, and the path the script takes.
        """
        assert "provenance" not in ThermoSearchRecord.model_fields
        assert "provenance" in ThermoRecord.model_fields
        body = _js_function(script, "thermoView(record)")
        assert "var provenance = thermo.provenance || {};" in body
        assert "record.provenance" not in body
        assert "primary.level_of_theory" in body

    @pytest.mark.parametrize(
        ("name", "enum"),
        (
            ("CALCULATION_WORDS", CalculationType),
            ("THERMO_MODEL_WORDS", ThermoModelKindQuery),
            ("KINETICS_MODEL_WORDS", KineticsModelKind),
        ),
    )
    def test_every_enum_token_the_page_prints_has_a_word(self, script, name, enum):
        """A token added to one of these enums cannot reach the page bare.

        These three are the enums whose tokens the page turns into
        prose. Every member has an entry, checked against the enum
        rather than against a copy of it, so the map cannot drift out
        of date while every other assertion here keeps passing.
        """
        mapping = _js_map(script, name)
        assert set(mapping) == {member.value for member in enum}
        for token, word in mapping.items():
            assert word and "_" not in word, (token, word)

    def test_the_equation_arrow_is_replaced_only_where_it_stands_alone(self, script):
        """``<=>`` between spaces is an arrow; anywhere else it is data.

        SMILES are full of ``=`` and brackets, so the substitution is
        anchored on the surrounding spaces. Splitting on the spaced
        token is what makes that true, and a version that replaced the
        bare token would corrupt a structure rather than typeset an
        equation.
        """
        body = _js_function(script, "equationText(equation)")
        assert '.split(" <=> ").join(" ⇌ ")' in body
        assert '.split(" => ").join(" → ")' in body
        assert '"<=>"' not in body

    def test_the_scripting_off_reader_loses_none_of_this(self, document, page):
        """Every rendering above is script-built, by construction.

        These cards render search results, which do not exist until a
        fetch has run. What must survive is what worked before one did,
        and none of it is touched by any of the above.
        """
        assert len(_noscript_forms(document, SPECIES_SEARCH_PATH)) == len(SEARCH_FIELDS) - 1
        assert len(_noscript_forms(document, REACTION_SEARCH_PATH)) >= 1
        assert document.find("input", id="search-input")
        assert re.findall(r"<script\b([^>]*)>", page, re.IGNORECASE) == [""]


class TestTrustVerdictIsShown:
    """An expanded result says how far its evidence has been checked.

    The page already showed what evidence a record *has*. What it did
    not show is the thing that separates this database from a folder of
    output files: how much of the evidence a rubric expects is actually
    there. That verdict rides under ``trust`` on the record and is
    opt-in.

    Two properties are load-bearing and are checked against the running
    API rather than against the page's own prose:

    * the token is asked for **by name**. ``include=trust`` is
      deliberately excluded from ``include=all`` on every surface,
      because evaluating a verdict pulls a large eager-load chain, so a
      page that reached for the convenience token would pay for the
      graph and still get no verdict.
    * the two lists that cannot serve it are not asked. Both answer
      ``422 unknown_include_token``, which would replace a working
      panel with an error.
    """

    #: Section keys whose list can carry a verdict, and the ones whose
    #: list cannot. Every section in the script must be in exactly one.
    TRUST_KEYS = frozenset({"thermo", "statmech", "transport", "kinetics", "transition-state"})
    NO_TRUST_KEYS = frozenset({"conformers", "calculations"})

    @staticmethod
    def _section(script: str, key: str) -> str:
        """One entry of ``SECTIONS`` / ``RXN_SECTIONS``, by its key."""
        block = re.search(
            r'\{\s*key: "' + re.escape(key) + r'",(.*?)\n    \}', script, flags=re.DOTALL
        )
        assert block is not None, key
        return block.group(1)

    @staticmethod
    def _function(script: str, signature: str) -> str:
        body = re.search(
            r"function " + re.escape(signature) + r" \{(.*?)\n  \}", script, flags=re.DOTALL
        )
        assert body is not None, signature
        return body.group(1)

    def test_every_section_is_classified_and_none_was_forgotten(self, script):
        """The two buckets above account for every section on the page.

        Without this, a section added later is silently outside both
        lists and every other test here keeps passing while saying
        nothing about it.
        """
        keys = set(re.findall(r'\n    \{\n      key: "([^"]+)"', script))
        assert keys == self.TRUST_KEYS | self.NO_TRUST_KEYS, keys

    def test_a_list_that_can_carry_a_verdict_asks_for_it_by_name(self, script):
        """``include=trust``, spelled out, on exactly the lists that serve it."""
        assert 'var TRUST_PARAM = "include=trust";' in script
        for key in self.TRUST_KEYS:
            block = self._section(script, key)
            assert "trust: function (record)" in block, key
            url = re.search(r"url: function \(ref\) \{(.*?)\n      \}", block, flags=re.DOTALL)
            assert url is not None, key
            assert "TRUST_PARAM" in url.group(1), key
        for key in self.NO_TRUST_KEYS:
            block = self._section(script, key)
            assert "trust:" not in block, key
            assert "TRUST_PARAM" not in block, key

    def test_the_page_never_reaches_for_the_convenience_token(self, script):
        """``include=all`` would cost the eager-load graph and yield no verdict.

        Not a style preference: ``trust`` is an internal token on every
        surface here, so ``all`` expands *without* it. Swapping the two
        would buy the whole selectinload chain and render nothing.

        Asserted over the script's string literals rather than over the
        page text, because the comment above ``TRUST_PARAM`` names the
        token it is refusing to use and should keep doing so.
        """
        literals = re.findall(r"\"([^\"]*)\"", script)
        assert literals
        for literal in literals:
            assert "include=all" not in literal, literal
        assert "include=trust" in literals

    @pytest.mark.parametrize(
        "path",
        (
            "/api/v1/scientific/thermo/search?species_entry_ref=spe_x",
            "/api/v1/scientific/transition-states/search?reaction_entry_ref=rxe_x",
        ),
    )
    def test_asking_for_all_would_not_have_delivered_the_verdict(self, client_factory, path):
        """The API itself decides this, so ask it.

        Each search echoes the include set it *resolved*. ``trust``
        resolves to itself; ``all`` resolves to a set that does not
        contain it. The nonsense token is the control: it proves this
        endpoint validates the include set at all, so a pass here can
        never come from the request being rejected earlier for some
        other reason.
        """
        with client_factory() as c:
            control = c.get(f"{path}&include=zzz_not_a_token")
            assert control.status_code == 422
            assert control.json()["code"] == "unknown_include_token"

            asked = c.get(f"{path}&include=trust")
            assert asked.status_code == 200
            assert "trust" in asked.json()["request"]["include"]

            convenient = c.get(f"{path}&include=all")
            assert convenient.status_code == 200
            assert "trust" not in convenient.json()["request"]["include"]

    @pytest.mark.parametrize(
        "path",
        (
            "/api/v1/scientific/conformers/search?species_entry_ref=spe_x",
            "/api/v1/scientific/species-calculations/search?species_entry_ref=spe_x",
        ),
    )
    def test_the_lists_that_refuse_the_token_are_not_asked(self, client_factory, path):
        """Asking these would replace a working panel with a 422."""
        with client_factory() as c:
            refused = c.get(f"{path}&include=trust")
            assert refused.status_code == 422
            assert refused.json()["code"] == "unknown_include_token"

    def test_every_verdict_the_rubric_can_return_has_a_word(self, script):
        """No badge may fall through to a raw enum name.

        The label set is read off the rubric's own enum, so a verdict
        added there fails here until the page has wording for it.
        """
        for badge in EvidenceBadge:
            word = badge.value.replace("_", " ")
            assert f"{badge.value}: \"{word}\"" in script, badge.value

    def test_the_words_are_the_rubrics_own_and_invent_no_scale(self, script, page):
        """Named verdicts, not stars, not a percentage, not a traffic light.

        The API publishes a *set* of labels and no ordering over them.
        Drawing them as a rank would assert something the contract does
        not, so every grade gets the same neutral chip: one ``.trust-grade``
        rule, no per-verdict selector, and no negative phase colour
        anywhere in the block.
        """
        css = re.search(r"<style>(.*?)</style>", page, flags=re.DOTALL | re.IGNORECASE).group(1)
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
            if ".trust" not in selectors:
                continue
            assert "--phase-neg" not in body, selectors
        for badge in EvidenceBadge:
            assert f".trust-{badge.value}" not in css, badge.value
        # The ratio exists on the wire and is deliberately not restated:
        # the spec forbids showing it as a percentage, and the counts of
        # named checks are what a reader can actually follow up.
        assert "evidence_completeness" not in script
        assert "passed_count" in script
        assert "possible_count" in script

    def test_the_verdict_and_the_review_state_stay_two_different_facts(self, script):
        """A record is routinely well supported *and* under review.

        ``trust_status`` is a deterministic rubric's verdict;
        ``review.status`` is whether a person has looked. Showing one in
        place of the other would report the corpus as either better or
        worse checked than it is, so the card carries both and neither
        vocabulary leaks into the other's renderer.
        """
        trust_words = dict(re.findall(r"\n    (\w+): \"([^\"]+)\"\n?", script))
        verdicts = {badge.value for badge in EvidenceBadge}
        grades = {word for key, word in trust_words.items() if key in verdicts}
        reviews = set(REVIEW_STATES)
        assert grades, "no verdict wording found"
        assert grades & reviews == set()

        body = self._function(script, "trustNode(trust)")
        assert "trust.trust_status" in body
        assert "reviewBadge" not in body, "the verdict must not be dressed as a review state"

        detail = self._function(script, "detailBody(section, ref, payload)")
        assert "reviewBadge(reviewed)" in detail
        # A record with no review block of its own still shows one: the
        # fragment carries the same field and the head must not go silent.
        assert "trust ? trust.review_status : null" in detail

    def test_an_absent_verdict_reads_as_not_assessed_rather_than_as_a_bad_one(self, script):
        """Silence next to graded cards would read as the lowest grade.

        Two absences, one wording: a list that carries no verdict at
        all, and a record whose verdict did not come back.
        """
        messages = re.findall(r"var TRUST_NOT_(?:ASSESSED|ON_LIST) =\s*(.*?);\n", script, re.DOTALL)
        assert len(messages) == 2
        for message in messages:
            assert "not assessed" in message, message
            for badge in EvidenceBadge:
                assert badge.value.replace("_", " ") not in message, message

        body = self._function(script, "trustNode(trust)")
        assert "if (!trust || !trust.trust_status) {" in body
        assert "TRUST_NOT_ASSESSED" in body

        detail = self._function(script, "detailBody(section, ref, payload)")
        assert "if (!section.trust) {" in detail
        assert "TRUST_NOT_ON_LIST" in detail

    def test_the_named_missing_checks_are_shown_and_ship_collapsed(self, script):
        """"Why only mostly supported?" has a published answer -- show it.

        ``ts_single_point_present`` carrying the outcome ``missing`` is
        the gap a reader can otherwise only guess at. It ships inside a
        closed ``<details>`` because a card that opens into thirty check
        names is the wall of text the expansion exists to avoid.

        The two lists are now selected out of the ordered
        ``evidence.checks`` map rather than read from two sibling arrays,
        so the page must go through ``checksWithOutcome`` for both.
        """
        body = self._function(script, "trustNode(trust)")
        assert 'checksWithOutcome(evidence.checks, "missing")' in body
        assert 'checksWithOutcome(evidence.checks, "passed")' in body
        selector = self._function(script, "checksWithOutcome(checks, outcome)")
        assert "checks[name] === outcome" in selector
        assert 'make("details", "trust-why")' in body
        assert 'make("summary", null, "What the rubric checked")' in body
        assert "open" not in re.sub(r"[^\w]", " ", body).split(), "the detail must ship collapsed"
        assert 'checkList("not present", missing)' in body
        assert 'checkList("present", passed)' in body
        assert "evidence.hard_fail_reason" in body


class TestACheckNameDoesNotRepeatItsHeading:
    """The list says the outcome once, in the heading above it.

    The panel groups the named checks under "present" and "not present"
    and then printed the wire name, so a reader got the outcome twice --
    "present: charge present" -- and under the other heading got what
    reads as a contradiction: "not present: path search evidence
    present". The suffix is redundant *because the heading states the
    outcome*: a fact about this page, not about the wire. The API still
    names the check ``charge_present`` and ``evidence.checks`` is
    unchanged.

    The rule is **trailing-only, and only when nothing else says
    present**, which is the property these tests exist to hold:

    * ``charge_present`` -> "Charge". The word was the heading's job.
    * ``dipole_source_present_if_dipole_present`` -> unchanged. Its
      second presence is a *condition* ("if a dipole is present"), not
      the outcome; "dipole source present if dipole" is broken English
      and a different claim. One wrong entry is worse than twelve
      slightly long ones.
    * ``at_least_one_thermo_representation_present`` -> "At least one
      thermo representation". "present" is spelled inside
      "re**present**ation", so a substring rule keeps a suffix that is
      genuinely redundant. The comparison is over whole tokens.
    * ``multiplicity_valid`` -> "Multiplicity valid". Not a presence
      claim; nothing to remove.
    """

    @staticmethod
    def _conditionals() -> set[str]:
        """Checks that end in ``_present`` *and* carry an earlier one.

        They are the reason the rule is not a global replace, and they are
        read out of the registry rather than listed here so the set cannot
        quietly go stale.
        """
        return {
            name
            for name in trust_check_names()
            if name.endswith("_present") and "present" in name.split("_")[:-1]
        }

    def test_the_registry_still_contains_the_case_that_forbids_a_global_replace(self):
        """If this ever empties, the trailing-only rule stops being load-bearing.

        Without it every later test here would keep passing while proving
        nothing about the distinction it was written for.
        """
        assert "dipole_source_present_if_dipole_present" in self._conditionals()
        assert len(self._conditionals()) == 3

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("charge_present", "Charge"),
            ("path_search_evidence_present", "Path search evidence"),
            ("transition_state_entry_present", "Transition state entry"),
            ("ts_single_point_present", "TS single point"),
            ("ts_graph_or_smiles_present", "TS graph or SMILES"),
            ("source_calculation_lot_present", "Source calculation LOT"),
            ("nasa_coefficients_present", "NASA coefficients"),
            ("irc_evidence_present", "IRC evidence"),
        ],
    )
    def test_a_trailing_present_goes_because_the_heading_already_said_it(self, name, expected):
        assert name in trust_check_names()
        assert trust_check_label(name) == expected

    @pytest.mark.parametrize(
        "name, expected",
        [
            (
                "dipole_source_present_if_dipole_present",
                "Dipole source present if dipole present",
            ),
            (
                "polarizability_source_present_if_polarizability_present",
                "Polarizability source present if polarizability present",
            ),
            ("scan_source_present_if_torsions_present", "Scan source present if torsions present"),
        ],
    )
    def test_a_conditional_keeps_both_of_its_presences(self, name, expected):
        """The test that must fail if anyone widens this to a global replace.

        "A dipole source is present **if a dipole is present**" is one
        claim with a condition attached. Removing either "present"
        changes what the check says it checked.
        """
        assert trust_check_label(name) == expected
        assert trust_check_label(name).endswith("present")
        assert trust_check_label(name).count("present") == 2

    def test_present_spelled_inside_a_word_is_not_a_presence_claim(self):
        """"re-present-ation" must not defend a suffix that is redundant."""
        name = "at_least_one_thermo_representation_present"
        assert name in trust_check_names()
        assert trust_check_label(name) == "At least one thermo representation"

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("multiplicity_valid", "Multiplicity valid"),
            ("ts_status_not_rejected", "TS status not rejected"),
            ("ts_status_recorded", "TS status recorded"),
            ("quality_recorded", "Quality recorded"),
        ],
    )
    def test_a_check_that_makes_no_presence_claim_loses_nothing(self, name, expected):
        assert trust_check_label(name) == expected
        assert len(trust_check_label(name).split(" ")) == len(name.split("_"))

    def test_no_label_drops_or_invents_a_word_anywhere_in_the_vocabulary(self):
        """Driven over every check the rubrics can emit, not over examples.

        A label is the name's own tokens, in the name's own order, with
        at most one word gone -- a trailing "present", and only where no
        other token spells it. Nothing is expanded, translated,
        reordered or abbreviated; the only other difference permitted is
        letter case.

        This is what a global replace fails: it would strip the middle
        "present" out of the three conditionals as well.
        """
        conditionals = self._conditionals()
        assert trust_check_names(), "the registry produced no checks to drive"
        for name in trust_check_names():
            tokens = name.split("_")
            expected = tokens
            if tokens[-1] == "present" and name not in conditionals:
                expected = tokens[:-1]
            label = trust_check_label(name)
            assert [word.lower() for word in label.split(" ")] == expected, name
            assert label[:1] == label[:1].upper(), name

    def test_exactly_the_measured_share_of_the_vocabulary_is_shortened(self):
        """The counts, so a silent widening or narrowing has to be admitted.

        Measured 2026-08-24 over the registry: 100 distinct checks, 66 of
        them ending in the word, 63 shortened and 3 held back as
        conditionals.
        """
        names = trust_check_names()
        shortened = [n for n in names if trust_check_label(n).lower() != n.replace("_", " ")]
        assert len(names) == 100
        assert len([n for n in names if n.endswith("_present")]) == 66
        assert len(shortened) == 63
        assert len(self._conditionals()) == 66 - 63

    def test_the_page_ships_a_label_for_every_check_the_rubrics_can_emit(self, script):
        """A check added tomorrow cannot arrive on the page unlabelled.

        The map is built on the server from the rubric registry, so this
        also proves the page and :func:`trust_check_label` agree -- the
        page cannot drift from the rule the tests above pin.
        """
        match = re.search(r"var CHECK_WORDS = (\{.*?\});\n", script, flags=re.DOTALL)
        assert match is not None, "the page carries no check-name label map"
        shipped = json.loads(match.group(1))
        assert set(shipped) == set(trust_check_names())
        assert shipped == {name: trust_check_label(name) for name in trust_check_names()}

    def test_the_list_prints_the_label_and_keeps_the_raw_name_reachable(self, script):
        """The wire token stays recoverable -- it is what a bug report quotes.

        Not on screen (that is the redundancy this removed) but on the
        item itself, alongside the raw JSON every card already links to.
        """
        body = TestTrustVerdictIsShown._function(script, "checkList(title, names)")
        assert 'make("li", null, checkLabel(names[i]))' in body
        assert 'item.setAttribute("title", names[i]);' in body

        lookup = TestTrustVerdictIsShown._function(script, "checkLabel(name)")
        assert "Object.prototype.hasOwnProperty.call(CHECK_WORDS, name)" in lookup
        assert "return words(name);" in lookup

    def test_the_headings_still_state_the_outcome(self, script):
        """The suffix is redundant *because* these two lines say it.

        Delete either heading and the shortening becomes a loss of
        information rather than a removal of a repeat, so the rule and
        the headings are pinned together.
        """
        body = TestTrustVerdictIsShown._function(script, "trustNode(trust)")
        assert 'checkList("not present", missing)' in body
        assert 'checkList("present", passed)' in body


class TestHeroExampleIsReal:
    """The worked example is the checker's own output, not a mock-up."""

    def test_the_hero_input_still_produces_the_hero_code(self):
        warnings = evaluate_frequency_list_linearity(
            len(HERO_FREQUENCIES),
            HERO_XYZ,
            location="calculation.freq.frequencies",
        )
        assert [w.code for w in warnings] == [HERO_CODE]

    def test_the_hero_sentence_agrees_with_the_checker_on_the_mode_count(self):
        """3N-5 = 4 on the page must be 3N-5 = 4 in the message.

        The page paraphrases a long message down to four sentences. This
        pins the one number in that paraphrase to the number the checker
        actually produced, so a change to either side is caught here
        rather than by a reader.
        """
        warnings = evaluate_frequency_list_linearity(
            len(HERO_FREQUENCIES),
            HERO_XYZ,
            location="calculation.freq.frequencies",
        )
        message = warnings[0].message
        assert "3N-5 = 4 vibrations" in message
        page = render_landing_page(api_reference_path=None)
        assert "3N&minus;5 = 4" in page

    def test_the_page_does_not_call_an_advisory_a_rejection(self):
        """This check accepts the record. Saying otherwise misrepresents it."""
        page = render_landing_page(api_reference_path=None)
        assert "accepted and flagged, not refused" in page
        assert HERO_CODE in page

    def test_the_hero_geometry_is_shown_verbatim_without_the_xyz_header(self):
        """The coordinates are the deposit; the header is file plumbing.

        Every atom row appears exactly as deposited. The count and
        comment lines do not: alone above three coordinates on a web
        page, a bare ``3`` reads as a stray number rather than as an
        XYZ field, and the panel labels the block instead.
        """
        page = render_landing_page(api_reference_path=None)
        atoms = hero_atom_lines()
        assert len(atoms) == 3
        for line in atoms:
            assert line in page
        assert "<pre>" + "\n".join(atoms) + "</pre>" in page
        assert ">3\ncarbon dioxide" not in page

    def test_the_true_spectrum_is_the_one_carbon_dioxide_has(self):
        """3N-5 = 4 modes, with the doubly degenerate bend counted twice.

        Carbon dioxide is linear and has three atoms, so it has four
        vibrations: two stretches and one bend, and the bend occupies
        two of the four because it is doubly degenerate. Getting this
        wrong would put a false spectrum on a public page under a
        heading claiming it is what the molecule has.
        """
        n_atoms = len(hero_atom_lines())
        assert len(HERO_TRUE_FREQUENCIES) == 3 * n_atoms - 5
        assert HERO_TRUE_FREQUENCIES == (667.0, 667.0, 1333.0, 2349.0)
        assert HERO_TRUE_FREQUENCIES.count(667.0) == 2

    def test_the_deposit_is_the_true_spectrum_with_the_degeneracy_collapsed(self):
        """The deposit is derived by the very mistake being illustrated.

        Exactly one mode short, and short by a duplicate rather than by
        an arbitrary omission, because a de-duplicating parser is what
        produces this deposit in the wild.
        """
        assert HERO_FREQUENCIES == (667.0, 1333.0, 2349.0)
        assert len(HERO_FREQUENCIES) == len(HERO_TRUE_FREQUENCIES) - 1
        assert set(HERO_FREQUENCIES) == set(HERO_TRUE_FREQUENCIES)
        assert len(HERO_FREQUENCIES) == len(set(HERO_FREQUENCIES))

    def test_the_panel_shows_both_lists_with_the_missing_mode_marked(self):
        """The gap must be visible, not asserted.

        Three frequencies with nothing unusual about any of them, plus
        a sentence saying one is missing, is legible only to a reader
        who already knows the answer. Both rows are on the page, one
        frequency per column, and the absent slot is a marked cell
        sitting directly above the frequency that belongs in it.
        """
        page = render_landing_page(api_reference_path=None)
        rows = re.findall(r"<tr>(.*?)</tr>", page, flags=re.DOTALL)
        assert len(rows) == 2, "the panel should carry the deposit and the spectrum"
        deposited, spectrum = rows

        assert '<th scope="row">deposited</th>' in deposited
        assert "has</th>" in spectrum

        # The spectrum row carries every true mode, in order.
        assert re.findall(r"<td[^>]*>([\d.]+)</td>", spectrum) == [
            f"{value:.1f}" for value in HERO_TRUE_FREQUENCIES
        ]
        # The deposited row carries every deposited mode, and one gap.
        assert re.findall(r"<td[^>]*>(?:<span[^>]*>)?([\w.]+)", deposited) == [
            "667.0",
            "missing",
            "1333.0",
            "2349.0",
        ]
        assert deposited.count('class="gap"') == 1
        # The marked cell in the spectrum row is under the marked gap,
        # and it is the 667 the deposit dropped.
        marked = re.findall(r'<td class="gap-source">([\d.]+)</td>', spectrum)
        assert marked == ["667.0"]

    def test_the_panel_explains_the_degeneracy_rather_than_compressing_it(self):
        """Three ideas in one clause is what made the panel unreadable.

        "a degenerate bend, reported once by a de-duplicating parser"
        packs the degeneracy, the parser and the consequence into nine
        words. Each gets its own sentence now, and the word that does
        the work -- doubly degenerate -- is on the page.
        """
        page = render_landing_page(api_reference_path=None)
        assert "doubly degenerate" in page
        assert "perpendicular planes" in page
        assert "de-duplicates equal frequencies" in page


class TestDocSurfaceExposure:
    """``EXPOSE_API_DOCS`` x ``EXPOSE_API_REFERENCE``, all three states."""

    def test_docs_on_exposes_swagger_redoc_and_openapi(self, client_factory, monkeypatch):
        monkeypatch.setattr(settings, "expose_api_docs", True)
        monkeypatch.setattr(settings, "expose_api_reference", False)
        with client_factory() as c:
            assert c.get("/docs").status_code == 200
            assert c.get("/redoc").status_code == 200
            assert c.get("/openapi.json").status_code == 200

    def test_reference_on_serves_redoc_and_openapi_but_never_swagger(
        self, client_factory, monkeypatch
    ):
        """The hosted posture. Swagger carries a live request console.

        ReDoc renders the same schema as a static reference with no
        "Try it out" button, so publishing the contract does not also
        publish a request builder aimed at the deployment.
        """
        monkeypatch.setattr(settings, "expose_api_docs", False)
        monkeypatch.setattr(settings, "expose_api_reference", True)
        with client_factory() as c:
            assert c.get("/docs").status_code == 404
            assert c.get("/redoc").status_code == 200
            assert c.get("/openapi.json").status_code == 200

    def test_both_off_registers_none_of_the_three(self, client_factory, monkeypatch):
        monkeypatch.setattr(settings, "expose_api_docs", False)
        monkeypatch.setattr(settings, "expose_api_reference", False)
        with client_factory() as c:
            assert c.get("/docs").status_code == 404
            assert c.get("/redoc").status_code == 404
            assert c.get("/openapi.json").status_code == 404

    def test_the_new_setting_defaults_to_off(self):
        """Upgrading must not make an existing deployment more exposed."""
        assert Settings(deployment_mode="local").expose_api_reference is False

    def test_the_reference_is_allowed_in_hosted_mode(self):
        """It is safe on, which is the entire reason it is a separate flag.

        ``EXPOSE_API_DOCS=true`` is a boot-refusing violation in hosted
        mode. This one must not be, or a hosted deployment could never
        publish its own API reference.
        """
        hosted = Settings(
            deployment_mode="hosted_public",
            auth_allow_open_registration=False,
            expose_api_docs=False,
            expose_api_reference=True,
            legacy_reads_require_auth=True,
            session_cookie_secure=True,
            allow_public_internal_ids=False,
            rate_limit_enabled=True,
            cors_allow_origins=[],
            db_statement_timeout_ms=30_000,
        )
        validate_deployment_safety(hosted)  # does not raise

    def test_landing_page_links_the_reference_only_when_one_is_served(
        self, client_factory, monkeypatch
    ):
        """A landing page that links to a 404 is the bug being fixed."""
        monkeypatch.setattr(settings, "expose_api_docs", False)
        monkeypatch.setattr(settings, "expose_api_reference", False)
        with client_factory() as c:
            assert 'href="/redoc"' not in c.get("/").text

        monkeypatch.setattr(settings, "expose_api_reference", True)
        with client_factory() as c:
            assert 'href="/redoc"' in c.get("/").text


class TestLandingPageChangesNoExistingRoute:
    def test_the_route_table_gains_the_landing_route_and_nothing_else(self, monkeypatch):
        """Adding ``/`` shadowed nothing and reordered nothing.

        Built twice from the same factory -- once as shipped, once with
        the landing router replaced by an empty one -- the two ordered
        route tables must differ by exactly the ``/`` entry, appended
        last. Order matters because Starlette resolves routes in
        registration order, so a route inserted earlier could capture a
        request an existing route used to answer.
        """
        with_landing = [route.path for route in create_app().routes]

        monkeypatch.setattr(app_module, "landing_router", APIRouter())
        without_landing = [route.path for route in create_app().routes]

        assert with_landing == [*without_landing, "/"]

    def test_no_other_route_claims_the_root_path(self):
        roots = [route for route in create_app().routes if route.path == "/"]
        assert len(roots) == 1

    def test_status_and_readyz_are_untouched(self, client_factory):
        with client_factory() as c:
            status = c.get("/api/v1/status")
            readyz = c.get("/api/v1/readyz")
        assert status.status_code == 200
        assert status.headers["content-type"].startswith("application/json")
        assert "status" in status.json()
        assert readyz.status_code in (200, 503)
        assert readyz.headers["content-type"].startswith("application/json")

    def test_the_landing_route_is_absent_from_the_openapi_document(
        self, client_factory, monkeypatch
    ):
        """It is a page, not an operation.

        The generated schema is the machine contract: it feeds the
        golden snapshot and the client's parity ledger. An HTML page
        listed there would be an operation every consumer has to triage.
        """
        monkeypatch.setattr(settings, "expose_api_docs", True)
        with client_factory() as c:
            schema = c.get("/openapi.json").json()
        assert "/" not in schema["paths"]


class TestLevelOfTheoryIsShownWhereTheApiSendsIt:
    """At what level a record was computed, on the surfaces that answer it.

    ``evidence_summary`` said how *much* evidence a record carried and
    nothing said at what level, so the level of theory behind a
    conformer group or a transition state was two requests away behind
    ``include=calculations``. ``levels_of_theory`` closed that on four
    surfaces and this page had zero occurrences of the field.

    Three properties are load-bearing, and each is a way the block can
    go quietly wrong:

    * **Every level, not the first.** The value is a list at every
      length. A record whose optimisation is cheap and whose single
      point is expensive carries two, which is the standard composite
      workflow rather than a fault -- 12 of the 34 transition-state
      entries on the deployment do it -- and a renderer that indexes
      ``[0]`` drops exactly the expensive half a reader came for.
    * **Absence is a fact, and it is the API's fact.** A calculation
      type with no key had no calculation of that type; the missing
      single point behind a transition state is the gap the project
      owner spotted by eye. The rows are driven by the record's own
      coverage block so that an absence can be *shown* -- and only by
      it, so that nothing is invented to show.
    * **No section where there is no field.** Statmech, transport and
      network do not carry ``levels_of_theory``; the deferral is
      deliberate and their sources are keyed by role rather than by
      calculation type. A blank "at what level" on a statmech card
      would report a gap in data that is not missing.

    And one non-property, stated because it would be easy to add: the
    block **reports and never judges**. Nothing here warns about a
    record with two levels, and nothing styles the second one
    differently from the first.
    """

    #: The views that set ``levels``, and the ones that must not. Every
    #: view on the page is in exactly one, so a view added later is not
    #: silently outside both while these assertions keep passing.
    WITH_LEVELS = ("conformerView(record)", "transitionStateView(record)")
    WITHOUT_LEVELS = (
        "thermoView(record)",
        "statmechView(record)",
        "transportView(record)",
        "calculationView(record)",
        "kineticsView(record)",
    )

    #: The four functions that decide and draw the block.
    LEVEL_FUNCTIONS = (
        "coverageOf(evidence)",
        "levelRow(type, found, covered)",
        "levelRows(evidence)",
        "levelsBlock(rows)",
    )

    def test_the_two_surfaces_that_carry_the_field_are_the_two_that_render_it(self, script):
        """Asserted against the schemas, not against a list of names.

        The views that read an ``evidence_summary`` do not all get the
        same block, because the API does not send it to all of them.
        Which ones do is read off the read models themselves, so that a
        surface gaining the field later fails here rather than
        rendering nothing forever.
        """
        assert "levels_of_theory" in ConformerGroupEvidenceSummary.model_fields
        assert "levels_of_theory" in TransitionStateEntryEvidenceSummary.model_fields
        assert "levels_of_theory" not in StatmechEvidenceSummary.model_fields
        assert "levels_of_theory" not in TransportEvidenceSummary.model_fields

        for signature in self.WITH_LEVELS:
            assert "levels: levelRows(evidence)" in _js_function(script, signature), signature
        for signature in self.WITHOUT_LEVELS:
            assert "levels:" not in _js_function(script, signature), signature
        views = set(re.findall(r"function (\w+View\(record\)) \{", script))
        assert views == set(self.WITH_LEVELS) | set(self.WITHOUT_LEVELS), views

    def test_a_surface_that_does_not_carry_the_field_renders_no_empty_section(self, script):
        """No heading, no blank list, no hint that something is missing.

        Two guards, and both are needed. ``levelRows`` answers null the
        moment the field is absent -- before it reads a coverage block,
        which several of those surfaces *do* carry and which would
        otherwise produce a column of "no calculation of this type"
        about a record whose surface was never asked the question. And
        the card renderer requires rows to exist before it writes the
        heading, so a view that sets nothing draws nothing.
        """
        rows = _js_function(script, "levelRows(evidence)")
        assert "var levels = evidence.levels_of_theory;" in rows
        absent = re.search(r"if \(!levels\) \{[^}]*return null;[^}]*\}", rows)
        assert absent is not None, rows
        assert rows.index("if (!levels)") < rows.index("coverageOf(evidence)")

        detail = _js_function(script, "detailBody(section, ref, payload)")
        guard = re.search(
            r"if \(view\.levels && view\.levels\.length\) \{(.*?)\n      \}", detail, re.DOTALL
        )
        assert guard is not None, detail
        assert 'groupLabel("at what level")' in guard.group(1)
        assert "levelsBlock(view.levels)" in guard.group(1)
        # The heading is written in exactly one place, so it cannot
        # appear without the rows that justify it.
        assert script.count('groupLabel("at what level")') == 1

    def test_a_record_with_nothing_at_any_level_gets_no_block_either(self, script):
        """A column of "none" says only what the count above it said."""
        rows = _js_function(script, "levelRows(evidence)")
        assert "return carried ? rows : null;" in rows
        # Set in both loops -- the known calculation types and the
        # unknown ones -- so neither can be the only path that counts.
        assert len(re.findall(r"if \(row\.displays\.length\) \{ carried = true; \}", rows)) == 2

    def test_every_level_in_a_list_is_rendered_and_never_only_the_first(self, script):
        """The second entry is the expensive single point.

        Two loops have to hold for that to be true -- one collecting
        the levels off the payload, one drawing them -- and neither may
        be replaced by a subscript. The subscript ban is checked over
        every function in the block rather than over the two that could
        be wrong today, because ``[0]`` is the shape of this mistake
        wherever it appears.
        """
        row = _js_function(script, "levelRow(type, found, covered)")
        collect = re.search(
            r"for \(var i = 0; i < found\.length; i \+= 1\) \{(.*?)\n    \}", row, re.DOTALL
        )
        assert collect is not None, row
        assert "row.displays.push(found[i].display);" in collect.group(1)

        block = _js_function(script, "levelsBlock(rows)")
        draw = re.search(
            r"for \(var j = 0; j < displays\.length; j \+= 1\) \{(.*?)\n        \}",
            block,
            re.DOTALL,
        )
        assert draw is not None, block
        assert 'make("span", "level", displays[j])' in draw.group(1)

        for signature in self.LEVEL_FUNCTIONS:
            body = _js_function(script, signature)
            assert "[0]" not in body, signature

    def test_the_display_string_is_used_as_the_api_computed_it(self, script):
        """``method/basis`` is one string on the wire and one on screen.

        Reassembling it here would give the same level two spellings,
        and the second one drifts the first time a level carries a
        dispersion correction or a solvent.
        """
        code = _js_code(_js_function(script, "levelRow(type, found, covered)"))
        assert "found[i].display" in code
        for part in ("method", "basis", "dispersion", "solvent"):
            assert part not in code, part

    def test_a_calculation_type_the_record_lacks_is_shown_rather_than_omitted(self, script):
        """The gap is the finding; a renderer drawing only what it is sent hides it.

        Three states, three renderings, and the two absences do not
        share a sentence: a type with no calculation at all is a
        different fact from a type whose calculation names no level,
        and the API distinguishes them by key-absent versus
        empty-list.
        """
        assert 'var LEVEL_NONE = "no calculation of this type";' in script
        assert 'var LEVEL_UNRECORDED = "no level of theory recorded";' in script
        row = _js_function(script, "levelRow(type, found, covered)")
        assert "row.note = covered ? LEVEL_UNRECORDED : LEVEL_NONE;" in row
        rows = _js_function(script, "levelRows(evidence)")
        # A type nobody said anything about gets no row at all: the
        # coverage block is the only licence to draw an absence.
        assert (
            "if (!found && !Object.prototype.hasOwnProperty.call(coverage, type)) { continue; }"
            in rows
        )
        block = _js_function(script, "levelsBlock(rows)")
        assert 'cell.className = "absent";' in block
        assert "rows[i].note" in block

    def test_both_coverage_shapes_are_read_and_neither_is_assumed(self, script):
        """A TS entry answers in booleans, a conformer group in counts.

        The asymmetry is deliberate on both sides -- a boolean is
        unambiguous for one provenance row and misleading pooled over
        six -- so the page reads both rather than picking one and
        quietly rendering nothing on the other surface.
        """
        assert "evidence_coverage" in ConformerGroupEvidenceSummary.model_fields
        assert {"has_opt", "has_sp"} <= set(TransitionStateEntryEvidenceSummary.model_fields)
        coverage = _js_function(script, "coverageOf(evidence)")
        assert "var coverage = evidence.evidence_coverage;" in coverage
        assert 'name.indexOf("has_") === 0' in coverage
        assert "seen[name.slice(4)] = !!evidence[name];" in coverage

    def test_only_a_calculation_type_can_get_a_level_row(self, script):
        """``geometry_validation`` is an evidence facet, not a job.

        It sits in the conformer coverage block beside ``opt`` and
        ``freq`` and can never appear in ``levels_of_theory``, so a row
        saying it has no level would report an absence that was never a
        possibility. The row set is therefore driven by the calculation
        types the page has words for -- checked here against the enum
        -- and not by whatever keys the coverage block happens to
        carry.
        """
        facets = set(ConformerEvidenceCoverage.model_fields) - {
            member.value for member in CalculationType
        }
        assert facets == {"geometry_validation", "scf_stability"}, facets
        rows = _js_function(script, "levelRows(evidence)")
        assert "for (type in CALCULATION_WORDS) {" in rows
        assert "for (type in coverage)" not in rows
        # A type the enum has grown and this build has no word for still
        # gets its levels printed, after the ones it does.
        unknown = re.search(r"for \(type in levels\) \{(.*?)\n    \}", rows, re.DOTALL)
        assert unknown is not None, rows
        assert "!Object.prototype.hasOwnProperty.call(CALCULATION_WORDS, type)" in unknown.group(1)

    def test_two_levels_on_one_record_are_reported_and_never_judged(self, script, page):
        """12 of 34 deployed transition-state entries carry two.

        Marking them would mark a third of correct records as suspect.
        So there is no warning, no flag, and no second style: every
        entry is drawn in the same element, with the same class, as the
        first, and the block's own rule carries no colour at all.
        """
        block = _js_function(script, "levelsBlock(rows)")
        # One element type, one class name, for every entry in the list.
        assert set(re.findall(r'make\("span", "([^"]+)"', block)) == {"level"}
        for word in ("inconsistent", "mismatch", "conflict", "suspect"):
            assert word not in script.lower(), word
        css = _stylesheet(page)
        rule = re.search(r"\.levels \.level \{([^}]*)\}", css)
        assert rule is not None
        assert "color" not in rule.group(1)


class TestReactionFamilyReadsAsAName:
    """``H_Abstraction`` is a machine identifier. "Hydrogen Abstraction" is a name.

    The page printed the stored identifier, which reads to a chemist as
    something the renderer failed to render. The readable name is
    derived on the server by
    :mod:`app.chemistry.reaction_family_display`, so the browser makes
    no chemistry decision at all -- it transcribes a map, the same
    arrangement the trust check labels use.

    The interesting half is the refusal. Six families carry a token
    nobody could decode (``COm``, ``CSm``, ``2F``, ``ExoTetCyclic``) and
    are returned verbatim rather than half-translated, because
    "Surface Carbonate 2F Decomposition" looks like a human name while
    silently carrying an untranslated chemistry token. Those must be
    *drawn* as identifiers too, or a raw string sitting in a list of
    readable names reads as a bug.
    """

    @staticmethod
    def _family_words(script: str) -> dict[str, str]:
        block = re.search(r"var FAMILY_WORDS = (\{.*?\});", script, flags=re.DOTALL)
        assert block is not None, "the page carries no family name map"
        return json.loads(block.group(1))

    def test_the_map_is_the_servers_derivation_and_not_a_copy_of_it(self, script):
        """Every entry, checked against the function that produces it.

        A hand-maintained copy drifts the moment a token expansion
        lands. This is derived at render time from the canonical set,
        by the same function ``/meta/reaction-families`` answers with.
        """
        words = self._family_words(script)
        assert words == reaction_family_display_names()
        for name, display in words.items():
            assert display == reaction_family_display_name(name), name
        assert words["H_Abstraction"] == "Hydrogen Abstraction"
        assert words["1,3_sigmatropic_rearrangement"] == "1,3 sigmatropic rearrangement"

    def test_the_deliberately_unresolved_families_are_absent_from_it(self, script):
        """Absence is the instruction, and it is the refusal's own list.

        Six of the 125 are refused by
        :func:`is_unresolved_reaction_family`. None of them may appear
        here mapping to itself: a family with no entry and a family
        refused a translation are the same fact to a reader, and giving
        the refusals a key would leave an unknown family as the only
        case with no branch -- which is the case most likely to arrive.
        """
        words = self._family_words(script)
        refused = {
            name for name in CANONICAL_REACTION_FAMILIES if is_unresolved_reaction_family(name)
        }
        assert len(refused) == 6, sorted(refused)
        assert "Surface_Carbonate_2F_Decomposition" in refused
        assert refused & set(words) == set()
        assert set(words) == set(CANONICAL_REACTION_FAMILIES) - refused

    def test_a_named_family_is_prose_and_an_unnamed_one_is_an_identifier(self, script, page):
        """The two must not look alike, which is the whole point.

        A name goes in the body face. Anything else keeps
        ``code.ident-value`` -- the monospace every public ref on this
        page is drawn in -- so a reader can see at a glance which of
        the two they are looking at.
        """
        body = _js_function(script, "familyNode(name)")
        assert 'make("code", "ident-value", identifier)' in body
        assert 'make("span", "family-name", FAMILY_WORDS[identifier])' in body
        assert "Object.prototype.hasOwnProperty.call(FAMILY_WORDS, identifier)" in body
        css = _stylesheet(page)
        assert _css_declaration(css, ".family-name", "font-family") == "var(--sans)"
        assert _css_declaration(css, ".ident-value,\n.formula", "font-family") == "var(--mono)"

    def test_the_stored_identifier_stays_recoverable(self, script):
        """It is the token a client hands back as ``?family=``.

        Kept in ``title`` on the named branch, and printed outright on
        the other, so no rendering of a family loses the string a
        reader needs to quote or to query with.
        """
        body = _js_function(script, "familyNode(name)")
        assert 'named.setAttribute("title", identifier);' in body

    def test_the_reaction_record_renders_the_family_through_it(self, script):
        """And no longer hands the raw string to the identifier list."""
        renderer = _js_function(script, "rxnRecordNode(record)")
        assert '["Reaction family", familyNode(record.family), false]' in renderer
        assert '["Reaction family", record.family' not in renderer

    def test_a_record_with_no_family_still_says_so(self, script):
        """Null is a rendering here as it is everywhere else on this page."""
        body = _js_function(script, "familyNode(name)")
        assert 'if (name === null || name === undefined || name === "") { return null; }' in body
        idents = _js_function(script, "identList(rows)")
        assert 'cell.className = "absent";' in idents


class TestTheVocabularyIsLinkedWhereTheTokensAre:
    """438 generated tokens, and until now nothing pointed at them.

    ``docs/guides/api_vocabulary.md`` defines every status, prefix,
    check name and refusal code the API answers with, generated from
    the same declarations. The confusions it answers are *in situ* --
    ``matched_direction: reverse`` inside a result, ``not_applicable``
    inside a trust panel -- so the links are at those two places and in
    the search panel, and nowhere else. Not one link per token.
    """

    def test_the_published_page_is_addressed_by_its_nav_path(self):
        """Built from the documentation root, not written out whole.

        A deployment pointing at its own documentation build gets the
        glossary from that build, and no version of this page can link
        to a vocabulary belonging to a different version of the API.
        """
        assert VOCABULARY_URL == DOCS_URL + VOCABULARY_PATH
        assert VOCABULARY_URL.startswith(DOCS_URL)
        assert VOCABULARY_PATH == "guides/api_vocabulary/"
        assert VOCABULARY_TRUST_FRAGMENT.startswith("#")
        assert VOCABULARY_DIRECTION_FRAGMENT.startswith("#")

    def test_the_page_carries_the_link_in_static_markup(self, document, page):
        """So the scripting-off reader gets it too.

        It is also the only copy of the URL on the page: the script
        reads this anchor's ``href`` rather than carrying one of its
        own, which is what keeps the inline script free of absolute
        URLs.
        """
        found = document.find("a", id="vocabulary-link")
        assert len(found) == 1, found
        attrs, ancestors = found[0]
        assert attrs["href"] == VOCABULARY_URL
        assert "script" not in ancestors
        assert page.count(VOCABULARY_URL) == 1

    def test_the_inline_script_reads_the_href_rather_than_holding_a_url(self, script):
        """An absolute URL in the script would break the self-containment rule.

        :class:`TestLandingPageIsSelfContained` asserts no string
        literal in the script contains ``://``. A link is a
        destination, not a subresource, so the anchor is fine -- but
        the script may not spell it, and does not need to.
        """
        assert 'doc.getElementById("vocabulary-link")' in script
        assert f'var VOCABULARY_TRUST = "{VOCABULARY_TRUST_FRAGMENT}";' in script
        assert f'var VOCABULARY_DIRECTION = "{VOCABULARY_DIRECTION_FRAGMENT}";' in script
        for literal in re.findall(r"\"([^\"]*)\"", script):
            assert "://" not in literal, literal

    def test_the_two_in_situ_links_are_at_the_two_confusions(self, script):
        """The trust disclosure, and the direction chip. Exactly those.

        A link per token would be forty links on a card. These two are
        where a reader is already stuck on a word: ``not_applicable``
        beside a check name, and "matched in reverse" beside an
        equation whose reactants are the ones they asked for.
        """
        assert script.count("addVocabLink(") == 3  # one definition, two calls
        trust = _js_function(script, "trustNode(trust)")
        assert "addVocabLink(explains, VOCABULARY_TRUST," in trust
        reaction = _js_function(script, "rxnRecordNode(record)")
        assert "addVocabLink(matched, VOCABULARY_DIRECTION," in reaction
        assert "DIRECTION_WORDS[record.matched_direction]" in reaction

    def test_a_missing_anchor_costs_the_link_and_not_the_card(self, script):
        """The renderer must not throw over a hyperlink."""
        helper = _js_function(script, "addVocabLink(node, fragment, text)")
        assert "if (!VOCABULARY) { return; }" in helper
        assert 'anchor(VOCABULARY + fragment, "vocab-link", text)' in helper

    def test_no_route_but_the_landing_page_learned_about_the_glossary(self, client_factory):
        """It is a link on a page: no endpoint serves or proxies it."""
        with client_factory() as c:
            assert VOCABULARY_URL in c.get("/").text
            assert c.get("/guides/api_vocabulary/").status_code == 404


class TestACalculationCardStatesOnlyWhatTheRecordStates:
    """Four ways the calculations panel used to say something untrue.

    All four were found on one live entry -- ``[CH3]``, fourteen
    calculations, one conformer group -- and none of them was a wrong
    number. Each was a true number given a frame that made it read as
    something else, which is the failure mode a rendered page has and
    a JSON document does not.

    * **A frequencies card announced "Electronic energy -- not
      recorded".** A freq job has no electronic energy to report. The
      read API says so by carrying no energy block, and the card
      collapsed that into an energy block with the reading missing.
    * **No card said which conformer it belonged to**, although every
      record carries one or carries an explicit nothing.
    * **The entry advertised fourteen calculations and the conformer
      group card said eleven.** Both counts are right and they count
      different sets; nothing on the page said which set either was.
    * **The cap was confessed underneath the cards it had already
      applied.**
    """

    def test_a_calculation_with_no_energy_block_gets_no_energy_row(self, script):
        """Absence is about the shape of the answer; null is about the data.

        The read API draws that line deliberately and the card erased
        it: ``record.energy || {}`` turns "this job reports no
        electronic energy" into "this job's electronic energy is
        missing", and the second is an accusation the payload never
        made. The guard is on the block, never on a table of which
        calculation types are supposed to have one -- the payload
        already knows, and a lookup table here would be this page
        inventing an expectation it is not entitled to hold.
        """
        # Read off the schema rather than off the one payload this was
        # found on: the block is optional on the record and the reading
        # is optional inside it, which is exactly the pair the card has
        # to keep apart. A schema that stopped drawing that line would
        # fail here rather than quietly making the guard meaningless.
        record = SpeciesCalculationsSearchRecord.model_fields["energy"]
        assert not record.is_required()
        assert str(record.annotation).endswith("CalculationEnergyBlock | None")
        assert not CalculationEnergyBlock.model_fields["energy_hartree"].is_required()

        body = _js_function(script, "calculationView(record)")
        code = _js_code(body)
        assert "var energy = record.energy;" in code
        assert "record.energy || {}" not in code
        headline = re.search(r"headline: energy\s*\?(.*?)\n      facts:", code, re.DOTALL)
        assert headline is not None, code
        assert "Electronic energy" in headline.group(1)
        assert ": null" in headline.group(1)
        # No calculation type decides this. The payload does.
        after_energy = code.split("var energy")[1]
        for token in ('"freq"', '"opt"', '"sp"'):
            assert token not in after_energy, token

    def test_an_energy_block_that_is_present_and_empty_still_says_not_recorded(self, script):
        """That one is a real gap, and a real gap keeps the sentinel.

        The fix must not become "hide the row whenever the number is
        missing", which would trade one silent misreading for another:
        a job that should have reported an energy and did not is
        exactly the case a reader needs to see.
        """
        body = _js_code(_js_function(script, "calculationView(record)"))
        # The row is emitted on the strength of the block alone, and
        # the reading inside it goes through the same formatter every
        # other quantity uses -- which answers a null with ABSENT.
        assert 'fixed(energy.energy_hartree, 6, "hartree")' in body
        assert "energy.energy_hartree ?" not in body
        assert "energy.energy_hartree !=" not in body
        assert "energy.energy_hartree ===" not in body
        filler = _js_function(script, "fillValue(node, value)")
        assert "value === null || value === undefined" in filler
        assert "ABSENT" in filler

    def test_the_card_renderer_writes_no_heading_without_rows_to_put_under_it(self, script):
        """The same guard the level-of-theory block already had.

        A view that sets no ``headline`` and a record whose surface
        carries none both arrive as a falsy value and both draw
        nothing. This is asserted beside the ``levels`` guard on
        purpose: they are one rule, and a later edit that keeps one and
        drops the other should fail here.
        """
        detail = _js_function(script, "detailBody(section, ref, payload)")
        assert "if (view.headline && view.headline.length) {" in detail
        assert "if (view.levels && view.levels.length) {" in detail
        assert detail.count("quantityBlock(view.headline)") == 1

    def test_a_calculation_card_names_the_conformer_it_belongs_to(self, script):
        """The record has carried it all along.

        It goes in the facts rather than under "how it was produced".
        Which basin a geometry sits in is what the calculation is
        about, not a fact about the machinery that produced it, and the
        provenance group is for the second kind.
        """
        # The two field names the card reads are the schema's, checked
        # against it so a rename breaks the test rather than the page.
        assert not SpeciesCalculationsSearchRecord.model_fields["conformer"].is_required()
        for field in ("conformer_group_label", "conformer_group_ref"):
            assert field in ConformerContextBlock.model_fields, field

        body = _js_code(_js_function(script, "calculationView(record)"))
        assert '["Conformer", conformerText(record.conformer)]' in body
        facts = body.split("facts:")[1].split("provenance:")[0]
        assert "Conformer" in facts
        assert "Conformer" not in body.split("provenance:")[1]

        helper = _js_code(_js_function(script, "conformerText(conformer)"))
        assert "conformer.conformer_group_label" in helper
        # A group the payload does not name falls back to its ref
        # before it falls back to the sentinel.
        assert helper.index("conformer_group_label") < helper.index("conformer_group_ref")

    def test_a_calculation_with_no_conformer_says_that_and_not_not_recorded(self, script):
        """Three of the fourteen have none, and that is the whole story.

        "not recorded" would read as a gap in the deposit. The
        calculation was deposited without a conformer, which is a
        different fact and the one that explains why a group counts
        fewer calculations than its entry does -- so the page has to
        be able to say *which* nothing it is looking at.
        """
        helper = _js_code(_js_function(script, "conformerText(conformer)"))
        sentence = re.search(r"if \(!conformer\) \{ return \{ sentence: \"([^\"]+)\" \}; \}", helper)
        assert sentence is not None, helper
        assert sentence.group(1) != "not recorded"
        assert "conformer" in sentence.group(1)
        assert sentence.group(1) == sentence.group(1).lower()

        # Rendered as a sentence about the record, in the same body
        # face as the sentinel, so it cannot be read as the name of a
        # conformer group that happens to be called that.
        filler = _js_function(script, "fillValue(node, value)")
        stated = re.search(
            r"if \(typeof value === \"object\" && value\.sentence\) \{(.*?)\n    \}",
            filler,
            re.DOTALL,
        )
        assert stated is not None, filler
        assert "absent" in stated.group(1)
        assert "value.sentence" in stated.group(1)
        # Before the quantity branch, or a ``{sentence}`` would fall
        # into it and print "undefined".
        assert filler.index("value.sentence") < filler.index('make("span", "unit", value.unit)')

    def test_the_conformer_note_says_which_calculations_the_count_counts(self, script):
        """Fourteen and eleven are both right, and they count different sets.

        The entry counts every calculation deposited against it; the
        group counts the ones assigned to that basin. Read side by side
        with nothing between them, the pair reads as arithmetic that
        does not work, and a reader who concludes the database cannot
        add up has been misled by the page rather than by the data.
        """
        conformers = re.search(
            r'\{\s*key: "conformers",(.*?)\n    \}', script, flags=re.DOTALL
        )
        assert conformers is not None
        note = re.search(r"note: (.*?),\n", conformers.group(1), flags=re.DOTALL)
        assert note is not None, conformers.group(1)
        prose = " ".join(re.findall(r'"([^"]*)"', note.group(1)))
        assert "Calculations behind it" in prose
        assert "not every calculation on the entry" in prose
        assert "without a conformer" in prose
        # The field label the note explains still exists to be explained.
        assert '["Calculations behind it", evidence.calculation_count]' in script

    def test_the_page_never_subtracts_the_two_counts(self, script):
        """Fourteen minus eleven is three, and three is not a finding.

        Whether those three ought to carry a conformer is a question
        about the deposit, not about the rendering, and a page that
        prints the remainder has answered it. Each number says what it
        counts; the reader draws their own conclusion or goes to the
        raw list.
        """
        detail = _js_code(_js_function(script, "detailBody(section, ref, payload)"))
        conformer_view = _js_code(_js_function(script, "conformerView(record)"))
        calculation_view = _js_code(_js_function(script, "calculationView(record)"))
        for body in (detail, conformer_view, calculation_view):
            assert not re.search(r"calculation_count\s*-", body), body
            assert not re.search(r"-\s*evidence\.calculation_count", body), body
            assert "unassigned" not in body

    def test_the_cap_is_stated_before_the_cards_and_not_only_after(self, script):
        """A promise and its correction have to arrive in that order.

        The count line says fourteen, five cards follow, and a note
        underneath them used to be the first mention that five was all
        there would ever be -- by which point the reader has spent the
        scroll looking for the other nine.
        """
        code = _js_code(_js_function(script, "detailBody(section, ref, payload)"))
        for fragment in ('"detail-count"', '"Showing the first "', "for (var i = 0; i < shown"):
            assert fragment in code, fragment
        promise = code.index('"detail-count"')
        confession = code.index('"Showing the first "')
        cards = code.index("for (var i = 0; i < shown; i += 1)")
        assert promise < confession < cards, (promise, confession, cards)
        # ``shown`` has to be known before it can be stated.
        assert code.index("var shown = Math.min(records.length, CARD_LIMIT);") < confession

    def test_the_total_above_the_cards_is_still_the_endpoints_own(self, script):
        """The cap shortens the reading, never the answer.

        Stating the cap earlier must not turn the headline count into
        the number of cards drawn, which is the obvious wrong way to
        make the two agree.
        """
        code = _js_code(_js_function(script, "detailBody(section, ref, payload)"))
        assert 'make("p", "detail-count", plural(total, section.one, section.many))' in code
        assert "var total = pagination.total;" in code
        # And the foot of the list no longer repeats the same sentence.
        assert code.count('"Showing the first "') == 1
        assert '"The remaining " + section.many' in code
