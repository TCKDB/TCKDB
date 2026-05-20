"""Shared fixtures: parsed CCCBDB records for the three Phase 1 fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers.cccbdb.models import CCCBDBExperimentalSpeciesRecord
from app.importers.cccbdb.parsers import parse_experimental_species_page

FIXTURES_DIR = (
    Path(__file__).resolve().parents[3]
    / "app"
    / "importers"
    / "cccbdb"
    / "fixtures"
)

H2_URL = "https://cccbdb.nist.gov/exp1x.asp?casno=1333740"
H2O_URL = "https://cccbdb.nist.gov/exp1x.asp?casno=7732185"
BENZENE_URL = "https://cccbdb.nist.gov/exp1x.asp?casno=71432"


def _load_record(name: str, url: str) -> CCCBDBExperimentalSpeciesRecord:
    html = (FIXTURES_DIR / name).read_text(encoding="utf-8")
    return parse_experimental_species_page(html, source_url=url)


@pytest.fixture(scope="module")
def h2_record() -> CCCBDBExperimentalSpeciesRecord:
    return _load_record("experimental_h2.html", H2_URL)


@pytest.fixture(scope="module")
def h2o_record() -> CCCBDBExperimentalSpeciesRecord:
    return _load_record("experimental_h2o.html", H2O_URL)


@pytest.fixture(scope="module")
def benzene_record() -> CCCBDBExperimentalSpeciesRecord:
    return _load_record("experimental_benzene.html", BENZENE_URL)
