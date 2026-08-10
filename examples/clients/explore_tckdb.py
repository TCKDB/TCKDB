"""Explore a live TCKDB deployment - the script form of ``explore_tckdb.ipynb``.

Read-only. Every request here is an anonymous GET against the public
scientific read API; nothing in this file can modify the database.

Run it directly to print a tour of what a deployment holds::

    python examples/clients/explore_tckdb.py
    TCKDB_BASE_URL=http://localhost:8000/api/v1 python examples/clients/explore_tckdb.py

The notebook alongside this file is the version to hand to a colleague: it
carries the same calls with explanation and plots. This module keeps every
function importable, so a script, a notebook or a REPL can all reuse it::

    from examples.clients.explore_tckdb import inventory, chebyshev_k

Only ``requests`` is required (``matplotlib`` only for the notebook's plots).
``tckdb-client`` is the better choice for programmatic work, but it pins a
contract version, and a client newer than the deployment it talks to will 404
on endpoints the server does not have yet. Plain HTTP keeps this tour working
against any deployment, which matters when handing it to someone else.

Nothing here assumes a particular deployment holds particular data. Every
section degrades to "not present in this deployment" rather than raising, so
the tour runs against an empty local instance as well as against a populated
one.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any, Iterable

import requests

DEFAULT_BASE_URL = "https://tckdb.homecalvin.com/api/v1"
TIMEOUT_S = 30

#: How many times to wait out a 429 before giving up.
#:
#: A full tour is a few hundred requests, which is enough to trip a public
#: deployment's rate limiter. The server does not leave a client guessing: it
#: answers 429 with a ``Retry-After`` header and a ``retry_after_seconds``
#: context field saying exactly how long the window has left. Reading that is
#: the whole of correct behaviour here -- a fixed sleep would be either too
#: short (and hammer a limiter that is already refusing) or needlessly long.
RATE_LIMIT_RETRIES = 3

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
SESSION = requests.Session()

R_J_MOL_K = 8.31446261815324
R_KJ_MOL_K = R_J_MOL_K / 1000.0


class TCKDBError(RuntimeError):
    """An error the server described in its own typed envelope.

    TCKDB answers a failed request with ``{"code", "detail", "context"}``
    rather than a bare status line. ``code`` is the machine-readable half and
    is the thing to branch on; ``detail`` is written for a human. Keeping both
    on the exception means a caller never has to re-parse the response.
    """

    def __init__(self, status: int, code: str, detail: Any, context: Any = None):
        self.status = status
        self.code = code
        self.detail = detail
        self.context = context or {}
        super().__init__(f"{status} {code}: {detail}")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def base_url() -> str:
    """The deployment to talk to; override with ``TCKDB_BASE_URL``."""
    return os.environ.get("TCKDB_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _retry_after_seconds(response: requests.Response, envelope: dict) -> float:
    """How long the server says to wait, from the header or the envelope."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    context = envelope.get("context") or {}
    return float(context.get("retry_after_seconds") or 1.0)


def get(path: str, **params: Any) -> dict:
    """One anonymous GET against the read API, waiting out any rate limit.

    :raises TCKDBError: when the server returns its typed error envelope.
    """
    url = f"{base_url()}/{path.lstrip('/')}"
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        response = SESSION.get(url, params=params, timeout=TIMEOUT_S)
        if response.ok:
            return response.json()
        try:
            envelope = response.json()
        except ValueError:
            response.raise_for_status()
            raise  # unreachable; keeps type checkers happy

        if response.status_code == 429 and attempt < RATE_LIMIT_RETRIES:
            delay = _retry_after_seconds(response, envelope)
            # Say so. A silent sleep is indistinguishable from a hang, and this
            # one can be tens of seconds.
            print(f"  rate limited; waiting {delay:.0f}s before retrying {path}")
            time.sleep(delay)
            continue

        raise TCKDBError(
            response.status_code,
            envelope.get("code", "unknown"),
            envelope.get("detail", response.text[:200]),
            envelope.get("context"),
        )
    raise AssertionError("unreachable")


def try_get(path: str, **params: Any) -> dict | None:
    """Like :func:`get`, but ``None`` when this deployment cannot answer.

    Only swallows "this deployment does not have that": a 404 (endpoint or
    record absent) or a 405. A 422 still raises, because that means *the
    request itself* was wrong -- a missing filter, an unknown include token --
    and silently returning ``None`` for a mistake in the query is how a tour
    ends up quietly showing nothing and looking like an empty database.
    """
    try:
        return get(path, **params)
    except TCKDBError as exc:
        if exc.status in (404, 405):
            return None
        raise


def records(path: str, **params: Any) -> list[dict]:
    """The ``records`` list of a search response, or ``[]`` if unavailable."""
    payload = try_get(path, **params)
    return list(payload.get("records", [])) if payload else []


def health() -> dict:
    """Liveness check. Works without any query parameters."""
    return get("health")


# ---------------------------------------------------------------------------
# What is in this deployment?
#
# Every /search endpoint is lookup-by-identity: it requires a filter, and
# answers 422 without one. That is deliberate -- you ask a scientific database
# for a molecule, not for a page of rows. The analytics surfaces are the
# exception, and the only way to ask "what is in here?" before you know what
# to ask for.
# ---------------------------------------------------------------------------

ANALYTICS = ("thermo", "kinetics", "statmech", "calculations")


def inventory() -> dict[str, dict]:
    """Row counts and review roll-ups per analytics surface.

    :returns: ``{surface: {"total": int, "review": {...}}}``, omitting any
        surface this deployment does not serve.
    """
    found: dict[str, dict] = {}
    for surface in ANALYTICS:
        payload = try_get(f"scientific/analytics/{surface}", limit=1)
        if payload is None:
            continue
        found[surface] = {
            "total": payload.get("pagination", {}).get("total", 0),
            "review": payload.get("review_summary", {}),
        }
    return found


VOCABULARIES = ("methods", "basis-sets", "software", "reaction-families")


def vocabularies() -> dict[str, list[dict]]:
    """The controlled vocabularies actually represented, with usage counts.

    This is the fastest way to see what a deployment is *about*: which levels
    of theory it was built from, which codes produced it, which reaction
    families it covers.
    """
    found: dict[str, list[dict]] = {}
    for name in VOCABULARIES:
        payload = try_get(f"scientific/meta/{name}")
        if payload is None:
            continue
        found[name] = list(payload.get("results") or [])
    return found


# ---------------------------------------------------------------------------
# Species
# ---------------------------------------------------------------------------


def find_species(**identifier: Any) -> list[dict]:
    """Look up species by identity.

    At least one of ``smiles``, ``inchi``, ``inchi_key``, ``formula``,
    ``species_ref`` or ``species_entry_ref`` is required.
    """
    return records("scientific/species/search", **identifier)


def substructure_search(smarts: str, **params: Any) -> list[dict]:
    """Every species containing a SMARTS pattern.

    The complement of :func:`find_species`: identity search answers "do you
    have *this* molecule", substructure search answers "what do you have that
    *contains* this". Served by the RDKit cartridge in PostgreSQL, so the
    matching happens in the database rather than by pulling every structure
    across the wire.
    """
    return records("scientific/species/structure-search", query_smarts=smarts, **params)


def thermo_for(species_ref: str) -> list[dict]:
    """Every thermodynamic record attached to one species."""
    return records("scientific/thermo/search", species_ref=species_ref)


def calculations_for(species_ref: str) -> list[dict]:
    """Every quantum-chemistry calculation behind one species."""
    return records("scientific/calculations/search", species_ref=species_ref)


def statmech_for(species_ref: str) -> list[dict]:
    """Statistical-mechanics records (frequencies, rotors) for one species."""
    return records("scientific/statmech/search", species_ref=species_ref)


def transport_for(species_ref: str) -> list[dict]:
    """Transport records (Lennard-Jones parameters) for one species."""
    return records("scientific/transport/search", species_ref=species_ref)


# ---------------------------------------------------------------------------
# NASA polynomials
#
# A NASA-7 fit stores two coefficient sets meeting at ``t_mid``. Evaluating
# them is what turns a stored record back into Cp(T), H(T) and S(T), and is
# the reason to query thermo at all rather than read a summary table.
# ---------------------------------------------------------------------------


def _nasa_coefficients(nasa: dict, temperature_k: float) -> list[float]:
    if temperature_k <= nasa["t_mid"]:
        return nasa["low_temperature_coefficients"]
    return nasa["high_temperature_coefficients"]


def cp_j_mol_k(nasa: dict, temperature_k: float) -> float:
    """Cp/R = a1 + a2*T + a3*T^2 + a4*T^3 + a5*T^4."""
    a = _nasa_coefficients(nasa, temperature_k)
    t = temperature_k
    return R_J_MOL_K * (a[0] + a[1] * t + a[2] * t**2 + a[3] * t**3 + a[4] * t**4)


def h_kj_mol(nasa: dict, temperature_k: float) -> float:
    """H/(R*T) = a1 + a2*T/2 + a3*T^2/3 + a4*T^3/4 + a5*T^4/5 + a6/T."""
    a = _nasa_coefficients(nasa, temperature_k)
    t = temperature_k
    dimensionless = a[0] + a[1] * t / 2 + a[2] * t**2 / 3 + a[3] * t**3 / 4 + a[4] * t**4 / 5 + a[5] / t
    return R_J_MOL_K * t * dimensionless / 1000.0


def s_j_mol_k(nasa: dict, temperature_k: float) -> float:
    """S/R = a1*lnT + a2*T + a3*T^2/2 + a4*T^3/3 + a5*T^4/4 + a7."""
    a = _nasa_coefficients(nasa, temperature_k)
    t = temperature_k
    return R_J_MOL_K * (a[0] * math.log(t) + a[1] * t + a[2] * t**2 / 2 + a[3] * t**3 / 3 + a[4] * t**4 / 4 + a[6])


def first_nasa(thermo_records: Iterable[dict]) -> dict | None:
    """The first NASA-7 polynomial among some thermo records, if any."""
    for record in thermo_records:
        nasa = record.get("thermo", {}).get("nasa")
        if nasa:
            return nasa
    return None


# ---------------------------------------------------------------------------
# Reactions and kinetics
# ---------------------------------------------------------------------------


def reaction_entry_refs(limit: int = 50) -> list[str]:
    """Reaction entries that have kinetics, discovered via analytics.

    ``/scientific/reactions/search`` needs reactants, products or a ref to
    scope it, so it cannot be used to find out *which* reactions exist. The
    kinetics analytics surface can, and every row carries the entry ref that
    the reaction endpoints take.
    """
    rows = records("scientific/analytics/kinetics", limit=limit)
    seen: list[str] = []
    for row in rows:
        ref = row.get("reaction_entry_ref")
        if ref and ref not in seen:
            seen.append(ref)
    return seen


def reaction(reaction_entry_ref: str) -> dict | None:
    """One reaction entry: equation, family, participants, availability."""
    found = records("scientific/reactions/search", reaction_entry_ref=reaction_entry_ref)
    return found[0] if found else None


def reaction_full(reaction_entry_ref: str) -> dict | None:
    """Reaction entry with species, kinetics and transition states attached."""
    return try_get(f"scientific/reaction-entries/{reaction_entry_ref}/full", include="all")


def kinetics_for(reaction_entry_ref: str) -> list[dict]:
    """Every rate coefficient stored for one reaction entry."""
    return records(f"scientific/reaction-entries/{reaction_entry_ref}/kinetics")


def arrhenius_k(parameters: dict, temperature_k: float) -> float:
    """Evaluate k(T) = A * T^n * exp(-Ea / R T) from a kinetics record.

    ``n`` is absent for a plain (two-parameter) Arrhenius form and defaults to
    zero. The returned value carries the record's own ``A_units``: TCKDB does
    not normalise rate units, because the correct unit depends on molecularity
    and silently converting would be a guess. Read ``A_units`` and say what
    you plotted.
    """
    a = parameters["A"]
    n = parameters.get("n") or 0.0
    ea = parameters.get("Ea_kj_mol") or 0.0
    return a * temperature_k**n * math.exp(-ea / (R_KJ_MOL_K * temperature_k))


# ---------------------------------------------------------------------------
# Transition states
# ---------------------------------------------------------------------------


def transition_states(**filters: Any) -> list[dict]:
    """Transition states matching a filter (e.g. ``has_freq="true"``).

    Like every search endpoint this needs at least one filter. The ``has_*``
    family is the useful one here: it asks for saddle points by *what evidence
    exists for them*, not by identity.
    """
    return records("scientific/transition-states/search", **filters)


# ---------------------------------------------------------------------------
# Pressure-dependent networks
#
# A master-equation solve produces k(T, P) for every channel in a network. The
# fitted forms below are how that surface is stored: Chebyshev is a smooth
# two-dimensional fit, PLOG is a set of Arrhenius expressions at fixed
# pressures with logarithmic interpolation between them.
# ---------------------------------------------------------------------------


def networks(**filters: Any) -> list[dict]:
    """Pressure-dependent networks matching a filter."""
    return records("scientific/networks/search", **filters)


def network_kinetics(model: str, limit: int = 50) -> list[dict]:
    """Channel rate coefficients of one fitted form.

    :param model: ``"chebyshev"`` or ``"plog"``.
    """
    flag = {"chebyshev": "has_chebyshev", "plog": "has_plog"}[model]
    include = {"chebyshev": "coefficients", "plog": "plog"}[model]
    return records(
        "scientific/network-kinetics/search",
        include=include,
        limit=limit,
        **{flag: "true"},
    )


def channel_key(record: dict) -> tuple[str, str]:
    """Identify a channel by the composition of the states it connects.

    Two fits of the same channel come back as two different records with two
    different refs, so ref equality cannot pair them. The composition hashes
    can: they are computed from the participating species and stoichiometry,
    which is what makes "the same channel" the same.
    """
    channel = record["network_channel"]
    return (
        channel["source_state_composition_hash"],
        channel["sink_state_composition_hash"],
    )


def channel_label(record: dict) -> str:
    """A readable ``A + B -> C`` label for a channel."""

    def side(state: dict) -> str:
        parts = []
        for participant in state["participants"]:
            stoich = participant["stoichiometry"]
            prefix = f"{stoich} " if stoich != 1 else ""
            parts.append(prefix + participant["canonical_smiles"])
        return " + ".join(parts)

    channel = record["network_channel"]
    return f"{side(channel['source_state'])} -> {side(channel['sink_state'])}"


def _chebyshev_basis(order: int, x: float) -> float:
    """Chebyshev polynomial of the first kind, T_n(x) = cos(n * arccos x)."""
    return math.cos(order * math.acos(x))


def chebyshev_k(record: dict, temperature_k: float, pressure_bar: float) -> float:
    """Evaluate a stored Chebyshev fit at one (T, P).

    The Chemkin convention, which is what the stored coefficients assume:

    * temperature is reduced in **inverse** T, ``Tr = (2/T - 1/Tmin - 1/Tmax)
      / (1/Tmax - 1/Tmin)``, because Arrhenius behaviour is linear in 1/T;
    * pressure is reduced in **log** P, ``Pr = (2 lgP - lgPmin - lgPmax) /
      (lgPmax - lgPmin)``, because falloff is a decade-scale phenomenon;
    * both map the fitted domain onto ``[-1, 1]``, where the Chebyshev basis
      is defined, and the sum is of ``lg k`` (hence ``stores_log10_k``).

    Reduced coordinates are clamped to the fitted domain. Outside it the basis
    functions diverge, so an unclamped evaluation does not extrapolate, it
    produces nonsense -- silently, and by many orders of magnitude.
    """
    meta = record["network_kinetics"]
    block = record["coefficients"]
    t_min, t_max = meta["tmin_k"], meta["tmax_k"]
    p_min, p_max = meta["pmin_bar"], meta["pmax_bar"]

    t_reduced = (2.0 / temperature_k - 1.0 / t_min - 1.0 / t_max) / (1.0 / t_max - 1.0 / t_min)
    p_reduced = (2.0 * math.log10(pressure_bar) - math.log10(p_min) - math.log10(p_max)) / (
        math.log10(p_max) - math.log10(p_min)
    )
    t_reduced = max(-1.0, min(1.0, t_reduced))
    p_reduced = max(-1.0, min(1.0, p_reduced))

    log10_k = sum(
        term["coefficient"]
        * _chebyshev_basis(term["temperature_order"], t_reduced)
        * _chebyshev_basis(term["pressure_order"], p_reduced)
        for term in block["coefficients"]
    )
    return 10.0**log10_k


def plog_k(record: dict, temperature_k: float, pressure_bar: float) -> float:
    """Evaluate a stored PLOG fit at one (T, P).

    Arrhenius is evaluated at each bracketing pressure and the two results are
    interpolated linearly in ``lg k`` against ``lg P`` -- the definition of the
    PLOG form. Below the lowest and above the highest tabulated pressure the
    nearest expression is used unchanged, which is what Chemkin does.
    """
    entries = record["plog"]
    pressures = sorted({entry["pressure_bar"] for entry in entries})

    def arrhenius_sum(pressure: float) -> float:
        return sum(
            entry["a"] * temperature_k ** entry["n"] * math.exp(-entry["ea_kj_mol"] / (R_KJ_MOL_K * temperature_k))
            for entry in entries
            if entry["pressure_bar"] == pressure
        )

    if pressure_bar <= pressures[0]:
        return arrhenius_sum(pressures[0])
    if pressure_bar >= pressures[-1]:
        return arrhenius_sum(pressures[-1])

    low = max(p for p in pressures if p <= pressure_bar)
    high = min(p for p in pressures if p >= pressure_bar)
    if low == high:
        return arrhenius_sum(low)

    k_low, k_high = arrhenius_sum(low), arrhenius_sum(high)
    weight = (math.log10(pressure_bar) - math.log10(low)) / (math.log10(high) - math.log10(low))
    return 10 ** (math.log10(k_low) + weight * (math.log10(k_high) - math.log10(k_low)))


def paired_channel_fits(limit: int = 50) -> list[tuple[dict, dict]]:
    """Channels stored in **both** a Chebyshev and a PLOG form.

    A network fitted twice is a redundancy, and redundancy is where a database
    can be checked against itself: the two forms come from independent fits, so
    evaluating both at the same (T, P) tests the stored numbers rather than
    just reading them back.
    """
    chebyshev = network_kinetics("chebyshev", limit=limit)
    plog = {channel_key(record): record for record in network_kinetics("plog", limit=limit)}
    return [(record, plog[channel_key(record)]) for record in chebyshev if channel_key(record) in plog]


# ---------------------------------------------------------------------------
# Command-line tour
# ---------------------------------------------------------------------------


def _rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def _tour() -> None:
    print(f"deployment : {base_url()}")
    print(f"health     : {health()}")

    _rule("What this deployment holds")
    stock = inventory()
    if not stock:
        print("  no analytics surfaces served here")
    for surface, block in stock.items():
        approved = block["review"].get("approved", 0)
        print(f"  {surface:14s} {block['total']:6d} records  ({approved} approved)")
    for name, entries in vocabularies().items():
        top = ", ".join(f"{e['value']} ({e['count']})" for e in entries[:4])
        print(f"  {name:14s} {top}" if top else f"  {name:14s} -")

    _rule("Species and thermodynamics")
    for smiles in ("C=C", "[CH3]", "[OH]", "O"):
        found = find_species(smiles=smiles)
        if not found:
            print(f"  {smiles:8s} not present in this deployment")
            continue
        record = found[0]
        ref = record["species_ref"]
        thermo = thermo_for(ref)
        print(
            f"  {smiles:8s} {record['inchi_key']:29s} "
            f"thermo={len(thermo):2d} calcs={len(calculations_for(ref)):3d} "
            f"statmech={len(statmech_for(ref)):2d}"
        )
        nasa = first_nasa(thermo)
        if nasa:
            print(
                f"           Cp(300K)={cp_j_mol_k(nasa, 300):7.2f} J/mol/K   "
                f"H(300K)={h_kj_mol(nasa, 300):8.2f} kJ/mol   "
                f"S(300K)={s_j_mol_k(nasa, 300):7.2f} J/mol/K"
            )

    _rule("Reactions")
    refs = reaction_entry_refs(limit=5)
    if not refs:
        print("  no reactions with kinetics in this deployment")
    for ref in refs:
        entry = reaction(ref)
        if entry is None:
            continue
        available = entry["availability"]
        print(f"  {entry['equation']}")
        print(
            f"     family={entry.get('family') or '-'}  "
            f"kinetics={available['kinetics_count']}  "
            f"TS={available['has_transition_state']}  "
            f"atom_map={available['has_atom_map']}"
        )
        for record in kinetics_for(ref)[:1]:
            params = record["parameters"]
            evidence = record.get("evidence_completeness") or {}
            print(
                f"     k(1000 K) = {arrhenius_k(params, 1000.0):.4g} {params['A_units']}"
                f"   evidence {evidence.get('score', '?')}/{evidence.get('max', '?')}"
            )

    _rule("Pressure-dependent networks")
    found = networks(has_channels="true")
    if not found:
        print("  none in this deployment")
    for record in found:
        summary = record["evidence_summary"]
        print(
            f"  {record['network']['name']}: {summary['species_count']} species, "
            f"{summary['channel_count']} channels, {summary['kinetics_count']} k(T,P) records"
        )

    pairs = paired_channel_fits()
    if pairs:
        print(f"\n  {len(pairs)} channel(s) stored in both Chebyshev and PLOG form.")
        print(f"  {'channel':46s} {'k_cheb':>11s} {'k_plog':>11s} {'ratio':>7s}   (1000 K, 1 bar)")
        worst, worst_channel = 0.0, ""
        for index, (cheb, plog) in enumerate(pairs):
            k_c = chebyshev_k(cheb, 1000.0, 1.0)
            k_p = plog_k(plog, 1000.0, 1.0)
            if abs(math.log10(k_c / k_p)) > worst:
                worst = abs(math.log10(k_c / k_p))
                worst_channel = channel_label(cheb)
            if index < 5:
                print(f"  {channel_label(cheb)[:46]:46s} {k_c:11.4g} {k_p:11.4g} {k_c / k_p:7.3f}")
        print(f"\n  worst disagreement: {10**worst:.2f}x on {worst_channel}")


if __name__ == "__main__":
    _tour()
