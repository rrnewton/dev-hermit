use std::any::type_name;
use std::hint::black_box;
use std::time::Instant;

use reverie::GlobalTool;
use serde::Serialize;
use serde::de::DeserializeOwned;
use serde_json::json;

type Request = <detcore::GlobalState as GlobalTool>::Request;
type Response = <detcore::GlobalState as GlobalTool>::Response;

const WARMUPS: usize = 100_000;
const ITERATIONS: usize = 1_000_000;
const REPS: usize = 9;

fn encode<T: Serialize>(value: &T) -> Vec<u8> {
    bincode::serde::encode_to_vec(value, bincode::config::legacy()).unwrap()
}

fn decode<T: DeserializeOwned>(bytes: &[u8]) -> T {
    bincode::serde::decode_from_slice(bytes, bincode::config::legacy())
        .unwrap()
        .0
}

fn fixture() -> (Request, Response) {
    let request = serde_json::from_value(json!([
        {
            "syscalls": 1,
            "syscall_nanos": 1000,
            "rcbs": 64,
            "weighted_rcbs": null,
            "nondet_instrs": 0,
            "extra_nanos": 0,
            "starting_micros": 0,
            "multiplier": 1.0
        },
        {"creator": 1, "generation": 0},
        "GlobalTimeLowerBound"
    ]))
    .unwrap();
    let response =
        serde_json::from_value(json!([null, {"GlobalTimeLowerBound": 1_000_000}])).unwrap();
    (request, response)
}

fn direct_once(request: &Request, response: &Response) {
    black_box(request.clone());
    black_box(response.clone());
}

fn serde_once(request: &Request, response: &Response) {
    let request_bytes = black_box(encode(black_box(request)));
    let decoded_request: Request = black_box(decode(black_box(&request_bytes)));
    let response_bytes = black_box(encode(black_box(response)));
    let decoded_response: Response = black_box(decode(black_box(&response_bytes)));
    black_box(decoded_request);
    black_box(decoded_response);
}

fn run(name: &str, f: impl Fn()) -> f64 {
    let start = Instant::now();
    for _ in 0..ITERATIONS {
        f();
    }
    let ns = start.elapsed().as_nanos() as f64 / ITERATIONS as f64;
    println!("sample,{name},{ns:.6}");
    ns
}

fn quantile(sorted: &[f64], numerator: usize, denominator: usize) -> f64 {
    sorted[(sorted.len() - 1) * numerator / denominator]
}

fn summary(name: &str, mut samples: Vec<f64>) {
    samples.sort_by(f64::total_cmp);
    println!(
        "summary,{name},n={},median_ns={:.6},p25_ns={:.6},p75_ns={:.6},min_ns={:.6},max_ns={:.6}",
        samples.len(),
        quantile(&samples, 1, 2),
        quantile(&samples, 1, 4),
        quantile(&samples, 3, 4),
        samples[0],
        samples[samples.len() - 1]
    );
}

fn main() {
    let (request, response) = fixture();
    let request_bytes = encode(&request);
    let response_bytes = encode(&response);
    let request_back: Request = decode(&request_bytes);
    let response_back: Response = decode(&response_bytes);
    assert_eq!(request_back, request);
    assert_eq!(response_back, response);

    println!("meta,hermit_sha,75506005d873a76f62be00b1d82696188651047a");
    println!("meta,reverie_pinned_sha,0ae0c01b5e4c9fbf85c97adc66c2740f280727df");
    println!("meta,reverie_live_main_sha,6144323c5dab8b521278fce206f8774360c2b05f");
    println!("meta,request_type,{}", type_name::<Request>());
    println!("meta,response_type,{}", type_name::<Response>());
    println!("meta,request_bytes,{}", request_bytes.len());
    println!("meta,response_bytes,{}", response_bytes.len());
    println!("meta,warmups,{WARMUPS}");
    println!("meta,iterations_per_rep,{ITERATIONS}");
    println!("meta,reps,{REPS}");

    for _ in 0..WARMUPS {
        direct_once(&request, &response);
        serde_once(&request, &response);
    }

    let mut direct = Vec::with_capacity(REPS);
    let mut serde = Vec::with_capacity(REPS);
    for rep in 0..REPS {
        if rep % 2 == 0 {
            direct.push(run("direct_clone", || direct_once(&request, &response)));
            serde.push(run("bincode_roundtrip", || serde_once(&request, &response)));
        } else {
            serde.push(run("bincode_roundtrip", || serde_once(&request, &response)));
            direct.push(run("direct_clone", || direct_once(&request, &response)));
        }
    }
    summary("direct_clone", direct.clone());
    summary("bincode_roundtrip", serde.clone());
    let mut paired_delta: Vec<f64> = serde.iter().zip(&direct).map(|(s, d)| s - d).collect();
    paired_delta.sort_by(f64::total_cmp);
    println!(
        "summary,paired_delta,n={},median_ns={:.6},p25_ns={:.6},p75_ns={:.6}",
        paired_delta.len(),
        quantile(&paired_delta, 1, 2),
        quantile(&paired_delta, 1, 4),
        quantile(&paired_delta, 3, 4)
    );
}
