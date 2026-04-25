"""Scientific-invariant regression tests.

This suite pins a small set of high-value, physically meaningful invariants
that CRUD / schema tests do not enforce:

- thermodynamic consistency (G ~ H - T*S, NASA piecewise continuity)
- identity-hash determinism (geom_hash, lot_hash, stoichiometry_hash)
- unit-conversion correctness
- structure/representation consistency (graph vs geometry)
- end-to-end workflow preservation of invariant-relevant fields

See ``docs/scientific-invariant-tests-spec.md`` for the motivation and scope.
"""
