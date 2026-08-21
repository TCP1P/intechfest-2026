//! Wire protocol between server, client, replay, and spectator.
//!
//! Single source of truth for every byte that crosses the trust boundary.
//! Any new field a client can learn or send must live here.

use serde::{Deserialize, Serialize};

pub const PROTOCOL_VERSION: u16 = 13;
pub const ALPN: &[u8] = b"minions/0";
pub const MAX_PLAYERS: usize = 8;
pub const MAX_MSG_BYTES: usize = 64 * 1024;
pub const MAX_NAME_LEN: usize = 24;

pub type PlayerId = u8;
pub type Tick = u64;

/// Consistent placeholder name for a player whose real name is unknown.
pub fn fallback_name(id: PlayerId) -> String {
    format!("P{id}")
}

/// Signed 16.16 fixed-point scalar, wire-compatible across platforms.
#[derive(Copy, Clone, Debug, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Fp(pub i32);

#[derive(Copy, Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Vec3 {
    pub x: Fp,
    pub y: Fp,
    pub z: Fp,
}

#[derive(Copy, Clone, Debug, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Buttons(pub u16);

impl Buttons {
    pub const EMPTY: Self = Self(0);
    pub const FORWARD: Self = Self(1 << 0);
    pub const BACK: Self = Self(1 << 1);
    pub const LEFT: Self = Self(1 << 2);
    pub const RIGHT: Self = Self(1 << 3);
    pub const JUMP: Self = Self(1 << 4);
    pub const FIRE: Self = Self(1 << 5);
    pub const CROUCH: Self = Self(1 << 6);

    #[inline]
    pub fn contains(self, other: Self) -> bool {
        (self.0 & other.0) == other.0
    }
    #[inline]
    pub fn insert(&mut self, other: Self) {
        self.0 |= other.0;
    }
    #[inline]
    pub fn bits(self) -> u16 {
        self.0
    }
}

impl core::ops::BitOr for Buttons {
    type Output = Self;
    fn bitor(self, rhs: Self) -> Self {
        Self(self.0 | rhs.0)
    }
}
impl core::ops::BitOrAssign for Buttons {
    fn bitor_assign(&mut self, rhs: Self) {
        self.0 |= rhs.0;
    }
}

#[derive(Copy, Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Input {
    pub tick: Tick,
    pub buttons: Buttons,
    /// Yaw in 16.16 fixed-point radians; wrapped in `[-π, π)` by the server.
    pub yaw: Fp,
    pub pitch: Fp,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum ClientMessage {
    Join {
        protocol_version: u16,
        name: String,
        /// Per-match access key. Required when the server runs in
        /// instance mode (matchmade lobbies); ignored otherwise.
        key: Option<String>,
        /// Minion variant chosen on the selection screen (0..=3).
        variant: u8,
    },
    Input(Input),
    Leave,
}

/// One row of the live scoreboard. Broadcast in `ServerMessage::Roster`
/// every second and on roster changes — names, picked minions, latency,
/// and score are server-authoritative.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RosterEntry {
    pub id: PlayerId,
    pub name: String,
    pub variant: u8,
    pub rtt_ms: u16,
    pub kills: u16,
    /// Kills whose banana was collected by the killer before the victim
    /// denied it. This is the primary competitive score.
    pub confirmed_kills: u16,
    pub deaths: u16,
    pub connected: bool,
}

/// Scoreboard ordering: most confirmed kills first, then total kills, then
/// fewest deaths. The same comparator is used for live and final standings.
pub fn rank_cmp(a: &RosterEntry, b: &RosterEntry) -> std::cmp::Ordering {
    b.confirmed_kills
        .cmp(&a.confirmed_kills)
        .then(b.kills.cmp(&a.kills))
        .then(a.deaths.cmp(&b.deaths))
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum ServerMessage {
    Roster(Vec<RosterEntry>),
    /// Match ended (instance mode). The ONLY trigger for the client's
    /// game-over flow — connection loss is not a result.
    GameOver {
        standings: Vec<RosterEntry>,
    },
    Welcome {
        your_id: PlayerId,
        tick_rate_hz: u16,
    },
    Reject {
        reason: String,
    },
    PlayerJoined {
        id: PlayerId,
        name: String,
    },
    PlayerLeft {
        id: PlayerId,
    },
    /// State sync. Over the network this rides UNRELIABLE datagrams
    /// (newest wins; clients drop stale ticks) so packet loss can't
    /// head-of-line-block the world state. Its `events` arrive separately.
    Snapshot(Snapshot),
    /// Tick events split out of the snapshot onto the RELIABLE stream —
    /// kills, damage, explosions must never be lost to a dropped datagram.
    Events {
        tick: Tick,
        events: Vec<GameEvent>,
    },
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Snapshot {
    pub tick: Tick,
    pub players: Vec<PlayerState>,
    /// Live projectiles (rockets). Authoritative positions; clients
    /// render and interpolate, never simulate.
    pub projectiles: Vec<ProjectileState>,
    /// Unresolved bananas dropped by PvP kills. Only the killer can confirm
    /// one and only the victim can deny it; clients merely render the state.
    pub confirmations: Vec<ConfirmationState>,
    /// Availability per arena powerup (same order as the arena's powerup
    /// list) — drives pickup visibility/respawn presentation.
    pub powerups_up: Vec<bool>,
    pub events: Vec<GameEvent>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectileState {
    pub id: u16,
    pub pos: Vec3,
    pub vel: Vec3,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConfirmationState {
    pub id: u16,
    pub killer: PlayerId,
    pub victim: PlayerId,
    pub pos: Vec3,
    pub expires_in: u16,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PlayerState {
    pub id: PlayerId,
    pub pos: Vec3,
    pub yaw: Fp,
    pub pitch: Fp,
    pub health: u8,
    /// 0 = alive and interactive. Non-zero while dead and awaiting respawn.
    pub respawn_in: u16,
    pub crouching: bool,
    /// Remaining powerup ticks (0 = inactive) — drives the HUD badges.
    pub quad_ticks: u16,
    pub haste_ticks: u16,
    pub weapon: WeaponKind,
    /// Remaining ammo for limited weapons (rail is infinite, reads 0).
    pub ammo: u16,
    pub armor: u8,
    pub serum_ticks: u16,
}

/// Discrete events that happened during a single server tick. Bundled into
/// the tick's `Snapshot` so a client reconstructs state and timeline from
/// the same stream the replay writer sees.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum GameEvent {
    Fire {
        shooter: PlayerId,
        from: Vec3,
        to: Vec3,
        hit: Option<PlayerId>,
    },
    Damage {
        attacker: PlayerId,
        victim: PlayerId,
        amount: u8,
    },
    Kill {
        attacker: PlayerId,
        victim: PlayerId,
    },
    Spawn {
        id: PlayerId,
        pos: Vec3,
    },
    PowerupPickup {
        id: PlayerId,
        kind: PowerupKind,
    },
    /// One flamethrower emission (a few per second while held) — purely
    /// for client VFX; damage arrives as normal Damage events.
    Flame {
        shooter: PlayerId,
        from: Vec3,
        dir: Vec3,
    },
    /// Rocket detonation (VFX; damage arrives as Damage events).
    Explosion {
        at: Vec3,
    },
    ScenarioState {
        kind: ScenarioKind,
        active: bool,
    },
    ScenarioDamage {
        id: PlayerId,
        kind: ScenarioKind,
        amount: u8,
    },
    ScenarioKill {
        id: PlayerId,
        kind: ScenarioKind,
    },
    /// A PvP frag dropped a banana at the victim's authoritative position.
    ConfirmationDropped {
        confirmation_id: u16,
        killer: PlayerId,
        victim: PlayerId,
    },
    /// The killer collected their banana and converted the frag into score.
    KillConfirmed {
        confirmation_id: u16,
        killer: PlayerId,
        victim: PlayerId,
    },
    /// The victim recovered their banana before the killer could collect it.
    KillDenied {
        confirmation_id: u16,
        killer: PlayerId,
        victim: PlayerId,
    },
    /// Neither eligible player reached the banana before its deadline.
    ConfirmationExpired {
        confirmation_id: u16,
        killer: PlayerId,
        victim: PlayerId,
    },
}

impl GameEvent {
    /// If this event is a frag, return `(attacker, victim)` where `attacker`
    /// is `None` for self-kills and environmental (scenario) deaths.
    /// Single owner of the self-kill scoring rule.
    pub fn frag_death(&self) -> Option<(Option<PlayerId>, PlayerId)> {
        match *self {
            GameEvent::Kill { attacker, victim } => {
                Some(((attacker != victim).then_some(attacker), victim))
            }
            GameEvent::ScenarioKill { id, .. } => Some((None, id)),
            _ => None,
        }
    }
}

impl PowerupKind {
    /// Serde-canonical snake_case name (matches `rename_all = "snake_case"`).
    /// Single source of truth for the wire/JSON slug of each variant.
    pub fn slug(&self) -> &'static str {
        match self {
            PowerupKind::QuadDamage => "quad_damage",
            PowerupKind::Haste => "haste",
            PowerupKind::Banana => "banana",
            PowerupKind::Health => "health",
            PowerupKind::Flamethrower => "flamethrower",
            PowerupKind::Shotgun => "shotgun",
            PowerupKind::RocketLauncher => "rocket_launcher",
            PowerupKind::Armor => "armor",
            PowerupKind::MegaHealth => "mega_health",
            PowerupKind::Serum => "serum",
        }
    }
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PowerupKind {
    QuadDamage,
    Haste,
    Banana,
    /// Instant +65 HP (capped at 100), Quake-style health pack.
    Health,
    /// Weapon pickup: flamethrower with limited fuel; reverts to the
    /// rail when empty or on death.
    Flamethrower,
    Shotgun,
    RocketLauncher,
    /// +50 armor (cap 100). Armor absorbs 2/3 of incoming damage.
    Armor,
    /// +100 health up to 200; the overheal rots 1 HP/s back to 100.
    MegaHealth,
    /// PX-41 serum: 8 s berserk — incoming weapon damage is halved,
    /// slight speed boost, and the minion glows purple for everyone.
    /// Weapons keep firing normally.
    Serum,
}

#[derive(Copy, Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum WeaponKind {
    #[default]
    Rail,
    Flamethrower,
    Shotgun,
    RocketLauncher,
}

#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScenarioKind {
    KitchenLava,
}

#[derive(Debug, thiserror::Error)]
pub enum CodecError {
    #[error("message exceeds MAX_MSG_BYTES ({MAX_MSG_BYTES})")]
    TooLarge,
    #[error("bincode: {0}")]
    Bincode(#[from] bincode::Error),
}

pub fn encode<T: Serialize>(msg: &T) -> Result<Vec<u8>, CodecError> {
    let bytes = bincode::serialize(msg)?;
    if bytes.len() > MAX_MSG_BYTES {
        return Err(CodecError::TooLarge);
    }
    Ok(bytes)
}

pub fn decode<T: for<'a> Deserialize<'a>>(bytes: &[u8]) -> Result<T, CodecError> {
    if bytes.len() > MAX_MSG_BYTES {
        return Err(CodecError::TooLarge);
    }
    Ok(bincode::deserialize(bytes)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Captures the variant name serde would emit, using only serde itself
    /// (no serde_json dependency). Every unused method is unreachable for a
    /// fieldless enum.
    struct VariantName(&'static str);

    macro_rules! unsupported {
        ($($m:ident($($a:ident: $t:ty),*) -> $r:ty;)*) => {$(
            fn $m(self $(, $a: $t)*) -> Result<$r, Self::Error> {
                unreachable!("not a unit variant")
            }
        )*};
    }

    impl serde::Serializer for &mut VariantName {
        type Ok = ();
        type Error = std::fmt::Error;
        type SerializeSeq = serde::ser::Impossible<(), Self::Error>;
        type SerializeTuple = serde::ser::Impossible<(), Self::Error>;
        type SerializeTupleStruct = serde::ser::Impossible<(), Self::Error>;
        type SerializeTupleVariant = serde::ser::Impossible<(), Self::Error>;
        type SerializeMap = serde::ser::Impossible<(), Self::Error>;
        type SerializeStruct = serde::ser::Impossible<(), Self::Error>;
        type SerializeStructVariant = serde::ser::Impossible<(), Self::Error>;

        fn serialize_unit_variant(
            self,
            _name: &'static str,
            _index: u32,
            variant: &'static str,
        ) -> Result<(), Self::Error> {
            self.0 = variant;
            Ok(())
        }

        unsupported! {
            serialize_bool(_v: bool) -> ();
            serialize_i8(_v: i8) -> ();
            serialize_i16(_v: i16) -> ();
            serialize_i32(_v: i32) -> ();
            serialize_i64(_v: i64) -> ();
            serialize_u8(_v: u8) -> ();
            serialize_u16(_v: u16) -> ();
            serialize_u32(_v: u32) -> ();
            serialize_u64(_v: u64) -> ();
            serialize_f32(_v: f32) -> ();
            serialize_f64(_v: f64) -> ();
            serialize_char(_v: char) -> ();
            serialize_str(_v: &str) -> ();
            serialize_bytes(_v: &[u8]) -> ();
            serialize_none() -> ();
            serialize_unit() -> ();
            serialize_seq(_len: Option<usize>) -> Self::SerializeSeq;
            serialize_tuple(_len: usize) -> Self::SerializeTuple;
            serialize_map(_len: Option<usize>) -> Self::SerializeMap;
        }

        fn serialize_some<T: ?Sized + Serialize>(self, _v: &T) -> Result<(), Self::Error> {
            unreachable!("not a unit variant")
        }
        fn serialize_unit_struct(self, _name: &'static str) -> Result<(), Self::Error> {
            unreachable!("not a unit variant")
        }
        fn serialize_newtype_struct<T: ?Sized + Serialize>(
            self,
            _name: &'static str,
            _v: &T,
        ) -> Result<(), Self::Error> {
            unreachable!("not a unit variant")
        }
        fn serialize_newtype_variant<T: ?Sized + Serialize>(
            self,
            _name: &'static str,
            _index: u32,
            _variant: &'static str,
            _v: &T,
        ) -> Result<(), Self::Error> {
            unreachable!("not a unit variant")
        }
        fn serialize_tuple_struct(
            self,
            _name: &'static str,
            _len: usize,
        ) -> Result<Self::SerializeTupleStruct, Self::Error> {
            unreachable!("not a unit variant")
        }
        fn serialize_tuple_variant(
            self,
            _name: &'static str,
            _index: u32,
            _variant: &'static str,
            _len: usize,
        ) -> Result<Self::SerializeTupleVariant, Self::Error> {
            unreachable!("not a unit variant")
        }
        fn serialize_struct(
            self,
            _name: &'static str,
            _len: usize,
        ) -> Result<Self::SerializeStruct, Self::Error> {
            unreachable!("not a unit variant")
        }
        fn serialize_struct_variant(
            self,
            _name: &'static str,
            _index: u32,
            _variant: &'static str,
            _len: usize,
        ) -> Result<Self::SerializeStructVariant, Self::Error> {
            unreachable!("not a unit variant")
        }
    }

    fn serde_name(k: &PowerupKind) -> &'static str {
        let mut out = VariantName("");
        k.serialize(&mut out).unwrap();
        out.0
    }

    #[test]
    fn slug_matches_serde() {
        use PowerupKind::*;
        for k in [
            QuadDamage,
            Haste,
            Banana,
            Health,
            Flamethrower,
            Shotgun,
            RocketLauncher,
            Armor,
            MegaHealth,
            Serum,
        ] {
            assert_eq!(k.slug(), serde_name(&k), "slug drift for {k:?}");
        }
    }
}
