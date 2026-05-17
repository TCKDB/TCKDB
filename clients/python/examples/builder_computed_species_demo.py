"""Computed-species builder demo with two-phase artifact upload.

Single-species sibling of ``builder_computed_reaction_demo.py``.
Constructs one :class:`Species` with its ``opt + freq + sp``
calculations, attaches a small fake artifact to two of them, then
walks the documented public surface end-to-end:

  - ``upload.to_payload()`` — the scientific bundle that ships first.
  - ``upload.emission_diagnostics()`` — what the builder accepted but
    the bundle schema doesn't (yet) emit on the wire.
  - ``upload.iter_calculation_entries(with_artifacts_only=True)`` —
    public iteration over calcs that need second-phase upload.
  - ``upload.artifact_plan_preview()`` — synthetic-IDs preview of
    what ``upload.artifact_plan(server_result)`` will produce.

Without ``TCKDB_BASE_URL`` / ``TCKDB_API_KEY`` the demo runs
self-contained — temp-dir fake files, mock-IDs preview, no network.
With both env vars set, the last cell performs the live two-phase
upload: bundle via ``client.upload(...)`` then per-artifact POSTs
via ``client.upload_artifacts(plan, idempotency_key_prefix=…)``.

This demo is intentionally simpler than the computed-reaction one —
no TS, no kinetics, no multi-species — so producers wiring their
first computed-species pipeline land on a single, short example.
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
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    PlannedArtifactUpload,
    SourceCalculations,
    Species,
    SoftwareRelease,
    Statmech,
    Thermo,
    Transport,
)  # noqa: F401  PlannedArtifactUpload imported for its public-API signature only


SR = SoftwareRelease(software="Gaussian", version="16", revision="C.01")
LOT = LevelOfTheory(method="wb97xd", basis="def2tzvp")

# Ethanol — different molecule from the reaction demo's CH4 so the
# two demos stay visually distinct in side-by-side use.
ETHANOL_XYZ = (
    "9\nethanol\n"
    "C  -0.748  -0.015   0.024\n"
    "C   0.558   0.591  -0.420\n"
    "O   1.617  -0.260  -0.029\n"
    "H  -1.555   0.611  -0.357\n"
    "H  -0.876  -0.030   1.108\n"
    "H  -0.835  -1.030  -0.367\n"
    "H   0.682   1.604  -0.029\n"
    "H   0.541   0.658  -1.512\n"
    "H   2.443   0.075  -0.404"
)


def _materialise_fake_artifacts(workdir: Path) -> dict[str, Path]:
    """Write tiny stand-in files to ``workdir``.

    Two artifacts in two distinct calcs — one input deck attached to
    ``opt``, one output log attached to ``sp``. Keeps the
    no-network demo and the live-upload demo both self-contained.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    opt_input = workdir / "ethanol_opt.gjf"
    opt_input.write_text(
        "%chk=ethanol.chk\n"
        "# wb97xd/def2tzvp opt freq\n"
        "\nethanol (demo only)\n\n0 1\n[geometry stub]\n"
    )
    sp_log = workdir / "ethanol_sp.log"
    sp_log.write_text(
        "Entering Gaussian System, Link 0\n"
        "fake ethanol SP output log — demo only\n"
    )
    return {"opt": opt_input, "sp": sp_log}


def build_upload(artifact_paths: dict[str, Path]) -> ComputedSpeciesUpload:
    """Assemble the demo ethanol :class:`ComputedSpeciesUpload`.

    One opt + freq + sp triple, one Thermo (NASA), one Statmech, one
    Transport (which the bundle schema doesn't carry today — see the
    emission diagnostic). Two artifacts: an input deck on ``opt``
    and an output log on ``sp``.
    """
    geom = Geometry.from_xyz(ETHANOL_XYZ)
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom, converged=True,
        final_energy_hartree=-154.81, label="ethanol opt",
    )
    freq = Calculation.freq(
        SR, LOT, n_imag=0, zpe_hartree=0.084,
        depends_on=opt, label="ethanol freq",
    )
    sp = Calculation.sp(
        SR, LOT, electronic_energy_hartree=-154.85,
        depends_on=opt, label="ethanol sp",
    )

    # ``add_artifact`` is local-only metadata; the bundle's to_payload()
    # does not embed bytes. The second-phase ``client.upload_artifacts``
    # call later in :func:`main` ships the files once the server has
    # assigned ``calculation.id`` values.
    opt.add_artifact(artifact_paths["opt"], kind="input")
    sp.add_artifact(artifact_paths["sp"], kind="output_log")

    ethanol = Species(
        smiles="CCO", charge=0, multiplicity=1, label="ethanol",
    )
    # One shared SourceCalculations bag for both blocks — thermo wants
    # opt+freq+sp, statmech wants opt+freq. ``.only(...)`` makes each
    # block's source choice explicit at the call site.
    sources = SourceCalculations(opt=opt, freq=freq, sp=sp)
    thermo = Thermo.nasa(
        coeffs_low=[3.5] + [0.0] * 6,
        coeffs_high=[3.5] + [0.0] * 6,
        t_low=200, t_mid=1000, t_high=5000,
        h298_kj_mol=-234.0, s298_j_mol_k=281.6,
        # Computed-species ThermoInBundle DOES carry source_calculations
        # — these survive on the wire (no diagnostic).
        source_calculations=sources.only("opt", "freq", "sp"),
    )
    statmech = Statmech(
        external_symmetry=1, point_group="C1",
        is_linear=False, rigid_rotor_kind="asymmetric_top",
        statmech_treatment="rrho",
        source_calculations=sources.only("opt", "freq"),
    )
    transport = Transport(
        sigma_angstrom=4.5, epsilon_over_k_k=362.6,
        dipole_debye=1.69, polarizability_angstrom3=5.41,
        rotational_relaxation=1.5,
    )

    return ComputedSpeciesUpload(
        species=ethanol,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        thermo=thermo,
        statmech=statmech,
        # Phase-5: ComputedSpeciesUpload validates transport locally
        # but the bundle schema does not yet carry a transport field,
        # so the demo exercises that diagnostic.
        transport=transport,
    )


def _print_artifact_summary(upload: ComputedSpeciesUpload) -> None:
    """Render the attached-artifacts view using the public iterator.

    Uses :meth:`ComputedSpeciesUpload.iter_calculation_entries` so
    the demo never reaches into private upload state — kept in
    lockstep with the matching reaction demo.
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
                f"  - [{entry.bucket:<8}] {label:<14} {art.kind:<14} "
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
            f"  - calc_key={entry.calculation_key:<14} "
            f"calc_id={entry.calculation_id:<6} "
            f"kind={entry.kind:<12} path={entry.path}"
        )


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="tckdb-builder-species-demo-"))
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
        # ``artifact_plan_preview`` mints deterministic synthetic ids
        # so producers see the planned shape without a server round
        # trip. Same upload state → same preview, every time.
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

        # Phase 2 — artifact upload. The computed-species response
        # carries the calc-key → calculation-id mapping nested under
        # each conformer; ``artifact_plan(result)`` knows how to read
        # it. If the response shape is wrong (older server, or a
        # different endpoint accidentally), the builder raises a
        # clear TCKDBBuilderValidationError.
        try:
            plan = upload.artifact_plan(result)
        except Exception as exc:
            print(f"artifact_plan failed: {exc}")
            return

        idem_prefix = f"species-demo:{int(time.time())}"
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
