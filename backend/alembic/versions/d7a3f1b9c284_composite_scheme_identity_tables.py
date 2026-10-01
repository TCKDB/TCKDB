"""composite_scheme and its binding to level_of_theory (ADR 0021, phase P2)

Adds the identity tables a composite level of theory needs, and binds the
existing levels of theory that name a catalogued composite method.

Tables (all new; none is deployed yet, and this revision is the one that
deploys them, so later changes to them are new revisions)
* ``composite_scheme`` -- the recipe. Identity: unique on ``definition_hash``,
  carries a content-derived ``public_ref`` (``csch_``), never updated in place.
* ``composite_scheme_term`` -- the recipe's terms, in order.
* ``composite_scheme_term_input`` -- the level-of-theory inputs of a term.
* ``level_of_theory_composite`` -- side table binding a level of theory to a
  scheme, like ``level_of_theory_merge``. ``level_of_theory.lot_hash`` is not
  touched.

Enums (new types): ``composite_scheme_kind``, ``composite_term_operation``,
``energy_component_kind``, ``composite_extrapolation_formula``,
``composite_input_slot``, ``composite_binding_source``.

What the backfill writes
------------------------
For every **unmerged** ``level_of_theory`` row whose canonical method key is one
of the catalogue's named composite methods (``CBS-QB3``, ``cbsqb3``, ``G4``,
...), in id order:

* get-or-create the ``named_method`` scheme for the key, with
  ``definition_hash = sha256`` of the canonical JSON
  ``{"kind":"named_method","method":<key>}``, the catalogue's name, its recipe
  ZPE scale factor where one is cited, and its internal geometry and
  frequency levels where the catalogue states them. Those internal levels are
  ordinary ``level_of_theory`` rows found by their hash or created here (a
  level created here has no calculations, so the usage-derived level-of-theory
  reads and searches omit it, but ``GET /api/v1/levels-of-theory`` lists every
  unmerged level and shows it). An internal level that was itself merged into
  another is replaced by the row it was merged into;
* insert the binding ``(level_of_theory_id, scheme_id,
  binding_source = 'named_method_catalogue')`` unless it exists.

No terms are created: the catalogue holds no term list. A merged row is not
bound (reads resolve it to the row it was merged into, which is). A row whose
method is not catalogued is left alone: ``B3LYP`` stays unbound.

The Pi holds no such row (measured 2026-10-01), so on the hosted database the
backfill writes nothing; it is written to be correct on any database all the
same, and a test seeds one.

Rules frozen here
-----------------
``_NAMED_METHODS`` (the catalogue as of ``app/chemistry/composite_methods.py``
at this revision, only the fields the scheme row copies), ``_METHOD_ALIASES``
(the aliases that reach a catalogue key), ``_canonical_json`` /
``_definition_hash``, the level-of-theory hash formula for an internal level,
and the two public-ref formulas are a copy of the application's rules. A
migration must describe what it ran, so it imports no application code; tests
hold the copies in agreement with the application for as long as the rules are
the same.

Downgrade
---------
Drops the four tables and the six enum types, and with them every binding and
scheme, including any minted after this upgrade. Internal-level
``level_of_theory`` rows the backfill created are left in place (they are
ordinary levels of theory, and cannot be told from ones a producer sent). The
downgrade prints how many bindings and schemes it drops.

Revision ID: d7a3f1b9c284
Revises: b9e4c2a7d153
Create Date: 2026-10-01
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d7a3f1b9c284"
down_revision: Union[str, Sequence[str], None] = "b9e4c2a7d153"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str, *labels: str) -> postgresql.ENUM:
    return postgresql.ENUM(*labels, name=name, create_type=False)


_KIND = _enum("composite_scheme_kind", "named_method", "extrapolation", "additive")
_OPERATION = _enum("composite_term_operation", "base", "extrapolation", "difference", "value", "empirical")
_COMPONENT = _enum(
    "energy_component_kind",
    "total",
    "reference",
    "correlation",
    "triples",
    "dboc",
    "scalar_relativistic",
)
_FORMULA = _enum(
    "composite_extrapolation_formula",
    "inverse_power",
    "inverse_power_shifted_half",
    "karton_martin_scf",
    "exponential_three_point",
)
_SLOT = _enum("composite_input_slot", "value", "high", "low", "cardinal")
_BINDING_SOURCE = _enum("composite_binding_source", "named_method_catalogue", "declared")
_ENUMS = (_KIND, _OPERATION, _COMPONENT, _FORMULA, _SLOT, _BINDING_SOURCE)

_SCHEME_REF_DEFAULT = sa.text("'csch_' || substring(replace(gen_random_uuid()::text, '-', ''), 1, 26)")


# ---------------------------------------------------------------------------
# Frozen rules
# ---------------------------------------------------------------------------

#: key -> (name, geometry level, frequency level, recipe ZPE scale factor, paper DOI).
#: A level is ``(method, basis)``; ``None`` where the catalogue does not state it.
_NAMED_METHODS: dict[str, tuple[str, tuple[str, str] | None, tuple[str, str] | None, float | None, str]] = {
    "cbs-qb3": ("CBS-QB3", ("B3LYP", "CBSB7"), ("B3LYP", "CBSB7"), 0.99, "10.1063/1.477924"),
    "rocbs-qb3": ("ROCBS-QB3", None, None, None, "10.1063/1.2335438"),
    "cbs-apno": ("CBS-APNO (CBS-QCI/APNO)", None, None, None, "10.1063/1.467306"),
    "cbs-4m": ("CBS-4M", None, None, None, "10.1063/1.481224"),
    "g3": ("G3", ("MP2(FU)", "6-31G(d)"), ("HF", "6-31G(d)"), 0.8929, "10.1063/1.477422"),
    "g3b3": ("G3//B3LYP (G3B3)", ("B3LYP", "6-31G(d)"), ("B3LYP", "6-31G(d)"), 0.96, "10.1063/1.478676"),
    "g3mp2": ("G3(MP2)", None, ("HF", "6-31G(d)"), 0.8929, "10.1063/1.478385"),
    "g3mp2b3": (
        "G3(MP2)//B3LYP (G3MP2B3)",
        ("B3LYP", "6-31G(d)"),
        ("B3LYP", "6-31G(d)"),
        0.96,
        "10.1063/1.478676",
    ),
    "g4": ("G4", ("B3LYP", "6-31G(2df,p)"), ("B3LYP", "6-31G(2df,p)"), 0.9854, "10.1063/1.2436888"),
    "g4mp2": ("G4(MP2)", ("B3LYP", "6-31G(2df,p)"), ("B3LYP", "6-31G(2df,p)"), 0.9854, "10.1063/1.2770701"),
    "w1": ("W1", ("B3LYP", "cc-pVTZ+1"), ("B3LYP", "cc-pVTZ+1"), 0.985, "10.1063/1.479454"),
    "w1u": ("W1U", None, None, None, "10.1021/ct900260g"),
    "w1bd": ("W1BD", None, None, None, "10.1021/ct900260g"),
    "w1ro": ("W1RO", None, None, None, "10.1021/ct900260g"),
    "w2": ("W2", ("CCSD(T)", "cc-pVQZ+1"), None, None, "10.1063/1.479454"),
}

#: The aliases of the method key that reach a catalogue key (alias -> key).
_METHOD_ALIASES = {
    "cbsqb3": "cbs-qb3",
    "rocbsqb3": "rocbs-qb3",
    "cbs4m": "cbs-4m",
    "cbsapno": "cbs-apno",
    "g4(mp2)": "g4mp2",
    "g3(mp2)": "g3mp2",
    "g3(mp2)b3": "g3mp2b3",
}

#: Frozen copy of ``app.chemistry.basis_set_names.HYPHEN_RULES``.
_HYPHEN_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![^-])def2(?=[a-z])"), "def2-"),
    (re.compile(r"(?<![^-])ccp(?=(?:w?c)?v)"), "cc-p"),
)


def _method_key(name: str) -> str:
    key = name.strip().lower()
    return _METHOD_ALIASES.get(key, key)


def _basis_key(name: str | None) -> str | None:
    if name is None:
        return None
    key = name.strip().lower()
    if not key:
        return None
    for pattern, replacement in _HYPHEN_RULES:
        key = pattern.sub(replacement, key)
    return key


def _canonical_json(key: str) -> str:
    return json.dumps({"kind": "named_method", "method": key}, sort_keys=True, separators=(",", ":"))


def _definition_hash(key: str) -> str:
    return hashlib.sha256(_canonical_json(key).encode("utf-8")).hexdigest()


def _internal_level_hash(method: str, basis: str) -> str:
    """``lot_hash`` of an ordinary level with only a method and a basis."""
    payload = {
        "method": _method_key(method),
        "basis": _basis_key(basis),
        "aux_basis": None,
        "cabs_basis": None,
        "dispersion": None,
        "solvent": None,
        "solvent_model": None,
        "keywords": None,
        "spin_treatment": "unknown",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _content_ref(prefix: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).digest()[:16]
    body = base64.b32encode(digest).decode("ascii").lower().rstrip("=")
    return f"{prefix}_{body[:26]}"


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def _internal_level_id(bind, level: tuple[str, str] | None) -> int | None:
    """Find or create the ordinary level a recipe runs internally."""
    if level is None:
        return None
    method, basis = level
    lot_hash = _internal_level_hash(method, basis)
    row_id = bind.scalar(sa.text("SELECT id FROM level_of_theory WHERE lot_hash = :h"), {"h": lot_hash})
    if row_id is None:
        row_id = bind.scalar(
            sa.text(
                "INSERT INTO level_of_theory (method, basis, lot_hash, public_ref) "
                "VALUES (:m, :b, :h, :r) RETURNING id"
            ),
            {"m": method, "b": basis, "h": lot_hash, "r": _content_ref("lot", f"lot_hash:{lot_hash}")},
        )
    merged_into = bind.scalar(
        sa.text("SELECT into_lot_id FROM level_of_theory_merge WHERE merged_lot_id = :i"), {"i": row_id}
    )
    return merged_into if merged_into is not None else row_id


def _scheme_id(bind, key: str) -> int:
    definition_hash = _definition_hash(key)
    found = bind.scalar(
        sa.text("SELECT id FROM composite_scheme WHERE definition_hash = :h"), {"h": definition_hash}
    )
    if found is not None:
        return found
    name, geometry, frequency, zpe_scale, doi = _NAMED_METHODS[key]
    return bind.scalar(
        sa.text(
            "INSERT INTO composite_scheme (kind, name, definition_hash, geometry_level_of_theory_id, "
            "frequency_level_of_theory_id, recipe_zpe_scale_factor, note, public_ref) "
            "VALUES (CAST('named_method' AS composite_scheme_kind), :name, :h, :g, :f, :z, :note, :r) "
            "RETURNING id"
        ),
        {
            "name": name,
            "h": definition_hash,
            "g": _internal_level_id(bind, geometry),
            "f": _internal_level_id(bind, frequency),
            "z": zpe_scale,
            "note": (
                f"Defining paper: doi:{doi}. Values as catalogued in "
                "app/chemistry/composite_methods.py when the row was written."
            ),
            "r": _content_ref("csch", f"csch:definition_hash={definition_hash}"),
        },
    )


def _backfill(bind) -> tuple[int, int]:
    """Bind every unmerged catalogued level. Returns (bindings written, schemes written)."""
    schemes_before = bind.scalar(sa.text("SELECT count(*) FROM composite_scheme"))
    rows = bind.execute(
        sa.text(
            "SELECT id, method FROM level_of_theory "
            "WHERE id NOT IN (SELECT merged_lot_id FROM level_of_theory_merge) ORDER BY id"
        )
    ).all()
    written = 0
    for row_id, method in rows:
        key = _method_key(method)
        if key not in _NAMED_METHODS:
            continue
        written += bind.execute(
            sa.text(
                "INSERT INTO level_of_theory_composite (level_of_theory_id, scheme_id, binding_source) "
                "VALUES (:l, :s, CAST('named_method_catalogue' AS composite_binding_source)) "
                "ON CONFLICT (level_of_theory_id) DO NOTHING"
            ),
            {"l": row_id, "s": _scheme_id(bind, key)},
        ).rowcount
    schemes_after = bind.scalar(sa.text("SELECT count(*) FROM composite_scheme"))
    return written, schemes_after - schemes_before


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def upgrade() -> None:
    bind = op.get_bind()
    for enum in _ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "composite_scheme",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", _KIND, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("definition_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("geometry_level_of_theory_id", sa.BigInteger(), nullable=True),
        sa.Column("frequency_level_of_theory_id", sa.BigInteger(), nullable=True),
        sa.Column("recipe_zpe_scale_factor", sa.Double(), nullable=True),
        sa.Column("source_literature_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("public_ref", sa.String(length=40), server_default=_SCHEME_REF_DEFAULT, nullable=False),
        sa.CheckConstraint(
            "recipe_zpe_scale_factor IS NULL OR recipe_zpe_scale_factor > 0",
            name=op.f("ck_composite_scheme_recipe_zpe_scale_factor_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["geometry_level_of_theory_id"],
            ["level_of_theory.id"],
            name=op.f("fk_composite_scheme_geometry_level_of_theory_id"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["frequency_level_of_theory_id"],
            ["level_of_theory.id"],
            name=op.f("fk_composite_scheme_frequency_level_of_theory_id"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["source_literature_id"],
            ["literature.id"],
            name=op.f("fk_composite_scheme_source_literature_id_literature"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_composite_scheme")),
        sa.UniqueConstraint("definition_hash", name=op.f("uq_composite_scheme_definition_hash")),
    )
    op.create_index(op.f("ix_composite_scheme_public_ref"), "composite_scheme", ["public_ref"], unique=True)

    op.create_table(
        "composite_scheme_term",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("scheme_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("operation", _OPERATION, nullable=False),
        sa.Column("energy_component", _COMPONENT, nullable=False),
        sa.Column("formula", _FORMULA, nullable=True),
        sa.Column("exponent", sa.Double(), nullable=True),
        sa.CheckConstraint("position >= 0", name=op.f("ck_composite_scheme_term_position_non_negative")),
        sa.CheckConstraint(
            "formula IS NULL OR operation = 'extrapolation'",
            name=op.f("ck_composite_scheme_term_formula_only_on_extrapolation"),
        ),
        sa.CheckConstraint(
            "exponent IS NULL OR formula IS NOT NULL",
            name=op.f("ck_composite_scheme_term_exponent_needs_formula"),
        ),
        sa.ForeignKeyConstraint(
            ["scheme_id"],
            ["composite_scheme.id"],
            name=op.f("fk_composite_scheme_term_scheme_id_composite_scheme"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_composite_scheme_term")),
        sa.UniqueConstraint("scheme_id", "position", name="uq_composite_scheme_term_scheme_id_position"),
    )

    op.create_table(
        "composite_scheme_term_input",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("term_id", sa.BigInteger(), nullable=False),
        sa.Column("slot", _SLOT, nullable=False),
        sa.Column("level_of_theory_id", sa.BigInteger(), nullable=False),
        sa.Column("cardinal_number", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "cardinal_number IS NULL OR cardinal_number >= 1",
            name=op.f("ck_composite_scheme_term_input_cardinal_number_positive"),
        ),
        sa.CheckConstraint(
            "slot <> 'cardinal' OR cardinal_number IS NOT NULL",
            name=op.f("ck_composite_scheme_term_input_cardinal_slot_needs_number"),
        ),
        sa.ForeignKeyConstraint(
            ["term_id"],
            ["composite_scheme_term.id"],
            name=op.f("fk_composite_scheme_term_input_term_id_composite_scheme_term"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["level_of_theory_id"],
            ["level_of_theory.id"],
            name=op.f("fk_composite_scheme_term_input_level_of_theory_id"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_composite_scheme_term_input")),
    )
    op.create_index(
        "uq_composite_scheme_term_input_term_slot_cardinal",
        "composite_scheme_term_input",
        ["term_id", "slot", "cardinal_number"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_composite_scheme_term_input_level_of_theory_id",
        "composite_scheme_term_input",
        ["level_of_theory_id"],
        unique=False,
    )

    op.create_table(
        "level_of_theory_composite",
        sa.Column("level_of_theory_id", sa.BigInteger(), nullable=False),
        sa.Column("scheme_id", sa.BigInteger(), nullable=False),
        sa.Column("binding_source", _BINDING_SOURCE, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["level_of_theory_id"],
            ["level_of_theory.id"],
            name=op.f("fk_level_of_theory_composite_level_of_theory_id_level_of_theory"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.ForeignKeyConstraint(
            ["scheme_id"],
            ["composite_scheme.id"],
            name=op.f("fk_level_of_theory_composite_scheme_id_composite_scheme"),
            initially="IMMEDIATE",
            deferrable=True,
        ),
        sa.PrimaryKeyConstraint("level_of_theory_id", name=op.f("pk_level_of_theory_composite")),
    )
    op.create_index(
        op.f("ix_level_of_theory_composite_scheme_id"), "level_of_theory_composite", ["scheme_id"], unique=False
    )

    written, schemes = _backfill(bind)
    print(
        f"composite_scheme backfill: {written} level(s) of theory bound to a named-method scheme, "
        f"{schemes} scheme(s) created."
    )


def downgrade() -> None:
    bind = op.get_bind()
    bindings = bind.scalar(sa.text("SELECT count(*) FROM level_of_theory_composite"))
    schemes = bind.scalar(sa.text("SELECT count(*) FROM composite_scheme"))
    op.drop_table("level_of_theory_composite")
    op.drop_table("composite_scheme_term_input")
    op.drop_table("composite_scheme_term")
    op.drop_table("composite_scheme")
    for enum in reversed(_ENUMS):
        enum.drop(bind, checkfirst=True)
    print(
        f"composite_scheme downgrade: dropped {bindings} binding(s) and {schemes} scheme(s); "
        "internal-level level_of_theory rows created by the upgrade were left in place."
    )
