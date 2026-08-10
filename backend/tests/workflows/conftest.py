"""Keep the workflow suite from writing anything the next test can see.

Every test here persists a lot: species, entries, calculations, geometries,
transition states, and the scientific products hanging off them. They share
one database for the whole pytest process, and many of them locate "the" row
they just made with an unqualified query — ``session.scalar(select(Calculation)
.where(Calculation.type == irc))`` and friends. That only works while the
database contains nothing but the current test's own writes.

For a long time it did not. The tree used the session-scoped ``db_engine``
directly with ``with Session(db_engine) as session, session.begin():``, which
commits on block exit, so each test inherited everything its predecessors had
written. Running ``test_network_pdep_upload.py`` before
``test_computed_reaction_upload.py`` left fifteen committed ``irc``
calculations behind and five computed-reaction tests then asserted against the
wrong one — deterministically, with no random seed involved.

The tree now takes ``db_conn`` (per-test transaction, rolled back at teardown;
see ``tests/conftest.py``).

The enforcement — the fixture that fails the individual test that commits
rather than letting the damage surface as an inexplicable failure in whatever
file happens to run next — used to live here. It now lives in
``tests/conftest.py`` (``_refuse_committed_rows``) and covers every tree, not
just this one, because ~40 other files had the same habit. This module is kept
for the history above; the tree needs no fixtures of its own.
"""

from __future__ import annotations
