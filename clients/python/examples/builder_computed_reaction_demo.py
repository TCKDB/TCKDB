"""End-to-end computed-reaction builder demo with two-phase artifacts.

Constructs a full CH3 + H -> CH4 reaction with every supported
builder block — reactant/product opt/freq/sp calcs, TS opt/freq/sp,
modified-Arrhenius kinetics with duplicate source-calc roles,
per-species thermo (NASA), per-species statmech, and per-species
transport — attaches a tiny fake artifact to one TS calc and one
species-side calc, prints a one-page payload summary, the upload's
emission diagnostics, and an artifact summary.

By default, with no environment variables set, it exercises the
builder layer without touching a server and shows a mock-IDs
artifact-plan preview:

    python clients/python/examples/builder_computed_reaction_demo.py

With both ``TCKDB_BASE_URL`` and ``TCKDB_API_KEY`` set, it runs the
full two-phase flow:

    export TCKDB_BASE_URL=http://127.0.0.1:8010/api/v1
    export TCKDB_API_KEY=tck_…
    python clients/python/examples/builder_computed_reaction_demo.py

In server mode the demo:

  1. Posts the scientific bundle via ``client.upload(upload,
     warn_on_dropped_fields=True)``.
  2. Calls ``upload.artifact_plan(result)`` to resolve bundle-local
     calc keys against the server-assigned calculation ids.
  3. Calls ``client.upload_artifacts(plan, idempotency_key_prefix=…)``
     to ship each attached file in a second-phase POST.

Fake artifact files are written into a temporary directory so the
demo is self-contained — no external files are required for either
mode.
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
    Transport,
    TransitionState,
)  # noqa: F401  PlannedArtifactUpload imported for its public-API signature only


SR = SoftwareRelease(software="Gaussian", version="16", revision="C.01")
LOT = LevelOfTheory(method="wb97xd", basis="def2tzvp")

CH3_XYZ = "4\nch3\nC 0 0 0\nH 0 0 1\nH 0 1 0\nH 0 -1 0"
H_XYZ = "1\nh\nH 0 0 0"
CH4_XYZ = "5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0"
TS_XYZ = "3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0"


def _calc_trio(label_prefix: str, xyz: str, sp_energy: float):
    """Return ``(opt, freq, sp)`` for one species or the TS."""
    geom = Geometry.from_xyz(xyz)
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom, converged=True,
        final_energy_hartree=sp_energy - 0.05,
        label=f"{label_prefix} opt",
    )
    freq = Calculation.freq(
        SR, LOT, n_imag=0, zpe_hartree=0.03,
        depends_on=opt, label=f"{label_prefix} freq",
    )
    sp = Calculation.sp(
        SR, LOT, electronic_energy_hartree=sp_energy,
        depends_on=opt, label=f"{label_prefix} sp",
    )
    return opt, freq, sp


def _materialise_fake_artifacts(workdir: Path) -> dict[str, Path]:
    """Write tiny stand-in files to ``workdir`` and return their paths.

    The actual files are kept small (a few hundred bytes each) so the
    base64-encoded inline POST stays well under any sane size limit.
    The server's per-kind extension allowlist (``.log`` for
    ``output_log``, ``.gjf`` for ``input``) determines the suffix.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    ts_opt_log = workdir / "ts_opt.log"
    ts_opt_log.write_text(
        "Entering Gaussian System, Link 0\n"
        "fake TS opt output log — demo only\n"
    )
    species_sp_log = workdir / "ch4_sp.log"
    species_sp_log.write_text(
        "Entering Gaussian System, Link 0\n"
        "fake CH4 sp output log — demo only\n"
    )
    return {
        "ts_opt": ts_opt_log,
        "ch4_sp": species_sp_log,
    }


def build_upload(artifact_paths: dict[str, Path]) -> ComputedReactionUpload:
    """Construct the demo CH3 + H -> CH4 ComputedReactionUpload.

    Two artifacts are attached:

    - one to the TS opt calculation (``ts_opt.log``);
    - one to the CH4 species-side SP calculation (``ch4_sp.log``).

    This exercises both the TS-bucket and the species-bucket plan
    paths in :meth:`ComputedReactionUpload.artifact_plan`.
    """
    ch3_opt, ch3_freq, ch3_sp = _calc_trio("ch3", CH3_XYZ, -39.71)
    h_opt, h_freq, h_sp = _calc_trio("h", H_XYZ, -0.5)
    ch4_opt, ch4_freq, ch4_sp = _calc_trio("ch4", CH4_XYZ, -40.51)

    ts_geom = Geometry.from_xyz(TS_XYZ)
    ts_opt = Calculation.opt(
        SR, LOT, output_geometry=ts_geom, converged=True,
        final_energy_hartree=-40.45, label="ts opt",
    )
    ts_freq = Calculation.freq(
        SR, LOT, n_imag=1, imag_freq_cm1=-1200.0, zpe_hartree=0.04,
        depends_on=ts_opt, label="ts freq",
    )
    ts_sp = Calculation.sp(
        SR, LOT, electronic_energy_hartree=-40.42,
        depends_on=ts_opt, label="ts sp",
    )

    # --- Attach artifacts ---------------------------------------------
    # ``add_artifact`` is local-only metadata; the bundle's to_payload()
    # does not embed bytes. The second-phase ``client.upload_artifacts``
    # call later in :func:`main` ships the files once the server has
    # assigned ``calculation.id`` values.
    ts_opt.add_artifact(artifact_paths["ts_opt"], kind="output_log")
    ch4_sp.add_artifact(artifact_paths["ch4_sp"], kind="output_log")

    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    h = Species(smiles="[H]", charge=0, multiplicity=2, label="H")
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")

    # Kinetics-side sources: duplicate roles (two reactants) drop out
    # of the list-of-tuples form naturally via the list kwarg.
    kin_sources = SourceCalculations(
        reactant_energy=[ch3_sp, h_sp],
        product_energy=ch4_sp,
        ts_energy=ts_sp,
        freq=ts_freq,
    )
    kin = Kinetics.modified_arrhenius(
        A=1.2e13, A_units="cm3/mol/s", n=0.5, Ea=10.0, Ea_units="kJ/mol",
        Tmin=300, Tmax=2500,
        source_calculations=kin_sources.as_list(),
    )

    # CH4-side thermo + statmech share an opt/freq bag; ``.only(...)``
    # makes each block's source-role choice explicit at the call site.
    ch4_sources = SourceCalculations(opt=ch4_opt, freq=ch4_freq)

    rxn = ChemReaction(
        reactants=[ch3, h], products=[ch4],
        family="H_Abstraction",
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=ts_geom, label="ts",
        ),
        kinetics=[kin],
    )

    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt, ts_freq, ts_sp],
        species_calculations={
            ch3: [ch3_opt, ch3_freq, ch3_sp],
            h:   [h_opt,   h_freq,   h_sp],
            ch4: [ch4_opt, ch4_freq, ch4_sp],
        },
        species_thermo={
            # Phase-3B: source_calculations validated locally on the
            # reaction path but not emitted on the wire — the demo
            # exercises that diagnostic.
            ch4: Thermo.nasa(
                coeffs_low=[0.5] + [0.0] * 6,
                coeffs_high=[0.5] + [0.0] * 6,
                t_low=200, t_mid=1000, t_high=5000,
                h298_kj_mol=-74.6, s298_j_mol_k=186.3,
                source_calculations=ch4_sources.only("opt", "freq"),
            ),
        },
        species_statmech={
            ch4: Statmech(
                external_symmetry=12, point_group="Td",
                is_linear=False, rigid_rotor_kind="spherical_top",
                statmech_treatment="rrho",
                source_calculations=ch4_sources.only("opt", "freq"),
            ),
        },
        species_transport={
            # Phase-5: bundle schema has no transport field — the
            # demo exercises that diagnostic too.
            ch4: Transport(
                sigma_angstrom=3.8, epsilon_over_k_k=141.4,
                dipole_debye=0.0, polarizability_angstrom3=2.6,
                rotational_relaxation=13.0,
            ),
        },
    )


def _print_artifact_summary(upload: ComputedReactionUpload) -> None:
    """Render the per-bucket attached-artifact view using the public
    iteration API. Uses :meth:`ComputedReactionUpload.iter_calculation_entries`
    so the demo never reaches into private upload state.
    """
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
                f"  - [{entry.bucket:>4}] {label:<10} {art.kind:<18} "
                f"{art.path}"
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
            f"  - calc_key={entry.calculation_key:<10} "
            f"calc_id={entry.calculation_id:<6} "
            f"kind={entry.kind:<12} path={entry.path}"
        )


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="tckdb-builder-demo-"))
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

    _print_artifact_summary(upload)
    print()

    base_url = os.environ.get("TCKDB_BASE_URL")
    api_key = os.environ.get("TCKDB_API_KEY")
    if not base_url or not api_key:
        print(
            "TCKDB_BASE_URL or TCKDB_API_KEY not set — skipping live upload."
        )
        # Show the artifact plan against fake ids so producers can see
        # the shape ``upload.artifact_plan(result)`` would return after
        # the server returns ``calculation_keys`` in the response. The
        # public ``artifact_plan_preview()`` mints deterministic
        # synthetic ids — same upload state, same preview, no server
        # round trip.
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
        # Phase 1 — scientific bundle. ``warn_on_dropped_fields=True``
        # re-emits the diagnostics above as Python ``UserWarning``s so
        # producer pipelines can capture them with the usual
        # warnings.* tooling.
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            result = client.upload(upload, warn_on_dropped_fields=True)
        print("== Server response (truncated) ==")
        print(json.dumps(result, indent=2)[:1200])
        print()

        # Phase 2 — artifact upload. Resolve bundle-local keys against
        # the server's calculation_keys response field, then post each
        # file.
        try:
            plan = upload.artifact_plan(result)
        except Exception as exc:
            print(f"artifact_plan failed: {exc}")
            return

        # Idempotency-key prefix groups all artifacts produced by this
        # demo invocation under one stable namespace so retries land on
        # the server's stored response.
        idem_prefix = f"builder-demo:{int(time.time())}"
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
