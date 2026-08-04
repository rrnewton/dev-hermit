from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest


SCRIPT = pathlib.Path(__file__).with_name("lint-rust-error-string-proxies.py")
SPEC = importlib.util.spec_from_file_location("rust_error_string_lint", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
LINT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LINT
SPEC.loader.exec_module(LINT)


class RustErrorStringProxyLintTest(unittest.TestCase):
    def kinds(self, source: str) -> list[str]:
        return [finding.kind for finding in LINT.scan_source(source)]

    def test_rejects_error_display_string_equality(self) -> None:
        source = """
fn classify(error: Errno) -> bool {
    error.to_string() == Errno::EFAULT.to_string()
}
"""
        self.assertEqual(
            self.kinds(source),
            ["error display string compared instead of typed error"],
        )

    def test_rejects_error_display_string_predicate_in_condition(self) -> None:
        source = """
fn retry(error: Error) {
    if error.to_string().contains("temporary") {
        retry_once();
    }
}
"""
        self.assertEqual(
            self.kinds(source),
            ["error display string used as a control-flow condition"],
        )

    def test_rejects_multiline_match_scrutinee(self) -> None:
        source = """
fn classify(operation_error: Error) {
    match operation_error
        .to_string()
        .as_str()
    {
        "missing" => recover(),
        _ => fail(),
    }
}
"""
        self.assertEqual(
            self.kinds(source),
            ["error display string used as a control-flow condition"],
        )

    def test_allows_typed_error_match(self) -> None:
        source = """
fn classify(error: Errno) -> bool {
    matches!(error, Errno::EFAULT)
}
"""
        self.assertEqual(self.kinds(source), [])

    def test_allows_error_rendering_outside_control_flow(self) -> None:
        source = """
fn report(error: Error) -> String {
    error.to_string()
}
"""
        self.assertEqual(self.kinds(source), [])

    def test_allows_map_err_conversion_inside_boolean_condition(self) -> None:
        source = """
fn query(image: Image) -> Result<(), String> {
    if !image.query().map_err(|error| error.to_string())? {
        return Err("query failed".into());
    }
    Ok(())
}
"""
        self.assertEqual(self.kinds(source), [])

    def test_allows_map_err_conversion_inside_match_scrutinee(self) -> None:
        source = """
fn run(builder: Builder) -> i32 {
    match builder.build().and_then(|runtime| {
        runtime.run().map_err(|error| std::io::Error::other(error.to_string()))
    }) {
        Ok(_) => 0,
        Err(_) => 1,
    }
}
"""
        self.assertEqual(self.kinds(source), [])

    def test_ignores_non_error_values_comments_and_strings(self) -> None:
        source = r'''
fn inspect(path: PathBuf) -> bool {
    // if error.to_string() == Errno::EFAULT.to_string() {}
    let example = "if error.to_string().contains(\"EFAULT\") {}";
    if path.to_string_lossy().contains("target") {
        return true;
    }
    !example.is_empty()
}
'''
        self.assertEqual(self.kinds(source), [])


if __name__ == "__main__":
    unittest.main()
