"""Pin docs/guides/depositing_a_thermo_record.md's quoted refusal messages
to the code that actually emits them.

The doc quotes four refusal messages verbatim in fenced code blocks. Without
this test, improving or fixing a message in
``tckdb_schemas.enthalpy_reference`` silently leaves the doc quoting stale
prose -- exactly the drift that produced the review's finding that the doc
made false claims about the code. Each assertion here fails loudly instead.
"""

import re
from pathlib import Path

from tckdb_schemas.enthalpy_reference import (
    EnthalpyReferenceKind,
    enthalpy_reference_error,
)

DOC = Path(__file__).resolve().parents[3] / "docs" / "guides" / "depositing_a_thermo_record.md"


def _normalize(text: str) -> str:
    """Collapse the doc's line-wrapped prose to the same shape as a
    ``str``-concatenated Python message, so wrap points don't matter."""
    return re.sub(r"\s+", " ", text).strip()


def _fenced_block_after(doc_text: str, heading: str) -> str:
    marker = f"**`{heading}`**"
    start = doc_text.index(marker)
    fence_start = doc_text.index("```", start) + 3
    fence_end = doc_text.index("```", fence_start)
    return doc_text[fence_start:fence_end]


def test_declaration_absent_message_matches_the_code():
    doc_text = DOC.read_text()
    code, message = enthalpy_reference_error({"h298_kj_mol": 0.0})
    assert code == "enthalpy_declaration_absent"
    assert _normalize(_fenced_block_after(doc_text, code)) == _normalize(message)


def test_declaration_without_content_message_matches_the_code():
    doc_text = DOC.read_text()
    code, message = enthalpy_reference_error({
        "s298_j_mol_k": 10, "enthalpy_reference_kind": "formation_298k",
    })
    assert code == "enthalpy_declaration_without_content"
    assert _normalize(_fenced_block_after(doc_text, code)) == _normalize(message)


def test_quantity_not_storable_message_matches_the_code():
    doc_text = DOC.read_text()
    code, message = enthalpy_reference_error({
        "h298_kj_mol": 10, "enthalpy_reference_kind": "sensible_increment",
    })
    assert code == "enthalpy_quantity_not_storable_here"
    assert _normalize(_fenced_block_after(doc_text, code)) == _normalize(message)


def test_reference_kind_unrecognized_message_matches_the_code_shape():
    """The doc quotes this one with a literal ``'<value>'`` placeholder
    (the real message interpolates the offered value), so this pins the
    surrounding sentence rather than a byte-identical message."""
    doc_text = DOC.read_text()
    code, message = enthalpy_reference_error({
        "h298_kj_mol": 10, "enthalpy_reference_kind": "Formation_298K",
    })
    assert code == "enthalpy_reference_kind_unrecognized"
    doc_block = _normalize(_fenced_block_after(doc_text, code))
    doc_template = doc_block.replace("'<value>'", "'Formation_298K'")
    assert doc_template == _normalize(message)


def test_enthalpy_reference_kind_has_exactly_one_legal_value():
    """The doc's worked examples and refusal prose all assume this."""
    assert [member.value for member in EnthalpyReferenceKind] == ["formation_298k"]
