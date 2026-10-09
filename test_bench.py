"""Fast harness regression tests; `bench.py verify` tests compiled applications."""

import json
from pathlib import Path
import tempfile
import unittest

import bench


class HarnessTests(unittest.TestCase):
    def test_deterministic_generation_and_shape(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = bench.generate(Path(a), modules=1, enforce_size=False)
            second = bench.generate(Path(b), modules=1, enforce_size=False)
            self.assertEqual(first, second)
            self.assertEqual(len({x["business_body_lines"] for x in first.values()}), 1)
            for variant in bench.VARIANTS:
                source = (Path(a) / variant / "domain0/src/workflow_000.rs").read_text()
                self.assertEqual(source.count("fn leaf_"), 8)
                self.assertEqual(source.count("fn stage_"), 7)
                self.assertEqual(source.count("?;"), 14)
            typed = first["eros-typed"]["files"]
            lean = first["eros-typed-lean"]["files"]
            self.assertEqual(
                {p: h for p, h in typed.items() if p.endswith(".rs")},
                {p: h for p, h in lean.items() if p.endswith(".rs")},
            )

    def test_size_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "insufficient"):
                bench.generate(Path(directory), modules=1)

    def test_precise_sets(self):
        sets = {tuple(sorted(g)) for r in range(8) for g in bench.groups(r)}
        self.assertEqual(len(sets), 17)
        for rotation in range(8):
            groups = bench.groups(rotation)
            self.assertEqual(set(sum(groups[:4], [])), set(range(8)))
            self.assertTrue(set(groups[0]) < set(groups[4]) < set(groups[6]))

    def test_mutation_roundtrip(self):
        for shared in (False, True):
            original = (
                bench.MODEL
                if shared
                else bench.workflow_source("anyhow", 0, 0)[0].replace(
                    "let salt = 1u64;", "let salt = 1u64; // BENCH_EDIT_LEAF", 1
                )
            )
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "source.rs"
                path.write_text(original)
                bench.mutate(path, True, shared)
                self.assertNotEqual(path.read_text(), original)
                self.assertEqual(len(path.read_text()), len(original))
                with self.assertRaises(ValueError):
                    bench.mutate(path, True, shared)
                bench.mutate(path, False, shared)
                self.assertEqual(path.read_text(), original)

    def test_rebuild_audit_rejects_dependency_build(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "log"
            log.write_text(" Running `rustc --crate-name model src/lib.rs`\n")
            self.assertEqual(bench.audit_rebuild(log, ["model"]), ["model"])
            with self.assertRaises(AssertionError):
                bench.audit_rebuild(log, ["domain0", "app"])

    def test_summary_rejects_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "raw.jsonl").write_text(json.dumps({"returncode": 1}) + "\n")
            with self.assertRaises(ValueError):
                bench.summarize(root)

    def test_summary_rejects_interrupted_or_duplicate_cells(self):
        cell = {
            "mode": "check-cold-target",
            "variant": "anyhow",
            "trial": 0,
            "position": 0,
        }
        other = {**cell, "variant": "eros-erased", "position": 1}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "plan.json").write_text(json.dumps({"cells": [cell, other]}))
            for rows in ([cell], [cell, cell], [other, cell]):
                (root / "raw.jsonl").write_text(
                    "".join(json.dumps({**row, "returncode": 0}) + "\n" for row in rows)
                )
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    bench.summarize(root)

    def test_command_directory_ignores_callers_cargo_config(self):
        self.assertNotEqual(bench.command_directory(), Path.cwd())
        self.assertEqual(bench.command_directory().parent, Path("/tmp"))
        result = bench.execute(["pwd"], capture_output=True)
        self.assertEqual(result.stdout.strip(), str(bench.command_directory()))

    def test_oracle_failure_categories(self):
        text = bench.oracle(37, 6)
        self.assertTrue(text.startswith("0 0 256 "))
        self.assertEqual(text.count("32"), 8)


if __name__ == "__main__":
    unittest.main()
