"""Merging k sorted streams of (key, value) records into one."""

from __future__ import annotations

from typing import Iterable, Iterator


def merge_streams(streams: list[Iterable[tuple[str, int]]]) -> Iterator[tuple[str, int]]:
    """Yield the merged stream in (key, value) sorted order.

    Every input stream is sorted by key. The merge reads all of them and
    re-sorts, so the output is ordered — but a key that appears in more than
    one stream comes out once per stream.
    """
    everything: list[tuple[str, int]] = []
    for stream in streams:
        everything.extend(stream)
    everything.sort(key=lambda kv: kv[0])
    yield from everything
