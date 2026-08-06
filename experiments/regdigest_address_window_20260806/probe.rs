// Faithful extraction of detcore/src/regdigest.rs::canonicalize_and_hash_pairs at
// PR #1618 head f16d173b4. The digest is sha256(summary), so summary equality is
// exactly digest equality -- comparing summaries avoids pulling in detcore::Digest.
use std::collections::HashMap;
use std::fmt::Write as _;

const USER_VA_MIN: u64 = 0x1000;
const USER_VA_MAX: u64 = 0x0000_7fff_ffff_ffff;
fn is_address(value: u64) -> bool { (USER_VA_MIN..=USER_VA_MAX).contains(&value) }

fn canonicalize(pairs: &[(&str, u64)]) -> String {
    let mut ordinals: HashMap<u64, u32> = HashMap::new();
    let mut next_ordinal: u32 = 1;
    let mut summary = String::new();
    for (i, (name, value)) in pairs.iter().enumerate() {
        if i > 0 { summary.push(' '); }
        if is_address(*value) {
            let ordinal = *ordinals.entry(*value).or_insert_with(|| { let a = next_ordinal; next_ordinal += 1; a });
            let _ = write!(summary, "{}=a{}", name, ordinal);
        } else {
            let _ = write!(summary, "{}=v{}", name, value);
        }
    }
    summary
}

fn case(label: &str, a: &[(&str, u64)], b: &[(&str, u64)], want_equal: bool) {
    let (sa, sb) = (canonicalize(a), canonicalize(b));
    let eq = sa == sb;
    let verdict = if eq == want_equal { "as-designed" } else { "** UNEXPECTED **" };
    println!("  {:<52} equal={:<5} want={:<5} {}", label, eq, want_equal, verdict);
    if eq != want_equal || label.starts_with("HOLE") {
        println!("      A: {}\n      B: {}", sa, sb);
    }
}

fn main() {
    println!("== reproduce the module's own advertised properties ==");
    case("address-only diff (shifted backend)",
         &[("rdi",0x7fff_0000_1000),("rsi",42),("rsp",0x7fff_0000_2000),("rip",0x4010)],
         &[("rdi",0x5555_5555_1000),("rsi",42),("rsp",0x5555_5555_9000),("rip",0x8020)], true);
    case("non-address value diff (42 vs 43)",
         &[("rdi",0x7fff_0000_1000),("rsi",42)], &[("rdi",0x7fff_0000_1000),("rsi",43)], false);
    case("aliasing change (two regs share an addr vs not)",
         &[("rdi",0x7fff_1000),("rsi",0x7fff_1000)], &[("rdi",0x7fff_1000),("rsi",0x7fff_2000)], false);
    case("appearance-order change",
         &[("rdi",0x7fff_1000),("rsi",0x7fff_2000)], &[("rdi",0x7fff_2000),("rsi",0x7fff_1000)], true);

    println!("\n== the is_address() PROXY: non-pointer values inside the VA window ==");
    case("HOLE-1 read() count 4096 vs 8192 in rdx",
         &[("rdi",3),("rsi",0x7fff_0000_1000),("rdx",4096)],
         &[("rdi",3),("rsi",0x7fff_0000_1000),("rdx",8192)], false);
    case("HOLE-2 lseek offset 65536 vs 131072 in rsi",
         &[("rdi",3),("rsi",65536)], &[("rdi",3),("rsi",131072)], false);
    case("HOLE-3 timestamp-ish value 1_700_000_000 vs +1",
         &[("r12",1_700_000_000)], &[("r12",1_700_000_001)], false);
    case("CONTROL just below the window: 4095 vs 4094",
         &[("rdx",4095)], &[("rdx",4094)], false);
    case("CONTROL negative -1 (above VA_MAX) vs -2",
         &[("rdx",u64::MAX)], &[("rdx",u64::MAX-1)], false);

    println!("\n== the test fixture's own boundary: marker in %r15 (callee-saved) ==");
    // register_marker.c pins the marker in r15 and keeps it below 0x1000.
    case("AS SHIPPED  r15 marker 101 vs 202 (below 0x1000)",
         &[("r15",101),("rip",0x4010)], &[("r15",202),("rip",0x4010)], false);
    // Same test, same fixture shape, marker moved just above the window floor.
    case("PLANT       r15 marker 0x1000+101 vs 0x1000+202",
         &[("r15",0x1000+101),("rip",0x4010)], &[("r15",0x1000+202),("rip",0x4010)], false);

}
