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
* :class:`TestHeroExampleIsReal` -- the worked example in the hero is
  re-run through the real checker on every test run. If the page and
  ``app.services.frequency_geometry_linearity`` ever disagree, this
  fails rather than leaving a plausible-looking fiction on a public
  page.
* :class:`TestDocSurfaceExposure` -- the three-way matrix of
  ``EXPOSE_API_DOCS`` x ``EXPOSE_API_REFERENCE``, including the one
  that matters for the hosted deployment: ReDoc on, Swagger still 404.
* :class:`TestLandingPageChangesNoExistingRoute` -- the invariant that
  adding ``/`` shadowed and reordered nothing, proved by building the
  same application with the landing router removed and diffing the
  ordered route table.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import quote

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
    HERO_XYZ,
    REPO_URL,
    SEARCH_EXAMPLES,
    SEARCH_FIELDS,
    SEED_FIELD,
    SEED_VALUE,
    SPECIES_SEARCH_PATH,
    render_landing_page,
)
from app.api.startup_checks import validate_deployment_safety
from app.services.frequency_geometry_linearity import (
    evaluate_frequency_list_linearity,
)

#: The four write-behaviour roles the page must name. These are the
#: repository's central organising idea, not decoration: every table is
#: exactly one of them, and the role fixes how the table may be written.
DATA_ROLES = ("identity", "provenance", "result", "curation")

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

    def test_page_names_every_data_role_with_its_write_behaviour(self, client_factory):
        with client_factory() as c:
            body = c.get("/").text
        for role in DATA_ROLES:
            assert f"<dt>{role} " in body, f"landing page does not name the {role} role"
        # The behaviour word is the load-bearing half of the label: a page
        # that lists four nouns and omits how each is written has not said
        # the thing the roles exist to say.
        assert body.count('<span class="behaviour">append-only</span>') == 2
        assert '<span class="behaviour">deduped</span>' in body
        assert '<span class="behaviour">overlay</span>' in body

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
        prose = re.sub(r"<style>.*?</style>", "", body, flags=re.DOTALL)
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
        style = re.search(r"<style>(.*?)</style>", body, flags=re.DOTALL)
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
        forms = [
            (attrs, ancestors)
            for attrs, ancestors in document.find("form")
            if "noscript" not in ancestors
        ]
        assert len(forms) == 1
        attrs, _ = forms[0]
        assert attrs["action"] == SPECIES_SEARCH_PATH
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

    def test_a_result_links_onward_to_its_own_records(self, script):
        """A landing page that leads nowhere is the defect being fixed.

        Every product the search says a record has becomes a link to
        the endpoint that serves it, so a visitor can follow a result
        into the data rather than reading about it.
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
        fallback_forms = [
            attrs for attrs, ancestors in document.find("form") if "noscript" in ancestors
        ]
        assert fallback_forms
        for attrs in fallback_forms:
            assert attrs["action"] == SPECIES_SEARCH_PATH
            assert attrs["method"] == "get"

        named = {
            attrs["name"]
            for attrs, ancestors in document.find("input")
            if "noscript" in ancestors and attrs.get("name")
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

    def test_the_hero_deposit_is_shown_verbatim(self):
        page = render_landing_page(api_reference_path=None)
        for line in HERO_XYZ.splitlines():
            assert line in page


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
