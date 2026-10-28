"""The kind of a network channel is a function of the two states it joins.

A pressure-dependent network channel is directed, source to sink. What it *does* macroscopically
follows from whether each end is a well (one energized or stable molecule) or a separated set of
fragments (a bimolecular or termolecular entrance or exit). There is one rule, applied to every
pair, so the kind is always defined and a depositor cannot label the same channel two ways:

========================================  ===============
source state kind -> sink state kind       channel kind
========================================  ===============
well -> well                               ``isomerization``
bimolecular/termolecular -> well           ``association``
well -> bimolecular/termolecular           ``dissociation``
bimolecular/termolecular -> bi/termol.     ``exchange``
========================================  ===============

Termolecular is treated exactly like bimolecular. A channel between two fragment sets is
``exchange`` even when it changes only stereochemistry (``E-N2H2 + H2 -> Z-N2H2 + H2``).

This module is the single place the rule lives. The upload validator, the ingestion script and
any client that wants to predict the stored kind call :func:`derive_channel_kind`.
"""

from __future__ import annotations

from tckdb_schemas.enums import NetworkChannelKind

__all__ = ["W_NETWORK_CHANNEL_KIND_MISMATCH", "derive_channel_kind"]

#: A depositor-supplied channel ``kind`` disagrees with the kind derived from its states.
W_NETWORK_CHANNEL_KIND_MISMATCH = "network_channel_kind_mismatch"

_WELL = "well"
_FRAGMENTS = frozenset({"bimolecular", "termolecular"})


def _state_kind(value: object) -> str:
    text = getattr(value, "value", value)
    if text != _WELL and text not in _FRAGMENTS:
        raise ValueError(
            f"unknown network state kind {text!r}; expected 'well', 'bimolecular' or 'termolecular'."
        )
    return str(text)


def derive_channel_kind(source_state_kind: object, sink_state_kind: object) -> NetworkChannelKind:
    """Return the channel kind implied by a channel's source and sink state kinds.

    :param source_state_kind: ``well``, ``bimolecular`` or ``termolecular`` (a string or an enum
        whose value is one).
    :param sink_state_kind: same vocabulary, for the sink.
    :raises ValueError: a state kind outside that vocabulary.
    """
    source = _state_kind(source_state_kind)
    sink = _state_kind(sink_state_kind)
    if source == _WELL:
        return NetworkChannelKind.isomerization if sink == _WELL else NetworkChannelKind.dissociation
    return NetworkChannelKind.association if sink == _WELL else NetworkChannelKind.exchange
