"""The method and basis-set catalogue seed: complete, consistent with the identity keys, and definitional."""

from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

import pytest

from app.chemistry.basis_set_names import basis_identity_key
from app.chemistry.dispersion_names import level_identity_keys
from app.chemistry.method_families import UNCLASSIFIED, classify_method
from app.db.models.common import CatalogueMethodKind
from app.services.method_catalogue_seed import SEED_DIR, SeedError, load_seed

CROSSREF_RECORD = Path(__file__).resolve().parents[1] / "fixtures" / "catalogue" / "crossref_verification.json"

#: Method / dispersion / basis spellings in live levels of theory and in the fixtures the catalogue serves.
METHOD_SPELLINGS = (
    "MRCI+Davidson", "b3lyp", "dlpno-ccsd(t)-f12", "gfn2", "hf", "wb97x-d3", "wb97xd", "wb97xd3",
    "GFN2-xTB", "B3LYP", "wB97X-D", "B2PLYPD3", "CCSD(T)-F12", "cbs-qb3", "g4", "mp2", "ccsd(t)", "casscf",
    "nevpt2", "m062x",
)
DISPERSION_SPELLINGS = ("D3BJ",)
BASIS_SPELLINGS = (
    "aug-cc-pV(T+d)Z", "def2tzvp", "def2-TZVP", "cc-pvtz-f12", "aug-cc-pVTZ", "6-31G(d)", "cc-pVDZ", "cc-pVTZ",
    "cc-pVQZ", "def2-svp", "def2-universal-jkfit", "aug-cc-pVTZ-F12", "aug-cc-pvtz/c", "cc-pvtz-f12-cabs",
)

#: Words that rank or judge a method. A description says what a method is, never how good it is.
EVALUATIVE = (
    "accurate", "accuracy", "best", "better", "gold standard", "reliable", "reliability", "recommended",
    "recommend", "superior", "excellent", "robust", "state-of-the-art", "popular", "widely used", "preferred",
    "improved", "outperform", "cheap", "expensive", "efficient", "good", "poor", "precise", "sophisticated",
    "powerful", "high-quality", "inferior", "worse", "worst", "optimal",
)
_EVALUATIVE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in EVALUATIVE) + r")\b", re.IGNORECASE)


@pytest.fixture(scope="module")
def seed():
    return load_seed()


def test_the_seed_holds_every_entry_and_reference(seed):
    assert len(seed.methods) == 22
    assert sum(m.kind is CatalogueMethodKind.dispersion_correction for m in seed.methods) == 2
    assert len(seed.basis_sets) == 15
    assert len(seed.references) == 45


def test_every_key_is_a_fixed_point_of_its_identity_key_function(seed):
    for m in seed.methods:
        for k in m.keys:
            method_key, dispersion_key = level_identity_keys(k, None)
            if m.kind is CatalogueMethodKind.method:
                assert (method_key, dispersion_key) == (k, None), k
            else:
                assert level_identity_keys("b3lyp", k) == ("b3lyp", k), k
    for b in seed.basis_sets:
        for k in b.keys:
            assert basis_identity_key(k) == k


def test_every_spelling_in_use_keys_to_an_entry_or_is_known_uncatalogued(seed):
    method_keys = {k for m in seed.methods if m.kind is CatalogueMethodKind.method for k in m.keys}
    dispersion_keys = {k for m in seed.methods if m.kind is CatalogueMethodKind.dispersion_correction for k in m.keys}
    basis_keys = {k for b in seed.basis_sets for k in b.keys}
    missing = [s for s in METHOD_SPELLINGS if level_identity_keys(s, None)[0] not in method_keys]
    missing += [s for s in DISPERSION_SPELLINGS if level_identity_keys("b3lyp", s)[1] not in dispersion_keys]
    missing += [
        s
        for s in BASIS_SPELLINGS
        if basis_identity_key(s) not in basis_keys and basis_identity_key(s) not in seed.known_uncatalogued_basis_sets
    ]
    assert missing == []
    assert seed.known_uncatalogued_basis_sets == ("aug-cc-pvtz-f12",)


def test_seed_families_agree_with_the_method_family_classification(seed):
    disagreements = []
    for m in seed.methods:
        if m.kind is not CatalogueMethodKind.method:
            assert m.family is None
            continue
        for k in m.keys:
            derived = classify_method(k)
            if derived == UNCLASSIFIED or derived.family != m.family:
                disagreements.append((k, m.family, derived.family))
    assert disagreements == []


def test_descriptions_and_names_carry_no_evaluative_words(seed):
    assert _EVALUATIVE.search("an accurate method")  # the pattern can fire
    offenders = [
        (e.key, field, hit.group(0))
        for e in (*seed.methods, *seed.basis_sets)
        for field in ("description", "full_name", "short_name")
        if (hit := _EVALUATIVE.search(getattr(e, field)))
    ]
    assert offenders == []


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def test_every_reference_is_the_crossref_record_for_its_doi(seed):
    record = {v["doi"].lower(): v for v in json.loads(CROSSREF_RECORD.read_text(encoding="utf-8")).values()}
    assert set(record) == set(seed.references)
    for doi, ref in seed.references.items():
        raw = record[doi]
        assert raw["ok"] and raw["type"] == "journal-article", doi
        assert (ref.year, ref.volume, ref.issue, ref.pages) == (raw["year"], raw["volume"], raw["issue"], raw["page"])
        assert ref.authors == tuple(raw["authors"]), doi
        assert ref.verified_source == "crossref"
        if "�" in raw["title"]:
            assert "�" not in ref.title and len(ref.title) == len(_clean(raw["title"]))
        else:
            assert ref.title == _clean(raw["title"]), doi
        if "�" not in raw["journal"]:
            assert ref.journal == _clean(raw["journal"]), doi


def test_the_seed_literature_file_keeps_the_damaged_registry_text_it_repaired():
    text = (SEED_DIR / "literature.yml").read_text(encoding="utf-8")
    assert text.count("registry_title:") == text.count("�", 0, len(text)) // 3 == 1


def test_no_stored_text_carries_markup_or_damaged_characters(seed):
    for ref in seed.references.values():
        for value in (ref.title, ref.journal):
            assert not re.search(r"[<>�]|\s{2,}", value), (ref.doi, value)


def _copy_seed(tmp_path: Path) -> Path:
    for name in ("literature.yml", "methods.yml", "basis_sets.yml"):
        shutil.copy(SEED_DIR / name, tmp_path / name)
    return tmp_path


@pytest.mark.parametrize(
    "file, old, new, problem",
    [
        ("methods.yml", "key: b3lyp\n", "key: B3LYP\n", "not a fixed point"),
        ("methods.yml", "aliases: [gfn2]", "aliases: [gfn2, b3lyp]", "names both"),
        ("methods.yml", "{doi: 10.1063/1.2436888, role: original}", "{doi: 10.9999/none, role: original}", "not in literature.yml"),
        ("methods.yml", "{doi: 10.1063/1.2436888, role: original}", "{doi: 10.1063/1.2436888, role: invented}", "role must be"),
        ("basis_sets.yml", "family: Pople", "family: Somebody", "not in BASIS_SET_FAMILIES"),
        ("basis_sets.yml", "  - aug-cc-pvtz-f12", "  - def2-tzvp", "known_uncatalogued"),
    ],
)
def test_an_inconsistent_seed_is_refused(tmp_path, file, old, new, problem):
    seed_dir = _copy_seed(tmp_path)
    text = (seed_dir / file).read_text(encoding="utf-8")
    assert old in text
    (seed_dir / file).write_text(text.replace(old, new, 1), encoding="utf-8")
    with pytest.raises(SeedError, match=problem):
        load_seed(seed_dir)
