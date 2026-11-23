//! Resource lifecycle above independent immutable language graphs.
use crate::mapping::Mapping;
use crate::packs::{read_descriptor_file, PackDescriptor, ReverseIndex};
use crate::{
    unicode, valid_text, Candidate, Graph, QueryOptions, QueryResult, QueryStatus, Region, Search,
};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;
use std::sync::{
    atomic::{AtomicU8, Ordering},
    Arc, RwLock,
};
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PackMode {
    Off,
    ReverseOnly,
    Input,
}
impl PackMode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::ReverseOnly => "reverse",
            Self::Input => "input",
        }
    }
}

struct Pack {
    descriptor: PackDescriptor,
    // One shared descriptor pins the source generation even while its graphs
    // are off. Activation never resolves a possibly replaced pathname again.
    backing: Arc<File>,
    bundled: bool,
    mode: PackMode,
    graph: Option<Arc<Graph>>,
    reverse: Option<Arc<ReverseIndex>>,
}
impl Pack {
    fn activate(&mut self, mode: PackMode) -> Result<(), String> {
        if mode == self.mode && (mode == PackMode::Off || self.graph.is_some()) {
            return Ok(());
        }
        if mode == PackMode::Off {
            self.graph = None;
            self.reverse = None;
            self.mode = mode;
            return Ok(());
        }
        if mode == PackMode::Input && self.descriptor.flags & 2 == 0 {
            return Err("pack does not support forward input".into());
        }
        if mode == PackMode::ReverseOnly && self.descriptor.flags & 4 == 0 {
            return Err("pack does not support reverse lookup".into());
        }
        // Construct replacements before changing live resources: failed loads
        // retain the previous generation and the caller's selected policy.
        let mapping = if self.bundled {
            Mapping::open_file_range(
                self.backing
                    .try_clone()
                    .map_err(|_| "cannot clone dictionary handle")?,
                self.descriptor.graph_offset,
                self.descriptor.graph_bytes,
            )?
        } else {
            Mapping::open_file(
                self.backing
                    .try_clone()
                    .map_err(|_| "cannot clone dictionary handle")?,
            )?
        };
        let graph = Arc::new(Graph::load_mode(mapping, mode == PackMode::Input)?);
        if graph.keys != self.descriptor.keys || graph.words != self.descriptor.words {
            return Err("pack graph counts differ from its directory".into());
        }
        if self.bundled && graph.version != 2 {
            return Err("bundled graphs must use 24-bit format v2".into());
        }
        if graph.present_regions & !self.descriptor.region_mask != 0 {
            return Err("pack graph contains undeclared regions".into());
        }
        let reverse = if self.bundled && self.descriptor.reverse_bytes != 0 {
            Some(Arc::new(ReverseIndex::load(
                Mapping::open_file_range(
                    self.backing
                        .try_clone()
                        .map_err(|_| "cannot clone dictionary handle")?,
                    self.descriptor.reverse_offset,
                    self.descriptor.reverse_bytes,
                )?,
                graph.words,
                graph.keys,
            )?))
        } else {
            None
        };
        if reverse
            .as_ref()
            .is_some_and(|index| index.present_regions() & !self.descriptor.region_mask != 0)
        {
            return Err("pack reverse index contains undeclared regions".into());
        }
        self.graph = Some(graph);
        self.reverse = reverse;
        self.mode = mode;
        Ok(())
    }
}
struct State {
    packs: Vec<Pack>,
    bytes: usize,
    format: u32,
    keys: u32,
    words: u32,
}
struct Shared {
    state: RwLock<State>,
    mask: AtomicU8,
}
#[derive(Clone)]
pub struct Engine {
    shared: Arc<Shared>,
}

pub struct PackInfo {
    pub id: String,
    pub region_mask: u8,
    pub mode: PackMode,
    pub loaded: bool,
    pub graph_bytes: u64,
    pub reverse_bytes: u64,
    pub search_index_bytes: usize,
    pub forward: bool,
    pub reverse: bool,
    pub mapped_bytes: u64,
}
pub struct Pronunciation {
    pub ipa: String,
    pub region: Region,
    pub derived: bool,
    pub frequency_score: u8,
    pub pack_id: String,
}
pub struct ReverseResult {
    pub readings: Vec<Pronunciation>,
    pub truncated: bool,
}

impl Engine {
    /// # Safety
    /// Every backing file must remain immutable until the last engine clone
    /// and any in-flight query have dropped their mapped graph snapshots.
    pub unsafe fn load(path: impl AsRef<Path>) -> Result<Self, String> {
        Self::load_with_defaults(path, true)
    }
    /// # Safety
    /// Same immutable-file contract as `load`. Disabling default loads reads
    /// only the directory; graph validation is deferred until a pack is enabled.
    pub unsafe fn load_with_defaults(
        path: impl AsRef<Path>,
        load_defaults: bool,
    ) -> Result<Self, String> {
        let mut file = File::open(path.as_ref()).map_err(|_| "cannot open dictionary")?;
        let bytes = usize::try_from(file.metadata().map_err(|_| "cannot stat dictionary")?.len())
            .map_err(|_| "dictionary exceeds platform size limit")?;
        let descriptors = read_descriptor_file(&mut file)?;
        let bundled = descriptors.is_some();
        let (descriptors, format) = match descriptors {
            Some(value) => (value, 4),
            None => {
                let mut h = [0u8; 108];
                file.seek(SeekFrom::Start(0))
                    .map_err(|_| "cannot seek dictionary")?;
                file.read_exact(&mut h)
                    .map_err(|_| "truncated dictionary")?;
                let version = u32::from_le_bytes(h[8..12].try_into().unwrap());
                if &h[..8] != b"IPADWG01"
                    || !matches!(version, 2 | 3)
                    || !(108..40_000_000).contains(&bytes)
                    || u32::from_le_bytes(h[20..24].try_into().unwrap()) == 0
                    || u32::from_le_bytes(h[24..28].try_into().unwrap()) == 0
                {
                    return Err("invalid legacy dictionary header".into());
                }
                (
                    vec![PackDescriptor {
                        id: "core".into(),
                        region_mask: 31,
                        flags: 7,
                        graph_offset: 0,
                        graph_bytes: bytes as u64,
                        reverse_offset: 0,
                        reverse_bytes: 0,
                        keys: u32::from_le_bytes(h[20..24].try_into().unwrap()),
                        words: u32::from_le_bytes(h[24..28].try_into().unwrap()),
                        graph_sha256: [0; 32],
                        reverse_sha256: [0; 32],
                    }],
                    version,
                )
            }
        };
        let keys = descriptors.iter().try_fold(0u32, |a, d| {
            a.checked_add(d.keys).ok_or("pack key count overflow")
        })?;
        let words = descriptors.iter().try_fold(0u32, |a, d| {
            a.checked_add(d.words).ok_or("pack word count overflow")
        })?;
        let backing = Arc::new(file);
        let mut packs = Vec::new();
        for descriptor in descriptors {
            let flags = descriptor.flags;
            let mut pack = Pack {
                descriptor,
                backing: Arc::clone(&backing),
                bundled,
                mode: PackMode::Off,
                graph: None,
                reverse: None,
            };
            if load_defaults && flags & 1 != 0 {
                pack.activate(if flags & 2 != 0 {
                    PackMode::Input
                } else {
                    PackMode::ReverseOnly
                })?;
            }
            packs.push(pack);
        }
        Ok(Self {
            shared: Arc::new(Shared {
                state: RwLock::new(State {
                    packs,
                    bytes,
                    format,
                    keys,
                    words,
                }),
                mask: AtomicU8::new(255),
            }),
        })
    }
    /// Mount independently delivered packs into the same candidate/reverse engine.
    /// # Safety
    /// The additional backing file must remain immutable for this engine's lifetime.
    /// Existing IDs cannot be replaced in place; construct a new engine for updates.
    pub unsafe fn add_dictionary(
        &self,
        path: impl AsRef<Path>,
        load_defaults: bool,
    ) -> Result<(), String> {
        let extra = Self::load_with_defaults(path, load_defaults)?;
        let extra = Arc::try_unwrap(extra.shared)
            .map_err(|_| "unexpected shared dictionary")?
            .state
            .into_inner()
            .map_err(|_| "pack state poisoned")?;
        let mut state = self
            .shared
            .state
            .write()
            .map_err(|_| "pack state poisoned")?;
        if extra.format != 4 || state.format == 3 {
            return Err("additional dictionaries must be 24-bit pack containers".into());
        }
        if state.packs.len() + extra.packs.len() > 8
            || state
                .bytes
                .checked_add(extra.bytes)
                .is_none_or(|n| n >= 96_000_000)
        {
            return Err("installed language packs exceed runtime limits".into());
        }
        if extra.packs.iter().any(|p| {
            state.packs.iter().any(|old| {
                old.descriptor.id == p.descriptor.id
                    || old.descriptor.region_mask & p.descriptor.region_mask != 0
            })
        }) {
            return Err("duplicate pack ID or overlapping region mask".into());
        }
        let keys = state
            .keys
            .checked_add(extra.keys)
            .ok_or("pack key count overflow")?;
        let words = state
            .words
            .checked_add(extra.words)
            .ok_or("pack word count overflow")?;
        state.bytes += extra.bytes;
        state.keys = keys;
        state.words = words;
        state.format = 4;
        state.packs.extend(extra.packs);
        Ok(())
    }
    pub fn configure(&self, mask: u8) {
        self.shared.mask.store(mask, Ordering::Relaxed);
    }
    pub fn configure_legacy(&self, mask: u8) {
        let _ = self
            .shared
            .mask
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |old| {
                Some((old & 224) | (mask & 31))
            });
    }
    pub fn set_pack_mode(&self, id: &str, mode: PackMode) -> Result<(), String> {
        let mut state = self
            .shared
            .state
            .write()
            .map_err(|_| "pack state poisoned")?;
        state
            .packs
            .iter_mut()
            .find(|p| p.descriptor.id == id)
            .ok_or("unknown language pack")?
            .activate(mode)
    }
    pub fn list_packs(&self) -> Vec<PackInfo> {
        let state = self.shared.state.read().expect("pack state poisoned");
        state
            .packs
            .iter()
            .map(|p| PackInfo {
                id: p.descriptor.id.clone(),
                region_mask: p
                    .graph
                    .as_ref()
                    .map_or(p.descriptor.region_mask, |g| g.present_regions),
                mode: p.mode,
                loaded: p.graph.is_some(),
                graph_bytes: p.descriptor.graph_bytes,
                reverse_bytes: p.descriptor.reverse_bytes,
                forward: p.descriptor.flags & 2 != 0,
                reverse: p.descriptor.flags & 4 != 0,
                mapped_bytes: if p.graph.is_some() {
                    p.descriptor.graph_bytes + p.descriptor.reverse_bytes
                } else {
                    0
                },
                search_index_bytes: p
                    .graph
                    .as_ref()
                    .map_or(0, |g| g.max_match_depth.len() + g.match_presence.len() * 8),
            })
            .collect()
    }
    fn forward_graphs(&self) -> Vec<(String, Arc<Graph>)> {
        let state = self.shared.state.read().expect("pack state poisoned");
        state
            .packs
            .iter()
            .filter(|p| p.mode == PackMode::Input)
            .filter_map(|p| {
                p.graph
                    .as_ref()
                    .map(|g| (p.descriptor.id.clone(), Arc::clone(g)))
            })
            .collect()
    }
    pub fn segment_pattern(&self) -> String {
        let graphs = self.forward_graphs();
        if graphs.len() == 1 {
            return graphs[0].1.pattern.clone();
        }
        let symbols: BTreeSet<u32> = graphs
            .iter()
            .flat_map(|(_, g)| g.match_alphabet.iter().copied())
            .collect();
        if symbols.is_empty() {
            return "(?!)".into();
        }
        let mut pattern = String::from("[");
        for cp in (0x80..0x300).chain(0x1e00..0x2000) {
            let (parts, count) = unicode::expansion(cp);
            if count > 1 && parts[..count].iter().all(|p| symbols.contains(p)) {
                pattern.push_str(&format!("\\x{{{cp:x}}}"));
            }
        }
        for cp in &symbols {
            pattern.push_str(&format!("\\x{{{cp:x}}}"));
        }
        for digit in b'1'..=b'5' {
            if symbols.contains(&(0x2e9 - u32::from(digit - b'1'))) {
                pattern.push(char::from(digit));
            }
        }
        pattern.push_str("]+");
        pattern
    }
    pub fn accepts_input(&self, raw: &str) -> bool {
        Self::accepts(&self.forward_graphs(), raw)
    }
    fn accepts(graphs: &[(String, Arc<Graph>)], raw: &str) -> bool {
        if raw.is_empty() || raw.len() > 256 || !valid_text(raw, 64, true) {
            return false;
        }
        raw.chars().all(|ch| {
            let cp = if ('1'..='5').contains(&ch) {
                0x2e9 - (ch as u32 - '1' as u32)
            } else {
                ch as u32
            };
            let (parts, n) = unicode::expansion(cp);
            parts[..n].iter().all(|p| {
                graphs
                    .iter()
                    .any(|(_, g)| g.match_alphabet.binary_search(p).is_ok())
            })
        })
    }
    pub fn dictionary_bytes(&self) -> usize {
        self.shared.state.read().unwrap().bytes
    }
    pub fn format_version(&self) -> u32 {
        self.shared.state.read().unwrap().format
    }
    pub fn index_bits(&self) -> u32 {
        if self.format_version() == 3 {
            32
        } else {
            24
        }
    }
    pub fn arc_record_bytes(&self) -> u32 {
        self.index_bits() / 8 + 1
    }
    pub fn key_count(&self) -> u32 {
        self.shared.state.read().unwrap().keys
    }
    pub fn word_count(&self) -> u32 {
        self.shared.state.read().unwrap().words
    }
    pub fn node_count(&self) -> u32 {
        self.shared
            .state
            .read()
            .unwrap()
            .packs
            .iter()
            .filter_map(|p| p.graph.as_ref())
            .map(|g| g.nodes)
            .sum()
    }
    pub fn arc_count(&self) -> u32 {
        self.shared
            .state
            .read()
            .unwrap()
            .packs
            .iter()
            .filter_map(|p| p.graph.as_ref())
            .map(|g| g.arcs)
            .sum()
    }
    pub fn alphabet_count(&self) -> u32 {
        let graphs = self.forward_graphs();
        graphs
            .iter()
            .flat_map(|(_, g)| {
                (0..g.alphabet)
                    .map(|i| crate::u32le(g.section(0), i as usize * 4))
                    .collect::<Vec<_>>()
            })
            .collect::<BTreeSet<_>>()
            .len() as u32
    }
    pub fn query(&self, raw: &str, options: QueryOptions) -> QueryResult {
        let start = Instant::now();
        let deadline = start + Duration::from_millis(u64::from(options.deadline_ms.clamp(1, 60)));
        let mask = self.shared.mask.load(Ordering::Relaxed);
        let all = self.forward_graphs();
        // Legacy / explicitly single-language operation remains the exact old
        // query path, with its original state and deadline budgets.
        let graphs: Vec<_> = all
            .iter()
            .filter(|(_, g)| g.present_regions & mask != 0)
            .collect();
        if graphs.len() == 1 {
            return query_graph(&graphs[0].1, raw, options, mask, deadline, start);
        }
        let mut result = QueryResult::default();
        if raw.is_empty() {
            return result;
        }
        if !Self::accepts(&all, raw) {
            result.status = QueryStatus::UnsupportedInput;
            return result;
        }
        if graphs.is_empty() || options.candidate_limit == 0 {
            return result;
        }
        let mut slots: Vec<Option<QueryResult>> = (0..graphs.len()).map(|_| None).collect();
        // Probe every pack's exact matches before expensive omission searches.
        // Each graph retains its existing heap budget. All work shares one wall
        // deadline; only one search heap exists at a time.
        for i in 0..graphs.len() {
            slots[i] = Some(query_graph(
                &graphs[i].1,
                raw,
                QueryOptions {
                    allow_omissions: false,
                    prefix: false,
                    ..options
                },
                mask,
                deadline,
                start,
            ));
        }
        for i in 0..graphs.len() {
            if Instant::now() >= deadline {
                result.truncated = true;
                break;
            }
            let mut response = query_graph(&graphs[i].1, raw, options, mask, deadline, start);
            if let Some(exact) = slots[i].take() {
                let mut texts: HashSet<String> =
                    response.candidates.iter().map(|c| c.text.clone()).collect();
                response.candidates.extend(
                    exact
                        .candidates
                        .into_iter()
                        .filter(|c| texts.insert(c.text.clone())),
                );
                response.exact_found |= exact.exact_found;
            }
            slots[i] = Some(response);
        }
        let mut merged: Vec<(usize, Candidate)> = Vec::new();
        let mut by_text = HashMap::new();
        let mut any_supported = false;
        for (pack, response) in slots.into_iter().enumerate() {
            let Some(response) = response else { continue };
            any_supported |= response.status != QueryStatus::UnsupportedInput;
            if response.status == QueryStatus::Error {
                result.status = QueryStatus::Error;
                result.truncated = true;
            }
            result.exact_found |= response.exact_found;
            result.truncated |= response.truncated;
            result.matched_keys += response.matched_keys;
            result.matched_records += response.matched_records;
            result.explored_states += response.explored_states;
            for candidate in response.candidates {
                if let Some(&at) = by_text.get(&candidate.text) {
                    let (old_pack, old): &mut (usize, Candidate) = &mut merged[at];
                    let mut tagset: BTreeSet<&str> = old.language.split('/').collect();
                    tagset.extend(candidate.language.split('/'));
                    let language = tagset.into_iter().collect::<Vec<_>>().join("/");
                    if candidate.native_score > old.native_score {
                        *old = candidate;
                        *old_pack = pack;
                    }
                    old.language = language;
                } else {
                    by_text.insert(candidate.text.clone(), merged.len());
                    merged.push((pack, candidate));
                }
            }
        }
        merged.sort_by(|a, b| {
            b.1.native_score
                .cmp(&a.1.native_score)
                .then_with(|| a.0.cmp(&b.0))
                .then_with(|| a.1.rank.cmp(&b.1.rank))
        });
        let limit = options.candidate_limit.min(180) as usize;
        if merged.len() > limit {
            result.truncated = true;
            merged.truncate(limit);
        }
        result.candidates = merged
            .into_iter()
            .enumerate()
            .map(|(rank, (_, mut candidate))| {
                candidate.rank = rank as i32;
                candidate
            })
            .collect();
        if !any_supported {
            result.status = QueryStatus::UnsupportedInput;
        }
        if Instant::now() >= deadline {
            result.truncated = true;
            if result.candidates.is_empty() {
                result.status = QueryStatus::Timeout;
            }
        }
        if result.status == QueryStatus::Error {
            result.candidates.clear();
        }
        result.elapsed_ms = start.elapsed().as_secs_f64() * 1000.;
        result
    }
    pub fn reverse(&self, word: &str) -> Result<ReverseResult, String> {
        if !valid_text(word, 1000, true) {
            return Err("invalid lookup word".into());
        }
        let mask = self.shared.mask.load(Ordering::Relaxed);
        let packs: Vec<_> = {
            let s = self
                .shared
                .state
                .read()
                .map_err(|_| "pack state poisoned")?;
            s.packs
                .iter()
                .filter_map(|p| {
                    p.graph
                        .as_ref()
                        .filter(|_| p.descriptor.flags & 4 != 0)
                        .map(|g| (p.descriptor.id.clone(), Arc::clone(g), p.reverse.clone()))
                })
                .collect()
        };
        let mut readings = Vec::new();
        let mut truncated = false;
        for (id, graph, index) in packs {
            let Some(wid) = graph.word_id(word)? else {
                continue;
            };
            if let Some(index) = index {
                let (records, more) =
                    index.readings_filtered_limit(wid, mask, 512 - readings.len())?;
                truncated |= more;
                for record in records {
                    readings.push(Pronunciation {
                        ipa: graph.key(record.key)?,
                        region: Region::from_code(record.region),
                        derived: record.derived,
                        frequency_score: record.frequency,
                        pack_id: id.clone(),
                    });
                }
            } else {
                for key in 0..graph.keys {
                    let bytes = &graph.section(5)
                        [graph.index(4, key) as usize..graph.index(4, key + 1) as usize];
                    let mut cursor = 0;
                    let records = crate::varint(bytes, &mut cursor)?;
                    let mut current = 0;
                    for _ in 0..records {
                        let code = crate::varint(bytes, &mut cursor)?;
                        current += code >> 4;
                        let frequency = bytes[cursor];
                        cursor += 1;
                        if current == wid && mask & (1 << (code & 7)) != 0 {
                            if readings.len() == 512 {
                                truncated = true;
                                break;
                            }
                            readings.push(Pronunciation {
                                ipa: graph.key(key)?,
                                region: Region::from_code((code & 7) as u8),
                                derived: code & 8 != 0,
                                frequency_score: frequency,
                                pack_id: id.clone(),
                            });
                        }
                        if current > wid {
                            break;
                        }
                    }
                    if truncated {
                        break;
                    }
                }
            }
        }
        Ok(ReverseResult {
            readings,
            truncated,
        })
    }
}
fn query_graph(
    graph: &Graph,
    raw: &str,
    options: QueryOptions,
    mask: u8,
    deadline: Instant,
    start: Instant,
) -> QueryResult {
    let mut query = Search {
        graph,
        result: QueryResult::default(),
        deadline,
        timeout: false,
        candidate_overflow: false,
        limit: options.candidate_limit.min(180) as usize,
        mask,
        consumed: HashSet::new(),
        hits: Vec::new(),
    };
    if query.run(raw, options).is_err() {
        query.result.status = QueryStatus::Error;
        query.result.candidates.clear();
    }
    query.result.elapsed_ms = start.elapsed().as_secs_f64() * 1000.;
    query.result
}
