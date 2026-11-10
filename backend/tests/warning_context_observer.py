"""Hold every warning any producer builds, in any test, to the rule that a message is TCKDB's sentence (#838).

Why a runtime observer and not only a sweep
-------------------------------------------
The sweep in ``tests/api/test_warning_messages_from_codes.py`` deposits sentinel strings and reaches about 40 of the
~80 warning codes. A producer for a code the sweep does not reach can still move a label out of
``context["depositor_supplied"]`` into an unmarked key, or build the message some other way, and nothing would notice.
So the rule is applied where warnings are made, by whichever test caused them. Two hooks:

* :meth:`UploadWarning.from_code` is wrapped once per process: the ``depositor_supplied`` a producer passes must have
  the right shape (production logs and drops a bad entry; the test fails), and the finished context must pass
  :func:`context_problems` -- every string outside ``depositor_supplied`` is a value of the closed set its key names.
* ``tckdb_schemas.upload_warning._INSTANCE_CHECK`` is set, so every ``UploadWarning`` *instance* made from code under
  ``backend/app`` or ``tckdb_schemas`` -- by ``from_code``, ``from_stored``, ``__class__(...)``, ``model_validate``,
  anything -- must have ``message == render_message(code, context)``. The check is a test-time hook and not a model
  validator on purpose: a client parses a server's warnings into this class, possibly from another version of the
  package, and must read whatever message that server sent. Instances a test builds by hand (a stored row, a stale
  message) are not checked: the frame that made them is in a test file.

A violation raises at the producer, and is also recorded so that a producer that swallows the exception (a broad
``except``) still fails the test that caused it, through the autouse fixture in ``tests/conftest.py``.
"""

from __future__ import annotations

import sys

from tckdb_schemas import upload_warning
from tckdb_schemas.deposit_warning_messages import context_problems, depositor_supplied_problems, render_message
from tckdb_schemas.upload_warning import UploadWarning

VIOLATIONS: list[str] = []
_INSTALLED = False
#: ``UploadWarning.from_code`` as production runs it (no checks), for a test of what production does with bad input.
production_from_code = None
_CLOSED: dict | None = None


def _closed() -> dict:
    """The closed sets only the backend knows: the refusal-code catalogue and the log parser's method keys."""
    global _CLOSED
    if _CLOSED is None:
        from app.api.code_catalogue import catalogued_codes
        from app.services.gaussian_composite_parser import _LAYOUTS

        _CLOSED = {"refusal_code": frozenset(catalogued_codes()), "log_method": frozenset(_LAYOUTS)}
    return _CLOSED


def _made_by_a_producer() -> bool:
    """Whether the code that made the instance is under ``backend/app`` or ``tckdb_schemas`` (not a test, not a lib)."""
    frame = sys._getframe(1)
    while frame is not None:
        name = frame.f_code.co_filename
        skipped_here = name.endswith("upload_warning.py") and frame.f_code.co_name in ("from_code", "_held_to_the_rule_in_tests")
        if "/pydantic/" in name or name.endswith("warning_context_observer.py") or skipped_here:
            frame = frame.f_back
            continue
        return "/backend/app/" in name or "/tckdb_schemas/" in name
    return False


def _record(text: str) -> None:
    VIOLATIONS.append(text)
    raise AssertionError(text)


def _check_instance(warning: UploadWarning) -> None:
    if not _made_by_a_producer():
        return
    if warning.message != render_message(warning.code, warning.context):
        _record(f"{warning.code} at {warning.field!r}: message is not render_message(code, context)")
    problems = context_problems(warning.code, warning.context, closed=_closed())
    if problems:
        _record(f"{warning.code} at {warning.field!r}: {'; '.join(problems)}")


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = UploadWarning.from_code.__func__  # type: ignore[attr-defined]
    global production_from_code
    production_from_code = lambda **kw: original(UploadWarning, **kw)  # noqa: E731

    def checked(cls, *, field, code, context=None):
        problems = depositor_supplied_problems(context)
        if problems:
            _record(f"{code} at {field!r}: {'; '.join(problems)}")
        return original(cls, field=field, code=code, context=context)

    UploadWarning.from_code = classmethod(checked)  # type: ignore[method-assign]
    upload_warning._INSTANCE_CHECK = _check_instance
    _INSTALLED = True


def drain() -> list[str]:
    seen = list(VIOLATIONS)
    VIOLATIONS.clear()
    return seen
