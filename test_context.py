"""Regression gates for isolated, matched context workloads."""

import json
from pathlib import Path
import tempfile
import unittest

import bench
import context_workload


class ContextTests(unittest.TestCase):
    def test_balanced_plan(self):
        cells = bench.measurement_plan(bench.CONTEXT, 6, 3, 12)["cells"]
        self.assertEqual(len(cells), 252)
        for mode in dict.fromkeys(row["mode"] for row in cells):
            rows = [row for row in cells if row["mode"] == mode]
            repeats = (
                3 if mode.startswith("build") else 12 if mode == "check-noop" else 6
            )
            self.assertEqual(len(rows), 6 * repeats)
            for trial in range(repeats):
                order = (
                    bench.CONTEXT.variants[trial % 6 :]
                    + bench.CONTEXT.variants[: trial % 6]
                )
                self.assertEqual(
                    [row["variant"] for row in rows if row["trial"] == trial],
                    list(order),
                )
            if not mode.startswith("build"):
                for variant in bench.CONTEXT.variants:
                    self.assertEqual(
                        sorted(
                            row["position"] for row in rows if row["variant"] == variant
                        ),
                        sorted(list(range(6)) * (repeats // 6)),
                    )
        with self.assertRaises(ValueError):
            bench.measurement_plan(bench.CONTEXT, 0, 3, 12)

    def test_published_primary_hashes_unchanged(self):
        expected = json.loads(
            (bench.ROOT / "results/2026-10-09/source-manifest.json").read_text()
        )
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(bench.generate(Path(directory)), expected)

    def test_determinism_and_matched_sources(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = bench.generate(Path(a), 1, False, bench.CONTEXT)
            second = bench.generate(Path(b), 1, False, bench.CONTEXT)
            self.assertEqual(first, second)
            for family in ("typed", "erased"):

                def source(kind, name):
                    return (Path(a) / f"{family}-{kind}" / name).read_text()

                for kind in ("none", "macro", "manual"):
                    entry = first[f"{family}-{kind}"]
                    self.assertEqual(
                        entry["context_sites"], 0 if kind == "none" else 64
                    )
                    self.assertEqual(
                        source(kind, "app/src/main.rs"),
                        source("none", "app/src/main.rs"),
                    )
                    self.assertEqual(source(kind, "model/src/lib.rs"), bench.MODEL)
                    workflow = source(kind, "domain0/src/workflow_000.rs")
                    self.assertEqual(workflow.count("?;"), 14)
                    with tempfile.TemporaryDirectory() as edit:
                        path = Path(edit) / "source.rs"
                        path.write_text(workflow)
                        bench.mutate(path, True)
                        bench.mutate(path, False)
                        self.assertEqual(path.read_text(), workflow)
                macro = source("macro", "domain0/src/workflow_000.rs")
                manual = source("manual", "domain0/src/workflow_000.rs")
                self.assertEqual(macro.count("#[eros::context("), 7)
                self.assertEqual(manual.count("#[track_caller]"), 7)
                # Every original concrete leaf body stays byte-identical.
                prefix = "/// Compose validated records"
                none = source("none", "domain0/src/workflow_000.rs")
                self.assertEqual(
                    none.split(prefix)[0], manual.split("#[doc(hidden)]")[0]
                )

    def test_representative_pinned_expansion(self):
        source = "/// Doc.\npub fn run(index: usize, input: Request) -> eros::Result<Record> {\n    body(index, input)\n}"
        expanded = context_workload.attach(
            source, "run", '"site {}", input.seed', "manual"
        )
        self.assertEqual(
            expanded,
            """#[doc(hidden)]
#[track_caller]
fn __run_internal(index: usize, input: Request) -> eros::Result<Record> {
    body(index, input)
}
/// Doc.
pub fn run(index: usize, input: Request) -> eros::Result<Record> {
    use eros::Context as _;
    __run_internal(index, input).with_context(|| eros::__private::format!("site {}", input.seed))
}""",
        )
        with self.assertRaises(ValueError):
            context_workload.attach(source, "missing", '"site"', "macro")

    def test_context_summary_rejects_incomplete_six_variants(self):
        cells = [
            {"mode": "check-noop", "variant": v, "trial": 0, "position": i}
            for i, v in enumerate(bench.CONTEXT.variants)
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bench.write(root / "plan.json", json.dumps({"cells": cells}))
            bench.write(
                root / "raw.jsonl",
                "".join(json.dumps({**r, "returncode": 0}) + "\n" for r in cells[:-1]),
            )
            with self.assertRaisesRegex(ValueError, "incomplete"):
                bench.summarize(root, bench.CONTEXT)

    def test_paths_are_disjoint(self):
        for experiment in (bench.PRIMARY, bench.CONTEXT):
            self.assertIn(
                str(experiment.generated),
                " ".join(
                    bench.command(
                        experiment.variants[0],
                        Path("/tmp/target"),
                        experiment=experiment,
                    )
                ),
            )
        self.assertNotEqual(bench.PRIMARY.targets, bench.CONTEXT.targets)


if __name__ == "__main__":
    unittest.main()
