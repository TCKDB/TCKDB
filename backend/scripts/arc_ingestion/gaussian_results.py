"""Parse calculation results from Gaussian output (log) files.

Extracts:
- Final SCF energy (Hartree)
- Optimization convergence and step count
- Vibrational frequencies (including imaginary)
- Zero-point energy (Hartree)
- Number of imaginary frequencies
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OptResult:
    """Results from a Gaussian geometry optimization."""

    converged: bool
    n_steps: int
    final_energy_hartree: float


@dataclass
class FreqResult:
    """Results from a Gaussian frequency calculation."""

    frequencies_cm1: list[float]
    n_imag: int
    imag_freq_cm1: float | None  # most negative, None if n_imag == 0
    zpe_hartree: float


@dataclass
class SPResult:
    """Results from a single-point energy calculation (or final opt energy)."""

    electronic_energy_hartree: float


def parse_opt_result(lines: list[str]) -> OptResult:
    """Parse optimization results from Gaussian log lines.

    Looks for:
    - "Optimization completed" / "Stationary point found" → converged
    - "Step number N out of" → count steps
    - Last "SCF Done:" line → final energy
    """
    converged = False
    n_steps = 0
    final_energy: float | None = None

    for line in lines:
        if "Optimization completed" in line or "Stationary point found" in line:
            converged = True

        m = re.search(r"Step number\s+(\d+)\s+out of", line)
        if m:
            step = int(m.group(1))
            if step > n_steps:
                n_steps = step

        m = re.search(r"SCF Done:.*=\s+([-\d.]+)\s+A\.U\.", line)
        if m:
            final_energy = float(m.group(1))

    if final_energy is None:
        # Try the archive line format: \HF=-193.1592581\
        for line in lines:
            m = re.search(r"\\HF=([-\d.]+)\\", line)
            if m:
                final_energy = float(m.group(1))

    if final_energy is None:
        raise ValueError("No SCF energy found in log file.")

    return OptResult(
        converged=converged,
        n_steps=n_steps,
        final_energy_hartree=final_energy,
    )


def parse_freq_result(lines: list[str]) -> FreqResult:
    """Parse frequency results from Gaussian log lines.

    Looks for:
    - "Frequencies --" lines → all frequencies
    - "Zero-point correction=" line → ZPE in Hartree
    - "NImag=N" in archive string → number of imaginary frequencies
    """
    frequencies: list[float] = []
    zpe_hartree: float | None = None
    n_imag_from_archive: int | None = None

    for line in lines:
        if "Frequencies --" in line:
            parts = line.split()
            # Format: "Frequencies --    141.8754    233.9485    259.0201"
            idx = parts.index("--") + 1
            for val in parts[idx:]:
                try:
                    frequencies.append(float(val))
                except ValueError:
                    break

        m = re.search(r"Zero-point correction=\s+([-\d.]+)", line)
        if m:
            zpe_hartree = float(m.group(1))

        m = re.search(r"NImag=(\d+)", line)
        if m:
            n_imag_from_archive = int(m.group(1))

    if not frequencies:
        raise ValueError("No frequencies found in log file.")
    if zpe_hartree is None:
        raise ValueError("No zero-point correction found in log file.")

    # Count imaginary frequencies (negative values)
    imag_freqs = [f for f in frequencies if f < 0]
    n_imag = n_imag_from_archive if n_imag_from_archive is not None else len(imag_freqs)
    imag_freq_cm1 = min(imag_freqs) if imag_freqs else None

    return FreqResult(
        frequencies_cm1=frequencies,
        n_imag=n_imag,
        imag_freq_cm1=imag_freq_cm1,
        zpe_hartree=zpe_hartree,
    )


def parse_sp_energy(lines: list[str]) -> SPResult:
    """Parse the final SCF energy from a Gaussian log.

    Works for both dedicated SP jobs and extracting the final energy from opt jobs.
    """
    final_energy: float | None = None

    for line in lines:
        m = re.search(r"SCF Done:.*=\s+([-\d.]+)\s+A\.U\.", line)
        if m:
            final_energy = float(m.group(1))

    if final_energy is None:
        for line in lines:
            m = re.search(r"\\HF=([-\d.]+)\\", line)
            if m:
                final_energy = float(m.group(1))

    if final_energy is None:
        raise ValueError("No SCF energy found in log file.")

    return SPResult(electronic_energy_hartree=final_energy)


# -- File-level convenience wrappers --


def _read_lines(path: str | Path) -> list[str]:
    with open(path) as f:
        return f.readlines()


def parse_opt_result_from_file(path: str | Path) -> OptResult:
    return parse_opt_result(_read_lines(path))


def parse_freq_result_from_file(path: str | Path) -> FreqResult:
    return parse_freq_result(_read_lines(path))


def parse_sp_energy_from_file(path: str | Path) -> SPResult:
    return parse_sp_energy(_read_lines(path))
