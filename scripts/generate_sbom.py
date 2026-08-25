#!/usr/bin/env python3
"""Generate a Software Bill of Materials (SBOM) for Hive.

Produces CycloneDX-compatible JSON for supply-chain auditing.

Usage::

    python scripts/generate_sbom.py --output sbom.json
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from typing import Any

HIVE_DEPS = [
    "joblib",
    "numpy",
    "scikit-learn",
    "pydantic",
    "prometheus-client",
    "opentelemetry-api",
    "opentelemetry-sdk",
]


def _get_package_info(name: str) -> dict[str, Any]:
    try:
        dist = importlib.metadata.distribution(name)
        return {
            "name": dist.metadata["Name"],
            "version": dist.metadata["Version"],
            "license": dist.metadata.get("License", "unknown"),
            "home_page": dist.metadata.get("Home-page", ""),
        }
    except importlib.metadata.PackageNotFoundError:
        return {"name": name, "version": "not-installed", "license": "unknown", "home_page": ""}


def generate_sbom() -> dict[str, Any]:
    components = [_get_package_info(d) for d in HIVE_DEPS]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "serialNumber": "urn:uuid:hive-agent-memory",
        "version": 1,
        "metadata": {
            "timestamp": "2026-06-02T00:00:00Z",
            "tools": [{"name": "generate_sbom.py", "version": "1.0"}],
        },
        "components": components,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate Hive SBOM")
    p.add_argument("--output", default="sbom.json", help="Output JSON file")
    args = p.parse_args(argv)

    sbom = generate_sbom()
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(sbom, fh, indent=2)

    print(f"SBOM written to {args.output} ({len(sbom['components'])} components)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
