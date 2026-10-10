//! The only raw-memory boundary in the graph reader. Dictionaries must be
//! immutable while loaded: install updates as a new file, then load that file.
use std::fs::File;
use std::path::Path;

pub struct Mapping {
    #[cfg(all(unix, target_pointer_width = "64"))]
    pointer: std::ptr::NonNull<u8>,
    #[cfg(all(unix, target_pointer_width = "64"))]
    length: usize,
    // Holding the file does not prevent external writes; immutability is an
    // explicit caller contract, identical to the previous mmap engine.
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
    pub fn open(path: &Path) -> Result<Self, String> {
        let file = File::open(path).map_err(|_| "cannot open dictionary")?;
        let meta = file.metadata().map_err(|_| "cannot stat dictionary")?;
        if !meta.is_file() {
            return Err("dictionary is not a regular file".into());
        }
        if meta.len() < 108 || meta.len() >= 40_000_000 {
            return Err("dictionary exceeds the 40 MB runtime safety cap".into());
        }
        #[cfg(all(unix, target_pointer_width = "64"))]
        {
            use std::os::fd::AsRawFd;
            // SAFETY: valid file descriptor, nonzero validated length, read-only
            // private mapping. Ownership is retained until Mapping::drop.
            let pointer = unsafe {
                unix::mmap(
                    std::ptr::null_mut(),
                    meta.len() as usize,
                    1,
                    2,
                    file.as_raw_fd(),
                    0,
                )
            };
            if pointer as isize == -1 {
                return Err("cannot map dictionary".into());
            }
            let pointer =
                std::ptr::NonNull::new(pointer.cast::<u8>()).ok_or("cannot map dictionary")?;
            Ok(Self {
                pointer,
                length: meta.len() as usize,
                _file: file,
            })
        }
        #[cfg(not(all(unix, target_pointer_width = "64")))]
        {
            use std::io::Read;
            let mut bytes = Vec::with_capacity(meta.len() as usize);
            // A concurrently growing file cannot cause an unbounded read.
            file.take(meta.len() + 1)
                .read_to_end(&mut bytes)
                .map_err(|_| "cannot read dictionary")?;
            if bytes.len() != meta.len() as usize {
                return Err("dictionary size changed".into());
            }
            Ok(Self { bytes })
        }
    }
    pub fn bytes(&self) -> &[u8] {
        #[cfg(all(unix, target_pointer_width = "64"))]
        // SAFETY: the mapping is valid for its owned length and read-only. Its
        // lifetime exceeds the returned borrow; no mutable slice is exposed.
        unsafe {
            std::slice::from_raw_parts(self.pointer.as_ptr(), self.length)
        }
        #[cfg(not(all(unix, target_pointer_width = "64")))]
        {
            &self.bytes
        }
    }
}

// SAFETY: Mapping only exposes shared read-only slices. Its allocation remains
// mapped until the last owning Graph is dropped; query lifetime owns an Arc.
#[cfg(all(unix, target_pointer_width = "64"))]
unsafe impl Send for Mapping {}
#[cfg(all(unix, target_pointer_width = "64"))]
unsafe impl Sync for Mapping {}

#[cfg(all(unix, target_pointer_width = "64"))]
impl Drop for Mapping {
    fn drop(&mut self) {
        // SAFETY: exact pointer/length returned by mmap, unmapped exactly once.
        unsafe {
            unix::munmap(self.pointer.as_ptr().cast(), self.length);
        }
    }
}
