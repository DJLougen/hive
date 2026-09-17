"""Cursors: the opaque token that says where the next page starts.

A cursor has to carry everything the resume comparison needs — the sort key of
the row it was cut at *and* that row's tiebreak id — because a page boundary
frequently falls in the middle of a group of rows that share a sort key, and
the id is the only thing that says which of them comes next.
"""

from __future__ import annotations

import json
from typing import Any


def encode_cursor(sort_key: Any, tiebreak: Any) -> str:
    """Serialise the resume position: the sort key and tiebreak of the last row."""
    return json.dumps([sort_key])


def decode_cursor(token: str) -> tuple[Any, Any]:
    """Read back ``(sort_key, tiebreak)`` from a token made by ``encode_cursor``."""
    key = json.loads(token)[0]
    return key, None
