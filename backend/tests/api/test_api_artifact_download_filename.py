"""The ``Content-Disposition`` an artifact download sends.

Serving only ``filename*=`` was a real defect, not a curl quirk: curl's
``-J`` reads only the plain ``filename=`` parameter and ignores the RFC
5987 extended form, so with no plain form it fell back to the URL's last
path segment and saved every artifact as a file called ``download``.
wget's ``--content-disposition`` does understand ``filename*``, so the
two clients disagreed -- which is what made it look like curl's fault.

Measured on the hosted instance 2026-09-14:
``curl --fail -OJ .../artifacts/9a21f943.../download`` fetched the right
459 bytes (digest verified) and wrote them to ``./download``.

These test the pure helper rather than the route. The route needs a
stored object in the artifact store to reach the header at all, and the
header's correctness does not depend on any of that -- the naming rule
is the whole claim, and it is checkable on its own.
"""

from __future__ import annotations

import pytest

from app.api.routes.scientific.artifacts import content_disposition_for


class TestBothFormsAreSent:
    def test_the_plain_form_is_present(self):
        # THE regression. curl's -J reads only this parameter; without it
        # every artifact saved as a file called "download".
        assert 'filename="input.gjf"' in content_disposition_for("input.gjf")

    def test_the_extended_form_is_present(self):
        assert "filename*=UTF-8''input.gjf" in content_disposition_for("input.gjf")

    def test_a_non_ascii_name_keeps_its_exact_form_in_filename_star(self):
        # The whole point of `filename*`: the fallback loses fidelity, the
        # extended form does not.
        header = content_disposition_for("rapport_résumé.log")
        assert "filename*=UTF-8''rapport_r%C3%A9sum%C3%A9.log" in header
        assert 'filename="rapport_r_sum_.log"' in header


class TestTheAsciiFallbackCannotBreakTheHeader:
    """A filename is stored data and a header value is a line in the
    response. Replacement, not escaping: a structurally impossible
    injection beats a correctly escaped one."""

    @pytest.mark.parametrize(
        "hostile",
        [
            'evil".gjf',                       # ends the quoted parameter
            "line\r\nX-Injected: yes",         # splits the header
            "line\nX-Injected: yes",           # bare LF
            "semi;colon.gjf",                  # parameter separator
            "back\\slash.gjf",
            "null\x00byte.gjf",
        ],
    )
    def test_no_structural_character_survives(self, hostile):
        header = content_disposition_for(hostile)
        # Exactly three quotes: the pair around the plain filename, and
        # nothing else. Anything more means a value escaped its parameter.
        assert header.count('"') == 2
        for forbidden in ["\r", "\n", "\x00"]:
            assert forbidden not in header
        # Two semicolons: disposition; filename; filename*. A third means
        # a value introduced a parameter of its own.
        assert header.count(";") == 2
        assert header.isascii()

    def test_a_name_that_is_entirely_unsafe_still_yields_something(self):
        # `filename=""` is worse than a name that says nothing useful.
        assert 'filename="download"' in content_disposition_for("///")


def test_the_header_is_the_one_the_route_sends():
    """No reassembly here.

    An earlier version of this file built the header itself from the
    helper's two return values and asserted against that -- so it tested
    its own string, and a mutation removing the `filename=` half from the
    ROUTE passed all eleven tests. The helper now returns the whole
    header and the route uses it verbatim, which is what makes these
    assertions bite.
    """
    header = content_disposition_for("input.gjf")
    assert header == (
        'attachment; filename="input.gjf"; filename*=UTF-8\'\'input.gjf'
    )
