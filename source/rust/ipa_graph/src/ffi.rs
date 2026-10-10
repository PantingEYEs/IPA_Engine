//! Stable C ABI for the Android UTF-16 adapter and other native hosts.
//!
//! Handles must come from `ipa_graph_open`, and the caller must finish all
//! operations before closing them. Output strings belong to Rust and must be
//! released with `ipa_graph_string_free`. No Rust panic crosses this boundary.
//! The host must keep each mapped dictionary inode unchanged until the last
//! handle closes; replacing its path atomically with a new inode is allowed.

use crate::{CandidateKind, Engine, QueryOptions, QueryResult};
use std::ffi::{c_char, CString};
use std::fmt::Write;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;
use std::slice;
use std::str;

pub const OK: i32 = 0;
pub const INVALID_ARGUMENT: i32 = 1;
pub const LOAD_ERROR: i32 = 2;
pub const PANIC: i32 = 3;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct CQueryOptions {
    pub candidate_limit: u32,
    pub prefix: u32,
    pub prefix_key_budget: u32,
    pub deadline_ms: u32,
    pub allow_omissions: u32,
    pub omission_state_budget: u32,
}

impl From<CQueryOptions> for QueryOptions {
    fn from(value: CQueryOptions) -> Self {
        Self {
            candidate_limit: value.candidate_limit,
            prefix: value.prefix != 0,
            prefix_key_budget: value.prefix_key_budget,
            deadline_ms: value.deadline_ms,
            allow_omissions: value.allow_omissions != 0,
            omission_state_budget: value.omission_state_budget,
        }
    }
}

fn boundary(f: impl FnOnce() -> i32) -> i32 {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or(PANIC)
}

unsafe fn input<'a>(bytes: *const u8, length: usize) -> Result<&'a str, i32> {
    if length > isize::MAX as usize || (bytes.is_null() && length != 0) {
        return Err(INVALID_ARGUMENT);
    }
    if length == 0 {
        return Ok("");
    }
    str::from_utf8(slice::from_raw_parts(bytes, length)).map_err(|_| INVALID_ARGUMENT)
}

unsafe fn output(destination: *mut *mut c_char, value: String) -> i32 {
    if destination.is_null() {
        return INVALID_ARGUMENT;
    }
    match CString::new(value) {
        Ok(value) => {
            *destination = value.into_raw();
            OK
        }
        Err(_) => INVALID_ARGUMENT,
    }
}

/// Escape a Unicode string without changing its scalar values.
pub fn json_string(value: &str) -> String {
    let mut text = String::with_capacity(value.len() + 2);
    text.push('"');
    for ch in value.chars() {
        match ch {
            '"' => text.push_str("\\\""),
            '\\' => text.push_str("\\\\"),
            '\n' => text.push_str("\\n"),
            '\r' => text.push_str("\\r"),
            '\t' => text.push_str("\\t"),
            '\u{08}' => text.push_str("\\b"),
            '\u{0c}' => text.push_str("\\f"),
            value if value < ' ' => {
                let _ = write!(text, "\\u{:04x}", value as u32);
            }
            value => text.push(value),
        }
    }
    text.push('"');
    text
}

/// JNI retains numeric candidate kinds; the CLI retains its historical names.
pub fn serialize_query(result: &QueryResult, raw: Option<&str>, accepts: bool) -> String {
    let mut text = String::new();
    text.push('{');
    if let Some(raw) = raw {
        let _ = write!(text, "\"raw\":{},\"accepts_input\":{},", json_string(raw), accepts);
    }
    let elapsed = if result.elapsed_ms.is_finite() { result.elapsed_ms.max(0.0) } else { 0.0 };
    let _ = write!(text,
        "\"status\":{},\"elapsed_ms\":{},\"exact_found\":{},\"truncated\":{},\"matched_keys\":{},\"explored_states\":{},\"matched_records\":{},",
        json_string(result.status.as_str()), elapsed, result.exact_found,
        result.truncated, result.matched_keys, result.explored_states, result.matched_records);
    if raw.is_some() {
        let _ = write!(text, "\"returned\":{},", result.candidates.len());
    }
    text.push_str("\"candidates\":[");
    for (i, candidate) in result.candidates.iter().enumerate() {
        if i != 0 { text.push(','); }
        let kind = if raw.is_some() {
            json_string(if candidate.kind == CandidateKind::Conversion { "conversion" } else { "completion" })
        } else {
            (candidate.kind as u32).to_string()
        };
        let _ = write!(text,
            "{{\"text\":{},\"language\":{},\"rank\":{},\"kind\":{},\"native_score\":{},\"context_affinity\":0,\"matched_ipa\":{},\"frequency_score\":{},\"region\":{},\"derived\":{},\"omitted_symbols\":{}",
            json_string(&candidate.text), json_string(&candidate.language), candidate.rank,
            kind, candidate.native_score, json_string(&candidate.matched_ipa),
            candidate.frequency_score, json_string(candidate.region.as_str()),
            candidate.derived, candidate.omitted_symbols);
        if raw.is_some() {
            let _ = write!(text, ",\"frequency_zipf\":{}", f64::from(candidate.frequency_score) / 20.0);
        }
        text.push('}');
    }
    text.push_str("]}");
    text
}

pub fn serialize_metadata(engine: &Engine, load_ms: Option<f64>) -> String {
    let mut text = format!(
        "{{\"dictionary_bytes\":{},\"format_version\":{},\"index_bits\":{},\"arc_record_bytes\":{},\"nodes\":{},\"arcs\":{},\"keys\":{},\"words\":{},\"alphabet\":{},\"segment_pattern\":{}",
        engine.dictionary_bytes(), engine.format_version(), engine.index_bits(),
        engine.arc_record_bytes(), engine.node_count(), engine.arc_count(),
        engine.key_count(), engine.word_count(), engine.alphabet_count(),
        json_string(engine.segment_pattern()));
    if let Some(load_ms) = load_ms {
        let _ = write!(text, ",\"load_ms\":{}", load_ms);
    }
    text.push('}');
    text
}

#[no_mangle]
pub unsafe extern "C" fn ipa_graph_open(
    path: *const u8, path_length: usize, handle: *mut *mut Engine,
    diagnostic: *mut *mut c_char,
) -> i32 {
    if !handle.is_null() { *handle = ptr::null_mut(); }
    if !diagnostic.is_null() { *diagnostic = ptr::null_mut(); }
    boundary(|| {
        if handle.is_null() { return INVALID_ARGUMENT; }
        let path = match input(path, path_length) {
            Ok(path) if !path.is_empty() && !path.contains('\0') => path,
            _ => return INVALID_ARGUMENT,
        };
        // The C ABI host promises the mapped inode is immutable until close.
        // Android installs dictionaries under verified, content-hash filenames.
        match unsafe { Engine::load(path) } {
            Ok(engine) => { *handle = Box::into_raw(Box::new(engine)); OK }
            Err(error) => {
                if !diagnostic.is_null() { let _ = output(diagnostic, error); }
                LOAD_ERROR
            }
        }
    })
}

#[no_mangle]
pub unsafe extern "C" fn ipa_graph_close(handle: *mut Engine) -> i32 {
    boundary(|| {
        if !handle.is_null() { drop(Box::from_raw(handle)); }
        OK
    })
}

#[no_mangle]
pub unsafe extern "C" fn ipa_graph_configure(handle: *const Engine, region_mask: u32) -> i32 {
    boundary(|| {
        let Some(engine) = handle.as_ref() else { return INVALID_ARGUMENT; };
        if region_mask & !31 != 0 { return INVALID_ARGUMENT; }
        engine.configure(region_mask as u8);
        OK
    })
}

#[no_mangle]
pub unsafe extern "C" fn ipa_graph_accepts_input(
    handle: *const Engine, raw: *const u8, raw_length: usize, accepts: *mut u32,
) -> i32 {
    if !accepts.is_null() { *accepts = 0; }
    boundary(|| {
        let Some(engine) = handle.as_ref() else { return INVALID_ARGUMENT; };
        if accepts.is_null() { return INVALID_ARGUMENT; }
        let raw = match input(raw, raw_length) { Ok(raw) => raw, Err(code) => return code };
        *accepts = u32::from(engine.accepts_input(raw));
        OK
    })
}

#[no_mangle]
pub unsafe extern "C" fn ipa_graph_segment_pattern(handle: *const Engine, text: *mut *mut c_char) -> i32 {
    if !text.is_null() { *text = ptr::null_mut(); }
    boundary(|| {
        let Some(engine) = handle.as_ref() else { return INVALID_ARGUMENT; };
        output(text, engine.segment_pattern().to_owned())
    })
}

#[no_mangle]
pub unsafe extern "C" fn ipa_graph_query(
    handle: *const Engine, raw: *const u8, raw_length: usize,
    options: *const CQueryOptions, text: *mut *mut c_char,
) -> i32 {
    if !text.is_null() { *text = ptr::null_mut(); }
    boundary(|| {
        let Some(engine) = handle.as_ref() else { return INVALID_ARGUMENT; };
        let raw = match input(raw, raw_length) { Ok(raw) => raw, Err(code) => return code };
        let options = options.as_ref().copied().map(QueryOptions::from).unwrap_or_default();
        output(text, serialize_query(&engine.query(raw, options), None, false))
    })
}

#[no_mangle]
pub unsafe extern "C" fn ipa_graph_metadata(handle: *const Engine, text: *mut *mut c_char) -> i32 {
    if !text.is_null() { *text = ptr::null_mut(); }
    boundary(|| {
        let Some(engine) = handle.as_ref() else { return INVALID_ARGUMENT; };
        output(text, serialize_metadata(engine, None))
    })
}

#[no_mangle]
pub unsafe extern "C" fn ipa_graph_empty_result(unsupported: u32, text: *mut *mut c_char) -> i32 {
    if !text.is_null() { *text = ptr::null_mut(); }
    boundary(|| output(text, format!(
        "{{\"status\":\"{}\",\"elapsed_ms\":0,\"exact_found\":false,\"truncated\":false,\"matched_keys\":0,\"explored_states\":0,\"matched_records\":0,\"candidates\":[]}}",
        if unsupported != 0 { "unsupported_input" } else { "error" })))
}

#[no_mangle]
pub unsafe extern "C" fn ipa_graph_string_free(text: *mut c_char) {
    let _ = boundary(|| {
        if !text.is_null() { drop(CString::from_raw(text)); }
        OK
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strings_and_invalid_ffi_calls_are_safe() {
        assert_eq!(json_string("a\0\n\\\"𠮷"), "\"a\\u0000\\n\\\\\\\"𠮷\"");
        unsafe {
            assert_eq!(ipa_graph_close(ptr::null_mut()), OK);
            assert_eq!(ipa_graph_configure(ptr::null(), 31), INVALID_ARGUMENT);
            let mut text = ptr::null_mut();
            assert_eq!(ipa_graph_empty_result(1, &mut text), OK);
            let result = std::ffi::CStr::from_ptr(text).to_str().unwrap();
            assert!(result.contains("unsupported_input"));
            ipa_graph_string_free(text);
            assert_eq!(ipa_graph_query(ptr::null(), ptr::null(), 0, ptr::null(), &mut text), INVALID_ARGUMENT);
            assert!(text.is_null());
        }
    }
}
