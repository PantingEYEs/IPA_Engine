//! Directory and reverse-index readers for independently mapped language packs.
//!
//! Directory SHA-256 values describe build provenance. The installer verifies
//! the downloaded container's hash; this dependency-free reader checks bounds
//! and structural semantics, and never maps the entire carrier file.
use crate::mapping::Mapping;
use std::collections::HashSet;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

const MAX_FILE: u64 = 96_000_000;
const MAX_SLICE: u64 = 40_000_000;
const ALIGNMENT: u64 = 65_536;
const HEADER: usize = 40;
const ENTRY: usize = 128;

#[derive(Clone, Debug)]
pub struct PackDescriptor {
    pub id: String,
    pub region_mask: u8,
    pub flags: u32,
    pub graph_offset: u64,
    pub graph_bytes: u64,
    pub reverse_offset: u64,
    pub reverse_bytes: u64,
    pub keys: u32,
    pub words: u32,
    /// Build provenance; installers verify the complete downloaded file.
    #[allow(dead_code)]
    pub graph_sha256: [u8; 32],
    pub reverse_sha256: [u8; 32],
}

fn u32_at(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap())
}
fn u64_at(bytes: &[u8], at: usize) -> u64 {
    u64::from_le_bytes(bytes[at..at + 8].try_into().unwrap())
}
fn u24_at(bytes: &[u8], at: usize) -> u32 {
    u32::from(bytes[at]) | u32::from(bytes[at + 1]) << 8 | u32::from(bytes[at + 2]) << 16
}
fn read_exact(file: &mut File, bytes: &mut [u8]) -> Result<(), String> {
    file.read_exact(bytes)
        .map_err(|_| "truncated language-pack directory".into())
}

/// Legacy files return `None`; their original graph loader validates them.
/// New carriers return descriptors only, leaving every graph/reverse unmapped.
#[allow(dead_code)] // Path wrapper retained for directory tests and tools.
pub fn read_descriptor(path: &Path) -> Result<Option<Vec<PackDescriptor>>, String> {
    let mut file = File::open(path).map_err(|_| "cannot open dictionary")?;
    read_descriptor_file(&mut file)
}

/// Read the directory from the same opened inode that subsequent mappings use.
/// The caller opens it once and retains that handle even when all packs are off.
pub fn read_descriptor_file(file: &mut File) -> Result<Option<Vec<PackDescriptor>>, String> {
    file.seek(SeekFrom::Start(0))
        .map_err(|_| "cannot seek dictionary directory")?;
    let metadata = file.metadata().map_err(|_| "cannot stat dictionary")?;
    if !metadata.is_file() {
        return Err("dictionary is not a regular file".into());
    }
    let mut header = [0; HEADER];
    read_exact(file, &mut header[..8])?;
    if &header[..8] == b"IPADWG01" {
        return Ok(None);
    }
    if &header[..8] != b"IPAPACK1" {
        return Err("unknown dictionary magic".into());
    }
    if metadata.len() >= MAX_FILE {
        return Err("language-pack container exceeds 96 MB cap".into());
    }
    read_exact(file, &mut header[8..])?;
    let count = u32_at(&header, 12) as usize;
    let directory_end = u64_at(&header, 24);
    if u32_at(&header, 8) != 1
        || !(1..=8).contains(&count)
        || directory_end != (HEADER + count * ENTRY) as u64
        || u64_at(&header, 32) != metadata.len()
        || directory_end > metadata.len()
    {
        return Err("invalid language-pack header".into());
    }
    let mut directory = vec![0; count * ENTRY];
    read_exact(file, &mut directory)?;
    let mut packs = Vec::with_capacity(count);
    let mut ids = HashSet::new();
    let mut masks = 0u8;
    let mut ranges = Vec::new();
    let (mut key_count, mut word_count) = (0u32, 0u32);
    for bytes in directory.chunks_exact(ENTRY) {
        let id_end = bytes[..16]
            .iter()
            .position(|&c| c == 0)
            .ok_or("pack id is not terminated")?;
        if id_end == 0
            || bytes[id_end..16].iter().any(|&c| c != 0)
            || !bytes[..id_end]
                .iter()
                .all(|c| c.is_ascii_alphanumeric() || *c == b'_' || *c == b'-')
        {
            return Err("invalid language-pack id".into());
        }
        let id =
            String::from_utf8(bytes[..id_end].to_vec()).map_err(|_| "invalid pack id encoding")?;
        let region_mask = u32_at(bytes, 16);
        let flags = u32_at(bytes, 20);
        if region_mask == 0
            || region_mask > 255
            || masks & region_mask as u8 != 0
            || !ids.insert(id.clone())
            || flags & !7 != 0
            || flags & 6 == 0
        {
            return Err("invalid, repeated or overlapping language-pack policy".into());
        }
        masks |= region_mask as u8;
        let pack = PackDescriptor {
            id,
            region_mask: region_mask as u8,
            flags,
            graph_offset: u64_at(bytes, 24),
            graph_bytes: u64_at(bytes, 32),
            reverse_offset: u64_at(bytes, 40),
            reverse_bytes: u64_at(bytes, 48),
            keys: u32_at(bytes, 56),
            words: u32_at(bytes, 60),
            graph_sha256: bytes[64..96].try_into().unwrap(),
            reverse_sha256: bytes[96..128].try_into().unwrap(),
        };
        if pack.keys == 0 || pack.words == 0 || pack.keys > 0x00ff_ffff || pack.words > 0x00ff_ffff
        {
            return Err("invalid language-pack graph counts".into());
        }
        key_count = key_count
            .checked_add(pack.keys)
            .ok_or("pack key count overflow")?;
        word_count = word_count
            .checked_add(pack.words)
            .ok_or("pack word count overflow")?;
        check_range(
            pack.graph_offset,
            pack.graph_bytes,
            108,
            directory_end,
            metadata.len(),
            &mut ranges,
        )?;
        if flags & 4 != 0 {
            check_range(
                pack.reverse_offset,
                pack.reverse_bytes,
                24,
                directory_end,
                metadata.len(),
                &mut ranges,
            )?;
        } else if pack.reverse_offset != 0
            || pack.reverse_bytes != 0
            || pack.reverse_sha256 != [0; 32]
        {
            return Err("reverse payload exists without reverse capability".into());
        }
        packs.push(pack);
    }
    ranges.sort_unstable();
    if ranges.windows(2).any(|pair| pair[0].1 > pair[1].0)
        || key_count != u32_at(&header, 16)
        || word_count != u32_at(&header, 20)
    {
        return Err("overlapping payloads or inconsistent container totals".into());
    }
    if file
        .metadata()
        .map_err(|_| "cannot restat dictionary")?
        .len()
        != metadata.len()
    {
        return Err("dictionary size changed while reading directory".into());
    }
    Ok(Some(packs))
}

fn check_range(
    offset: u64,
    bytes: u64,
    minimum: u64,
    directory_end: u64,
    file_end: u64,
    ranges: &mut Vec<(u64, u64)>,
) -> Result<(), String> {
    let end = offset
        .checked_add(bytes)
        .ok_or("language-pack payload range overflow")?;
    if offset < directory_end
        || offset % ALIGNMENT != 0
        || bytes < minimum
        || bytes >= MAX_SLICE
        || end > file_end
    {
        return Err("invalid language-pack payload bounds".into());
    }
    ranges.push((offset, end));
    Ok(())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ReverseRecord {
    pub key: u32,
    pub region: u8,
    pub derived: bool,
    pub frequency: u8,
}

pub struct ReverseIndex {
    mapping: Mapping,
    words: u32,
    record_start: usize,
    present_regions: u8,
}
impl ReverseIndex {
    pub fn load(mapping: Mapping, expected_words: u32, expected_keys: u32) -> Result<Self, String> {
        let bytes = mapping.bytes();
        if bytes.len() < 24 || &bytes[..8] != b"IPAREV01" || u32_at(bytes, 8) != 1 {
            return Err("invalid reverse-index header".into());
        }
        let words = u32_at(bytes, 12);
        let keys = u32_at(bytes, 16);
        let records = u32_at(bytes, 20);
        if words == 0
            || keys == 0
            || words != expected_words
            || keys != expected_keys
            || words > 0x00ff_ffff
            || keys > 0x00ff_ffff
            || records > 0x00ff_ffff
        {
            return Err("reverse-index graph counts differ".into());
        }
        let record_start = 24u64 + (u64::from(words) + 1) * 3;
        if record_start + u64::from(records) * 5 != bytes.len() as u64 {
            return Err("reverse-index length differs from counts".into());
        }
        if u24_at(bytes, 24) != 0 || u24_at(bytes, 24 + words as usize * 3) != records {
            return Err("invalid reverse-index offset endpoints".into());
        }
        let mut present_regions = 0u8;
        for word in 0..words as usize {
            let start = u24_at(bytes, 24 + word * 3);
            let end = u24_at(bytes, 27 + word * 3);
            if start > end || end > records {
                return Err("invalid reverse-index offsets".into());
            }
            let mut previous = None;
            for record in start..end {
                let at = record_start as usize + record as usize * 5;
                let key = u24_at(bytes, at);
                let flags = bytes[at + 3];
                let order = (key, flags & 7);
                if key >= keys
                    || flags & !15 != 0
                    || bytes[at + 4] > 180
                    || previous.is_some_and(|old| old >= order)
                {
                    return Err("invalid or unsorted reverse-index record".into());
                }
                present_regions |= 1 << (flags & 7);
                previous = Some(order);
            }
        }
        Ok(Self {
            mapping,
            words,
            record_start: record_start as usize,
            present_regions,
        })
    }
    pub fn present_regions(&self) -> u8 {
        self.present_regions
    }
    /// Inspection helpers; the runtime uses bounded, region-filtered reads.
    #[allow(dead_code)]
    pub fn readings(&self, word: u32) -> Result<Vec<ReverseRecord>, String> {
        self.readings_limit(word, usize::MAX)
    }
    #[allow(dead_code)]
    pub fn readings_limit(&self, word: u32, limit: usize) -> Result<Vec<ReverseRecord>, String> {
        self.readings_masked_limit(word, 255, limit)
    }
    /// Filters before limiting, so a disabled region cannot hide later readings.
    #[allow(dead_code)]
    pub fn readings_masked_limit(
        &self,
        word: u32,
        mask: u8,
        limit: usize,
    ) -> Result<Vec<ReverseRecord>, String> {
        self.readings_filtered_limit(word, mask, limit)
            .map(|(records, _)| records)
    }
    /// Returns only enabled records and whether another enabled record exists.
    /// Allocation is bounded by `limit`, even when the word has many readings.
    pub fn readings_filtered_limit(
        &self,
        word: u32,
        mask: u8,
        limit: usize,
    ) -> Result<(Vec<ReverseRecord>, bool), String> {
        if word >= self.words {
            return Err("reverse-index word id out of range".into());
        }
        let bytes = self.mapping.bytes();
        let start = u24_at(bytes, 24 + word as usize * 3);
        let end = u24_at(bytes, 27 + word as usize * 3);
        let mut result = Vec::with_capacity(((end - start) as usize).min(limit));
        for record in start..end {
            let at = self.record_start + record as usize * 5;
            let flags = bytes[at + 3];
            if mask & (1 << (flags & 7)) == 0 {
                continue;
            }
            if result.len() == limit {
                return Ok((result, true));
            }
            result.push(ReverseRecord {
                key: u24_at(bytes, at),
                region: flags & 7,
                derived: flags & 8 != 0,
                frequency: bytes[at + 4],
            });
        }
        Ok((result, false))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Seek, SeekFrom, Write};
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    fn path() -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "ipa-pack-reader-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ))
    }
    fn put32(bytes: &mut [u8], at: usize, value: u32) {
        bytes[at..at + 4].copy_from_slice(&value.to_le_bytes());
    }
    fn put64(bytes: &mut [u8], at: usize, value: u64) {
        bytes[at..at + 8].copy_from_slice(&value.to_le_bytes());
    }
    fn put24(bytes: &mut [u8], at: usize, value: u32) {
        bytes[at..at + 3].copy_from_slice(&value.to_le_bytes()[..3]);
    }
    fn descriptor_bytes() -> Vec<u8> {
        let mut bytes = vec![0; HEADER + ENTRY];
        bytes[..8].copy_from_slice(b"IPAPACK1");
        put32(&mut bytes, 8, 1);
        put32(&mut bytes, 12, 1);
        put32(&mut bytes, 16, 3);
        put32(&mut bytes, 20, 2);
        put64(&mut bytes, 24, 168);
        put64(&mut bytes, 32, ALIGNMENT * 2 + 24);
        bytes[40..44].copy_from_slice(b"core");
        put32(&mut bytes, 56, 31);
        put32(&mut bytes, 60, 7);
        put64(&mut bytes, 64, ALIGNMENT);
        put64(&mut bytes, 72, 108);
        put64(&mut bytes, 80, ALIGNMENT * 2);
        put64(&mut bytes, 88, 24);
        put32(&mut bytes, 96, 3);
        put32(&mut bytes, 100, 2);
        bytes
    }
    fn inspect(bytes: &[u8], length: u64) -> Result<Option<Vec<PackDescriptor>>, String> {
        let path = path();
        let mut file = File::create(&path).unwrap();
        file.write_all(bytes).unwrap();
        file.set_len(length).unwrap();
        drop(file);
        let result = read_descriptor(&path);
        std::fs::remove_file(path).unwrap();
        result
    }
    #[test]
    fn directory_is_read_without_opening_graphs() {
        let bytes = descriptor_bytes();
        let descriptors = inspect(&bytes, ALIGNMENT * 2 + 24).unwrap().unwrap();
        assert_eq!(descriptors.len(), 1);
        assert_eq!(descriptors[0].id, "core");
        assert_eq!(descriptors[0].region_mask, 31);
        assert_eq!(descriptors[0].graph_offset, ALIGNMENT);
        assert_eq!(inspect(b"IPADWG01", 8).unwrap().is_none(), true);
        assert!(inspect(b"BADMAGIC", 8).is_err());
    }
    #[test]
    fn malformed_headers_ranges_and_policies_are_rejected() {
        let good = descriptor_bytes();
        let length = ALIGNMENT * 2 + 24;
        for (at, value, width) in [
            (8, 2, 4),
            (12, 0, 4),
            (12, 9, 4),
            (16, 4, 4),
            (24, 167, 8),
            (32, length + 1, 8),
            (56, 256, 4),
            (56, 0, 4),
            (60, 15, 4),
            (64, ALIGNMENT + 1, 8),
            (72, 107, 8),
            (80, ALIGNMENT, 8),
            (88, 40_000_000, 8),
            (96, 0, 4),
        ] {
            let mut bad = good.clone();
            if width == 8 {
                put64(&mut bad, at, value);
            } else {
                put32(&mut bad, at, value as u32);
            }
            assert!(inspect(&bad, length).is_err(), "accepted mutation at {at}");
        }
        let mut bad = good.clone();
        bad[40] = b'/';
        assert!(inspect(&bad, length).is_err());
        let mut bad = good.clone();
        bad[46] = b'x';
        assert!(inspect(&bad, length).is_err());
        assert!(inspect(&good[..100], 100).is_err());
        assert!(inspect(&good, MAX_FILE).is_err());
    }
    #[test]
    fn duplicate_ids_and_region_ownership_are_rejected() {
        let good = descriptor_bytes();
        let mut bytes = good.clone();
        bytes.extend_from_slice(&good[40..]);
        put32(&mut bytes, 12, 2);
        put32(&mut bytes, 16, 6);
        put32(&mut bytes, 20, 4);
        put64(&mut bytes, 24, 296);
        put64(&mut bytes, 32, ALIGNMENT * 4 + 24);
        let second = HEADER + ENTRY;
        put64(&mut bytes, second + 24, ALIGNMENT * 3);
        put64(&mut bytes, second + 40, ALIGNMENT * 4);
        assert!(inspect(&bytes, ALIGNMENT * 4 + 24).is_err());
        bytes[second..second + 16].fill(0);
        bytes[second..second + 2].copy_from_slice(b"ru");
        assert!(inspect(&bytes, ALIGNMENT * 4 + 24).is_err());
        put32(&mut bytes, second + 16, 32);
        assert_eq!(
            inspect(&bytes, ALIGNMENT * 4 + 24).unwrap().unwrap().len(),
            2
        );
    }
    fn reverse_bytes() -> Vec<u8> {
        let mut bytes = vec![0; 24 + 9 + 15];
        bytes[..8].copy_from_slice(b"IPAREV01");
        put32(&mut bytes, 8, 1);
        put32(&mut bytes, 12, 2);
        put32(&mut bytes, 16, 3);
        put32(&mut bytes, 20, 3);
        put24(&mut bytes, 24, 0);
        put24(&mut bytes, 27, 2);
        put24(&mut bytes, 30, 3);
        for (at, key, flags, freq) in [(33, 0, 0, 180), (38, 0, 9, 0), (43, 2, 7, 1)] {
            put24(&mut bytes, at, key);
            bytes[at + 3] = flags;
            bytes[at + 4] = freq;
        }
        bytes
    }
    fn reverse(bytes: &[u8]) -> Result<ReverseIndex, String> {
        let path = path();
        let mut file = File::create(&path).unwrap();
        file.seek(SeekFrom::Start(ALIGNMENT)).unwrap();
        file.write_all(bytes).unwrap();
        drop(file);
        let mapping = Mapping::open_range(&path, ALIGNMENT, bytes.len() as u64)?;
        std::fs::remove_file(path).unwrap();
        ReverseIndex::load(mapping, 2, 3)
    }
    #[test]
    fn reverse_lookup_checks_sorting_and_filters_before_limit() {
        let index = reverse(&reverse_bytes()).unwrap();
        assert_eq!(index.present_regions(), 131);
        assert_eq!(index.readings(0).unwrap().len(), 2);
        assert_eq!(index.readings_limit(0, 1).unwrap()[0].region, 0);
        let reading = index.readings_masked_limit(0, 2, 1).unwrap();
        assert_eq!(reading.len(), 1);
        assert_eq!(reading[0].region, 1);
        assert!(reading[0].derived);
        assert!(!index.readings_filtered_limit(0, 1, 1).unwrap().1);
        assert!(index.readings_filtered_limit(0, 3, 1).unwrap().1);
        assert!(!index.readings_filtered_limit(0, 2, 1).unwrap().1);
        assert!(index.readings_filtered_limit(0, 2, 0).unwrap().1);
        assert!(!index.readings_filtered_limit(0, 0, 0).unwrap().1);
        assert_eq!(index.readings(1).unwrap()[0].key, 2);
        assert!(index.readings(2).is_err());
        assert!(index.readings_masked_limit(0, 0, 3).unwrap().is_empty());
        for (at, value) in [
            (24, 1),
            (27, 4),
            (30, 2),
            (36, 16),
            (37, 181),
            (38, 0),
            (41, 0),
            (43, 3),
        ] {
            let mut bad = reverse_bytes();
            bad[at] = value;
            // at38 is a no-op; use the key byte to force descending ordering.
            if at == 38 {
                bad[33] = 1;
            }
            assert!(reverse(&bad).is_err(), "accepted reverse mutation at {at}");
        }
        let mut bad = reverse_bytes();
        bad.push(0);
        assert!(reverse(&bad).is_err());
    }
}
