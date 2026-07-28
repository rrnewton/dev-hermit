#![cfg(feature = "derive")]

use core::ptr::NonNull;
use shmem_pod::pod_api::{BindError, MethodResolver, MethodSignature};

mod first_order {
    #[shmem_pod::pod(
        namespace = "tests.counter.v1",
        bindings = Bindings,
        descriptor = API
    )]
    unsafe extern "C" {
        #[pod_method(id = 9, symbol = "test_read")]
        pub fn read(state: *mut u8) -> u64;
        #[pod_method(id = 2, symbol = "test_layout")]
        pub fn layout() -> u64;
        #[pod_method(id = 7, symbol = "test_add")]
        pub fn add(state: *mut u8, key: u64, delta: u64) -> i32;
    }
}

mod second_order {
    #[shmem_pod::pod(
        namespace = "tests.counter.v1",
        bindings = Bindings,
        descriptor = API
    )]
    unsafe extern "C" {
        #[pod_method(id = 7, symbol = "renamed_link_symbol")]
        pub fn add(state: *mut u8, key: u64, delta: u64) -> i32;
        #[pod_method(id = 9, symbol = "test_read")]
        pub fn read(state: *mut u8) -> u64;
        #[pod_method(id = 2, symbol = "test_layout")]
        pub fn layout() -> u64;
    }
}

mod changed_signature {
    #[shmem_pod::pod(
        namespace = "tests.counter.v1",
        bindings = Bindings,
        descriptor = API
    )]
    unsafe extern "C" {
        #[pod_method(id = 2, symbol = "test_layout")]
        pub fn layout() -> u64;
        #[pod_method(id = 7, symbol = "test_add")]
        pub fn add(state: *mut u8) -> u64;
        #[pod_method(id = 9, symbol = "test_read")]
        pub fn read(state: *mut u8) -> u64;
    }
}

unsafe extern "C" fn layout_entry() -> u64 {
    64
}

unsafe extern "C" fn add_entry(state: *mut u8, key: u64, delta: u64) -> i32 {
    if state.is_null() {
        return -1;
    }
    unsafe { state.cast::<u64>().write(key + delta) };
    0
}

unsafe extern "C" fn read_entry(state: *mut u8) -> u64 {
    unsafe { state.cast::<u64>().read() }
}

struct TestResolver {
    corrupt_signature: bool,
}

// SAFETY: each returned entry below is a live function with the signature
// declared for its method ID. The corruption mode returns an error instead.
unsafe impl MethodResolver for TestResolver {
    fn resolve(&self, id: u32, signature: MethodSignature) -> Result<NonNull<()>, BindError> {
        let (expected, pointer) = match id {
            2 => (
                MethodSignature::NoArgsU64,
                layout_entry as unsafe extern "C" fn() -> u64 as *mut (),
            ),
            7 => (
                MethodSignature::StateU64U64Status,
                add_entry as unsafe extern "C" fn(*mut u8, u64, u64) -> i32 as *mut (),
            ),
            9 => (
                MethodSignature::StateU64,
                read_entry as unsafe extern "C" fn(*mut u8) -> u64 as *mut (),
            ),
            _ => return Err(BindError::MissingMethod { id }),
        };
        let actual = if self.corrupt_signature && id == 7 {
            MethodSignature::StateU64
        } else {
            expected
        };
        if actual != signature {
            return Err(BindError::SignatureMismatch {
                id,
                expected: signature,
                actual,
            });
        }
        Ok(NonNull::new(pointer).unwrap())
    }
}

#[test]
fn fingerprint_and_method_order_depend_on_ids_not_source_order() {
    assert_eq!(first_order::API.fingerprint, second_order::API.fingerprint);
    assert_ne!(
        first_order::API.fingerprint,
        changed_signature::API.fingerprint
    );
    assert_eq!(
        first_order::API
            .methods
            .iter()
            .map(|method| method.id)
            .collect::<Vec<_>>(),
        [2, 7, 9]
    );
    assert_eq!(
        first_order::API.method(7).unwrap().signature,
        MethodSignature::StateU64U64Status
    );
    assert!(first_order::API.method(8).is_none());
}

#[test]
fn generated_bindings_dispatch_with_declared_function_types() {
    let resolver = TestResolver {
        corrupt_signature: false,
    };
    let bindings = unsafe { first_order::Bindings::bind(&resolver) }.unwrap();
    let mut state = 0_u64;
    assert_eq!(unsafe { (bindings.layout)() }, 64);
    assert_eq!(
        unsafe { (bindings.add)((&mut state as *mut u64).cast(), 20, 22) },
        0
    );
    assert_eq!(
        unsafe { (bindings.read)((&mut state as *mut u64).cast()) },
        42
    );
}

#[test]
fn generated_binding_rejects_signature_mismatch_before_conversion() {
    let resolver = TestResolver {
        corrupt_signature: true,
    };
    assert!(matches!(
        unsafe { first_order::Bindings::bind(&resolver) },
        Err(BindError::SignatureMismatch { id: 7, .. })
    ));
}
