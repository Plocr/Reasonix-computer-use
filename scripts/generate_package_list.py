"""Generate a compact package inventory for the self-contained release."""

from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    packages = []
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name") or distribution.name
        license_name = distribution.metadata.get("License-Expression") or distribution.metadata.get("License") or ""
        license_name = " ".join(str(license_name).split())[:100]
        packages.append((name, distribution.version, license_name or "See package metadata"))

    lines = [
        "# Third-party Python packages",
        "",
        "This inventory is generated from the embedded Windows runtime.",
        "Package licenses remain governed by their respective distributions.",
        "",
        "| Package | Version | License metadata |",
        "|---|---:|---|",
    ]
    for name, version, license_name in sorted(packages, key=lambda item: item[0].casefold()):
        lines.append(f"| {name} | {version} | {license_name.replace('|', '/')} |")
    Path(args.output).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
