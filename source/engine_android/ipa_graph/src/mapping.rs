//! The only raw-memory boundary in the graph reader. Dictionaries must be
//! immutable while loaded: install updates as a new file, then load that file.
use std::fs::File;
use std::path::Path;

const MAX_GRAPH_BYTES: u64 = 40_000_000;
const MAX_BUNDLE_BYTES: u64 = 96_000_000;
const PACK_ALIGNMENT: u64 = 65_536;

pub struct Mapping {
    #[cfg(all(unix, target_pointer_width = "64"))]
    pointer: std::ptr::NonNull<u8>,
    #[cfg(all(unix, target_pointer_width = "64"))]
    length: usize,
    // The host must keep each inode immutable. A held file does not prevent
    // writes by another process; atomic pathname replacement remains safe.
    #[cfg(all(unix, target_pointer_width = "64"))]
    _file: File,
    #[cfg(not(all(unix, target_pointer_width = "64")))]
    bytes: Vec<u8>,
}

#[cfg(all(unix, target_pointer_width = "64"))]
mod unix {
    use std::ffi::c_void;
    extern "C" {
        pub fn mmap(
            address: *mut c_void,
            length: usize,
            protection: i32,
            flags: i32,
            descriptor: i32,
            offset: i64,
        ) -> *mut c_void;
        pub fn munmap(address: *mut c_void, length: usize) -> i32;
    }
}

impl Mapping {
    /// Legacy whole-file graph mapping; preserves the original 40 MB bound.
    #[allow(dead_code)] // Path wrapper retained for independent loader tests.
    pub fn open(path: &Path) -> Result<Self, String> {
        let file = File::open(path).map_err(|_| "cannot open dictionary")?;
        Self::open_file(file)
    }

    /// Map an already-open legacy graph without resolving its pathname again.
    pub fn open_file(file: File) -> Result<Self, String> {
        let meta = file.metadata().map_err(|_| "cannot stat dictionary")?;
        if !meta.is_file() {
            return Err("dictionary is not a regular file".into());
        }
        if meta.len() < 108 || meta.len() >= MAX_GRAPH_BYTES {
            return Err("dictionary exceeds the 40 MB runtime safety cap".into());
        }
        let length = meta.len();
        Self::map_file(file, 0, length, length)
    }

    /// Independently owns exactly one aligned graph/reverse range. Other packs
    /// can remain closed, and dropping this object unmaps only this range.
    #[allow(dead_code)] // Path wrapper retained for independent range tests.
    pub fn open_range(path: &Path, offset: u64, length: u64) -> Result<Self, String> {
        let file = File::open(path).map_err(|_| "cannot open language pack")?;
        Self::open_file_range(file, offset, length)
    }

    /// Independently map a range of the pinned container inode. Cloned file
    /// handles remain valid after an atomic replacement or removal of its path.
    pub fn open_file_range(file: File, offset: u64, length: u64) -> Result<Self, String> {
        let meta = file.metadata().map_err(|_| "cannot stat language pack")?;
        if !meta.is_file() {
            return Err("language pack is not a regular file".into());
        }
        let end = offset
            .checked_add(length)
            .ok_or("language pack range overflow")?;
        if meta.len() >= MAX_BUNDLE_BYTES
            || offset % PACK_ALIGNMENT != 0
            || length < 24
            || length >= MAX_GRAPH_BYTES
            || end > meta.len()
        {
            return Err("invalid language pack range or size".into());
        }
        Self::map_file(file, offset, length, meta.len())
    }

    fn map_file(
        file: File,
        offset: u64,
        length: u64,
        expected_file_length: u64,
    ) -> Result<Self, String> {
        let length =
            usize::try_from(length).map_err(|_| "mapping length exceeds platform limit")?;
        let offset_signed =
            i64::try_from(offset).map_err(|_| "mapping offset exceeds platform limit")?;
        #[cfg(all(unix, target_pointer_width = "64"))]
        {
            use std::os::fd::AsRawFd;
            // SAFETY: nonempty, bounds-checked regular-file range; the offset is
            // zero or 64 KiB aligned (compatible with 4/16/64 KiB page sizes).
            // The host's immutable-inode contract lasts until this owner drops.
            let pointer = unsafe {
                unix::mmap(
                    std::ptr::null_mut(),
                    length,
                    1,
                    2,
                    file.as_raw_fd(),
                    offset_signed,
                )
            };
            if pointer as isize == -1 {
                return Err("cannot map dictionary range".into());
            }
            let pointer = std::ptr::NonNull::new(pointer.cast::<u8>())
                .ok_or("cannot map dictionary range")?;
            let mapped = Self {
                pointer,
                length,
                _file: file,
            };
            // Reject detectable concurrent growth/truncation during opening. The
            // caller still owns immutability after return; this is not a lock.
            if mapped
                ._file
                .metadata()
                .map_err(|_| "cannot restat dictionary")?
                .len()
                != expected_file_length
            {
                return Err("dictionary size changed while mapping".into());
            }
            Ok(mapped)
        }
        #[cfg(not(all(unix, target_pointer_width = "64")))]
        {
            use std::io::{Read, Seek, SeekFrom};
            let _ = offset_signed;
            let mut file = file;
            file.seek(SeekFrom::Start(offset))
                .map_err(|_| "cannot seek dictionary range")?;
            let mut bytes = Vec::with_capacity(length);
            (&mut file)
                .take(length as u64)
                .read_to_end(&mut bytes)
                .map_err(|_| "cannot read dictionary range")?;
            if bytes.len() != length
                || file
                    .metadata()
                    .map_err(|_| "cannot restat dictionary")?
                    .len()
                    != expected_file_length
            {
                return Err("dictionary size changed while reading".into());
            }
            Ok(Self { bytes })
        }
    }

    pub fn bytes(&self) -> &[u8] {
        #[cfg(all(unix, target_pointer_width = "64"))]
        // SAFETY: this owner holds the exact live read-only mapping range.
        unsafe {
            std::slice::from_raw_parts(self.pointer.as_ptr(), self.length)
        }
        #[cfg(not(all(unix, target_pointer_width = "64")))]
        {
            &self.bytes
        }
    }
}

// SAFETY: shared immutable views remain mapped until the final owner drops.
#[cfg(all(unix, target_pointer_width = "64"))]
unsafe impl Send for Mapping {}
#[cfg(all(unix, target_pointer_width = "64"))]
unsafe impl Sync for Mapping {}
#[cfg(all(unix, target_pointer_width = "64"))]
impl Drop for Mapping {
    fn drop(&mut self) {
        // SAFETY: exact pointer/length returned by mmap, unmapped once.
        unsafe {
            unix::munmap(self.pointer.as_ptr().cast(), self.length);
        }
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
            "ipa-range-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ))
    }
    #[test]
    fn ranges_have_independent_owners_and_checked_boundaries() {
        let path = path();
        let mut file = File::create(&path).unwrap();
        file.write_all(&[b'A'; 108]).unwrap();
        file.seek(SeekFrom::Start(PACK_ALIGNMENT)).unwrap();
        file.write_all(&[b'B'; 32]).unwrap();
        drop(file);
        let first = Mapping::open_range(&path, 0, 108).unwrap();
        let second = Mapping::open_range(&path, PACK_ALIGNMENT, 32).unwrap();
        assert_eq!(first.bytes(), &[b'A'; 108]);
        drop(first);
        assert_eq!(second.bytes(), &[b'B'; 32]);
        assert!(Mapping::open_range(&path, 1, 24).is_err());
        assert!(Mapping::open_range(&path, 0, 23).is_err());
        assert!(Mapping::open_range(&path, PACK_ALIGNMENT, 33).is_err());
        assert!(Mapping::open_range(&path, u64::MAX - 15, 32).is_err());
        drop(second);
        std::fs::remove_file(path).unwrap();
    }
    #[test]
    fn ranges_allow_large_carriers_but_not_large_individual_graphs() {
        let path = path();
        let mut file = File::create(&path).unwrap();
        file.write_all(&[0; 108]).unwrap();
        file.set_len(40_000_001).unwrap();
        drop(file);
        assert!(Mapping::open(&path).is_err());
        assert_eq!(
            Mapping::open_range(&path, 0, 108).unwrap().bytes().len(),
            108
        );
        assert!(Mapping::open_range(&path, 0, 40_000_000).is_err());
        std::fs::OpenOptions::new()
            .write(true)
            .open(&path)
            .unwrap()
            .set_len(MAX_BUNDLE_BYTES)
            .unwrap();
        assert!(Mapping::open_range(&path, 0, 108).is_err());
        std::fs::remove_file(path).unwrap();
    }
}
