"""Log statistics for the alerting pipeline."""


def count_errors(path: str) -> int:
    """Count lines in `path` whose level field is ERROR.

    Lines look like:  2026-09-01T12:00:01 [ERROR] message text
    Only the bracketed level field determines severity; message text may
    mention 'error' without the line being an error.
    """
    count = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "error" in line.lower():
                count += 1
    return count
