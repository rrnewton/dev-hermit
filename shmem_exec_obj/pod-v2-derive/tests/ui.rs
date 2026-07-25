#[test]
fn rejects_unsupported_or_unsound_shapes() {
    let tests = trybuild::TestCases::new();
    tests.compile_fail("tests/ui/*.rs");
}
