"""Look species up in PubChem by InChIKey and build the review file. Standard library only; GET only.

A request carries one InChIKey in the URL path and a plain User-Agent, nothing else.
"""

from __future__ import annotations

import http.client
import itertools
import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, TypeGuard

from app.services.species_name_normalisation import name_problem, normalise_name
from app.services.species_name_review import REVIEW_FORMAT, is_inchikey

SPECIES_LIST_FORMAT = "tckdb-species-list/1"

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PROPERTIES = "Title,IUPACName,Charge,CovalentUnitCount"
COMPOUND_URL = "https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
USER_AGENT = "tckdb-name-import"
MAX_REQUESTS_PER_SECOND = 4
TIMEOUT_SECONDS = 10
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2.0
MAX_RESPONSE_BYTES = 1_000_000
CHECKPOINT_EVERY = 25

NAME_KINDS = (("Title", "common"), ("IUPACName", "systematic"))

__all__ = [
    "REVIEW_FORMAT",
    "SPECIES_LIST_FORMAT",
    "LookupResult",
    "PubChemClient",
    "build_review",
    "is_inchikey",
    "parse_property_response",
]


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass
class LookupResult:
    """``status`` is ``ok``, ``not_found`` or ``error``; ``properties`` are PubChem's property rows."""

    status: str
    properties: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""


def parse_property_response(status_code: int, body: bytes | str) -> LookupResult:
    """Interpret one PUG REST property response."""
    if status_code == 404:
        return LookupResult("not_found")
    if status_code != 200:
        return LookupResult("error", detail=f"http_{status_code}")
    try:
        payload = json.loads(body)
        rows = payload["PropertyTable"]["Properties"]
    except (ValueError, KeyError, TypeError, RecursionError):
        return LookupResult("error", detail="unexpected_response")
    if not isinstance(rows, list):
        return LookupResult("error", detail="unexpected_response")
    if not rows:
        return LookupResult("not_found")
    return LookupResult("ok", [row for row in rows if isinstance(row, dict)])


class PubChemClient:
    """One InChIKey per call, at most ``MAX_REQUESTS_PER_SECOND`` requests a second, a bounded retry on 503."""

    def __init__(
        self,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        base_url: str = PUBCHEM_BASE,
        max_per_second: float = MAX_REQUESTS_PER_SECOND,
        timeout: float = TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        backoff: float = RETRY_BACKOFF_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._opener = opener
        self._sleep = sleep
        self._clock = clock
        self._base_url = base_url.rstrip("/")
        self._interval = 1.0 / max_per_second
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        self._max_bytes = max_bytes
        self._last_start: float | None = None
        self.requests_made = 0

    def _throttle(self) -> None:
        if self._last_start is not None:
            wait = self._interval - (self._clock() - self._last_start)
            if wait > 0:
                self._sleep(wait)
        self._last_start = self._clock()

    def lookup(self, inchikey: str) -> LookupResult:
        if not is_inchikey(inchikey):
            return LookupResult("error", detail="not_an_inchikey")
        url = f"{self._base_url}/compound/inchikey/{inchikey}/property/{PROPERTIES}/JSON"
        for attempt in itertools.count():
            self._throttle()
            self.requests_made += 1
            request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
            try:
                with self._opener(request, timeout=self._timeout) as response:
                    body = response.read(self._max_bytes + 1)
                    if len(body) > self._max_bytes:
                        return LookupResult("error", detail="response_too_large")
                    return parse_property_response(response.status, body)
            except urllib.error.HTTPError as exc:
                if exc.code == 503 and attempt < self._max_retries:
                    self._sleep(self._backoff * (attempt + 1))
                    continue
                if exc.code == 404:
                    return LookupResult("not_found")
                return LookupResult("error", detail=f"http_{exc.code}")
            except (TimeoutError, socket.timeout):
                return LookupResult("error", detail="timeout")
            except urllib.error.URLError as exc:
                return LookupResult("error", detail="timeout" if isinstance(exc.reason, socket.timeout) else "network")
            except (OSError, http.client.HTTPException, RecursionError):
                return LookupResult("error", detail="network")
        raise AssertionError("unreachable")  # pragma: no cover - itertools.count never ends


def write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    """Write ``document`` so a reader (or a crash) sees the old file or the new one, never half of it."""
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def fetch_to_file(
    species: Iterable[dict[str, Any]],
    out: Path,
    client: PubChemClient,
    *,
    earlier_skipped: list[dict[str, Any]] | None = None,
    checkpoint_every: int = CHECKPOINT_EVERY,
) -> dict[str, Any]:
    """``build_review`` with the review file rewritten every ``checkpoint_every`` InChIKeys and at the end."""
    earlier = list(earlier_skipped or [])

    def save(document: dict[str, Any]) -> None:
        write_json_atomic(out, {**document, "skipped": earlier + document["skipped"]})

    review = build_review(species, client.lookup, checkpoint=save, checkpoint_every=checkpoint_every)
    save(review)
    return {**review, "skipped": earlier + review["skipped"]}


def _skip(refs: list[str], inchikey: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"species_refs": refs, "inchi_key": inchikey, "reason": reason, **extra}


def _document(rows: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> dict[str, Any]:
    return {"format": REVIEW_FORMAT, "source": "PubChem", "rows": list(rows), "skipped": list(skipped)}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def build_review(
    species: Iterable[dict[str, Any]],
    lookup: Callable[[str], LookupResult],
    *,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
    checkpoint_every: int = CHECKPOINT_EVERY,
    now: Callable[[], str] = _now,
) -> dict[str, Any]:
    """The review document for ``species``.

    Each item has ``species_ref``, ``inchi_key`` and ``charge``; ``smiles`` is optional (a species whose SMILES has
    several components is skipped). PubChem often lists several compounds under one InChIKey; only a compound with a
    single covalent unit and the species' charge is a candidate, and exactly one must remain. A name is listed once
    per species; a name the same as the other kind's is listed as the common one. ``checkpoint`` receives the
    document so far after every ``checkpoint_every`` InChIKeys.
    """
    by_key: dict[str, list[str]] = defaultdict(list)
    charges: dict[str, int] = {}
    skipped: list[dict[str, Any]] = []
    for item in species:
        ref, key, charge, smiles = (item.get(k) for k in ("species_ref", "inchi_key", "charge", "smiles"))
        if not isinstance(ref, str) or not isinstance(key, str) or not is_inchikey(key) or not _is_int(charge):
            skipped.append(_skip([ref] if isinstance(ref, str) else [], str(key), "bad_input_row"))
            continue
        if isinstance(smiles, str) and "." in smiles:
            skipped.append(_skip([ref], key, "smiles_multi_component"))
            continue
        if ref not in by_key[key]:
            by_key[key].append(ref)
        charges.setdefault(key, int(charge))

    rows: list[dict[str, Any]] = []
    for done, (key, refs) in enumerate(by_key.items(), start=1):
        _review_key(key, refs, charges[key], lookup, rows, skipped, now)
        if checkpoint is not None and done % checkpoint_every == 0:
            checkpoint(_document(rows, skipped))
    return _document(rows, skipped)


def _review_key(
    key: str,
    refs: list[str],
    charge: int,
    lookup: Callable[[str], LookupResult],
    rows: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    now: Callable[[], str],
) -> None:
    if len(refs) > 1:
        skipped.append(
            _skip(refs, key, "inchikey_shared_by_species", detail="PubChem does not tell species apart by spin state")
        )
        return
    result = lookup(key)
    if result.status == "not_found":
        skipped.append(_skip(refs, key, "no_pubchem_match"))
        return
    if result.status != "ok":
        skipped.append(_skip(refs, key, "lookup_failed", detail=result.detail))
        return
    candidates: dict[int, dict[str, Any]] = {}
    for props in result.properties:
        cid = props.get("CID")
        if (
            _is_int(cid)
            and props.get("CovalentUnitCount") == 1
            and _is_int(props.get("CovalentUnitCount"))
            and props.get("Charge") == charge
            and _is_int(props.get("Charge"))
        ):
            candidates.setdefault(cid, props)
    if len(candidates) != 1:
        all_cids = sorted({p["CID"] for p in result.properties if _is_int(p.get("CID"))})
        reason = "several_cids" if candidates else "no_matching_cid"
        skipped.append(_skip(refs, key, reason, cids=sorted(candidates) if candidates else all_cids))
        return
    ((cid, props),) = candidates.items()
    seen: set[str] = set()
    for field_name, kind in NAME_KINDS:
        name = props.get(field_name)
        if not isinstance(name, str) or not name.strip():
            skipped.append(_skip(refs, key, "name_missing", cid=cid, kind=kind))
            continue
        problem = name_problem(name)
        if problem is not None:
            skipped.append(_skip(refs, key, problem, cid=cid, kind=kind, name=name[:200]))
            continue
        name_key = normalise_name(name)
        if name_key in seen:
            continue
        seen.add(name_key)
        rows.append(
            {
                "species_ref": refs[0],
                "inchi_key": key,
                "name": name,
                "kind": kind,
                "cid": cid,
                "url": COMPOUND_URL.format(cid=cid),
                "retrieved_at": now(),
            }
        )
