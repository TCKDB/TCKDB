"""Stage 5: upload built payloads via the generic ``tckdb-client`` (M4).

This is the only stage that touches the network. ``tckdb-client`` is imported
lazily so that stages 1-4 (and their tests) never require it. Every logical
record gets a deterministic idempotency key derived from
``(mechanism_id, record_kind, canonical_key)`` (spec §8) so a re-import does
not duplicate result rows.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .payloads import BuiltPayloads


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._:-]", "-", text)


def _reaction_key(payload: dict) -> str:
    reactants = "+".join(
        p["species_entry"]["smiles"] for p in payload["reaction"]["reactants"]
    )
    products = "+".join(
        p["species_entry"]["smiles"] for p in payload["reaction"]["products"]
    )
    arrow = "=" if payload["reaction"].get("reversible") else ">"
    return f"{reactants}{arrow}{products}:{payload.get('model_kind', '')}"


def _species_key_parts(kind: str, entry: dict) -> tuple:
    """Key parts naming a species by structure and spin: SMILES, multiplicity, and charge when not zero."""
    parts = [kind, _slug(entry["smiles"]), f"m{entry['multiplicity']}"]
    if entry.get("charge"):
        parts.append(f"q{entry['charge']}")
    return tuple(parts)


@dataclass
class RecordResult:
    kind: str
    key: str
    ok: bool
    replayed: bool = False
    error: str | None = None


@dataclass
class UploadReport:
    """Machine-readable per-record import summary (spec §10)."""

    results: list[RecordResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, result: RecordResult) -> None:
        self.results.append(result)

    def summary(self) -> dict:
        by_kind: dict[str, dict[str, int]] = {}
        for r in self.results:
            bucket = by_kind.setdefault(
                r.kind, {"ok": 0, "replayed": 0, "errored": 0}
            )
            if not r.ok:
                bucket["errored"] += 1
            else:
                bucket["ok"] += 1
                if r.replayed:
                    bucket["replayed"] += 1
        return {
            "by_kind": by_kind,
            "errored_total": sum(1 for r in self.results if not r.ok),
            "warnings": len(self.warnings),
        }


def upload_payloads(
    payloads: BuiltPayloads,
    client,
    mechanism_id: str,
    *,
    dry_run: bool = False,
) -> UploadReport:
    """Upload thermo, transport, then kinetics via ``client.upload(...)``.

    Species-carrying records (thermo/transport) go first so that reaction
    uploads find their species already resolved (spec §9). Per-record errors
    are collected and reported rather than aborting the whole mechanism —
    identity has already been validated all-or-nothing upstream.

    :param client: An object exposing ``request_json(method, path, json=,
        idempotency_key=)`` and/or ``upload(kind, payload, idempotency_key=)``
        (the real ``TCKDBClient``, or a stub in tests).
    :param mechanism_id: Stable identifier for this mechanism (idempotency).
    :param dry_run: When True, build keys and record intent without sending.
    """
    from tckdb_client import make_idempotency_key  # lazy: M1-M3 stay client-free

    report = UploadReport(warnings=list(payloads.warnings))

    def _send(kind: str, endpoint: str, payload: dict, key_parts: list[str]) -> None:
        key = make_idempotency_key(mechanism_id, *key_parts)
        if dry_run:
            report.add(RecordResult(kind=kind, key=key, ok=True))
            return
        try:
            resp = client.request_json(
                "POST", endpoint, json=payload, idempotency_key=key
            )
            replayed = bool(getattr(resp, "idempotency_replayed", False))
            report.add(
                RecordResult(kind=kind, key=key, ok=True, replayed=replayed)
            )
        except Exception as exc:  # noqa: BLE001 - collect and continue
            report.add(
                RecordResult(kind=kind, key=key, ok=False, error=str(exc))
            )

    # A species record is keyed by its structure and spin (SMILES, multiplicity, and charge when it is not zero),
    # so the key does not depend on where the species sits in the file: singlet and triplet CH2 get two keys.
    # If two species of one mechanism still share all of that, they are keyed by their CHEMKIN label instead, and
    # the import report says so.
    for kind, endpoint, items, names in (
        ("thermo", "/uploads/thermo", payloads.thermo, getattr(payloads, "thermo_names", [])),
        ("transport", "/uploads/transport", payloads.transport, getattr(payloads, "transport_names", [])),
    ):
        base_keys = [_species_key_parts(kind, payload["species_entry"]) for payload in items]
        shared = {key for key, n in Counter(base_keys).items() if n > 1}
        for index, (payload, base) in enumerate(zip(items, base_keys)):
            parts = list(base)
            if base in shared:
                if index >= len(names):
                    raise ValueError(
                        f"two {kind} records share structure and spin {base[1:]} and the CHEMKIN labels needed to "
                        "tell them apart were not supplied"
                    )
                parts.append(_slug(names[index]))
                report.warnings.append(
                    f"Species sharing SMILES and multiplicity {base[1:]} are keyed by their CHEMKIN label "
                    f"({names[index]}) for {kind}."
                )
            _send(kind, endpoint, payload, parts)

    seen: dict[str, int] = {}
    for payload in payloads.kinetics:
        rkey = _reaction_key(payload)
        n = seen.get(rkey, 0)
        seen[rkey] = n + 1
        # Distinguish DUP result rows with a stable ordinal suffix.
        parts = ["kinetics", _slug(rkey), str(n)]
        _send("kinetics", "/uploads/kinetics", payload, parts)

    return report
