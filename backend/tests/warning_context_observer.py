"""Hold every warning any producer builds, in any test, to the rule that a message is TCKDB's sentence (#838).

Why a runtime observer and not only a sweep
-------------------------------------------
The sweep in ``tests/api/test_warning_messages_from_codes.py`` deposits sentinel strings and reaches about 40 of the
~80 warning codes. A producer for a code the sweep does not reach can still move a label out of
``context["depositor_supplied"]`` into an unmarked key and nothing would notice. So the rule is applied where every
warning is made: :meth:`UploadWarning.from_code` is wrapped once per process, and each warning is checked as it is
built, by whichever test caused it.

What is checked, for every warning
----------------------------------
* its code has a sentence (``RENDERED``);
* its ``message`` is exactly ``render_message(code, context)``;
* every string in its context sits under ``depositor_supplied`` (a dict of strings, lists of strings or lists of
  records of strings) or under a key in ``UNMARKED_STRING_KEYS`` whose value looks like what the key says
  (a public ref, an element symbol).

A violation raises at the producer, and is also recorded so that a producer that swallows the exception (a broad
``except``) still fails the test that caused it, through the autouse fixture in ``tests/conftest.py``.
"""

from __future__ import annotations

from tckdb_schemas.deposit_warning_messages import context_problems, render_message
from tckdb_schemas.upload_warning import UploadWarning

VIOLATIONS: list[str] = []
_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = UploadWarning.from_code.__func__  # type: ignore[attr-defined]

    def checked(cls, *, field, code, context=None):
        warning = original(cls, field=field, code=code, context=context)
        problems = context_problems(code, warning.context)
        if warning.message != render_message(code, warning.context):
            problems.append("message is not render_message(code, context)")
        if problems:
            text = f"{code} at {field!r}: {'; '.join(problems)}"
            VIOLATIONS.append(text)
            raise AssertionError(text)
        return warning

    UploadWarning.from_code = classmethod(checked)  # type: ignore[method-assign]
    _INSTALLED = True


def drain() -> list[str]:
    seen = list(VIOLATIONS)
    VIOLATIONS.clear()
    return seen
