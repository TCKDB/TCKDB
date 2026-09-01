"""``SoftwareReleaseRef.version`` warns on and normalises a composite banner.

Issue #305 originally refused a ``version`` that embedded a parsed ESS
startup banner (``"Gaussian 09, Revision D.01"`` instead of
``version="09", revision="D.01"``). Refusing broke real ingestion outright:
``test_api_arc_run_fixtures.py::test_arc_run_payload_uploads_cleanly``
HTTP-422'd on five real ARC payloads. The owner's call: warn and
normalise, never refuse.

MEASURED distinct ``(name, version)`` pairs across the ARC fixtures (see
the PR description for the full table):

* 329x ``("ARC", "1.1.0")`` -- clean, must stay silent.
* 206x ``("gaussian", "Gaussian 09, Revision D.01")`` -- composite,
  name matches -- normalise.
* 54x  ``("orca", "ORCA 6.0.0")`` -- composite, name matches -- normalise.
* 5x   ``("gaussian", "ORCA 6.0.0")`` -- composite, name does **not**
  match -- a producer bug (a stale ``name`` rode along with a real
  observed ORCA version). Leave completely untouched; warn that the
  *name* looks wrong instead.
* 63x/23x ``("atom_energy"/"bac_petersson", "None")`` -- a separate,
  unrelated defect (the literal 4-char string, not a composite banner).
  Explicitly out of scope for this guard; asserted here only to prove it
  is left alone.

Each accepted/normalised/warned case is asserted individually, not as a
group loop -- a blanket "these all pass" assertion cannot say which one
broke.
"""

from __future__ import annotations

from tckdb_schemas.fragments.refs import (
    W_SOFTWARE_RELEASE_NAME_LOOKS_WRONG,
    W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE,
    SoftwareReleaseRef,
    collect_software_release_version_warnings,
)

# ---------------------------------------------------------------------------
# The two real composite banners: normalised, and warned
# ---------------------------------------------------------------------------


def test_orca_banner_normalises_leading_name_stripped() -> None:
    """The live ``orca``/``"ORCA 6.0.0"`` pair strips to a bare version."""
    ref = SoftwareReleaseRef(name="orca", version="ORCA 6.0.0")
    assert ref.version == "6.0.0"
    assert ref.revision is None


def test_orca_banner_warns_composite() -> None:
    ref = SoftwareReleaseRef(name="orca", version="ORCA 6.0.0")
    warning = ref.version_warning()
    assert warning is not None
    assert warning.code == W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE


def test_gaussian_banner_normalises_version_and_revision_split() -> None:
    """The live ``gaussian``/``"Gaussian 09, Revision D.01"`` pair splits
    into ``version`` and ``revision`` after stripping the leading name."""
    ref = SoftwareReleaseRef(name="gaussian", version="Gaussian 09, Revision D.01")
    assert ref.version == "09"
    assert ref.revision == "D.01"


def test_gaussian_banner_warns_composite() -> None:
    ref = SoftwareReleaseRef(name="gaussian", version="Gaussian 09, Revision D.01")
    warning = ref.version_warning()
    assert warning is not None
    assert warning.code == W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE
    assert warning.field == "version"


# ---------------------------------------------------------------------------
# The mismatch case: never normalised, no matter what
# ---------------------------------------------------------------------------


def test_mismatched_name_leaves_version_completely_untouched() -> None:
    """The live ``gaussian``/``"ORCA 6.0.0"`` pair (5x in the ARC fixtures).

    A stale/default ``name`` rode along with a version observed from a
    different program. The version is the real, observed fact; the name
    is the producer bug. Stripping here would manufacture
    ``name="gaussian", version="6.0.0"`` -- a Gaussian release that never
    existed -- and destroy the only evidence the record disagrees with
    itself. This is the assertion that catches an over-eager normaliser.
    """
    ref = SoftwareReleaseRef(name="gaussian", version="ORCA 6.0.0")
    assert ref.version == "ORCA 6.0.0"
    assert ref.revision is None
    assert ref.name == "gaussian"


def test_mismatched_name_warns_with_its_own_code() -> None:
    """Distinct code from the composite-normalisation warning: a different
    defect (the *name* is wrong) with a different remedy."""
    ref = SoftwareReleaseRef(name="gaussian", version="ORCA 6.0.0")
    warning = ref.version_warning()
    assert warning is not None
    assert warning.code == W_SOFTWARE_RELEASE_NAME_LOOKS_WRONG
    assert warning.code != W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE
    assert "gaussian" in warning.message
    assert "ORCA 6.0.0" in warning.message


# ---------------------------------------------------------------------------
# A clean deposit stays completely silent
# ---------------------------------------------------------------------------


def test_arc_clean_version_is_untouched() -> None:
    """The live ``ARC``/``"1.1.0"`` pair (329x): no whitespace, nothing to do."""
    ref = SoftwareReleaseRef(name="ARC", version="1.1.0")
    assert ref.version == "1.1.0"
    assert ref.revision is None


def test_arc_clean_version_produces_no_warning() -> None:
    ref = SoftwareReleaseRef(name="ARC", version="1.1.0")
    assert ref.version_warning() is None


# ---------------------------------------------------------------------------
# The depositor's own revision is never overwritten
# ---------------------------------------------------------------------------


def test_depositor_supplied_revision_is_never_overwritten() -> None:
    """A composite ``version`` that would split out its own revision must
    not clobber a revision the depositor already supplied. Both fields are
    left exactly as declared -- choosing between them would be guessing."""
    ref = SoftwareReleaseRef(
        name="gaussian",
        version="Gaussian 09, Revision D.01",
        revision="E.01",
    )
    assert ref.version == "Gaussian 09, Revision D.01"
    assert ref.revision == "E.01"
    warning = ref.version_warning()
    assert warning is not None
    assert warning.code == W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE


# ---------------------------------------------------------------------------
# The 'None' defect: explicitly out of scope, left alone
# ---------------------------------------------------------------------------


def test_the_literal_string_none_is_left_alone() -> None:
    """The live ``atom_energy``/``bac_petersson`` / ``"None"`` pairs (86x
    combined). A separate, unrelated defect -- not composite, no internal
    whitespace, nothing this guard's predicate can act on. Explicitly out
    of scope for this PR (see module docstring); asserted here only to
    pin that this guard does not touch it."""
    ref = SoftwareReleaseRef(name="atom_energy", version="None")
    assert ref.version == "None"
    assert ref.version_warning() is None


# ---------------------------------------------------------------------------
# Other real/plausible clean versions must keep passing silently
# ---------------------------------------------------------------------------


def test_gaussian_16_is_untouched() -> None:
    ref = SoftwareReleaseRef(name="Gaussian", version="16")
    assert ref.version == "16"
    assert ref.version_warning() is None


def test_molpro_style_calendar_version_is_untouched() -> None:
    ref = SoftwareReleaseRef(name="Molpro", version="2025.1")
    assert ref.version == "2025.1"
    assert ref.version_warning() is None


def test_orca_style_bare_version_is_untouched() -> None:
    ref = SoftwareReleaseRef(name="ORCA", version="5.0.3")
    assert ref.version == "5.0.3"
    assert ref.version_warning() is None


def test_missing_version_is_untouched() -> None:
    ref = SoftwareReleaseRef(name="ORCA")
    assert ref.version is None
    assert ref.version_warning() is None


# ---------------------------------------------------------------------------
# The declared string is not silently lost: a trace survives in ``notes``
# ---------------------------------------------------------------------------


def test_normalisation_appends_a_trace_to_notes() -> None:
    ref = SoftwareReleaseRef(name="orca", version="ORCA 6.0.0")
    assert ref.notes is not None
    assert "ORCA 6.0.0" in ref.notes


def test_normalisation_preserves_an_existing_depositor_note() -> None:
    ref = SoftwareReleaseRef(
        name="orca", version="ORCA 6.0.0", notes="depositor's own note"
    )
    assert ref.notes is not None
    assert ref.notes.startswith("depositor's own note")
    assert "ORCA 6.0.0" in ref.notes


def test_mismatch_case_does_not_touch_notes() -> None:
    """Nothing was normalised in the mismatch case, so nothing to trace."""
    ref = SoftwareReleaseRef(name="gaussian", version="ORCA 6.0.0")
    assert ref.notes is None


# ---------------------------------------------------------------------------
# The generic collector, over a nested request-shaped tree
# ---------------------------------------------------------------------------


def test_collector_finds_a_directly_embedded_ref() -> None:
    from pydantic import BaseModel

    class Calc(BaseModel):
        software_release: SoftwareReleaseRef | None = None

    calc = Calc(
        software_release=SoftwareReleaseRef(name="orca", version="ORCA 6.0.0")
    )
    warnings = collect_software_release_version_warnings(calc)
    assert len(warnings) == 1
    assert warnings[0].field == "software_release.version"
    assert warnings[0].code == W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE


def test_collector_finds_refs_nested_in_lists_with_correct_field_paths() -> None:
    from pydantic import BaseModel

    class Calc(BaseModel):
        software_release: SoftwareReleaseRef | None = None

    class Species(BaseModel):
        key: str
        calculations: list[Calc] = []

    class Bundle(BaseModel):
        species: list[Species]

    bundle = Bundle(
        species=[
            Species(
                key="sp0",
                calculations=[
                    Calc(
                        software_release=SoftwareReleaseRef(
                            name="gaussian", version="Gaussian 09, Revision D.01"
                        )
                    )
                ],
            ),
            Species(
                key="sp1",
                calculations=[
                    Calc(
                        software_release=SoftwareReleaseRef(
                            name="gaussian", version="ORCA 6.0.0"
                        )
                    )
                ],
            ),
            Species(key="sp2", calculations=[Calc()]),
        ]
    )
    warnings = collect_software_release_version_warnings(bundle)
    assert len(warnings) == 2
    by_field = {w.field: w.code for w in warnings}
    assert (
        by_field["species[0].calculations[0].software_release.version"]
        == W_SOFTWARE_RELEASE_VERSION_IS_COMPOSITE
    )
    assert (
        by_field["species[1].calculations[0].software_release.version"]
        == W_SOFTWARE_RELEASE_NAME_LOOKS_WRONG
    )


def test_collector_is_silent_over_a_tree_with_no_composite_versions() -> None:
    from pydantic import BaseModel

    class Calc(BaseModel):
        software_release: SoftwareReleaseRef | None = None

    calc = Calc(software_release=SoftwareReleaseRef(name="ARC", version="1.1.0"))
    assert collect_software_release_version_warnings(calc) == []


def test_collector_handles_none_root() -> None:
    assert collect_software_release_version_warnings(None) == []
