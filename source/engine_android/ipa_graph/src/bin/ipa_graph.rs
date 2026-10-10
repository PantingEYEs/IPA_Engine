//! JSON-compatible desktop frontend for the same core linked into Android.
use ipa_graph::ffi::{json_string, serialize_metadata, serialize_query};
use ipa_graph::{Engine, QueryOptions, QueryStatus};
use std::env;
use std::io::{self, BufRead, Write};
use std::time::Instant;

fn usage() {
    eprintln!("Usage:\n  ipa_graph info DICTIONARY\n  ipa_graph query DICTIONARY IPA [options]\n  ipa_graph repl DICTIONARY [options]\n  ipa_graph bench DICTIONARY IPA|- [options]\nOptions: --region en-US|en-GB|zh-CN|zh-TW|ja-JP --prefix --strict --limit N\n         --prefix-budget N --omission-budget N --deadline-ms N --iterations N --warmup N");
}

fn number(value: &str) -> Result<u32, String> {
    if value.is_empty() || !value.bytes().all(|value| value.is_ascii_digit()) {
        return Err("invalid integer option".to_owned());
    }
    value.parse::<u32>().ok().filter(|value| *value <= 10_000_000)
        .ok_or_else(|| "invalid integer option".to_owned())
}

fn value<'a>(arguments: &'a [String], position: &mut usize) -> Result<&'a str, String> {
    *position += 1;
    arguments.get(*position).map(String::as_str)
        .ok_or_else(|| "incomplete option".to_owned())
}

fn run(arguments: &[String]) -> Result<i32, String> {
    if arguments.len() < 3 { usage(); return Ok(2); }
    let command = arguments[1].as_str();
    if !["info", "query", "repl", "bench"].contains(&command) {
        usage(); return Ok(2);
    }
    let mut position = 3;
    let mut raw = "";
    if command == "query" || command == "bench" {
        if arguments.len() < 4 { usage(); return Ok(2); }
        raw = &arguments[3];
        position = 4;
    }
    let mut options = QueryOptions::default();
    let mut regions = 31u8;
    let mut explicit_regions = false;
    let mut iterations = 1000u32;
    let mut warmup = 100u32;
    while position < arguments.len() {
        let option = arguments[position].as_str();
        match option {
            "--prefix" => options.prefix = true,
            "--strict" => options.allow_omissions = false,
            "--region" => {
                if !explicit_regions { regions = 0; explicit_regions = true; }
                regions |= match value(arguments, &mut position)? {
                    "en-US" => 1, "en-GB" => 2, "zh-CN" => 4, "zh-TW" => 8, "ja-JP" => 16,
                    region => return Err(format!("unknown region: {region}")),
                };
            }
            "--limit" => options.candidate_limit = number(value(arguments, &mut position)?)?,
            "--prefix-budget" => options.prefix_key_budget = number(value(arguments, &mut position)?)?,
            "--omission-budget" => options.omission_state_budget = number(value(arguments, &mut position)?)?,
            "--deadline-ms" => options.deadline_ms = number(value(arguments, &mut position)?)?,
            "--iterations" => iterations = number(value(arguments, &mut position)?)?.clamp(1, 1_000_000),
            "--warmup" => warmup = number(value(arguments, &mut position)?)?.min(1_000_000),
            _ => return Err(format!("unknown or incomplete option: {option}")),
        }
        position += 1;
    }
    let start = Instant::now();
    // CLI callers must not rewrite or truncate the mapped dictionary while
    // this process runs. An atomic replacement of its pathname is safe.
    let engine = match unsafe { Engine::load(&arguments[2]) } {
        Ok(engine) => engine,
        Err(error) => { eprintln!("Dictionary initialization failed: {error}"); return Ok(1); }
    };
    let load_ms = start.elapsed().as_secs_f64() * 1000.0;
    engine.configure(regions);
    if command == "info" {
        println!("{}", serialize_metadata(&engine, Some(load_ms)));
    } else if command == "query" {
        let result = engine.query(raw, options);
        println!("{}", serialize_query(&result, Some(raw), engine.accepts_input(raw)));
        return Ok(i32::from(matches!(result.status, QueryStatus::Timeout | QueryStatus::Error)));
    } else if command == "repl" {
        let stdin = io::stdin();
        let mut stdout = io::BufWriter::new(io::stdout().lock());
        for line in stdin.lock().lines() {
            let line = line.map_err(|error| error.to_string())?;
            let raw = line.strip_suffix('\r').unwrap_or(&line);
            let result = engine.query(raw, options);
            writeln!(stdout, "{}", serialize_query(&result, Some(raw), engine.accepts_input(raw)))
                .map_err(|error| error.to_string())?;
            stdout.flush().map_err(|error| error.to_string())?;
        }
    } else {
        let cases = if raw == "-" {
            let mut cases = Vec::new();
            for line in io::stdin().lock().lines() {
                let mut line = line.map_err(|error| error.to_string())?;
                if line.ends_with('\r') { line.pop(); }
                if cases.len() >= 1_000_000 || line.len() > 256 {
                    return Err("benchmark input list exceeds its bound".to_owned());
                }
                cases.push(line);
            }
            if cases.is_empty() { return Err("benchmark requires at least one input line".to_owned()); }
            cases
        } else { vec![raw.to_owned()] };
        for index in 0..warmup {
            engine.query(&cases[index as usize % cases.len()], options);
        }
        let mut times = Vec::with_capacity(iterations as usize);
        let mut timeouts = 0;
        let mut errors = 0;
        let mut returned = 0;
        for index in 0..iterations {
            let result = engine.query(&cases[index as usize % cases.len()], options);
            times.push(result.elapsed_ms);
            timeouts += u32::from(result.status == QueryStatus::Timeout);
            errors += u32::from(result.status == QueryStatus::Error);
            returned = result.candidates.len();
        }
        times.sort_by(f64::total_cmp);
        let percentile = |fraction: f64| times[(fraction * times.len() as f64).ceil() as usize - 1];
        println!("{{\"raw\":{},\"iterations\":{},\"cases\":{},\"warmup\":{},\"format_version\":{},\"index_bits\":{},\"dictionary_bytes\":{},\"load_ms\":{},\"p50_ms\":{},\"p95_ms\":{},\"p99_ms\":{},\"max_ms\":{},\"timeouts\":{},\"errors\":{},\"last_returned\":{}}}",
            json_string(raw), iterations, cases.len(), warmup, engine.format_version(),
            engine.index_bits(), engine.dictionary_bytes(), load_ms,
            percentile(0.5), percentile(0.95), percentile(0.99), times.last().unwrap(), timeouts, errors, returned);
        return Ok(i32::from(timeouts != 0 || errors != 0));
    }
    Ok(0)
}

fn main() {
    let arguments: Vec<_> = env::args().collect();
    let code = match run(&arguments) {
        Ok(code) => code,
        Err(error) => { eprintln!("{error}"); 2 }
    };
    std::process::exit(code);
}
