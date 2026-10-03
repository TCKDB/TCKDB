"""One way to write a level of theory in full, for every place that prints one.

A level of theory's identity (``lot_hash``) covers more than ``method/basis``: dispersion, solvent and solvent
model, spin treatment, core treatment, and the auxiliary and CABS basis sets (which matter for RI and F12) all
tell two levels apart. A label that drops one lets two distinct levels read identically, so the notation of a
record's levels (``ScientificLevelsSummary.notation``) and the ML-dataset export's ``label`` both come from this
function and cannot drift apart.

Written, in order, when stated: ``method/basis`` (``method`` alone with no basis), then in parentheses
``aux=...``, ``cabs=...``, ``disp=...``, ``solvent=model:name`` (or ``solvent=name`` with no model),
``spin=...`` and ``core=...``. An unstated part is omitted, never written as a default.

Deliberately **not** written: raw ``keywords``. They are free text that is part of ``lot_hash`` but is not a
property a reader can compare at a glance, and they can be long. The stable machine key is ``lot_hash``; the
label is the readable form and is never a key.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["render_level_label"]


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Enum):
        value = value.value
    text = str(value)
    return text or None


def render_level_label(
    *,
    method: str,
    basis: str | None = None,
    aux_basis: str | None = None,
    cabs_basis: str | None = None,
    dispersion: str | None = None,
    solvent: str | None = None,
    solvent_model: str | None = None,
    spin_treatment: object | None = None,
    core_treatment: object | None = None,
) -> str:
    """The full label of a level of theory.

    :param method: The method as stored.
    :param basis: The orbital basis, if any.
    :param aux_basis: The auxiliary (RI) basis, if stated.
    :param cabs_basis: The CABS basis, if stated.
    :param dispersion: The dispersion correction, if any.
    :param solvent: The solvent, if any.
    :param solvent_model: The solvent model; written before the solvent (``smd:water``), and only with one.
    :param spin_treatment: Restricted / unrestricted / restricted-open, an enum or its value, if stated.
    :param core_treatment: Frozen core / all electron, an enum or its value, if stated.
    :returns: ``method/basis`` followed by ``(part=value, ...)`` for each stated part.
    """
    core = f"{method}/{basis}" if basis else method
    extra: list[str] = []
    if _text(aux_basis):
        extra.append(f"aux={_text(aux_basis)}")
    if _text(cabs_basis):
        extra.append(f"cabs={_text(cabs_basis)}")
    if _text(dispersion):
        extra.append(f"disp={_text(dispersion)}")
    solvent_text = _text(solvent)
    if solvent_text:
        model = _text(solvent_model)
        extra.append(f"solvent={model}:{solvent_text}" if model else f"solvent={solvent_text}")
    if _text(spin_treatment):
        extra.append(f"spin={_text(spin_treatment)}")
    if _text(core_treatment):
        extra.append(f"core={_text(core_treatment)}")
    return f"{core} ({', '.join(extra)})" if extra else core
