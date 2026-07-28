use shmem_pod::injection::{
    AdapterCallGate, AddressRole, BOOTSTRAP_PAGE_SIZE, BootstrapContext, BootstrapError,
    BootstrapFdError, BootstrapFlags, BootstrapStatus, ConnectorKind, DescriptorRole,
    parse_bootstrap_fd,
};

fn valid_context() -> BootstrapContext {
    BootstrapContext::new(
        ConnectorKind::Preload,
        BootstrapFlags::REQUIRED.union(BootstrapFlags::INHERIT_ACROSS_EXEC),
        10,
        11,
        12,
        BOOTSTRAP_PAGE_SIZE * 2,
        BOOTSTRAP_PAGE_SIZE * 4,
        7,
        0x1234,
        [0x5a; 32],
        [0xa5; 16],
    )
    .unwrap()
}

#[test]
fn stable_context_round_trip_and_c_layout() {
    let context = valid_context();
    context.validate().unwrap();
    assert_eq!(core::mem::size_of::<BootstrapContext>(), 160);
    assert_eq!(core::mem::align_of::<BootstrapContext>(), 8);
    assert_eq!(
        BootstrapContext::decode(&context.encode()).unwrap(),
        context
    );
}

#[test]
fn bootstrap_status_values_are_stable_for_c_callers() {
    assert_eq!(BootstrapStatus::Ok as i32, 0);
    assert_eq!(BootstrapStatus::InvalidContext as i32, -1);
    assert_eq!(BootstrapStatus::InvalidTransport as i32, -2);
    assert_eq!(BootstrapStatus::IncompatibleImage as i32, -3);
    assert_eq!(BootstrapStatus::Disabled as i32, -4);
    assert_eq!(BootstrapStatus::Reentrant as i32, -5);
    assert_eq!(BootstrapStatus::InitializationFailed as i32, -6);
}

#[test]
fn complete_artifact_length_need_not_be_page_aligned() {
    let mut context = valid_context();
    // One real reduced POC shape: a 4,096-byte header plus 8,112 code bytes.
    context.artifact_len = 12_208;
    context.validate().unwrap();
    assert_eq!(
        BootstrapContext::decode(&context.encode()).unwrap(),
        context
    );
}

#[test]
fn decoder_rejects_every_trust_relevant_field_class() {
    let bytes = valid_context().encode();
    for (offset, expected) in [
        (0, BootstrapError::BadMagic),
        (8, BootstrapError::UnsupportedVersion(0)),
        (10, BootstrapError::WrongSize(0)),
        (20, BootstrapError::NonzeroReserved),
        (144, BootstrapError::NonzeroReserved),
    ] {
        let mut malformed = bytes;
        malformed[offset] = 0;
        if offset == 20 || offset == 144 {
            malformed[offset] = 1;
        }
        assert_eq!(BootstrapContext::decode(&malformed), Err(expected));
    }
    assert_eq!(
        BootstrapContext::decode(&bytes[..bytes.len() - 1]),
        Err(BootstrapError::EncodedLength(159))
    );
}

#[test]
fn context_rejects_aliases_bounds_and_flag_address_disagreement() {
    let mut context = valid_context();
    context.code_fd = context.artifact_fd;
    assert_eq!(context.validate(), Err(BootstrapError::AliasedDescriptors));

    let mut context = valid_context();
    context.state_fd = 2;
    assert_eq!(
        context.validate(),
        Err(BootstrapError::InvalidDescriptor(DescriptorRole::State, 2))
    );

    let mut context = valid_context();
    context.artifact_len = BOOTSTRAP_PAGE_SIZE - 1;
    assert_eq!(
        context.validate(),
        Err(BootstrapError::InvalidArtifactLength(
            BOOTSTRAP_PAGE_SIZE - 1
        ))
    );

    let mut context = valid_context();
    context.state_len = 0;
    assert_eq!(
        context.validate(),
        Err(BootstrapError::InvalidStateLength(0))
    );

    let mut context = valid_context();
    context.flags |= BootstrapFlags::FIXED_CODE_ADDRESS.bits();
    assert_eq!(
        context.validate(),
        Err(BootstrapError::InvalidAddress(AddressRole::Code, 0))
    );

    let mut context = valid_context();
    context.code_address = BOOTSTRAP_PAGE_SIZE;
    assert_eq!(
        context.validate(),
        Err(BootstrapError::InvalidAddress(
            AddressRole::Code,
            BOOTSTRAP_PAGE_SIZE
        ))
    );

    let context = valid_context()
        .with_fixed_addresses(0x4000_0000, 0x4000_0000)
        .unwrap_err();
    assert_eq!(context, BootstrapError::AliasedFixedAddresses(0x4000_0000));

    let mut context = valid_context();
    context.control_fd = 20;
    assert_eq!(
        context.validate(),
        Err(BootstrapError::IncoherentControlTransport)
    );

    let mut context = valid_context();
    context.flags |= BootstrapFlags::SCM_RIGHTS_TRANSPORT.bits();
    assert_eq!(
        context.validate(),
        Err(BootstrapError::IncoherentControlTransport)
    );

    let context = valid_context().with_scm_rights_provenance(20).unwrap();
    context.validate().unwrap();

    assert_eq!(
        valid_context().with_scm_rights_provenance(2),
        Err(BootstrapError::InvalidDescriptor(
            DescriptorRole::Control,
            2
        ))
    );
    assert_eq!(
        valid_context().with_fixed_code_address(1),
        Err(BootstrapError::InvalidAddress(AddressRole::Code, 1))
    );
    assert_eq!(
        valid_context().with_fixed_state_address(1),
        Err(BootstrapError::InvalidAddress(AddressRole::State, 1))
    );

    let managed_flags =
        BootstrapFlags::FIXED_CODE_ADDRESS.union(BootstrapFlags::SCM_RIGHTS_TRANSPORT);
    assert_eq!(
        BootstrapContext::new(
            ConnectorKind::Preload,
            managed_flags,
            10,
            11,
            12,
            BOOTSTRAP_PAGE_SIZE * 2,
            BOOTSTRAP_PAGE_SIZE * 4,
            7,
            0x1234,
            [0x5a; 32],
            [0xa5; 16],
        ),
        Err(BootstrapError::BuilderManagedFlags(managed_flags.bits()))
    );
}

#[test]
fn context_rejects_default_identity_material() {
    let mut context = valid_context();
    context.generation = 0;
    assert_eq!(context.validate(), Err(BootstrapError::ZeroGeneration));

    let mut context = valid_context();
    context.api_fingerprint = [0; 16];
    assert_eq!(context.validate(), Err(BootstrapError::ZeroApiFingerprint));

    let mut context = valid_context();
    context.artifact_sha256 = [0; 32];
    assert_eq!(context.validate(), Err(BootstrapError::ZeroArtifactDigest));

    let mut context = valid_context();
    context.instance_nonce = [0; 16];
    assert_eq!(context.validate(), Err(BootstrapError::ZeroInstanceNonce));
}

#[test]
fn environment_fd_parser_is_strict_and_bounded() {
    assert_eq!(parse_bootstrap_fd(b"3"), Ok(3));
    assert_eq!(parse_bootstrap_fd(b"2147483647"), Ok(i32::MAX));
    assert_eq!(parse_bootstrap_fd(b""), Err(BootstrapFdError::Empty));
    assert_eq!(
        parse_bootstrap_fd(b"03"),
        Err(BootstrapFdError::LeadingZero)
    );
    assert_eq!(parse_bootstrap_fd(b" 3"), Err(BootstrapFdError::NonDecimal));
    assert_eq!(parse_bootstrap_fd(b"+3"), Err(BootstrapFdError::NonDecimal));
    assert_eq!(
        parse_bootstrap_fd(b"3\0"),
        Err(BootstrapFdError::NonDecimal)
    );
    assert_eq!(
        parse_bootstrap_fd(b"2147483648"),
        Err(BootstrapFdError::Overflow)
    );
    assert_eq!(
        parse_bootstrap_fd(b"2"),
        Err(BootstrapFdError::StandardIo(2))
    );
}

#[test]
fn admission_gate_disables_new_calls_without_invalidating_live_calls() {
    let gate = AdapterCallGate::new();
    let first = gate.try_enter().unwrap();
    let second = gate.try_enter().unwrap();
    assert_eq!(gate.active_calls(), 2);
    assert_eq!(gate.disable(), 2);
    assert!(gate.is_disabled());
    assert!(gate.try_enter().is_none());
    drop(first);
    assert_eq!(gate.active_calls(), 1);
    drop(second);
    assert_eq!(gate.active_calls(), 0);
}

#[test]
fn post_fork_reset_requires_a_disabled_quiescent_gate() {
    let gate = AdapterCallGate::new();
    assert_eq!(gate.disable(), 0);
    assert!(gate.is_disabled());
    unsafe { gate.reset_after_fork() }.unwrap();
    assert_eq!(gate.active_calls(), 0);
    assert!(!gate.is_disabled());
    assert!(gate.try_enter().is_some());
}

#[test]
fn post_fork_reset_fails_closed_on_non_quiescent_state() {
    let gate = AdapterCallGate::new();
    let live = gate.try_enter().unwrap();
    assert_eq!(gate.disable(), 1);
    let error = unsafe { gate.reset_after_fork() }.unwrap_err();
    assert!(error.was_disabled());
    assert_eq!(error.active_calls(), 1);
    drop(live);
    unsafe { gate.reset_after_fork() }.unwrap();

    let gate = AdapterCallGate::new();
    let error = unsafe { gate.reset_after_fork() }.unwrap_err();
    assert!(!error.was_disabled());
    assert_eq!(error.active_calls(), 0);
}
