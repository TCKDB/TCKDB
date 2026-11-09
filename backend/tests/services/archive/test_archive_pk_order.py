"""Archive row order must agree between the writer (SQL) and the reader (Python).

``_parse_rows`` checks that primary keys are strictly increasing by comparing
the *decoded* values in Python. PostgreSQL orders an enum by declaration
position and text by collation, so ordering by the raw column made the writer
emit an order the reader rejected (e.g. a species that is both reactant and
product of one reaction: ``reactant < product`` in the enum, the opposite as
strings).
"""

from __future__ import annotations

import io

from sqlalchemy import select, text
from sqlalchemy.sql.sqltypes import CHAR, Enum, String, Text

from app.db.base import Base
from app.db.models.reaction import ReactionParticipant
from app.services.archive import core as archive_core
from app.services.archive import restore_archive, write_archive
from app.services.archive.registry import included_tables_in_fk_order
from tests.services.archive.test_archive import _empty_archive_tables
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_species,
    next_inchi_key,
)


def _exposed_pk_columns():
    return [
        (table, column)
        for table in included_tables_in_fk_order(Base.metadata)
        for column in table.primary_key.columns
        if isinstance(column.type, (Enum, CHAR, String, Text))
    ]


def test_every_enum_and_text_primary_key_is_ordered_like_python(db_session) -> None:
    exposed = _exposed_pk_columns()
    names = {table.name for table, _ in exposed}
    assert {"reaction_participant", "calculation_parameter_vocab"} <= names

    checked_enums = 0
    for table, column in exposed:
        order = archive_core._pk_order_expression(column)
        assert 'COLLATE "C"' in str(order.compile()), f"{table.name}.{column.name} is not codepoint-ordered"
        if isinstance(column.type, Enum):
            ordered = db_session.scalars(
                text(
                    f'SELECT v::text FROM unnest(enum_range(NULL::"{column.type.name}")) AS t(v) '
                    'ORDER BY v::text COLLATE "C"'
                )
            ).all()
            assert list(ordered) == sorted(column.type.enums), f"{table.name}.{column.name}"
            checked_enums += 1
    assert checked_enums >= 10

    samples = ["a", "B", "_x", "Z", "b_1", "B_2", "é", "A-b", "a b"]
    ordered_text = db_session.scalars(
        text('SELECT v FROM unnest(CAST(:vals AS text[])) AS t(v) ORDER BY v COLLATE "C"').bindparams(vals=samples)
    ).all()
    assert list(ordered_text) == sorted(samples)


def test_round_trip_with_a_species_on_both_sides_of_a_reaction(db_session) -> None:
    a = make_species(db_session, inchi_key=next_inchi_key("ARCHPKA"))
    b = make_species(db_session, inchi_key=next_inchi_key("ARCHPKB"))
    c = make_species(db_session, inchi_key=next_inchi_key("ARCHPKC"))
    reaction = make_chem_reaction(db_session, reactants=[a, b], products=[a, c])

    def participants():
        return {
            (p.reaction_id, p.species_id, p.role.value, p.stoichiometry)
            for p in db_session.scalars(
                select(ReactionParticipant).where(ReactionParticipant.reaction_id == reaction.id)
            )
        }

    expected = participants()
    assert len(expected) == 4

    archive = io.BytesIO()
    write_archive(db_session, archive)
    _empty_archive_tables(db_session)
    restore_archive(db_session, io.BytesIO(archive.getvalue()))
    assert participants() == expected
