"""Parser for CCCBDB's Experimental index/discovery page.

The pages ``https://cccbdb.nist.gov/exp2x.asp`` (and the redirected
``exp1x.asp``) are CCCBDB's canonical "Experimental" sub-menu. They
list every experimental property table the database exposes via flat
GET URLs (``hf0kx.asp``, ``goodlistx.asp``, ``diplistx.asp``,
``pollistx.asp``, ``expdiatomicsx.asp``, …) along with their
human-readable section labels (``Enthalpy at 0 Kelvin``, ``Polarizability``,
…).

We do NOT crawl the linked pages from here — discovery is read-only
metadata. The parser exists so the importer can:

* enumerate which experimental pages CCCBDB currently exposes;
* compare that enumeration to :data:`PROPERTY_CONFIGS` (audit);
* and surface ``unconfigured_experimental_links`` to maintainers when
  CCCBDB ships new property tables.

The companion module :mod:`app.importers.cccbdb.property_config_audit`
joins the two halves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin


CCCBDB_BASE_URL = "https://cccbdb.nist.gov/"
EXPERIMENTAL_INDEX_URLS: tuple[str, ...] = (
    "https://cccbdb.nist.gov/exp2x.asp",
    "https://cccbdb.nist.gov/exp1x.asp",
)


# Map href → tckdb ``property_kind`` token used in
# ``PROPERTY_CONFIGS``. This is the static "if you see this URL, the
# config we'd want is named X" lookup; the audit module joins on the
# absolute_url for a stable identity. Only includes links that
# already correspond to a registered (or imminently registrable)
# property_kind in this codebase — missing entries are surfaced as
# ``unconfigured_experimental_links`` so the maintainer sees them.
_HREF_TO_TARGET_GUESS: dict[str, str] = {
    "hf0kx.asp": "hf_0",
    "goodlistx.asp": "hf_0_with_uncertainty",
    "diplistx.asp": "dipole",
    "pollistx.asp": "polarizability_iso",
    "quadlistx.asp": "quadrupole_moment",
    "expdiatomicsx.asp": "diatomic_spectroscopic",
    "exprot1x.asp": "rotational_constant_experimental",
    "expgeom1x.asp": "experimental_geometries",
    "expbondlengths1x.asp": "experimental_bond_lengths",
    "expangle1x.asp": "experimental_bond_angles",
    "expvibs1x.asp": "experimental_vibrations",
    "exptriatomicsx.asp": "triatomic_spectroscopic",
    "exppg1x.asp": "experimental_point_groups",
    "diatomicexpbondx.asp": "diatomic_bond_lengths",
    "ea1x.asp": "atomization_energy",
    "exprotbarx.asp": "internal_rotation_barrier",
    "refstatex.asp": "reference_states",
    "elecspinx.asp": "spin_splittings",
    "exp1x.asp": "experimental_per_species",
    "xpx.asp": "experimental_property_index",
}


# Hrefs known to require a POST against ``getformx.asp`` (CCCBDB
# form-only pages). The audit reports these as deferred rather than
# treating them as missing flat-table configs — the property-table
# importer is single-GET only and cannot drive a session-aware form.
FORM_ONLY_HREFS: frozenset[str] = frozenset({
    "exprot1x.asp",
    "expvibs1x.asp",
    "ea1x.asp",
    "expgeom1x.asp",
    "expbondlengths1x.asp",
    "expangle1x.asp",
    "exppg1x.asp",
    "exptriatomicsx.asp",
    "exprotbarx.asp",
    "exp1x.asp",
    "xpx.asp",
})


def is_form_only(href: str) -> bool:
    """Return True if ``href`` is one of the CCCBDB form-only pages
    that the property-table importer cannot ingest without a
    session-aware POST resolver."""

    if not href:
        return False
    cleaned = href.split("?", 1)[0].split("#", 1)[0]
    cleaned = cleaned.rsplit("/", 1)[-1].lower()
    return cleaned in FORM_ONLY_HREFS


def _guess_target(href: str) -> str | None:
    """Map an Experimental-section href onto a tckdb
    ``property_kind`` token. Strips the URL of any path prefix and
    query string before lookup, so ``xp1x.asp?prop=9`` and
    ``/xp1x.asp?prop=9`` both hit the same entry."""

    if not href:
        return None
    # Strip query string + fragments + leading slashes.
    cleaned = href.split("?", 1)[0].split("#", 1)[0]
    cleaned = cleaned.rsplit("/", 1)[-1].lower()
    return _HREF_TO_TARGET_GUESS.get(cleaned)


@dataclass(frozen=True)
class ExperimentalIndexLink:
    """One discovered link under the Experimental sub-menu."""

    section_path: tuple[str, ...]
    label: str
    href: str
    absolute_url: str
    target_guess: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "section_path": list(self.section_path),
            "label": self.label,
            "href": self.href,
            "absolute_url": self.absolute_url,
            "target_guess": self.target_guess,
        }


@dataclass
class ExperimentalIndex:
    """Aggregate result: links + source provenance."""

    source_url: str
    links: list[ExperimentalIndexLink] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def absolute_urls(self) -> set[str]:
        return {link.absolute_url for link in self.links}

    def by_target_guess(self) -> dict[str, list[ExperimentalIndexLink]]:
        out: dict[str, list[ExperimentalIndexLink]] = {}
        for link in self.links:
            if link.target_guess is None:
                continue
            out.setdefault(link.target_guess, []).append(link)
        return out

    def to_json(self) -> dict[str, object]:
        return {
            "source_url": self.source_url,
            "warnings": list(self.warnings),
            "links": [link.to_json() for link in self.links],
        }


# ---------------------------------------------------------------------------
# HTML walker
# ---------------------------------------------------------------------------


_WS_RE = re.compile(r"\s+")


def _clean_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


class _ExperimentalSectionWalker(HTMLParser):
    """Walks the CCCBDB ``exp2x.asp`` / ``exp1x.asp`` sub-menu and
    extracts every link below the ``Experimental`` branch.

    The HTML pattern in the menu is consistent:

    .. code-block:: html

        <ul>
          <li class="...class">
            <a Href="parent.asp">Section name</a>
            <ul>                        <!-- children of "Section name" -->
              <li class="...subclass">
                <a Href="child.asp">Leaf label</a>
              </li>
              <li class="...subclass">
                Plain text section
                <ul>
                  <li class="...">
                    <a Href="grandchild.asp">Leaf</a>
                  </li>
                </ul>
              </li>
            </ul>
          </li>
        </ul>

    The walker tracks one section label per ``<ul>`` depth. The label
    is taken from the *enclosing* ``<li>`` — either the leading
    ``<a>`` text or the leading plain-text. On ``</ul>`` the label is
    popped so siblings get a clean breadcrumb.

    Anchors that open a sub-``<ul>`` are demoted from "leaf link" to
    "section header" — they remain reachable via the section_path of
    their children, but are not double-counted in
    :attr:`ExperimentalIndex.links`.
    """

    def __init__(self, source_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.links: list[ExperimentalIndexLink] = []
        self.warnings: list[str] = []

        self._in_experimental = False
        self._depth_when_entered: int | None = None
        self._ul_depth = 0
        self._section_stack: list[str] = []

        # Per-<li> capture state. Each entry tracks:
        #   text_buf: plain text seen inside the li (before any <ul>)
        #   first_anchor_label: label of the first <a> inside this li
        #   first_anchor_emitted_index: index into self.links of that
        #     anchor's leaf-link entry (so we can remove it later
        #     when we discover this li wraps a nested <ul>).
        #   has_nested_ul: True once a <ul> has opened inside this li
        self._li_stack: list[dict[str, object]] = []

        # Per-<a> capture
        self._in_link = False
        self._current_href: str | None = None
        self._link_buf: list[str] = []

    # -- start/end --------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_d = {k.lower(): v for k, v in attrs}

        if tag == "a":
            href = attrs_d.get("href") or ""
            # Detect the sentinel: <a Href="expdatax.asp">Experimental</a>
            # The Experimental branch is what follows this anchor's
            # parent <li>; the actual sub-tree opens with the next
            # <ul> inside that <li>.
            if href.lower().startswith("expdatax.asp") \
                    and not self._in_experimental:
                self._in_experimental = True
                # Depth where we should pop out of the Experimental
                # branch is the current ul depth — once a </ul>
                # crosses back below this, we're outside again.
                self._depth_when_entered = self._ul_depth
                self._section_stack = ["Experimental"]
                # Note: we deliberately do NOT capture this anchor as
                # a leaf — "Experimental" is the section name itself.
                return

            self._in_link = True
            self._current_href = href
            self._link_buf = []
            return

        if tag == "ul":
            self._ul_depth += 1
            if not self._in_experimental:
                return
            # We are entering a nested list. The parent <li> on the
            # top of _li_stack now has a nested ul — promote its
            # label to a section name, and demote its leaf link (if
            # any) so it isn't double-counted as a data target.
            if self._li_stack:
                top = self._li_stack[-1]
                top["has_nested_ul"] = True
                label = (
                    top.get("first_anchor_label")
                    or _clean_text(str(top.get("text_buf") or ""))
                )
                if label and label != self._section_stack[-1:]:
                    # Skip duplicate root push (the "Experimental"
                    # sentinel anchor's <li> would otherwise push
                    # "Experimental" a second time).
                    if not self._section_stack \
                            or self._section_stack[-1] != label:
                        self._section_stack.append(label)
                        top["pushed_section"] = True
                # Demote the first anchor's leaf entry, if we emitted one.
                emit_idx = top.get("first_anchor_emitted_index")
                if isinstance(emit_idx, int) and emit_idx == len(self.links) - 1:
                    self.links.pop()
                    top["first_anchor_emitted_index"] = None
            return

        if tag == "li":
            # CCCBDB's menu HTML has no explicit </li> — an <li>
            # closes implicitly when the next <li> at the same depth
            # opens, or when its parent </ul> fires. Close any
            # prior <li> sitting at the current ul_depth before
            # opening a new one.
            self._implicit_close_li_at_depth(self._ul_depth)
            self._li_stack.append({
                "ul_depth": self._ul_depth,
                "text_buf": "",
                "first_anchor_label": None,
                "first_anchor_emitted_index": None,
                "has_nested_ul": False,
                "pushed_section": False,
            })
            return

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "a":
            if self._in_link:
                self._finish_link()
            return

        if tag == "ul":
            # Implicitly close any <li>s that lived inside this <ul>
            # before we collapse a depth level.
            self._implicit_close_li_at_depth(self._ul_depth)
            self._ul_depth -= 1
            if not self._in_experimental:
                return
            # Pop section labels back to the depth target. We pushed
            # at most one label per <ul>, so we pop one per </ul> as
            # long as we don't undershoot the root.
            entered = self._depth_when_entered or 0
            target_len = max(1, self._ul_depth - entered + 1)
            while len(self._section_stack) > target_len:
                self._section_stack.pop()
            if self._ul_depth <= entered:
                # We've left the Experimental sub-tree (the body
                # opens at depth = entered + 1, so depth == entered
                # means we just closed it).
                self._in_experimental = False
                self._depth_when_entered = None
                self._section_stack = []
            return

        if tag == "li":
            # Explicit </li> is rare in CCCBDB HTML but harmless to
            # honor. Pop the top entry if its depth matches.
            if self._li_stack \
                    and self._li_stack[-1]["ul_depth"] == self._ul_depth:
                self._li_stack.pop()
            return

    # -- data ------------------------------------------------------------

    def handle_data(self, data):
        if self._in_link:
            self._link_buf.append(data)
            return
        if self._li_stack and self._in_experimental:
            top = self._li_stack[-1]
            if not top.get("has_nested_ul"):
                top["text_buf"] = str(top.get("text_buf") or "") + data

    # -- helpers ---------------------------------------------------------

    def _implicit_close_li_at_depth(self, depth: int) -> None:
        """Pop any ``_li_stack`` entries at ``depth`` or deeper.

        Used when a new ``<li>`` opens at the same depth as a
        previously-open ``<li>`` (implicit close), and when ``</ul>``
        fires (closing every ``<li>`` that lived inside that ``<ul>``).

        If a popped ``<li>`` had pushed a section label, the section
        label is popped from ``_section_stack`` too — otherwise
        sibling ``<li>``s would inherit the previous sibling's
        section name.
        """

        while self._li_stack and self._li_stack[-1]["ul_depth"] >= depth:
            popped = self._li_stack.pop()
            if popped.get("pushed_section") and self._section_stack:
                self._section_stack.pop()

    def _finish_link(self) -> None:
        href = self._current_href or ""
        label = _clean_text("".join(self._link_buf))
        self._in_link = False
        self._current_href = None
        self._link_buf = []

        if not self._in_experimental:
            return
        if not href or not label:
            return
        if href.startswith("javascript:"):
            return

        absolute = urljoin(self.source_url, href)
        link = ExperimentalIndexLink(
            section_path=tuple(self._section_stack),
            label=label,
            href=href,
            absolute_url=absolute,
            target_guess=_guess_target(href),
        )
        self.links.append(link)

        if self._li_stack:
            top = self._li_stack[-1]
            if top.get("first_anchor_label") is None:
                top["first_anchor_label"] = label
                top["first_anchor_emitted_index"] = len(self.links) - 1


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_experimental_index_page(
    html: str,
    *,
    source_url: str = EXPERIMENTAL_INDEX_URLS[0],
) -> ExperimentalIndex:
    """Parse one CCCBDB experimental index page.

    :param html: Raw HTML of ``exp2x.asp`` / ``exp1x.asp``.
    :param source_url: Provenance only; used to resolve absolute URLs.
    :returns: An :class:`ExperimentalIndex` populated with every link
        discovered under the Experimental sub-tree.
    """

    walker = _ExperimentalSectionWalker(source_url=source_url)
    walker.feed(html)
    walker.close()

    index = ExperimentalIndex(source_url=source_url)
    seen: set[tuple[tuple[str, ...], str, str]] = set()
    for link in walker.links:
        key = (link.section_path, link.label, link.absolute_url)
        if key in seen:
            continue
        seen.add(key)
        index.links.append(link)
    index.warnings = list(walker.warnings)
    if not index.links:
        index.warnings.append(
            "no Experimental section found on page; "
            "did the upstream HTML drop the expdatax.asp anchor?"
        )
    return index


__all__ = [
    "CCCBDB_BASE_URL",
    "EXPERIMENTAL_INDEX_URLS",
    "ExperimentalIndex",
    "ExperimentalIndexLink",
    "parse_experimental_index_page",
]
