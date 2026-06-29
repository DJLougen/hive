#!/usr/bin/env python3
"""Extract GitHub release title and body for a semver tag.

Reads CHANGELOG.md for the release body and RELEASE_NOTES.md for a
human-readable title (Highlights line). Writes:

  release_name.txt   — e.g. "Hive v0.6.0 — Real-workload evaluation and API hardening"
  release_notes.md   — CHANGELOG section for the version
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _changelog_body(version: str, root: Path) -> str:
    content = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    pattern = rf"## \[{re.escape(version)}\].*?(?=\n## \[|\Z)"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(0).strip()
    return f"## [{version}]\n\nSee CHANGELOG.md for details."


def _release_title(version: str, root: Path) -> str:
    notes = (root / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    pattern = (
        rf"## v{re.escape(version)}[^\n]*\n+### Highlights\n+"
        r"((?:.+\n?)+?)(?=\n### |\n## |\Z)"
    )
    match = re.search(pattern, notes)
    if not match:
        return f"Hive v{version}"

    # First sentence / line of highlights becomes the subtitle.
    raw = " ".join(match.group(1).split())
    subtitle = raw.split(". ")[0].rstrip(".")
    if len(subtitle) > 90:
        subtitle = subtitle[:87].rstrip() + "..."
    return f"Hive v{version} — {subtitle}"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <version>  (e.g. 0.6.1)")

    version = sys.argv[1].lstrip("v")
    root = Path(__file__).resolve().parents[1]

    (root / "release_name.txt").write_text(_release_title(version, root) + "\n", encoding="utf-8")
    (root / "release_notes.md").write_text(_changelog_body(version, root) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
