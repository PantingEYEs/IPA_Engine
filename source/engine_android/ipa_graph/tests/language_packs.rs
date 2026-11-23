//! Lifecycle and compatibility checks use tiny independent synthetic packs.
use ipa_graph::{Engine, PackMode, QueryOptions, QueryStatus};
use std::{
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};
static NEXT: AtomicU64 = AtomicU64::new(0);
const BUNDLE: &[u8] = include_bytes!("fixtures/language-packs.ipad");
struct File(PathBuf);
impl File {
    fn new(bytes: &[u8]) -> Self {
        let p = std::env::temp_dir().join(format!(
            "ipa-packs-{}-{}.ipad",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::write(&p, bytes).unwrap();
        Self(p)
    }
    fn load(&self, defaults: bool) -> Engine {
        unsafe { Engine::load_with_defaults(&self.0, defaults).unwrap() }
    }
}
impl Drop for File {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
fn u64_at(b: &[u8], at: usize) -> usize {
    u64::from_le_bytes(b[at..at + 8].try_into().unwrap()) as usize
}
fn strict() -> QueryOptions {
    QueryOptions {
        allow_omissions: false,
        ..QueryOptions::default()
    }
}
fn has(e: &Engine, raw: &str, word: &str) -> bool {
    e.query(raw, strict())
        .candidates
        .iter()
        .any(|c| c.text == word)
}
fn graph(i: usize) -> &'static [u8] {
    let at = 40 + 128 * i;
    let off = u64_at(BUNDLE, at + 24);
    let len = u64_at(BUNDLE, at + 32);
    &BUNDLE[off..off + len]
}
fn one_pack(i: usize) -> Vec<u8> {
    let at = 40 + 128 * i;
    let g = graph(i);
    let roff = u64_at(BUNDLE, at + 40);
    let rlen = u64_at(BUNDLE, at + 48);
    let rev = &BUNDLE[roff..roff + rlen];
    let goff = 65536usize;
    let newrev = (goff + g.len() + 65535) / 65536 * 65536;
    let mut out = vec![0; newrev + rev.len()];
    out[..40].copy_from_slice(&BUNDLE[..40]);
    out[12..16].copy_from_slice(&1u32.to_le_bytes());
    out[16..24].copy_from_slice(&BUNDLE[at + 56..at + 64]);
    out[24..32].copy_from_slice(&168u64.to_le_bytes());
    let size = out.len() as u64;
    out[32..40].copy_from_slice(&size.to_le_bytes());
    out[40..168].copy_from_slice(&BUNDLE[at..at + 128]);
    out[64..72].copy_from_slice(&(goff as u64).to_le_bytes());
    out[80..88].copy_from_slice(&(newrev as u64).to_le_bytes());
    out[goff..goff + g.len()].copy_from_slice(g);
    out[newrev..].copy_from_slice(rev);
    out
}
#[test]
fn defaults_enable_all_languages_and_reverse_is_independent_of_forward_mode() {
    let f = File::new(BUNDLE);
    let e = f.load(true);
    assert_eq!(e.format_version(), 4);
    assert_eq!(e.index_bits(), 24);
    assert_eq!(e.list_packs().len(), 4);
    assert!(e
        .list_packs()
        .iter()
        .all(|p| p.mode == PackMode::Input && p.loaded && p.search_index_bytes > 0));
    for (raw, word, id) in [
        ("a", "alpha", "core"),
        ("kot", "кот", "ru"),
        ("ki.taːb", "كتاب", "ar"),
        ("han.ɡuk̚", "한국", "ko"),
    ] {
        assert!(has(&e, raw, word), "{id}");
        let reverse = e.reverse(word).unwrap();
        assert!(reverse
            .readings
            .iter()
            .any(|r| r.ipa == raw && r.pack_id == id));
    }
    for (raw, word) in [("rka", "река"), ("ktb", "كتاب"), ("hnɡk", "한국")] {
        assert!(
            e.query(
                raw,
                QueryOptions {
                    candidate_limit: 180,
                    ..QueryOptions::default()
                }
            )
            .candidates
            .iter()
            .any(|c| c.text == word),
            "{raw}"
        );
    }
    e.set_pack_mode("ru", PackMode::ReverseOnly).unwrap();
    assert!(!has(&e, "kot", "кот"));
    assert!(!e.reverse("кот").unwrap().readings.is_empty());
    let p = e.list_packs().into_iter().find(|p| p.id == "ru").unwrap();
    assert_eq!(p.search_index_bytes, 0);
    assert!(p.mapped_bytes > 0);
    e.set_pack_mode("ru", PackMode::Off).unwrap();
    assert!(e.reverse("кот").unwrap().readings.is_empty());
    let p = e.list_packs().into_iter().find(|p| p.id == "ru").unwrap();
    assert!(!p.loaded);
    assert_eq!(p.mapped_bytes, 0);
    assert_eq!(p.search_index_bytes, 0);
    e.set_pack_mode("ru", PackMode::Input).unwrap();
    assert!(has(&e, "kot", "кот"));
    assert!(e
        .reverse("definitely-not-a-fixture-word")
        .unwrap()
        .readings
        .is_empty());
}
#[test]
fn lazy_open_and_region_filters_have_different_resource_semantics() {
    let f = File::new(BUNDLE);
    let e = f.load(false);
    assert!(e
        .list_packs()
        .iter()
        .all(|p| !p.loaded && p.mapped_bytes == 0 && p.search_index_bytes == 0));
    assert_eq!(e.segment_pattern(), "(?!)");
    assert!(!e.accepts_input("a"));
    assert!(e.set_pack_mode("not-installed", PackMode::Input).is_err());
    e.set_pack_mode("ru", PackMode::ReverseOnly).unwrap();
    assert!(!e.reverse("кот").unwrap().readings.is_empty());
    assert!(!has(&e, "kot", "кот"));
    e.set_pack_mode("ru", PackMode::Input).unwrap();
    assert!(has(&e, "kot", "кот"));
    let before = e.list_packs()[1].mapped_bytes;
    e.configure(31);
    assert!(!has(&e, "kot", "кот"));
    assert!(e.reverse("кот").unwrap().readings.is_empty());
    assert_eq!(e.list_packs()[1].mapped_bytes, before);
    e.configure(255);
    e.configure_legacy(0);
    assert!(has(&e, "kot", "кот"));
    assert!(!e.reverse("кот").unwrap().readings.is_empty());
    let clone = e.clone();
    clone.set_pack_mode("ru", PackMode::Off).unwrap();
    assert!(!e.list_packs()[1].loaded);
}
#[test]
fn core_candidates_and_diagnostics_match_the_unchanged_raw_graph() {
    let f = File::new(BUNDLE);
    let raw = File::new(graph(0));
    let e = f.load(true);
    let old = raw.load(true);
    e.configure(31);
    for input in ["a", "ba", "ɕoː", "ni˨˩˦", "ab", "x", "a🙂", ""] {
        for prefix in [false, true] {
            let options = QueryOptions {
                prefix,
                candidate_limit: 180,
                ..QueryOptions::default()
            };
            let a = e.query(input, options);
            let b = old.query(input, options);
            assert_eq!(
                (
                    a.status,
                    a.exact_found,
                    a.truncated,
                    a.matched_keys,
                    a.matched_records,
                    a.explored_states
                ),
                (
                    b.status,
                    b.exact_found,
                    b.truncated,
                    b.matched_keys,
                    b.matched_records,
                    b.explored_states
                ),
                "{input}"
            );
            let shape = |r: ipa_graph::QueryResult| {
                r.candidates
                    .into_iter()
                    .map(|c| {
                        (
                            c.text,
                            c.language,
                            c.native_score,
                            c.matched_ipa,
                            c.omitted_symbols,
                        )
                    })
                    .collect::<Vec<_>>()
            };
            assert_eq!(shape(a), shape(b), "{input}");
        }
    }
}
#[test]
fn independently_delivered_files_mount_atomically_into_one_engine() {
    let core = File::new(&one_pack(0));
    let ru = File::new(&one_pack(1));
    let e = core.load(true);
    assert_eq!(e.list_packs().len(), 1);
    let words = e.word_count();
    unsafe {
        e.add_dictionary(&ru.0, false).unwrap();
    }
    assert_eq!(e.list_packs().len(), 2);
    assert!(e.word_count() > words);
    assert!(!has(&e, "kot", "кот"));
    e.set_pack_mode("ru", PackMode::Input).unwrap();
    assert!(has(&e, "kot", "кот"));
    assert!(has(&e, "a", "alpha"));
    let before = (e.word_count(), e.dictionary_bytes());
    assert!(unsafe { e.add_dictionary(&ru.0, true) }.is_err());
    assert_eq!((e.word_count(), e.dictionary_bytes()), before);
    assert!(has(&e, "kot", "кот"));
}
#[test]
fn failed_lazy_activation_retains_the_previous_state() {
    let mut damaged = BUNDLE.to_vec();
    let off = u64_at(&damaged, 40 + 128 + 40);
    damaged[off] = b'X';
    let file = File::new(&damaged);
    assert!(unsafe { Engine::load(&file.0) }.is_err());
    let e = file.load(false);
    e.set_pack_mode("core", PackMode::Input).unwrap();
    assert!(e.set_pack_mode("ru", PackMode::Input).is_err());
    assert!(!e.list_packs()[1].loaded);
    assert!(has(&e, "a", "alpha"));
    // A structurally valid reverse record must still belong to this pack.
    let mut wrong_region = BUNDLE.to_vec();
    let off = u64_at(&wrong_region, 40 + 128 + 40);
    let words = u32::from_le_bytes(wrong_region[off + 12..off + 16].try_into().unwrap()) as usize;
    wrong_region[off + 24 + (words + 1) * 3 + 3] = 6;
    let wrong = File::new(&wrong_region);
    assert!(unsafe { Engine::load(&wrong.0) }.is_err());
}
#[test]
fn concurrent_queries_keep_valid_snapshots_during_unload() {
    let file = File::new(BUNDLE);
    let e = file.load(true);
    let worker = e.clone();
    let task = std::thread::spawn(move || {
        for _ in 0..100 {
            assert!(has(&worker, "a", "alpha"));
            let r = worker.query("kot", QueryOptions::default());
            assert_ne!(r.status, QueryStatus::Error);
        }
    });
    for _ in 0..50 {
        e.set_pack_mode("ru", PackMode::Off).unwrap();
        e.set_pack_mode("ru", PackMode::Input).unwrap();
    }
    task.join().unwrap();
    assert!(has(&e, "kot", "кот"));
}

#[cfg(unix)]
#[test]
fn atomic_path_replacement_never_switches_an_existing_engine_generation() {
    let original = File::new(&one_pack(1));
    let replacement = File::new(&one_pack(3));
    let engine = original.load(true);
    let lazy = original.load(false);
    assert!(has(&engine, "kot", "кот"));
    std::fs::rename(&replacement.0, &original.0).unwrap();

    // A newly opened engine sees the newly installed container.
    let next = original.load(true);
    assert!(has(&next, "han.ɡuk̚", "한국"));
    // Loaded and never-yet-loaded packs must both retain their original inode.
    engine.set_pack_mode("ru", PackMode::Off).unwrap();
    engine.set_pack_mode("ru", PackMode::Input).unwrap();
    lazy.set_pack_mode("ru", PackMode::ReverseOnly).unwrap();
    assert!(has(&engine, "kot", "кот"));
    assert_eq!(lazy.reverse("кот").unwrap().readings[0].ipa, "kot");
}
