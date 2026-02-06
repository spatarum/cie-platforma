from __future__ import annotations

import re

from django.utils.html import escape
from django.utils.safestring import mark_safe


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def newsletter_text_to_html(text: str) -> str:
    """Convertește un text simplu în HTML sigur pentru newsletter.

    - Escape pentru conținut (prevenire XSS)
    - Linkuri în format Markdown: [text](https://exemplu.md)
    - Newline -> <br>

    Returnează un string marcat safe (conține doar <a> și <br>). 
    """
    if not text:
        return ""

    s = escape(text)

    def _repl(m: re.Match) -> str:
        label = m.group(1)
        url = m.group(2)
        return (
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
        )

    s = _MD_LINK_RE.sub(_repl, s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", "<br>\n")
    return mark_safe(s)
