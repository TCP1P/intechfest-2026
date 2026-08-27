//! Transport-agnostic message channels between any net backend (QUIC,
//! in-process demo sim, or browser WebTransport) and the renderer.

use bevy::prelude::Resource;
use crossbeam_channel::{Receiver, Sender};
use protocol::{ClientMessage, ServerMessage};
use std::sync::Arc;

#[derive(Resource)]
pub struct NetIn {
    pub rx: Receiver<ServerMessage>,
}

#[derive(Resource)]
pub struct NetOut {
    pub tx: Sender<ClientMessage>,
}

#[derive(Clone, Debug)]
pub enum NetLifecycleEvent {
    Started,
    Ended { reason: Option<String> },
}

#[derive(Resource)]
pub struct NetLifecycle {
    pub rx: Receiver<NetLifecycleEvent>,
}

/// Reusable connection starter. Matchmaking installs one arena session at a
/// time; the same desktop process can start another after a completed match.
#[derive(Resource, Clone)]
pub struct NetStarter(Arc<dyn Fn(u8) -> Result<(), String> + Send + Sync>);

impl NetStarter {
    pub fn new(start: impl Fn(u8) -> Result<(), String> + Send + Sync + 'static) -> Self {
        Self(Arc::new(start))
    }

    pub fn start(&self, variant: u8) -> Result<(), String> {
        (self.0)(variant)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn starter_is_reusable_across_completed_matches() {
        let starts = Arc::new(AtomicUsize::new(0));
        let observed = starts.clone();
        let starter = NetStarter::new(move |_| {
            observed.fetch_add(1, Ordering::Relaxed);
            Ok(())
        });

        starter.start(0).unwrap();
        starter.start(3).unwrap();

        assert_eq!(starts.load(Ordering::Relaxed), 2);
    }
}
