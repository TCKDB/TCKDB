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
    REASON_ARCHIVE_ORIENTATION,
    REASON_ARCHIVE_UNREADABLE,
    read_gaussian_archive_hessian,
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


def test_archive_atoms_not_in_the_input_orientation_are_refused():
    """A rotated frame must not be bound as if it were the input orientation."""
    lines = BUTANE.splitlines()
    for i, line in enumerate(lines):
        parts = line.split()
        if len(parts) == 6 and parts[0] == "1" and parts[1] == "6":  # first row of the Input orientation table
            parts[3] = f"{float(parts[3]) + 0.5:.6f}"
            lines[i] = "      " + "          ".join(parts)
            break
    else:  # pragma: no cover
        raise AssertionError("fixture has no Input orientation table")
    read = read_gaussian_archive_hessian("\n".join(lines) + "\n")
    assert read.parsed is None
    assert read.reason == REASON_ARCHIVE_ORIENTATION


def test_a_log_without_an_input_orientation_table_is_refused():
    lines = [line for line in BUTANE.splitlines() if "Input orientation" not in line]
    read = read_gaussian_archive_hessian("\n".join(lines) + "\n")
    assert read.parsed is None
    assert read.reason == REASON_ARCHIVE_ORIENTATION


@pytest.mark.parametrize("text", ["", "no archive here\n", " 1\\1\\GINC-X\\Freq\\\\@\n"])
def test_a_log_with_no_archive_hessian_reads_nothing_and_says_nothing(text):
    read = read_gaussian_archive_hessian(text)
    assert read.parsed is None
    assert read.reason is None
