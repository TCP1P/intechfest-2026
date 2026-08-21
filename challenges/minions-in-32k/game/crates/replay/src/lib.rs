//! Deterministic replay record/playback.
//!
//! File format (little-endian):
//!   magic          [u8; 4] = b"MNRP"
//!   format_version u16     = 0
//!   protocol_ver   u16
//!   tick_rate_hz   u16
//!   reserved       u16
//!   seed           u64
//!   start_unix_ms  u64
//!   events: repeated { length: u32, payload: bincode<ReplayEvent> }
//!
//! Events contain everything needed to re-simulate the round: inputs, joins,
//! leaves, feature-flag flips. Snapshots are optional and only present in
//! `-verbose` recordings (useful for early-stage sanity checking).

use protocol::{Input, PlayerId, Snapshot, Tick};
use serde::{Deserialize, Serialize};
use std::path::Path;
use tokio::io::{AsyncReadExt, AsyncWriteExt, BufReader, BufWriter};

pub const MAGIC: [u8; 4] = *b"MNRP";
pub const FORMAT_VERSION: u16 = 2;

#[derive(Copy, Clone, Debug)]
pub struct Header {
    pub format_version: u16,
    pub protocol_version: u16,
    pub tick_rate_hz: u16,
    pub seed: u64,
    pub start_unix_ms: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum ReplayEvent {
    PlayerJoin {
        tick: Tick,
        id: PlayerId,
        name: String,
    },
    PlayerLeave {
        tick: Tick,
        id: PlayerId,
    },
    Input {
        id: PlayerId,
        input: Input,
    },
    /// Feature-flag flip; logged into the stream so replays mid-flip reconstruct correctly.
    FlagFlip {
        tick: Tick,
        key: String,
        value: String,
    },
    /// Optional verbose snapshot, for early sanity checks.
    Snapshot(Snapshot),
    /// Lag-compensation rewind change (server-measured RTT -> ticks).
    /// Part of sim state: replays must apply it or re-simulation
    /// diverges. (format v1)
    PlayerDelay {
        tick: Tick,
        id: PlayerId,
        ticks: u8,
    },
    /// Terminal marker: the match has ended and the replay is finalized.
    /// Kept at its existing variant index for back-compat.
    /// Spectating refuses to serve a replay lacking this event (no live scouting).
    MatchEnd {
        tick: Tick,
    },
    /// The official clock started. Any confirmation dropped during staging is
    /// removed so an unscored frag cannot produce scored evidence. (format v2)
    /// New variants are always appended to preserve earlier bincode indices.
    ScoringWindowStarted {
        tick: Tick,
    },
}

#[derive(Debug, thiserror::Error)]
pub enum ReplayError {
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("bincode: {0}")]
    Bincode(#[from] bincode::Error),
    #[error("bad magic")]
    BadMagic,
    #[error("unsupported format version {0}")]
    UnsupportedVersion(u16),
    #[error("oversized event ({0} bytes)")]
    OversizedEvent(u32),
}

const MAX_EVENT_BYTES: u32 = 1 << 20;

pub struct Writer {
    file: BufWriter<tokio::fs::File>,
}

impl Writer {
    pub async fn create(path: impl AsRef<Path>, header: Header) -> Result<Self, ReplayError> {
        let file = tokio::fs::File::create(path).await?;
        let mut w = BufWriter::new(file);
        w.write_all(&MAGIC).await?;
        w.write_all(&header.format_version.to_le_bytes()).await?;
        w.write_all(&header.protocol_version.to_le_bytes()).await?;
        w.write_all(&header.tick_rate_hz.to_le_bytes()).await?;
        w.write_all(&0u16.to_le_bytes()).await?; // reserved
        w.write_all(&header.seed.to_le_bytes()).await?;
        w.write_all(&header.start_unix_ms.to_le_bytes()).await?;
        w.flush().await?;
        Ok(Self { file: w })
    }

    pub async fn write_event(&mut self, event: &ReplayEvent) -> Result<(), ReplayError> {
        let bytes = bincode::serialize(event)?;
        let len = bytes.len() as u32;
        if len > MAX_EVENT_BYTES {
            return Err(ReplayError::OversizedEvent(len));
        }
        self.file.write_all(&len.to_le_bytes()).await?;
        self.file.write_all(&bytes).await?;
        Ok(())
    }

    pub async fn flush(&mut self) -> Result<(), ReplayError> {
        self.file.flush().await?;
        Ok(())
    }
}

pub struct Reader {
    file: BufReader<tokio::fs::File>,
    pub header: Header,
}

impl Reader {
    pub async fn open(path: impl AsRef<Path>) -> Result<Self, ReplayError> {
        let file = tokio::fs::File::open(path).await?;
        let mut r = BufReader::new(file);
        let mut magic = [0u8; 4];
        r.read_exact(&mut magic).await?;
        if magic != MAGIC {
            return Err(ReplayError::BadMagic);
        }
        let format_version = read_u16(&mut r).await?;
        // v0 files decode fine (PlayerDelay was appended to the enum)
        if format_version > FORMAT_VERSION {
            return Err(ReplayError::UnsupportedVersion(format_version));
        }
        let protocol_version = read_u16(&mut r).await?;
        let tick_rate_hz = read_u16(&mut r).await?;
        let _reserved = read_u16(&mut r).await?;
        let seed = read_u64(&mut r).await?;
        let start_unix_ms = read_u64(&mut r).await?;
        Ok(Self {
            file: r,
            header: Header {
                format_version,
                protocol_version,
                tick_rate_hz,
                seed,
                start_unix_ms,
            },
        })
    }

    /// Returns `Ok(None)` at clean EOF.
    pub async fn read_event(&mut self) -> Result<Option<ReplayEvent>, ReplayError> {
        let mut len_buf = [0u8; 4];
        match self.file.read_exact(&mut len_buf).await {
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
            Err(e) => return Err(e.into()),
        }
        let len = u32::from_le_bytes(len_buf);
        if len > MAX_EVENT_BYTES {
            return Err(ReplayError::OversizedEvent(len));
        }
        let mut buf = vec![0u8; len as usize];
        self.file.read_exact(&mut buf).await?;
        Ok(Some(bincode::deserialize(&buf)?))
    }
}

async fn read_u16<R: AsyncReadExt + Unpin>(r: &mut R) -> Result<u16, ReplayError> {
    let mut b = [0u8; 2];
    r.read_exact(&mut b).await?;
    Ok(u16::from_le_bytes(b))
}
async fn read_u64<R: AsyncReadExt + Unpin>(r: &mut R) -> Result<u64, ReplayError> {
    let mut b = [0u8; 8];
    r.read_exact(&mut b).await?;
    Ok(u64::from_le_bytes(b))
}
