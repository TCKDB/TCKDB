"""Query and body parameters that no longer exist, refused by name.

FastAPI ignores a query parameter a route does not declare, and a Pydantic request model ignores a body field it
does not declare. For a parameter that *used to* do something that is the wrong failure: ``?collapse=first`` would
keep working as a request and quietly return every record, and ``?temperature_min=500`` would return a response
whose coverage fields no longer reflect it. A request that names a removed parameter therefore gets a 422 naming
what to use instead.

Two kinds:

* **Removed** (``browse_parameter_removed``). A browse read lists every matching record in display order and never
  picks one (``docs/specs/browse_order.md``). ``collapse`` and ``selection_policy`` asked it to pick. "Give me one"
  is the select endpoints' job, so the refusal says which one.
* **Renamed** (``parameter_renamed``). The parameter stays but its name described something it never did. The
  refusal names the new spelling.

The same table guards a GET route (``reject_removed_query_params``, which looks at the raw query string) and a POST
body (``RemovedParameters.guard_body``, a Pydantic ``mode="before"`` validator), so the two entry points cannot
disagree about what is gone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from tckdb_schemas.coded_error import CodedValidationError


#: The parameters a browse read used to take in order to return one record instead of the list.
PICKING_PARAMETERS: tuple[str, ...] = ("collapse", "selection_policy")

API_PREFIX = "/api/v1/scientific"
THERMO_SELECT = f"POST {API_PREFIX}/species-entries/{{species_entry_ref}}/thermo/select"
KINETICS_SELECT = f"POST {API_PREFIX}/reaction-entries/{{reaction_entry_ref}}/kinetics/select"
STATMECH_SELECT = f"POST {API_PREFIX}/statmech/select"
CALCULATION_SELECT = f"POST {API_PREFIX}/species-entries/{{species_entry_ref}}/calculations/select"


@dataclass(frozen=True)
class RemovedParameters:
    """What one read no longer accepts.

    :param select_endpoint: The select endpoint that answers "give me one" for this record kind, or ``None`` when
        there is none (the refusal then says so rather than pointing nowhere).
    :param renamed: Old parameter name to new parameter name.
    """

    select_endpoint: str | None = None
    renamed: Mapping[str, str] = field(default_factory=dict)
    picking: bool = True
    #: Overrides the sentence that says where to go instead (an export has no select endpoint to point at).
    pointer: str | None = None

    def check(self, names: Iterable[str]) -> None:
        """Raise the coded 422 when ``names`` contains a removed or renamed parameter."""
        present = set(names)
        for old, new in self.renamed.items():
            if old in present:
                raise CodedValidationError(
                    "parameter_renamed",
                    f"{old} was renamed {new}. It never filtered the records; it only sets the window the "
                    f"temperature_coverage field of each record is computed against. Use {new}.",
                    context={"parameter": old, "replacement": new},
                )
        if not self.picking:
            return
        for name in PICKING_PARAMETERS:
            if name in present:
                where = self.pointer or (
                    f"To get one record under a policy you state, use {self.select_endpoint}."
                    if self.select_endpoint
                    else "This record kind has no select endpoint yet; filter the list instead."
                )
                raise CodedValidationError(
                    "browse_parameter_removed",
                    f"{name} is not accepted: TCKDB never picks one record for you on a read or an export; it "
                    f"returns every matching record in display order. {where}",
                    context={"parameter": name, "select_endpoint": self.select_endpoint},
                )

    def guard_body(self, data: Any) -> Any:
        """``mode="before"`` validator body: refuse a removed field in a JSON body, pass everything else through."""
        if isinstance(data, Mapping):
            self.check(data.keys())
        return data


def reject_removed_query_params(request: Any, spec: RemovedParameters) -> None:
    """Refuse a removed parameter on a GET route. ``request`` is the Starlette ``Request``."""
    spec.check(request.query_params.keys())


THERMO = RemovedParameters(
    select_endpoint=THERMO_SELECT,
    renamed={
        "temperature_min": "coverage_temperature_min_k",
        "temperature_max": "coverage_temperature_max_k",
    },
)
KINETICS = RemovedParameters(
    select_endpoint=KINETICS_SELECT,
    renamed={
        "temperature_min": "coverage_temperature_min_k",
        "temperature_max": "coverage_temperature_max_k",
    },
)
STATMECH = RemovedParameters(select_endpoint=STATMECH_SELECT)
TRANSPORT = RemovedParameters(select_endpoint=None)
SPECIES = RemovedParameters(select_endpoint=None)
REACTIONS = RemovedParameters(select_endpoint=None)
SPECIES_CALCULATIONS = RemovedParameters(select_endpoint=CALCULATION_SELECT)
