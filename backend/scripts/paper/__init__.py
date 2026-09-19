"""Manuscript-number generators.

Every ``[DATA]`` number in the paper comes from one generator in
:mod:`scripts.paper.registry`, run against the restored publication database
and compared byte-for-byte with the ``expected_outputs/`` the deposit ships.
The package is copied into the deposit as its ``result_generator`` members.
"""
