"""Closed integer intervals: parse, merge, format."""

from .merge import merge
from .model import Interval, covers, touches_or_overlaps
from .parse import format_intervals, parse_intervals

__all__ = ["Interval", "covers", "format_intervals", "merge", "parse_intervals",
           "touches_or_overlaps"]
