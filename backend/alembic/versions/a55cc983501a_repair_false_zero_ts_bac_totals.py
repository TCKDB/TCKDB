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
Pi on 2026-09-16. A row with a component (a genuine BAC that states the
bonds it summed) is a different claim and is left alone by construction,
not by exemption.

**``value = 0`` is deliberately narrow, and says a different thing than
the upload contract.** ``assert_bac_total_has_required_components``
(same PR) refuses a componentless ``bac_petersson`` ``bac_total``
targeting a transition state at *any* value -- there being no bond
assignment for a saddle point does not become more or less provable at
a different number. This revision repairs only the shape actually
measured: 19 rows, all at exactly zero. Widening the predicate to match
a nonzero componentless row is not the same claim -- there is no
measurement behind it, and "match on shape, not a count" means matching
the shape that was found, not inferring a broader one nobody has
observed. A nonzero componentless TS-side row, if one is ever found on
some other database, is a real instance of the false shape the contract
now refuses on deposit; repairing it is a deliberate, separate decision
this revision does not make.
``tests/db/test_bac_total_false_zero_repair_migration.py`` seeds exactly
such a row and asserts it survives, so this narrowing is pinned rather
than incidental.

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

Other tables that can point at an ``applied_energy_correction`` by
``(record_type, record_id)``
------------------------------------------------------------------------
Five more polymorphic tables were checked for the same dangling-pointer
question ``record_review`` raised, since none of them are exercised by
this revision's own tests:

* **``submission_record_link``** -- no FK, no CHECK on ``record_type``,
  and (confirmed against ``c6f2a9d4e7b1``'s trigger set) **no DELETE
  guard at all**. This is the one that actually breaks something: it is
  what ``approve_submission``/``reject_submission``
  (``app/services/submission.py``) iterate to decide which records to
  transition, not ``record_review``. Left dangling, the pending
  submission that deposited a deleted correction can be neither
  approved (``lock_scientific_records`` does ``SELECT ... FOR UPDATE``
  on the gone row and raises ``DomainError``) nor rejected (the review
  is already ``deprecated``, and ``deprecated -> rejected`` is a
  disallowed transition) -- a curator is stuck with no way forward, on
  every one of the 17 rows' parent submissions on the Pi, none of which
  are approved yet. This revision deletes the matching link rows: the
  table's own module docstring is explicit that "the real foreign keys
  for the scientific records live on their own tables" and it carries
  no ownership/append-only contract of its own to violate.
* **``record_machine_review``** -- can point at one (the enum column
  carries every ``submission_record_type`` value and nothing narrows
  it), and has no DELETE guard either, but is append-only *by design*
  (its own module docstring: "a re-review inserts a new row; rows are
  never updated in place") and private -- never rendered on any public
  or curator-facing surface, and never consulted by
  ``approve_submission``/``reject_submission``. Deleting a historical
  machine-review pass to tidy up a pointer it never uses would be the
  same mistake this revision's own record_review rewrite exists to
  stop making, with no DB trigger there to have caught it. Left alone,
  on purpose.
* **``machine_review_curator_task``** -- can also point at one (same
  unnarrowed enum), no DELETE guard, but its own module docstring
  states the non-interference policy plainly: "a task create/assign/
  resolve writes **only** to this table" -- resolving a task never
  loads, locks, or otherwise depends on the record it names, so a
  dangling one does not block anything the way ``submission_record_link``
  does. Left alone; a human resolving it sees a task about a record
  that is gone, which is a fact worth surfacing, not an error to
  suppress.
* **``record_reproducibility_assessment``** -- can point at one (same
  unnarrowed enum), but is DELETE-guarded
  (``trg_repro_assessment_append_only``, same origin migration): this
  revision could not delete a referencing row if it wanted to, and
  measured has none for any of the 17. ``_refuse_if_referenced_elsewhere``
  checks this at upgrade time and aborts rather than deleting a
  correction out from under an assessment this revision cannot repair.
* **``scientific_record_supersession``** -- can point at one (its own
  ``supported_type`` CHECK lists it explicitly) and is DELETE-guarded
  (``trg_as_append_only_scientific_record_supersession``, same
  mechanism as ``record_review_event``). Same treatment: checked and
  refused on, never silently orphaned.
* **``dataset_release``** carries no ``(record_type, record_id)`` pair
  at all -- not polymorphic, cannot reference this or anything else by
  id. ``release_selection``, the table that actually links a release to
  member records, has its own CHECK constraint
  (``selectable_record_type``) that excludes
  ``applied_energy_correction`` by name: a release recommends
  scientific product values, not applied corrections. Neither is
  reachable from this bug.

What this revision does
------------------------
1. Refuses to run if any matched row is frozen (above), or if any
   matched row is referenced from a table this revision cannot repair
   (``record_reproducibility_assessment``, ``scientific_record_supersession``;
   see above).
2. Deletes the matched ``applied_energy_correction`` rows. No
   ``applied_energy_correction_component`` rows exist for them (the
   predicate requires it), so nothing else needs deleting first.
3. Deletes the matched ``submission_record_link`` rows (above) so a
   pending submission that deposited a deleted correction can still be
   approved or rejected.
4. Moves each deleted row's ``record_review`` (if any) to ``deprecated``
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

#: The two polymorphic tables that can reference an
#: ``applied_energy_correction`` and refuse every DELETE on themselves
#: unconditionally (``record_reproducibility_assessment``,
#: ``scientific_record_supersession`` -- see the module docstring's
#: "Other tables" section). This revision cannot repair a reference from
#: either, so it checks for one before deleting anything, the same way
#: it checks for a frozen ``record_review``.
_UNREPAIRABLE_REFERENCE_QUERY = text(
    """
    SELECT 'record_reproducibility_assessment' AS table_name, record_id
      FROM record_reproducibility_assessment
     WHERE record_type = 'applied_energy_correction'
       AND record_id = ANY(:ids)
    UNION ALL
    SELECT 'scientific_record_supersession' AS table_name, superseded_record_id AS record_id
      FROM scientific_record_supersession
     WHERE record_type = 'applied_energy_correction'
       AND superseded_record_id = ANY(:ids)
    UNION ALL
    SELECT 'scientific_record_supersession' AS table_name, superseding_record_id AS record_id
      FROM scientific_record_supersession
     WHERE record_type = 'applied_energy_correction'
       AND superseding_record_id = ANY(:ids)
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
    """Abort with a legible, complete explanation before touching anything.

    This is not what keeps a frozen row from being deleted --
    ``trg_as_root_applied_energy_correction`` (``c6f2a9d4e7b1``) already
    refuses that DELETE unconditionally at the database level and rolls
    the whole transaction back on its own; removing this function does
    not silently delete a frozen row (measured: the mutation-checked
    test for this function catches the removal only because it asserts
    the specific ``RuntimeError`` this raises, and a raw, uncaught DB
    trigger error surfaces instead). What this buys is a readable
    operator-facing message naming every affected row before any
    statement runs, instead of an opaque trigger error partway through
    -- worth having, but a legibility improvement over the database's
    own guarantee, not a substitute for it.

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


def _refuse_if_referenced_elsewhere(bind, matched_ids: list[int]) -> None:
    """Abort if a matched row is referenced from a table this revision
    cannot repair.

    ``record_reproducibility_assessment`` and
    ``scientific_record_supersession`` both refuse every DELETE on
    themselves unconditionally (see the module docstring's "Other
    tables" section), so this revision could not remove a referencing
    row even if it decided to. Measured: neither table holds a row for
    any of the 17 on the Pi. Re-derived at upgrade time, like the frozen
    check, so this is correct on any database, not only the one
    measured.
    """
    if not matched_ids:
        return
    rows = bind.execute(
        _UNREPAIRABLE_REFERENCE_QUERY, {"ids": matched_ids}
    ).all()
    if rows:
        detail = "; ".join(f"{row.table_name} record_id={row.record_id}" for row in rows)
        raise RuntimeError(
            "Cannot upgrade to a55cc983501a: applied_energy_correction "
            f"row(s) referenced from {detail} match the false-TS-zero-BAC "
            "shape this revision repairs, but this revision has no way to "
            "repair a reference from either table (both refuse every "
            "DELETE on themselves unconditionally). Resolve these "
            "references manually before re-running this upgrade."
        )


def _system_actor_id(bind) -> int:
    """The ``app_user`` id this revision's ``record_review`` transitions
    are attributed to.

    Idempotent (``ON CONFLICT ... DO UPDATE`` so it always ``RETURNING``s
    a row rather than raising on a re-run), because a review transition
    is a terminal status and ``record_review_terminal_requires_reviewer``
    demands a real ``reviewed_by``/``reviewed_at`` on it -- the same
    requirement a real curator's own transition satisfies with their id.
    Created ``is_active = false`` with no password: this account can
    never log in or authenticate as itself, only be pointed at by a
    foreign key.

    The ``ON CONFLICT`` path is a real hazard if ``system-migration-actor``
    were ever a genuine person's username: silently reusing their row
    would attribute these transitions to them. Guarded by re-reading the
    row this INSERT/upsert resolved to and refusing unless it is
    unmistakably this migration's own account (admin, inactive, no
    password) -- a real user account fails at least one of those.
    """
    row = bind.execute(
        text(
            """
            INSERT INTO app_user (username, role, is_active, password_hash)
            VALUES ('system-migration-actor', 'admin', false, NULL)
            ON CONFLICT (username) DO UPDATE SET username = EXCLUDED.username
            RETURNING id, role, is_active, password_hash
            """
        )
    ).one()
    if row.role != "admin" or row.is_active is not False or row.password_hash is not None:
        raise RuntimeError(
            "Cannot upgrade to a55cc983501a: an app_user already exists with "
            "username='system-migration-actor' that does not match the shape "
            "this migration creates it in (role='admin', is_active=false, "
            "no password) -- it looks like a real account, and attributing "
            "these record_review transitions to it would misattribute them "
            "to whoever that is. Rename that account or choose a different "
            "system-actor username before re-running this upgrade."
        )
    return row.id


def _deprecate_record_reviews(bind, matched_ids: list[int], actor_id: int) -> None:
    """Move each matched correction's ``record_review`` to ``deprecated``.

    Mirrors ``app.services.record_review.set_record_review_status``
    field-for-field (``status``, ``reviewed_by``, ``reviewed_at``,
    ``note``, and the appended event), because that -- not a
    reimplementation, and not a way around it -- is "the normal
    status-change path". ``note`` is set only on the final transition to
    ``deprecated``, not on the intermediate reopen, matching a curator
    who reopens a record without comment and then deprecates it with a
    reason. Two statements
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
                       reviewed_at = now(), note = :reason
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
    _refuse_if_referenced_elsewhere(bind, matched_ids)
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

    # submission_record_link is what approve_submission/reject_submission
    # actually iterate (not record_review); left dangling, a pending
    # submission that deposited a deleted correction can be neither
    # approved nor rejected. No FK, no append-only contract on this
    # table -- see the module docstring's "Other tables" section.
    bind.execute(
        text(
            "DELETE FROM submission_record_link "
            "WHERE record_type = 'applied_energy_correction' "
            "AND record_id = ANY(:ids)"
        ),
        {"ids": matched_ids},
    )

    actor_id = _system_actor_id(bind)
    _deprecate_record_reviews(bind, matched_ids, actor_id)


def downgrade() -> None:
    """No-op: deleted rows are gone, not un-deleted, and a deprecated
    review row stays deprecated. See the module docstring's "Downgrade
    cannot restore deleted rows" section."""
