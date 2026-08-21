#![cfg_attr(not(windows), allow(dead_code))]

mod generated;

use generated::*;
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::slice;
use std::str;
use std::sync::{Mutex, OnceLock};

type HmacSha256 = Hmac<Sha256>;

const ABI_VERSION: u32 = 0x0002_0000;
const SHA256_LENGTH: usize = 32;
const MAX_TEXT_LENGTH: usize = 64 * 1024 * 1024;
const MAX_INPUT_LENGTH: usize = 1024 * 1024;
const MAX_OUTPUT_LENGTH: usize = 64 * 1024;
const MAX_CHANNELS: usize = 32;
const EMPTY_SETTINGS_HASH: &str =
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

const STATUS_OK: u32 = 0;
const STATUS_INPUT: u32 = 1;
#[cfg(all(not(windows), not(feature = "e2e-smoke")))]
const STATUS_PLATFORM: u32 = 2;
const STATUS_MODULE: u32 = 3;
const STATUS_IMAGE: u32 = 4;
const STATUS_DEBUGGER: u32 = 5;
const STATUS_WRITABLE_CODE: u32 = 6;
const STATUS_CODE_CHANGED: u32 = 7;
const STATUS_CANARY: u32 = 8;
const STATUS_FILE_CHANGED: u32 = 9;
const STATUS_NOT_INITIALIZED: u32 = 10;
const STATUS_KEY: u32 = 11;
const STATUS_INTERNAL: u32 = 12;
const STATUS_POLICY: u32 = 13;
const STATUS_CAPSULE: u32 = 14;

const ENVELOPE_VERSION: u8 = 2;
const FRAME_VERSION: u8 = 1;
const ARENA_FRAME_PATH: &str = "/api/v3/frame";
const READY_CAPSULE_KIND: u8 = 0x91;
const JUDGE_CAPSULE_KIND: u8 = 0x4c;
const CAPSULE_VERSION: u8 = 1;
const CAPSULE_MAGIC: [u8; 4] = [0xc7, 0x51, 0xa9, 0x05];

#[derive(Clone)]
struct PolicySpec {
    magic: [u8; 4],
    tag: u8,
    #[allow(dead_code)]
    // Retained so tests can verify that server/native selection stays in lockstep.
    selector: u8,
    #[allow(dead_code)]
    // Retained so tests can verify that server/native selection stays in lockstep.
    stride: u8,
    #[allow(dead_code)]
    // The encoded VM program is exercised by the native-policy regression tests.
    program: [u8; 11],
}

fn derive_policy_spec(nonce: &str) -> PolicySpec {
    let mut key = variant_key();
    let mut payload = Vec::with_capacity(FRAME_MAGIC.len() + nonce.len());
    payload.extend_from_slice(&FRAME_MAGIC);
    payload.extend_from_slice(nonce.as_bytes());
    let digest = hmac_sha256(&key, &payload);
    key.fill(0);

    let mut magic = [0u8; 4];
    for index in 0..magic.len() {
        magic[index] = digest[index] ^ FRAME_MAGIC[index];
    }
    let mut tag = digest[4] ^ PROFILE_TAG;
    if tag == 0 {
        tag = PROFILE_TAG | 1;
    }
    let selector = digest[5] | 0x80;
    let stride = digest[6] % 15 + 1;
    let program = [
        VM_SELECT_INDEX,
        tag,
        selector,
        stride,
        magic[0],
        magic[1],
        magic[2],
        magic[3],
        VM_VALIDATE,
        VM_EMIT,
        VM_HALT,
    ];
    PolicySpec {
        magic,
        tag,
        selector,
        stride,
        program,
    }
}

fn parse_policy_program(program: &[u8]) -> Result<PolicySpec, u32> {
    if program.len() != 11
        || program[0] != VM_SELECT_INDEX
        || program[8] != VM_VALIDATE
        || program[9] != VM_EMIT
        || program[10] != VM_HALT
        || program[1] == 0
        || program[3] == 0
    {
        return Err(STATUS_POLICY);
    }
    let program: [u8; 11] = program.try_into().map_err(|_| STATUS_POLICY)?;
    Ok(PolicySpec {
        magic: [program[4], program[5], program[6], program[7]],
        tag: program[1],
        selector: program[2],
        stride: program[3],
        program,
    })
}

struct ChannelState {
    channel_id: String,
    team_id: String,
    access_token: String,
    authenticated: bool,
    expires_at_ms: i64,
    request_key: [u8; SHA256_LENGTH],
    response_key: [u8; SHA256_LENGTH],
    sequence: u64,
    last_request_mac: [u8; SHA256_LENGTH],
    has_last_request: bool,
    pending_action: Option<u8>,
    pending_ticket: Option<String>,
    ready_capsule: Vec<u8>,
    judge_capsule: Vec<u8>,
    accepted_inputs: u64,
    finish_digest: Option<[u8; SHA256_LENGTH]>,
}

impl Drop for ChannelState {
    fn drop(&mut self) {
        self.request_key.fill(0);
        self.response_key.fill(0);
        self.last_request_mac.fill(0);
        self.team_id.clear();
        self.access_token.clear();
        self.ready_capsule.fill(0);
        self.judge_capsule.fill(0);
        if let Some(mut digest) = self.finish_digest.take() {
            digest.fill(0);
        }
    }
}

struct NativeState {
    canary_key: [u8; SHA256_LENGTH],
    canary: [u8; SHA256_LENGTH],
    canary_tag: [u8; SHA256_LENGTH],
    text_address: usize,
    text_length: usize,
    text_tag: [u8; SHA256_LENGTH],
    checks: u64,
    next_handle_nonce: u64,
    channels: HashMap<u64, ChannelState>,
}

static STATE: OnceLock<Mutex<NativeState>> = OnceLock::new();

/// Single opaque ABI entry point. Operation identifiers, field tags, and VM
/// opcodes are deterministically permuted for each release.
///
/// # Safety
/// Input and output pointers must reference their declared byte counts. The
/// optional handle destination must reference one writable `u64`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn r4_gate(
    operation: u32,
    handle: u64,
    input_pointer: *const u8,
    input_length: usize,
    output_pointer: *mut u8,
    output_capacity: usize,
    output_length: *mut usize,
    output_handle: *mut u64,
) -> u32 {
    let input = match unsafe { input(input_pointer, input_length, MAX_INPUT_LENGTH) } {
        Ok(value) => value,
        Err(status) => return status,
    };

    if operation == OP_ABI {
        return unsafe {
            emit(
                output_pointer,
                output_capacity,
                output_length,
                &ABI_VERSION.to_le_bytes(),
            )
        };
    }
    if operation == OP_INITIALIZE {
        return initialize(input);
    }

    with_state(|state| {
        let status = guard_locked(state);
        if status != STATUS_OK {
            return status;
        }
        match operation {
            OP_HEALTH => unsafe { emit(output_pointer, output_capacity, output_length, &[]) },
            OP_OPEN_CHANNEL => unsafe {
                open_channel(
                    state,
                    input,
                    output_handle,
                    output_pointer,
                    output_capacity,
                    output_length,
                )
            },
            OP_CREATE_REQUEST => unsafe {
                create_request(
                    state,
                    handle,
                    input,
                    output_pointer,
                    output_capacity,
                    output_length,
                )
            },
            OP_VERIFY_RESPONSE => unsafe {
                verify_response(
                    state,
                    handle,
                    input,
                    output_pointer,
                    output_capacity,
                    output_length,
                )
            },
            OP_VERIFY_HANDSHAKE => unsafe {
                verify_handshake(
                    state,
                    handle,
                    input,
                    output_pointer,
                    output_capacity,
                    output_length,
                )
            },
            OP_BUILD_ENVELOPE => unsafe {
                build_envelope(
                    state,
                    handle,
                    input,
                    output_pointer,
                    output_capacity,
                    output_length,
                )
            },
            OP_FINALIZE => unsafe {
                finalize_policy(
                    state,
                    handle,
                    input,
                    output_pointer,
                    output_capacity,
                    output_length,
                )
            },
            OP_CLOSE => {
                if let Some(mut channel) = state.channels.remove(&handle) {
                    channel.request_key.fill(0);
                    channel.response_key.fill(0);
                    unsafe { emit(output_pointer, output_capacity, output_length, &[]) }
                } else {
                    STATUS_KEY
                }
            }
            _ => STATUS_INPUT,
        }
    })
}

fn initialize(expected: &[u8]) -> u32 {
    if expected.len() != SHA256_LENGTH {
        return STATUS_INPUT;
    }
    if STATE.get().is_some() {
        return with_state(guard_locked);
    }

    #[cfg(windows)]
    {
        match initialize_windows(expected) {
            Ok(state) => {
                if STATE.set(Mutex::new(state)).is_err() {
                    return with_state(guard_locked);
                }
                with_state(guard_locked)
            }
            Err(status) => status,
        }
    }

    #[cfg(all(not(windows), feature = "e2e-smoke"))]
    {
        let _ = expected;
        match initialize_e2e_smoke() {
            Ok(state) => {
                if STATE.set(Mutex::new(state)).is_err() {
                    return with_state(guard_locked);
                }
                with_state(guard_locked)
            }
            Err(status) => status,
        }
    }

    #[cfg(all(not(windows), not(feature = "e2e-smoke")))]
    {
        let _ = expected;
        STATUS_PLATFORM
    }
}

unsafe fn open_channel(
    state: &mut NativeState,
    input: &[u8],
    output_handle: *mut u64,
    output_pointer: *mut u8,
    output_capacity: usize,
    output_length: *mut usize,
) -> u32 {
    if output_handle.is_null() || state.channels.len() >= MAX_CHANNELS {
        return STATUS_KEY;
    }
    let mut cursor = Cursor::new(input);
    let expires_at_ms = match cursor.i64() {
        Ok(value) => value,
        Err(status) => return status,
    };
    let channel_id = match cursor.string_u16(128) {
        Ok(value) if !value.is_empty() => value.to_owned(),
        _ => return STATUS_INPUT,
    };
    let request_key = match cursor.array_32() {
        Ok(value) => value,
        Err(status) => return status,
    };
    let response_key = match cursor.array_32() {
        Ok(value) => value,
        Err(status) => return status,
    };
    if !cursor.done() {
        return STATUS_INPUT;
    }

    for _ in 0..64 {
        let mut random = [0u8; 8];
        if random_bytes(&mut random).is_err() {
            return STATUS_INTERNAL;
        }
        let candidate = u64::from_le_bytes(random) ^ state.next_handle_nonce;
        state.next_handle_nonce = state.next_handle_nonce.wrapping_add(0x9e37_79b9_7f4a_7c15);
        if candidate != 0 && !state.channels.contains_key(&candidate) {
            state.channels.insert(
                candidate,
                ChannelState {
                    channel_id,
                    team_id: String::new(),
                    access_token: String::new(),
                    authenticated: false,
                    expires_at_ms,
                    request_key,
                    response_key,
                    sequence: 0,
                    last_request_mac: [0u8; SHA256_LENGTH],
                    has_last_request: false,
                    pending_action: None,
                    pending_ticket: None,
                    ready_capsule: Vec::new(),
                    judge_capsule: Vec::new(),
                    accepted_inputs: 0,
                    finish_digest: None,
                },
            );
            unsafe { output_handle.write(candidate) };
            return unsafe { emit(output_pointer, output_capacity, output_length, &[]) };
        }
    }
    STATUS_INTERNAL
}

unsafe fn create_request(
    state: &mut NativeState,
    handle: u64,
    input: &[u8],
    output_pointer: *mut u8,
    output_capacity: usize,
    output_length: *mut usize,
) -> u32 {
    let Some(channel) = state.channels.get_mut(&handle) else {
        return STATUS_KEY;
    };
    let mut cursor = Cursor::new(input);
    let sent_at_ms = match cursor.i64() {
        Ok(value) if value < channel.expires_at_ms => value,
        _ => return STATUS_KEY,
    };
    let method = match cursor.string_u16(16) {
        Ok(value) if !value.is_empty() => value,
        _ => return STATUS_INPUT,
    };
    let target = match cursor.string_u16(4096) {
        Ok(value) if value.starts_with('/') => value,
        _ => return STATUS_INPUT,
    };
    let body = match cursor.bytes_u32(MAX_INPUT_LENGTH) {
        Ok(value) => value,
        Err(status) => return status,
    };
    if !cursor.done() {
        return STATUS_INPUT;
    }
    if channel.pending_action.is_some() {
        return STATUS_KEY;
    }

    let method_upper = method.to_ascii_uppercase();
    let pending = if target == ARENA_FRAME_PATH {
        if method_upper != "POST" || !channel.authenticated {
            return STATUS_KEY;
        }
        let (action, ticket) = match inspect_frame_request(body) {
            Ok(value) => value,
            Err(status) => return status,
        };
        if action == FRAME_FINISH {
            let Some(expected) = channel.finish_digest else {
                return STATUS_POLICY;
            };
            let actual: [u8; SHA256_LENGTH] = Sha256::digest(body).into();
            if !fixed_time_eq(&expected, &actual) {
                return STATUS_POLICY;
            }
        }
        Some((action, ticket))
    } else {
        None
    };

    let sequence = match channel.sequence.checked_add(1) {
        Some(value) => value,
        None => return STATUS_KEY,
    };
    let previous = channel.last_request_mac;
    let previous_hex = if channel.has_last_request {
        hex(&previous)
    } else {
        "0".repeat(SHA256_LENGTH * 2)
    };
    let body_digest = Sha256::digest(body);
    let canonical = format!(
        "rythme-secure-channel-v3\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n",
        channel.channel_id,
        method_upper,
        target,
        sequence,
        sent_at_ms,
        hex(body_digest.as_slice()),
        previous_hex,
    );
    let signature = hmac_sha256(&channel.request_key, canonical.as_bytes());

    channel.sequence = sequence;
    channel.last_request_mac = signature;
    channel.has_last_request = true;
    if let Some((action, ticket)) = pending {
        channel.pending_action = Some(action);
        channel.pending_ticket = ticket;
    }

    let mut output = Vec::with_capacity(72);
    output.extend_from_slice(&sequence.to_le_bytes());
    output.extend_from_slice(&previous);
    output.extend_from_slice(&signature);
    unsafe { emit(output_pointer, output_capacity, output_length, &output) }
}

unsafe fn verify_response(
    state: &mut NativeState,
    handle: u64,
    input: &[u8],
    output_pointer: *mut u8,
    output_capacity: usize,
    output_length: *mut usize,
) -> u32 {
    let Some(channel) = state.channels.get_mut(&handle) else {
        return STATUS_KEY;
    };
    let mut cursor = Cursor::new(input);
    let sequence = match cursor.u64() {
        Ok(value) => value,
        Err(status) => return status,
    };
    let status_code = match cursor.i32() {
        Ok(value) => value,
        Err(status) => return status,
    };
    let body = match cursor.bytes_u32(MAX_INPUT_LENGTH) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let request_signature = match cursor.array_32() {
        Ok(value) => value,
        Err(status) => return status,
    };
    let provided = match cursor.array_32() {
        Ok(value) => value,
        Err(status) => return status,
    };
    if !cursor.done()
        || sequence != channel.sequence
        || !channel.has_last_request
        || !fixed_time_eq(&request_signature, &channel.last_request_mac)
    {
        return STATUS_INPUT;
    }
    let body_digest = Sha256::digest(body);
    let canonical = format!(
        "rythme-secure-channel-v3\n{}\n{}\n{}\n{}\n{}\n",
        channel.channel_id,
        sequence,
        status_code,
        hex(body_digest.as_slice()),
        hex(&request_signature),
    );
    let expected = hmac_sha256(&channel.response_key, canonical.as_bytes());
    if !fixed_time_eq(&expected, &provided) {
        return STATUS_KEY;
    }

    if let Some(action) = channel.pending_action {
        if (200..300).contains(&status_code) {
            let observation = match inspect_frame_response(
                body,
                action,
                channel.pending_ticket.as_deref(),
                &channel.team_id,
            ) {
                Ok(value) => value,
                Err(status) => return status,
            };
            match observation {
                FrameObservation::Ready(mut capsule) => {
                    channel.ready_capsule.fill(0);
                    channel.ready_capsule.clear();
                    channel.ready_capsule.append(&mut capsule);
                    channel.judge_capsule.fill(0);
                    channel.judge_capsule.clear();
                    channel.accepted_inputs = 0;
                    channel.finish_digest = None;
                }
                FrameObservation::Input(accepted) => {
                    let Some(total) = channel.accepted_inputs.checked_add(accepted as u64) else {
                        return STATUS_POLICY;
                    };
                    channel.accepted_inputs = total;
                }
                FrameObservation::Result(mut capsule) => {
                    channel.judge_capsule.fill(0);
                    channel.judge_capsule.clear();
                    channel.judge_capsule.append(&mut capsule);
                }
                FrameObservation::Finish => {
                    if let Some(mut digest) = channel.finish_digest.take() {
                        digest.fill(0);
                    }
                }
                FrameObservation::Other => {}
            }
        }
        channel.pending_action = None;
        channel.pending_ticket = None;
    }
    unsafe { emit(output_pointer, output_capacity, output_length, &[]) }
}

unsafe fn verify_handshake(
    state: &mut NativeState,
    handle: u64,
    input: &[u8],
    output_pointer: *mut u8,
    output_capacity: usize,
    output_length: *mut usize,
) -> u32 {
    let Some(channel) = state.channels.get_mut(&handle) else {
        return STATUS_KEY;
    };
    let mut cursor = Cursor::new(input);
    let team = match cursor.string_u16(512) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let token = match cursor.string_u16(512) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let client_public = match cursor.string_u16(4096) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let server_public = match cursor.string_u16(4096) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let client_nonce = match cursor.string_u16(512) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let server_nonce = match cursor.string_u16(512) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let expires_at_ms = match cursor.i64() {
        Ok(value) => value,
        Err(status) => return status,
    };
    let provided = match cursor.array_32() {
        Ok(value) => value,
        Err(status) => return status,
    };
    if !cursor.done() || expires_at_ms != channel.expires_at_ms {
        return STATUS_INPUT;
    }
    let canonical = format!(
        "rythme-channel-handshake-v3\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n{}\n",
        team,
        token,
        channel.channel_id,
        client_public,
        server_public,
        client_nonce,
        server_nonce,
        expires_at_ms,
    );
    let expected = hmac_sha256(&channel.response_key, canonical.as_bytes());
    if !fixed_time_eq(&expected, &provided) {
        return STATUS_KEY;
    }
    channel.team_id.clear();
    channel.team_id.push_str(team);
    channel.access_token.clear();
    channel.access_token.push_str(token);
    channel.authenticated = true;
    unsafe { emit(output_pointer, output_capacity, output_length, &[]) }
}

unsafe fn build_envelope(
    state: &mut NativeState,
    handle: u64,
    input: &[u8],
    output_pointer: *mut u8,
    output_capacity: usize,
    output_length: *mut usize,
) -> u32 {
    let Some(channel) = state.channels.get(&handle) else {
        return STATUS_KEY;
    };
    if !channel.authenticated || channel.pending_action.is_some() {
        return STATUS_KEY;
    }
    let mut cursor = Cursor::new(input);
    let ruleset = match cursor.string_u16(16) {
        Ok(value) if matches!(value, "osu" | "taiko" | "fruits" | "mania") => value,
        _ => return STATUS_INPUT,
    };
    let acronym = match cursor.string_u16(256) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let nonce = match cursor.string_u16(512) {
        Ok(value) if !value.is_empty() => value,
        _ => return STATUS_INPUT,
    };
    if !cursor.done() {
        return STATUS_INPUT;
    }
    let _ = ruleset;
    let spec = derive_policy_spec(nonce);

    let mut mods: Vec<&str> = if acronym == "NM" || acronym.is_empty() {
        Vec::new()
    } else {
        acronym.split(',').collect()
    };
    mods.sort_unstable();
    mods.dedup();
    if mods.len() > 12 || mods.iter().any(|value| !valid_mod_acronym(value)) {
        return STATUS_INPUT;
    }
    let mut profile = Vec::new();
    profile.push(mods.len() as u8);
    for value in mods {
        profile.push(value.len() as u8);
        profile.extend_from_slice(value.as_bytes());
    }
    let mut envelope = Vec::new();
    envelope.extend_from_slice(&spec.magic);
    envelope.push(ENVELOPE_VERSION);
    envelope.push(1);
    envelope.push(spec.tag);
    envelope.extend_from_slice(&(profile.len() as u16).to_le_bytes());
    envelope.extend_from_slice(&profile);
    unsafe { emit(output_pointer, output_capacity, output_length, &envelope) }
}

unsafe fn finalize_policy(
    state: &mut NativeState,
    handle: u64,
    input: &[u8],
    output_pointer: *mut u8,
    output_capacity: usize,
    output_length: *mut usize,
) -> u32 {
    if !input.is_empty() {
        return STATUS_INPUT;
    }
    let Some(channel) = state.channels.get_mut(&handle) else {
        return STATUS_KEY;
    };
    if !channel.authenticated
        || channel.pending_action.is_some()
        || channel.ready_capsule.is_empty()
        || channel.judge_capsule.is_empty()
        || channel.accepted_inputs == 0
    {
        return STATUS_POLICY;
    }
    let ready_raw = channel.ready_capsule.as_slice();
    let judge_raw = channel.judge_capsule.as_slice();
    let ready_payload = match open_capsule(ready_raw, READY_CAPSULE_KIND) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let judge_payload = match open_capsule(judge_raw, JUDGE_CAPSULE_KIND) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let ready = match parse_ready_capsule(ready_payload) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let judged = match parse_judge_capsule(judge_payload) {
        Ok(value) => value,
        Err(status) => return status,
    };
    if ready.team_id != judged.team_id
        || ready.heat_id != judged.heat_id
        || ready.ticket != judged.ticket
        || ready.team_id != channel.team_id
    {
        return STATUS_CAPSULE;
    }
    let claim = match execute_policy_vm(ready.ruleset, ready.envelope, ready.program) {
        Ok(value) => value,
        Err(status) => return status,
    };
    let proof = native_proof(ready_raw, judge_raw, &claim);

    let mut output = Vec::new();
    output.extend_from_slice(&FRAME_MAGIC);
    output.push(FRAME_VERSION);
    output.push(FRAME_FINISH);
    if append_u16_bytes(&mut output, ready_raw).is_err()
        || append_u16_bytes(&mut output, judge_raw).is_err()
        || append_u16_bytes(&mut output, claim.acronym.as_bytes()).is_err()
    {
        return STATUS_POLICY;
    }
    output.extend_from_slice(&claim.rate_milli.to_le_bytes());
    if append_u16_bytes(&mut output, EMPTY_SETTINGS_HASH.as_bytes()).is_err() {
        return STATUS_POLICY;
    }
    output.extend_from_slice(&proof);
    channel.finish_digest = Some(Sha256::digest(&output).into());
    unsafe { emit(output_pointer, output_capacity, output_length, &output) }
}

enum FrameObservation {
    Ready(Vec<u8>),
    Input(u32),
    Result(Vec<u8>),
    Finish,
    Other,
}

fn inspect_frame_request(raw: &[u8]) -> Result<(u8, Option<String>), u32> {
    let (action, mut cursor) = open_wire_frame(raw, None)?;
    let ticket = match action {
        FRAME_ENROLL => {
            if !cursor.done() {
                return Err(STATUS_INPUT);
            }
            None
        }
        FRAME_READY => {
            let ticket = cursor.string_u16(512)?.to_owned();
            if ticket.is_empty() {
                return Err(STATUS_INPUT);
            }
            let _ = cursor.bytes_u16(2048)?;
            if !cursor.done() {
                return Err(STATUS_INPUT);
            }
            Some(ticket)
        }
        FRAME_CUES | FRAME_RESULT => {
            let ticket = cursor.string_u16(512)?.to_owned();
            if ticket.is_empty() || !cursor.done() {
                return Err(STATUS_INPUT);
            }
            Some(ticket)
        }
        FRAME_INPUT => {
            let ticket = cursor.string_u16(512)?.to_owned();
            if ticket.is_empty() || cursor.u8()? > 3 {
                return Err(STATUS_INPUT);
            }
            let count = cursor.u16()? as usize;
            if !(1..=64).contains(&count) {
                return Err(STATUS_INPUT);
            }
            for _ in 0..count {
                let _ = cursor.u64()?;
                let _ = cursor.i32()?;
                let _ = cursor.string_u16(512)?;
                let _ = cursor.u16()?;
                match cursor.u8()? {
                    0 => {}
                    1 => {
                        let _ = cursor.u16()?;
                        let _ = cursor.u16()?;
                    }
                    _ => return Err(STATUS_INPUT),
                }
            }
            if !cursor.done() {
                return Err(STATUS_INPUT);
            }
            Some(ticket)
        }
        FRAME_FINISH => {
            let _ = cursor.bytes_u16(8192)?;
            let _ = cursor.bytes_u16(8192)?;
            let _ = cursor.string_u16(64)?;
            let _ = cursor.i32()?;
            let _ = cursor.string_u16(128)?;
            let _ = cursor.take(SHA256_LENGTH)?;
            if !cursor.done() {
                return Err(STATUS_INPUT);
            }
            None
        }
        _ => return Err(STATUS_INPUT),
    };
    Ok((action, ticket))
}

fn inspect_frame_response(
    raw: &[u8],
    expected_action: u8,
    pending_ticket: Option<&str>,
    team_id: &str,
) -> Result<FrameObservation, u32> {
    let (_, mut cursor) = open_wire_frame(raw, Some(expected_action))?;
    match expected_action {
        FRAME_READY => {
            if cursor.u8()? != 1 {
                return Err(STATUS_POLICY);
            }
            let _ = cursor.string_u16(64)?;
            let _ = cursor.i32()?;
            let _ = cursor.string_u16(128)?;
            let _ = cursor.i32()?;
            let capsule = cursor.bytes_u16(8192)?;
            if !cursor.done() {
                return Err(STATUS_INPUT);
            }
            let ready = parse_ready_capsule(open_capsule(capsule, READY_CAPSULE_KIND)?)?;
            if ready.team_id != team_id
                || Some(ready.ticket) != pending_ticket
                || ready.song_id.is_empty()
                || ready.chart_digest.is_empty()
            {
                return Err(STATUS_CAPSULE);
            }
            Ok(FrameObservation::Ready(capsule.to_vec()))
        }
        FRAME_INPUT => {
            let accepted = cursor.i32()?;
            let _ = cursor.string_u16(512)?;
            if accepted < 0 || !cursor.done() {
                return Err(STATUS_INPUT);
            }
            Ok(FrameObservation::Input(accepted as u32))
        }
        FRAME_RESULT => {
            let _ = cursor.i32()?;
            let _ = cursor.i32()?;
            let _ = cursor.i64()?;
            let _ = cursor.i64()?;
            let _ = cursor.i64()?;
            let _ = cursor.take(8)?;
            let _ = cursor.i32()?;
            let _ = cursor.string_u16(512)?;
            let capsule = cursor.bytes_u16(8192)?;
            if !cursor.done() {
                return Err(STATUS_INPUT);
            }
            let judged = parse_judge_capsule(open_capsule(capsule, JUDGE_CAPSULE_KIND)?)?;
            if judged.team_id != team_id || Some(judged.ticket) != pending_ticket {
                return Err(STATUS_CAPSULE);
            }
            Ok(FrameObservation::Result(capsule.to_vec()))
        }
        FRAME_FINISH => Ok(FrameObservation::Finish),
        FRAME_ENROLL | FRAME_CUES => Ok(FrameObservation::Other),
        _ => Err(STATUS_INPUT),
    }
}

fn open_wire_frame(raw: &[u8], expected_action: Option<u8>) -> Result<(u8, Cursor<'_>), u32> {
    if raw.len() < 6 || raw.len() > MAX_OUTPUT_LENGTH || raw[..4] != FRAME_MAGIC {
        return Err(STATUS_INPUT);
    }
    let action = raw[5];
    if raw[4] != FRAME_VERSION || expected_action.is_some_and(|expected| expected != action) {
        return Err(STATUS_INPUT);
    }
    Ok((action, Cursor::new(&raw[6..])))
}

fn append_u16_bytes(output: &mut Vec<u8>, value: &[u8]) -> Result<(), u32> {
    let length = u16::try_from(value.len()).map_err(|_| STATUS_POLICY)?;
    output.extend_from_slice(&length.to_le_bytes());
    output.extend_from_slice(value);
    Ok(())
}

struct ReadyCapsule<'a> {
    team_id: &'a str,
    heat_id: &'a str,
    ticket: &'a str,
    song_id: &'a str,
    ruleset: &'a str,
    chart_digest: &'a str,
    envelope: &'a [u8],
    program: &'a [u8],
}

struct JudgeCapsule<'a> {
    team_id: &'a str,
    heat_id: &'a str,
    ticket: &'a str,
}

fn open_capsule(raw: &[u8], expected_kind: u8) -> Result<&[u8], u32> {
    if raw.len() < 6 + SHA256_LENGTH || raw.len() > 8192 {
        return Err(STATUS_CAPSULE);
    }
    let payload = &raw[..raw.len() - SHA256_LENGTH];
    let provided = &raw[raw.len() - SHA256_LENGTH..];
    if payload[..4] != CAPSULE_MAGIC || payload[4] != expected_kind || payload[5] != CAPSULE_VERSION
    {
        return Err(STATUS_CAPSULE);
    }
    let mut key = capsule_key();
    let expected = hmac_sha256(&key, payload);
    key.fill(0);
    if !fixed_time_eq(&expected, provided) {
        return Err(STATUS_CAPSULE);
    }
    Ok(payload)
}

fn parse_ready_capsule(payload: &[u8]) -> Result<ReadyCapsule<'_>, u32> {
    let mut cursor = Cursor::new(&payload[6..]);
    let team_id = cursor.string_u16(512)?;
    let heat_id = cursor.string_u16(512)?;
    let ticket = cursor.string_u16(512)?;
    let song_id = cursor.string_u16(512)?;
    let ruleset = cursor.string_u16(16)?;
    let chart_digest = cursor.string_u16(512)?;
    let _nonce = cursor.string_u16(512)?;
    let _start_at_ms = cursor.i64()?;
    let envelope = cursor.bytes_u16(2048)?;
    let program = cursor.bytes_u16(256)?;
    if !cursor.done() {
        return Err(STATUS_CAPSULE);
    }
    Ok(ReadyCapsule {
        team_id,
        heat_id,
        ticket,
        song_id,
        ruleset,
        chart_digest,
        envelope,
        program,
    })
}

fn parse_judge_capsule(payload: &[u8]) -> Result<JudgeCapsule<'_>, u32> {
    let mut cursor = Cursor::new(&payload[6..]);
    let team_id = cursor.string_u16(512)?;
    let heat_id = cursor.string_u16(512)?;
    let ticket = cursor.string_u16(512)?;
    let _input_digest = cursor.string_u16(512)?;
    let _raw_basis = cursor.i64()?;
    if !cursor.done() {
        return Err(STATUS_CAPSULE);
    }
    Ok(JudgeCapsule {
        team_id,
        heat_id,
        ticket,
    })
}

#[derive(Debug)]
struct PolicyClaim {
    acronym: String,
    rate_milli: i32,
}

fn execute_policy_vm(ruleset: &str, envelope: &[u8], program: &[u8]) -> Result<PolicyClaim, u32> {
    if !matches!(ruleset, "osu" | "taiko" | "fruits" | "mania") {
        return Err(STATUS_POLICY);
    }
    let spec = parse_policy_program(program)?;
    let profiles = modifier_profiles(envelope, &spec)?;
    let mut selected: Option<Vec<String>> = None;
    let mut validated: Option<PolicyClaim> = None;
    let mut emitted = false;
    let mut pc = 0usize;
    while pc < program.len() {
        let opcode = program[pc];
        pc += 1;
        match opcode {
            VM_SELECT_INDEX => {
                if pc + 7 > program.len()
                    || program[pc] != spec.tag
                    || program[pc + 3..pc + 7] != spec.magic
                {
                    return Err(STATUS_POLICY);
                }
                let selector = program[pc + 1] as i8 as i32;
                let stride = program[pc + 2] as i32;
                pc += 7;
                let mut index = (selector + stride) % profiles.len() as i32;
                if index < 0 {
                    index += profiles.len() as i32;
                }
                selected = profiles.get(index as usize).cloned();
            }
            VM_VALIDATE => {
                let Some(mut mods) = selected.take() else {
                    return Err(STATUS_POLICY);
                };
                mods.sort_unstable();
                mods.dedup();
                let acronym = if mods.is_empty() {
                    "NM".to_owned()
                } else {
                    mods.join(",")
                };
                if acronym != "NM" && acronym != "HT" {
                    return Err(STATUS_POLICY);
                }
                validated = Some(PolicyClaim {
                    rate_milli: if acronym == "HT" { 750 } else { 1000 },
                    acronym,
                });
            }
            VM_EMIT => {
                if validated.is_none() {
                    return Err(STATUS_POLICY);
                }
                emitted = true;
            }
            VM_HALT => {
                if pc != program.len() || !emitted {
                    return Err(STATUS_POLICY);
                }
                return validated.ok_or(STATUS_POLICY);
            }
            _ => return Err(STATUS_POLICY),
        }
    }
    Err(STATUS_POLICY)
}

fn modifier_profiles(envelope: &[u8], spec: &PolicySpec) -> Result<Vec<Vec<String>>, u32> {
    if envelope.len() < 6 || envelope[..4] != spec.magic || envelope[4] != ENVELOPE_VERSION {
        return Err(STATUS_POLICY);
    }
    let count = envelope[5] as usize;
    if count == 0 || count > 16 {
        return Err(STATUS_POLICY);
    }
    let mut cursor = Cursor::new(&envelope[6..]);
    let mut profiles = Vec::new();
    for _ in 0..count {
        let tag = cursor.u8()?;
        let value = cursor.bytes_u16(512)?;
        if tag == spec.tag {
            profiles.push(parse_profile(value)?);
        }
    }
    if !cursor.done() || profiles.is_empty() {
        return Err(STATUS_POLICY);
    }
    Ok(profiles)
}

fn parse_profile(value: &[u8]) -> Result<Vec<String>, u32> {
    let mut cursor = Cursor::new(value);
    let count = cursor.u8()? as usize;
    if count > 12 {
        return Err(STATUS_POLICY);
    }
    let mut mods = Vec::new();
    for _ in 0..count {
        let length = cursor.u8()? as usize;
        let raw = cursor.take(length)?;
        let acronym = str::from_utf8(raw).map_err(|_| STATUS_POLICY)?;
        if !valid_mod_acronym(acronym) {
            return Err(STATUS_POLICY);
        }
        mods.push(acronym.to_owned());
    }
    if !cursor.done() {
        return Err(STATUS_POLICY);
    }
    Ok(mods)
}

fn native_proof(ready_raw: &[u8], judge_raw: &[u8], claim: &PolicyClaim) -> [u8; 32] {
    let ready_digest = Sha256::digest(ready_raw);
    let judge_digest = Sha256::digest(judge_raw);
    let mut payload = Vec::new();
    payload.extend_from_slice(&FRAME_MAGIC);
    payload.push(FRAME_FINISH);
    payload.extend_from_slice(ready_digest.as_slice());
    payload.extend_from_slice(judge_digest.as_slice());
    payload.push(claim.acronym.len() as u8);
    payload.extend_from_slice(claim.acronym.as_bytes());
    payload.extend_from_slice(&claim.rate_milli.to_le_bytes());
    payload.extend_from_slice(EMPTY_SETTINGS_HASH.as_bytes());
    let mut key = proof_key();
    let proof = hmac_sha256(&key, &payload);
    key.fill(0);
    proof
}

fn valid_mod_acronym(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 8
        && value
            .bytes()
            .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit())
}

struct Cursor<'a> {
    value: &'a [u8],
    offset: usize,
}

impl<'a> Cursor<'a> {
    fn new(value: &'a [u8]) -> Self {
        Self { value, offset: 0 }
    }

    fn take(&mut self, length: usize) -> Result<&'a [u8], u32> {
        let end = self.offset.checked_add(length).ok_or(STATUS_INPUT)?;
        if end > self.value.len() {
            return Err(STATUS_INPUT);
        }
        let result = &self.value[self.offset..end];
        self.offset = end;
        Ok(result)
    }

    fn u8(&mut self) -> Result<u8, u32> {
        Ok(self.take(1)?[0])
    }

    fn u16(&mut self) -> Result<u16, u32> {
        Ok(u16::from_le_bytes(
            self.take(2)?.try_into().map_err(|_| STATUS_INPUT)?,
        ))
    }

    fn u32(&mut self) -> Result<u32, u32> {
        Ok(u32::from_le_bytes(
            self.take(4)?.try_into().map_err(|_| STATUS_INPUT)?,
        ))
    }

    fn u64(&mut self) -> Result<u64, u32> {
        Ok(u64::from_le_bytes(
            self.take(8)?.try_into().map_err(|_| STATUS_INPUT)?,
        ))
    }

    fn i32(&mut self) -> Result<i32, u32> {
        Ok(i32::from_le_bytes(
            self.take(4)?.try_into().map_err(|_| STATUS_INPUT)?,
        ))
    }

    fn i64(&mut self) -> Result<i64, u32> {
        Ok(i64::from_le_bytes(
            self.take(8)?.try_into().map_err(|_| STATUS_INPUT)?,
        ))
    }

    fn bytes_u16(&mut self, maximum: usize) -> Result<&'a [u8], u32> {
        let length = self.u16()? as usize;
        if length > maximum {
            return Err(STATUS_INPUT);
        }
        self.take(length)
    }

    fn bytes_u32(&mut self, maximum: usize) -> Result<&'a [u8], u32> {
        let length = self.u32()? as usize;
        if length > maximum {
            return Err(STATUS_INPUT);
        }
        self.take(length)
    }

    fn string_u16(&mut self, maximum: usize) -> Result<&'a str, u32> {
        str::from_utf8(self.bytes_u16(maximum)?).map_err(|_| STATUS_INPUT)
    }

    fn array_32(&mut self) -> Result<[u8; 32], u32> {
        self.take(32)?.try_into().map_err(|_| STATUS_INPUT)
    }

    fn done(&self) -> bool {
        self.offset == self.value.len()
    }
}

unsafe fn emit(
    output_pointer: *mut u8,
    output_capacity: usize,
    output_length: *mut usize,
    value: &[u8],
) -> u32 {
    if output_length.is_null()
        || output_capacity > MAX_OUTPUT_LENGTH
        || output_capacity < value.len()
        || (!value.is_empty() && output_pointer.is_null())
    {
        return STATUS_INPUT;
    }
    if !value.is_empty() {
        unsafe { std::ptr::copy_nonoverlapping(value.as_ptr(), output_pointer, value.len()) };
    }
    unsafe { output_length.write(value.len()) };
    STATUS_OK
}

fn with_state(operation: impl FnOnce(&mut NativeState) -> u32) -> u32 {
    let Some(state) = STATE.get() else {
        return STATUS_NOT_INITIALIZED;
    };
    match state.lock() {
        Ok(mut state) => operation(&mut state),
        Err(_) => STATUS_INTERNAL,
    }
}

fn guard_locked(state: &mut NativeState) -> u32 {
    #[cfg(windows)]
    if debugger_present() {
        return STATUS_DEBUGGER;
    }

    let canary_tag = hmac_sha256(&state.canary_key, &state.canary);
    if !fixed_time_eq(&canary_tag, &state.canary_tag) {
        return STATUS_CANARY;
    }

    state.checks = state.checks.wrapping_add(1);
    if state.checks & 31 == 1 {
        #[cfg(windows)]
        {
            if writable_code_page(state.text_address) {
                return STATUS_WRITABLE_CODE;
            }
            let text = unsafe {
                slice::from_raw_parts(state.text_address as *const u8, state.text_length)
            };
            let current = hmac_sha256(&state.canary_key, text);
            if !fixed_time_eq(&current, &state.text_tag) {
                return STATUS_CODE_CHANGED;
            }
        }
    }
    STATUS_OK
}

fn hmac_sha256(key: &[u8], payload: &[u8]) -> [u8; SHA256_LENGTH] {
    let mut mac = HmacSha256::new_from_slice(key).expect("HMAC accepts arbitrary key lengths");
    mac.update(payload);
    mac.finalize().into_bytes().into()
}

fn fixed_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    left.iter()
        .zip(right)
        .fold(0u8, |difference, (a, b)| difference | (a ^ b))
        == 0
}

fn hex(value: &[u8]) -> String {
    const TABLE: &[u8; 16] = b"0123456789abcdef";
    let mut result = String::with_capacity(value.len() * 2);
    for &byte in value {
        result.push(TABLE[(byte >> 4) as usize] as char);
        result.push(TABLE[(byte & 0x0f) as usize] as char);
    }
    result
}

unsafe fn input<'a>(pointer: *const u8, length: usize, maximum: usize) -> Result<&'a [u8], u32> {
    if length > maximum || (length != 0 && pointer.is_null()) {
        return Err(STATUS_INPUT);
    }
    if length == 0 {
        return Ok(&[]);
    }
    Ok(unsafe { slice::from_raw_parts(pointer, length) })
}

#[cfg(windows)]
fn initialize_windows(expected_file_hash: &[u8]) -> Result<NativeState, u32> {
    if debugger_present() {
        return Err(STATUS_DEBUGGER);
    }
    let module = current_module()?;
    let path = module_path(module)?;
    let file = std::fs::read(path).map_err(|_| STATUS_MODULE)?;
    let file_hash = Sha256::digest(&file);
    if !fixed_time_eq(file_hash.as_slice(), expected_file_hash) {
        return Err(STATUS_FILE_CHANGED);
    }

    let (text_address, text_length) = unsafe { pe_text_range(module as usize) }?;
    if writable_code_page(text_address) {
        return Err(STATUS_WRITABLE_CODE);
    }

    let mut canary_key = [0u8; SHA256_LENGTH];
    let mut canary = [0u8; SHA256_LENGTH];
    random_bytes(&mut canary_key)?;
    random_bytes(&mut canary)?;
    let canary_tag = hmac_sha256(&canary_key, &canary);
    let text = unsafe { slice::from_raw_parts(text_address as *const u8, text_length) };
    let text_tag = hmac_sha256(&canary_key, text);
    let mut handle_seed = [0u8; 8];
    random_bytes(&mut handle_seed)?;

    Ok(NativeState {
        canary_key,
        canary,
        canary_tag,
        text_address,
        text_length,
        text_tag,
        checks: 0,
        next_handle_nonce: u64::from_le_bytes(handle_seed),
        channels: HashMap::new(),
    })
}

#[cfg(windows)]
fn random_bytes(output: &mut [u8]) -> Result<(), u32> {
    if output.len() > u32::MAX as usize {
        return Err(STATUS_INPUT);
    }
    let status = unsafe {
        BCryptGenRandom(
            std::ptr::null_mut(),
            output.as_mut_ptr(),
            output.len() as u32,
            BCRYPT_USE_SYSTEM_PREFERRED_RNG,
        )
    };
    if status < 0 {
        Err(STATUS_INTERNAL)
    } else {
        Ok(())
    }
}

#[cfg(all(not(windows), feature = "e2e-smoke"))]
fn initialize_e2e_smoke() -> Result<NativeState, u32> {
    let mut canary_key = [0u8; SHA256_LENGTH];
    let mut canary = [0u8; SHA256_LENGTH];
    let mut handle_seed = [0u8; 8];
    random_bytes(&mut canary_key)?;
    random_bytes(&mut canary)?;
    random_bytes(&mut handle_seed)?;
    let canary_tag = hmac_sha256(&canary_key, &canary);

    Ok(NativeState {
        canary_key,
        canary,
        canary_tag,
        text_address: 0,
        text_length: 0,
        text_tag: [0u8; SHA256_LENGTH],
        checks: 0,
        next_handle_nonce: u64::from_le_bytes(handle_seed),
        channels: HashMap::new(),
    })
}

#[cfg(all(not(windows), feature = "e2e-smoke"))]
fn random_bytes(output: &mut [u8]) -> Result<(), u32> {
    use std::io::Read;

    std::fs::File::open("/dev/urandom")
        .and_then(|mut source| source.read_exact(output))
        .map_err(|_| STATUS_INTERNAL)
}

#[cfg(all(not(windows), not(feature = "e2e-smoke")))]
fn random_bytes(_output: &mut [u8]) -> Result<(), u32> {
    Err(STATUS_PLATFORM)
}

#[cfg(windows)]
fn debugger_present() -> bool {
    unsafe {
        if IsDebuggerPresent() != 0 {
            return true;
        }
        let process = GetCurrentProcess();
        let mut remote = 0i32;
        if CheckRemoteDebuggerPresent(process, &mut remote) != 0 && remote != 0 {
            return true;
        }

        let mut debug_port = 0usize;
        if NtQueryInformationProcess(
            process,
            PROCESS_DEBUG_PORT,
            &mut debug_port as *mut usize as *mut std::ffi::c_void,
            size_of::<usize>() as u32,
            std::ptr::null_mut(),
        ) >= 0
            && debug_port != 0
        {
            return true;
        }
        let mut debug_object = 0usize;
        if NtQueryInformationProcess(
            process,
            PROCESS_DEBUG_OBJECT_HANDLE,
            &mut debug_object as *mut usize as *mut std::ffi::c_void,
            size_of::<usize>() as u32,
            std::ptr::null_mut(),
        ) >= 0
            && debug_object != 0
        {
            return true;
        }
        let mut debug_flags = 1u32;
        if NtQueryInformationProcess(
            process,
            PROCESS_DEBUG_FLAGS,
            &mut debug_flags as *mut u32 as *mut std::ffi::c_void,
            size_of::<u32>() as u32,
            std::ptr::null_mut(),
        ) >= 0
            && debug_flags == 0
        {
            return true;
        }
    }
    peb_debug_flags()
}

#[cfg(all(windows, target_arch = "x86_64"))]
fn peb_debug_flags() -> bool {
    let peb: usize;
    unsafe {
        std::arch::asm!("mov {}, gs:[0x60]", out(reg) peb, options(nostack, preserves_flags, readonly));
        if peb == 0 {
            return false;
        }
        let being_debugged = *((peb + 2) as *const u8);
        let global_flags = *((peb + 0xbc) as *const u32);
        being_debugged != 0 || global_flags & 0x70 != 0
    }
}

#[cfg(all(windows, not(target_arch = "x86_64")))]
fn peb_debug_flags() -> bool {
    false
}

#[cfg(windows)]
fn current_module() -> Result<*mut std::ffi::c_void, u32> {
    let mut module = std::ptr::null_mut();
    let address = r4_gate as *const () as *const u16;
    let ok = unsafe {
        GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            address,
            &mut module,
        )
    };
    if ok == 0 || module.is_null() {
        Err(STATUS_MODULE)
    } else {
        Ok(module)
    }
}

#[cfg(windows)]
fn module_path(module: *mut std::ffi::c_void) -> Result<std::path::PathBuf, u32> {
    use std::os::windows::ffi::OsStringExt;
    let mut buffer = vec![0u16; 32_768];
    let length = unsafe { GetModuleFileNameW(module, buffer.as_mut_ptr(), buffer.len() as u32) };
    if length == 0 || length as usize >= buffer.len() {
        return Err(STATUS_MODULE);
    }
    buffer.truncate(length as usize);
    Ok(std::ffi::OsString::from_wide(&buffer).into())
}

#[cfg(windows)]
fn writable_code_page(address: usize) -> bool {
    let mut info = MemoryBasicInformation::default();
    let read = unsafe {
        VirtualQuery(
            address as *const std::ffi::c_void,
            &mut info,
            size_of::<MemoryBasicInformation>(),
        )
    };
    if read == 0 {
        return true;
    }
    matches!(
        info.protect & 0xff,
        PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY
    )
}

#[cfg(windows)]
unsafe fn pe_text_range(module: usize) -> Result<(usize, usize), u32> {
    if unsafe { read_u16(module, 0) } != IMAGE_DOS_SIGNATURE {
        return Err(STATUS_IMAGE);
    }
    let nt_offset = unsafe { read_u32(module, 0x3c) } as usize;
    if !(0x40..=0x10_0000).contains(&nt_offset)
        || unsafe { read_u32(module, nt_offset) } != IMAGE_NT_SIGNATURE
    {
        return Err(STATUS_IMAGE);
    }
    let file_header = nt_offset + 4;
    let section_count = unsafe { read_u16(module, file_header + 2) } as usize;
    let optional_length = unsafe { read_u16(module, file_header + 16) } as usize;
    if section_count == 0 || section_count > 96 {
        return Err(STATUS_IMAGE);
    }
    let optional_header = file_header + 20;
    if unsafe { read_u16(module, optional_header) } != IMAGE_NT_OPTIONAL_HDR64_MAGIC {
        return Err(STATUS_IMAGE);
    }
    let section_table = optional_header
        .checked_add(optional_length)
        .ok_or(STATUS_IMAGE)?;
    for index in 0..section_count {
        let section = section_table
            .checked_add(index.checked_mul(40).ok_or(STATUS_IMAGE)?)
            .ok_or(STATUS_IMAGE)?;
        let mut name = [0u8; 8];
        for (offset, value) in name.iter_mut().enumerate() {
            *value = unsafe { read_u8(module, section + offset) };
        }
        if !name.starts_with(b".text") {
            continue;
        }
        let virtual_size = unsafe { read_u32(module, section + 8) } as usize;
        let virtual_address = unsafe { read_u32(module, section + 12) } as usize;
        if virtual_size == 0 || virtual_size > MAX_TEXT_LENGTH {
            return Err(STATUS_IMAGE);
        }
        let address = module.checked_add(virtual_address).ok_or(STATUS_IMAGE)?;
        return Ok((address, virtual_size));
    }
    Err(STATUS_IMAGE)
}

#[cfg(windows)]
unsafe fn read_u8(base: usize, offset: usize) -> u8 {
    unsafe { *((base + offset) as *const u8) }
}
#[cfg(windows)]
unsafe fn read_u16(base: usize, offset: usize) -> u16 {
    unsafe { ((base + offset) as *const u16).read_unaligned() }
}
#[cfg(windows)]
unsafe fn read_u32(base: usize, offset: usize) -> u32 {
    unsafe { ((base + offset) as *const u32).read_unaligned() }
}

#[cfg(windows)]
use std::mem::size_of;

#[cfg(windows)]
const BCRYPT_USE_SYSTEM_PREFERRED_RNG: u32 = 0x0000_0002;
#[cfg(windows)]
const GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT: u32 = 0x0000_0002;
#[cfg(windows)]
const GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS: u32 = 0x0000_0004;
#[cfg(windows)]
const PROCESS_DEBUG_PORT: u32 = 7;
#[cfg(windows)]
const PROCESS_DEBUG_OBJECT_HANDLE: u32 = 0x1e;
#[cfg(windows)]
const PROCESS_DEBUG_FLAGS: u32 = 0x1f;
#[cfg(windows)]
const PAGE_READWRITE: u32 = 0x04;
#[cfg(windows)]
const PAGE_WRITECOPY: u32 = 0x08;
#[cfg(windows)]
const PAGE_EXECUTE_READWRITE: u32 = 0x40;
#[cfg(windows)]
const PAGE_EXECUTE_WRITECOPY: u32 = 0x80;
#[cfg(windows)]
const IMAGE_DOS_SIGNATURE: u16 = 0x5a4d;
#[cfg(windows)]
const IMAGE_NT_SIGNATURE: u32 = 0x0000_4550;
#[cfg(windows)]
const IMAGE_NT_OPTIONAL_HDR64_MAGIC: u16 = 0x20b;

#[cfg(windows)]
#[repr(C)]
#[derive(Default)]
struct MemoryBasicInformation {
    base_address: *mut std::ffi::c_void,
    allocation_base: *mut std::ffi::c_void,
    allocation_protect: u32,
    partition_id: u16,
    region_size: usize,
    state: u32,
    protect: u32,
    kind: u32,
}

#[cfg(windows)]
#[link(name = "bcrypt")]
unsafe extern "system" {
    fn BCryptGenRandom(
        algorithm: *mut std::ffi::c_void,
        output: *mut u8,
        output_length: u32,
        flags: u32,
    ) -> i32;
}

#[cfg(windows)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn CheckRemoteDebuggerPresent(
        process: *mut std::ffi::c_void,
        debugger_present: *mut i32,
    ) -> i32;
    fn GetCurrentProcess() -> *mut std::ffi::c_void;
    fn GetModuleFileNameW(module: *mut std::ffi::c_void, filename: *mut u16, size: u32) -> u32;
    fn GetModuleHandleExW(
        flags: u32,
        module_name: *const u16,
        module: *mut *mut std::ffi::c_void,
    ) -> i32;
    fn IsDebuggerPresent() -> i32;
    fn VirtualQuery(
        address: *const std::ffi::c_void,
        information: *mut MemoryBasicInformation,
        length: usize,
    ) -> usize;
}

#[cfg(windows)]
#[link(name = "ntdll")]
unsafe extern "system" {
    fn NtQueryInformationProcess(
        process: *mut std::ffi::c_void,
        information_class: u32,
        information: *mut std::ffi::c_void,
        information_length: u32,
        return_length: *mut u32,
    ) -> i32;
}

#[cfg(test)]
mod tests {
    use super::*;

    fn append_text(value: &mut Vec<u8>, text: &str) {
        value.extend_from_slice(&(text.len() as u16).to_le_bytes());
        value.extend_from_slice(text.as_bytes());
    }

    fn append_blob(value: &mut Vec<u8>, raw: &[u8]) {
        value.extend_from_slice(&(raw.len() as u16).to_le_bytes());
        value.extend_from_slice(raw);
    }

    fn seal_capsule(mut payload: Vec<u8>) -> Vec<u8> {
        let mut key = capsule_key();
        let tag = hmac_sha256(&key, &payload);
        key.fill(0);
        payload.extend_from_slice(&tag);
        payload
    }

    fn ready_capsule(spec: &PolicySpec, envelope: &[u8]) -> Vec<u8> {
        let mut value = Vec::new();
        value.extend_from_slice(&CAPSULE_MAGIC);
        value.push(READY_CAPSULE_KIND);
        value.push(CAPSULE_VERSION);
        append_text(&mut value, "team-1");
        append_text(&mut value, "heat-1");
        append_text(&mut value, "ticket-1");
        append_text(&mut value, "song-1");
        append_text(&mut value, "osu");
        append_text(&mut value, "chart-1");
        append_text(&mut value, "f4e2c1a0d8b69785");
        value.extend_from_slice(&1_700_000_000_000i64.to_le_bytes());
        append_blob(&mut value, envelope);
        append_blob(&mut value, &spec.program);
        seal_capsule(value)
    }

    fn judge_capsule() -> Vec<u8> {
        let mut value = Vec::new();
        value.extend_from_slice(&CAPSULE_MAGIC);
        value.push(JUDGE_CAPSULE_KIND);
        value.push(CAPSULE_VERSION);
        append_text(&mut value, "team-1");
        append_text(&mut value, "heat-1");
        append_text(&mut value, "ticket-1");
        append_text(&mut value, "input-digest");
        value.extend_from_slice(&900_000i64.to_le_bytes());
        seal_capsule(value)
    }

    fn channel(ready: Vec<u8>, judge: Vec<u8>, accepted_inputs: u64) -> ChannelState {
        ChannelState {
            channel_id: "channel-1".to_owned(),
            team_id: "team-1".to_owned(),
            access_token: "token-1".to_owned(),
            authenticated: true,
            expires_at_ms: i64::MAX,
            request_key: [1u8; SHA256_LENGTH],
            response_key: [2u8; SHA256_LENGTH],
            sequence: 0,
            last_request_mac: [0u8; SHA256_LENGTH],
            has_last_request: false,
            pending_action: None,
            pending_ticket: None,
            ready_capsule: ready,
            judge_capsule: judge,
            accepted_inputs,
            finish_digest: None,
        }
    }

    fn native_state(channel: ChannelState) -> NativeState {
        NativeState {
            canary_key: [0u8; SHA256_LENGTH],
            canary: [0u8; SHA256_LENGTH],
            canary_tag: [0u8; SHA256_LENGTH],
            text_address: 0,
            text_length: 0,
            text_tag: [0u8; SHA256_LENGTH],
            checks: 0,
            next_handle_nonce: 0,
            channels: HashMap::from([(7, channel)]),
        }
    }

    fn profile_value(mods: &[&str]) -> Vec<u8> {
        let mut value = vec![mods.len() as u8];
        for item in mods {
            value.push(item.len() as u8);
            value.extend_from_slice(item.as_bytes());
        }
        value
    }

    fn envelope(spec: &PolicySpec, profiles: &[&[&str]]) -> Vec<u8> {
        let mut value = Vec::new();
        value.extend_from_slice(&spec.magic);
        value.push(ENVELOPE_VERSION);
        value.push(profiles.len() as u8);
        for profile in profiles {
            let encoded = profile_value(profile);
            value.push(spec.tag);
            value.extend_from_slice(&(encoded.len() as u16).to_le_bytes());
            value.extend_from_slice(&encoded);
        }
        value
    }

    #[test]
    fn issued_policy_views_are_stable() {
        let spec = derive_policy_spec("f4e2c1a0d8b69785");
        let server_index = (spec.selector as usize + spec.stride as usize) % 3;
        let mut native_index = (spec.selector as i8 as i32 + spec.stride as i32) % 3;
        if native_index < 0 {
            native_index += 3;
        }
        assert_ne!(server_index, native_index as usize);

        let mut profiles: [&[&str]; 3] = [&[], &[], &[]];
        profiles[server_index] = &["DT"];
        profiles[native_index as usize] = &["HT"];
        let raw = envelope(&spec, &profiles);
        let claim = execute_policy_vm("osu", &raw, &spec.program).unwrap();
        assert_eq!(claim.acronym, "HT");
        assert_eq!(claim.rate_milli, 750);
    }

    #[test]
    fn vm_rejects_free_form_penalty_profile() {
        let spec = derive_policy_spec("0011223344556677");
        let raw = envelope(&spec, &[&["TP"]]);
        assert_eq!(
            execute_policy_vm("osu", &raw, &spec.program).unwrap_err(),
            STATUS_POLICY
        );
    }

    #[test]
    fn completion_requires_an_observed_input_transcript() {
        let spec = derive_policy_spec("f4e2c1a0d8b69785");
        let raw = envelope(&spec, &[&["HT"]]);
        let ready = ready_capsule(&spec, &raw);
        let judge = judge_capsule();
        let mut state = native_state(channel(ready, judge, 0));
        let mut output = [0u8; 16 * 1024];
        let mut length = 0usize;
        let status = unsafe {
            finalize_policy(
                &mut state,
                7,
                &[],
                output.as_mut_ptr(),
                output.len(),
                &mut length,
            )
        };
        assert_eq!(status, STATUS_POLICY);
        assert_eq!(length, 0);
    }

    #[test]
    fn completion_is_a_channel_bound_finish_frame() {
        let spec = derive_policy_spec("f4e2c1a0d8b69785");
        let raw = envelope(&spec, &[&["HT"]]);
        let ready = ready_capsule(&spec, &raw);
        let judge = judge_capsule();
        let mut state = native_state(channel(ready, judge, 1));
        let mut output = [0u8; 16 * 1024];
        let mut length = 0usize;
        let status = unsafe {
            finalize_policy(
                &mut state,
                7,
                &[],
                output.as_mut_ptr(),
                output.len(),
                &mut length,
            )
        };
        assert_eq!(status, STATUS_OK);
        let frame = &output[..length];
        assert_eq!(&frame[..4], &FRAME_MAGIC);
        assert_eq!(frame[4], FRAME_VERSION);
        assert_eq!(frame[5], FRAME_FINISH);
        assert_eq!(inspect_frame_request(frame).unwrap(), (FRAME_FINISH, None));
        assert_eq!(
            state.channels.get(&7).unwrap().finish_digest,
            Some(Sha256::digest(frame).into())
        );
    }

    #[test]
    fn release_key_shares_are_nontrivial_and_separate() {
        let capsule = capsule_key();
        let proof = proof_key();
        let variant = variant_key();
        assert_ne!(capsule, [0u8; 32]);
        assert_ne!(proof, [0u8; 32]);
        assert_ne!(capsule, proof);
        assert_ne!(variant, [0u8; 32]);
        assert_ne!(variant, capsule);
        assert_ne!(variant, proof);
    }

    #[test]
    fn hmac_matches_rfc_4231_case_one() {
        let key = [0x0bu8; 20];
        assert_eq!(
            hex(&hmac_sha256(&key, b"Hi There")),
            "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
        );
    }
}
