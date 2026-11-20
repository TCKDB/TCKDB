"""MkDocs hook: point links that leave docs/ at the file on GitHub.

A page may link a repository file outside docs/ with a relative path such as
``../../backend/scripts/x.py``. That works on GitHub but not on the site, so
at build time the link is rewritten to its GitHub URL on main.
"""

import posixpath
import re

GITHUB = "https://github.com/TCKDB/TCKDB/blob/main/"
_LINK = re.compile(r"\]\((?!https?:|mailto:|#|/)([^)\s#]+)(#[^)\s]*)?\)")


def on_page_markdown(markdown, page, config, files):
    page_dir = posixpath.dirname(page.file.src_uri)

    def rewrite(match):
        target = posixpath.normpath(posixpath.join("docs", page_dir, match.group(1)))
        if target.startswith("docs/") or target.startswith(".."):
            return match.group(0)
        return f"]({GITHUB}{target}{match.group(2) or ''})"

    return _LINK.sub(rewrite, markdown)
