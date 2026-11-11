"""The species list the fetch starts from: read from a TCKDB instance's public species browse (GET only)."""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

from app.importers.pubchem_names.fetch import MAX_REQUESTS_PER_SECOND, SPECIES_LIST_FORMAT, TIMEOUT_SECONDS, USER_AGENT

PAGE_SIZE = 200
BROWSE_PATH = "/api/v1/scientific/species/browse"


def fetch_species_list(
    base_url: str,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Page through ``base_url``'s species browse and return the species list document."""
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("base URL must start with http:// or https://")
    root = f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"
    species: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode({"limit": PAGE_SIZE, "offset": offset})
        request = urllib.request.Request(
            f"{root}{BROWSE_PATH}?{query}", method="GET", headers={"User-Agent": USER_AGENT}
        )
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
        records = payload.get("records", [])
        for record in records:
            species.append(
                {
                    "species_ref": record["species_ref"],
                    "inchi_key": record["inchi_key"],
                    "formula": record.get("formula"),
                    "charge": record["charge"],
                    "multiplicity": record["multiplicity"],
                }
            )
        offset += len(records)
        if not records or offset >= payload.get("pagination", {}).get("total", 0):
            break
        sleep(1.0 / MAX_REQUESTS_PER_SECOND)
    return {"format": SPECIES_LIST_FORMAT, "species": species}
