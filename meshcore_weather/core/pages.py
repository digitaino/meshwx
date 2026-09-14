"""Replies longer than one mesh message.

A reply is rendered in full, then cut into pages at the message budget on
item boundaries ("; ", " | ", ", ", then a space). Page 1 goes out as the
answer; each later page goes out when the same person says "more". Pages
are numbered so the reader knows what is left:

    ... Hail 1.00 in Albany (1/3) more
    ... (2/3) more
    ... (3/3)

Nothing is ever cut inside an item, and no page exceeds the budget, tag
included. A single item longer than a page is the one exception: it is cut
at the budget and continues on the next page.
"""

from __future__ import annotations

SEPARATORS = ("; ", " | ", ", ", " ")


def split_pages(text: str, budget: int) -> list[str]:
    """Cut `text` into pages of at most `budget` characters, tagged."""
    text = " ".join(text.split())          # phones wrap; newlines only waste a line
    if len(text) <= budget:
        return [text]
    pages = _split(text, budget - len(" (9/9) more"))
    if len(pages) >= 10:
        pages = _split(text, budget - len(" (99/99) more"))
    n = len(pages)
    return [p + (f" ({i}/{n}) more" if i < n else f" ({i}/{n})") for i, p in enumerate(pages, 1)]


def _split(text: str, room: int) -> list[str]:
    pages: list[str] = []
    rest = text
    while rest:
        if len(rest) <= room:
            pages.append(rest)
            break
        cut = rest[:room]
        pos = room
        for sep in SEPARATORS:
            i = cut.rfind(sep)
            if i >= room // 2:            # a boundary in the second half: use it
                pos = i
                break
        page = rest[:pos].rstrip(" ;|,")
        rest = rest[pos:].lstrip(" ;|,")
        if not page:                      # only separators before the cut: hard cut
            page, rest = rest[:room], rest[room:]
        pages.append(page)
    return pages
