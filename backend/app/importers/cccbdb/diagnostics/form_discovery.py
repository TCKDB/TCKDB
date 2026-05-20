"""HTML form discovery for CCCBDB diagnostics.

Walks an HTML page with stdlib :mod:`html.parser` and returns every
``<form>`` element it sees, with each form's method, action, and all
``<input>`` / ``<select>`` / ``<button>`` fields. Useful for figuring
out *exactly* what the browser would POST when a user submits the
formula-entry form on ``exp1x.asp``.

Conservative: we capture what we observe and let the caller decide
which form to submit and with which values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class FormField:
    """One input/select/button on a form.

    :param name: ``name`` attribute (``None`` for unnamed fields,
        which the browser would not submit).
    :param kind: ``input``, ``select``, or ``button``.
    :param input_type: HTML ``type`` for ``<input>`` (``text``,
        ``hidden``, ``submit``, …). ``None`` for selects / buttons.
    :param default_value: ``value`` attribute or selected option.
    :param options: For ``<select>``, the list of option values.
    """

    name: str | None
    kind: str
    input_type: str | None = None
    default_value: str | None = None
    options: list[str] = field(default_factory=list)


@dataclass
class DiscoveredForm:
    """One ``<form>`` element on a page."""

    action: str | None
    method: str
    fields: list[FormField] = field(default_factory=list)

    def named_fields(self) -> dict[str, FormField]:
        """Return ``{name: field}`` for fields whose ``name`` is non-empty."""

        return {f.name: f for f in self.fields if f.name}


class _FormExtractor(HTMLParser):
    """Build a list of :class:`DiscoveredForm` from an HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[DiscoveredForm] = []
        self._current_form: DiscoveredForm | None = None
        self._in_select = False
        self._current_select: FormField | None = None
        self._capture_option_text = False
        self._current_option_value: str | None = None
        self._option_text_buf: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        tag = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form":
            self._current_form = DiscoveredForm(
                action=attr_map.get("action") or None,
                method=(attr_map.get("method") or "GET").upper(),
            )
            return
        if self._current_form is None:
            return
        if tag == "input":
            self._current_form.fields.append(
                FormField(
                    name=attr_map.get("name") or None,
                    kind="input",
                    input_type=(attr_map.get("type") or "text").lower(),
                    default_value=attr_map.get("value") or None,
                )
            )
        elif tag == "select":
            self._current_select = FormField(
                name=attr_map.get("name") or None,
                kind="select",
            )
            self._in_select = True
        elif tag == "option" and self._in_select and self._current_select is not None:
            self._current_option_value = (
                attr_map.get("value") or None
            )
            self._option_text_buf = []
            self._capture_option_text = True
        elif tag == "button":
            self._current_form.fields.append(
                FormField(
                    name=attr_map.get("name") or None,
                    kind="button",
                    input_type=(attr_map.get("type") or "submit").lower(),
                    default_value=attr_map.get("value") or None,
                )
            )
        elif tag == "textarea":
            self._current_form.fields.append(
                FormField(
                    name=attr_map.get("name") or None,
                    kind="input",
                    input_type="textarea",
                    default_value=attr_map.get("value") or None,
                )
            )

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "form":
            if self._current_form is not None:
                self.forms.append(self._current_form)
            self._current_form = None
        elif tag == "select" and self._in_select:
            if self._current_select is not None and self._current_form is not None:
                self._current_form.fields.append(self._current_select)
            self._in_select = False
            self._current_select = None
        elif tag == "option" and self._capture_option_text:
            text_value = "".join(self._option_text_buf).strip()
            value = self._current_option_value or text_value
            if self._current_select is not None:
                self._current_select.options.append(value)
            self._capture_option_text = False
            self._current_option_value = None
            self._option_text_buf = []

    def handle_data(self, data: str) -> None:
        if self._capture_option_text:
            self._option_text_buf.append(data)


def discover_forms(html: str) -> list[DiscoveredForm]:
    """Parse all ``<form>`` elements on ``html``.

    Returns an empty list when the page has no forms. The order of
    forms follows their order in the source.
    """

    extractor = _FormExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.forms
