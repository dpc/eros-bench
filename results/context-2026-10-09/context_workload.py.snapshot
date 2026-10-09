"""Separate high-density context experiment, derived from the unchanged workload."""

import json
from pathlib import Path
import re
import shutil
import tempfile

import bench


def attach(source, name, arguments, kind):
    """Match the synchronous explicit-format expansion of eros-macros 0.8.1."""
    pattern = re.compile(
        rf"(?P<docs>///[^\n]*\n)(?P<vis>pub )?fn {name}"
        r"(?P<signature>\([^\n]*\) -> [^\n{]+) \{\n(?P<body>.*?)^\}",
        re.MULTILINE | re.DOTALL,
    )

    def replace(match):
        docs, visibility, signature, body = (
            match["docs"],
            match["vis"] or "",
            match["signature"],
            match["body"],
        )
        if kind == "macro":
            return f"#[eros::context({arguments})]\n{match[0]}"
        call = "index, input" if name == "run" else "input"
        return (
            f"#[doc(hidden)]\n#[track_caller]\nfn __{name}_internal{signature} {{\n{body}}}\n"
            f"{docs}{visibility}fn {name}{signature} {{\n"
            f"    use eros::Context as _;\n"
            f"    __{name}_internal({call}).with_context(|| eros::__private::format!({arguments}))\n"
            "}"
        )

    if kind == "none":
        return source
    result, count = pattern.subn(replace, source)
    if count != 1:
        raise ValueError(f"expected exactly one generated function: {name}: {count}")
    return result


def context_tests(domain, modules, kind):
    """Independently compute all expected frames from category positions."""
    expected = (
        f"""
            let position = (usize::from(fail) + 8 - index % 8) % 8;
            let stages = [position / 2, 4 + position / 4, 6];
            let mut expected: Vec<String> = stages.iter().map(|stage|
                format!("domain={domain} workflow={{index}} stage={{stage}} seed={{seed}} fail={{fail}}")
            ).collect();
            expected.push(format!("domain={domain} workflow={{index}} seed={{seed}} fail={{fail}}"));
"""
        if kind != "none"
        else "            let expected: Vec<String> = Vec::new();\n"
    )
    return f"""use model::Request;

#[test]
fn every_context_frame_in_attachment_order() {{
    for seed in [0, 37, u64::MAX] {{
        for index in 0..{modules} {{
            assert!(domain{domain}::run(index, Request {{ seed, fail: 255 }}).is_ok());
            for fail in 0u8..8 {{
                let error = match domain{domain}::run(index, Request {{ seed, fail }}) {{
                    Err(error) => error,
                    Ok(_) => panic!("injected failure succeeded"),
                }};
                assert_eq!(error.to_string(), format!("D{domain}E{{fail}}"));
                let actual: Vec<String> = error.contexts().map(|frame| frame.value().to_string()).collect();
{expected}
                assert_eq!(actual, expected, "workflow={{index}} fail={{fail}} seed={{seed}}");
            }}
        }}
    }}
}}
""" + (lazy_test(kind) if domain == 0 and kind != "none" else "")


def lazy_test(kind):
    source = """/// Check that message formatting happens only on failure.
fn probe(input: model::Request) -> eros::Result<()> {
    if input.fail == 255 { Ok(()) } else { Err(eros::error!("probe")) }
}
"""
    source = attach(source, "probe", '"probe {}", counted(input.seed)', kind)
    return (
        """
use std::sync::atomic::{AtomicUsize, Ordering};
static FORMATTED: AtomicUsize = AtomicUsize::new(0);
fn counted(seed: u64) -> u64 {
    FORMATTED.fetch_add(1, Ordering::SeqCst);
    seed
}
"""
        + source
        + """
#[test]
fn successful_context_is_lazy() {
    assert!(probe(Request { seed: 37, fail: 255 }).is_ok());
    assert_eq!(FORMATTED.load(Ordering::SeqCst), 0);
    let error = probe(Request { seed: 37, fail: 0 }).unwrap_err();
    assert_eq!(FORMATTED.load(Ordering::SeqCst), 1);
    assert_eq!(error.contexts().next().unwrap().value().to_string(), "probe 37");
}
"""
    )


def generate(destination, modules=bench.MODULES, enforce_size=True):
    metadata = {}
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        primary = bench.generate(base, modules, enforce_size)
        for variant in bench.CONTEXT.variants:
            family, kind = variant.split("-")
            original = f"eros-{family}"
            root = destination / variant
            if root.exists():
                shutil.rmtree(root)
            shutil.copytree(base / original, root)
            for domain in range(bench.DOMAINS):
                for module in range(modules):
                    path = root / f"domain{domain}/src/workflow_{module:03}.rs"
                    source = path.read_text()
                    for stage in range(7):
                        arguments = (
                            f'"domain={domain} workflow={module} stage={stage} seed={{}} fail={{}}", '
                            "input.seed, input.fail"
                        )
                        source = attach(source, f"stage_{stage}", arguments, kind)
                    bench.write(path, source)
                path = root / f"domain{domain}/src/lib.rs"
                arguments = (
                    f'"domain={domain} workflow={{}} seed={{}} fail={{}}", '
                    "index, input.seed, input.fail"
                )
                bench.write(path, attach(path.read_text(), "run", arguments, kind))
                bench.write(
                    root / f"domain{domain}/tests/context.rs",
                    context_tests(domain, modules, kind),
                )
            files = sorted(p for p in root.rglob("*") if p.is_file())
            production = [
                p for p in files if p.suffix == ".rs" and "tests" not in p.parts
            ]
            sites = bench.DOMAINS * (modules * 7 + 1) if kind != "none" else 0
            metadata[variant] = {
                **primary[original],
                "uncontextualized_workflow_sha256": bench.digest(
                    b"".join(
                        p.read_bytes()
                        for p in sorted(
                            (base / original).glob("domain*/src/workflow_*.rs")
                        )
                    )
                ),
                "context_sites": sites,
                "failure_context_frames": 4 if sites else 0,
                "additional_expanded_functions": sites,
                "rust_code_lines": sum(
                    bench.lines_count(p.read_text()) for p in production
                ),
                "test_rust_code_lines": sum(
                    bench.lines_count(p.read_text())
                    for p in files
                    if p.suffix == ".rs" and "tests" in p.parts
                ),
                "files": {
                    str(p.relative_to(root)): bench.digest(p.read_bytes())
                    for p in files
                },
            }
    bench.write(destination / "manifest.json", json.dumps(metadata, indent=2) + "\n")
    return metadata
