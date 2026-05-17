"""ARC-style dry-run: realistic workflow data → ``tckdb_client.builders``.

This example walks the public builder API through a workflow-shaped
upload — the kind of data an ARC-style pipeline would produce — and
prints what would be sent without performing the live POST unless
``TCKDB_BASE_URL`` and ``TCKDB_API_KEY`` are both set.

The chemistry: ``CH4 + OH → CH3 + H2O`` (H-abstraction). One
realistic conformer per species, one TS, one set of artifacts on
the TS opt log + one species-side SP log. Kinetics carries
duplicate-role source calcs (two reactants → two
``reactant_energy`` entries). One species (CH3) carries thermo +
statmech blocks; the rest stay identity-only.

This is **not** an ARC integration. It uses no ARC code and no
ARC files; it mocks the *shape* of an ARC-style submission with
hard-coded XYZs and small fake artifact files. The point is to
test whether the public builder API — including
:class:`SourceCalculations`, :meth:`upload.summary`,
:meth:`upload.emission_diagnostics`,
:meth:`upload.artifact_plan_preview` — reads naturally when fed
data shaped like what a real workflow would emit.

==========================================================
Conformer-boundary policy (``docs/conformer_semantic_boundary.md``)
==========================================================

The script represents **what the workflow stands behind** — one
scientifically meaningful geometry per species — and never the
candidate list the workflow walked past. The upload schema has no
notion of a workflow-preferred-from-N record, and no per-species
list of alternate geometries. A workflow that ran RDKit / CREST /
ARC conformer pruning and converged on one structure ships that
one; the search history lives in the artifacts, not in the upload
schema.

Public-API only: the script uses no private upload attributes and
no manually-synthesised server response mappings. The offline plan
preview comes from :meth:`upload.artifact_plan_preview`.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import warnings
from pathlib import Path

from tckdb_client import TCKDBClient
from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    PlannedArtifactUpload,
    SourceCalculations,
    Species,
    SoftwareRelease,
    Statmech,
    Thermo,
    TransitionState,
)  # noqa: F401  PlannedArtifactUpload imported for its public-API signature only


# Two software releases — realistic ARC-style pipelines often use a
# different code for the higher-LoT single point than for opt/freq.
GAUSSIAN = SoftwareRelease(software="Gaussian", version="16", revision="C.01")
ORCA = SoftwareRelease(software="ORCA", version="5.0.4")

LOT_OPT_FREQ = LevelOfTheory(method="wb97xd", basis="def2tzvp")
LOT_SP = LevelOfTheory(method="ccsd(t)-f12a", basis="cc-pvtz-f12")


# ----------------------------------------------------------------------
# Reaction geometry (one converged structure per species — *not* the
# search history).  XYZs are intentionally tiny stand-ins, not real
# optimised structures.
# ----------------------------------------------------------------------

CH4_XYZ = (
    "5\nch4 (converged opt)\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
OH_XYZ = (
    "2\noh (converged opt)\n"
    "O  0.000  0.000  0.000\n"
    "H  0.000  0.000  0.970"
)
CH3_XYZ = (
    "4\nch3 (converged opt, D3h)\n"
    "C  0.000  0.000  0.000\n"
    "H  1.078  0.000  0.000\n"
    "H -0.539  0.934  0.000\n"
    "H -0.539 -0.934  0.000"
)
H2O_XYZ = (
    "3\nh2o (converged opt)\n"
    "O  0.000  0.000  0.117\n"
    "H  0.000  0.757 -0.469\n"
    "H  0.000 -0.757 -0.469"
)
TS_XYZ = (
    "7\nts (converged saddle, one imag mode)\n"
    "C  0.000  0.000 -1.000\n"
    "H  0.629  0.629 -0.371\n"
    "H -0.629 -0.629 -0.371\n"
    "H -0.629  0.629 -1.629\n"
    "H  0.000  0.000  0.300\n"
    "O  0.000  0.000  1.300\n"
    "H  0.000  0.000  2.270"
)


# ----------------------------------------------------------------------
# Calculation trios — opt + freq at LOT_OPT_FREQ, sp at LOT_SP.
# ----------------------------------------------------------------------


def _build_trio(
    label_prefix: str,
    xyz: str,
    final_energy_hartree: float,
    sp_energy_hartree: float,
    zpe_hartree: float,
    *,
    n_imag: int = 0,
    imag_freq_cm1: float | None = None,
) -> tuple[Calculation, Calculation, Calculation]:
    """Build the opt / freq / sp trio for one species or the TS.

    Mirrors what an ARC-style pipeline would emit: one opt at the
    workflow's primary LoT, one freq on the same geometry, and one
    high-LoT single-point on top of the opt's converged structure.
    Only the converged structure is shipped — any conformer-search
    history that produced it lives in the artifact files.
    """
    geom = Geometry.from_xyz(xyz)
    # ``note=`` carries workflow-side context on each calc. The value
    # is preserved on the builder (visible via
    # ``upload.iter_calculations()``) but is intentionally *not*
    # emitted on the wire — today's CalculationInBundle schema has
    # no per-calc note field. See ``docs/conformer_semantic_boundary.md``
    # for why conformer-search history rides on artifacts, not the
    # upload schema.
    opt = Calculation.opt(
        GAUSSIAN, LOT_OPT_FREQ,
        output_geometry=geom,
        final_energy_hartree=final_energy_hartree,
        converged=True,
        label=f"{label_prefix} opt",
        note=(
            f"{label_prefix}: lowest-energy converged structure; "
            "conformer search history retained as artifacts."
        ),
    )
    freq_kwargs: dict[str, object] = {
        "n_imag": n_imag,
        "zpe_hartree": zpe_hartree,
        "depends_on": opt,
        "label": f"{label_prefix} freq",
        "note": f"{label_prefix}: harmonic on the converged opt geometry.",
    }
    if imag_freq_cm1 is not None:
        freq_kwargs["imag_freq_cm1"] = imag_freq_cm1
    freq = Calculation.freq(GAUSSIAN, LOT_OPT_FREQ, **freq_kwargs)
    sp = Calculation.sp(
        ORCA, LOT_SP,
        electronic_energy_hartree=sp_energy_hartree,
        depends_on=opt,
        label=f"{label_prefix} sp",
        note=f"{label_prefix}: high-LoT single point on the opt geometry.",
    )
    return opt, freq, sp


# ----------------------------------------------------------------------
# Fake artifact files.  ARC-style pipelines emit far more than these
# two; we attach two on purpose so the script exercises both the TS
# bucket and a species-side bucket of ``iter_calculation_entries``.
# ----------------------------------------------------------------------


def _materialise_fake_artifacts(workdir: Path) -> dict[str, Path]:
    workdir.mkdir(parents=True, exist_ok=True)
    ts_opt_log = workdir / "ts_opt.log"
    ts_opt_log.write_text(
        "Entering Gaussian System, Link 0\n"
        "fake TS opt output log — converged saddle; one imag mode @ -1432 cm^-1\n"
        "Normal termination of Gaussian 16 at ...\n"
    )
    ch3_sp_log = workdir / "ch3_sp.log"
    ch3_sp_log.write_text(
        "Reading ORCA output stub\n"
        "fake CH3 high-LoT single-point — final E(CCSD(T)-F12) = ...\n"
    )
    return {"ts_opt": ts_opt_log, "ch3_sp": ch3_sp_log}


# ----------------------------------------------------------------------
# Upload assembly
# ----------------------------------------------------------------------


def build_upload(artifact_paths: dict[str, Path]) -> ComputedReactionUpload:
    """Assemble the ``CH4 + OH → CH3 + H2O`` upload.

    The shape mirrors what an ARC-style adapter would build from a
    converged workflow run: one converged geometry per species, one
    TS, kinetics with duplicate ``reactant_energy`` roles for the
    two reactant SPs, and thermo + statmech blocks on the CH3
    product (the workflow ran StatMech for CH3 only in this demo).
    """
    ch4_opt, ch4_freq, ch4_sp = _build_trio("ch4", CH4_XYZ, -40.518, -40.518, 0.0451)
    oh_opt, oh_freq, oh_sp = _build_trio("oh", OH_XYZ, -75.704, -75.711, 0.00853)
    ch3_opt, ch3_freq, ch3_sp = _build_trio("ch3", CH3_XYZ, -39.838, -39.844, 0.0301)
    h2o_opt, h2o_freq, h2o_sp = _build_trio(
        "h2o", H2O_XYZ, -76.420, -76.428, 0.0214,
    )

    ts_opt, ts_freq, ts_sp = _build_trio(
        "ts", TS_XYZ,
        final_energy_hartree=-116.198,
        sp_energy_hartree=-116.208,
        zpe_hartree=0.0521,
        n_imag=1,
        imag_freq_cm1=-1432.0,
    )

    # Artifacts — one TS-side, one species-side.  add_artifact is
    # local-only metadata; the bundle's to_payload() does not embed
    # bytes.  Phase-2 client.upload_artifacts(...) ships them once
    # the server returns calculation IDs.
    ts_opt.add_artifact(artifact_paths["ts_opt"], kind="output_log")
    ch3_sp.add_artifact(artifact_paths["ch3_sp"], kind="output_log")

    # Species identity — workflow tools sometimes invent custom labels.
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    oh = Species(smiles="[OH]", charge=0, multiplicity=2, label="OH")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    h2o = Species(smiles="O", charge=0, multiplicity=1, label="H2O")

    # Source-calculation provenance via the public ``SourceCalculations``
    # helper.  Duplicate-role kinetics (bimolecular both sides) reads
    # cleanly as scalar/list kwargs; thermo + statmech on the CH3
    # product share one bag and pick subsets with ``.only(...)``.
    kin_sources = SourceCalculations(
        reactant_energy=[ch4_sp, oh_sp],
        product_energy=[ch3_sp, h2o_sp],
        ts_energy=ts_sp,
        freq=ts_freq,
    )
    kin = Kinetics.modified_arrhenius(
        A=1.93e13, A_units="cm3/mol/s",
        n=2.18, Ea=10.13, Ea_units="kJ/mol",
        Tmin=300, Tmax=2500,
        source_calculations=kin_sources.as_list(),
        label="CH4+OH H-abstraction, modified Arrhenius",
    )

    ch3_sources = SourceCalculations(opt=ch3_opt, freq=ch3_freq, sp=ch3_sp)

    rxn = ChemReaction(
        reactants=[ch4, oh],
        products=[ch3, h2o],
        family="H_Abstraction",
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=Geometry.from_xyz(TS_XYZ),
            label="ts",
        ),
        kinetics=[kin],
    )

    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt, ts_freq, ts_sp],
        species_calculations={
            ch4: [ch4_opt, ch4_freq, ch4_sp],
            oh:  [oh_opt,  oh_freq,  oh_sp],
            ch3: [ch3_opt, ch3_freq, ch3_sp],
            h2o: [h2o_opt, h2o_freq, h2o_sp],
        },
        species_thermo={
            ch3: Thermo.nasa(
                coeffs_low=[3.5] + [0.0] * 6,
                coeffs_high=[3.5] + [0.0] * 6,
                t_low=200, t_mid=1000, t_high=5000,
                h298_kj_mol=146.7,
                s298_j_mol_k=194.2,
                source_calculations=ch3_sources.only("opt", "freq", "sp"),
            ),
        },
        species_statmech={
            ch3: Statmech(
                external_symmetry=6,
                point_group="D3h",
                is_linear=False,
                rigid_rotor_kind="symmetric_top",
                statmech_treatment="rrho",
                source_calculations=ch3_sources.only("opt", "freq"),
            ),
        },
    )


# ----------------------------------------------------------------------
# Reporting helpers — print views via public iteration only.
# ----------------------------------------------------------------------


def _print_workflow_mapping(upload: ComputedReactionUpload) -> None:
    """Walk the public iteration helpers and surface how the
    workflow-shaped data lands in the bundle. Pure read; uses no
    private attributes."""
    print("== Workflow → builder mapping ==")
    by_bucket: dict[str, list[Calculation]] = {}
    for entry in upload.iter_calculation_entries():
        by_bucket.setdefault(entry.bucket, []).append(entry.calculation)
    for bucket, calcs in by_bucket.items():
        by_type = {}
        for c in calcs:
            by_type[c.type] = by_type.get(c.type, 0) + 1
        rendered = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
        print(f"  - [{bucket}]  {rendered}")
    # Surface source-calc counts via to_payload — the only
    # supported way to reach the wire-shaped numbers, and a useful
    # sanity check that SourceCalculations has wired in correctly.
    payload = upload.to_payload()
    if payload.get("kinetics"):
        ksrc = payload["kinetics"][0].get("source_calculations", [])
        print(f"  - kinetics[0] source_calculations: {len(ksrc)} entries")
    for sp in payload.get("species", []):
        thermo = sp.get("thermo") or {}
        tsrc = thermo.get("source_calculations", [])
        sm = sp.get("statmech") or {}
        smsrc = sm.get("source_calculations", [])
        if tsrc or smsrc:
            print(
                f"  - species[{sp['key']}]: "
                f"thermo source_calculations={len(tsrc)}, "
                f"statmech source_calculations={len(smsrc)}"
            )


def _print_artifact_summary(upload: ComputedReactionUpload) -> None:
    entries = list(
        upload.iter_calculation_entries(with_artifacts_only=True)
    )
    print("== Artifacts ==")
    if not entries:
        print("(none attached)")
        return
    total = sum(len(e.calculation.artifacts) for e in entries)
    print(f"{total} artifact(s) across {len(entries)} calculation(s):")
    for entry in entries:
        calc = entry.calculation
        for art in calc.artifacts:
            label = calc.label or calc.type
            print(
                f"  - [{entry.bucket:>4}] {label:<10} {art.kind:<14} {art.path}"
            )
    print(
        "\nArtifacts are NOT embedded in the scientific payload. They "
        "are uploaded in a second phase via\n"
        "  plan = upload.artifact_plan(result)\n"
        "  client.upload_artifacts(plan, idempotency_key_prefix=…)\n"
        "once the server returns calculation IDs."
    )


def _print_plan_preview(plan: list[PlannedArtifactUpload]) -> None:
    print("== Artifact plan preview (mock calculation IDs) ==")
    if not plan:
        print("(empty)")
        return
    for entry in plan:
        print(
            f"  - calc_key={entry.calculation_key:<14} "
            f"calc_id={entry.calculation_id:<6} "
            f"kind={entry.kind:<12} path={entry.path}"
        )


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="tckdb-arc-style-dryrun-"))
    artifact_paths = _materialise_fake_artifacts(workdir)
    upload = build_upload(artifact_paths)
    payload = upload.to_payload()

    print("== Payload summary ==")
    print(upload.summary().to_text())
    print()

    print("== Emission diagnostics ==")
    diags = upload.emission_diagnostics()
    if not diags:
        print("(none)")
    for diag in diags:
        print(f"[{diag.level.upper():>7}] {diag.code}")
        print(f"          path: {diag.path}")
        msg = diag.message.strip()
        for chunk in (msg[i : i + 70] for i in range(0, len(msg), 70)):
            print(f"          {chunk}")
    print()

    _print_workflow_mapping(upload)
    print()

    _print_artifact_summary(upload)
    print()

    base_url = os.environ.get("TCKDB_BASE_URL")
    api_key = os.environ.get("TCKDB_API_KEY")
    if not base_url or not api_key:
        print(
            "TCKDB_BASE_URL or TCKDB_API_KEY not set — skipping live upload."
        )
        # Show the artifact plan against fake ids so producers can see
        # the shape ``upload.artifact_plan(result)`` would return once
        # the server response is available. The public
        # ``artifact_plan_preview()`` mints deterministic synthetic
        # ids — same upload state, same preview, no server round trip.
        try:
            plan_preview = upload.artifact_plan_preview()
        except Exception as exc:  # pragma: no cover - preview only
            print(f"(artifact plan preview unavailable: {exc})")
        else:
            _print_plan_preview(plan_preview)
        print()
        print("== Wire payload (truncated) ==")
        rendered = json.dumps(payload, indent=2)
        if len(rendered) > 800:
            rendered = rendered[:800] + "\n  …(truncated)"
        print(rendered)
        return

    with TCKDBClient(base_url, api_key=api_key) as client:
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            result = client.upload(upload, warn_on_dropped_fields=True)
        print("== Server response (truncated) ==")
        print(json.dumps(result, indent=2)[:1200])
        print()

        try:
            plan = upload.artifact_plan(result)
        except Exception as exc:
            print(f"artifact_plan failed: {exc}")
            return

        idem_prefix = f"arc-style-dryrun:{int(time.time())}"
        print(f"== Artifact upload (phase 2, prefix={idem_prefix!r}) ==")
        if not plan:
            print("(no artifacts to upload)")
            return
        results = client.upload_artifacts(plan, idempotency_key_prefix=idem_prefix)
        for entry, response in zip(plan, results):
            n = len(response.get("artifacts", [])) if isinstance(response, dict) else 0
            print(
                f"  - calc_id={entry.calculation_id:<6} "
                f"kind={entry.kind:<12} → {n} server row(s)"
            )


if __name__ == "__main__":
    main()
