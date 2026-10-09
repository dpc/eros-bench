#!/usr/bin/env python3
"""Explain ELF section sizes using the last scored release artifacts, without rebuilding."""

import argparse
from collections import Counter
import json
from pathlib import Path

import bench


def analyze(output, experiment=bench.PRIMARY):
    rows = [
        json.loads(line) for line in (output / "sizes.jsonl").read_text().splitlines()
    ]
    analysis = {}
    for variant in experiment.variants:
        row = next(
            row
            for row in reversed(rows)
            if row["variant"] == variant and row["profile"] == "release"
        )
        binary = (
            experiment.targets
            / "measure/build-release-cold-target"
            / variant
            / "release/app"
        )
        if bench.digest(binary.read_bytes()) != row["sha256"]:
            raise ValueError(f"artifact no longer matches the scored run: {binary}")
        commands = [
            ["size", "-A", str(binary)],
            ["readelf", "-rW", str(binary)],
            ["nm", "-S", "-C", str(binary)],
        ]
        sections = bench.execute(commands[0], capture_output=True).stdout
        relocations = bench.execute(commands[1], capture_output=True).stdout
        symbols = bench.execute(commands[2], capture_output=True).stdout
        adapter_pattern = " as eros::error_union::SendSyncError>::as_any"
        adapters = [line for line in symbols.splitlines() if adapter_pattern in line]
        counts = Counter(
            word
            for line in relocations.splitlines()
            for word in line.split()
            if word.startswith("R_")
        )
        analysis[variant] = {
            "sha256": row["sha256"],
            "commands": commands,
            "section_bytes": {
                parts[0]: int(parts[1])
                for line in sections.splitlines()
                if (parts := line.split()) and parts[0].startswith(".")
            },
            "relocation_counts": dict(sorted(counts.items())),
            "adapter_symbol_pattern": adapter_pattern,
            "adapter_symbol_count": len(adapters),
            "domain5_error0_adapter_definitions": [
                line for line in adapters if "<domain5::errors::Error0 " in line
            ],
            "size_A_stdout": sections,
        }
    bench.write(output / "elf-analysis.json", json.dumps(analysis, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--experiment", choices=["primary", "context"], default="primary"
    )
    args = parser.parse_args()
    with bench.exclusive():
        analyze(
            args.output.resolve(),
            bench.CONTEXT if args.experiment == "context" else bench.PRIMARY,
        )
