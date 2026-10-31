"""Find the ways source code can make an ``UploadWarning`` whose message is not ``render_message(code, context)`` (#838).

``UploadWarning.from_code`` is the one constructor. Anything else lets a producer write the message itself, or change
it after the fact, so this finds, in a source file:

* a call of the class under any name it is bound to (``UploadWarning(...)``, an alias from ``import ... as``, a module
  attribute ``warning_module.UploadWarning(...)``, a name assigned from it);
* the other ways a pydantic class makes an instance: ``model_validate``, ``model_validate_json``, ``model_construct``,
  ``construct``, ``parse_obj``, ``parse_raw``, ``from_orm``;
* ``model_copy(update=...)`` that sets ``message``, ``code`` or ``context`` (or spreads an unknown dict), or passes an
  update it cannot read to something named like a warning (re-rooting ``field`` is the one legitimate use);
* assigning to a ``.message`` attribute, ``setattr(x, "message", ...)`` and ``object.__setattr__(x, "message", ...)``.

It reads source text and is itself tested on snippets, so a mutation of a producer cannot slip past it by a spelling
this file did not think of without a test here failing first.
"""

from __future__ import annotations

import ast
import re

_BUILDERS = frozenset(
    {
        "model_validate",
        "model_validate_json",
        "model_validate_strings",
        "model_construct",
        "construct",
        "parse_obj",
        "parse_raw",
        "from_orm",
    }
)
_NAME = "UploadWarning"


def _aliases(tree: ast.AST) -> set[str]:
    names = {_NAME}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for item in node.names:
                if item.name == _NAME:
                    names.add(item.asname or item.name)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _is_class(node.value, names):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id not in names:
                        names.add(target.id)
                        changed = True
    return names


def _is_class(node: ast.AST, names: set[str]) -> bool:
    return (isinstance(node, ast.Name) and node.id in names) or (
        isinstance(node, ast.Attribute) and node.attr == _NAME
    )


_WARNING_NAME = re.compile(r"(^w$|warn)", re.IGNORECASE)
_WARNING_KEYS = frozenset({"message", "code", "context"})


def _copies_a_warning_into_something_else(call: ast.Call) -> bool:
    """Whether a ``model_copy`` call can change what a warning says: it sets message, code or context, or it passes an
    update that cannot be read statically to something named like a warning."""
    update = next((kw.value for kw in call.keywords if kw.arg == "update"), None)
    if update is None:
        return False
    if isinstance(update, ast.Dict):
        keys = [k.value if isinstance(k, ast.Constant) else None for k in update.keys]
        return any(k in _WARNING_KEYS or k is None for k in keys)
    receiver = call.func.value if isinstance(call.func, ast.Attribute) else None
    return isinstance(receiver, ast.Name) and bool(_WARNING_NAME.search(receiver.id))


def violations(source: str, filename: str = "<source>") -> list[str]:
    tree = ast.parse(source)
    names = _aliases(tree)
    found: list[str] = []

    def add(node: ast.AST, what: str) -> None:
        found.append(f"{filename}:{node.lineno}: {what}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if _is_class(func, names):
                add(node, "builds an UploadWarning directly; use UploadWarning.from_code")
            elif isinstance(func, ast.Attribute) and _is_class(func.value, names) and func.attr in _BUILDERS:
                add(node, f"builds an UploadWarning with {func.attr}; use UploadWarning.from_code")
            elif isinstance(func, ast.Attribute) and func.attr == "model_copy":
                if _copies_a_warning_into_something_else(node):
                    add(node, "model_copy changes what a warning says; only its field may be re-rooted")
            elif isinstance(func, ast.Name) and func.id == "setattr" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and arg.value == "message":
                    add(node, "sets .message after the fact")
            elif isinstance(func, ast.Attribute) and func.attr == "__setattr__" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and arg.value == "message":
                    add(node, "sets .message after the fact")
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "message":
                    add(node, "assigns to a .message attribute")
    return found
