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
at the budget and continues on the next page. With a byte budget as well (a
DM carries at most 156 UTF-8 bytes to a v1.15 phone) a page fits both, and a
cut never falls inside a multi-byte character.
"""

from __future__ import annotations

SEPARATORS = ("; ", " | ", ", ", " ")


def split_pages(text: str, budget: int, max_bytes: int | None = None) -> list[str]:
    """Cut `text` into pages of at most `budget` characters (and `max_bytes`
    UTF-8 bytes when given), tagged."""
    text = " ".join(text.split())          # phones wrap; newlines only waste a line
    if len(text) <= budget and (max_bytes is None or len(text.encode()) <= max_bytes):
        return [text]
    pages = _split(text, *_rooms(budget, max_bytes, " (9/9) more"))
    if len(pages) >= 10:
        pages = _split(text, *_rooms(budget, max_bytes, " (99/99) more"))
    n = len(pages)
    return [p + (f" ({i}/{n}) more" if i < n else f" ({i}/{n})") for i, p in enumerate(pages, 1)]


def _rooms(budget: int, max_bytes: int | None, tag: str) -> tuple[int, int | None]:
    return budget - len(tag), None if max_bytes is None else max_bytes - len(tag.encode())


def _take(rest: str, room: int, room_bytes: int | None) -> int:
    """How many leading characters of `rest` fit in `room` characters and
    `room_bytes` bytes. Whole characters only."""
    n = min(len(rest), room)
    if room_bytes is not None:
        used = 0
        for i, c in enumerate(rest[:n]):
            used += len(c.encode("utf-8", "replace"))
            if used > room_bytes:
                n = i
                break
    return max(1, n)


def _split(text: str, room: int, room_bytes: int | None = None) -> list[str]:
    pages: list[str] = []
    rest = text
    while rest:
        n = _take(rest, room, room_bytes)
        if n >= len(rest):
            pages.append(rest)
            break
        cut = rest[:n]
        pos = n
        for sep in SEPARATORS:
            i = cut.rfind(sep)
            if i >= n // 2:               # a boundary in the second half: use it
                pos = i
                break
        page = rest[:pos].rstrip(" ;|,")
        rest = rest[pos:].lstrip(" ;|,")
        if not page:                      # only separators before the cut: hard cut
            n = _take(rest, room, room_bytes)
            page, rest = rest[:n], rest[n:]
        pages.append(page)
    return pages
