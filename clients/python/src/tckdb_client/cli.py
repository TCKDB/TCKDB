"""CLI entry point for the offline replay engine.

``tckdb-replay <bundle_dir>`` walks a TCKDB Offline Payload Bundle and
posts each pending/failed sidecar via the existing HTTP client. The
CLI is a thin wrapper: it builds a ``client_factory`` closure capturing
``api_key`` and ``timeout``, then hands the bundle to
:func:`tckdb_client.replay.replay_bundle`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from tckdb_client.client import TCKDBClient
from tckdb_client.errors import TCKDBConnectionError, TCKDBHTTPError
from tckdb_client.replay import (
    SUPPORTED_PAYLOAD_KINDS,
    ReplayFailure,
    ReplaySummary,
    replay_bundle,
)

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_ARGPARSE = 2
EXIT_BUNDLE_DIR = 3
EXIT_NOT_FOUND = 4

DEFAULT_FAILURE_GROUPS_SHOWN = 10


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tckdb-replay",
        description=(
            "Replay a TCKDB Offline Payload Bundle to a TCKDB instance. "
            f"Supported payload_kinds: {', '.join(SUPPORTED_PAYLOAD_KINDS)}."
        ),
    )
    parser.add_argument(
        "bundle_dir",
        help="Path to the bundle directory (e.g. tckdb_payloads/).",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the base_url recorded in sidecars.",
    )
    parser.add_argument(
        "--api-key-env",
        default="TCKDB_API_KEY",
        help="Environment variable holding the API key. Default: TCKDB_API_KEY.",
    )
    parser.add_argument(
        "--only-pending",
        action="store_true",
        help="Skip sidecars whose status is 'failed' (only attempt 'pending').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Walk the bundle without making HTTP calls or mutating sidecars.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-request timeout in seconds. Default: 30.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    parser.add_argument(
        "--show-failures",
        action="store_true",
        help=(
            "List every distinct failure group instead of capping at "
            f"the top {DEFAULT_FAILURE_GROUPS_SHOWN}."
        ),
    )
    return parser


def _format_summary(
    summary: ReplaySummary,
    *,
    show_all_failures: bool = False,
    max_failure_groups: int = DEFAULT_FAILURE_GROUPS_SHOWN,
) -> str:
    lines = [
        "tckdb-replay summary:",
        f"  total                              : {summary.total}",
        f"  uploaded                           : {summary.uploaded}",
        f"  skipped (already uploaded)         : {summary.skipped_already_uploaded}",
        f"  skipped (marked skipped)           : {summary.skipped_marked_skipped}",
        f"  skipped (failed; --only-pending set): "
        f"{summary.skipped_failed_due_to_only_pending}",
        f"  skipped (needs regeneration)       : "
        f"{summary.skipped_needs_regeneration}",
        f"  failed                             : {summary.failed}",
        f"  dry_run                            : {summary.dry_run}",
    ]
    if summary.by_kind:
        lines.append("  by kind:")
        for kind in sorted(summary.by_kind):
            buckets = summary.by_kind[kind]
            parts = ", ".join(f"{k}={v}" for k, v in sorted(buckets.items()))
            lines.append(f"    {kind}: {parts}")

    if summary.failures:
        lines.extend(
            _format_failure_groups(
                summary.failures,
                show_all=show_all_failures,
                max_groups=max_failure_groups,
            )
        )

    return "\n".join(lines)


def _group_failures(
    failures: tuple[ReplayFailure, ...],
) -> list[tuple[str, str, int, str]]:
    """Group failures by (payload_kind, last_error).

    Returns a list of ``(kind, last_error, count, sample_path)`` tuples
    sorted by count descending. Identical errors (e.g. 72 sidecars all
    missing ``payload_kind``) collapse to one row, so the operator sees
    *what* went wrong at a glance instead of 72 near-identical lines.
    """
    grouped: dict[tuple[str, str], list[str]] = {}
    for f in failures:
        grouped.setdefault((f.payload_kind, f.last_error), []).append(
            f.sidecar_path
        )
    rows = [
        (kind, err, len(paths), paths[0])
        for (kind, err), paths in grouped.items()
    ]
    rows.sort(key=lambda r: (-r[2], r[0], r[1]))
    return rows


def _format_failure_groups(
    failures: tuple[ReplayFailure, ...],
    *,
    show_all: bool,
    max_groups: int,
) -> list[str]:
    rows = _group_failures(failures)
    total = sum(r[2] for r in rows)
    lines = [
        "",
        f"Failure breakdown ({total} sidecar{'s' if total != 1 else ''}, "
        f"{len(rows)} distinct error{'s' if len(rows) != 1 else ''}):",
    ]
    shown = rows if show_all else rows[:max_groups]
    width = max(len(str(r[2])) for r in shown)
    for kind, err, count, sample in shown:
        lines.append(f"  {count:>{width}}× [{kind}] {err}")
        lines.append(f"  {' ' * width}  e.g. {sample}")
    hidden = len(rows) - len(shown)
    if hidden > 0:
        hidden_count = sum(r[2] for r in rows[len(shown):])
        lines.append(
            f"  … and {hidden} more distinct error"
            f"{'s' if hidden != 1 else ''} "
            f"covering {hidden_count} sidecar"
            f"{'s' if hidden_count != 1 else ''} "
            f"(use --show-failures to list them all)"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    bundle_dir = Path(args.bundle_dir)
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        print(
            f"error: bundle_dir does not exist or is not a directory: {bundle_dir}",
            file=sys.stderr,
        )
        return EXIT_BUNDLE_DIR

    api_key = os.environ.get(args.api_key_env)
    if not args.dry_run and not api_key:
        parser.error(
            f"API key env var {args.api_key_env!r} is not set "
            "(required unless --dry-run is given)"
        )

    timeout = args.timeout

    def _client_factory(base_url: str) -> TCKDBClient:
        return TCKDBClient(base_url=base_url, api_key=api_key, timeout=timeout)

    summary = replay_bundle(
        bundle_dir,
        client_factory=_client_factory,
        base_url_override=args.base_url,
        only_pending=args.only_pending,
        dry_run=args.dry_run,
    )

    print(
        _format_summary(
            summary,
            show_all_failures=args.show_failures,
            max_failure_groups=DEFAULT_FAILURE_GROUPS_SHOWN,
        )
    )
    return EXIT_OK if summary.failed == 0 else EXIT_FAILURES


##############################################################################
# ``tckdb`` — the read-only CLI verb (``tckdb get reaction <ref>``).
#
# This is a second, unrelated console script sharing this module only
# because the entry-point convention (``prog:function``) makes that the
# path of least surprise. It does not touch ``main``/``_build_parser``
# above, which remain ``tckdb-replay``'s exactly as before.
##############################################################################

DEFAULT_BASE_URL = "https://tckdb.homecalvin.com/api/v1"
#: Order matters only for readability; the server does not care.
DEFAULT_INCLUDE = ["species", "kinetics", "transition_states", "networks"]

_NOT_RECORDED = "not recorded"
_NONE_DEPOSITED = "none deposited"

_REF_RE = re.compile(r"^(rxe_|rxn_)[A-Za-z0-9]+$")

#: Tokens the client itself knows might legitimately be missing from an
#: older-but-otherwise-fine API build. Only a token in this set is ever
#: silently dropped and retried on an ``unknown_include_token`` 422 — a
#: typo like ``speceis`` is a real usage error and must surface as one,
#: never vanish into a retry that quietly falls back to the server's
#: default include set with exit 0.
_FORWARD_COMPAT_INCLUDE_TOKENS = frozenset({"networks"})


def _validate_reaction_ref(value: str) -> str:
    """argparse ``type=`` validator for the ``ref`` positional.

    Accepts ``rxe_...``, ``rxn_...``, or a bare integer id. Anything else
    is a usage error argparse turns into exit code 2, before any HTTP
    call is made.
    """
    if _REF_RE.match(value) or value.isdigit():
        return value
    raise argparse.ArgumentTypeError(
        f"invalid reaction reference {value!r}: expected 'rxe_...', "
        "'rxn_...', or an integer reaction_entry id"
    )


def _split_include_tokens(value: str) -> list[str]:
    """argparse ``type=`` for ``--include``: splits a comma-joined value.

    ``--help`` (and the plan) advertise the default as the comma-joined
    string ``species,kinetics,transition_states,networks``. Without this,
    a user pasting that form as one ``--include`` value sent the single
    bogus token ``"species,kinetics,transition_states,networks"``, which
    the server 422s and the retry-once fallback then silently swallows
    (see ``_FORWARD_COMPAT_INCLUDE_TOKENS``), landing on the server's
    default include set with exit 0 — no error, wrong data. Splitting
    here makes both forms work: ``--include species,kinetics`` and
    ``--include species --include kinetics``. Each parsed occurrence
    contributes a list of tokens; :func:`_flatten_include_groups` merges
    them into one flat list.
    """
    tokens = [t.strip() for t in value.split(",")]
    return [t for t in tokens if t]


def _flatten_include_groups(groups: list[list[str]]) -> list[str]:
    flat: list[str] = []
    for group in groups:
        flat.extend(group)
    return flat


def _build_tckdb_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tckdb",
        description="Read-only CLI for the TCKDB scientific read API.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    get_parser = sub.add_parser("get", help="Fetch one record by reference.")
    get_sub = get_parser.add_subparsers(dest="entity", required=True)

    reaction_parser = get_sub.add_parser(
        "reaction",
        help="Fetch a reaction entry (rxe_...), or list entries under a "
        "reaction identity (rxn_...).",
    )
    reaction_parser.add_argument(
        "ref",
        type=_validate_reaction_ref,
        help="rxe_... entry ref, rxn_... identity ref, or an integer "
        "reaction_entry id.",
    )
    reaction_parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"API root. Default: {DEFAULT_BASE_URL}",
    )
    reaction_parser.add_argument(
        "--include",
        action="append",
        type=_split_include_tokens,
        default=None,
        help=(
            "Section to include; repeatable, and/or comma-joined in one "
            "value (both '--include species,kinetics' and '--include "
            "species --include kinetics' work). Overrides the default set "
            f"entirely when given. Default: {','.join(DEFAULT_INCLUDE)}."
        ),
    )
    reaction_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the raw response envelope, pretty-printed, instead "
        "of a human-readable table.",
    )
    reaction_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-request timeout in seconds. Default: 30.",
    )
    return parser


def _parse_unknown_include_tokens(detail: object) -> list[str]:
    """Pull the offending token names out of an ``unknown_include_token`` body.

    The server reports e.g. ``"unknown_include_token: token(s) ['networks']
    not legal for ...\"``. Returns ``[]`` if the shape does not match
    (defensive: a future error-message rewording should not raise here,
    just fail the retry-once heuristic and let the original error surface).
    """
    text = detail if isinstance(detail, str) else str(detail)
    match = re.search(r"token\(s\)\s*\[(.*?)\]", text)
    if not match:
        return []
    return re.findall(r"'([^']*)'", match.group(1))


def _fetch_reaction_full(
    client: TCKDBClient, ref: str, include: list[str]
) -> tuple[dict, list[str]]:
    """Call ``get_reaction_full``, degrading once if the server rejects a token.

    Until the API build carrying the ``networks`` include token is deployed,
    requesting it 422s with ``code=unknown_include_token``. Rather than fail
    the whole command over one section the caller merely defaulted to, retry
    exactly once with the offending token(s) removed — but only when every
    rejected token is one this client already knows might be missing on an
    older deployment (:data:`_FORWARD_COMPAT_INCLUDE_TOKENS`). A rejected
    token outside that set (a typo, e.g. ``speceis``) is a real usage
    error: silently dropping it would retry against the server's *default*
    include set and return exit 0 with the wrong data instead of telling
    the caller their ``--include`` value was wrong. Returns the response
    alongside the list of tokens that had to be dropped, so the caller can
    both render the sections that *did* come back and tell the operator why
    one didn't.
    """
    current = list(include)
    dropped: list[str] = []
    retried = False
    while True:
        try:
            data = client.get_reaction_full(ref, include=current)
            return data, dropped
        except TCKDBHTTPError as exc:
            if (
                retried
                or exc.status_code != 422
                or exc.code != "unknown_include_token"
            ):
                raise
            rejected = set(_parse_unknown_include_tokens(exc.detail)) & set(current)
            # Every rejected token must be one this client recognizes as
            # forward-compat, or a typo riding alongside a known token
            # (or a lone typo) would be silently dropped too.
            if not rejected or not rejected <= _FORWARD_COMPAT_INCLUDE_TOKENS:
                raise
            retried = True
            dropped.extend(sorted(rejected))
            current = [t for t in current if t not in rejected]


def _fmt_scalar(value: Any, *, unit: str = "") -> str:
    if value is None:
        return _NOT_RECORDED
    if isinstance(value, float):
        text = f"{value:g}"
    else:
        text = str(value)
    return f"{text} {unit}".strip() if unit else text


def _level_display(level: dict | None) -> str:
    if not level:
        return _NOT_RECORDED
    return level.get("display") or level.get("label") or _NOT_RECORDED


def _format_participant(p: dict) -> str:
    """One equation-side participant: formula when served, SMILES otherwise.

    Stoichiometry is shown as a leading coefficient only when > 1 and
    actually served — a missing ``stoichiometry`` key (pre-deploy API)
    is never assumed to be 1 dressed up as a fact; it is simply not
    printed, the same way a plain equation omits a coefficient of 1.
    """
    label = p.get("formula") or p.get("smiles") or "?"
    stoich = p.get("stoichiometry")
    if isinstance(stoich, int) and stoich > 1:
        return f"{stoich} {label}"
    return label


def _equation_line(species: dict, reversible: bool) -> str:
    arrow = "<=>" if reversible else "->"
    left = " + ".join(_format_participant(p) for p in (species.get("reactants") or []))
    right = " + ".join(_format_participant(p) for p in (species.get("products") or []))
    return f"{left} {arrow} {right}"


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def _fmt_row(cells: list[str]) -> str:
        return "  ".join(str(c).ljust(w) for c, w in zip(cells, widths))

    lines = [_fmt_row(headers), _fmt_row(["-" * w for w in widths])]
    lines.extend(_fmt_row(row) for row in rows)
    return "\n".join(lines)


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


_KINETICS_HEADERS = [
    "kinetics_ref",
    "model_kind",
    "A",
    "n",
    "Ea",
    "T_range",
    "geometry_level",
    "frequency_level",
    "energy_level",
    "energy_source",
]


def _kinetics_row(k: dict) -> list[str]:
    params = k.get("parameters") or {}
    tc = k.get("temperature_coverage") or {}
    tmin, tmax = tc.get("record_min_k"), tc.get("record_max_k")
    t_range = _NOT_RECORDED if tmin is None or tmax is None else f"{tmin:g}-{tmax:g} K"
    levels = k.get("levels")
    if levels:
        geometry = _level_display(levels.get("geometry"))
        frequency = _level_display(levels.get("frequency"))
        energy = _level_display(levels.get("energy"))
        energy_source = levels.get("energy_source") or _NOT_RECORDED
    else:
        geometry = frequency = energy = energy_source = _NOT_RECORDED
    return [
        k.get("kinetics_ref") or _NOT_RECORDED,
        k.get("model_kind") or _NOT_RECORDED,
        _fmt_scalar(params.get("A"), unit=params.get("A_units") or ""),
        _fmt_scalar(params.get("n")),
        _fmt_scalar(params.get("Ea_kj_mol"), unit="kJ/mol"),
        t_range,
        geometry,
        frequency,
        energy,
        energy_source,
    ]


_TS_HEADERS = [
    "transition_state_entry_ref",
    "status",
    "ts_opt",
    "ts_freq",
    "ts_sp",
    "ts_irc",
    "levels",
]


def _ts_row(ts: dict) -> list[str]:
    calcs = ts.get("calculations") or {}

    def _slot(name: str) -> str:
        slot = calcs.get(name)
        return (slot.get("calculation_ref") or _NOT_RECORDED) if slot else _NOT_RECORDED

    lot = (ts.get("evidence_summary") or {}).get("levels_of_theory") or {}

    def _lvl(name: str) -> str:
        entries = lot.get(name)
        if not entries:
            return _NOT_RECORDED
        return "; ".join(_level_display(e) for e in entries)

    levels = f"opt={_lvl('opt')} freq={_lvl('freq')} sp={_lvl('sp')} irc={_lvl('irc')}"
    return [
        ts.get("transition_state_entry_ref") or _NOT_RECORDED,
        ts.get("status") or _NOT_RECORDED,
        _slot("ts_opt"),
        _slot("ts_freq"),
        _slot("ts_sp"),
        _slot("ts_irc"),
        levels,
    ]


_NETWORK_HEADERS = ["network_ref", "name", "T_range", "P_range", "channels"]


def _network_row(n: dict) -> list[str]:
    tmin, tmax = n.get("solve_temperature_min_k"), n.get("solve_temperature_max_k")
    t_range = _NOT_RECORDED if tmin is None or tmax is None else f"{tmin:g}-{tmax:g} K"
    pmin, pmax = n.get("solve_pressure_min_bar"), n.get("solve_pressure_max_bar")
    p_range = _NOT_RECORDED if pmin is None or pmax is None else f"{pmin:g}-{pmax:g} bar"
    return [
        n.get("network_ref") or _NOT_RECORDED,
        n.get("name") or _NOT_RECORDED,
        t_range,
        p_range,
        _fmt_scalar(n.get("channel_count")),
    ]


def render_reaction_full_table(data: dict) -> str:
    """Human-readable rendering of a ``/reaction-entries/{ref}/full`` envelope.

    A section is rendered only when its key is present in ``data`` at all
    (requested and answered by the server); an empty list prints
    "none deposited", never a bare blank. A key genuinely absent (not
    requested, or dropped by the include-token fallback) means the section
    is skipped entirely — silence, not a claim that nothing exists.
    """
    entry = data.get("reaction_entry") or {}
    lines: list[str] = []

    species = data.get("species")
    if species is not None:
        lines.append(_equation_line(species, bool(entry.get("reversible"))))
    else:
        lines.append(entry.get("equation") or _NOT_RECORDED)
    lines.append(
        f"entry {entry.get('reaction_entry_ref') or _NOT_RECORDED}  "
        f"reaction {entry.get('reaction_ref') or _NOT_RECORDED}  "
        f"family {entry.get('family') or _NOT_RECORDED}"
    )

    kinetics = data.get("kinetics")
    networks = data.get("networks")
    if kinetics is not None:
        lines.append("")
        lines.append("Kinetics:")
        if not kinetics:
            if networks:
                net_ref = networks[0].get("network_ref") or _NOT_RECORDED
                lines.append(
                    "  No rate coefficient deposited on this entry; "
                    "phenomenological k(T,P) for this system is served by "
                    f"network {net_ref}"
                )
            else:
                lines.append(f"  {_NONE_DEPOSITED}")
        else:
            lines.append(
                _indent(_render_table(_KINETICS_HEADERS, [_kinetics_row(k) for k in kinetics]))
            )

    ts_list = data.get("transition_states")
    if ts_list is not None:
        lines.append("")
        lines.append("Transition states:")
        if not ts_list:
            lines.append(f"  {_NONE_DEPOSITED}")
        else:
            lines.append(_indent(_render_table(_TS_HEADERS, [_ts_row(t) for t in ts_list])))

    if networks is not None:
        lines.append("")
        lines.append("Networks:")
        if not networks:
            lines.append(f"  {_NONE_DEPOSITED}")
        else:
            lines.append(
                _indent(_render_table(_NETWORK_HEADERS, [_network_row(n) for n in networks]))
            )

    return "\n".join(lines)


def render_reaction_chooser(data: dict) -> str:
    """Human-readable rendering of a ``search_reactions(reaction_ref=...)`` envelope."""
    records = data.get("records") or []
    if not records:
        return "no reaction entries found"
    plural = "y" if len(records) == 1 else "ies"
    lines = [f"{len(records)} entr{plural} found:"]
    for r in records:
        avail = r.get("availability") or {}
        lines.append(
            f"  {r.get('reaction_entry_ref') or _NOT_RECORDED}  "
            f"kinetics={_fmt_scalar(avail.get('kinetics_count'))}  "
            f"has_transition_state={_fmt_scalar(avail.get('has_transition_state'))}"
        )
    # ``or "?"``, not the default-arg form: a record whose ref key is present
    # but null (server data gap) must not crash the join with a TypeError.
    refs = ", ".join(r.get("reaction_entry_ref") or "?" for r in records)
    lines.append(f"hint: tckdb get reaction <rxe_ref>, one of: {refs}")
    return "\n".join(lines)


def _cmd_get_reaction(args: argparse.Namespace) -> int:
    include = _flatten_include_groups(args.include) if args.include else list(DEFAULT_INCLUDE)
    client = TCKDBClient(base_url=args.base_url, timeout=args.timeout)
    try:
        if args.ref.startswith("rxn_"):
            try:
                data = client.search_reactions(reaction_ref=args.ref)
            except TCKDBConnectionError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_FAILURES
            except TCKDBHTTPError as exc:
                if exc.status_code == 404:
                    print(
                        f"error: reaction {args.ref!r} not found",
                        file=sys.stderr,
                    )
                    return EXIT_NOT_FOUND
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_FAILURES
            if args.json:
                print(json.dumps(data, indent=2))
            else:
                print(render_reaction_chooser(data))
            return EXIT_OK

        try:
            data, dropped = _fetch_reaction_full(client, args.ref, include)
        except TCKDBConnectionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_FAILURES
        except TCKDBHTTPError as exc:
            if exc.status_code == 404:
                print(
                    f"error: reaction entry {args.ref!r} not found",
                    file=sys.stderr,
                )
                return EXIT_NOT_FOUND
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_FAILURES

        if dropped:
            print(
                "note: this API deployment does not support "
                f"include={','.join(sorted(set(dropped)))} yet; retried without it",
                file=sys.stderr,
            )
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(render_reaction_full_table(data))
        return EXIT_OK
    finally:
        client.close()


def main_tckdb(argv: list[str] | None = None) -> int:
    parser = _build_tckdb_parser()
    args = parser.parse_args(argv)

    if args.command == "get" and args.entity == "reaction":
        return _cmd_get_reaction(args)

    parser.error(f"unknown command: {args.command} {getattr(args, 'entity', '')}")
    return EXIT_ARGPARSE  # pragma: no cover - parser.error() exits above.


if __name__ == "__main__":
    sys.exit(main())
