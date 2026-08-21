//! Shared math, constants, and determinism helpers.

pub mod fp;
pub mod rng;
pub mod trig;

pub use fp::{FpExt, Vec3Ext};
pub use rng::DetRng;

/// Gameplay tick rate. 64 Hz is decided (see PLAN.md).
pub const DEFAULT_TICK_HZ: u16 = 64;

/// Default internal render-target dimensions. 256 × 128 = 32 768 pixels,
/// which is what "32K" means in this event edition's name.
///
/// The client can be redirected to a different resolution via a sibling
/// `config.toml`. This override is intentionally undocumented in public
/// materials — discovering it is part of the CTF's cheat progression.
pub const DEFAULT_RENDER_WIDTH: u32 = 256;
pub const DEFAULT_RENDER_HEIGHT: u32 = 128;
