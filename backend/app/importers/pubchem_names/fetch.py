"""Look species up in PubChem by InChIKey and build the review file. Standard library only; GET only.

A request carries one InChIKey in the URL path and a plain User-Agent, nothing else.
"""

from __future__ import annotations

import itertools
import json
import re
import socket
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from app.services.species_name_normalisation import name_problem, normalise_name

REVIEW_FORMAT = "tckdb-species-names-review/1"
SPECIES_LIST_FORMAT = "tckdb-species-list/1"

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
COMPOUND_URL = "https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"
USER_AGENT = "tckdb-name-import"
MAX_REQUESTS_PER_SECOND = 4
TIMEOUT_SECONDS = 10
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2.0

_INCHIKEY = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")

NAME_KINDS = (("Title", "common"), ("IUPACName", "systematic"))


def is_inchikey(value: object) -> bool:
    return isinstance(value, str) and _INCHIKEY.fullmatch(value) is not None


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
    except (ValueError, KeyError, TypeError):
        return LookupResult("error", detail="unexpected_response")
    if not isinstance(rows, list) or not rows:
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
    ) -> None:
        self._opener = opener
        self._sleep = sleep
        self._clock = clock
        self._base_url = base_url.rstrip("/")
        self._interval = 1.0 / max_per_second
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
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
        url = f"{self._base_url}/compound/inchikey/{inchikey}/property/Title,IUPACName/JSON"
        for attempt in itertools.count():
            self._throttle()
            self.requests_made += 1
            request = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
            try:
                with self._opener(request, timeout=self._timeout) as response:
                    return parse_property_response(response.status, response.read())
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
        raise AssertionError("unreachable")  # pragma: no cover - itertools.count never ends


def _skip(refs: list[str], inchikey: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"species_refs": refs, "inchi_key": inchikey, "reason": reason, **extra}


def build_review(species: Iterable[dict[str, Any]], lookup: Callable[[str], LookupResult]) -> dict[str, Any]:
    """The review document for ``species`` (each with ``species_ref`` and ``inchi_key``).

    A name is listed once per species; a name the same as the other kind's is listed as the common one.
    """
    by_key: dict[str, list[str]] = defaultdict(list)
    skipped: list[dict[str, Any]] = []
    for item in species:
        ref, key = item.get("species_ref"), item.get("inchi_key")
        if not isinstance(ref, str) or not isinstance(key, str) or not is_inchikey(key):
            skipped.append(_skip([ref] if isinstance(ref, str) else [], str(key), "bad_input_row"))
            continue
        if ref not in by_key[key]:
            by_key[key].append(ref)

    rows: list[dict[str, Any]] = []
    for key, refs in by_key.items():
        if len(refs) > 1:
            skipped.append(
                _skip(refs, key, "inchikey_shared_by_species", detail="PubChem does not tell species apart by spin state")
            )
            continue
        result = lookup(key)
        if result.status == "not_found":
            skipped.append(_skip(refs, key, "no_pubchem_match"))
            continue
        if result.status != "ok":
            skipped.append(_skip(refs, key, "lookup_failed", detail=result.detail))
            continue
        cids = sorted({int(p["CID"]) for p in result.properties if isinstance(p.get("CID"), int)})
        if len(cids) != 1:
            skipped.append(_skip(refs, key, "several_cids" if cids else "no_pubchem_match", cids=cids))
            continue
        cid = cids[0]
        props = result.properties[0]
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
                }
            )
    return {"format": REVIEW_FORMAT, "source": "PubChem", "rows": rows, "skipped": skipped}
