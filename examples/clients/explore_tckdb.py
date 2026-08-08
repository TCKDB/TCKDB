"""Explore a live TCKDB deployment — the script form of ``explore_tckdb.ipynb``.

Read-only. Every request here is an anonymous GET against the public
scientific read API; nothing in this file can modify the database.

Run it directly to print a tour of what a deployment holds::

    python examples/clients/explore_tckdb.py
    TCKDB_BASE_URL=http://localhost:8000 python examples/clients/explore_tckdb.py

The notebook alongside this file is the version to hand to a colleague: it
carries the same calls with explanation and plots. This module keeps the
functions importable so the notebook stays thin and both stay in sync.

Only ``requests`` is required. ``tckdb-client`` is the better choice for
programmatic work, but it pins a contract version, and a client newer than
the deployment it talks to will 404 on endpoints the server does not have
yet. Plain HTTP keeps this tour working against any deployment.
"""

from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_BASE_URL = "https://tckdb.homecalvin.com/api/v1"
TIMEOUT_S = 30

#: One connection, reused for every call.
#:
#: This is not a micro-optimisation. The hosted deployment resolves to both A
#: and AAAA records, and ``urllib3`` tries resolved addresses **serially** --
#: so on a network where IPv6 is advertised but blackholed, every new
#: connection waits for the IPv6 attempt to time out before falling back to
#: IPv4. Measured against the hosted instance from such a network: 18.4 s per
#: call with a fresh connection each time, 144 ms with this session. ``curl``
#: does not show the problem because it races both stacks (Happy Eyeballs)
#: and gives up on IPv6 in milliseconds.
#:
#: Reusing the connection pays that cost once instead of per request. It is
#: also simply correct for repeated calls to one host.
SESSION = requests.Session()


def base_url() -> str:
    """The deployment to talk to; override with ``TCKDB_BASE_URL``."""
    return os.environ.get("TCKDB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def get(path: str, **params: Any) -> dict:
    """One anonymous GET against the scientific read API.

    Raises with the server's own error envelope when there is one — TCKDB
    returns a typed ``{"code", "detail", "context"}`` body, which says far
    more than a bare status line.
    """
    response = SESSION.get(f"{base_url()}/{path.lstrip('/')}", params=params, timeout=TIMEOUT_S)
    if not response.ok:
        try:
            envelope = response.json()
            raise RuntimeError(
                f"{response.status_code} {envelope.get('code', '?')}: {envelope.get('detail', response.text[:200])}"
            )
        except ValueError:
            response.raise_for_status()
    return response.json()


def health() -> dict:
    """Liveness check. Works without any query parameters."""
    return get("health")


def find_species(**identifier: Any) -> list[dict]:
    """Look up species by identity.

    The search endpoints are lookup-by-identity, not list-everything: at
    least one of ``smiles``, ``inchi``, ``inchi_key``, ``formula``,
    ``species_ref`` or ``species_entry_ref`` is required. That is deliberate
    for a scientific database — you ask for a molecule, not a page of rows.
    """
    return get("scientific/species/search", **identifier)["records"]


def thermo_for(species_ref: str) -> list[dict]:
    """Every thermodynamic record attached to one species."""
    return get("scientific/thermo/search", species_ref=species_ref)["records"]


def calculations_for(species_ref: str) -> list[dict]:
    """Every quantum-chemistry calculation behind one species."""
    return get("scientific/calculations/search", species_ref=species_ref)["records"]


def statmech_for(species_ref: str) -> list[dict]:
    """Statistical-mechanics records (frequencies, rotors) for one species."""
    return get("scientific/statmech/search", species_ref=species_ref)["records"]


# ---------------------------------------------------------------------------
# NASA polynomials
#
# A NASA-7 fit stores two coefficient sets meeting at ``t_mid``. Evaluating
# them is what turns a stored record back into Cp(T), H(T) and S(T), so the
# functions below are the point of querying thermo at all.
# ---------------------------------------------------------------------------

R_J_MOL_K = 8.31446261815324


def _coefficients(nasa: dict, temperature_k: float) -> list[float]:
    if temperature_k <= nasa["t_mid"]:
        return nasa["low_temperature_coefficients"]
    return nasa["high_temperature_coefficients"]


def cp_j_mol_k(nasa: dict, temperature_k: float) -> float:
    """Cp/R = a1 + a2·T + a3·T² + a4·T³ + a5·T⁴."""
    a = _coefficients(nasa, temperature_k)
    t = temperature_k
    return R_J_MOL_K * (a[0] + a[1] * t + a[2] * t**2 + a[3] * t**3 + a[4] * t**4)


def h_kj_mol(nasa: dict, temperature_k: float) -> float:
    """H/(R·T) = a1 + a2·T/2 + a3·T²/3 + a4·T³/4 + a5·T⁴/5 + a6/T."""
    a = _coefficients(nasa, temperature_k)
    t = temperature_k
    dimensionless = (
        a[0] + a[1] * t / 2 + a[2] * t**2 / 3 + a[3] * t**3 / 4 + a[4] * t**4 / 5 + a[5] / t
    )
    return R_J_MOL_K * t * dimensionless / 1000.0


def s_j_mol_k(nasa: dict, temperature_k: float) -> float:
    """S/R = a1·lnT + a2·T + a3·T²/2 + a4·T³/3 + a5·T⁴/4 + a7."""
    import math

    a = _coefficients(nasa, temperature_k)
    t = temperature_k
    return R_J_MOL_K * (
        a[0] * math.log(t) + a[1] * t + a[2] * t**2 / 2 + a[3] * t**3 / 3 + a[4] * t**4 / 4 + a[6]
    )


def _tour() -> None:
    print(f"deployment : {base_url()}")
    print(f"health     : {health()}\n")

    for smiles in ("C=C", "[CH3]", "[OH]", "O"):
        species = find_species(smiles=smiles)
        if not species:
            print(f"{smiles:8s} not present in this deployment")
            continue
        record = species[0]
        ref = record["species_ref"]
        thermo = thermo_for(ref)
        calcs = calculations_for(ref)
        print(
            f"{smiles:8s} {record['inchi_key']:29s} "
            f"thermo={len(thermo):2d} calcs={len(calcs):3d} statmech={len(statmech_for(ref)):2d}"
        )
        nasa = next((t["thermo"].get("nasa") for t in thermo if t["thermo"].get("nasa")), None)
        if nasa:
            print(
                f"         Cp(300K)={cp_j_mol_k(nasa, 300):7.2f} J/mol/K   "
                f"H(300K)={h_kj_mol(nasa, 300):8.2f} kJ/mol   "
                f"S(300K)={s_j_mol_k(nasa, 300):7.2f} J/mol/K"
            )


if __name__ == "__main__":
    _tour()
