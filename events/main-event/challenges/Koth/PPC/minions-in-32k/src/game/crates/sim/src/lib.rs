//! Pure game simulation shared between the server and the replay verifier.
//!
//! This crate is deliberately free of networking, async, and logging. A
//! single tick is: feed inputs with `apply_input`, call `advance()`, read the
//! returned `Snapshot`. The same inputs on the same seed produce the same
//! snapshot stream on a given machine.

pub mod arena;
pub mod powerup;
pub mod scenes;
pub mod weapon;
pub mod world;

pub use arena::Arena;
pub use world::{
    JoinError, World, CONFIRMATION_LIFETIME_SECONDS, CONFIRMATION_PICKUP_RADIUS, MAX_CONFIRMATIONS,
    PLAYER_HEIGHT, PLAYER_RADIUS,
};
