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
    """No CDN, no external stylesheet, no web font, no script."""

    def test_no_element_fetches_a_subresource(self, client_factory):
        with client_factory() as c:
            body = c.get("/").text
        assert re.search(r"<(link|script|img|iframe|source|object|embed)\b", body) is None

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
