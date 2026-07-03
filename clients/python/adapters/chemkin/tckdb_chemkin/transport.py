"""CHEMKIN ``tran.dat`` transport-file parser (stage 1, dependency-light).

Each data row is::

    NAME  GEOM  EPS/K  SIGMA  DIPOLE  POLARIZABILITY  ZROT   ! comment

* GEOM: 0 = single atom, 1 = linear, 2 = nonlinear
* EPS/K: Lennard-Jones well depth epsilon/k_B in K
* SIGMA: Lennard-Jones collision diameter in Angstrom
* DIPOLE: dipole moment in Debye
* POLARIZABILITY: in Angstrom^3
* ZROT: rotational relaxation collision number at 298 K
"""

from __future__ import annotations

from .ast import TransportEntry


def _strip_comment(line: str) -> str:
    idx = line.find("!")
    return line if idx == -1 else line[:idx]


def parse_transport_file(text: str) -> dict[str, TransportEntry]:
    """Parse ``tran.dat`` contents into name -> :class:`TransportEntry`.

    Lines that are blank, comment-only, or a ``TRANSPORT``/``END`` keyword are
    skipped. A malformed row (fewer than 7 fields, or non-numeric values)
    raises ``ValueError`` naming the offending line.
    """
    entries: dict[str, TransportEntry] = {}
    for line_no, raw in enumerate(text.splitlines(), start=1):
        code = _strip_comment(raw).strip()
        if not code:
            continue
        if code.upper() in {"END"} or code.upper().startswith(("TRANSPORT", "TRAN ")):
            continue
        tokens = code.split()
        if len(tokens) < 7:
            raise ValueError(
                f"Transport row {line_no} has {len(tokens)} fields; "
                f"expected at least 7: {code!r}"
            )
        name = tokens[0]
        try:
            geom = int(float(tokens[1]))
            eps = float(tokens[2])
            sigma = float(tokens[3])
            dipole = float(tokens[4])
            polar = float(tokens[5])
            zrot = float(tokens[6])
        except ValueError as exc:
            raise ValueError(
                f"Transport row {line_no} has a non-numeric field: {code!r}"
            ) from exc
        entries[name] = TransportEntry(
            name=name,
            geometry_index=geom,
            eps_over_k=eps,
            sigma_angstrom=sigma,
            dipole_debye=dipole,
            polarizability_angstrom3=polar,
            rot_relaxation=zrot,
            line_no=line_no,
        )
    return entries
