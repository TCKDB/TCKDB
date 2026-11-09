"""The Hessian in a Gaussian log's archive block, for logs that print no force-constant block.

Fixtures are real trimmed Gaussian 16 logs; see ``tests/fixtures/gaussian_archive/ORIGIN.md``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.chemistry.normal_modes import atomic_mass, unpack_lower_triangle
from app.db.models.common import HessianSource
from app.services.gaussian_archive_hessian import (
    FRAME_CONSISTENCY_RTOL,
    REASON_ARCHIVE_ATOMS,
    REASON_ARCHIVE_FRAME,
    REASON_ARCHIVE_UNREADABLE,
    read_gaussian_archive_hessian,
    rigid_motion_residual,
)
from app.services.hessian_parsing import (
    GAUSSIAN_HESSIAN_MARKER,
    parse_hessian_from_artifact,
    parse_triangular_force_constants,
)
from app.services.hessian_reanalysis import (
    HessianPrintFormat,
    ReanalysisStatus,
    compare_spectrum,
    resolve_print_format,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "gaussian_archive"
BUTANE = (FIXTURES / "n_butane_g16_archive_only.log").read_text()
ETHANETHIOL = (FIXTURES / "ethanethiol_g16_both_blocks.log").read_text()


def _printed_frequencies(text: str) -> list[float]:
    return [float(x) for line in text.splitlines() if "Frequencies --" in line for x in line.split("--")[1].split()]


def test_the_butane_fixture_has_no_force_constant_block_but_a_full_archive_triangle():
    assert GAUSSIAN_HESSIAN_MARKER not in BUTANE
    assert parse_triangular_force_constants(BUTANE, marker=GAUSSIAN_HESSIAN_MARKER) is None

    read = read_gaussian_archive_hessian(BUTANE)

    assert read.reason is None
    assert read.parsed is not None
    assert read.parsed.natoms == 14
    assert len(read.parsed.lower_triangle_hartree_bohr2) == 42 * 43 // 2 == 903
    assert read.parsed.source is HessianSource.parsed_log_archive
    assert read.parsed.reference_coords_angstrom is not None
    elements = sorted(e for e, *_ in read.parsed.reference_coords_angstrom)
    assert elements == ["C"] * 4 + ["H"] * 10


def test_the_artifact_entry_point_falls_back_to_the_archive_when_the_block_is_absent():
    parsed = parse_hessian_from_artifact(BUTANE, from_hess_file=False)
    assert parsed is not None
    assert parsed.source is HessianSource.parsed_log_archive


def test_both_blocks_agree_on_a_real_log_and_the_force_constant_block_is_preferred():
    block = parse_triangular_force_constants(ETHANETHIOL, marker=GAUSSIAN_HESSIAN_MARKER)
    archive = read_gaussian_archive_hessian(ETHANETHIOL).parsed
    assert block is not None and archive is not None

    natoms, packed = block
    assert archive.natoms == natoms == 9
    assert len(archive.lower_triangle_hartree_bohr2) == len(packed) == 378
    # The force-constant block prints six significant figures, so an element of magnitude ~0.5 is good to 5e-7:
    # the two are the same matrix, same units (hartree/bohr^2), same atom order, same frame.
    differences = np.abs(np.array(archive.lower_triangle_hartree_bohr2) - np.array(packed))
    assert differences.max() < 1e-5
    assert (differences <= 0.5 * 10.0 ** (np.floor(np.log10(np.abs(packed) + 1e-300)) - 5) + 5e-9 + 1e-12).all()

    preferred = parse_hessian_from_artifact(ETHANETHIOL, from_hess_file=False)
    assert preferred is not None
    assert preferred.source is HessianSource.parsed_log
    assert preferred.lower_triangle_hartree_bohr2 == packed


def test_values_split_across_wrapped_lines_are_rejoined():
    """Re-wrap the archive at another width, so numbers break in different places; the matrix must not change."""
    lines = BUTANE.splitlines()
    start = _archive_line_index(BUTANE)
    end = next(i for i in range(start, len(lines)) if not lines[i].strip() or "Normal termination" in lines[i])
    joined = "".join(line[1:] for line in lines[start:end])
    rewrapped = [" " + joined[k : k + 53] for k in range(0, len(joined), 53)]
    text = "\n".join(lines[:start] + rewrapped + lines[end:]) + "\n"

    original = read_gaussian_archive_hessian(BUTANE).parsed
    again = read_gaussian_archive_hessian(text).parsed
    assert original is not None and again is not None
    assert again.lower_triangle_hartree_bohr2 == original.lower_triangle_hartree_bohr2


def test_the_butane_hessian_reproduces_the_logs_frequencies_within_the_existing_tolerance():
    read = read_gaussian_archive_hessian(BUTANE)
    assert read.parsed is not None
    parsed = read.parsed
    atoms = parsed.reference_coords_angstrom
    assert atoms is not None
    printed = sorted(_printed_frequencies(BUTANE))
    assert len(printed) == 36

    print_format, basis = resolve_print_format(parsed.source, "gaussian")
    assert print_format is HessianPrintFormat.gaussian_archive
    assert "parsed_log_archive" in basis

    result = compare_spectrum(
        unpack_lower_triangle(parsed.lower_triangle_hartree_bohr2, parsed.natoms),
        np.array([[x, y, z] for _, x, y, z in atoms]),
        [atomic_mass(element, None) for element, *_ in atoms],
        [(index, f) for index, f in enumerate(printed, start=1)],
        print_format=print_format,
        print_format_basis=basis,
    )

    assert result.status is ReanalysisStatus.analysed
    assert result.stored_count == result.recovered_count == 36
    assert result.within_tolerance is True
    assert result.matched_count == 36
    assert result.max_abs_deviation_cm1 < 0.01


def _archive_line_index(text: str) -> int:
    lines = text.splitlines()
    return max(i for i, line in enumerate(lines) if line.startswith(" 1\\1\\GINC"))


def test_a_truncated_archive_matrix_is_refused_not_zero_filled():
    lines = BUTANE.splitlines()
    start = _archive_line_index(BUTANE)
    # Cut the Hessian short while keeping the block terminated: drop two lines from the middle of the archive.
    del lines[start + 80 : start + 82]
    read = read_gaussian_archive_hessian("\n".join(lines) + "\n")
    assert read.parsed is None
    assert read.reason == REASON_ARCHIVE_UNREADABLE


def test_a_block_that_never_terminates_is_not_read():
    text = BUTANE.replace("\\\\\\@", "\\\\\\X")
    read = read_gaussian_archive_hessian(text)
    assert read.parsed is None
    assert read.reason is None


def _archive_text(text: str) -> tuple[str, str, str]:
    """``(before, joined archive block, after)`` with the wrap removed, so a test can edit the block's fields."""
    lines = text.splitlines()
    start = _archive_line_index(text)
    end = next(i for i in range(start, len(lines)) if not lines[i].strip() or "Normal termination" in lines[i])
    joined = "".join(line[1:] for line in lines[start:end])
    return "\n".join(lines[:start]) + "\n", joined, "\n" + "\n".join(lines[end:]) + "\n"


def _replace_atoms(text: str, atoms_section: str) -> str:
    before, joined, after = _archive_text(text)
    sections = joined.split("\\\\")
    sections[3] = atoms_section
    return before + " " + "\\\\".join(sections) + after


def _rotated_atoms_section(degrees: float) -> str:
    atoms = read_gaussian_archive_hessian(BUTANE).parsed.reference_coords_angstrom
    a = np.radians(degrees)
    rows = []
    for e, x, y, z in atoms:
        rows.append(f"{e},{np.cos(a) * x - np.sin(a) * y:.10f},{np.sin(a) * x + np.cos(a) * y:.10f},{z:.10f}")
    return "0,1\\" + "\\".join(rows)


def test_the_consistent_archive_has_a_rigid_motion_residual_well_inside_the_bound():
    parsed = read_gaussian_archive_hessian(BUTANE).parsed
    residual = rigid_motion_residual(parsed.lower_triangle_hartree_bohr2, parsed.reference_coords_angstrom)
    assert residual < FRAME_CONSISTENCY_RTOL / 5


def test_a_hessian_whose_atoms_are_rotated_relative_to_it_is_refused():
    """The failure the old Input-orientation guard was named for: matrix and atoms in different frames."""
    read = read_gaussian_archive_hessian(_replace_atoms(BUTANE, _rotated_atoms_section(20.0)))
    assert read.parsed is None
    assert read.reason == REASON_ARCHIVE_FRAME


def test_a_log_without_an_input_orientation_table_still_parses():
    lines = [line for line in BUTANE.splitlines() if "Input orientation" not in line]
    read = read_gaussian_archive_hessian("\n".join(lines) + "\n")
    assert read.reason is None
    assert read.parsed is not None


def test_windows_line_endings_are_read_like_unix_ones():
    assert "\r" not in BUTANE
    again = read_gaussian_archive_hessian(BUTANE.replace("\n", "\r\n")).parsed
    assert again is not None
    assert again.lower_triangle_hartree_bohr2 == read_gaussian_archive_hessian(BUTANE).parsed.lower_triangle_hartree_bohr2


def test_iso_and_fragment_labels_are_read_as_their_element():
    atoms = read_gaussian_archive_hessian(BUTANE).parsed.reference_coords_angstrom
    rows = [f"{e}{'(Iso=13,Fragment=1)' if k == 0 else ''},{x:.10f},{y:.10f},{z:.10f}" for k, (e, x, y, z) in enumerate(atoms)]
    rows[1] = f"{atoms[1][0]}(Fragment=1),0,{atoms[1][1]:.10f},{atoms[1][2]:.10f},{atoms[1][3]:.10f}"
    parsed = read_gaussian_archive_hessian(_replace_atoms(BUTANE, "0,1\\" + "\\".join(rows))).parsed
    assert parsed is not None
    assert [e for e, *_ in parsed.reference_coords_angstrom] == [e for e, *_ in atoms]


def test_a_z_matrix_archive_is_refused_with_its_own_reason():
    zmat = "0,1\\C\\C,1,1.5\\C,2,1.5,1,109.5\\C,3,1.5,2,109.5,1,180."
    read = read_gaussian_archive_hessian(_replace_atoms(BUTANE, zmat))
    assert read.parsed is None
    assert read.reason == REASON_ARCHIVE_ATOMS


def test_an_unreadable_last_block_does_not_fall_back_to_an_earlier_one():
    before, joined, after = _archive_text(BUTANE)
    broken = joined.replace("NImag=0\\\\0", "NImag=0\\\\x", 1)
    assert broken != joined
    text = before + " " + joined + "\n\n" + " " + broken + after
    assert read_gaussian_archive_hessian(before + " " + joined + after).parsed is not None
    read = read_gaussian_archive_hessian(text)
    assert read.parsed is None
    assert read.reason == REASON_ARCHIVE_UNREADABLE


@pytest.mark.parametrize("text", ["", "no archive here\n", " 1\\1\\GINC-X\\Freq\\\\@\n"])
def test_a_log_with_no_archive_hessian_reads_nothing_and_says_nothing(text):
    read = read_gaussian_archive_hessian(text)
    assert read.parsed is None
    assert read.reason is None


# ---------------------------------------------------------------------------
# A non-stationary geometry: rotation invariance gives H (e x r) = e x g, not 0
# ---------------------------------------------------------------------------

_BOHR = 0.52917721067


def _spring_model(atoms_angstrom):
    """Energy sum 0.5 k (d_ij - L_ij)^2 over all pairs, away from its minimum: analytic gradient and Hessian."""
    xyz = np.array([a[1:] for a in atoms_angstrom], dtype=float) / _BOHR
    n = len(xyz)
    rest = np.linspace(1.0, 2.0, n * (n - 1) // 2)
    grad = np.zeros((n, 3))
    hess = np.zeros((3 * n, 3 * n))
    k = 0
    for i in range(n):
        for j in range(i):
            d = xyz[i] - xyz[j]
            dist = np.linalg.norm(d)
            u = d / dist
            stretch = dist - rest[k]
            k += 1
            grad[i] += stretch * u
            grad[j] -= stretch * u
            block = np.outer(u, u) + stretch / dist * (np.eye(3) - np.outer(u, u))
            hess[3 * i : 3 * i + 3, 3 * i : 3 * i + 3] += block
            hess[3 * j : 3 * j + 3, 3 * j : 3 * j + 3] += block
            hess[3 * i : 3 * i + 3, 3 * j : 3 * j + 3] -= block
            hess[3 * j : 3 * j + 3, 3 * i : 3 * i + 3] -= block
    packed = [float(hess[r, c]) for r in range(3 * n) for c in range(r + 1)]
    return packed, grad.reshape(-1).tolist()


_MODEL_ATOMS = [
    ("C", 0.0, 0.0, 0.0),
    ("C", 1.4, 0.2, 0.1),
    ("O", 2.1, 1.3, -0.4),
    ("H", -0.6, 0.9, 0.3),
    ("H", -0.5, -0.8, 0.5),
    ("H", 0.3, 0.1, -1.1),
]


def test_a_consistent_non_stationary_hessian_is_accepted_only_with_its_gradient():
    packed, gradient = _spring_model(_MODEL_ATOMS)
    assert max(abs(g) for g in gradient) > 0.1  # far from stationary
    assert rigid_motion_residual(packed, _MODEL_ATOMS, gradient) < 1e-9
    assert rigid_motion_residual(packed, _MODEL_ATOMS) > FRAME_CONSISTENCY_RTOL  # the plain test would refuse it


def test_the_gradient_sign_convention_is_dE_dx():
    packed, gradient = _spring_model(_MODEL_ATOMS)
    assert rigid_motion_residual(packed, _MODEL_ATOMS, [-g for g in gradient]) > FRAME_CONSISTENCY_RTOL


def test_a_rotated_frame_is_refused_even_with_a_gradient():
    packed, gradient = _spring_model(_MODEL_ATOMS)
    a = np.radians(5.0)
    rotation = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    rotated = [(e, *(rotation @ np.array([x, y, z]))) for e, x, y, z in _MODEL_ATOMS]
    assert rigid_motion_residual(packed, rotated, gradient) > FRAME_CONSISTENCY_RTOL


def _synthetic_log(atoms, packed, gradient) -> str:
    rows = "\\".join(f"{e},{x:.10f},{y:.10f},{z:.10f}" for e, x, y, z in atoms)
    hess = ",".join(f"{v:.8f}" for v in packed)
    grad = ",".join(f"{v:.8f}" for v in gradient)
    body = f"1\\1\\GINC-NODE\\Freq\\X\\\\#P freq\\\\title\\\\0,1\\{rows}\\\\Version=X\\NImag=0\\\\{hess}\\\\{grad}\\\\\\@"
    lines = [" Entering Gaussian System, Link 0=g16", " Gaussian 16:  ES64L-G16RevC.02"]
    lines += [" " + body[k : k + 69] for k in range(0, len(body), 69)]
    return "\n".join(lines) + "\n"


def test_a_log_at_a_non_stationary_geometry_is_read_through_its_gradient_section():
    packed, gradient = _spring_model(_MODEL_ATOMS)
    read = read_gaussian_archive_hessian(_synthetic_log(_MODEL_ATOMS, packed, gradient))
    assert read.reason is None
    assert read.parsed is not None
    assert read.parsed.natoms == 6


def test_a_log_whose_gradient_section_is_missing_falls_back_to_the_stationary_test():
    packed, gradient = _spring_model(_MODEL_ATOMS)
    text = _synthetic_log(_MODEL_ATOMS, packed, gradient)
    # Drop the gradient section: the non-stationary matrix now fails the plain test.
    joined = "".join(line[1:] for line in text.splitlines()[2:])
    no_gradient = joined.replace("\\\\" + ",".join(f"{v:.8f}" for v in gradient), "")
    assert no_gradient != joined
    again = "\n".join(text.splitlines()[:2]) + "\n " + no_gradient + "\n"
    assert read_gaussian_archive_hessian(again).reason == REASON_ARCHIVE_FRAME
