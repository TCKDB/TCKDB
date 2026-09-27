"""No ESS parser writes a banner where a version belongs (issue #305, item 2).

The composite ``"Gaussian 16, Revision C.02"`` release on the deployed
archive was declared by the producer; it was never written by TCKDB's own
banner extraction. These tests pin that for all three parsers, on real
output logs: each ``parse_software_version`` returns a bare version token
and nothing else, and the Gaussian build string decomposes into
``version="16"``, ``revision="C.02"`` through the DR-0008 reconciliation
mapper -- the same fields a normalised declaration lands on.

Also the evidence side of item 3: these are the lines the stored
``output_log`` artifacts carry, which is what
``app.services.software_release_version_fill`` reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tckdb_schemas.fragments.refs import SoftwareReleaseRef

from app.services import (
    gaussian_parameter_parser,
    molpro_parameter_parser,
    orca_parameter_parser,
)
from app.services.software_reconciliation import parsed_dict_to_ref

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.mark.parametrize(
    ("parser", "fixture", "expected"),
    [
        (
            gaussian_parameter_parser,
            "gaussian/sp_ub3lyp_g16.log",
            {
                "name": "gaussian",
                "version": "16",
                "build": "ES64L-G16RevC.02",
                "release_date_raw": "7-Dec-2021",
            },
        ),
        (
            orca_parameter_parser,
            "orca/opt_orca.out",
            # The banner reads "Program Version 6.1.0-f.0  -   RELEASE   -";
            # the "-f.0" flavour suffix is not part of the version token.
            {"name": "orca", "version": "6.1.0", "build": None, "release_date_raw": None},
        ),
        (
            molpro_parameter_parser,
            "molpro/ch4_closed_shell/input.out",
            {"name": "molpro", "version": "2026.1", "build": None, "release_date_raw": None},
        ),
    ],
)
def test_parser_emits_a_bare_version_not_a_banner(parser, fixture, expected):
    parsed = parser.parse_software_version((FIXTURES / fixture).read_text())

    assert parsed == expected
    # A version the parser emits must pass the composite-banner normaliser
    # untouched and silent -- if it ever carried the program name or a
    # "Revision" label, this is where it would show.
    ref = SoftwareReleaseRef(name=expected["name"], version=parsed["version"])
    assert ref.version == expected["version"]
    assert ref.version_warning() is None


def test_gaussian_16_c02_build_decomposes_to_version_and_revision():
    parsed = gaussian_parameter_parser.parse_software_version(
        (FIXTURES / "gaussian/sp_ub3lyp_g16.log").read_text()
    )

    ref = parsed_dict_to_ref(parsed)

    assert (ref.name, ref.version, ref.revision, ref.build) == (
        "gaussian",
        "16",
        "C.02",
        "ES64L-G16RevC.02",
    )
