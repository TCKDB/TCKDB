"""Shared fixtures: paths + parsed mechanism for the CHEMKIN adapter tests."""

from pathlib import Path

import pytest

from tckdb_chemkin.identity import (
    IdentityResolver,
    parse_species_dictionary,
    parse_species_map_csv,
)
from tckdb_chemkin.parser import parse_mechanism
from tckdb_chemkin.transport import parse_transport_file

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def mini_mechanism():
    mech = parse_mechanism(read("mini.inp"), thermo_text=read("therm.dat"))
    mech.transport = parse_transport_file(read("tran.dat"))
    return mech


@pytest.fixture
def resolver():
    r = IdentityResolver()
    r.rmg_dict = parse_species_dictionary(read("species_dictionary.txt"))
    return r


@pytest.fixture
def csv_resolver():
    r = IdentityResolver()
    r.csv_map = parse_species_map_csv(read("species_map.csv"))
    r.rmg_dict = parse_species_dictionary(read("species_dictionary.txt"))
    return r
