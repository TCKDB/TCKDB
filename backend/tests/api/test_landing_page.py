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
    hero_atom_lines,
    reaction_query,
    render_landing_page,
)
from app.api.startup_checks import validate_deployment_safety
from app.services.frequency_geometry_linearity import (
    evaluate_frequency_list_linearity,
)

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
        """
        renderer = re.search(r"function rxnRecordNode\(record\) \{.*?\n  \}", script, re.DOTALL)
        assert renderer is not None
        source = renderer.group(0)
        assert 'make("span", "equation", record.equation' in source
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
