"""repair false zero bac_total corrections targeting a transition state

Task #264. Measured on the live deployment before this revision was
written: 19 ``applied_energy_correction`` rows carry
``application_role='bac_total'``, ``value=0``, zero
``applied_energy_correction_component`` children, and ``note IS NULL``.

* 2 target a ``species_entry`` and are honest: both are monatomic
  (``[H]``, ``[O]``). An atom has no bonds, so a bond-additivity total of
  zero is true and needs no components. This revision does not touch
  them -- the predicate below never matches a species-targeted row.
* 17 target a ``transition_state_entry`` and are false. A
  bond-additivity correction is definitionally a sum over bonds; a
  saddle point carries no bond assignment at all (Arkane's own
  Petersson-BAC routine sums whatever bond dictionary it is handed, and
  ARC supplies none for a TS), and the reactant/product species of the
  same reactions carry real BACs of -3 to -16 kcal/mol. A reader who
  forms a corrected barrier height from the stored numbers gets one
  biased by the reactants' correction alone. ``app/services/energy_correction_resolution.py``'s
  ``assert_bac_total_has_required_components`` (same PR) closes the
  upload-side contract that let this shape in; this revision repairs
  what is already stored.

The predicate, and why it is not a row-id list
-----------------------------------------------
::

    application_role = 'bac_total'
      AND target_transition_state_entry_id IS NOT NULL
      AND value = 0
      AND NOT EXISTS (a matching applied_energy_correction_component)

Matched on shape so this revision is correct on any database this
revision is ever applied to, not merely on the 17 rows measured on the
Pi on 2026-09-16. A row that later gained a component (a genuine BAC
that states the bonds it summed) or carries a nonzero value is a
different claim and is left alone by construction, not by exemption.

The frozen-row refusal
-----------------------
None of the 17 measured rows is frozen (``record_review.first_approved_at
IS NULL`` on every one), so ADR 0003's accepted-science freeze does not
block this repair today. It could, on some other database, and deleting
frozen science must be an explicit human decision -- never a migration's.
``upgrade()`` therefore re-derives the matched set fresh and raises,
rather than skipping silently, if any matched row's ``record_review`` has
``first_approved_at IS NOT NULL``. No such row exists in the fixture this
revision's test seeds either; the test seeds one anyway, specifically to
prove the raise fires.

``record_review`` is deprecated, never deleted
------------------------------------------------
An earlier version of this revision deleted the orphaned
``record_review``/``record_review_event`` rows too, via a transactional
``ALTER TABLE ... DISABLE TRIGGER`` around the two guards
(``trg_guard_record_review``, ``trg_append_only_record_review_event``)
that ``c6f2a9d4e7b1`` installed specifically to refuse *every* ``DELETE``
on those tables, unconditionally. ``docs/specs/accepted_science_immutability.md``
says outright that even the audited repair-declaration mechanism added
after that revision "stay[s] unconditionally refused, declaration or
not" for DELETE, and that "record_review is outside this mechanism
entirely; approval history is not repairable." A migration that turns
that guard off to get past it is not a repair; it is the guard not
working, on the one table whose entire job is recording that a guard
was never worked around. That version is withdrawn. This one never
issues ``ALTER TABLE ... DISABLE TRIGGER`` and never runs ``DELETE`` on
either table.

The dangling-pointer problem (task #262: a review row whose target is
gone still shows up as work) is real and is solved instead by using the
mechanism the review system already has for exactly this: a status
transition. After the 17 ``applied_energy_correction`` rows are deleted,
each surviving ``record_review`` row for one of them is moved to
``deprecated`` -- an allowed transition from every non-terminal or
``rejected`` status a genuinely unfrozen row can be in (see
``app.services.record_review._ALLOWED_TRANSITIONS``; ``under_review``
does not transition to ``deprecated`` directly, so a row found in that
state is first moved to ``not_reviewed``, exactly as a curator reopening
it would be) -- and a ``record_review_event`` is appended recording the
transition, mirroring exactly what
``app.services.record_review.set_record_review_status`` writes for a
real curator action. The history grows; nothing is deleted, updated in
place past its append-only guard, or hidden. Since ``2026-09-16``
(``994f504e``, #488) a review row whose record no longer resolves
renders as "this record cannot be named" rather than erroring, so a
``deprecated`` row pointing at a since-deleted correction is handled
and honest on the read side too, and the review queue's default filter
(open work) already excludes ``deprecated`` -- it stops being offered as
work without being erased.

The transition is attributed to a dedicated, idempotently-provisioned
``app_user`` row (``username='system-migration-actor'``) rather than
left actor-less, because ``record_review``'s own
``record_review_terminal_requires_reviewer`` constraint requires
``reviewed_by``/``reviewed_at`` on every terminal status including
``deprecated`` -- the same requirement a real curator's transition
satisfies with their own id.

What this revision does
------------------------
1. Refuses to run if any matched row is frozen (above).
2. Deletes the matched ``applied_energy_correction`` rows. No
   ``applied_energy_correction_component`` rows exist for them (the
   predicate requires it), so nothing else needs deleting first.
3. Moves each deleted row's ``record_review`` (if any) to ``deprecated``
   -- via ``not_reviewed`` first when it was ``under_review`` -- and
   appends the corresponding ``record_review_event``(s). Never touches
   ``record_review_event`` rows that already exist.

Downgrade cannot restore deleted rows
--------------------------------------
``downgrade()`` is a deliberate no-op. There is no column, index or
constraint for it to reverse -- this is a pure data repair -- and the
17 (or however many a given database has) ``applied_energy_correction``
rows this revision deletes are gone: their original ``id``,
``created_at`` and ``created_by`` are not reconstructed by anything
written here, and their ``record_review`` rows stay ``deprecated``
forever, which is the honest record of what happened. A downgrade that
re-inserted rows with new ids and no history would be a different,
false claim about what those rows were, which is worse than admitting
the direction does not exist.

Revision ID: a55cc983501a
Revises: e3a7c1f9b2d4
Create Date: 2026-09-16 00:00:00.000000

"""

from typing import Sequence, Union

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a55cc983501a"
down_revision: Union[str, None] = "e3a7c1f9b2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Every row matching the false-TS-zero-BAC shape, by structure rather
#: than by id or count. See the module docstring's "predicate" section.
_MATCH_QUERY = text(
    """
    SELECT aec.id
      FROM applied_energy_correction AS aec
     WHERE aec.application_role = 'bac_total'
       AND aec.target_transition_state_entry_id IS NOT NULL
       AND aec.value = 0
       AND NOT EXISTS (
           SELECT 1
             FROM applied_energy_correction_component AS comp
            WHERE comp.applied_correction_id = aec.id
       )
    """
)

_FROZEN_QUERY = text(
    """
    SELECT rr.record_id
      FROM record_review AS rr
     WHERE rr.record_type = 'applied_energy_correction'
       AND rr.record_id = ANY(:ids)
       AND rr.first_approved_at IS NOT NULL
    """
)

#: Reason text stamped on the ``record_review_event`` rows this revision
#: appends. Never a database row id (DR-0028): the migration id and task
#: number are the only identifiers a reader needs to find the rest of
#: this story.
_DEPRECATION_REASON = (
    "Repaired by migration a55cc983501a (task 264): the applied_energy_correction "
    "this review was about was a componentless bac_total of zero targeting a "
    "transition state, which cannot be a real bond additivity correction, and "
    "has been deleted. Deprecated rather than left pointing at a deleted record."
)


def _refuse_if_any_frozen(bind, matched_ids: list[int]) -> None:
    """Abort rather than delete science a human has already approved.

    Re-derives frozen status directly from ``record_review`` at upgrade
    time -- never assumed from this revision's own measurement of the
    live deployment -- so this raises correctly on any database, not
    only the one it was written against.
    """
    if not matched_ids:
        return
    frozen_ids = list(bind.execute(_FROZEN_QUERY, {"ids": matched_ids}).scalars())
    if frozen_ids:
        raise RuntimeError(
            "Cannot upgrade to a55cc983501a: applied_energy_correction "
            f"row(s) {sorted(frozen_ids)} match the false-TS-zero-BAC "
            "shape this revision repairs, but each has a record_review "
            "with first_approved_at IS NOT NULL, meaning a human has "
            "already approved it. Deleting frozen science is an explicit human "
            "decision, never a migration's. Resolve these rows manually "
            "(curator review, then a deliberate follow-up) before "
            "re-running this upgrade."
        )


def _system_actor_id(bind) -> int:
    """The ``app_user`` id this revision's ``record_review`` transitions
    are attributed to.

    Idempotent (``ON CONFLICT ... DO UPDATE`` so it always ``RETURNING``s
    a row rather than raising on a re-run), because a review transition
    is a terminal status and ``record_review_terminal_requires_reviewer``
    demands a real ``reviewed_by``/``reviewed_at`` on it -- the same
    requirement a real curator's own transition satisfies with their id.
    """
    return bind.execute(
        text(
            """
            INSERT INTO app_user (username, role)
            VALUES ('system-migration-actor', 'admin')
            ON CONFLICT (username) DO UPDATE SET username = EXCLUDED.username
            RETURNING id
            """
        )
    ).scalar_one()


def _deprecate_record_reviews(bind, matched_ids: list[int], actor_id: int) -> None:
    """Move each matched correction's ``record_review`` to ``deprecated``.

    Mirrors ``app.services.record_review.set_record_review_status``
    field-for-field (status, ``reviewed_by``, ``reviewed_at``, and the
    appended event), because that -- not a reimplementation, and not a
    way around it -- is "the normal status-change path". Two statements
    because the transition table
    (``app.services.record_review._ALLOWED_TRANSITIONS``) does not allow
    ``under_review -> deprecated`` directly: a row found there is routed
    through ``not_reviewed`` first, exactly as a curator reopening it
    would be routed. A row already ``deprecated`` is left alone (a
    same-status transition is a no-op that emits nothing, matching the
    service).
    """
    if not matched_ids:
        return

    bind.execute(
        text(
            """
            WITH moved AS (
                UPDATE record_review
                   SET status = 'not_reviewed', reviewed_by = NULL, reviewed_at = NULL
                 WHERE record_type = 'applied_energy_correction'
                   AND record_id = ANY(:ids)
                   AND status = 'under_review'
                RETURNING id
            )
            INSERT INTO record_review_event
                (record_review_id, event_kind, from_status, to_status,
                 actor_user_id, reason)
            SELECT moved.id, 'status_change', 'under_review', 'not_reviewed',
                   :actor_id, :reason
              FROM moved
            """
        ),
        {"ids": matched_ids, "actor_id": actor_id, "reason": _DEPRECATION_REASON},
    )

    bind.execute(
        text(
            """
            WITH prior AS (
                SELECT id, status
                  FROM record_review
                 WHERE record_type = 'applied_energy_correction'
                   AND record_id = ANY(:ids)
                   AND status <> 'deprecated'
            ),
            moved AS (
                UPDATE record_review
                   SET status = 'deprecated', reviewed_by = :actor_id,
                       reviewed_at = now()
                  FROM prior
                 WHERE record_review.id = prior.id
                RETURNING record_review.id AS review_id, prior.status AS from_status
            )
            INSERT INTO record_review_event
                (record_review_id, event_kind, from_status, to_status,
                 actor_user_id, reason)
            SELECT moved.review_id, 'status_change', moved.from_status,
                   'deprecated', :actor_id, :reason
              FROM moved
            """
        ),
        {"ids": matched_ids, "actor_id": actor_id, "reason": _DEPRECATION_REASON},
    )


def upgrade() -> None:
    bind = op.get_bind()

    matched_ids = list(bind.execute(_MATCH_QUERY).scalars())
    _refuse_if_any_frozen(bind, matched_ids)
    if not matched_ids:
        return

    # The applied-correction root's own guard
    # (trg_as_root_applied_energy_correction) permits this unconditionally
    # for an unfrozen row -- already proven above. No component rows exist
    # for any matched id (the predicate requires it), so there is nothing
    # else to delete first.
    bind.execute(
        text("DELETE FROM applied_energy_correction WHERE id = ANY(:ids)"),
        {"ids": matched_ids},
    )

    actor_id = _system_actor_id(bind)
    _deprecate_record_reviews(bind, matched_ids, actor_id)


def downgrade() -> None:
    """No-op: deleted rows are gone, not un-deleted, and a deprecated
    review row stays deprecated. See the module docstring's "Downgrade
    cannot restore deleted rows" section."""
