#!/usr/bin/env python
"""Count calculation_dependency edges whose parent type breaks DR-0028 (#581).

READ-ONLY. This script issues one ``SELECT`` and nothing else; it never
writes, and it does not repair what it finds. Calculations may be approved
and therefore immutable, so a mismatched edge is reported for a person to
decide about, not rewritten.

An edge is *mismatched* when its role pins a parent type
(``_DEPENDENCY_ROLE_TO_PARENT_TYPE``; ``optimized_from`` accepts ``opt`` or
``path_search``) and the parent calculation has a different type -- for
example a ``single_point_on`` edge whose parent is an ``sp``. The role table
is read from the application code, so this query cannot drift from the rule
the upload paths enforce.

Usage (uses the same ``DB_*`` environment variables as the app)::

    python scripts/ops/count_mismatched_dependency_edges.py

Exit status: 0 when no edge is mismatched, 1 when at least one is, so a
non-zero exit is a finding, not a failure to run.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.services.calculation_resolution import (  # noqa: E402
    _DEPENDENCY_ROLE_TO_PARENT_TYPE,
    _OPTIMIZED_FROM_PARENT_TYPES,
)


def build_mismatch_sql() -> str:
    """The SELECT that groups mismatched edges by role, parent and child type."""
    allowed: dict[str, list[str]] = {
        role.value: [expected.value]
        for role, expected in _DEPENDENCY_ROLE_TO_PARENT_TYPE.items()
    }
    allowed["optimized_from"] = sorted(t.value for t in _OPTIMIZED_FROM_PARENT_TYPES)
    clauses = []
    for role, types in sorted(allowed.items()):
        listed = ", ".join(f"'{t}'" for t in types)
        clauses.append(
            f"(d.dependency_role::text = '{role}' "
            f"AND p.type::text NOT IN ({listed}))"
        )
    where = "\n     OR ".join(clauses)
    return (
        "SELECT d.dependency_role::text AS role,\n"
        "       p.type::text AS parent_type,\n"
        "       c.type::text AS child_type,\n"
        "       count(*) AS edges\n"
        "  FROM calculation_dependency d\n"
        "  JOIN calculation p ON p.id = d.parent_calculation_id\n"
        "  JOIN calculation c ON c.id = d.child_calculation_id\n"
        f" WHERE {where}\n"
        " GROUP BY 1, 2, 3\n"
        " ORDER BY edges DESC, 1, 2, 3"
    )


TOTAL_SQL = "SELECT count(*) FROM calculation_dependency"


def count_mismatched(session: Session) -> tuple[int, list[tuple[str, str, str, int]]]:
    total = session.execute(text(TOTAL_SQL)).scalar_one()
    rows = [
        (r.role, r.parent_type, r.child_type, r.edges)
        for r in session.execute(text(build_mismatch_sql()))
    ]
    return total, rows


def main() -> int:
    from app.api.deps import SessionLocal

    print(build_mismatch_sql())
    print()
    with SessionLocal() as session:
        # Belt and braces: nothing this script does may write.
        session.execute(text("SET TRANSACTION READ ONLY"))
        total, rows = count_mismatched(session)
    mismatched = sum(r[3] for r in rows)
    print(f"calculation_dependency edges: {total}")
    print(f"mismatched edges:             {mismatched}")
    for role, parent, child, n in rows:
        print(f"  role={role} parent={parent} child={child}: {n}")
    return 1 if mismatched else 0


if __name__ == "__main__":
    sys.exit(main())
