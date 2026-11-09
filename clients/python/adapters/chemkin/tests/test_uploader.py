"""M4: uploader against a stub client (no network) + opt-in live smoke test.

The live path is skipped unless ``TCKDB_CHEMKIN_LIVE=1`` and the standard
``TCKDB_BASE_URL`` / ``TCKDB_API_KEY`` env vars are set, so unit runs never
touch a database another agent may be using.
"""

import os
from pathlib import Path

import pytest

from tckdb_chemkin.identity import IdentityResolver, parse_species_dictionary
from tckdb_chemkin.parser import parse_mechanism
from tckdb_chemkin.payloads import ImportConfig, build_all_payloads
from tckdb_chemkin.transport import parse_transport_file
from tckdb_chemkin.uploader import upload_payloads

FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    return (FIXTURES / name).read_text()


@pytest.fixture
def built():
    mech = parse_mechanism(read("mini.inp"), thermo_text=read("therm.dat"))
    mech.transport = parse_transport_file(read("tran.dat"))
    resolver = IdentityResolver(rmg_dict=parse_species_dictionary(read("species_dictionary.txt")))
    return build_all_payloads(mech, resolver, ImportConfig(mechanism_name="MiniMech"))


class _StubResponse:
    def __init__(self, replayed=False):
        self.idempotency_replayed = replayed


class _StubClient:
    """Records every request; never contacts the network."""

    def __init__(self):
        self.calls = []

    def request_json(self, method, path, json=None, idempotency_key=None):
        self.calls.append((method, path, idempotency_key, json))
        return _StubResponse()


def test_uploads_thermo_transport_before_kinetics(built):
    client = _StubClient()
    report = upload_payloads(built, client=client, mechanism_id="mech-1")
    paths = [c[1] for c in client.calls]
    # thermo + transport must precede kinetics (species resolved first, §9)
    first_kin = paths.index("/uploads/kinetics")
    assert all(p != "/uploads/kinetics" for p in paths[:first_kin])
    assert "/uploads/thermo" in paths[:first_kin]
    assert report.summary()["errored_total"] == 0


def test_idempotency_keys_are_deterministic_and_unique(built):
    c1, c2 = _StubClient(), _StubClient()
    upload_payloads(built, client=c1, mechanism_id="mech-1")
    upload_payloads(built, client=c2, mechanism_id="mech-1")
    keys1 = [c[2] for c in c1.calls]
    keys2 = [c[2] for c in c2.calls]
    assert keys1 == keys2  # deterministic across runs
    assert len(set(keys1)) == len(keys1)  # unique per record (DUP disambiguated)


def _thermo_run(thermo, names):
    from types import SimpleNamespace

    payloads = SimpleNamespace(
        thermo=thermo, transport=[], kinetics=[], warnings=[], thermo_names=names, transport_names=[]
    )
    client = _StubClient()
    report = upload_payloads(payloads, client=client, mechanism_id="mech-1")
    return [c[2] for c in client.calls], report


def test_species_sharing_a_smiles_are_keyed_by_spin_not_by_file_order(built):
    """Singlet and triplet CH2 share a SMILES; the key carries the multiplicity, so file order does not matter."""
    import copy

    singlet = copy.deepcopy(built.thermo[0])
    triplet = copy.deepcopy(built.thermo[0])
    triplet["species_entry"] = {**triplet["species_entry"], "multiplicity": 3}
    forward, _ = _thermo_run([singlet, triplet], ["A", "B"])
    backward, _ = _thermo_run([triplet, singlet], ["B", "A"])
    assert len(set(forward)) == 2
    assert dict(zip(["singlet", "triplet"], forward)) == dict(zip(["singlet", "triplet"], backward[::-1]))


def test_species_sharing_smiles_and_spin_fall_back_to_the_chemkin_label_and_say_so(built):
    import copy

    one, two = copy.deepcopy(built.thermo[0]), copy.deepcopy(built.thermo[0])
    keys, report = _thermo_run([one, two], ["CO2(12)", "CO2(99)"])
    assert len(set(keys)) == 2
    assert any("keyed by their CHEMKIN label" in w for w in report.warnings)


def test_dry_run_builds_keys_without_client(built):
    report = upload_payloads(built, client=None, mechanism_id="mech-1", dry_run=True)
    assert report.summary()["errored_total"] == 0
    assert sum(v["ok"] for v in report.summary()["by_kind"].values()) == sum(
        built.counts().values()
    )


def test_per_record_error_is_collected_not_raised(built):
    class _Boom(_StubClient):
        def request_json(self, *a, **k):
            raise RuntimeError("boom")

    report = upload_payloads(built, client=_Boom(), mechanism_id="mech-1")
    assert report.summary()["errored_total"] == sum(built.counts().values())


@pytest.mark.skipif(
    os.environ.get("TCKDB_CHEMKIN_LIVE") != "1"
    or not os.environ.get("TCKDB_BASE_URL"),
    reason="opt-in live upload (set TCKDB_CHEMKIN_LIVE=1 + TCKDB_BASE_URL/API_KEY)",
)
def test_live_upload_smoke(built):  # pragma: no cover - opt-in only
    from tckdb_client import TCKDBClient

    base_url = os.environ["TCKDB_BASE_URL"]
    api_key = os.environ.get("TCKDB_API_KEY")
    with TCKDBClient(base_url, api_key=api_key) as client:
        report = upload_payloads(built, client=client, mechanism_id="chemkin-mini-smoke")
    assert report.summary()["errored_total"] == 0
