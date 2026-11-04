"""Check what a depositor typed against what the uploaded deck or log says.

Three checks, all non-blocking: each earns a deposit warning built from a code and TCKDB's own tokens
(:func:`tckdb_schemas.upload_warning.UploadWarning.from_code`), and nothing is refused or corrected.

1. **Typed against read** (:func:`declared_parameter_warnings`). A parameter the depositor typed
   (``source='upload'``) is compared, per canonical key, with the one the parser read from an artifact
   (``source='parser'``). It is compared only when

   * both rows carry a canonical key *and* a canonical value (absent means "not stated", never "the
     default": a deck that is silent about a setting agrees with every declaration of it);
   * the key says it can change the result (``calculation_parameter_vocab.affects_scientific_result``);
   * the parser's program is the program the calculation declares (a Gaussian declaration is not judged
     against an ORCA log).

   ``freq.hessian_method`` is left to its own check (``hessian_method_deck_check`` and the inline refusal in
   ``hessian_method_assumption``), which knows what the stored tolerance rests on; judging it here too would
   say the same thing twice. ``internal_option.iop`` is left out because its raw key, not its value, names
   the option.

2. **Deck against log** (:func:`merge_artifact_observations`). When both an input deck and an output log
   state the same canonical key with different values, that is itself a warning.

3. **Method and basis** (:func:`level_of_theory_warnings`). The method and basis the parser read from the
   route line or keyword line are compared with the declared level of theory through the same identity
   keys the level of theory is deduplicated by, plus the two spellings the route line adds (a spin prefix,
   a frozen-core option on the method).

Message and context carry the canonical key and TCKDB tokens only. The values on each side are depositor
text (a typed value, a deck's spelling) and go under ``depositor_supplied``.

How an input deck, an output log and a reported value share a calculation
-------------------------------------------------------------------------
Parser rows carry two tags: ``artifact_kind`` (the deck, or the log) and ``statement_kind`` (``requested``: the deck,
or the input a log echoes, asked for it; ``reported``: the log prints it as the value the program used). A re-read
replaces only its own artifact kind, so neither erases the other.

Precedence for one setting: **reported, then the input echoed in the log, then the uploaded deck, then a value the
depositor typed.** The log is the record of the run and a deposit often has nothing else; the deck may not be the
exact input that produced it. So where a log's echo and a deck state the same setting the **echo wins** and the deck's
row is not kept (it is dropped, or deleted if it was stored first). A reported row is a different kind of statement
(a number the program printed, under its own canonical keys), so it is kept beside the requested rows and never
replaces one; the read side picks which to show. The result does not depend on which artifact arrived first:

* deck first, then log: the deck's rows for the settings the log's echo states are deleted;
* log first, then deck: the deck's rows for those settings are dropped before they are stored.

Either way the stored requested rows are the log's echo plus the deck's rows for the settings the echo is silent
about. A first output log's statement stands: a later log never silently replaces it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.upload_warning import UploadWarning

from app.chemistry.basis_set_names import basis_identity_key
from app.chemistry.method_families import CLASSIFICATION
from app.chemistry.method_names import method_identity_key
from app.db.models.calculation import Calculation, CalculationParameter, CalculationParameterVocab
from app.db.models.common import ArtifactKind, ParameterSource, ParameterStatementKind
from app.schemas.fragments.calculation import CalculationParameterObservation
from app.services.parameter_closed_values import normalise_typed_value

logger = logging.getLogger(__name__)

W_PARAMETER_DISAGREES_WITH_ARTIFACT = "parameter_disagrees_with_artifact"
W_INPUT_DECK_DISAGREES_WITH_OUTPUT_LOG = "input_deck_disagrees_with_output_log"
W_LEVEL_OF_THEORY_DISAGREES_WITH_ARTIFACT = "level_of_theory_disagrees_with_artifact"
W_OUTPUT_LOG_DISAGREES_WITH_EARLIER_OUTPUT_LOG = "output_log_disagrees_with_earlier_output_log"
W_REPORTED_SETTING_DIFFERS_FROM_REQUESTED = "reported_setting_differs_from_requested"

#: Keys another check owns, or whose value does not name the setting.
_NOT_RECONCILED_HERE = frozenset({"freq.hessian_method", "internal_option.iop"})


def _identity(canonical_key: str | None, section: str | None, raw_key: str, parameter_index: int | None) -> tuple:
    """What makes two parameter rows 'the same setting': the canonical key, else the section and raw key."""
    if canonical_key is not None:
        return ("canonical", canonical_key)
    return ("raw", (section or "").lower(), raw_key.lower(), parameter_index)


def _values_by_key(pairs: Iterable[tuple[str | None, str | None]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for key, value in pairs:
        if key is not None and value is not None:
            out.setdefault(key, set()).add(value)
    return out


def _conflict_warning(key: str, software: str, deck_values: set[str], log_values: set[str]) -> UploadWarning:
    return UploadWarning.from_code(
        field="artifacts",
        code=W_INPUT_DECK_DISAGREES_WITH_OUTPUT_LOG,
        context={
            "canonical_key": key,
            "software": software,
            "depositor_supplied": {"deck_value": sorted(deck_values), "log_value": sorted(log_values)},
        },
    )


def _log_conflict_warning(
    key: str, software: str, earlier: set[str], later: set[str], *, raw: bool = False
) -> UploadWarning:
    """``raw``: ``key`` is a raw key (a reported value with no canonical key); it goes under ``raw_key``."""
    return UploadWarning.from_code(
        field="artifacts",
        code=W_OUTPUT_LOG_DISAGREES_WITH_EARLIER_OUTPUT_LOG,
        context={
            ("raw_key" if raw else "canonical_key"): key,
            "software": software,
            "depositor_supplied": {"earlier_value": sorted(earlier), "later_value": sorted(later)},
        },
    )


def merge_artifact_observations(
    session: Session,
    calculation: Calculation,
    observations: Sequence[CalculationParameterObservation],
    *,
    artifact_kind: ArtifactKind,
    software: str,
) -> tuple[list[CalculationParameterObservation], list[UploadWarning]]:
    """Apply the precedence rule to a new read; return the observations to store and the conflicts found.

    For ``output_log``: the new requested (echoed) rows are compared with the stored deck rows and with an earlier
    log's rows, a setting an earlier log already states is dropped (the first statement stands), and the stored deck
    rows for the settings the log now states are deleted (the echo outranks the deck). New reported rows are compared
    with an earlier log's reported rows and dropped where one already states the value.
    For ``input``: the new deck rows for settings a stored log echo already states are dropped, and compared.

    Call before storing the new batch. Flushes nothing it does not delete.
    """
    parser_rows = list(
        session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calculation.id,
                CalculationParameter.source == ParameterSource.parser,
            )
        )
    )
    reported_kind = ParameterStatementKind.reported
    decks = [r for r in parser_rows if r.artifact_kind is ArtifactKind.input]
    echoes = [r for r in parser_rows if r.artifact_kind is ArtifactKind.output_log and r.statement_kind is not reported_kind]
    reports = [r for r in parser_rows if r.artifact_kind is ArtifactKind.output_log and r.statement_kind is reported_kind]

    def is_report(o) -> bool:
        return getattr(o, "statement_kind", None) is reported_kind

    def named(x, raw: bool) -> tuple[str | None, str | None]:
        """The canonical pair, or for a reported row with no canonical key (Gaussian's grid code) its raw pair."""
        if raw and x.canonical_key is None:
            return f"raw:{x.raw_key}", x.raw_value
        return x.canonical_key, x.canonical_value

    def compare(stored: Sequence[CalculationParameter], new: Sequence, make, raw: bool = False) -> list[UploadWarning]:
        stored_values = _values_by_key(named(r, raw) for r in stored)
        new_values = _values_by_key(named(o, raw) for o in new)
        return [
            make(key, stored_values[key], new_values[key])
            for key in sorted(new_values.keys() & stored_values.keys())
            if new_values[key] != stored_values[key]
        ]

    def ident(x) -> tuple:
        return _identity(x.canonical_key, x.section, x.raw_key, x.parameter_index)

    warnings: list[UploadWarning] = []
    new_reported = [o for o in observations if is_report(o)]
    new_requested = [o for o in observations if not is_report(o)]
    if artifact_kind is ArtifactKind.output_log:
        warnings += compare(decks, new_requested, lambda key, stored, new: _conflict_warning(key, software, stored, new))
        warnings += compare(echoes, new_requested, lambda key, stored, new: _log_conflict_warning(key, software, stored, new))
        warnings += compare(
            reports,
            new_reported,
            lambda key, stored, new: _log_conflict_warning(
                key.removeprefix("raw:"), software, stored, new, raw=key.startswith("raw:")
            ),
            raw=True,
        )
        taken_echo = {ident(r) for r in echoes}
        taken_report = {ident(r) for r in reports}
        kept = [o for o in new_requested if ident(o) not in taken_echo]
        kept_reported = [o for o in new_reported if ident(o) not in taken_report]
        stated = {ident(o) for o in kept}
        for row in decks:
            if ident(row) in stated:
                session.delete(row)
        return kept + kept_reported, warnings

    warnings += compare(echoes, new_requested, lambda key, stored, new: _conflict_warning(key, software, new, stored))
    taken = {ident(r) for r in echoes}
    return [o for o in observations if ident(o) not in taken], warnings


# ---------------------------------------------------------------------------
# typed against read
# ---------------------------------------------------------------------------


def declared_software(calculation: Calculation) -> str | None:
    release = calculation.software_release
    if release is None or release.software is None or not release.software.name:
        return None
    return release.software.name.strip().lower()


def declared_parameter_warnings(
    session: Session,
    calculation: Calculation,
    observed: Iterable[tuple[str | None, str | None]],
    *,
    parsed_software: str,
    artifact_kind: ArtifactKind,
) -> list[UploadWarning]:
    """Warn for each key the depositor typed and the artifact just read (``observed`` pairs) contradict.

    ``observed`` is what the artifact says, whether or not its rows were stored (the precedence rule can drop a
    log's rows), so the judgement is the same whichever artifact of a request is read first.
    """
    declared = declared_software(calculation)
    if declared is None or declared != parsed_software.strip().lower():
        return []
    read = _values_by_key(observed)
    keys = set(read) - _NOT_RECONCILED_HERE
    if not keys:
        return []

    scientific = set(
        session.scalars(
            select(CalculationParameterVocab.canonical_key).where(
                CalculationParameterVocab.canonical_key.in_(keys),
                CalculationParameterVocab.affects_scientific_result.is_(True),
            )
        )
    )
    typed_rows = session.scalars(
        select(CalculationParameter).where(
            CalculationParameter.calculation_id == calculation.id,
            CalculationParameter.source == ParameterSource.upload,
            CalculationParameter.canonical_key.in_(scientific),
        )
    )
    typed_raw = _values_by_key((r.canonical_key, r.canonical_value) for r in typed_rows)
    # A typed value is folded to the form the parsers emit before it is compared; one that cannot be (a non-token
    # for a closed-set key) is never judged.
    typed: dict[str, set[str]] = {}
    for key, values in typed_raw.items():
        folded = {normalise_typed_value(key, v, declared) for v in values}
        if None not in folded:
            typed[key] = {v for v in folded if v is not None}

    warnings: list[UploadWarning] = []
    for key in sorted(scientific & typed.keys()):
        if typed[key] != read[key]:
            warnings.append(
                UploadWarning.from_code(
                    field="parameters",
                    code=W_PARAMETER_DISAGREES_WITH_ARTIFACT,
                    context={
                        "canonical_key": key,
                        "software": declared,
                        "observed_in": artifact_kind.value,
                        "depositor_supplied": {
                            "declared_value": sorted(typed_raw[key]),
                            "observed_value": sorted(read[key]),
                        },
                    },
                )
            )
    return warnings


# ---------------------------------------------------------------------------
# method and basis
# ---------------------------------------------------------------------------

_SPIN_PREFIXES = ("ro", "u", "r")
_BASIS_POLARISATION = (("(d,p)", "**"), ("(d)", "*"))
#: Basis tokens that name no basis set (a general-basis block follows).
_NO_BASIS = frozenset({"gen", "genecp", "chkbasis", "read"})
#: A bare reference method named beside a correlated one (``DLPNO-CCSD(T)-F12`` with its ``HF``) says nothing about
#: the level, and a program's H-atom deck may swap the correlated method for it; it counts only when it is alone.
_REFERENCE_ONLY = frozenset({"hf", "uhf", "rhf", "rohf"})
_METHOD_ALIASES = {"pbe1pbe": "pbe0"}
_DISPERSION_SUFFIX = re.compile(r"^(.+)-(d2|d3|d3bj|d3zero|d4)$")
#: The dispersion a method-name suffix stands for (a bare ``D3`` is either damping, by program).
_SUFFIX_DISPERSION = {
    "d2": {"d2"},
    "d3": {"d3_bj", "d3_zero"},
    "d3bj": {"d3_bj"},
    "d3zero": {"d3_zero"},
    "d4": {"d4"},
}
#: Functionals whose names merely start with ``r`` or ``u`` (method-family table, ``method_families``): not a spin
#: prefix on a shorter method, so ``RPBE`` is not ``PBE`` and ``R2SCAN`` is not ``2SCAN``.
_NOT_SPIN_PREFIXED = frozenset(
    {
        "r2scan", "r2scan-3c", "r2scan0", "r2scan50", "r2scanh", "rscan", "revpbe", "revpbe0", "revtpss", "revtpssh",
        "revdsd-pbep86", "revdsd-pbepbe", "rpbe", "rs2", "rs2c", "rs3", "rasscf", "rm1", "reaxff", "uff",
    }
)


def _strip_options(method: str) -> str:
    """``CCSD(T,Full)`` -> ``CCSD(T)``; ``MP2(FC)`` -> ``MP2``: drop a trailing core option from the method."""
    lowered = method.strip().lower()
    lowered = re.sub(r",(full|fc|frozencore)\)$", ")", lowered)
    return re.sub(r"\((full|fc|frozencore)\)$", "", lowered)


def _is_known_method(key: str) -> bool:
    return method_identity_key(key) in CLASSIFICATION


def _method_keys(method: str, dispersions: frozenset[str] = frozenset()) -> set[str]:
    """Every spelling a method can be compared under: its identity key and the forms a route or keyword line adds.

    A frozen-core option, a spin prefix (``U``/``R``/``RO``), an RI prefix or suffix (``RI-MP2``, ``CCSD(T)-F12/RI``),
    a dispersion suffix and ``PBE1PBE`` for ``PBE0``. A prefix or suffix is stripped only when what is left is a
    known method (the method-family table), the full name is not itself a functional that merely starts that way
    (``RPBE``), and, for a dispersion suffix, the artifact states that dispersion apart (``B3LYP-D3BJ`` against a deck
    with ``B3LYP`` and ``D3BJ``): ``wB97X-D3`` is not ``wB97X``, and ``B97-D3`` is not ``B97``.
    """
    keys = {method_identity_key(method), method_identity_key(_strip_options(method))}
    for _ in range(3):
        for key in list(keys):
            keys.add(re.sub(r"/ri(-f12)?$", "", key))
            if key.startswith("ri-") and _is_known_method(key[3:]):
                keys.add(key[3:])
            suffix = _DISPERSION_SUFFIX.match(key)
            if suffix and _SUFFIX_DISPERSION[suffix.group(2)] & dispersions and _is_known_method(suffix.group(1)):
                keys.add(method_identity_key(suffix.group(1)))
            if key not in _NOT_SPIN_PREFIXED:
                for prefix in _SPIN_PREFIXES:
                    if key.startswith(prefix) and _is_known_method(key[len(prefix):]):
                        keys.add(method_identity_key(key[len(prefix):]))
                        break
            if key in _METHOD_ALIASES:
                keys.add(_METHOD_ALIASES[key])
    return keys


def _basis_key(basis: str | None) -> str | None:
    key = basis_identity_key(basis)
    if key is None:
        return None
    key = key.removesuffix("/auto")
    for spelled, star in _BASIS_POLARISATION:
        key = key.replace(spelled, star)
    return key


def _jobs(method_basis: dict | None, jobs: list[dict] | None) -> list[dict]:
    """Every job's ``{"methods": [...], "basis": ...}``; the single ``method_basis`` when the parser gave no list."""
    if jobs:
        return jobs
    if method_basis:
        methods = method_basis.get("methods") or ([method_basis["method"]] if method_basis.get("method") else [])
        return [{"methods": methods, "basis": method_basis.get("basis")}]
    return []


def _effective_methods(methods: list[str]) -> list[str]:
    correlated = [m for m in methods if m.strip().lower() not in _REFERENCE_ONLY]
    return correlated or methods


_ROUTE_OPT = re.compile(r"(?:^|\s)opt(?:[=(\s]|$)", re.I)
_ROUTE_FREQ = re.compile(r"(?:^|\s)freq(?:[=(\s]|$)", re.I)
_ROUTE_IRC = re.compile(r"(?:^|\s)irc(?:[=(\s]|$)", re.I)


def _job_type_matches(calc_type: str, route: str) -> bool:
    """Whether a route's job keywords fit a calculation type (``opt freq`` fits both an opt and a freq calculation)."""
    has = {
        "opt": bool(_ROUTE_OPT.search(route)),
        "freq": bool(_ROUTE_FREQ.search(route)),
        "irc": bool(_ROUTE_IRC.search(route)),
    }
    if calc_type in has:
        return has[calc_type]
    if calc_type == "sp":
        return not any(has.values())
    return True  # scan, conf, composite, ...: the method and basis alone decide


def _known_route_request(session: Session, calculation: Calculation) -> set[tuple[str, str]]:
    """The canonical settings the calculation's deck (or the depositor's typed rows) already request, resources aside."""
    rows = session.scalars(
        select(CalculationParameter).where(
            CalculationParameter.calculation_id == calculation.id,
            CalculationParameter.statement_kind != ParameterStatementKind.reported,
        )
    )
    return {
        (r.canonical_key, r.canonical_value)
        for r in rows
        if r.canonical_key is not None
        and r.canonical_value is not None
        and r.artifact_kind is not ArtifactKind.output_log
        and (r.section or "").lower() != "resource"
    }


def bind_gaussian_job(session: Session, calculation: Calculation, text: str) -> str | None:
    """The job of a Gaussian log that is this calculation, or ``None`` when no single job is.

    The rule, in order:

    1. A log with one job (one ``Entering Link 1``) is that job.
    2. Otherwise the candidates are the jobs whose echoed first route has the calculation's job type (``opt``,
       ``freq`` or ``irc`` keyword present; ``sp``: none of them) and its level of theory (route method and basis
       equal the calculation's, compared as the declared-level check compares them). One candidate is the job.
    3. Several candidates (``--Link1--`` jobs at one level differing only in keywords, e.g. two single points on
       different grids) are told apart by the settings the calculation already requests, from its input deck or typed
       rows: the job whose route states exactly those settings, resources aside. A calculation with no such rows
       cannot be told apart, so it binds to nothing.
    4. No candidate, or several still: ``None``. Nothing is read from the log, and no other job's value stands in.

    Step 3 reads what is stored when the log arrives, so a log that reaches a calculation before its deck is read as
    "no job" and its rows come from ``refresh_output_log_rows`` once the deck is in; a log with one job is unaffected.
    """
    from app.services.gaussian_parameter_parser import _parse_route_tokens, all_route_texts, parse_method_basis
    from app.services.reported_settings_parser import gaussian_jobs

    jobs = gaussian_jobs(text)
    if len(jobs) == 1:
        return jobs[0]
    lot = calculation.lot
    if lot is None or not lot.method:
        return None
    wanted = _method_keys(lot.method)
    calc_type = getattr(calculation.type, "value", calculation.type)
    matches: list[tuple[str, str]] = []  # (job text, its first route)
    for job in jobs:
        routes = all_route_texts(job)
        found = parse_method_basis(routes[0]) if routes else None
        if not found or not (wanted & _method_keys(found["method"])):
            continue
        basis = found["basis"]
        if lot.basis and basis.strip().lower() not in _NO_BASIS and _basis_key(lot.basis) != _basis_key(basis):
            continue
        if _job_type_matches(calc_type, routes[0]):
            matches.append((job, routes[0]))
    if len(matches) > 1:
        known = _known_route_request(session, calculation)
        if known:
            matches = [
                (job, route)
                for job, route in matches
                if {
                    (p["canonical_key"], p["canonical_value"])
                    for p in _parse_route_tokens(route)
                    if p.get("canonical_key") is not None and p.get("canonical_value") is not None
                }
                == known
            ]
    return matches[0][0] if len(matches) == 1 else None


def level_of_theory_warnings(
    calculation: Calculation,
    method_basis: dict | None,
    jobs: list[dict] | None = None,
    *,
    parsed_software: str,
    artifact_kind: ArtifactKind,
    dispersions: Iterable[str] = (),
) -> list[UploadWarning]:
    """Warn when no job of the artifact has the declared method and basis (never refuses).

    A file can hold several jobs (``--Link1--``, ORCA ``$new_job``): the declaration agrees when one job's pair
    matches it. Silent when the artifact states nothing, when the declaration states no basis, when the basis is a
    general-basis marker, and when the calculation's declared program is not the parser's program.
    """
    lot = calculation.lot
    declared = declared_software(calculation)
    all_jobs = _jobs(method_basis, jobs)
    if not all_jobs or lot is None or declared is None or declared != parsed_software.strip().lower():
        return []

    stated_dispersions = frozenset(dispersions)
    declared_keys = _method_keys(lot.method, stated_dispersions) if lot.method else set()

    def method_ok(job: dict) -> bool:
        methods = _effective_methods(job.get("methods") or [])
        return not methods or not declared_keys or any(declared_keys & _method_keys(m, stated_dispersions) for m in methods)

    def basis_ok(job: dict) -> bool:
        basis = job.get("basis")
        if not basis or not lot.basis or basis.strip().lower() in _NO_BASIS:
            return True
        return _basis_key(lot.basis) == _basis_key(basis)

    if any(method_ok(job) and basis_ok(job) for job in all_jobs):
        return []
    if not any(method_ok(job) for job in all_jobs):
        observed = next((m for job in all_jobs for m in _effective_methods(job.get("methods") or [])), "")
        return [_lot_warning("method", declared, artifact_kind, lot.method, observed)]
    observed = next((job["basis"] for job in all_jobs if job.get("basis")), "")
    return [_lot_warning("basis", declared, artifact_kind, lot.basis, observed)]


def _lot_warning(component: str, software: str, kind: ArtifactKind, declared_text: str, observed_text: str) -> UploadWarning:
    return UploadWarning.from_code(
        field="level_of_theory",
        code=W_LEVEL_OF_THEORY_DISAGREES_WITH_ARTIFACT,
        context={
            "component": component,
            "software": software,
            "observed_in": kind.value,
            "depositor_supplied": {"declared_value": declared_text, "observed_value": observed_text},
        },
    )


# ---------------------------------------------------------------------------
# reported against requested
# ---------------------------------------------------------------------------

#: What an ORCA 6 log reports for a requested ``DefGrid``: the largest angular (Lebedev) grid of the SCF XC grid, in
#: points. Source: ORCA 6.1 manual, "Details on the Numerical Integration Grids", Table 2.48 (angular grid scheme per
#: DEFGRID: SCF XC grid DEFGRID1 = 3, DEFGRID2 = 4, DEFGRID3 = 6) and section 2.10.5 (AngularGrid 3 = Lebedev194,
#: 4 = Lebedev302, 6 = Lebedev590). The integration accuracy is not in the table, so it is not compared. Checked on
#: the fixture logs (`DFT GRID GENERATION`: DEFGRID2 -> Lebedev-302, DEFGRID3 -> Lebedev-590).
_ORCA_DEFGRID_ANGULAR_POINTS = {"defgrid1": "194", "defgrid2": "302", "defgrid3": "590"}

#: What an ORCA log reports for a requested SCF convergence keyword. Source: ORCA 6.1 manual, "Self-Consistent-Field
#: (SCF)", Table 2.9 (threshold choices for convergence keywords): TightSCF TolE 1e-8, TolG 1e-5, TolX 1e-5,
#: TolErr 5e-7; VeryTightSCF 1e-9, 2e-6, 2e-6, 1e-8; LooseSCF 1e-5, 1e-4, 1e-4, 5e-4. Checked on the fixture logs
#: (`SCF SETTINGS`, `Convergence Tolerance:`: TightSCF -> 1e-8 / 1e-5 / 1e-5 / 5e-7).
_ORCA_SCF_PRESETS = {
    "tight": {"scf.tol_energy_eh": 1e-8, "scf.tol_orbital_gradient": 1e-5, "scf.tol_orbital_rotation": 1e-5, "scf.tol_diis_error": 5e-7},
    "very_tight": {"scf.tol_energy_eh": 1e-9, "scf.tol_orbital_gradient": 2e-6, "scf.tol_orbital_rotation": 2e-6, "scf.tol_diis_error": 1e-8},
    "loose": {"scf.tol_energy_eh": 1e-5, "scf.tol_orbital_gradient": 1e-4, "scf.tol_orbital_rotation": 1e-4, "scf.tol_diis_error": 5e-4},
}


def _same_number(a: str, b: float) -> bool:
    try:
        return abs(float(a) - b) <= 1e-9 * abs(b)
    except ValueError:
        return False


def _single(rows: Iterable[CalculationParameter], key: str) -> str | None:
    values = {r.canonical_value for r in rows if r.canonical_key == key and r.canonical_value is not None}
    return next(iter(values)) if len(values) == 1 else None


_SCF_KEYS = ("scf.tol_energy_eh", "scf.tol_orbital_gradient", "scf.tol_orbital_rotation", "scf.tol_diis_error")


_EXPLICIT_TOLERANCES = {
    "tole": "scf.tol_energy_eh",
    "tolg": "scf.tol_orbital_gradient",
    "tolx": "scf.tol_orbital_rotation",
    "tolerr": "scf.tol_diis_error",
}


def orca_preset_disagreements(
    requested_grid: str | None,
    requested_scf: str | None,
    reported: dict[str, str],
    explicit: dict[str, float] | None = None,
) -> list[tuple[str, str, str]]:
    """``(canonical key, requested value, reported value)`` for each reported value the request contradicts.

    ``explicit`` holds thresholds the input sets itself (``%scf TolE 1e-6``): an explicit value wins over the
    keyword's preset for that threshold, so ``!TightSCF`` plus ``TolE 1e-6`` is judged against 1e-6.
    """
    found: list[tuple[str, str, str]] = []
    expected_points = _ORCA_DEFGRID_ANGULAR_POINTS.get(requested_grid or "")
    got_points = reported.get("grid.angular_max")
    if expected_points is not None and got_points is not None and expected_points != got_points:
        found.append(("grid.angular_max", requested_grid, got_points))
    expected = dict(_ORCA_SCF_PRESETS.get(requested_scf or "", {}))
    expected.update(explicit or {})
    for key, value in expected.items():
        got = reported.get(key)
        if got is not None and not _same_number(got, value):
            found.append((key, requested_scf if key not in (explicit or {}) else "explicit", got))
    return found


def reported_setting_disagreements(
    session: Session, calculation: Calculation, software: str
) -> list[tuple[str, str, str]]:
    """``(canonical key, requested value, reported value)`` for each stored report a documented preset contradicts.

    ORCA only. The requested value is the log's echo, else the deck's: the same order as the read. Silent when either
    side is absent, when the requested value is not in the tables, and for Gaussian (its grid report is an
    undocumented internal code, and its 1e-8 threshold is both the default and ``Tight``).
    """
    if software != "orca":
        return []
    rows = list(
        session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calculation.id,
                CalculationParameter.source == ParameterSource.parser,
            )
        )
    )
    reported = [r for r in rows if r.statement_kind is ParameterStatementKind.reported]
    requested = [r for r in rows if r.statement_kind is not ParameterStatementKind.reported]
    echo = [r for r in requested if r.artifact_kind is ArtifactKind.output_log]
    deck = [r for r in requested if r.artifact_kind is ArtifactKind.input]

    def request(key: str) -> str | None:
        return _single(echo, key) or _single(deck, key)

    explicit: dict[str, float] = {}
    for rows_of in (deck, echo):  # the echo is read last, so it overrides the deck
        for r in rows_of:
            key = _EXPLICIT_TOLERANCES.get(r.raw_key.lower()) if (r.section or "").lower() == "scf" else None
            if key is not None:
                try:
                    explicit[key] = float(r.raw_value)
                except ValueError:
                    pass
    return orca_preset_disagreements(
        request("grid.quality"),
        request("scf.convergence"),
        {k: v for k in ("grid.angular_max", *_SCF_KEYS) if (v := _single(reported, k)) is not None},
        explicit,
    )


def reported_setting_warnings(
    session: Session,
    calculation: Calculation,
    software: str,
    *,
    before: Sequence[tuple[str, str, str]] = (),
) -> list[UploadWarning]:
    """Warn for each disagreement the current read created: present now, absent in ``before``, once per key.

    ``before`` is :func:`reported_setting_disagreements` taken ahead of the read, so a later unrelated upload, or the
    second of two artifacts that both leave the same disagreement in place, does not repeat a warning that was
    already given. Both rows are kept.
    """
    already = {key for key, _, _ in before}
    warnings: list[UploadWarning] = []
    for key, requested_value, reported_value in reported_setting_disagreements(session, calculation, software):
        if key in already:
            continue
        warnings.append(
            UploadWarning.from_code(
                field="artifacts",
                code=W_REPORTED_SETTING_DIFFERS_FROM_REQUESTED,
                context={
                    "canonical_key": key,
                    "software": software,
                    "depositor_supplied": {"requested_value": requested_value, "reported_value": reported_value},
                },
            )
        )
    return warnings
