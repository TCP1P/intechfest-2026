//! Deterministic RNG used by the server. Seeded from the replay header so
//! a recorded session reproduces bit-exactly on playback.

use rand::{RngCore, SeedableRng};
use rand_chacha::ChaCha12Rng;

pub struct DetRng {
    inner: ChaCha12Rng,
}

impl DetRng {
    pub fn from_seed(seed: u64) -> Self {
        Self {
            inner: ChaCha12Rng::seed_from_u64(seed),
        }
    }

    pub fn u64(&mut self) -> u64 {
        self.inner.next_u64()
    }

    pub fn range_i32(&mut self, lo: i32, hi: i32) -> i32 {
        assert!(hi > lo, "empty range");
        let span = (hi - lo) as u64;
        lo + (self.inner.next_u64() % span) as i32
    }
}
