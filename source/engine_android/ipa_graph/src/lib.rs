//! Read-only packed DAWG input-method engine. No raw dictionary expansion and
//! no external runtime dependencies. Candidate behavior matches IPADWG01 v2/v3.
pub mod ffi;
mod mapping;
mod packs;
mod runtime;
mod unicode;
pub use runtime::{Engine, PackInfo, PackMode, Pronunciation, ReverseResult};

use mapping::Mapping;
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashSet};
use std::rc::Rc;
#[cfg(test)]
use std::time::Duration;
use std::time::Instant;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum QueryStatus {
    Ok,
    UnsupportedInput,
    Timeout,
    Error,
}
impl QueryStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::UnsupportedInput => "unsupported_input",
            Self::Timeout => "timeout",
            Self::Error => "error",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(i32)]
pub enum CandidateKind {
    Conversion = 0,
    Completion = 2,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum Region {
    EnUS = 0,
    EnGB = 1,
    ZhCN = 2,
    ZhTW = 3,
    JaJP = 4,
    RuRU = 5,
    ArStandard = 6,
    KoKR = 7,
}
impl Region {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::EnUS => "en-US",
            Self::EnGB => "en-GB",
            Self::ZhCN => "zh-CN",
            Self::ZhTW => "zh-TW",
            Self::JaJP => "ja-JP",
            Self::RuRU => "ru-RU",
            Self::ArStandard => "ar-001",
            Self::KoKR => "ko-KR",
        }
    }
    fn from_code(code: u8) -> Self {
        match code {
            0 => Self::EnUS,
            1 => Self::EnGB,
            2 => Self::ZhCN,
            3 => Self::ZhTW,
            4 => Self::JaJP,
            5 => Self::RuRU,
            6 => Self::ArStandard,
            _ => Self::KoKR,
        }
    }
}
#[derive(Clone, Debug)]
pub struct Candidate {
    pub text: String,
    pub language: String,
    pub rank: i32,
    pub kind: CandidateKind,
    pub native_score: i32,
    pub context_affinity: f32,
    pub matched_ipa: String,
    pub frequency_score: u8,
    pub region: Region,
    pub derived: bool,
    pub omitted_symbols: u32,
}
#[derive(Clone, Copy, Debug)]
pub struct QueryOptions {
    pub candidate_limit: u32,
    pub prefix: bool,
    pub prefix_key_budget: u32,
    pub deadline_ms: u32,
    pub allow_omissions: bool,
    pub omission_state_budget: u32,
}
impl Default for QueryOptions {
    fn default() -> Self {
        Self {
            candidate_limit: 30,
            prefix: false,
            prefix_key_budget: 2048,
            deadline_ms: 60,
            allow_omissions: true,
            omission_state_budget: 20_000,
        }
    }
}
#[derive(Clone, Debug)]
pub struct QueryResult {
    pub candidates: Vec<Candidate>,
    pub status: QueryStatus,
    pub elapsed_ms: f64,
    pub exact_found: bool,
    pub truncated: bool,
    pub matched_keys: u32,
    pub matched_records: u64,
    pub explored_states: u32,
}
impl Default for QueryResult {
    fn default() -> Self {
        Self {
            candidates: Vec::new(),
            status: QueryStatus::Ok,
            elapsed_ms: 0.0,
            exact_found: false,
            truncated: false,
            matched_keys: 0,
            matched_records: 0,
            explored_states: 0,
        }
    }
}

#[inline]
fn u32le(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(bytes[at..at + 4].try_into().expect("validated u32 slice"))
}
fn varint(bytes: &[u8], cursor: &mut usize) -> Result<u32, String> {
    let mut value = 0u32;
    for shift in (0..=28).step_by(7) {
        let byte = *bytes.get(*cursor).ok_or("invalid varint bounds")?;
        *cursor += 1;
        if shift == 28 && byte & 0xf0 != 0 {
            return Err("invalid varint overflow".into());
        }
        value |= u32::from(byte & 127) << shift;
        if byte & 128 == 0 {
            if shift != 0 && byte & 127 == 0 {
                return Err("noncanonical varint".into());
            }
            return Ok(value);
        }
    }
    Err("invalid varint".into())
}
// IPA vowel symbols; classification only guides omission priorities. It never
// replaces, inserts or reorders a user's input symbols.
fn vowel(cp: u32) -> bool {
    char::from_u32(cp).is_some_and(|c| "aeiouyæøœɐɑɒɔəɚɛɜɝɞɘɨɪɯɵɤʉʊʌʏɶ".contains(c))
}
fn whitespace(cp: u32) -> bool {
    matches!(cp, 0x20 | 9..=13 | 0x85 | 0xa0 | 0x1680 | 0x2000..=0x200a
        | 0x2028 | 0x2029 | 0x202f | 0x205f | 0x3000)
}
fn valid_text(text: &str, max_units: usize, nonblank: bool) -> bool {
    let mut units = 0;
    let mut found_text = !nonblank;
    for cp in text.chars() {
        units += cp.len_utf16();
        if units > max_units {
            return false;
        }
        found_text = found_text || !whitespace(cp as u32);
    }
    found_text
}
#[inline]
fn symbol_bit(cp: u32) -> u64 {
    1 << (u64::from(cp).wrapping_mul(11_400_714_819_323_198_485) >> 58)
}
fn push_cp(text: &mut String, cp: u32) {
    text.push(char::from_u32(cp).expect("validated alphabet codepoint"));
}
fn labels(mask: u8) -> String {
    let mut parts = Vec::with_capacity(4);
    if mask & 3 != 0 {
        parts.push("EN");
    }
    if mask & 4 != 0 {
        parts.push("简");
    }
    if mask & 8 != 0 {
        parts.push("繁");
    }
    if mask & 16 != 0 {
        parts.push("日");
    }
    if mask & 32 != 0 {
        parts.push("RU");
    }
    if mask & 64 != 0 {
        parts.push("AR");
    }
    if mask & 128 != 0 {
        parts.push("KO");
    }
    parts.join("/")
}

struct Graph {
    mapping: Mapping,
    sections: [usize; 9],
    version: u32,
    width: usize,
    terminal_bit: u32,
    nodes: u32,
    arcs: u32,
    keys: u32,
    words: u32,
    alphabet: u32,
    root: u32,
    pattern: String,
    match_alphabet: Vec<u32>,
    // A key has at most 64 UTF-16 units; a scalar expands to at most three
    // comparison symbols, so every validated suffix needs at most 192.
    max_match_depth: Vec<u8>,
    match_presence: Vec<u64>,
    present_regions: u8,
}
impl Graph {
    #[inline]
    fn section(&self, i: usize) -> &[u8] {
        &self.mapping.bytes()[self.sections[i]..self.sections[i + 1]]
    }
    #[inline]
    fn index(&self, section: usize, i: u32) -> u32 {
        let bytes = self.section(section);
        let at = i as usize * self.width;
        if self.width == 3 {
            let entry = &bytes[at..at + 3];
            u32::from(entry[0]) | (u32::from(entry[1]) << 8) | (u32::from(entry[2]) << 16)
        } else {
            u32le(bytes, at)
        }
    }
    #[inline]
    fn terminal(&self, node: u32) -> bool {
        self.index(2, node) & self.terminal_bit != 0
    }
    #[inline]
    fn count(&self, node: u32) -> u32 {
        self.index(2, node) & !self.terminal_bit
    }
    #[inline]
    fn begin(&self, node: u32) -> u32 {
        self.index(1, node)
    }
    #[inline]
    fn end(&self, node: u32) -> u32 {
        self.index(1, node + 1)
    }
    #[inline]
    fn arc(&self, i: u32) -> (u8, u32) {
        let bytes = self.section(3);
        let at = i as usize * (self.width + 1);
        let entry = &bytes[at..at + self.width + 1];
        let child = if self.width == 3 {
            u32::from(entry[1]) | (u32::from(entry[2]) << 8) | (u32::from(entry[3]) << 16)
        } else {
            u32le(entry, 1)
        };
        (entry[0], child)
    }
    #[inline]
    fn cp(&self, label: u8) -> u32 {
        u32le(self.section(0), usize::from(label - 1) * 4)
    }
    fn label(&self, cp: u32) -> u8 {
        let (mut lo, mut hi) = (0, self.alphabet);
        while lo < hi {
            let mid = lo + (hi - lo) / 2;
            if u32le(self.section(0), mid as usize * 4) < cp {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        if lo < self.alphabet && u32le(self.section(0), lo as usize * 4) == cp {
            (lo + 1) as u8
        } else {
            0
        }
    }
    fn input(&self, raw: &str) -> Option<Vec<u32>> {
        if raw.is_empty() || raw.len() > 256 || !valid_text(raw, 64, true) {
            return None;
        }
        let mut cps = Vec::with_capacity(raw.chars().count());
        for cp in raw.chars() {
            let mut cp = cp as u32;
            if (u32::from(b'1')..=u32::from(b'5')).contains(&cp) {
                cp = 0x2e9 - (cp - u32::from(b'1'));
            }
            let (parts, count) = unicode::expansion(cp);
            if !parts[..count]
                .iter()
                .all(|p| self.match_alphabet.binary_search(p).is_ok())
            {
                return None;
            }
            cps.push(cp);
        }
        Some(cps)
    }
    fn walk(&self, cps: &[u32]) -> Option<(u32, u32)> {
        let (mut node, mut rank) = (self.root, 0);
        for &cp in cps {
            let wanted = self.label(cp);
            if self.terminal(node) {
                rank += 1;
            }
            let mut found = false;
            for i in self.begin(node)..self.end(node) {
                let (label, child) = self.arc(i);
                if label < wanted {
                    rank += self.count(child);
                } else if label == wanted {
                    node = child;
                    found = true;
                    break;
                } else {
                    break;
                }
            }
            if !found {
                return None;
            }
        }
        Some((node, rank))
    }
    fn key(&self, mut rank: u32) -> Result<String, String> {
        if rank >= self.keys {
            return Err("key rank outside graph".into());
        }
        let mut node = self.root;
        let mut ipa = String::new();
        loop {
            if self.terminal(node) {
                if rank == 0 {
                    return Ok(ipa);
                }
                rank -= 1;
            }
            let mut found = false;
            for i in self.begin(node)..self.end(node) {
                let (label, child) = self.arc(i);
                let count = self.count(child);
                if rank < count {
                    push_cp(&mut ipa, self.cp(label));
                    node = child;
                    found = true;
                    break;
                }
                rank -= count;
            }
            if !found {
                return Err("key rank changed".into());
            }
        }
    }
    fn word(&self, id: u32) -> Result<String, String> {
        let block = id / 16;
        let start = self.index(6, block) as usize;
        let end = self.index(6, block + 1) as usize;
        let bytes = &self.section(7)[start..end];
        let mut cursor = 0;
        let mut value = Vec::new();
        for _ in 0..=id % 16 {
            let shared = varint(bytes, &mut cursor)? as usize;
            let suffix = varint(bytes, &mut cursor)? as usize;
            if shared > value.len() {
                return Err("word pool changed".into());
            }
            value.truncate(shared);
            value.extend_from_slice(
                bytes
                    .get(cursor..cursor + suffix)
                    .ok_or("word pool changed")?,
            );
            cursor += suffix;
        }
        String::from_utf8(value).map_err(|_| "word pool changed".into())
    }
    fn word_id(&self, text: &str) -> Result<Option<u32>, String> {
        let (mut lo, mut hi) = (0, self.words);
        while lo < hi {
            let mid = lo + (hi - lo) / 2;
            if self.word(mid)?.as_str() < text {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        Ok(if lo < self.words && self.word(lo)? == text {
            Some(lo)
        } else {
            None
        })
    }
    fn load_mode(mapping: Mapping, search_indexes: bool) -> Result<Self, String> {
        let bytes = mapping.bytes();
        if bytes.len() < 108 || &bytes[..8] != b"IPADWG01" {
            return Err("invalid graph magic/header".into());
        }
        let version = u32le(bytes, 8);
        if version != 2 && version != 3 {
            return Err("unsupported graph version (requires v2 or v3)".into());
        }
        let width = if version == 2 { 3 } else { 4 };
        let terminal_bit = if version == 2 { 0x800000 } else { 0x80000000 };
        let nodes = u32le(bytes, 12);
        let arcs = u32le(bytes, 16);
        let keys = u32le(bytes, 20);
        let words = u32le(bytes, 24);
        let alphabet = u32le(bytes, 28);
        let root = u32le(bytes, 32);
        if nodes == 0
            || (version == 2 && (nodes > 0x1000000 || arcs >= 0x1000000))
            || keys == 0
            || keys >= terminal_bit
            || words == 0
            || alphabet == 0
            || alphabet > 255
            || root >= nodes
        {
            return Err("invalid graph counts".into());
        }
        let mut sections = [0usize; 9];
        for (i, offset) in sections.iter_mut().enumerate() {
            let at = 36 + i * 8;
            let value = u64::from_le_bytes(bytes[at..at + 8].try_into().expect("header directory"));
            if value > bytes.len() as u64 {
                return Err("invalid graph size/directory".into());
            }
            *offset = value as usize;
        }
        if sections[0] != 108
            || sections[8] != bytes.len()
            || sections.windows(2).any(|p| p[0] > p[1])
        {
            return Err("invalid graph size/directory".into());
        }
        let blocks = (u64::from(words) + 15) / 16;
        let expected = [
            (0, u64::from(alphabet) * 4),
            (1, (u64::from(nodes) + 1) * width as u64),
            (2, u64::from(nodes) * width as u64),
            (3, u64::from(arcs) * (width + 1) as u64),
            (4, (u64::from(keys) + 1) * width as u64),
            (6, (blocks + 1) * width as u64),
        ];
        if expected
            .iter()
            .any(|&(i, len)| (sections[i + 1] - sections[i]) as u64 != len)
            || (version == 2
                && (sections[6] - sections[5] >= 0x1000000
                    || sections[8] - sections[7] >= 0x1000000))
        {
            return Err("invalid graph section lengths".into());
        }
        let mut graph = Self {
            mapping,
            sections,
            version,
            width,
            terminal_bit,
            nodes,
            arcs,
            keys,
            words,
            alphabet,
            root,
            pattern: String::new(),
            match_alphabet: Vec::new(),
            max_match_depth: if search_indexes {
                vec![0; nodes as usize]
            } else {
                Vec::new()
            },
            match_presence: if search_indexes {
                vec![0; nodes as usize]
            } else {
                Vec::new()
            },
            present_regions: 0,
        };
        graph.validate(blocks as u32)?;
        Ok(graph)
    }
    fn validate(&mut self, blocks: u32) -> Result<(), String> {
        let mut previous_cp = 0;
        for i in 0..self.alphabet {
            let cp = u32le(self.section(0), i as usize * 4);
            if cp == 0 || cp <= previous_cp || char::from_u32(cp).is_none() {
                return Err("invalid alphabet".into());
            }
            previous_cp = cp;
            let (parts, count) = unicode::expansion(cp);
            self.match_alphabet.extend_from_slice(&parts[..count]);
            self.match_alphabet.push(cp);
        }
        self.match_alphabet.sort_unstable();
        self.match_alphabet.dedup();
        let mut pattern = String::from("[");
        for cp in (0x80..0x300).chain(0x1e00..0x2000) {
            let (parts, count) = unicode::expansion(cp);
            if count > 1
                && parts[..count]
                    .iter()
                    .all(|p| self.match_alphabet.binary_search(p).is_ok())
            {
                pattern.push_str(&format!("\\x{{{cp:x}}}"));
            }
        }
        for cp in &self.match_alphabet {
            pattern.push_str(&format!("\\x{{{cp:x}}}"));
        }
        for cp in u32::from(b'1')..=u32::from(b'5') {
            if self
                .match_alphabet
                .binary_search(&(0x2e9 - (cp - u32::from(b'1'))))
                .is_ok()
            {
                pattern.push_str(&format!("\\x{{{cp:x}}}"));
            }
        }
        pattern.push_str("]+");
        self.pattern = pattern;
        if self.index(1, 0) != 0 || self.index(1, self.nodes) != self.arcs {
            return Err("invalid arc directory".into());
        }
        let mut depths = vec![0u16; self.nodes as usize];
        for n in 0..self.nodes {
            let first = self.begin(n);
            let last = self.end(n);
            if first > last || last > self.arcs {
                return Err("invalid node arc range".into());
            }
            let mut total = u64::from(self.terminal(n));
            let mut previous_label = 0;
            let mut depth = 0;
            for i in first..last {
                let (label, child) = self.arc(i);
                if label == 0
                    || u32::from(label) > self.alphabet
                    || label <= previous_label
                    || child >= n
                {
                    return Err("invalid arc/order/acyclicity".into());
                }
                previous_label = label;
                total += u64::from(self.count(child));
                let cp = self.cp(label);
                depth = depth.max(depths[child as usize] + if cp >= 0x10000 { 2 } else { 1 });
                if !self.max_match_depth.is_empty() {
                    let (parts, count) = unicode::expansion(cp);
                    let match_depth = self.max_match_depth[child as usize]
                        .checked_add(count as u8)
                        .ok_or("graph comparison depth exceeds byte bound")?;
                    self.max_match_depth[n as usize] =
                        self.max_match_depth[n as usize].max(match_depth);
                    self.match_presence[n as usize] |= self.match_presence[child as usize];
                    for &part in &parts[..count] {
                        self.match_presence[n as usize] |= symbol_bit(part);
                    }
                }
            }
            if total == 0
                || total >= u64::from(self.terminal_bit)
                || u64::from(self.count(n)) != total
            {
                return Err("invalid suffix count".into());
            }
            if depth > 64 {
                return Err("graph key exceeds input length bound".into());
            }
            depths[n as usize] = depth;
        }
        if self.count(self.root) != self.keys {
            return Err("root suffix count differs from key count".into());
        }
        if self.terminal(self.root) {
            return Err("empty input key is forbidden".into());
        }
        let post_len = self.section(5).len();
        if self.index(4, 0) != 0 || self.index(4, self.keys) as usize != post_len {
            return Err("invalid posting directory".into());
        }
        let mut present_regions = 0;
        for key in 0..self.keys {
            let start = self.index(4, key) as usize;
            let end = self.index(4, key + 1) as usize;
            if start >= end || end > post_len {
                return Err("invalid posting bounds".into());
            }
            let bytes = &self.section(5)[start..end];
            let mut cursor = 0;
            let records = varint(bytes, &mut cursor)?;
            if records == 0 || records as usize > (bytes.len() - cursor) / 2 {
                return Err("invalid posting count".into());
            }
            let (mut prev_word, mut prev_region) = (0, 0);
            for record in 0..records {
                let code = varint(bytes, &mut cursor)?;
                let freq = *bytes.get(cursor).ok_or("invalid posting record")?;
                cursor += 1;
                let delta = code >> 4;
                let region = code & 7;
                if prev_word >= self.words
                    || delta >= self.words - prev_word
                    || region > 7
                    || freq > 180
                {
                    return Err("invalid posting word/region/score".into());
                }
                present_regions |= 1 << region;
                let id = prev_word + delta;
                if record != 0 && id == prev_word && region <= prev_region {
                    return Err("unordered or duplicate posting record".into());
                }
                prev_word = id;
                prev_region = region;
            }
            if cursor != bytes.len() {
                return Err("trailing posting bytes".into());
            }
        }
        let word_len = self.section(7).len();
        if self.index(6, 0) != 0 || self.index(6, blocks) as usize != word_len {
            return Err("invalid word directory".into());
        }
        let mut previous_word = Vec::new();
        for block in 0..blocks {
            let start = self.index(6, block) as usize;
            let end = self.index(6, block + 1) as usize;
            if start >= end || end > word_len {
                return Err("invalid word block bounds".into());
            }
            let bytes = &self.section(7)[start..end];
            let mut cursor = 0;
            let mut value = Vec::new();
            for j in 0..16.min(self.words - block * 16) {
                let shared = varint(bytes, &mut cursor)? as usize;
                let suffix = varint(bytes, &mut cursor)? as usize;
                if shared > value.len()
                    || (j == 0 && shared != 0)
                    || suffix > bytes.len() - cursor
                    || shared + suffix > 4000
                {
                    return Err("invalid front-coded word".into());
                }
                value.truncate(shared);
                value.extend_from_slice(&bytes[cursor..cursor + suffix]);
                cursor += suffix;
                let text = std::str::from_utf8(&value).map_err(|_| "invalid UTF-8 word")?;
                if !valid_text(text, 1000, true)
                    || (!previous_word.is_empty() && previous_word >= value)
                {
                    return Err("invalid blank, oversized or unordered word".into());
                }
                previous_word.clone_from(&value);
            }
            if cursor != bytes.len() {
                return Err("trailing word block bytes".into());
            }
        }
        self.present_regions = present_regions;
        Ok(())
    }
}

// The search oracle tests inspect an immutable single graph directly.
#[cfg(test)]
struct SingleEngine {
    graph: std::sync::Arc<Graph>,
}
#[cfg(test)]
impl SingleEngine {
    unsafe fn load(path: impl AsRef<std::path::Path>) -> Result<Self, String> {
        Ok(Self {
            graph: std::sync::Arc::new(Graph::load_mode(Mapping::open(path.as_ref())?, true)?),
        })
    }
}

#[derive(Debug)]
struct Hit {
    id: u32,
    mask: u8,
    freq: u8,
    region: Region,
    derived: bool,
    completion: bool,
    score: i32,
    ipa: Rc<str>,
    omitted: u32,
}
fn better(a: &Hit, b: &Hit) -> bool {
    a.score > b.score || (a.score == b.score && a.id < b.id)
}
#[derive(Debug, Eq, PartialEq)]
struct State {
    node: u32,
    rank: u32,
    position: usize,
    omitted: u32,
    cost: u32,
    at_onset: bool,
    onset_aligned: bool,
    // Independent alignment for abbreviations: an earlier matching coda must
    // not steal a symbol that can match the next explicit syllable onset.
    // At most 64 UTF-16 input units expand to at most 192 comparison symbols.
    onset_position: u8,
}
impl Ord for State {
    fn cmp(&self, other: &Self) -> Ordering {
        // Every suffix has at least one key. Subtree starting ranks therefore
        // order different lexical branches exactly as their IPA spellings do.
        // Equal starting ranks describe prefixes of the same first key; those
        // prefixes form an ancestor chain. Expanded length strictly increases
        // along that chain, so it supplies the same tie order without strings.
        other
            .cost
            .cmp(&self.cost)
            .then_with(|| other.rank.cmp(&self.rank))
            .then_with(|| {
                (other.position + other.omitted as usize)
                    .cmp(&(self.position + self.omitted as usize))
            })
    }
}
impl PartialOrd for State {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
struct Search<'a> {
    graph: &'a Graph,
    result: QueryResult,
    deadline: Instant,
    timeout: bool,
    candidate_overflow: bool,
    limit: usize,
    mask: u8,
    consumed: HashSet<u32>,
    hits: Vec<Hit>,
}
impl Search<'_> {
    fn check_time(&mut self) -> bool {
        if Instant::now() >= self.deadline {
            self.timeout = true;
        }
        self.timeout
    }
    fn insert(&mut self, hit: Hit) {
        if let Some(same) = self.hits.iter_mut().find(|h| h.id == hit.id) {
            let merged = same.mask | hit.mask;
            if better(&hit, same) {
                *same = hit;
            }
            same.mask = merged;
        } else if self.hits.len() < self.limit {
            self.hits.push(hit);
        } else {
            self.candidate_overflow = true;
            let mut worst = 0;
            for i in 1..self.hits.len() {
                if better(&self.hits[worst], &self.hits[i]) {
                    worst = i;
                }
            }
            if better(&hit, &self.hits[worst]) {
                self.hits[worst] = hit;
            }
        }
    }
    fn consume(
        &mut self,
        key: u32,
        ipa: &str,
        completion: bool,
        omitted: u32,
        initials: bool,
    ) -> Result<(), String> {
        if !self.consumed.insert(key) {
            return Ok(());
        }
        self.result.matched_keys += 1;
        let start = self.graph.index(4, key) as usize;
        let end = self.graph.index(4, key + 1) as usize;
        let bytes = &self.graph.section(5)[start..end];
        let mut cursor = 0;
        let records = varint(bytes, &mut cursor)?;
        // Sharing a key's spelling avoids one allocation for every posting.
        // This reference-counted string stays inside a single query thread.
        let matched_ipa: Rc<str> = Rc::from(ipa);
        let mut id = 0;
        let mut group: Option<Hit> = None;
        for r in 0..records {
            if r & 31 == 0 && self.check_time() {
                if let Some(group) = group {
                    self.insert(group);
                }
                return Ok(());
            }
            let code = varint(bytes, &mut cursor)?;
            id += code >> 4;
            let region = (code & 7) as u8;
            let derived = code & 8 != 0;
            let freq = *bytes.get(cursor).ok_or("posting changed")?;
            cursor += 1;
            if self.mask & (1 << region) == 0 {
                continue;
            }
            self.result.matched_records += 1;
            let mut hit = Hit {
                id,
                mask: 1 << region,
                freq,
                region: Region::from_code(region),
                derived,
                completion,
                score: (if completion {
                    1000
                } else if initials && omitted != 0 {
                    // Initial abbreviations compete by frequency rather than
                    // the number of vowels/tone marks in the full spelling.
                    6000
                } else if omitted != 0 {
                    5000 - omitted as i32 * 20
                } else {
                    10000
                }) + i32::from(freq)
                    - if derived { 5 } else { 0 },
                ipa: Rc::clone(&matched_ipa),
                omitted,
            };
            if group.as_ref().is_some_and(|group| group.id == id) {
                let group = group.as_mut().expect("group exists");
                let mask = group.mask | hit.mask;
                if better(&hit, group) {
                    std::mem::swap(group, &mut hit);
                }
                group.mask = mask;
            } else {
                if let Some(group) = group.take() {
                    self.insert(group);
                }
                group = Some(hit);
            }
        }
        if let Some(group) = group {
            self.insert(group);
        }
        Ok(())
    }
    fn run(&mut self, raw: &str, options: QueryOptions) -> Result<(), String> {
        if raw.is_empty() {
            return Ok(());
        }
        let Some(cps) = self.graph.input(raw) else {
            self.result.status = QueryStatus::UnsupportedInput;
            return Ok(());
        };
        let walked = self.graph.walk(&cps);
        self.result.exact_found = walked.is_some_and(|(node, _)| self.graph.terminal(node));
        if self.limit == 0 || self.mask == 0 {
            return Ok(());
        }
        self.hits.reserve(self.limit);
        let mut normalized = String::new();
        let mut input_symbols = 0;
        for &cp in &cps {
            push_cp(&mut normalized, cp);
            input_symbols += unicode::expansion(cp).1 as u32;
        }
        if self.result.exact_found {
            self.consume(walked.expect("exact walk").1, &normalized, false, 0, false)?;
        }
        if options.prefix && !self.timeout {
            if let Some((node, rank)) = walked {
                self.prefix(
                    node,
                    rank,
                    &normalized,
                    input_symbols,
                    options.prefix_key_budget,
                )?;
            }
        }
        if options.allow_omissions && !self.timeout {
            self.omissions(&cps, &normalized, options)?;
        }
        self.check_time();
        if self.timeout {
            self.result.truncated = true;
            if !options.allow_omissions || self.hits.is_empty() {
                self.result.status = QueryStatus::Timeout;
            }
        }
        if self.candidate_overflow {
            self.result.truncated = true;
        }
        self.hits
            .sort_unstable_by(|a, b| b.score.cmp(&a.score).then_with(|| a.id.cmp(&b.id)));
        self.result.candidates.reserve(self.hits.len());
        for h in &self.hits {
            self.result.candidates.push(Candidate {
                text: self.graph.word(h.id)?,
                language: labels(h.mask),
                rank: self.result.candidates.len() as i32,
                kind: if h.completion {
                    CandidateKind::Completion
                } else {
                    CandidateKind::Conversion
                },
                native_score: h.score,
                context_affinity: 0.0,
                matched_ipa: h.ipa.to_string(),
                frequency_score: h.freq,
                region: h.region,
                derived: h.derived,
                omitted_symbols: h.omitted,
            });
        }
        Ok(())
    }
    fn prefix(
        &mut self,
        node: u32,
        rank: u32,
        normalized: &str,
        input_symbols: u32,
        budget: u32,
    ) -> Result<(), String> {
        struct Frame {
            node: u32,
            next: u32,
            rank: u32,
            bytes: usize,
            entered: bool,
        }
        let budget = budget.clamp(1, 65536);
        let mut prefix_keys = 0;
        let mut ipa = normalized.to_owned();
        let mut stack = Vec::with_capacity(65);
        stack.push(Frame {
            node,
            next: self.graph.begin(node),
            rank,
            bytes: ipa.len(),
            entered: false,
        });
        while !stack.is_empty() && !self.timeout {
            if self.check_time() {
                break;
            }
            let frame = stack.last_mut().expect("prefix frame");
            if !frame.entered {
                frame.entered = true;
                if self.graph.terminal(frame.node) {
                    if prefix_keys == budget {
                        self.result.truncated = true;
                        break;
                    }
                    prefix_keys += 1;
                    let key_symbols: u32 = ipa
                        .chars()
                        .map(|cp| unicode::expansion(cp as u32).1 as u32)
                        .sum();
                    self.consume(
                        frame.rank,
                        &ipa,
                        ipa != normalized,
                        key_symbols - input_symbols,
                        false,
                    )?;
                    frame.rank += 1;
                }
            }
            if frame.next == self.graph.end(frame.node) {
                stack.pop();
                if let Some(parent) = stack.last() {
                    ipa.truncate(parent.bytes);
                }
                continue;
            }
            let (label, child) = self.graph.arc(frame.next);
            frame.next += 1;
            let child_rank = frame.rank;
            frame.rank += self.graph.count(child);
            push_cp(&mut ipa, self.graph.cp(label));
            stack.push(Frame {
                node: child,
                next: self.graph.begin(child),
                rank: child_rank,
                bytes: ipa.len(),
                entered: false,
            });
        }
        Ok(())
    }
    // Bounded phonemic-exact probe: when only annotations are missing, the
    // existing cheap search is usually sufficient. This probe only selects a
    // search strategy; it never supplies candidates or discards a valid path.
    fn annotation_only_match(&mut self, input: &[u32]) -> Result<bool, String> {
        let mut pending = vec![(self.graph.root, 0u32, 0usize)];
        let mut work = 0u32;
        while let Some((node, rank, position)) = pending.pop() {
            if work >= 1024 || self.check_time() {
                return Ok(false);
            }
            let mut child_rank = rank;
            if self.graph.terminal(node) {
                if position == input.len() {
                    let start = self.graph.index(4, rank) as usize;
                    let end = self.graph.index(4, rank + 1) as usize;
                    let bytes = &self.graph.section(5)[start..end];
                    let mut cursor = 0;
                    let records = varint(bytes, &mut cursor)?;
                    for record in 0..records {
                        if work == 1024 {
                            return Ok(false);
                        }
                        work += 1;
                        if record & 31 == 0 && self.check_time() {
                            return Ok(false);
                        }
                        let code = varint(bytes, &mut cursor)?;
                        cursor += 1;
                        if self.mask & (1 << (code & 7)) != 0 {
                            return Ok(true);
                        }
                    }
                }
                child_rank += 1;
            }
            for arc in self.graph.begin(node)..self.graph.end(node) {
                if work == 1024 {
                    return Ok(false);
                }
                work += 1;
                let (label, child) = self.graph.arc(arc);
                let (parts, count) = unicode::expansion(self.graph.cp(label));
                let mut matched = position;
                let mut valid = true;
                for &part in &parts[..count] {
                    if matched < input.len() && input[matched] == part {
                        matched += 1;
                    } else if !unicode::annotation(part) {
                        valid = false;
                        break;
                    }
                }
                if valid {
                    pending.push((child, child_rank, matched));
                }
                child_rank += self.graph.count(child);
            }
        }
        Ok(false)
    }
    // Minimum input suffix start accepted below each DAG node. Child IDs are
    // smaller than parent IDs, so one bottom-up pass gives exact ordered
    // feasibility without enumerating pronunciations or storing extra paths.
    fn suffix_requirements(&mut self, input: &[u32]) -> Option<Vec<u8>> {
        // Keep at least three quarters of the remaining deadline available
        // for ordinary search if analysis is too expensive on this device.
        let analysis_deadline =
            Instant::now() + self.deadline.saturating_duration_since(Instant::now()) / 4;
        self.suffix_requirements_before(input, analysis_deadline)
    }
    fn suffix_requirements_before(
        &mut self,
        input: &[u32],
        analysis_deadline: Instant,
    ) -> Option<Vec<u8>> {
        let stride = input.len() + 1;
        let mut reverse_step = vec![0u8; (self.graph.alphabet as usize + 1) * stride];
        for label in 1..=self.graph.alphabet {
            let (parts, count) = unicode::expansion(self.graph.cp(label as u8));
            for start in 0..stride {
                let mut position = start;
                for &part in parts[..count].iter().rev() {
                    if position > 0 && input[position - 1] == part {
                        position -= 1;
                    }
                }
                reverse_step[label as usize * stride + start] = position as u8;
            }
        }
        let mut required = vec![input.len() as u8; self.graph.nodes as usize];
        let last = input.last().copied().map(symbol_bit);
        for node in 0..self.graph.nodes {
            if node & 1023 == 0 {
                if self.check_time() || Instant::now() >= analysis_deadline {
                    // No partial array is used: unknown analysis must never
                    // be treated as proof that a candidate is unreachable.
                    return None;
                }
            }
            // Empty query suffix is a subsequence of every accepted path.
            // If even its last symbol is absent, no nonempty suffix can fit.
            if last.is_some_and(|bit| self.graph.match_presence[node as usize] & bit == 0) {
                continue;
            }
            let lower = input
                .len()
                .saturating_sub(self.graph.max_match_depth[node as usize] as usize);
            let mut best = input.len() as u8;
            for arc in self.graph.begin(node)..self.graph.end(node) {
                let (label, child) = self.graph.arc(arc);
                let start = required[child as usize];
                best = best.min(reverse_step[label as usize * stride + start as usize]);
                if best as usize == lower {
                    break;
                }
            }
            required[node as usize] = best;
        }
        Some(required)
    }
    fn omissions(
        &mut self,
        cps: &[u32],
        normalized: &str,
        options: QueryOptions,
    ) -> Result<(), String> {
        let mut input = Vec::new();
        for &cp in cps {
            let (parts, count) = unicode::expansion(cp);
            input.extend_from_slice(&parts[..count]);
        }
        let sounds = input.iter().filter(|&&cp| !unicode::annotation(cp)).count();
        let initials = (2..=8).contains(&sounds)
            && input.iter().all(|&cp| {
                whitespace(cp) || cp == 0x2e || (!unicode::annotation(cp) && !vowel(cp))
            });
        let mut remaining = vec![0u64; input.len() + 1];
        for i in (0..input.len()).rev() {
            remaining[i] = remaining[i + 1] | symbol_bit(input[i]);
        }
        let requirements = if input.len() >= 4
            && !self.result.exact_found
            && options.deadline_ms >= 10
            && options.omission_state_budget >= 4096
            && !self.annotation_only_match(&input)?
        {
            self.suffix_requirements(&input)
        } else {
            None
        };
        if self.timeout {
            return Ok(());
        }
        let budget = options.omission_state_budget.clamp(1, 200_000);
        let mut pending = BinaryHeap::new();
        pending.push(State {
            node: self.graph.root,
            rank: 0,
            position: 0,
            omitted: 0,
            cost: 0,
            at_onset: true,
            onset_aligned: true,
            onset_position: 0,
        });
        let mut created = 1;
        while !pending.is_empty() && !self.check_time() {
            let current = pending.pop().expect("pending state");
            self.result.explored_states += 1;
            if requirements
                .as_ref()
                .is_some_and(|required| current.position < required[current.node as usize] as usize)
            {
                continue;
            }
            if input.len() - current.position
                > usize::from(self.graph.max_match_depth[current.node as usize])
                || self.graph.match_presence[current.node as usize] & remaining[current.position]
                    != remaining[current.position]
            {
                continue;
            }
            let mut child_rank = current.rank;
            if self.graph.terminal(current.node) {
                if current.position == input.len() {
                    if current.omitted == 0 {
                        self.result.exact_found = true;
                    }
                    let ipa = self.graph.key(child_rank)?;
                    let completion =
                        options.prefix && ipa != normalized && ipa.starts_with(normalized);
                    if !completion {
                        self.consume(
                            child_rank,
                            &ipa,
                            false,
                            current.omitted,
                            initials
                                && current.onset_aligned
                                && usize::from(current.onset_position) == input.len(),
                        )?;
                    }
                }
                child_rank += 1;
            }
            for i in self.graph.begin(current.node)..self.graph.end(current.node) {
                let (label, child) = self.graph.arc(i);
                let cp = self.graph.cp(label);
                let (mut position, mut omitted, mut cost) =
                    (current.position, current.omitted, current.cost);
                let (mut at_onset, mut onset_aligned) = (current.at_onset, current.onset_aligned);
                let mut onset_position = usize::from(current.onset_position);
                let (parts, count) = unicode::expansion(cp);
                for &part in &parts[..count] {
                    let boundary = whitespace(part) || part == 0x2e;
                    let sound = !unicode::annotation(part) && !boundary;
                    let onset_match = initials
                        && onset_position < input.len()
                        && part == input[onset_position]
                        && (boundary || (sound && at_onset));
                    if onset_match {
                        onset_position += 1;
                    } else if initials && sound && at_onset {
                        onset_aligned = false;
                    }
                    if initials {
                        cost += if onset_match {
                            0
                        } else if boundary || unicode::annotation(part) {
                            1
                        } else if at_onset {
                            256
                        } else if vowel(part) {
                            16
                        } else {
                            64
                        };
                    }
                    if position < input.len() && part == input[position] {
                        position += 1;
                    } else {
                        omitted += 1;
                        if !initials {
                            cost += if unicode::annotation(part) { 1 } else { 128 };
                        }
                    }
                    if boundary {
                        at_onset = true;
                    } else if sound {
                        at_onset = false;
                    }
                }
                if requirements
                    .as_ref()
                    .is_none_or(|required| position >= required[child as usize] as usize)
                    && input.len() - position
                        <= usize::from(self.graph.max_match_depth[child as usize])
                    && self.graph.match_presence[child as usize] & remaining[position]
                        == remaining[position]
                {
                    if created == budget {
                        self.result.truncated = true;
                    } else {
                        pending.push(State {
                            node: child,
                            rank: child_rank,
                            position,
                            omitted,
                            cost,
                            at_onset,
                            onset_aligned,
                            onset_position: onset_position as u8,
                        });
                        created += 1;
                    }
                }
                child_rank += self.graph.count(child);
            }
        }
        if self.timeout || !pending.is_empty() {
            self.result.truncated = true;
        }
        Ok(())
    }
}

#[cfg(test)]
mod search_tests {
    use super::*;

    fn search(graph: &Graph, deadline: Instant) -> Search<'_> {
        Search {
            graph,
            result: QueryResult::default(),
            deadline,
            timeout: false,
            candidate_overflow: false,
            limit: 30,
            mask: 31,
            consumed: HashSet::new(),
            hits: Vec::new(),
        }
    }

    #[test]
    fn annotation_probe_respects_sounds_regions_and_expansion() {
        let path =
            std::env::temp_dir().join(format!("ipa-annotation-probe-{}.ipad", std::process::id()));
        std::fs::write(&path, include_bytes!("../tests/fixtures/omissions.ipad")).unwrap();
        {
            // SAFETY: the test owns an immutable fixture for the engine lifetime.
            let engine = unsafe { SingleEngine::load(&path).unwrap() };
            let mut probe = search(&engine.graph, Instant::now() + Duration::from_secs(60));
            let input = |raw: &str| {
                raw.chars()
                    .flat_map(|cp| {
                        let (parts, count) = unicode::expansion(cp as u32);
                        parts[..count].to_vec()
                    })
                    .collect::<Vec<_>>()
            };
            assert!(probe.annotation_only_match(&input("maɪ")).unwrap());
            assert!(probe.annotation_only_match(&input("ni xɑʊ")).unwrap());
            assert!(!probe.annotation_only_match(&input("maˈɪ")).unwrap());
            assert!(!probe.annotation_only_match(&input("nx")).unwrap());
            assert!(!probe.annotation_only_match(&input("nn")).unwrap());
            probe.mask = 1;
            assert!(!probe.annotation_only_match(&input("ni xɑʊ")).unwrap());
        }
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn unfinished_analysis_is_unknown_and_preserves_search_deadline() {
        let path =
            std::env::temp_dir().join(format!("ipa-analysis-limit-{}.ipad", std::process::id()));
        std::fs::write(&path, include_bytes!("../tests/fixtures/synthetic-24.ipad")).unwrap();
        {
            // SAFETY: immutable test fixture owned until engine drop.
            let engine = unsafe { SingleEngine::load(&path).unwrap() };
            let mut bounded = search(&engine.graph, Instant::now() + Duration::from_secs(60));
            let required = bounded.suffix_requirements_before(
                &[97, 98, 99, 100],
                Instant::now() - Duration::from_secs(1),
            );
            assert!(required.is_none());
            assert!(!bounded.timeout);
            bounded.run("ab", QueryOptions::default()).unwrap();
            assert_eq!(bounded.result.status, QueryStatus::Ok);
            assert!(bounded
                .result
                .candidates
                .iter()
                .any(|candidate| candidate.text == "about"));
        }
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn suffix_requirements_equal_independent_path_enumeration() {
        let path =
            std::env::temp_dir().join(format!("ipa-ordered-prune-{}.ipad", std::process::id()));
        std::fs::write(&path, include_bytes!("../tests/fixtures/synthetic-24.ipad")).unwrap();
        {
            // SAFETY: immutable uniquely owned test dictionary until drop.
            let engine = unsafe { SingleEngine::load(&path).unwrap() };
            for raw in ["", "a", "ab", "ba", "aaa", "ni", "ɕoː", "ni˨˩˦", "é"] {
                let query: Vec<_> = raw
                    .chars()
                    .flat_map(|cp| {
                        let (parts, n) = unicode::expansion(cp as u32);
                        parts[..n].to_vec()
                    })
                    .collect();
                let mut search = search(&engine.graph, Instant::now() + Duration::from_secs(60));
                let required = search.suffix_requirements(&query).unwrap();
                for node in 0..engine.graph.nodes {
                    let mut paths = vec![(node, Vec::<u32>::new())];
                    let mut oracle = query.len();
                    while let Some((at, symbols)) = paths.pop() {
                        if engine.graph.terminal(at) {
                            for start in 0..=query.len() {
                                let mut matched = start;
                                for cp in &symbols {
                                    if matched < query.len() && query[matched] == *cp {
                                        matched += 1;
                                    }
                                }
                                if matched == query.len() {
                                    oracle = oracle.min(start);
                                    break;
                                }
                            }
                        }
                        for arc in engine.graph.begin(at)..engine.graph.end(at) {
                            let (label, child) = engine.graph.arc(arc);
                            let (parts, n) = unicode::expansion(engine.graph.cp(label));
                            let mut next = symbols.clone();
                            next.extend_from_slice(&parts[..n]);
                            paths.push((child, next));
                        }
                    }
                    assert_eq!(
                        required[node as usize] as usize, oracle,
                        "{raw:?} node {node}"
                    );
                }
            }
        }
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn timeout_retains_completed_hits_and_reports_partial_search() {
        let path =
            std::env::temp_dir().join(format!("ipa-graph-deadline-{}.ipad", std::process::id()));
        std::fs::write(&path, include_bytes!("../tests/fixtures/synthetic-24.ipad")).unwrap();
        {
            // SAFETY: the uniquely owned fixture is unchanged until the engine
            // and every borrowed Search have been dropped below.
            let engine = unsafe { SingleEngine::load(&path).unwrap() };
            let (_, rank) = engine.graph.walk(&[u32::from(b'a')]).unwrap();
            for allow_omissions in [false, true] {
                let options = QueryOptions {
                    prefix: true,
                    allow_omissions,
                    ..QueryOptions::default()
                };
                let mut partial = search(&engine.graph, Instant::now() + Duration::from_secs(60));
                partial.consume(rank, "a", false, 0, false).unwrap();
                assert!(!partial.hits.is_empty());
                // Deterministically expire after the exact lookup completed,
                // rather than depending on hardware speed or a 1 ms race.
                partial.deadline = Instant::now() - Duration::from_secs(1);
                partial.run("a", options).unwrap();
                assert!(partial.result.exact_found);
                assert!(partial.result.truncated);
                assert!(partial.result.candidates.iter().any(|c| c.text == "alpha"));
                assert_eq!(
                    partial.result.status,
                    if allow_omissions {
                        QueryStatus::Ok
                    } else {
                        QueryStatus::Timeout
                    }
                );

                let mut empty = search(&engine.graph, Instant::now() - Duration::from_secs(1));
                empty.run("a", options).unwrap();
                assert!(empty.result.truncated);
                assert!(empty.result.candidates.is_empty());
                assert_eq!(empty.result.status, QueryStatus::Timeout);
            }
            // The extra alignment cursor occupies existing padding, preserving
            // the per-state memory bound on desktop and Android ARM64.
            assert!(std::mem::size_of::<State>() <= 32);
        }
        std::fs::remove_file(path).unwrap();
    }
}
