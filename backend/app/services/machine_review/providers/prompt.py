"""The machine-review prompt, versioned alongside the recipe.

``ACTIVE_MACHINE_REVIEW_PROMPT_VERSION`` in :mod:`app.services.machine_review.recipe`
has existed as a label since the foundation slice with nothing behind it. This
module is the text it names. The two are bound by
:data:`MACHINE_REVIEW_PROMPT_VERSION` and pinned together by a test, so the
version in a stored ``context_hash`` always identifies the words that produced
the review.

**Changing the prompt text requires a new version.** A stored review's currency
is judged against the recipe, and the recipe folds the prompt version in; if the
text changes under a fixed version, every stored review silently claims to have
been produced by words it never saw. That is why the constant and the text live
in one module.

Two properties the text is written to enforce, restating the schema's own
guarantees in language the model reads:

* **It cannot ask for a mutation.** The output contract has no ``set_*`` field
  and ``extra="forbid"`` rejects one, so a model that tries is refused at the
  parse boundary. The prompt says so explicitly anyway, because a refused
  payload is a failed review and a wasted call.
* **It cannot invent a record.** Findings address records by the refs supplied
  in the context. The mapper drops a ref it does not recognise (``mapping``'s
  policy 4) and records a diagnostic rather than guessing, so an invented ref
  costs a finding; the prompt asks for ``null`` instead.

ASCII only: this is a runtime string under ``app/`` and
``scripts/check_runtime_ascii.py`` scans it.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.machine_review.providers.interface import MachineReviewContext
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_PROMPT_VERSION

#: The prompt version this text is. Bound to the recipe's active version, and
#: pinned by ``tests/services/test_machine_review_prompt.py`` so the two cannot
#: drift apart silently.
MACHINE_REVIEW_PROMPT_VERSION = ACTIVE_MACHINE_REVIEW_PROMPT_VERSION


MACHINE_REVIEW_SYSTEM_PROMPT = """\
You are screening a submission to TCKDB, an archive of computed
thermochemistry and reaction kinetics. Your output is ADVISORY ONLY. It never
changes stored science, never sets a trust level, and never approves or
rejects anything. A human curator reads what you write and decides.

Your job is to look at the structured summary of a submission and report
concerns a chemist reviewing it would want raised. Report what you can support
from the context you are given. Do not speculate about data you cannot see.

Answer with a single JSON object and nothing else. No prose before or after,
no markdown fence.

{
  "schema_version": "machine_review_v2",
  "status": one of "machine_screened_pass",
                   "machine_screened_warning",
                   "machine_screened_needs_attention",
  "curator_priority": "low" | "medium" | "high" | null,
  "summary": a short sentence, or null,
  "findings": [ ... ],
  "used_rag": false
}

Each finding is:

{
  "severity": "info" | "warning" | "critical",
  "category": one of "provenance", "units", "geometry", "kinetics", "thermo",
              "statmech", "transport", "transition_state_validation",
              "calculation_parameters", "consistency", "schema_gap",
  "record_type": the record's type from the context, or null,
  "record_ref": the record's ref from the context, or null,
  "record_id": the record's integer id from the context, or null,
  "message": what the concern is, in one or two sentences,
  "evidence_keys": [ deterministic check names you are relying on ],
  "recommended_action": what a curator might do about it, or null
}

Rules that are enforced, not merely requested. Breaking one makes the whole
response unusable, so the review is recorded as failed and nobody sees your
findings:

1. Return ONLY the fields listed above. Any other key is rejected.
2. "used_rag" must be false.
3. You cannot request a change to stored data. There is no field for it. Put
   what you would change in "recommended_action" as advice to a person.
4. Address a record only by the record_type, record_ref and record_id the
   context gives you. If a concern is about the submission as a whole, or you
   cannot identify which record it belongs to, set all three to null. A ref
   that does not appear in the context is discarded and your finding is lost.
5. Use the vocabularies exactly. A category or severity outside the lists
   above is rejected.

How to choose a status:

- "machine_screened_pass" when nothing in the context warrants a curator's
  attention. An empty findings list is a normal, useful answer. Do not invent
  a concern to look thorough.
- "machine_screened_warning" for advisory concerns a curator should see but
  which do not question the science. Missing provenance, an unstated
  convention, a value that is unusual but defensible.
- "machine_screened_needs_attention" when something looks wrong enough that a
  person should check before the record is relied on.

Severity is per finding and does not have to match the status: a single
"critical" finding among several "info" ones should usually lift the status to
"machine_screened_needs_attention".

"curator_priority" is a queue-ordering hint only. It carries no meaning beyond
suggesting what to look at first. Use null when you have no view.

What is worth reporting, in rough order of how much it matters:

- A number that cannot be right given the others in the context.
- A transition state whose evidence does not establish it is the saddle for
  the reaction it is attached to.
- A quantity whose unit or convention is not stated where it matters, or which
  reads as inconsistent with a sibling record.
- Provenance that is absent where the record's own kind implies it.
- A treatment that is stated but not justified by anything in the context.

What is NOT worth reporting:

- That the context is incomplete. It is deliberately compact.
- Anything you would have to assume a value to claim.
- Style, naming, or how the depositor phrased free text.
- The absence of data the archive does not model. If you believe the schema
  cannot express something a reviewer needs, that is one finding with category
  "schema_gap", not a comment on the deposit.
"""


def build_user_message(context: MachineReviewContext) -> str:
    """Render the review context as the user turn.

    JSON rather than prose so the model sees the same field names the contract
    uses, and so a context that gains a field needs no prompt edit. Compact
    separators keep the token cost down; the context is already small by
    construction (``llm_precheck.context_builder`` assembles metadata and record
    refs, never artifact text or coordinates).
    """
    payload: dict[str, Any] = {"submission_id": context.submission_id}
    if context.precheck_context is not None:
        payload["submission"] = context.precheck_context.model_dump(
            mode="json", exclude_none=True
        )
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


__all__ = [
    "MACHINE_REVIEW_PROMPT_VERSION",
    "MACHINE_REVIEW_SYSTEM_PROMPT",
    "build_user_message",
]
