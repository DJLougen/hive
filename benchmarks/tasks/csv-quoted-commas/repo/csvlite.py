"""Minimal CSV line parser (we avoid the stdlib for embedded targets)."""


def parse_line(line: str) -> list[str]:
    """Parse one CSV line into fields."""
    line = line.rstrip("\r\n")
    return line.split(",")
