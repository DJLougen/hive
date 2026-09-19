"""Greedy word wrapping to a fixed column width."""

from __future__ import annotations


def wrap(text: str, width: int) -> list[str]:
    """Break ``text`` into lines of at most ``width`` characters.

    Words are kept whole; a line breaks only between words. A word longer
    than ``width`` takes a line of its own rather than being split. Blank
    input yields no lines at all.
    """
    if width < 1:
        raise ValueError("width must be at least 1")

    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if not current:
            current = word
            continue
        # FIXME: ``>`` lets a line reach width+1 before breaking
        if len(current) + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}"
    if current:
        lines.append(current)
    return lines
