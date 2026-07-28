fn main() {
    assert_eq!(
        std::env::var("CARGO_CFG_TARGET_OS").as_deref(),
        Ok("linux"),
        "the preload adapter requires Linux ELF semantics"
    );
    println!("cargo:rustc-cdylib-link-arg=-Wl,-z,nodelete");
}
