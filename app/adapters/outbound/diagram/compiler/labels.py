"""Turning a model-authored label into a drawn label.

One module because two compilers need the same trick and it is easy to get
subtly wrong in two places. The non-cloud prompt pack asks the model to put a
single emoji at the front of every label — `"👤 Claimant"`, `"📞 Report loss"` —
because a draw.io label renders as-is, so a leading emoji *is* the icon. Left
inline it renders at the label's own size (11-12pt) and reads as punctuation
rather than as an icon; lifted onto its own line at icon size it does the job
the pack asks of it.

Both moves need HTML labels, which is where the care is: the cell's ``value``
attribute is XML-escaped by ``emit.CellWriter``, so anything built here is
escaped a *second* time on the way out and must arrive already HTML-safe.
"""

from __future__ import annotations


def split_icon(label: str) -> tuple[str, str]:
    """Split a leading emoji off ``label``, returning ``(icon, rest)``.

    Keyed on "first whitespace-delimited token containing no letter or digit"
    rather than on Unicode emoji ranges. Emoji are a moving target — ``🕵️`` is
    three code points, ``🧑‍💼`` is five — and a range check that misses one
    silently leaves it inline at label size, which is the exact failure this
    exists to prevent. What the pack actually asks for is one symbol, a space,
    then words; anything alphanumeric is a name, not an icon.

    Returns ``("", label)`` when there is no icon, so callers can leave a label
    completely untouched rather than wrapping plain text in markup.
    """
    head, _, rest = label.strip().partition(" ")
    if head and rest.strip() and not any(c.isalnum() for c in head):
        return head, rest.strip()
    return "", label.strip()


def escape_html(text: str) -> str:
    """Escape ``text`` for use inside an HTML label.

    Without it a node called "Billing & Ops" reaches the browser as a bare
    ``&`` inside an HTML fragment.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def icon_over_name(
    label: str, *, icon_pt: int, bold: bool = False, fallback_icon: str = "",
) -> str:
    """``label`` as an HTML label with its emoji lifted above the name.

    ``fallback_icon`` supplies one for a label that has none — used where the
    *shape* already declared what the thing is (a flowchart ``actor`` node) and
    dropping the icon would leave nothing saying so.

    A label with no icon and no fallback comes back as plain text, unwrapped:
    a diagram that never used emoji should produce exactly the markup it did
    before this function existed.
    """
    icon, name = split_icon(label)
    icon = icon or fallback_icon
    if not icon:
        return label
    body = escape_html(name)
    if bold:
        body = f"<b>{body}</b>"
    return (
        f'<span style="font-size:{icon_pt}px;line-height:{icon_pt + 5}px">'
        f"{escape_html(icon)}</span><br>{body}"
    )
