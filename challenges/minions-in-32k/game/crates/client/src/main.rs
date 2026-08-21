mod channels;
mod config;
mod demo;
#[cfg(not(target_arch = "wasm32"))]
mod gateway;
#[cfg(not(target_arch = "wasm32"))]
mod net;
#[cfg(target_arch = "wasm32")]
mod net_wasm;

use bevy::animation::graph::AnimationNodeIndex;
#[cfg(not(target_arch = "wasm32"))]
use bevy::input::keyboard::{Key, KeyboardInput};
use bevy::input::mouse::MouseMotion;
#[cfg(not(target_arch = "wasm32"))]
use bevy::input::ButtonState;
use bevy::prelude::*;
use bevy::render::render_resource::{Extent3d, TextureDimension, TextureFormat, TextureUsages};
use bevy::render::view::RenderLayers;
use bevy::window::{CursorGrabMode, PresentMode, PrimaryWindow, WindowResolution};
use clap::Parser;
use common::FpExt;
use config::RenderConfig;
use protocol::{
    Buttons, ClientMessage, Fp, GameEvent, Input, PlayerId, PlayerState, PowerupKind, ServerMessage,
};
use sim::world::eye_height;
use std::collections::HashMap;
#[cfg(not(target_arch = "wasm32"))]
use std::time::{SystemTime, UNIX_EPOCH};
#[cfg(not(target_arch = "wasm32"))]
use zeroize::Zeroize;

const MOUSE_SENSITIVITY: f32 = 0.0025;
const FIRE_TRAIL_SECONDS: f32 = 0.25;

#[derive(Parser, Debug, Resource, Clone)]
#[command(version, about)]
pub struct ClientArgs {
    /// Stable RSCTF KotH capability. Exchanges once for short arena credentials.
    #[arg(long, conflicts_with_all = ["demo", "remote", "key", "instance_id", "cert_hash"])]
    pub token: Option<String>,
    /// Trusted RSCTF arena gateway used by the lobby or with --token.
    #[arg(
        long,
        default_value = "https://minions-private.tcp.1pc.tf",
        requires = "token"
    )]
    pub gateway: String,
    /// Connect to a remote game server at `host:port`.
    #[arg(long, conflicts_with = "demo")]
    pub remote: Option<String>,
    /// Play offline against local AI bots instead of opening the event lobby.
    #[arg(long)]
    pub demo: bool,
    /// Display name sent in the Join handshake (or shown above the head in demo).
    #[arg(long, default_value = "player")]
    pub name: String,
    /// Per-match access key (matchmade instance servers require it).
    #[arg(long)]
    pub key: Option<String>,
    /// 16-byte hex routing id for the udp-router (matchmade games, paired
    /// with `--remote`). When set, every datagram is prefixed with this id so
    /// the router demuxes the session onto the right match pod.
    #[arg(long)]
    pub instance_id: Option<String>,
    /// Pin the server's cert by SHA-256 of its DER bytes (64 hex chars). Set by
    /// the matchmaker's connection line; takes precedence over MINIONS_CERTHASH
    /// and the dev fallback. Absent => existing dev fallback (local bots).
    #[arg(long)]
    pub cert_hash: Option<String>,
}

impl ClientArgs {
    pub fn resolved_mode(&self) -> ClientMode {
        if self.demo {
            ClientMode::Demo
        } else if self.remote.is_some() {
            ClientMode::Remote
        } else {
            ClientMode::Lobby
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClientMode {
    Demo,
    Lobby,
    Remote,
}

#[cfg(test)]
mod cli_tests {
    use super::*;

    #[test]
    fn no_arguments_open_the_secure_event_lobby() {
        let args = ClientArgs::try_parse_from(["client"]).unwrap();
        assert_eq!(args.resolved_mode(), ClientMode::Lobby);
        assert!(args.token.is_none());
    }

    #[test]
    fn offline_demo_remains_explicitly_available() {
        let args = ClientArgs::try_parse_from(["client", "--demo"]).unwrap();
        assert_eq!(args.resolved_mode(), ClientMode::Demo);
    }

    #[test]
    fn event_token_cannot_be_mixed_with_direct_arena_secrets() {
        assert!(ClientArgs::try_parse_from([
            "client",
            "--token",
            "koth_abcdefgh-123456",
            "--key",
            "arena-secret"
        ])
        .is_err());
    }

    #[cfg(not(target_arch = "wasm32"))]
    #[test]
    fn lobby_mask_never_renders_the_token_secret() {
        let token = "koth_super-secret-team-capability";
        let masked = masked_lobby_token(token);
        assert!(masked.starts_with("koth_"));
        assert!(masked.contains(&format!("{} characters", token.len())));
        assert!(!masked.contains("super"));
        assert!(!masked.contains("secret"));
        assert!(!masked.contains("capability"));
    }

    #[cfg(not(target_arch = "wasm32"))]
    #[test]
    fn transient_gateway_attempt_keeps_the_lobby_token_for_retry() {
        let args = ClientArgs::try_parse_from(["client"]).unwrap();
        let token = LobbyToken("koth_team-capability-123456".into());
        let request = lobby_request_args(&args, &token);
        assert_eq!(
            request.token.as_deref(),
            Some("koth_team-capability-123456")
        );
        assert_eq!(token.0, "koth_team-capability-123456");
    }

    #[cfg(not(target_arch = "wasm32"))]
    #[test]
    fn command_line_capability_moves_into_zeroizing_lobby_storage() {
        let mut args =
            ClientArgs::try_parse_from(["client", "--token", "koth_team-capability-123456"])
                .unwrap();
        let token = take_lobby_token(&mut args);
        assert_eq!(token.0, "koth_team-capability-123456");
        assert!(args.token.is_none());
    }

    #[cfg(not(target_arch = "wasm32"))]
    #[test]
    fn crown_wait_message_rounds_up_and_explains_automatic_retry() {
        let message = crown_retry_message(91_001, 0);
        assert!(message.contains("01:32"));
        assert!(message.contains("token is saved"));
        assert!(message.contains("reconnecting automatically"));
    }

    #[test]
    fn completed_event_match_returns_to_lobby_instead_of_exiting() {
        let args = ClientArgs::try_parse_from(["client"]).unwrap();
        assert_eq!(post_match_screen(&args), Screen::Lobby);
    }

    #[test]
    fn completed_demo_match_can_restart_selection() {
        let args = ClientArgs::try_parse_from(["client", "--demo"]).unwrap();
        assert_eq!(post_match_screen(&args), Screen::Select);
    }
}

/// App flow: authenticate in the lobby -> pick a minion -> connect -> play.
#[derive(States, Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
enum Screen {
    #[default]
    Lobby,
    Select,
    Connect,
    Play,
    Over,
}

#[cfg(not(target_arch = "wasm32"))]
#[derive(Resource, Default)]
struct LobbyToken(String);

#[cfg(not(target_arch = "wasm32"))]
impl LobbyToken {
    fn replace(&mut self, mut token: String) {
        self.0.zeroize();
        std::mem::swap(&mut self.0, &mut token);
        token.zeroize();
    }

    fn clear(&mut self) {
        self.0.zeroize();
        self.0.clear();
    }
}

#[cfg(not(target_arch = "wasm32"))]
impl Drop for LobbyToken {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn take_lobby_token(args: &mut ClientArgs) -> LobbyToken {
    LobbyToken(args.token.take().unwrap_or_default())
}

#[cfg(not(target_arch = "wasm32"))]
#[derive(Clone, Copy, Default)]
enum LobbyStatusTone {
    #[default]
    Neutral,
    Busy,
    Waiting,
    Error,
}

#[cfg(not(target_arch = "wasm32"))]
#[derive(Resource)]
struct LobbyStatus {
    message: String,
    tone: LobbyStatusTone,
    retry_at_unix_ms: Option<i64>,
}

#[cfg(not(target_arch = "wasm32"))]
impl Default for LobbyStatus {
    fn default() -> Self {
        Self {
            message: "Paste the KotH token from your RSCTF challenge page.".into(),
            tone: LobbyStatusTone::Neutral,
            retry_at_unix_ms: None,
        }
    }
}

#[cfg(not(target_arch = "wasm32"))]
struct GatewayFailure {
    message: String,
    retry_at_unix_ms: Option<i64>,
    token_rejected: bool,
}

#[cfg(not(target_arch = "wasm32"))]
type GatewayOutcome = Result<ClientArgs, GatewayFailure>;

#[cfg(not(target_arch = "wasm32"))]
#[derive(Resource)]
struct LobbyExchange {
    tx: crossbeam_channel::Sender<GatewayOutcome>,
    rx: crossbeam_channel::Receiver<GatewayOutcome>,
    pending: bool,
}

#[cfg(not(target_arch = "wasm32"))]
impl Default for LobbyExchange {
    fn default() -> Self {
        let (tx, rx) = crossbeam_channel::bounded(1);
        Self {
            tx,
            rx,
            pending: false,
        }
    }
}

#[derive(Resource, Default)]
struct Roster(Vec<protocol::RosterEntry>);

impl Roster {
    fn name(&self, id: PlayerId) -> String {
        self.0
            .iter()
            .find(|e| e.id == id)
            .map(|e| e.name.clone())
            .unwrap_or_else(|| protocol::fallback_name(id))
    }
}

/// Recent frags shown top-right. Newest first; entries expire after
/// `KILL_FEED_TTL` and fade over the last `KILL_FEED_FADE`.
#[derive(Resource, Default)]
struct KillFeed(std::collections::VecDeque<KillLine>);

struct KillLine {
    text: String,
    born: f32,
    /// true when the local player was the killer or the victim — highlighted.
    mine: bool,
}

const KILL_FEED_MAX: usize = 6;
const KILL_FEED_TTL: f32 = 6.0;
const KILL_FEED_FADE: f32 = 1.5;

impl KillFeed {
    fn push(&mut self, text: String, born: f32, mine: bool) {
        self.0.push_front(KillLine { text, born, mine });
        self.0.truncate(KILL_FEED_MAX);
    }
}

#[derive(Component)]
struct KillFeedSlot(usize);

#[derive(Resource, Default)]
struct FinalStandings {
    rows: Vec<protocol::RosterEntry>,
    /// True when the transport died without a server-authoritative GameOver.
    lost: bool,
    message: Option<String>,
}

#[derive(Resource, Default)]
struct SelectedVariant(usize);

/// Deploy inputs ignored until this time — the click that focuses the
/// window must not pick a minion.
#[derive(Resource, Default)]
struct SelectArmedAt(f32);

#[derive(Resource, Default)]
struct MyPlayer {
    id: Option<PlayerId>,
    tick: u64,
    pos: Vec3,
    yaw: f32,
    pitch: f32,
    alive: bool,
    health: u8,
    respawn_in_ticks: u16,
    crouching: bool,
    quad_ticks: u16,
    haste_ticks: u16,
    weapon: protocol::WeaponKind,
    ammo: u16,
    armor: u8,
    serum_ticks: u16,
    // camera smoothing between 64 Hz snapshots
    pos_from: Vec3,
    pos_to: Vec3,
    interp_age: f32,
    lerp_dur: f32,
    last_snap_at: f32,
}

#[derive(Component)]
struct HealthText;

#[derive(Component)]
struct StatusText;

#[derive(Component)]
struct ConfirmationText;

#[derive(Resource, Default)]
struct ActiveConfirmations(Vec<protocol::ConfirmationState>);

#[derive(Resource, Default)]
struct ConfirmationEnts {
    by_id: HashMap<u16, Entity>,
}

#[derive(Resource, Default)]
struct LastSnapshotTick(protocol::Tick);

#[derive(Component)]
struct ConfirmationVis {
    base_y: f32,
    expires_in: u16,
}

#[derive(Component)]
struct GunViewmodel;

/// Which weapon this viewmodel group represents.
#[derive(Component)]
struct WeaponView(protocol::WeaponKind);

#[derive(Component)]
struct ViewAnchor(Vec3);

/// Viewmodel recoil impulse (1.0 on fire, exponential recovery).
#[derive(Resource, Default)]
struct ViewKick(f32);

/// Marker for podium-row demo minions that loop one specific animation
/// clip continuously. Not in the sim — pure render decoration for
/// `--demo` mode so players can inspect each animation without waiting
/// for it to trigger naturally.
#[derive(Component)]
struct Showcase;

#[derive(Resource, Default)]
struct LocalLook {
    yaw: f32,
    pitch: f32,
}

#[derive(Resource, PartialEq, Eq, Clone, Copy, Default)]
enum CameraMode {
    #[default]
    FirstPerson,
    ThirdPerson,
    TopDown,
}

#[derive(Resource, Default)]
struct MouseCaptured(bool);

#[derive(Component)]
struct GameCamera;

#[derive(Component)]
struct ElevatorPlatform(sim::arena::Elevator);

/// Kitchen-lava scenario presentation. `level` eases 0..1 so the lava
/// surface rises in and drains out instead of popping.
#[derive(Resource, Default)]
struct LavaState {
    active: bool,
    level: f32,
}

#[derive(Resource)]
struct LavaMat(Handle<StandardMaterial>);

#[derive(Component)]
struct LavaPlane {
    base_y: f32,
    min: Vec3,
    max: Vec3,
}

#[derive(Component)]
struct LavaBanner;

fn lava_anim(
    time: Res<Time>,
    mut lava: ResMut<LavaState>,
    mat: Option<Res<LavaMat>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut trails: ResMut<FireTrails>,
    mut q_plane: Query<(&mut Transform, &mut Visibility, &LavaPlane)>,
    mut q_banner: Query<(&mut Text, &mut Visibility), (With<LavaBanner>, Without<LavaPlane>)>,
) {
    let dt = time.delta_seconds();
    let t = time.elapsed_seconds();
    let target = if lava.active { 1.0 } else { 0.0 };
    lava.level += (target - lava.level).clamp(-dt / 1.5, dt / 1.5);
    let level = lava.level;

    for (mut tf, mut vis, plane) in q_plane.iter_mut() {
        *vis = if level > 0.01 {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
        // molten surface rises out of the floor and gently slops around
        tf.translation.y = plane.base_y - 0.3 + level * (0.36 + (t * 2.3).sin() * 0.03);
        if let Some(mat) = mat.as_ref() {
            if let Some(m) = materials.get_mut(&mat.0) {
                let pulse = 3.2 + ((t * 3.1).sin() + (t * 7.7).sin() * 0.4) * 1.2;
                m.emissive = LinearRgba::new(pulse, pulse * 0.22, 0.02, 1.0) * level;
            }
        }
        // eruption jets: a few short-lived flame trails at pseudo-random
        // spots (hashed frame index — no RNG needed for decoration)
        if lava.active {
            let n = (t * 60.0) as u32;
            for k in 0..2u32 {
                let h =
                    (n.wrapping_mul(2654435761).wrapping_add(k * 40503)) as f32 / u32::MAX as f32;
                let h2 =
                    (n.wrapping_mul(40503).wrapping_add(k * 2654435761)) as f32 / u32::MAX as f32;
                let p = Vec3::new(
                    plane.min.x + (plane.max.x - plane.min.x) * h,
                    tf.translation.y,
                    plane.min.z + (plane.max.z - plane.min.z) * h2,
                );
                trails.entries.push(Trail {
                    from: p,
                    to: p + Vec3::new((h - 0.5) * 0.8, 1.2 + h2 * 1.8, (h2 - 0.5) * 0.8),
                    life: 0.3,
                    hit: false,
                    flame: true,
                });
            }
        }
    }

    if let Ok((mut text, mut vis)) = q_banner.get_single_mut() {
        *vis = if lava.active {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
        if lava.active {
            let a = 0.65 + (t * 5.0).sin().abs() * 0.35;
            text.sections[0].style.color = Color::srgba(1.0, 0.45, 0.10, a);
        }
    }
}

fn animate_elevators(my: Res<MyPlayer>, mut q: Query<(&ElevatorPlatform, &mut Transform)>) {
    for (e, mut tf) in q.iter_mut() {
        tf.translation.y = e.0.top_y(my.tick, 64) - e.0.thickness * 0.5;
    }
}

#[derive(Component)]
struct PlayerAvatar {
    #[allow(dead_code)] // phase 2 scoring + kill-feed UI
    id: PlayerId,
    variant: usize,
    last_pos: Vec3,
    desired_clip: AnimClip,
    fire_until: f32,
    hit_until: f32,
    serum_active: bool,
    /// drives which weapon_* glb node is visible on this avatar
    held_weapon: protocol::WeaponKind,
    /// false for selection/showcase podium minions — they are placed by
    /// hand and must not be touched by snapshot interpolation.
    net_driven: bool,
    // Snapshot interpolation: render lerps from -> to over one tick so
    // remote players glide instead of stepping at 64 Hz.
    interp_from: Vec3,
    interp_to: Vec3,
    yaw_from: f32,
    yaw_to: f32,
    interp_age: f32,
    /// Snapshot arrival cadence (EMA). Frames don't align with the 64 Hz
    /// stream — lerping over a fixed 1/64 s fast-forwards then freezes
    /// whenever a frame consumes two snapshots, a visible micro-stutter.
    lerp_dur: f32,
    last_snap_at: f32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum AnimClip {
    Idle = 0,
    Run = 1,
    Strafe = 2,
    Jump = 3,
    Fire = 4,
    Hit = 5,
    Die = 6,
    Crouch = 7,
}

const MINION_ANIM_COUNT: usize = 8;
const MINION_VARIANT_COUNT: usize = 4;

struct MinionAnimSet {
    graph: Handle<AnimationGraph>,
    nodes: [AnimationNodeIndex; MINION_ANIM_COUNT],
}

#[derive(Resource)]
struct MinionAnims {
    variants: Vec<MinionAnimSet>,
}

#[derive(Component)]
struct AvatarAnim {
    avatar: Entity,
    variant: usize,
    current: AnimationNodeIndex,
}

#[derive(Resource, Default)]
struct Avatars {
    by_id: HashMap<PlayerId, Entity>,
}

#[derive(Resource, Default)]
struct RocketEnts {
    by_id: HashMap<u16, Entity>,
}

#[derive(Component)]
struct RocketVis {
    vel: Vec3,
}

#[derive(Resource, Default)]
struct FireTrails {
    entries: Vec<Trail>,
}

struct Trail {
    from: Vec3,
    to: Vec3,
    life: f32,
    hit: bool,
    flame: bool,
}

#[derive(Component)]
struct RenderTargetSprite;

fn main() {
    #[cfg(not(target_arch = "wasm32"))]
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info,wgpu=warn,naga=warn")),
        )
        .init();
    #[cfg(target_arch = "wasm32")]
    console_error_panic_hook::set_once();

    #[cfg(not(target_arch = "wasm32"))]
    let mut args = ClientArgs::parse();
    #[cfg(not(target_arch = "wasm32"))]
    let initial_lobby_token = take_lobby_token(&mut args);
    #[cfg(not(target_arch = "wasm32"))]
    if !initial_lobby_token.0.is_empty() {
        if let Err(error) = gateway::resolve_capability(&mut args, &initial_lobby_token.0) {
            eprintln!("Minions in 32K could not join the event: {error}");
            std::process::exit(2);
        }
    }
    // In the browser, connection parameters come from the page URL.
    // No host/port in the URL => offline demo vs bots, same as the exe.
    #[cfg(target_arch = "wasm32")]
    let args = ClientArgs {
        token: None,
        gateway: String::new(),
        remote: match (net_wasm::query("host"), net_wasm::query("port")) {
            (Some(h), Some(p)) if !h.is_empty() && !p.is_empty() => Some(format!("{h}:{p}")),
            _ => None,
        },
        demo: false,
        name: net_wasm::query("name").unwrap_or_else(|| "web_player".into()),
        key: net_wasm::query("key"),
        instance_id: None,
        cert_hash: None,
    };
    let render = RenderConfig::load();
    tracing::info!(
        width = render.width,
        height = render.height,
        "render resolution"
    );
    #[cfg(not(target_arch = "wasm32"))]
    let mode = args.resolved_mode();
    // Browser builds have no native clipboard or HTTPS exchange thread. A URL
    // without arena parameters therefore remains the explicit web demo.
    #[cfg(target_arch = "wasm32")]
    let mode = if args.remote.is_some() {
        ClientMode::Remote
    } else {
        ClientMode::Demo
    };
    let initial_screen = if mode == ClientMode::Lobby {
        Screen::Lobby
    } else {
        Screen::Select
    };
    tracing::info!(?mode, remote = ?args.remote, "client mode");

    let mut app = App::new();
    // All assets (zone glbs, minion glb) are embedded in the binary — the
    // shipped client is a single exe. Only config.toml stays on disk.
    app.add_plugins(bevy_embedded_assets::EmbeddedAssetPlugin {
        mode: bevy_embedded_assets::PluginMode::ReplaceDefault,
    });
    app.add_plugins(
        DefaultPlugins
            .build()
            .disable::<bevy::log::LogPlugin>()
            .set(ImagePlugin::default_nearest())
            .set(WindowPlugin {
                primary_window: Some(Window {
                    title: format!("minions-in-32k ({:?})", mode).to_lowercase(),
                    resolution: WindowResolution::new(1280.0, 800.0),
                    present_mode: PresentMode::AutoVsync,
                    resizable: true,
                    ..default()
                }),
                ..default()
            }),
    )
    .insert_resource(args.clone())
    .insert_resource(render)
    .insert_resource(MyPlayer::default())
    .insert_resource(LocalLook::default())
    .insert_resource(CameraMode::default())
    .insert_resource(MouseCaptured(false))
    .insert_resource(Avatars::default())
    .insert_resource(RocketEnts::default())
    .insert_resource(ConfirmationEnts::default())
    .insert_resource(ActiveConfirmations::default())
    .insert_resource(LastSnapshotTick::default())
    .insert_resource(ViewKick::default())
    .insert_resource(PowerupsUp::default())
    .insert_resource(FireTrails::default())
    .insert_resource(LavaState::default())
    .insert_resource(AudioVolumes::default())
    .insert_resource(Streaks::default())
    .insert_resource(MenuOpen::default())
    .insert_resource(ClearColor(Color::srgb(0.05, 0.05, 0.08)));

    match mode {
        ClientMode::Demo => {
            app.add_plugins(demo::DemoPlugin { args: args.clone() });
        }
        ClientMode::Remote => {
            #[cfg(not(target_arch = "wasm32"))]
            app.add_plugins(net::NetPlugin { args: args.clone() });
            #[cfg(target_arch = "wasm32")]
            app.add_plugins(net_wasm::WasmNetPlugin { args: args.clone() });
        }
        ClientMode::Lobby => {
            #[cfg(not(target_arch = "wasm32"))]
            app.add_plugins(net::NetPlugin { args: args.clone() });
            #[cfg(target_arch = "wasm32")]
            app.add_plugins(demo::DemoPlugin { args: args.clone() });
        }
    }

    #[cfg(not(target_arch = "wasm32"))]
    app.insert_resource(initial_lobby_token)
        .init_resource::<LobbyStatus>()
        .init_resource::<LobbyExchange>();

    app.insert_state(initial_screen)
        .init_resource::<Roster>()
        .init_resource::<KillFeed>()
        .init_resource::<SelectedVariant>()
        .init_resource::<SelectArmedAt>()
        .init_resource::<FinalStandings>();
    app.add_systems(
        Startup,
        (
            setup,
            spawn_arena,
            load_minion_anims,
            spawn_hud,
            spawn_fps_counter,
            spawn_showcase,
            setup_rocket_assets,
            setup_minion_icons,
            setup_audio,
            spawn_options_menu,
        ),
    )
    .add_systems(OnEnter(Screen::Select), select_enter)
    .add_systems(OnExit(Screen::Select), despawn_all::<SelectUi>)
    .add_systems(OnEnter(Screen::Connect), connect_enter)
    .add_systems(OnExit(Screen::Connect), despawn_all::<ConnectUi>)
    .add_systems(
        OnEnter(Screen::Play),
        (play_enter, spawn_tab_board, announce_slayer),
    )
    .add_systems(OnExit(Screen::Play), despawn_all::<TabBoardRoot>)
    .add_systems(OnEnter(Screen::Over), over_enter)
    .add_systems(OnExit(Screen::Over), despawn_all::<OverUi>)
    .add_systems(
        Update,
        (
            (select_input, select_camera).run_if(in_state(Screen::Select)),
            connect_pulse.run_if(in_state(Screen::Connect)),
            over_input.run_if(in_state(Screen::Over)),
            interpolate_avatars,
            (
                mouse_look,
                send_input,
                update_camera,
                update_hud,
                tab_board,
                fix_avatar_variants,
            )
                .run_if(in_state(Screen::Play)),
            drain_net,
            fly_rockets,
            serum_glow,
            viewmodel_motion,
            muzzle_flash,
            sync_held_weapons,
            (
                lava_anim,
                fps_counter,
                flash_decay,
                propagate_icon_layers,
                esc_menu,
                slider_input,
                slider_fill,
                apply_volumes,
                jumbotron_slides,
                #[cfg(target_arch = "wasm32")]
                wasm_pointer_lock_retry,
            ),
            pickup_anim,
            tick_fire_trails,
            render_fire_trails,
            fit_sprite_to_window,
            link_animation_players,
            drive_avatar_animations,
            animate_elevators,
        ),
    )
    .add_systems(Update, animate_confirmations)
    .add_systems(Update, update_kill_feed.run_if(in_state(Screen::Play)));

    #[cfg(not(target_arch = "wasm32"))]
    app.add_systems(OnEnter(Screen::Lobby), lobby_enter)
        .add_systems(OnExit(Screen::Lobby), despawn_all::<LobbyUi>)
        .add_systems(
            Update,
            (
                lobby_poll_exchange,
                lobby_retry_tick,
                lobby_keyboard_input,
                lobby_button_input,
                lobby_render,
            )
                .chain()
                .run_if(in_state(Screen::Lobby)),
        );

    app.run();
}

fn setup(
    mut commands: Commands,
    render: Res<RenderConfig>,
    q_window: Query<&mut Window, With<PrimaryWindow>>,
    mut images: ResMut<Assets<Image>>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut mouse_cap: ResMut<MouseCaptured>,
) {
    let size = Extent3d {
        width: render.width,
        height: render.height,
        depth_or_array_layers: 1,
    };
    let mut rt_image = Image::new_fill(
        size,
        TextureDimension::D2,
        &[0, 0, 0, 255],
        TextureFormat::Bgra8UnormSrgb,
        bevy::render::render_asset::RenderAssetUsages::default(),
    );
    rt_image.texture_descriptor.usage =
        TextureUsages::TEXTURE_BINDING | TextureUsages::COPY_DST | TextureUsages::RENDER_ATTACHMENT;
    let rt_handle = images.add(rt_image);

    let cam_3d = commands
        .spawn((
            Camera3dBundle {
                camera: Camera {
                    order: 0,
                    target: bevy::render::camera::RenderTarget::Image(rt_handle.clone()),
                    clear_color: ClearColorConfig::Custom(Color::srgb(0.1, 0.12, 0.18)),
                    ..default()
                },
                transform: Transform::from_xyz(0.0, 2.0, 0.0),
                ..default()
            },
            RenderLayers::layer(0),
            GameCamera,
        ))
        .id();

    // Gun viewmodel: a chunky stylized gun parented to the FPS camera so it
    // sits in the bottom-right corner of every frame. Toggled off in
    // top-down mode (see `update_camera`).
    let gun_body = meshes.add(Cuboid::new(0.10, 0.10, 0.32));
    let gun_barrel = meshes.add(Cylinder::new(0.035, 0.32));
    let gun_grip = meshes.add(Cuboid::new(0.06, 0.16, 0.08));
    let gun_sight = meshes.add(Cuboid::new(0.04, 0.04, 0.06));
    let gun_drum = meshes.add(Cylinder::new(0.07, 0.07));
    let body_mat = materials.add(Color::srgb(0.18, 0.20, 0.23));
    let barrel_mat = materials.add(Color::srgb(0.10, 0.12, 0.15));
    let grip_mat = materials.add(Color::srgb(0.40, 0.25, 0.10));
    let sight_mat = materials.add(Color::srgb(0.10, 0.85, 0.95));
    let drum_mat = materials.add(Color::srgb(1.0, 0.55, 0.10));
    let gun_anchor = Vec3::new(0.30, -0.22, -0.55); // x: right, y: down, z: forward
    commands
        .entity(cam_3d)
        // Camera3dBundle has no visibility components; the viewmodel
        // children need a visible ancestor or B0004 fires every frame.
        .insert(VisibilityBundle::default())
        .with_children(|p| {
            p.spawn((
                SpatialBundle::from_transform(
                    Transform::from_translation(gun_anchor)
                        .with_rotation(Quat::from_rotation_y(-0.10)),
                ),
                GunViewmodel,
                WeaponView(protocol::WeaponKind::Rail),
                ViewAnchor(gun_anchor),
                RenderLayers::layer(0),
            ))
            .with_children(|g| {
                g.spawn((
                    PbrBundle {
                        mesh: gun_body,
                        material: body_mat,
                        transform: Transform::from_xyz(0.0, 0.0, 0.0),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PbrBundle {
                        mesh: gun_barrel,
                        material: barrel_mat,
                        transform: Transform::from_xyz(0.0, 0.02, -0.30)
                            .with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PbrBundle {
                        mesh: gun_grip,
                        material: grip_mat,
                        transform: Transform::from_xyz(0.0, -0.12, 0.05),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PbrBundle {
                        mesh: gun_sight,
                        material: sight_mat,
                        transform: Transform::from_xyz(0.0, 0.08, -0.10),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PbrBundle {
                        mesh: gun_drum,
                        material: drum_mat,
                        transform: Transform::from_xyz(0.0, 0.0, 0.10)
                            .with_rotation(Quat::from_rotation_z(std::f32::consts::FRAC_PI_2)),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PointLightBundle {
                        point_light: PointLight {
                            color: Color::srgb(1.0, 0.85, 0.45),
                            intensity: 0.0,
                            range: 6.0,
                            shadows_enabled: false,
                            ..default()
                        },
                        transform: Transform::from_xyz(0.0, 0.02, -0.45),
                        ..default()
                    },
                    RenderLayers::layer(0),
                    MuzzleFlash,
                ));
            });

            // Flamethrower viewmodel: squat tank + wide nozzle.
            let tank = meshes.add(Cylinder::new(0.07, 0.26));
            let nozzle = meshes.add(Cylinder::new(0.045, 0.18));
            let bell = meshes.add(Cuboid::new(0.11, 0.11, 0.06));
            let tank_mat = materials.add(Color::srgb(0.95, 0.45, 0.10));
            let dark_mat = materials.add(Color::srgb(0.12, 0.13, 0.16));
            p.spawn((
                SpatialBundle::from_transform(
                    Transform::from_translation(gun_anchor)
                        .with_rotation(Quat::from_rotation_y(-0.10)),
                ),
                GunViewmodel,
                WeaponView(protocol::WeaponKind::Flamethrower),
                ViewAnchor(gun_anchor),
                RenderLayers::layer(0),
            ))
            .with_children(|g| {
                g.spawn((
                    PbrBundle {
                        mesh: tank.clone(),
                        material: tank_mat.clone(),
                        transform: Transform::from_xyz(0.0, -0.02, 0.04)
                            .with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PbrBundle {
                        mesh: nozzle,
                        material: dark_mat.clone(),
                        transform: Transform::from_xyz(0.0, 0.0, -0.22)
                            .with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PbrBundle {
                        mesh: bell,
                        material: tank_mat.clone(),
                        transform: Transform::from_xyz(0.0, 0.0, -0.33),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PointLightBundle {
                        point_light: PointLight {
                            color: Color::srgb(1.0, 0.85, 0.45),
                            intensity: 0.0,
                            range: 6.0,
                            shadows_enabled: false,
                            ..default()
                        },
                        transform: Transform::from_xyz(0.0, 0.02, -0.45),
                        ..default()
                    },
                    RenderLayers::layer(0),
                    MuzzleFlash,
                ));
            });

            // Shotgun viewmodel: double barrels over a wood stock.
            let barrel = meshes.add(Cylinder::new(0.028, 0.40));
            let stock = meshes.add(Cuboid::new(0.09, 0.09, 0.26));
            let steel_mat = materials.add(Color::srgb(0.55, 0.58, 0.64));
            let wood_mat = materials.add(Color::srgb(0.45, 0.28, 0.12));
            p.spawn((
                SpatialBundle::from_transform(
                    Transform::from_translation(gun_anchor)
                        .with_rotation(Quat::from_rotation_y(-0.10)),
                ),
                GunViewmodel,
                WeaponView(protocol::WeaponKind::Shotgun),
                ViewAnchor(gun_anchor),
                RenderLayers::layer(0),
            ))
            .with_children(|g| {
                for dx in [-0.032f32, 0.032] {
                    g.spawn((
                        PbrBundle {
                            mesh: barrel.clone(),
                            material: steel_mat.clone(),
                            transform: Transform::from_xyz(dx, 0.02, -0.26)
                                .with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                            ..default()
                        },
                        RenderLayers::layer(0),
                    ));
                }
                g.spawn((
                    PbrBundle {
                        mesh: stock,
                        material: wood_mat,
                        transform: Transform::from_xyz(0.0, -0.03, 0.06),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PointLightBundle {
                        point_light: PointLight {
                            color: Color::srgb(1.0, 0.85, 0.45),
                            intensity: 0.0,
                            range: 6.0,
                            shadows_enabled: false,
                            ..default()
                        },
                        transform: Transform::from_xyz(0.0, 0.02, -0.45),
                        ..default()
                    },
                    RenderLayers::layer(0),
                    MuzzleFlash,
                ));
            });

            // Bazooka viewmodel: one fat red tube past the shoulder.
            let tube = meshes.add(Cylinder::new(0.075, 0.55));
            let muzzle_ring = meshes.add(Cylinder::new(0.09, 0.05));
            let red_mat = materials.add(Color::srgb(0.80, 0.16, 0.14));
            p.spawn((
                SpatialBundle::from_transform(
                    Transform::from_translation(gun_anchor + Vec3::new(0.0, 0.04, 0.0))
                        .with_rotation(Quat::from_rotation_y(-0.10)),
                ),
                GunViewmodel,
                WeaponView(protocol::WeaponKind::RocketLauncher),
                ViewAnchor(gun_anchor + Vec3::new(0.0, 0.04, 0.0)),
                RenderLayers::layer(0),
            ))
            .with_children(|g| {
                g.spawn((
                    PbrBundle {
                        mesh: tube,
                        material: red_mat.clone(),
                        transform: Transform::from_xyz(0.0, 0.0, -0.10)
                            .with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PbrBundle {
                        mesh: muzzle_ring,
                        material: dark_mat,
                        transform: Transform::from_xyz(0.0, 0.0, -0.40)
                            .with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
                g.spawn((
                    PointLightBundle {
                        point_light: PointLight {
                            color: Color::srgb(1.0, 0.85, 0.45),
                            intensity: 0.0,
                            range: 6.0,
                            shadows_enabled: false,
                            ..default()
                        },
                        transform: Transform::from_xyz(0.0, 0.02, -0.45),
                        ..default()
                    },
                    RenderLayers::layer(0),
                    MuzzleFlash,
                ));
            });
        });

    commands.spawn((
        Camera2dBundle {
            camera: Camera {
                order: 1,
                ..default()
            },
            ..default()
        },
        RenderLayers::layer(1),
        // Without this, Bevy attaches UI to the first/lowest-order camera —
        // our 3D camera, which renders to a 256×128 texture, so the UI gets
        // baked into the RT and disappears. Pin UI to the window-targeted
        // 2D camera so the crosshair/HUD render at native window resolution.
        IsDefaultUiCamera,
    ));
    commands.spawn((
        SpriteBundle {
            texture: rt_handle,
            ..default()
        },
        RenderLayers::layer(1),
        RenderTargetSprite,
    ));

    commands.spawn((
        DirectionalLightBundle {
            directional_light: DirectionalLight {
                illuminance: 10_000.0,
                shadows_enabled: false,
                ..default()
            },
            transform: Transform::from_xyz(10.0, 20.0, 6.0).looking_at(Vec3::ZERO, Vec3::Y),
            ..default()
        },
        RenderLayers::layer(0),
    ));

    // Grab cursor on launch.
    // Cursor grabs on entering Play, not at boot — the selection screen
    // is keyboard-driven and shouldn't trap the mouse.
    let _ = (q_window, &mut mouse_cap);
}

fn spawn_arena(
    mut commands: Commands,
    asset_server: Res<AssetServer>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    let arena = sim::Arena::gru_lab_v3();

    // Dark safety-net floor under everything. Zone glbs bring their own
    // finished floor meshes on top.
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Plane3d::default().mesh().size(120.0, 80.0)),
            material: materials.add(Color::srgb(0.05, 0.05, 0.07)),
            transform: Transform::from_xyz(0.0, arena.floor_y - 0.02, 0.0),
            ..default()
        },
        RenderLayers::layer(0),
    ));

    // Themed zone visuals: each glb is authored in world-space, identity
    // transform places it where the sim expects.
    for name in [
        "sports",
        "sleeping",
        "machine_room",
        "kitchen",
        "bathrooms",
        "hall",
        "sky",
    ] {
        commands.spawn((
            SceneBundle {
                scene: asset_server.load(format!("zones/{name}.glb#Scene0")),
                ..default()
            },
            RenderLayers::layer(0),
        ));
    }

    // Render only collision walls the glbs don't already cover. For v1
    // that's just the showcase ("secret") room behind the south doorway.
    let wall_mesh = meshes.add(Cuboid::new(1.0, 1.0, 1.0));
    let wall_mat = materials.add(StandardMaterial {
        base_color: Color::srgb(0.32, 0.33, 0.38),
        perceptual_roughness: 0.9,
        ..default()
    });
    for wall in &arena.walls {
        if sim::Arena::classify_wall(wall) != "secret" {
            continue;
        }
        let center = (wall.max + wall.min) * 0.5;
        let size = wall.max - wall.min;
        commands.spawn((
            PbrBundle {
                mesh: wall_mesh.clone(),
                material: wall_mat.clone(),
                transform: Transform {
                    translation: center,
                    scale: size,
                    ..default()
                },
                ..default()
            },
            RenderLayers::layer(0),
        ));
    }

    // Elevator platforms. The sim derives their position from the tick, so
    // the render side runs the same pure function — no net traffic.
    let elev_mat = materials.add(StandardMaterial {
        base_color: Color::srgb(0.95, 0.71, 0.25),
        emissive: LinearRgba::new(0.25, 0.17, 0.03, 1.0),
        perceptual_roughness: 0.6,
        ..default()
    });
    for e in &arena.elevators {
        commands.spawn((
            PbrBundle {
                mesh: meshes.add(Cuboid::new(e.half_x * 2.0, e.thickness, e.half_z * 2.0)),
                material: elev_mat.clone(),
                transform: Transform::from_xyz(
                    e.center_x,
                    e.y_bottom - e.thickness * 0.5,
                    e.center_z,
                ),
                ..default()
            },
            ElevatorPlatform(*e),
            RenderLayers::layer(0),
        ));
    }

    // Kitchen-lava hazard surface. Hidden until the server's ScenarioState
    // says the scenario is live; lava_anim raises and pulses it.
    if let Some(zone) = arena.zones.iter().find(|z| z.name == "kitchen") {
        let center = (zone.bounds.min + zone.bounds.max) * 0.5;
        let size = zone.bounds.max - zone.bounds.min;
        let mat = materials.add(StandardMaterial {
            base_color: Color::srgb(0.95, 0.30, 0.05),
            emissive: LinearRgba::new(4.0, 0.9, 0.05, 1.0),
            perceptual_roughness: 0.4,
            ..default()
        });
        commands.insert_resource(LavaMat(mat.clone()));
        commands.spawn((
            PbrBundle {
                mesh: meshes.add(Plane3d::default().mesh().size(size.x, size.z)),
                material: mat,
                transform: Transform::from_xyz(center.x, arena.floor_y - 0.3, center.z),
                visibility: Visibility::Hidden,
                ..default()
            },
            LavaPlane {
                base_y: arena.floor_y,
                min: zone.bounds.min,
                max: zone.bounds.max,
            },
            RenderLayers::layer(0),
        ));
    }

    // Jump-pad pucks are baked into the zone glbs by ZoneBuild.jump_pad,
    // so the trigger and its visual can never drift apart.

    // Pickups: a steel pad with the item floating above it. The item
    // bobs/spins and disappears while taken (snapshot powerups_up).
    let pad_mesh = meshes.add(Cylinder::new(0.55, 0.07));
    let pad_mat = materials.add(StandardMaterial {
        base_color: Color::srgb(0.30, 0.33, 0.40),
        perceptual_roughness: 0.8,
        ..default()
    });
    for (index, pickup) in arena.powerups.iter().enumerate() {
        let base = Vec3::new(pickup.center.x, pickup.center.y + 0.035, pickup.center.z);
        let ring_color = match pickup.kind {
            PowerupKind::QuadDamage => Color::srgb(0.75, 0.25, 0.95),
            PowerupKind::Haste => Color::srgb(0.20, 0.95, 0.45),
            PowerupKind::Banana => Color::srgb(1.0, 0.85, 0.10),
            PowerupKind::Health => Color::srgb(0.95, 0.30, 0.30),
            PowerupKind::MegaHealth => Color::srgb(0.25, 0.55, 1.0),
            PowerupKind::Armor => Color::srgb(0.55, 0.68, 0.95),
            PowerupKind::Flamethrower => Color::srgb(1.0, 0.45, 0.10),
            PowerupKind::Shotgun => Color::srgb(0.70, 0.73, 0.80),
            PowerupKind::RocketLauncher => Color::srgb(0.90, 0.16, 0.16),
            PowerupKind::Serum => Color::srgb(0.62, 0.20, 0.85),
        };
        commands.spawn((
            PbrBundle {
                mesh: pad_mesh.clone(),
                material: pad_mat.clone(),
                transform: Transform::from_translation(base),
                ..default()
            },
            RenderLayers::layer(0),
        ));
        commands.spawn((
            PbrBundle {
                mesh: meshes.add(Cylinder::new(0.58, 0.03)),
                material: materials.add(StandardMaterial {
                    base_color: ring_color,
                    emissive: LinearRgba::from(ring_color.to_linear()) * 0.5,
                    ..default()
                }),
                transform: Transform::from_translation(base + Vec3::Y * 0.05),
                ..default()
            },
            RenderLayers::layer(0),
        ));
        let item = commands
            .spawn((
                SpatialBundle::from_transform(Transform::from_translation(base + Vec3::Y * 0.65)),
                PickupItem {
                    index,
                    base_y: base.y + 0.65,
                    phase: index as f32 * 1.7,
                },
                RenderLayers::layer(0),
            ))
            .id();
        spawn_pickup_model(
            &mut commands,
            item,
            pickup.kind,
            &mut meshes,
            &mut materials,
        );
    }
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Plane3d::default().mesh().size(24.0, 10.0)),
            material: materials.add(Color::srgb(0.20, 0.22, 0.28)),
            transform: Transform::from_xyz(0.0, 0.01, 21.0),
            ..default()
        },
        RenderLayers::layer(0),
    ));
    // Podium row runners — lit strips on the floor so the row reads as
    // intentional.
    for i in 0..8 {
        let x = -9.45 + (i as f32) * 2.7;
        commands.spawn((
            PbrBundle {
                mesh: meshes.add(Plane3d::default().mesh().size(1.6, 1.6)),
                material: materials.add(StandardMaterial {
                    base_color: Color::srgb(0.85, 0.55, 0.10),
                    emissive: LinearRgba::new(0.35, 0.20, 0.05, 1.0),
                    ..default()
                }),
                transform: Transform::from_xyz(x, 0.03, 22.0),
                ..default()
            },
            RenderLayers::layer(0),
        ));
    }

    // Showcase prop gallery. These small silhouettes echo the five zone
    // themes without reusing the full world-space zone GLBs.
    let pedestal_mesh = meshes.add(Cuboid::new(1.7, 0.25, 1.2));
    let pedestal_mat = materials.add(Color::srgb(0.18, 0.20, 0.25));
    let yellow_mat = materials.add(Color::srgb(0.95, 0.70, 0.14));
    let cyan_mat = materials.add(StandardMaterial {
        base_color: Color::srgb(0.10, 0.85, 0.95),
        emissive: LinearRgba::new(0.06, 0.45, 0.55, 1.0),
        ..default()
    });
    let steel_mat = materials.add(Color::srgb(0.42, 0.44, 0.48));
    let linen_mat = materials.add(Color::srgb(0.72, 0.82, 0.95));
    let tile_mat = materials.add(Color::srgb(0.86, 0.92, 0.94));
    let gallery = [
        (-8.4, "sports"),
        (-4.2, "sleeping"),
        (0.0, "machine"),
        (4.2, "kitchen"),
        (8.4, "bath"),
    ];
    for (x, _) in gallery {
        commands.spawn((
            PbrBundle {
                mesh: pedestal_mesh.clone(),
                material: pedestal_mat.clone(),
                transform: Transform::from_xyz(x, 0.14, 18.1),
                ..default()
            },
            RenderLayers::layer(0),
        ));
    }
    // sports: trophy + ball block
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Cylinder::new(0.30, 0.50)),
            material: yellow_mat.clone(),
            transform: Transform::from_xyz(-8.7, 0.65, 18.1),
            ..default()
        },
        RenderLayers::layer(0),
    ));
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Cuboid::new(0.45, 0.45, 0.45)),
            material: materials.add(Color::srgb(0.95, 0.95, 1.0)),
            transform: Transform::from_xyz(-8.15, 0.62, 18.1)
                .with_rotation(Quat::from_rotation_y(0.8)),
            ..default()
        },
        RenderLayers::layer(0),
    ));
    // sleeping: bunk bed silhouette
    for y in [0.55, 0.95] {
        commands.spawn((
            PbrBundle {
                mesh: meshes.add(Cuboid::new(1.25, 0.12, 0.55)),
                material: linen_mat.clone(),
                transform: Transform::from_xyz(-4.2, y, 18.1),
                ..default()
            },
            RenderLayers::layer(0),
        ));
    }
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Cuboid::new(1.35, 0.75, 0.08)),
            material: steel_mat.clone(),
            transform: Transform::from_xyz(-4.2, 0.75, 17.78),
            ..default()
        },
        RenderLayers::layer(0),
    ));
    // machine room: glowing reactor core
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Cylinder::new(0.38, 0.90)),
            material: cyan_mat.clone(),
            transform: Transform::from_xyz(0.0, 0.78, 18.1),
            ..default()
        },
        RenderLayers::layer(0),
    ));
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Cuboid::new(1.05, 0.12, 0.12)),
            material: steel_mat.clone(),
            transform: Transform::from_xyz(0.0, 1.25, 18.1),
            ..default()
        },
        RenderLayers::layer(0),
    ));
    // kitchen: counter + orange button
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Cuboid::new(1.25, 0.50, 0.55)),
            material: materials.add(Color::srgb(0.32, 0.34, 0.38)),
            transform: Transform::from_xyz(4.2, 0.58, 18.1),
            ..default()
        },
        RenderLayers::layer(0),
    ));
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Cylinder::new(0.22, 0.16)),
            material: materials.add(Color::srgb(1.0, 0.35, 0.08)),
            transform: Transform::from_xyz(4.2, 0.94, 18.1),
            ..default()
        },
        RenderLayers::layer(0),
    ));
    // bathroom: stall panel + toilet block
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Cuboid::new(0.10, 0.95, 1.05)),
            material: tile_mat.clone(),
            transform: Transform::from_xyz(8.05, 0.72, 18.1),
            ..default()
        },
        RenderLayers::layer(0),
    ));
    commands.spawn((
        PbrBundle {
            mesh: meshes.add(Cylinder::new(0.30, 0.22)),
            material: tile_mat,
            transform: Transform::from_xyz(8.55, 0.55, 18.1),
            ..default()
        },
        RenderLayers::layer(0),
    ));

    // Firing-range marker behind the stationary demo dummy.
    let target_red = materials.add(Color::srgb(0.85, 0.08, 0.08));
    let target_white = materials.add(Color::srgb(0.92, 0.92, 0.86));
    for (radius, mat, z) in [
        (0.95, target_red.clone(), 24.86),
        (0.70, target_white.clone(), 24.82),
        (0.42, target_red.clone(), 24.78),
        (0.18, target_white.clone(), 24.74),
    ] {
        commands.spawn((
            PbrBundle {
                mesh: meshes.add(Cylinder::new(radius, 0.05)),
                material: mat,
                transform: Transform::from_xyz(9.0, 1.15, z)
                    .with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                ..default()
            },
            RenderLayers::layer(0),
        ));
    }
}

/// Browsers only honour requestPointerLock inside a user gesture, and the
/// grab issued on entering Play isn't one — so the canvas never locks and
/// bevy's cache thinks it did. Re-toggle the grab across two frames on
/// every click while captured: the Locked re-apply lands within the
/// click's transient activation and the browser accepts it.
#[cfg(target_arch = "wasm32")]
fn wasm_pointer_lock_retry(
    mouse: Res<ButtonInput<MouseButton>>,
    mouse_cap: Res<MouseCaptured>,
    mut pending: Local<bool>,
    mut q_window: Query<&mut Window, With<PrimaryWindow>>,
) {
    let Ok(mut w) = q_window.get_single_mut() else {
        return;
    };
    if *pending {
        *pending = false;
        w.cursor.grab_mode = CursorGrabMode::Locked;
        w.cursor.visible = false;
        return;
    }
    // Only when the browser does NOT actually hold the lock — re-toggling
    // an established lock sprays giant mouse deltas on every shot.
    let locked = web_sys::window()
        .and_then(|w| w.document())
        .map(|d| d.pointer_lock_element().is_some())
        .unwrap_or(false);
    if mouse.just_pressed(MouseButton::Left) && mouse_cap.0 && !locked {
        w.cursor.grab_mode = CursorGrabMode::None;
        *pending = true;
    }
}

fn mouse_look(
    mut motion: EventReader<MouseMotion>,
    mouse_cap: Res<MouseCaptured>,
    mut look: ResMut<LocalLook>,
) {
    if !mouse_cap.0 {
        motion.clear();
        return;
    }
    for ev in motion.read() {
        // pointer-lock transitions (and focus regains) can emit one giant
        // bogus delta — no human moves 300 px in a single event
        if ev.delta.x.abs() > 300.0 || ev.delta.y.abs() > 300.0 {
            continue;
        }
        look.yaw -= ev.delta.x * MOUSE_SENSITIVITY;
        look.pitch = (look.pitch - ev.delta.y * MOUSE_SENSITIVITY).clamp(-1.5, 1.5);
    }
    // Wrap yaw to avoid unbounded drift.
    let tau = std::f32::consts::TAU;
    look.yaw = ((look.yaw + std::f32::consts::PI).rem_euclid(tau)) - std::f32::consts::PI;
}

#[derive(bevy::ecs::system::SystemParam)]
struct NetSyncCtx<'w> {
    lifecycle: Res<'w, channels::NetLifecycle>,
    screen: Res<'w, State<Screen>>,
    rockets: ResMut<'w, RocketEnts>,
    confirmation_ents: ResMut<'w, ConfirmationEnts>,
    active_confirmations: ResMut<'w, ActiveConfirmations>,
    powerups_up: ResMut<'w, PowerupsUp>,
    last_snapshot: ResMut<'w, LastSnapshotTick>,
}

#[allow(clippy::too_many_arguments)]
fn drain_net(
    mut commands: Commands,
    net: Res<channels::NetIn>,
    asset_server: Res<AssetServer>,
    time: Res<Time>,
    mut my: ResMut<MyPlayer>,
    mut avatars: ResMut<Avatars>,
    mut trails: ResMut<FireTrails>,
    mut roster: ResMut<Roster>,
    mut final_standings: ResMut<FinalStandings>,
    mut next_screen: ResMut<NextState<Screen>>,
    rocket_assets: Res<RocketAssets>,
    mut audio: AudioCtx,
    mut sync: NetSyncCtx,
    mut q: Query<(&mut Transform, &mut PlayerAvatar)>,
) {
    let now_sec = time.elapsed_seconds();
    // Events come reliably (stream); snapshots unreliably (datagrams).
    // Buffer events and apply them once after all state for the frame
    // has landed, so handlers always see the freshest avatars.
    let mut all_events: Vec<protocol::GameEvent> = Vec::new();
    while let Ok(msg) = net.rx.try_recv() {
        match msg {
            ServerMessage::Roster(entries) => {
                roster.0 = entries;
            }
            ServerMessage::GameOver { standings } => {
                final_standings.rows = standings;
                final_standings.lost = false;
                final_standings.message = None;
                next_screen.set(Screen::Over);
                tracing::info!("game over received");
            }
            ServerMessage::Welcome {
                your_id,
                tick_rate_hz: _,
            } => {
                my.id = Some(your_id);
                for (_, entity) in sync.confirmation_ents.by_id.drain() {
                    commands.entity(entity).despawn_recursive();
                }
                next_screen.set(Screen::Play);
                tracing::info!(id = your_id, "welcome");
            }
            ServerMessage::Reject { reason } => {
                tracing::error!(%reason, "server rejected join");
            }
            ServerMessage::Snapshot(mut snap) => {
                // datagrams reorder: never apply state older than rendered
                if snap.tick <= sync.last_snapshot.0 && sync.last_snapshot.0 != 0 {
                    continue;
                }
                sync.last_snapshot.0 = snap.tick;
                // demo mode still inlines events; remote sends Events msgs
                all_events.append(&mut snap.events);
                my.tick = snap.tick;
                let present: std::collections::HashSet<PlayerId> =
                    snap.players.iter().map(|p| p.id).collect();
                for ps in &snap.players {
                    if my.id == Some(ps.id) {
                        let snap_pos =
                            Vec3::new(ps.pos.x.to_f32(), ps.pos.y.to_f32(), ps.pos.z.to_f32());
                        // glide from where the camera IS, not the previous
                        // snapshot — double-snapshot frames skipped a tick
                        let render = my_render_pos(&my);
                        my.pos_from = if (snap_pos - my.pos_to).length_squared() > 25.0 {
                            snap_pos
                        } else {
                            render
                        };
                        my.pos_to = snap_pos;
                        my.interp_age = 0.0;
                        if now_sec - my.last_snap_at > 1e-4 {
                            let gap = (now_sec - my.last_snap_at).clamp(1.0 / 64.0, 0.25);
                            my.lerp_dur = (my.lerp_dur * 0.6 + gap * 0.4).clamp(1.0 / 64.0, 0.12);
                            my.last_snap_at = now_sec;
                        }
                        my.pos = snap_pos;
                        my.yaw = ps.yaw.to_f32();
                        my.pitch = ps.pitch.to_f32();
                        my.alive = ps.respawn_in == 0;
                        my.health = ps.health;
                        my.respawn_in_ticks = ps.respawn_in;
                        my.crouching = ps.crouching;
                        my.quad_ticks = ps.quad_ticks;
                        my.haste_ticks = ps.haste_ticks;
                        my.weapon = ps.weapon;
                        my.ammo = ps.ammo;
                        my.armor = ps.armor;
                        my.serum_ticks = ps.serum_ticks;
                    }
                    apply_player(
                        &mut commands,
                        &mut avatars,
                        &asset_server,
                        &mut q,
                        ps,
                        now_sec,
                        &roster,
                        &rocket_assets,
                    );
                }
                avatars.by_id.retain(|id, ent| {
                    if present.contains(id) {
                        true
                    } else {
                        commands.entity(*ent).despawn();
                        false
                    }
                });

                sync.powerups_up.0 = snap.powerups_up.clone();
                // sync rocket projectiles
                let live: std::collections::HashSet<u16> =
                    snap.projectiles.iter().map(|p| p.id).collect();
                for pr in &snap.projectiles {
                    let pos = Vec3::new(pr.pos.x.to_f32(), pr.pos.y.to_f32(), pr.pos.z.to_f32());
                    let vel = Vec3::new(pr.vel.x.to_f32(), pr.vel.y.to_f32(), pr.vel.z.to_f32());
                    if let Some(&ent) = sync.rockets.by_id.get(&pr.id) {
                        if let Some(mut e) = commands.get_entity(ent) {
                            e.insert(
                                Transform::from_translation(pos)
                                    .looking_to(vel.normalize_or_zero(), Vec3::Y),
                            );
                            e.insert(RocketVis { vel });
                        }
                    } else {
                        // No owner on the wire (protocol stays narrow) —
                        // a rocket born next to my eye is my launch.
                        let eye = my.pos_to + Vec3::Y * 1.4;
                        if (pos - eye).length() < 2.5 {
                            audio.kick.0 = 1.0;
                        }
                        sfx(
                            &mut commands,
                            &audio.sounds.rocket,
                            audio.vols.sfx * 0.9 * att(my.pos_to, pos),
                        );
                        let ent = spawn_rocket(&mut commands, &rocket_assets, pos, vel);
                        sync.rockets.by_id.insert(pr.id, ent);
                    }
                }
                sync.rockets.by_id.retain(|id, ent| {
                    if live.contains(id) {
                        true
                    } else {
                        commands.entity(*ent).despawn_recursive();
                        false
                    }
                });

                let live_confirmations: std::collections::HashSet<u16> =
                    snap.confirmations.iter().map(|item| item.id).collect();
                for confirmation in &snap.confirmations {
                    let pos = Vec3::new(
                        confirmation.pos.x.to_f32(),
                        confirmation.pos.y.to_f32(),
                        confirmation.pos.z.to_f32(),
                    );
                    if let Some(entity) =
                        sync.confirmation_ents.by_id.get(&confirmation.id).copied()
                    {
                        if let Some(mut entity_commands) = commands.get_entity(entity) {
                            entity_commands
                                .insert(Transform::from_translation(pos + Vec3::Y * 0.55));
                            entity_commands.insert(ConfirmationVis {
                                base_y: pos.y + 0.55,
                                expires_in: confirmation.expires_in,
                            });
                        }
                    } else {
                        let entity =
                            spawn_confirmation(&mut commands, &rocket_assets, confirmation, my.id);
                        sync.confirmation_ents.by_id.insert(confirmation.id, entity);
                    }
                }
                sync.confirmation_ents.by_id.retain(|id, entity| {
                    if live_confirmations.contains(id) {
                        true
                    } else {
                        commands.entity(*entity).despawn_recursive();
                        false
                    }
                });
                sync.active_confirmations.0 = snap.confirmations;
            }
            ServerMessage::Events { mut events, .. } => {
                all_events.append(&mut events);
            }
            ServerMessage::PlayerJoined { .. } | ServerMessage::PlayerLeft { .. } => {}
        }
    }
    while let Ok(event) = sync.lifecycle.rx.try_recv() {
        match event {
            channels::NetLifecycleEvent::Started => {
                sync.last_snapshot.0 = 0;
            }
            channels::NetLifecycleEvent::Ended { reason }
                if final_standings.rows.is_empty()
                    && !matches!(sync.screen.get(), Screen::Over | Screen::Lobby) =>
            {
                tracing::warn!(?reason, "connection lost");
                final_standings.lost = true;
                final_standings.rows = roster.0.clone();
                final_standings.message =
                    reason.or_else(|| Some("The arena connection closed unexpectedly.".into()));
                next_screen.set(Screen::Over);
            }
            channels::NetLifecycleEvent::Ended { .. } => {}
        }
    }
    for ev in &all_events {
        match ev {
            GameEvent::Fire {
                shooter,
                from,
                to,
                hit,
            } => {
                let src = Vec3::new(from.x.to_f32(), from.y.to_f32(), from.z.to_f32());
                let mut f = src;
                let t = Vec3::new(to.x.to_f32(), to.y.to_f32(), to.z.to_f32());
                if my.id == Some(*shooter) {
                    audio.kick.0 = 1.0;
                    // beam leaves the viewmodel muzzle, not the eye
                    let fwd = (t - f).normalize_or_zero();
                    let right = fwd.cross(Vec3::Y).normalize_or_zero();
                    f += right * 0.30 - Vec3::Y * 0.22 + fwd * 0.45;
                }
                push_rail_trail(&mut trails, f, t, hit.is_some());
                if let Some(ent) = avatars.by_id.get(shooter).copied() {
                    if let Ok((_, mut avatar)) = q.get_mut(ent) {
                        avatar.fire_until = now_sec + 0.35;
                        // one bang per trigger pull: the shotgun
                        // emits an event per pellet
                        let last = audio
                            .streaks
                            .last_fire
                            .get(shooter)
                            .copied()
                            .unwrap_or(-1.0);
                        let melee = (t - src).length() < 4.0;
                        if now_sec - last > 0.06 {
                            audio.streaks.last_fire.insert(*shooter, now_sec);
                            let (snd, base) = if melee {
                                (&audio.sounds.melee, 0.85)
                            } else if avatar.held_weapon == protocol::WeaponKind::Shotgun {
                                (&audio.sounds.shotgun, 1.0)
                            } else {
                                (&audio.sounds.rail, 0.9)
                            };
                            let vol = audio.vols.sfx * base * att(my.pos_to, src);
                            sfx(&mut commands, snd, vol);
                        }
                        if melee && hit.is_some() {
                            sfx(
                                &mut commands,
                                &audio.sounds.melee_hit,
                                audio.vols.sfx * att(my.pos_to, t),
                            );
                        }
                    }
                }
            }
            GameEvent::Explosion { at } => {
                let c = Vec3::new(at.x.to_f32(), at.y.to_f32(), at.z.to_f32());
                // two staggered debris rings + a column of sparks
                for j in 0..14 {
                    let a = j as f32 / 14.0 * std::f32::consts::TAU;
                    let up = if j % 2 == 0 { 0.85 } else { 0.30 };
                    let d = Vec3::new(a.cos(), up, a.sin()).normalize();
                    trails.entries.push(Trail {
                        from: c,
                        to: c + d * (2.2 + (j % 3) as f32 * 0.9),
                        life: 0.22 + (j % 4) as f32 * 0.05,
                        hit: false,
                        flame: true,
                    });
                }
                for j in 0..4 {
                    let a = j as f32 * 1.7 + 0.4;
                    trails.entries.push(Trail {
                        from: c,
                        to: c + Vec3::new(a.cos() * 0.5, 2.4 + j as f32 * 0.5, a.sin() * 0.5),
                        life: 0.35,
                        hit: false,
                        flame: true,
                    });
                }
                commands.spawn((
                    PointLightBundle {
                        point_light: PointLight {
                            color: Color::srgb(1.0, 0.62, 0.22),
                            intensity: 1_400_000.0,
                            range: 18.0,
                            shadows_enabled: false,
                            ..default()
                        },
                        transform: Transform::from_translation(c + Vec3::Y * 0.8),
                        ..default()
                    },
                    RenderLayers::layer(0),
                    Flash { ttl: 0.35 },
                ));
                let vol = audio.vols.sfx * (0.45 + 0.55 * att(my.pos_to, c));
                sfx(&mut commands, &audio.sounds.explosion, vol);
            }
            GameEvent::Flame { shooter, from, dir } if true => {
                if my.id == Some(*shooter) {
                    audio.kick.0 = (audio.kick.0 + 0.35).min(1.0);
                }
                if let Some(ent) = avatars.by_id.get(shooter).copied() {
                    if let Ok((_, mut avatar)) = q.get_mut(ent) {
                        avatar.fire_until = now_sec + 0.25;
                    }
                }
                let f = Vec3::new(from.x.to_f32(), from.y.to_f32(), from.z.to_f32());
                let d = Vec3::new(dir.x.to_f32(), dir.y.to_f32(), dir.z.to_f32());
                // curved tongues: each one bends sideways then
                // droops, drawn as chained segments
                let side_axis = Vec3::new(-d.z, 0.0, d.x);
                for j in 0..5 {
                    let wob = (now_sec * 9.0 + j as f32 * 2.1).sin() * 0.30;
                    let rise = (now_sec * 7.0 + j as f32 * 1.3).cos() * 0.20;
                    let reach = 3.6 + (j as f32 * 1.71).sin().abs() * 2.4;
                    let mut prev = f;
                    for s in 1..=3 {
                        let t01 = s as f32 / 3.0;
                        let bend =
                            side_axis * wob * t01 * t01 + Vec3::Y * (rise * t01 - 0.55 * t01 * t01);
                        let p = f + d * reach * t01 + bend;
                        trails.entries.push(Trail {
                            from: prev,
                            to: p,
                            life: 0.12 + t01 * 0.12,
                            hit: false,
                            flame: true,
                        });
                        prev = p;
                    }
                }
                // short bright core right at the nozzle
                trails.entries.push(Trail {
                    from: f,
                    to: f + d * 1.5,
                    life: 0.10,
                    hit: true,
                    flame: false,
                });
                sfx(
                    &mut commands,
                    &audio.sounds.flame,
                    audio.vols.sfx * 0.6 * att(my.pos_to, f),
                );
                let _ = shooter;
            }
            GameEvent::Damage {
                attacker, victim, ..
            } => {
                let mut victim_pos = None;
                if let Some(ent) = avatars.by_id.get(victim).copied() {
                    if let Ok((tf, mut avatar)) = q.get_mut(ent) {
                        // Hit clip is 0.45 s; cutting earlier
                        // leaves spine mid-flinch (face up).
                        avatar.hit_until = now_sec + 0.5;
                        victim_pos = Some(tf.translation);
                    }
                }
                // everyone in earshot hears the victim's pain,
                // throttled per victim (flame ticks 8x/s)
                let last = audio.streaks.last_pain.get(victim).copied().unwrap_or(-1.0);
                if now_sec - last > 0.25 {
                    audio.streaks.last_pain.insert(*victim, now_sec);
                    let vol = if my.id == Some(*victim) {
                        audio.vols.sfx
                    } else {
                        audio.vols.sfx * 0.9 * att(my.pos_to, victim_pos.unwrap_or(my.pos_to))
                    };
                    sfx(&mut commands, &audio.sounds.hurt, vol);
                }
                // shooter-side hitsound: full volume at any
                // range (the pain grunt fades with distance)
                if my.id == Some(*attacker)
                    && attacker != victim
                    && now_sec - audio.streaks.last_confirm > 0.06
                {
                    audio.streaks.last_confirm = now_sec;
                    sfx(
                        &mut commands,
                        &audio.sounds.hit_confirm,
                        audio.vols.sfx * 0.7,
                    );
                }
            }
            GameEvent::ScenarioDamage { id, .. } => {
                if let Some(ent) = avatars.by_id.get(id).copied() {
                    if let Ok((_, mut avatar)) = q.get_mut(ent) {
                        avatar.hit_until = now_sec + 0.5;
                    }
                }
                if my.id == Some(*id) && now_sec - audio.streaks.last_hurt > 0.15 {
                    audio.streaks.last_hurt = now_sec;
                    sfx(&mut commands, &audio.sounds.hurt, audio.vols.sfx);
                }
            }
            GameEvent::Spawn { id, .. } => {
                if my.id == Some(*id) {
                    sfx(&mut commands, &audio.sounds.spawn, audio.vols.sfx * 0.7);
                }
            }
            GameEvent::Kill { attacker, victim } => {
                tracing::info!(attacker, victim, "kill");
                let s = &mut *audio.streaks;
                let suicide = attacker == victim;
                if my.id == Some(*victim) {
                    s.combo = 0;
                    s.spree = 0;
                    sfx(&mut commands, &audio.sounds.death, audio.vols.sfx);
                } else if my.id == Some(*attacker) && !suicide {
                    sfx(&mut commands, &audio.sounds.kill, audio.vols.sfx * 0.9);
                    if now_sec - s.last_kill < 3.5 {
                        s.combo += 1;
                    } else {
                        s.combo = 1;
                    }
                    s.last_kill = now_sec;
                    s.spree += 1;
                    let line = if !s.first_blood_done {
                        Some(ANN_FIRST_BLOOD)
                    } else {
                        match (s.combo, s.spree) {
                            (2, _) => Some(ANN_DOUBLE),
                            (3, _) => Some(ANN_TRIPLE),
                            (c, _) if c >= 4 => Some(ANN_OVERKILL),
                            (_, 5) => Some(ANN_SPREE),
                            (_, 10) => Some(ANN_RAMPAGE),
                            _ => None,
                        }
                    };
                    if let Some(i) = line {
                        sfx(&mut commands, &audio.sounds.announcer[i], audio.vols.sfx);
                    }
                }
                if !suicide {
                    s.first_blood_done = true;
                }
                let mine = my.id == Some(*attacker) || my.id == Some(*victim);
                let text = if suicide {
                    format!("{} >< self", roster.name(*victim))
                } else {
                    format!("{} >> {}", roster.name(*attacker), roster.name(*victim))
                };
                audio.kill_feed.push(text, now_sec, mine);
            }
            GameEvent::ScenarioState { kind, active } => {
                tracing::info!(?kind, active, "scenario state");
                if *kind == protocol::ScenarioKind::KitchenLava {
                    audio.lava.active = *active;
                }
            }
            GameEvent::ScenarioKill { id, kind } => {
                tracing::info!(id, ?kind, "scenario kill");
                if my.id == Some(*id) {
                    audio.streaks.combo = 0;
                    audio.streaks.spree = 0;
                    sfx(&mut commands, &audio.sounds.death, audio.vols.sfx);
                }
                let hazard = match kind {
                    protocol::ScenarioKind::KitchenLava => "lava",
                };
                audio.kill_feed.push(
                    format!("{} [{hazard}]", roster.name(*id)),
                    now_sec,
                    my.id == Some(*id),
                );
            }
            GameEvent::ConfirmationDropped { killer, victim, .. } if my.id == Some(*killer) => {
                audio.kill_feed.push(
                    format!("BANANA DROPPED — CONFIRM {}", roster.name(*victim)),
                    now_sec,
                    true,
                );
            }
            GameEvent::ConfirmationDropped { killer, victim, .. } if my.id == Some(*victim) => {
                audio.kill_feed.push(
                    format!("YOUR BANANA DROPPED — DENY {}", roster.name(*killer)),
                    now_sec,
                    true,
                );
            }
            GameEvent::KillConfirmed { killer, victim, .. } => {
                audio.kill_feed.push(
                    format!(
                        "{} CONFIRMED {}",
                        roster.name(*killer),
                        roster.name(*victim)
                    ),
                    now_sec,
                    my.id == Some(*killer) || my.id == Some(*victim),
                );
            }
            GameEvent::KillDenied { killer, victim, .. } => {
                audio.kill_feed.push(
                    format!("{} DENIED {}", roster.name(*victim), roster.name(*killer)),
                    now_sec,
                    my.id == Some(*killer) || my.id == Some(*victim),
                );
            }
            GameEvent::ConfirmationExpired { killer, victim, .. }
                if my.id == Some(*killer) || my.id == Some(*victim) =>
            {
                audio.kill_feed.push("BANANA EXPIRED".into(), now_sec, true);
            }
            _ => {}
        }
    }
}

fn apply_player(
    commands: &mut Commands,
    avatars: &mut Avatars,
    asset_server: &AssetServer,
    q: &mut Query<(&mut Transform, &mut PlayerAvatar)>,
    ps: &PlayerState,
    now_sec: f32,
    roster: &Roster,
    fx: &RocketAssets,
) {
    // The minion glb is authored with feet at origin.
    let feet = Vec3::new(ps.pos.x.to_f32(), ps.pos.y.to_f32(), ps.pos.z.to_f32());
    if let Some(&ent) = avatars.by_id.get(&ps.id) {
        if let Ok((t, mut avatar)) = q.get_mut(ent) {
            let delta = feet - avatar.last_pos;
            avatar.interp_from = t.translation;
            avatar.interp_to = feet;
            avatar.yaw_from = t.rotation.to_euler(EulerRot::YXZ).0;
            avatar.yaw_to = ps.yaw.to_f32();
            avatar.interp_age = 0.0;
            if now_sec - avatar.last_snap_at > 1e-4 {
                let gap = (now_sec - avatar.last_snap_at).clamp(1.0 / 64.0, 0.25);
                avatar.lerp_dur = (avatar.lerp_dur * 0.6 + gap * 0.4).clamp(1.0 / 64.0, 0.12);
                avatar.last_snap_at = now_sec;
            }
            // teleports (respawn, takeover) snap instead of gliding
            if delta.length_squared() > 25.0 {
                avatar.interp_from = feet;
            }
            let moving = delta.xz().length_squared() > 0.005;
            let airborne = delta.y.abs() > 0.045; // ~3 m/s vertical
                                                  // lateral vs forward, relative to facing
            let yaw = ps.yaw.to_f32();
            let fwd = Vec3::new(-yaw.sin(), 0.0, -yaw.cos());
            let lateral = delta.xz().length() > 0.001
                && delta.x * fwd.x + delta.z * fwd.z < 0.5 * delta.xz().length();
            avatar.last_pos = feet;
            avatar.serum_active = ps.serum_ticks > 0;
            avatar.held_weapon = ps.weapon;
            avatar.desired_clip = if ps.respawn_in > 0 {
                AnimClip::Die
            } else if now_sec < avatar.fire_until {
                AnimClip::Fire
            } else if now_sec < avatar.hit_until {
                AnimClip::Hit
            } else if airborne {
                AnimClip::Jump
            } else if ps.crouching {
                AnimClip::Crouch
            } else if moving && lateral {
                AnimClip::Strafe
            } else if moving {
                AnimClip::Run
            } else {
                AnimClip::Idle
            };
        }
    } else {
        let variant = roster
            .0
            .iter()
            .find(|r| r.id == ps.id)
            .map(|r| r.variant as usize % MINION_VARIANT_COUNT)
            .unwrap_or_else(|| minion_variant_for_player(ps.id));
        let ent = commands
            .spawn((
                SceneBundle {
                    scene: asset_server.load(format!("minion/minion.glb#Scene{variant}")),
                    transform: Transform::from_translation(feet)
                        .with_rotation(Quat::from_rotation_y(ps.yaw.to_f32())),
                    ..default()
                },
                RenderLayers::layer(0),
                PlayerAvatar {
                    id: ps.id,
                    variant,
                    last_pos: feet,
                    desired_clip: AnimClip::Idle,
                    fire_until: 0.0,
                    hit_until: 0.0,
                    serum_active: false,
                    held_weapon: ps.weapon,
                    net_driven: true,
                    interp_from: feet,
                    interp_to: feet,
                    yaw_from: ps.yaw.to_f32(),
                    yaw_to: ps.yaw.to_f32(),
                    interp_age: 0.0,
                    lerp_dur: 1.0 / 64.0,
                    last_snap_at: 0.0,
                },
            ))
            .with_children(|parent| {
                parent.spawn((
                    PbrBundle {
                        mesh: fx.serum_mesh.clone(),
                        material: fx.serum_mat.clone(),
                        transform: Transform::from_xyz(0.0, 0.85, 0.0),
                        visibility: Visibility::Hidden,
                        ..default()
                    },
                    RenderLayers::layer(0),
                    SerumShell,
                ));
            })
            .id();
        avatars.by_id.insert(ps.id, ent);
    }
}

fn minion_variant_for_player(id: PlayerId) -> usize {
    (id as usize) % MINION_VARIANT_COUNT
}

fn load_minion_anims(
    mut commands: Commands,
    asset_server: Res<AssetServer>,
    mut graphs: ResMut<Assets<AnimationGraph>>,
) {
    let mut variants = Vec::with_capacity(MINION_VARIANT_COUNT);
    for variant in 0..MINION_VARIANT_COUNT {
        let mut graph = AnimationGraph::new();
        let root = graph.root;
        let mut nodes = [AnimationNodeIndex::new(0); MINION_ANIM_COUNT];
        for (clip_idx, node) in nodes.iter_mut().enumerate() {
            let animation_idx = variant * MINION_ANIM_COUNT + clip_idx;
            let clip: Handle<AnimationClip> =
                asset_server.load(format!("minion/minion.glb#Animation{animation_idx}"));
            *node = graph.add_clip(clip, 1.0, root);
        }
        variants.push(MinionAnimSet {
            graph: graphs.add(graph),
            nodes,
        });
    }
    commands.insert_resource(MinionAnims { variants });
}

fn link_animation_players(
    mut commands: Commands,
    anims: Res<MinionAnims>,
    parents: Query<&Parent>,
    avatars: Query<&PlayerAvatar>,
    mut q_new: Query<(Entity, &mut AnimationPlayer), Added<AnimationPlayer>>,
) {
    for (entity, mut player) in &mut q_new {
        // Walk up the hierarchy to find the avatar root.
        let mut cur = entity;
        let mut avatar_root: Option<(Entity, usize)> = None;
        while let Ok(p) = parents.get(cur) {
            let parent = p.get();
            if let Ok(avatar) = avatars.get(parent) {
                avatar_root = Some((parent, avatar.variant));
                break;
            }
            cur = parent;
        }
        let Some((avatar_ent, variant)) = avatar_root else {
            continue;
        };
        let Some(anim_set) = anims.variants.get(variant) else {
            continue;
        };
        let mut transitions = AnimationTransitions::new();
        transitions
            .play(
                &mut player,
                anim_set.nodes[AnimClip::Idle as usize],
                std::time::Duration::ZERO,
            )
            .repeat();
        commands.entity(entity).insert((
            anim_set.graph.clone(),
            transitions,
            AvatarAnim {
                avatar: avatar_ent,
                variant,
                current: anim_set.nodes[AnimClip::Idle as usize],
            },
        ));
    }
}

fn spawn_showcase(mut commands: Commands, args: Res<ClientArgs>, asset_server: Res<AssetServer>) {
    if args.resolved_mode() != ClientMode::Demo {
        return;
    }
    // Podium row behind the machine room's north wall — out of the way of
    // the bot-arena fight but reachable if a player pokes around. Each
    // podium holds one minion locked to a single clip.
    let clips = [
        AnimClip::Idle,
        AnimClip::Run,
        AnimClip::Strafe,
        AnimClip::Jump,
        AnimClip::Fire,
        AnimClip::Hit,
        AnimClip::Die,
        AnimClip::Crouch,
    ];
    let base_z = 22.0; // outside the z=16 south wall, in the "secret room".
    let spacing = 2.7;
    let x0 = -((clips.len() as f32 - 1.0) * spacing) / 2.0;
    for (i, clip) in clips.iter().enumerate() {
        let variant = i % MINION_VARIANT_COUNT;
        let px = x0 + (i as f32) * spacing;
        let pos = Vec3::new(px, 0.0, base_z);
        commands.spawn((
            SceneBundle {
                scene: asset_server.load(format!("minion/minion.glb#Scene{variant}")),
                transform: Transform::from_translation(pos),
                ..default()
            },
            RenderLayers::layer(0),
            PlayerAvatar {
                id: 0xF0 + i as u8, // well outside MAX_PLAYERS range
                variant,
                last_pos: pos,
                desired_clip: *clip,
                fire_until: 0.0,
                hit_until: 0.0,
                serum_active: false,
                held_weapon: protocol::WeaponKind::Rail,
                net_driven: false,
                interp_from: pos,
                interp_to: pos,
                yaw_from: 0.0,
                yaw_to: 0.0,
                interp_age: 0.0,
                lerp_dur: 1.0 / 64.0,
                last_snap_at: 0.0,
            },
            Showcase,
        ));
    }
}

#[derive(Component)]
struct HpBlock(u8);

#[derive(Component)]
struct ArmorBlock(u8);

#[derive(Component)]
struct WeaponText;

#[derive(Component)]
struct PowerBadge(PowerupKind);

#[derive(Component)]
struct PowerBadgeText(PowerupKind);

fn spawn_hud(mut commands: Commands) {
    // Kill feed: a top-right column of recent frags. Pre-spawn fixed slots and
    // fill them in `update_kill_feed` (no per-frame despawn churn).
    commands
        .spawn(NodeBundle {
            style: Style {
                position_type: PositionType::Absolute,
                top: Val::Px(14.0),
                right: Val::Px(16.0),
                flex_direction: FlexDirection::Column,
                align_items: AlignItems::FlexEnd,
                row_gap: Val::Px(3.0),
                ..default()
            },
            z_index: ZIndex::Global(10),
            ..default()
        })
        .with_children(|col| {
            for i in 0..KILL_FEED_MAX {
                col.spawn((
                    TextBundle::from_section(
                        "",
                        TextStyle {
                            font_size: 18.0,
                            color: Color::NONE,
                            ..default()
                        },
                    ),
                    KillFeedSlot(i),
                ));
            }
        });

    // Armor: five slim blue cells above the HP row.
    commands
        .spawn(NodeBundle {
            style: Style {
                position_type: PositionType::Absolute,
                left: Val::Px(18.0),
                bottom: Val::Px(72.0),
                column_gap: Val::Px(5.0),
                ..default()
            },
            z_index: ZIndex::Global(10),
            ..default()
        })
        .with_children(|row| {
            for i in 0..5u8 {
                row.spawn((
                    NodeBundle {
                        style: Style {
                            width: Val::Px(26.0),
                            height: Val::Px(7.0),
                            ..default()
                        },
                        background_color: BackgroundColor(Color::srgb(0.55, 0.68, 0.95)),
                        ..default()
                    },
                    ArmorBlock(i),
                ));
            }
        });

    // HP: five chunky banana-yellow cells above the HP text; they drain
    // right-to-left and flush red when low.
    commands
        .spawn(NodeBundle {
            style: Style {
                position_type: PositionType::Absolute,
                left: Val::Px(18.0),
                bottom: Val::Px(52.0),
                column_gap: Val::Px(5.0),
                ..default()
            },
            z_index: ZIndex::Global(10),
            ..default()
        })
        .with_children(|row| {
            for i in 0..5u8 {
                row.spawn((
                    NodeBundle {
                        style: Style {
                            width: Val::Px(26.0),
                            height: Val::Px(14.0),
                            ..default()
                        },
                        background_color: BackgroundColor(Color::srgb(1.0, 0.84, 0.30)),
                        ..default()
                    },
                    HpBlock(i),
                ));
            }
        });

    // Active powerup badges: bottom-right, colored chip + seconds left.
    commands
        .spawn(NodeBundle {
            style: Style {
                position_type: PositionType::Absolute,
                right: Val::Px(18.0),
                bottom: Val::Px(18.0),
                column_gap: Val::Px(10.0),
                ..default()
            },
            z_index: ZIndex::Global(10),
            ..default()
        })
        .with_children(|row| {
            for (kind, label, color) in [
                (
                    PowerupKind::QuadDamage,
                    "QUAD",
                    Color::srgb(0.75, 0.25, 0.95),
                ),
                (PowerupKind::Haste, "HASTE", Color::srgb(0.20, 0.95, 0.45)),
                (PowerupKind::Serum, "SERUM", Color::srgb(0.62, 0.20, 0.85)),
            ] {
                row.spawn((
                    NodeBundle {
                        style: Style {
                            padding: UiRect::axes(Val::Px(8.0), Val::Px(4.0)),
                            column_gap: Val::Px(6.0),
                            align_items: AlignItems::Center,
                            ..default()
                        },
                        background_color: BackgroundColor(color.with_alpha(0.85)),
                        visibility: Visibility::Hidden,
                        ..default()
                    },
                    PowerBadge(kind),
                ))
                .with_children(|chip| {
                    chip.spawn((
                        TextBundle::from_section(
                            format!("{label} 10s"),
                            TextStyle {
                                font_size: 18.0,
                                color: Color::srgb(0.05, 0.05, 0.08),
                                ..default()
                            },
                        ),
                        PowerBadgeText(kind),
                    ));
                });
            }
        });

    // Weapon + ammo: bottom-center.
    commands.spawn((
        TextBundle::from_section(
            "RAIL",
            TextStyle {
                font_size: 24.0,
                color: Color::srgb(0.85, 0.88, 0.95),
                ..default()
            },
        )
        .with_style(Style {
            position_type: PositionType::Absolute,
            bottom: Val::Px(18.0),
            left: Val::Percent(46.0),
            ..default()
        }),
        WeaponText,
    ));

    // Crosshair: small "+" centered on screen.
    commands
        .spawn(NodeBundle {
            style: Style {
                position_type: PositionType::Absolute,
                left: Val::Px(0.0),
                top: Val::Px(0.0),
                width: Val::Percent(100.0),
                height: Val::Percent(100.0),
                justify_content: JustifyContent::Center,
                align_items: AlignItems::Center,
                ..default()
            },
            z_index: ZIndex::Global(10),
            ..default()
        })
        .with_children(|p| {
            p.spawn(TextBundle::from_section(
                "+",
                TextStyle {
                    font_size: 28.0,
                    color: Color::srgba(1.0, 1.0, 1.0, 0.85),
                    ..default()
                },
            ));
        });

    // Health: bottom-left.
    commands.spawn((
        TextBundle::from_section(
            "100 HP",
            TextStyle {
                font_size: 28.0,
                color: Color::srgb(0.95, 0.95, 1.0),
                ..default()
            },
        )
        .with_style(Style {
            position_type: PositionType::Absolute,
            left: Val::Px(18.0),
            bottom: Val::Px(16.0),
            ..default()
        }),
        HealthText,
    ));

    // Status / respawn indicator: top-center.
    commands.spawn((
        TextBundle::from_section(
            "",
            TextStyle {
                font_size: 24.0,
                color: Color::srgb(1.0, 0.7, 0.5),
                ..default()
            },
        )
        .with_style(Style {
            position_type: PositionType::Absolute,
            left: Val::Percent(50.0),
            top: Val::Px(36.0),
            margin: UiRect {
                left: Val::Px(-110.0),
                ..default()
            },
            ..default()
        }),
        StatusText,
    ));

    // Primary score and current confirmation objective stay visible so a
    // player never has to infer why a frag did or did not become points.
    commands.spawn((
        TextBundle::from_section(
            "CONFIRMED 0 | FRAGS 0 — YELLOW CONFIRMS, RED DENIES",
            TextStyle {
                font_size: 20.0,
                color: Color::srgb(1.0, 0.84, 0.30),
                ..default()
            },
        )
        .with_style(Style {
            position_type: PositionType::Absolute,
            left: Val::Percent(50.0),
            top: Val::Px(66.0),
            margin: UiRect {
                left: Val::Px(-235.0),
                ..default()
            },
            ..default()
        }),
        ConfirmationText,
    ));

    // Kitchen-lava warning: under the score line, pulsing while active.
    commands.spawn((
        TextBundle {
            visibility: Visibility::Hidden,
            ..TextBundle::from_section(
                "!! KITCHEN FLOOR IS LAVA !!",
                TextStyle {
                    font_size: 22.0,
                    color: Color::srgba(1.0, 0.45, 0.10, 0.9),
                    ..default()
                },
            )
            .with_style(Style {
                position_type: PositionType::Absolute,
                left: Val::Percent(50.0),
                top: Val::Px(98.0),
                margin: UiRect {
                    left: Val::Px(-150.0),
                    ..default()
                },
                ..default()
            })
        },
        LavaBanner,
    ));
}

#[derive(Component)]
struct FpsText;

fn spawn_fps_counter(mut commands: Commands) {
    commands.spawn((
        TextBundle::from_section(
            "",
            TextStyle {
                font_size: 18.0,
                color: Color::srgba(0.75, 0.95, 0.75, 0.9),
                ..default()
            },
        )
        .with_style(Style {
            position_type: PositionType::Absolute,
            right: Val::Px(10.0),
            top: Val::Px(8.0),
            ..default()
        }),
        FpsText,
    ));
}

/// Smoothed FPS, refreshed 4x/s so the number is readable.
fn fps_counter(
    time: Res<Time>,
    mut acc: Local<(f32, u32)>,
    mut q: Query<&mut Text, With<FpsText>>,
) {
    acc.0 += time.delta_seconds();
    acc.1 += 1;
    if acc.0 < 0.25 {
        return;
    }
    let fps = acc.1 as f32 / acc.0;
    *acc = (0.0, 0);
    if let Ok(mut text) = q.get_single_mut() {
        text.sections[0].value = format!("{fps:.0} FPS");
    }
}

/// Fill the fixed kill-feed slots from the `KillFeed` deque (newest at top),
/// expiring old entries and fading each over its last `KILL_FEED_FADE` seconds.
fn update_kill_feed(
    time: Res<Time>,
    mut feed: ResMut<KillFeed>,
    mut slots: Query<(&KillFeedSlot, &mut Text)>,
) {
    let now = time.elapsed_seconds();
    // Entries are pushed front, so the back is oldest — expire from there.
    while feed
        .0
        .back()
        .map_or(false, |l| now - l.born > KILL_FEED_TTL)
    {
        feed.0.pop_back();
    }
    for (slot, mut text) in &mut slots {
        match feed.0.get(slot.0) {
            Some(line) => {
                let age = now - line.born;
                let alpha = ((KILL_FEED_TTL - age) / KILL_FEED_FADE).clamp(0.0, 1.0);
                let (r, g, b) = if line.mine {
                    (1.0, 0.84, 0.30) // banana-yellow when you're the killer/victim
                } else {
                    (0.85, 0.88, 0.95)
                };
                text.sections[0].value = line.text.clone();
                text.sections[0].style.color = Color::srgba(r, g, b, alpha);
            }
            None => {
                text.sections[0].value.clear();
                text.sections[0].style.color = Color::NONE;
            }
        }
    }
}

#[allow(clippy::type_complexity)]
fn update_hud(
    my: Res<MyPlayer>,
    roster: Res<Roster>,
    confirmations: Res<ActiveConfirmations>,
    mut q_health: Query<
        &mut Text,
        (
            With<HealthText>,
            Without<StatusText>,
            Without<ConfirmationText>,
        ),
    >,
    mut q_status: Query<
        &mut Text,
        (
            With<StatusText>,
            Without<HealthText>,
            Without<ConfirmationText>,
        ),
    >,
    mut q_confirmation: Query<
        &mut Text,
        (
            With<ConfirmationText>,
            Without<HealthText>,
            Without<StatusText>,
            Without<PowerBadgeText>,
            Without<WeaponText>,
        ),
    >,
    mut q_blocks: Query<(&HpBlock, &mut BackgroundColor), Without<ArmorBlock>>,
    mut q_armor: Query<(&ArmorBlock, &mut BackgroundColor), Without<HpBlock>>,
    mut q_badges: Query<(&PowerBadge, &mut Visibility)>,
    mut q_badge_text: Query<
        (&PowerBadgeText, &mut Text),
        (
            Without<HealthText>,
            Without<StatusText>,
            Without<WeaponText>,
            Without<ConfirmationText>,
        ),
    >,
    mut q_weapon: Query<
        &mut Text,
        (
            With<WeaponText>,
            Without<HealthText>,
            Without<StatusText>,
            Without<PowerBadgeText>,
            Without<ConfirmationText>,
        ),
    >,
) {
    for (block, mut bg) in q_armor.iter_mut() {
        let lit = my.alive && my.armor > block.0 * 20;
        bg.0 = if lit {
            Color::srgb(0.55, 0.68, 0.95)
        } else {
            Color::srgba(0.2, 0.22, 0.30, 0.45)
        };
    }
    for (block, mut bg) in q_blocks.iter_mut() {
        let lit = my.alive && my.health > block.0 * 20;
        bg.0 = if !lit {
            Color::srgba(0.2, 0.2, 0.25, 0.55)
        } else if my.health <= 30 {
            Color::srgb(0.95, 0.35, 0.30)
        } else {
            Color::srgb(1.0, 0.84, 0.30)
        };
    }
    for (badge, mut vis) in q_badges.iter_mut() {
        let ticks = match badge.0 {
            PowerupKind::QuadDamage => my.quad_ticks,
            PowerupKind::Haste => my.haste_ticks,
            PowerupKind::Serum => my.serum_ticks,
            _ => 0,
        };
        *vis = if ticks > 0 && my.alive {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
    }
    if let Ok(mut t) = q_weapon.get_single_mut() {
        match my.weapon {
            protocol::WeaponKind::Rail => {
                t.sections[0].value = "RAIL".into();
                t.sections[0].style.color = Color::srgb(0.85, 0.88, 0.95);
            }
            protocol::WeaponKind::Flamethrower => {
                t.sections[0].value = format!("FLAMER {}", my.ammo);
                t.sections[0].style.color = Color::srgb(1.0, 0.55, 0.15);
            }
            protocol::WeaponKind::Shotgun => {
                t.sections[0].value = format!("SHOTGUN {}", my.ammo);
                t.sections[0].style.color = Color::srgb(0.85, 0.87, 0.95);
            }
            protocol::WeaponKind::RocketLauncher => {
                t.sections[0].value = format!("BAZOOKA {}", my.ammo);
                t.sections[0].style.color = Color::srgb(0.95, 0.35, 0.30);
            }
        }
    }
    for (bt, mut text) in q_badge_text.iter_mut() {
        let (ticks, label) = match bt.0 {
            PowerupKind::QuadDamage => (my.quad_ticks, "QUAD"),
            PowerupKind::Haste => (my.haste_ticks, "HASTE"),
            PowerupKind::Serum => (my.serum_ticks, "SERUM"),
            _ => (0, ""),
        };
        if ticks > 0 {
            text.sections[0].value = format!("{label} {}s", (ticks as f32 / 64.0).ceil() as u32);
        }
    }
    if let Ok(mut t) = q_health.get_single_mut() {
        let hp = my.health;
        t.sections[0].value = if my.alive {
            format!("{hp} HP")
        } else {
            "DOWN".to_string()
        };
        t.sections[0].style.color = if !my.alive {
            Color::srgb(0.95, 0.4, 0.35)
        } else if hp <= 30 {
            Color::srgb(0.95, 0.55, 0.40)
        } else {
            Color::srgb(0.95, 0.95, 1.0)
        };
    }
    if let Ok(mut t) = q_status.get_single_mut() {
        if my.alive {
            t.sections[0].value.clear();
        } else {
            let secs = (my.respawn_in_ticks as f32) / 64.0;
            t.sections[0].value = format!("respawn in {:.1}s", secs);
        }
    }
    if let Ok(mut text) = q_confirmation.get_single_mut() {
        let score = my
            .id
            .and_then(|id| roster.0.iter().find(|entry| entry.id == id));
        let confirmed = score.map_or(0, |entry| entry.confirmed_kills);
        let kills = score.map_or(0, |entry| entry.kills);
        let objective = my.id.and_then(|id| {
            confirmations
                .0
                .iter()
                .filter(|item| item.killer == id || item.victim == id)
                .min_by_key(|item| item.expires_in)
                .map(|item| (item, item.killer == id))
        });
        if let Some((objective, is_confirm)) = objective {
            let seconds = objective.expires_in as f32 / 64.0;
            if is_confirm {
                text.sections[0].value =
                    format!("CONFIRMED {confirmed} | FRAGS {kills} — CONFIRM YELLOW {seconds:.1}s");
                text.sections[0].style.color = Color::srgb(1.0, 0.84, 0.30);
            } else {
                text.sections[0].value =
                    format!("CONFIRMED {confirmed} | FRAGS {kills} — DENY RED {seconds:.1}s");
                text.sections[0].style.color = Color::srgb(1.0, 0.35, 0.25);
            }
        } else {
            text.sections[0].value =
                format!("CONFIRMED {confirmed} | FRAGS {kills} — YELLOW CONFIRMS, RED DENIES");
            text.sections[0].style.color = Color::srgb(0.82, 0.85, 0.92);
        }
    }
}

fn drive_avatar_animations(
    anims: Res<MinionAnims>,
    avatars: Query<(&PlayerAvatar, Option<&Showcase>)>,
    mut q: Query<(
        &mut AvatarAnim,
        &mut AnimationPlayer,
        &mut AnimationTransitions,
    )>,
) {
    for (mut link, mut player, mut transitions) in &mut q {
        let Ok((avatar, showcase)) = avatars.get(link.avatar) else {
            continue;
        };
        let Some(anim_set) = anims.variants.get(link.variant) else {
            continue;
        };
        let target = anim_set.nodes[avatar.desired_clip as usize];
        if target == link.current {
            continue;
        }
        let duration = std::time::Duration::from_millis(120);
        // Showcase minions force-loop every clip so you can watch the
        // whole animation cycle — Fire/Hit/Die/Jump are one-shot in
        // normal gameplay but here we want them on repeat.
        let looping = showcase.is_some()
            || matches!(
                avatar.desired_clip,
                AnimClip::Idle | AnimClip::Run | AnimClip::Strafe | AnimClip::Crouch
            );
        let active = transitions.play(&mut player, target, duration);
        if looping {
            active.repeat();
        }
        link.current = target;
    }
}

fn send_input(
    keys: Res<ButtonInput<KeyCode>>,
    mouse: Res<ButtonInput<MouseButton>>,
    mouse_cap: Res<MouseCaptured>,
    net: Res<channels::NetOut>,
    my: Res<MyPlayer>,
    look: Res<LocalLook>,
    time: Res<Time>,
    mut accum: Local<f32>,
) {
    if my.id.is_none() {
        return;
    }
    // One input per sim tick, not per frame: uncapped-fps machines
    // (vsync fallback) otherwise trip the server's input-rate kick.
    *accum += time.delta_seconds();
    let tick_dt = 1.0 / 64.0;
    if *accum < tick_dt {
        return;
    }
    *accum = (*accum - tick_dt).min(tick_dt);
    let mut b = Buttons::EMPTY;
    if keys.pressed(KeyCode::KeyW) {
        b.insert(Buttons::FORWARD);
    }
    if keys.pressed(KeyCode::KeyS) {
        b.insert(Buttons::BACK);
    }
    if keys.pressed(KeyCode::KeyA) {
        b.insert(Buttons::LEFT);
    }
    if keys.pressed(KeyCode::KeyD) {
        b.insert(Buttons::RIGHT);
    }
    if keys.pressed(KeyCode::Space) {
        b.insert(Buttons::JUMP);
    }
    // Shift as alternate crouch: Ctrl+W closes browser tabs, and wasm
    // players hold exactly that combo to crouch-walk forward.
    if keys.pressed(KeyCode::ControlLeft)
        || keys.pressed(KeyCode::ControlRight)
        || keys.pressed(KeyCode::ShiftLeft)
        || keys.pressed(KeyCode::ShiftRight)
    {
        b.insert(Buttons::CROUCH);
    }
    // free cursor = menu/alt-tab; clicking UI must not fire
    if mouse.pressed(MouseButton::Left) && mouse_cap.0 {
        b.insert(Buttons::FIRE);
    }
    let msg = ClientMessage::Input(Input {
        tick: my.tick,
        buttons: b,
        yaw: Fp::from_f32(look.yaw),
        pitch: Fp::from_f32(look.pitch),
    });
    let _ = net.tx.try_send(msg);
}

#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn update_camera(
    mut mode: ResMut<CameraMode>,
    keys: Res<ButtonInput<KeyCode>>,
    my: Res<MyPlayer>,
    look: Res<LocalLook>,
    avatars: Res<Avatars>,
    mut q_cam: Query<&mut Transform, With<GameCamera>>,
    mut q_avatar: Query<
        &mut Visibility,
        (
            With<PlayerAvatar>,
            Without<GameCamera>,
            Without<GunViewmodel>,
        ),
    >,
    mut q_gun: Query<
        (&mut Visibility, &WeaponView),
        (
            With<GunViewmodel>,
            Without<PlayerAvatar>,
            Without<GameCamera>,
        ),
    >,
) {
    // Tab is the scoreboard; top-down view lives on V.
    if keys.just_pressed(KeyCode::KeyV) {
        *mode = match *mode {
            CameraMode::TopDown => CameraMode::FirstPerson,
            _ => CameraMode::TopDown,
        };
    }
    if keys.just_pressed(KeyCode::KeyC) {
        *mode = match *mode {
            CameraMode::FirstPerson => CameraMode::ThirdPerson,
            CameraMode::ThirdPerson => CameraMode::FirstPerson,
            // pressing C out of TopDown drops into ThirdPerson
            CameraMode::TopDown => CameraMode::ThirdPerson,
        };
    }

    let Ok(mut cam) = q_cam.get_single_mut() else {
        return;
    };
    // Any mode other than FirstPerson shows the local avatar and hides the
    // camera-child gun viewmodel (the avatar's own gun mesh takes over).
    let show_local_avatar = !matches!(*mode, CameraMode::FirstPerson);
    if let Some(ent) = my.id.and_then(|id| avatars.by_id.get(&id).copied()) {
        if let Ok(mut vis) = q_avatar.get_mut(ent) {
            *vis = if show_local_avatar {
                Visibility::Visible
            } else {
                Visibility::Hidden
            };
        }
    }
    for (mut vis, view) in q_gun.iter_mut() {
        *vis = if matches!(*mode, CameraMode::FirstPerson) && view.0 == my.weapon {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
    }

    match *mode {
        CameraMode::FirstPerson => {
            if my.id.is_some() {
                let eye = my_render_pos(&my) + Vec3::new(0.0, eye_height(my.crouching), 0.0);
                let (sy, cy) = look.yaw.sin_cos();
                let (sp, cp) = look.pitch.sin_cos();
                let forward = Vec3::new(-sy * cp, sp, -cy * cp);
                cam.translation = eye;
                cam.look_to(forward, Vec3::Y);
            }
        }
        CameraMode::ThirdPerson => {
            if my.id.is_some() {
                // Over-the-shoulder chase cam: anchored at the player's head
                // and pulled back along the *opposite* of the look vector.
                let eye = my_render_pos(&my) + Vec3::new(0.0, eye_height(my.crouching), 0.0);
                let (sy, cy) = look.yaw.sin_cos();
                let (sp, cp) = look.pitch.sin_cos();
                let forward = Vec3::new(-sy * cp, sp, -cy * cp);
                let distance = 3.2;
                let height = 0.8;
                let cam_pos = eye - forward * distance + Vec3::Y * height;
                cam.translation = cam_pos;
                cam.look_to(forward, Vec3::Y);
            }
        }
        CameraMode::TopDown => {
            cam.translation = Vec3::new(0.0, 60.0, 0.0);
            cam.look_at(Vec3::ZERO, Vec3::NEG_Z);
        }
    }
}

fn tick_fire_trails(time: Res<Time>, mut trails: ResMut<FireTrails>) {
    let dt = time.delta_seconds();
    trails.entries.retain_mut(|t| {
        t.life -= dt;
        t.life > 0.0
    });
}

fn render_fire_trails(mut gizmos: Gizmos, trails: Res<FireTrails>) {
    for t in &trails.entries {
        let alpha = (t.life / FIRE_TRAIL_SECONDS).clamp(0.0, 1.0);
        let color = if t.flame {
            // hot core fading to ember
            Color::srgba(1.0, 0.55 + 0.35 * alpha, 0.10, alpha)
        } else if t.hit {
            Color::srgba(1.0, 0.4, 0.2, alpha)
        } else {
            Color::srgba(1.0, 0.9, 0.4, alpha)
        };
        gizmos.line(t.from, t.to, color);
    }
}

fn fit_sprite_to_window(
    render: Res<RenderConfig>,
    window: Query<&Window>,
    mut sprites: Query<&mut Transform, With<RenderTargetSprite>>,
) {
    let Ok(win) = window.get_single() else {
        return;
    };
    let sx = win.width() / render.width as f32;
    let sy = win.height() / render.height as f32;
    let s = sx.min(sy);
    for mut t in &mut sprites {
        t.scale = Vec3::splat(s);
        t.translation = Vec3::ZERO;
    }
}

// ---------------------------------------------------------------- ui flow

const VARIANT_NAMES: [&str; MINION_VARIANT_COUNT] = ["CLASSIC", "BOB", "KEVIN", "STUART"];
// Clear of the moon (center (10,62,-20), r=10) — the stage used to be
// parked inside it, hiding two minions.
const SELECT_STAGE: Vec3 = Vec3::new(-16.0, 58.0, -20.0);

#[cfg(not(target_arch = "wasm32"))]
#[derive(Component)]
struct LobbyUi;

#[cfg(not(target_arch = "wasm32"))]
#[derive(Component)]
struct LobbyTokenText;

#[cfg(not(target_arch = "wasm32"))]
#[derive(Component)]
struct LobbyStatusText;

#[cfg(not(target_arch = "wasm32"))]
#[derive(Component, Clone, Copy, PartialEq, Eq)]
enum LobbyAction {
    Paste,
    Connect,
}

#[derive(Component)]
struct SelectUi;

#[derive(Component)]
struct SelectSpot(usize);

#[derive(Component)]
struct SelectNameText;

#[derive(Component)]
struct ConnectUi;

#[derive(Component)]
struct ConnectText;

#[derive(Component)]
struct TabBoardRoot;

#[cfg(not(target_arch = "wasm32"))]
fn lobby_enter(mut commands: Commands, token: Res<LobbyToken>, mut status: ResMut<LobbyStatus>) {
    if !token.0.is_empty() {
        status.message =
            "KotH token saved securely in memory. Press Connect for a new arena.".into();
        status.tone = LobbyStatusTone::Neutral;
        status.retry_at_unix_ms = None;
    }
    commands
        .spawn((
            NodeBundle {
                style: Style {
                    width: Val::Percent(100.0),
                    height: Val::Percent(100.0),
                    align_items: AlignItems::Center,
                    justify_content: JustifyContent::Center,
                    padding: UiRect::all(Val::Px(24.0)),
                    ..default()
                },
                background_color: BackgroundColor(Color::srgb(0.015, 0.02, 0.04)),
                z_index: ZIndex::Global(100),
                ..default()
            },
            LobbyUi,
        ))
        .with_children(|root| {
            root.spawn(NodeBundle {
                style: Style {
                    width: Val::Px(620.0),
                    max_width: Val::Percent(100.0),
                    flex_direction: FlexDirection::Column,
                    align_items: AlignItems::Stretch,
                    row_gap: Val::Px(18.0),
                    padding: UiRect::all(Val::Px(30.0)),
                    border: UiRect::all(Val::Px(1.0)),
                    ..default()
                },
                background_color: BackgroundColor(Color::srgba(0.055, 0.065, 0.10, 0.98)),
                border_color: BorderColor(Color::srgb(0.28, 0.32, 0.43)),
                ..default()
            })
            .with_children(|panel| {
                panel.spawn(TextBundle::from_section(
                    "MINIONS IN 32K",
                    TextStyle {
                        font_size: 40.0,
                        color: Color::srgb(1.0, 0.84, 0.30),
                        ..default()
                    },
                ));
                panel.spawn(TextBundle::from_section(
                    "SECURE EVENT LOBBY",
                    TextStyle {
                        font_size: 18.0,
                        color: Color::srgb(0.63, 0.69, 0.82),
                        ..default()
                    },
                ));
                panel.spawn(TextBundle::from_section(
                    "Copy your team KotH token from the challenge page, then paste it here. The token is exchanged over HTTPS and is never sent to the arena server.",
                    TextStyle {
                        font_size: 17.0,
                        color: Color::srgb(0.82, 0.84, 0.90),
                        ..default()
                    },
                ));
                panel
                    .spawn(NodeBundle {
                        style: Style {
                            width: Val::Percent(100.0),
                            min_height: Val::Px(62.0),
                            align_items: AlignItems::Center,
                            padding: UiRect::horizontal(Val::Px(18.0)),
                            border: UiRect::all(Val::Px(1.0)),
                            ..default()
                        },
                        background_color: BackgroundColor(Color::srgb(0.025, 0.03, 0.055)),
                        border_color: BorderColor(Color::srgb(0.40, 0.45, 0.58)),
                        ..default()
                    })
                    .with_children(|field| {
                        field.spawn((
                            TextBundle::from_section(
                                "Paste or type koth_...",
                                TextStyle {
                                    font_size: 19.0,
                                    color: Color::srgb(0.56, 0.60, 0.70),
                                    ..default()
                                },
                            ),
                            LobbyTokenText,
                        ));
                    });
                panel.spawn((
                    TextBundle::from_section(
                        "Paste the KotH token from your RSCTF challenge page.",
                        TextStyle {
                            font_size: 15.0,
                            color: Color::srgb(0.67, 0.71, 0.80),
                            ..default()
                        },
                    ),
                    LobbyStatusText,
                ));
                panel
                    .spawn(NodeBundle {
                        style: Style {
                            width: Val::Percent(100.0),
                            flex_direction: FlexDirection::Row,
                            column_gap: Val::Px(12.0),
                            ..default()
                        },
                        ..default()
                    })
                    .with_children(|actions| {
                        spawn_lobby_button(actions, LobbyAction::Paste, "PASTE TOKEN", 0.40);
                        spawn_lobby_button(actions, LobbyAction::Connect, "CONNECT", 0.60);
                    });
                panel.spawn(TextBundle::from_section(
                    "Keyboard: Ctrl+V to paste  |  Enter to connect  |  Backspace to edit",
                    TextStyle {
                        font_size: 13.0,
                        color: Color::srgb(0.48, 0.52, 0.63),
                        ..default()
                    },
                ));
            });
        });
}

#[cfg(not(target_arch = "wasm32"))]
fn spawn_lobby_button(
    parent: &mut ChildBuilder,
    action: LobbyAction,
    label: &'static str,
    width: f32,
) {
    parent
        .spawn((
            ButtonBundle {
                style: Style {
                    width: Val::Auto,
                    flex_grow: width,
                    min_height: Val::Px(50.0),
                    align_items: AlignItems::Center,
                    justify_content: JustifyContent::Center,
                    border: UiRect::all(Val::Px(1.0)),
                    ..default()
                },
                background_color: BackgroundColor(lobby_button_color(action, false, false)),
                border_color: BorderColor(Color::srgb(0.48, 0.53, 0.66)),
                ..default()
            },
            action,
        ))
        .with_children(|button| {
            button.spawn(TextBundle::from_section(
                label,
                TextStyle {
                    font_size: 17.0,
                    color: Color::WHITE,
                    ..default()
                },
            ));
        });
}

#[cfg(not(target_arch = "wasm32"))]
fn lobby_button_color(action: LobbyAction, pressed: bool, disabled: bool) -> Color {
    if disabled {
        return Color::srgb(0.15, 0.17, 0.22);
    }
    match (action, pressed) {
        (LobbyAction::Connect, false) => Color::srgb(0.14, 0.44, 0.33),
        (LobbyAction::Connect, true) => Color::srgb(0.09, 0.31, 0.23),
        (LobbyAction::Paste, false) => Color::srgb(0.18, 0.22, 0.31),
        (LobbyAction::Paste, true) => Color::srgb(0.12, 0.15, 0.22),
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn masked_lobby_token(token: &str) -> String {
    if token.is_empty() {
        return "Paste or type koth_...".into();
    }
    let prefix = if token.starts_with("koth_") {
        "koth_"
    } else {
        ""
    };
    let hidden = token.len().saturating_sub(prefix.len()).min(36);
    let ellipsis = if token.len().saturating_sub(prefix.len()) > hidden {
        "..."
    } else {
        ""
    };
    format!(
        "{prefix}{}{ellipsis}  ({} characters)",
        "*".repeat(hidden),
        token.len()
    )
}

#[cfg(not(target_arch = "wasm32"))]
fn paste_lobby_token(token: &mut LobbyToken, status: &mut LobbyStatus) {
    let clipboard = arboard::Clipboard::new().and_then(|mut clipboard| clipboard.get_text());
    match clipboard {
        Ok(mut raw) => {
            let candidate = raw.trim().to_owned();
            raw.zeroize();
            if let Err(error) = gateway::validate_capability(&candidate) {
                let mut candidate = candidate;
                candidate.zeroize();
                status.message = format!("Could not paste token: {error}");
                status.tone = LobbyStatusTone::Error;
                status.retry_at_unix_ms = None;
                return;
            }
            let length = candidate.len();
            token.replace(candidate);
            status.message = format!("Token pasted ({length} characters). Press Connect.");
            status.tone = LobbyStatusTone::Neutral;
            status.retry_at_unix_ms = None;
        }
        Err(_) => {
            status.message =
                "Clipboard access failed. Focus this window and use Ctrl+V, or type the token."
                    .into();
            status.tone = LobbyStatusTone::Error;
            status.retry_at_unix_ms = None;
        }
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn begin_lobby_exchange(
    token: &mut LobbyToken,
    status: &mut LobbyStatus,
    exchange: &mut LobbyExchange,
    args: &ClientArgs,
) {
    if exchange.pending {
        return;
    }
    let now = lobby_unix_time_millis();
    if status
        .retry_at_unix_ms
        .is_some_and(|retry_at| retry_at > now)
    {
        return;
    }
    status.retry_at_unix_ms = None;
    if let Err(error) = gateway::validate_capability(&token.0) {
        status.message = format!("Cannot connect: {error}");
        status.tone = LobbyStatusTone::Error;
        return;
    }

    let mut request_args = lobby_request_args(args, token);
    let tx = exchange.tx.clone();
    exchange.pending = true;
    status.message = "Verifying token and requesting a private arena session...".into();
    status.tone = LobbyStatusTone::Busy;

    if std::thread::Builder::new()
        .name("event-gateway".into())
        .spawn(move || {
            let outcome = gateway::resolve(&mut request_args)
                .map(|()| request_args)
                .map_err(|error| GatewayFailure {
                    retry_at_unix_ms: gateway::retry_at_unix_ms(&error),
                    token_rejected: gateway::token_was_rejected(&error),
                    message: error.to_string(),
                });
            let _ = tx.send(outcome);
        })
        .is_err()
    {
        exchange.pending = false;
        status.message = "Could not start the secure connection worker. Try again.".into();
        status.tone = LobbyStatusTone::Error;
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn lobby_request_args(args: &ClientArgs, token: &LobbyToken) -> ClientArgs {
    let mut request_args = args.clone();
    request_args.token = Some(token.0.clone());
    request_args
}

#[cfg(not(target_arch = "wasm32"))]
fn lobby_keyboard_input(
    mut events: EventReader<KeyboardInput>,
    keys: Res<ButtonInput<KeyCode>>,
    args: Res<ClientArgs>,
    mut token: ResMut<LobbyToken>,
    mut status: ResMut<LobbyStatus>,
    mut exchange: ResMut<LobbyExchange>,
) {
    if exchange.pending {
        events.clear();
        return;
    }
    let modifier = keys.pressed(KeyCode::ControlLeft)
        || keys.pressed(KeyCode::ControlRight)
        || keys.pressed(KeyCode::SuperLeft)
        || keys.pressed(KeyCode::SuperRight);
    for event in events.read() {
        if event.state != ButtonState::Pressed {
            continue;
        }
        match &event.logical_key {
            Key::Character(value) if modifier && value.eq_ignore_ascii_case("v") => {
                paste_lobby_token(&mut token, &mut status);
            }
            Key::Character(value) if !modifier => {
                for character in value.chars() {
                    if token.0.len() < 133
                        && (character.is_ascii_alphanumeric() || matches!(character, '_' | '-'))
                    {
                        token.0.push(character);
                    }
                }
                status.message = "Press Enter or Connect when the token is complete.".into();
                status.tone = LobbyStatusTone::Neutral;
                status.retry_at_unix_ms = None;
            }
            Key::Backspace => {
                token.0.pop();
                status.message = "Token edited. Press Enter or Connect when ready.".into();
                status.tone = LobbyStatusTone::Neutral;
                status.retry_at_unix_ms = None;
            }
            Key::Enter => {
                begin_lobby_exchange(&mut token, &mut status, &mut exchange, &args);
            }
            _ => {}
        }
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn lobby_button_input(
    args: Res<ClientArgs>,
    mut token: ResMut<LobbyToken>,
    mut status: ResMut<LobbyStatus>,
    mut exchange: ResMut<LobbyExchange>,
    interactions: Query<(&Interaction, &LobbyAction), Changed<Interaction>>,
) {
    for (interaction, action) in &interactions {
        if *interaction != Interaction::Pressed || exchange.pending {
            continue;
        }
        match action {
            LobbyAction::Paste => paste_lobby_token(&mut token, &mut status),
            LobbyAction::Connect => {
                begin_lobby_exchange(&mut token, &mut status, &mut exchange, &args)
            }
        }
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn lobby_poll_exchange(
    mut exchange: ResMut<LobbyExchange>,
    slot: Res<net::SessionArgsSlot>,
    mut args: ResMut<ClientArgs>,
    mut token: ResMut<LobbyToken>,
    mut status: ResMut<LobbyStatus>,
    mut next: ResMut<NextState<Screen>>,
) {
    let Ok(outcome) = exchange.rx.try_recv() else {
        return;
    };
    exchange.pending = false;
    match outcome {
        Ok(session_args) => {
            if let Err(error) = slot.install(session_args.clone()) {
                status.message = format!("Could not activate the arena session: {error}");
                status.tone = LobbyStatusTone::Error;
                return;
            }
            *args = session_args;
            status.message =
                "Authenticated. Token saved for the next match; choose your minion.".into();
            status.tone = LobbyStatusTone::Neutral;
            status.retry_at_unix_ms = None;
            next.set(Screen::Select);
        }
        Err(error) => {
            if let Some(retry_at) = error.retry_at_unix_ms {
                status.message = crown_retry_message(retry_at, lobby_unix_time_millis());
                status.tone = LobbyStatusTone::Waiting;
                status.retry_at_unix_ms = Some(retry_at);
            } else if error.token_rejected {
                token.clear();
                status.message =
                    format!("Connection failed: {}. Paste a fresh token.", error.message);
                status.tone = LobbyStatusTone::Error;
                status.retry_at_unix_ms = None;
            } else {
                status.message = format!(
                    "Connection failed: {}. Your token is still saved; press Connect to retry.",
                    error.message
                );
                status.tone = LobbyStatusTone::Error;
                status.retry_at_unix_ms = None;
            }
        }
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn lobby_retry_tick(
    args: Res<ClientArgs>,
    mut token: ResMut<LobbyToken>,
    mut status: ResMut<LobbyStatus>,
    mut exchange: ResMut<LobbyExchange>,
) {
    let Some(retry_at) = status.retry_at_unix_ms else {
        return;
    };
    let now = lobby_unix_time_millis();
    if exchange.pending {
        return;
    }
    if now >= retry_at {
        status.retry_at_unix_ms = None;
        begin_lobby_exchange(&mut token, &mut status, &mut exchange, &args);
    } else {
        status.message = crown_retry_message(retry_at, now);
        status.tone = LobbyStatusTone::Waiting;
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn lobby_unix_time_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(i64::MAX as u128) as i64
}

#[cfg(not(target_arch = "wasm32"))]
fn crown_retry_message(retry_at_unix_ms: i64, now_unix_ms: i64) -> String {
    let remaining_ms = retry_at_unix_ms.saturating_sub(now_unix_ms);
    let remaining_seconds = (remaining_ms.saturating_add(999) / 1_000) as u64;
    let minutes = remaining_seconds / 60;
    let seconds = remaining_seconds % 60;
    format!(
        "Crown reset protection is active - safe arena in {minutes:02}:{seconds:02}.\nYour token is saved; reconnecting automatically."
    )
}

#[cfg(not(target_arch = "wasm32"))]
fn lobby_render(
    token: Res<LobbyToken>,
    status: Res<LobbyStatus>,
    exchange: Res<LobbyExchange>,
    mut token_text: Query<&mut Text, (With<LobbyTokenText>, Without<LobbyStatusText>)>,
    mut status_text: Query<&mut Text, (With<LobbyStatusText>, Without<LobbyTokenText>)>,
    mut buttons: Query<(&LobbyAction, &Interaction, &mut BackgroundColor)>,
) {
    if let Ok(mut text) = token_text.get_single_mut() {
        text.sections[0].value = masked_lobby_token(&token.0);
        text.sections[0].style.color = if token.0.is_empty() {
            Color::srgb(0.56, 0.60, 0.70)
        } else {
            Color::srgb(0.92, 0.94, 0.98)
        };
    }
    if let Ok(mut text) = status_text.get_single_mut() {
        text.sections[0].value = status.message.clone();
        text.sections[0].style.color = match status.tone {
            LobbyStatusTone::Neutral => Color::srgb(0.67, 0.71, 0.80),
            LobbyStatusTone::Busy => Color::srgb(1.0, 0.84, 0.30),
            LobbyStatusTone::Waiting => Color::srgb(1.0, 0.68, 0.24),
            LobbyStatusTone::Error => Color::srgb(1.0, 0.42, 0.40),
        };
    }
    let now = lobby_unix_time_millis();
    let waiting = status
        .retry_at_unix_ms
        .is_some_and(|retry_at| retry_at > now);
    for (action, interaction, mut background) in &mut buttons {
        let disabled = exchange.pending || (*action == LobbyAction::Connect && waiting);
        *background = BackgroundColor(lobby_button_color(
            *action,
            *interaction == Interaction::Pressed,
            disabled,
        ));
    }
}

fn despawn_all<T: Component>(mut commands: Commands, q: Query<Entity, With<T>>) {
    for e in &q {
        commands.entity(e).despawn_recursive();
    }
}

fn select_enter(
    mut commands: Commands,
    asset_server: Res<AssetServer>,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
    mut q_cam: Query<&mut Transform, With<GameCamera>>,
    time: Res<Time>,
    mut armed: ResMut<SelectArmedAt>,
) {
    armed.0 = time.elapsed_seconds() + 0.6;
    // Stage the four minions high above the lab, night sky + moon behind.
    match q_cam.get_single_mut() {
        Ok(mut cam) => {
            *cam = Transform::from_translation(SELECT_STAGE + Vec3::new(0.0, 1.4, 6.4))
                .looking_at(SELECT_STAGE + Vec3::Y * 0.8, Vec3::Y);
            tracing::info!("select stage camera placed");
        }
        Err(e) => tracing::error!(?e, "selection camera query failed"),
    }
    let podium = meshes.add(Cylinder::new(0.62, 0.12));
    let podium_mat = materials.add(StandardMaterial {
        base_color: Color::srgb(0.36, 0.40, 0.48),
        ..default()
    });
    for v in 0..MINION_VARIANT_COUNT {
        let x = (v as f32 - 1.5) * 2.1;
        let pos = SELECT_STAGE + Vec3::X * x;
        // Idle-animated via the showcase pipeline: PlayerAvatar links the
        // animation player, Showcase force-loops the clip.
        commands.spawn((
            SceneBundle {
                scene: asset_server.load(format!("minion/minion.glb#Scene{v}")),
                // the glb fronts face -Z; the stage camera sits at +Z
                transform: Transform::from_translation(pos)
                    .with_rotation(Quat::from_rotation_y(std::f32::consts::PI)),
                ..default()
            },
            RenderLayers::layer(0),
            PlayerAvatar {
                id: 200 + v as PlayerId,
                variant: v,
                last_pos: pos,
                desired_clip: AnimClip::Idle,
                fire_until: 0.0,
                hit_until: 0.0,
                serum_active: false,
                held_weapon: protocol::WeaponKind::Rail,
                net_driven: false,
                interp_from: pos,
                interp_to: pos,
                yaw_from: std::f32::consts::PI,
                yaw_to: std::f32::consts::PI,
                interp_age: 0.0,
                lerp_dur: 1.0 / 64.0,
                last_snap_at: 0.0,
            },
            Showcase,
            SelectUi,
        ));
        commands.spawn((
            PbrBundle {
                mesh: podium.clone(),
                material: podium_mat.clone(),
                transform: Transform::from_translation(pos - Vec3::Y * 0.06),
                ..default()
            },
            RenderLayers::layer(0),
            SelectUi,
        ));
        // Overhead spotlight, lit when hovered.
        commands.spawn((
            SpotLightBundle {
                spot_light: SpotLight {
                    color: Color::srgb(1.0, 0.88, 0.55),
                    intensity: 0.0,
                    range: 12.0,
                    radius: 0.05,
                    inner_angle: 0.25,
                    outer_angle: 0.55,
                    shadows_enabled: false,
                    ..default()
                },
                transform: Transform::from_translation(pos + Vec3::Y * 3.4)
                    .looking_at(pos, Vec3::Z),
                ..default()
            },
            RenderLayers::layer(0),
            SelectSpot(v),
            SelectUi,
        ));
    }
    // text overlay
    commands
        .spawn((
            NodeBundle {
                style: Style {
                    width: Val::Percent(100.0),
                    height: Val::Percent(100.0),
                    flex_direction: FlexDirection::Column,
                    align_items: AlignItems::Center,
                    justify_content: JustifyContent::SpaceBetween,
                    padding: UiRect::vertical(Val::Percent(6.0)),
                    ..default()
                },
                ..default()
            },
            SelectUi,
        ))
        .with_children(|root| {
            root.spawn(TextBundle::from_section(
                "CHOOSE YOUR MINION",
                TextStyle {
                    font_size: 42.0,
                    color: Color::srgb(1.0, 0.84, 0.30),
                    ..default()
                },
            ));
            root.spawn((
                TextBundle::from_section(
                    VARIANT_NAMES[0],
                    TextStyle {
                        font_size: 34.0,
                        color: Color::WHITE,
                        ..default()
                    },
                ),
                SelectNameText,
            ));
            root.spawn(TextBundle::from_section(
                "CLICK A MINION TO DEPLOY",
                TextStyle {
                    font_size: 20.0,
                    color: Color::srgb(0.75, 0.78, 0.85),
                    ..default()
                },
            ));
        });
}

/// Map the window cursor into the 256x128 render target (the sprite is
/// centered and uniformly scaled by fit_sprite_to_window), then pick the
/// minion column whose projected position is nearest.
fn hovered_variant(
    window: &Window,
    render: &RenderConfig,
    cam: &Camera,
    cam_tf: &GlobalTransform,
) -> Option<usize> {
    let cursor = window.cursor_position()?;
    let s = (window.width() / render.width as f32).min(window.height() / render.height as f32);
    let rw = render.width as f32 * s;
    let rh = render.height as f32 * s;
    let origin = Vec2::new((window.width() - rw) * 0.5, (window.height() - rh) * 0.5);
    let local = (cursor - origin) / s;
    if local.x < 0.0
        || local.y < 0.0
        || local.x > render.width as f32
        || local.y > render.height as f32
    {
        return None;
    }
    let mut best: Option<(usize, f32)> = None;
    for v in 0..MINION_VARIANT_COUNT {
        let pos = SELECT_STAGE + Vec3::X * (v as f32 - 1.5) * 2.1 + Vec3::Y * 0.6;
        let Some(screen) = cam.world_to_viewport(cam_tf, pos) else {
            continue;
        };
        let d = (screen - local).length();
        if d < 22.0 && best.map_or(true, |(_, bd)| d < bd) {
            best = Some((v, d));
        }
    }
    best.map(|(v, _)| v)
}

#[allow(clippy::too_many_arguments)]
fn select_input(
    keys: Res<ButtonInput<KeyCode>>,
    mouse: Res<ButtonInput<MouseButton>>,
    render: Res<RenderConfig>,
    q_window: Query<&Window, With<PrimaryWindow>>,
    q_cam: Query<(&Camera, &GlobalTransform), With<GameCamera>>,
    mut selected: ResMut<SelectedVariant>,
    starter: Res<channels::NetStarter>,
    mut standings: ResMut<FinalStandings>,
    mut next: ResMut<NextState<Screen>>,
    mut q_spots: Query<(&SelectSpot, &mut SpotLight)>,
    mut q_name: Query<&mut Text, With<SelectNameText>>,
    time: Res<Time>,
    armed: Res<SelectArmedAt>,
) {
    // keyboard fallback keeps working
    if keys.just_pressed(KeyCode::ArrowLeft) || keys.just_pressed(KeyCode::KeyA) {
        selected.0 = (selected.0 + MINION_VARIANT_COUNT - 1) % MINION_VARIANT_COUNT;
    }
    if keys.just_pressed(KeyCode::ArrowRight) || keys.just_pressed(KeyCode::KeyD) {
        selected.0 = (selected.0 + 1) % MINION_VARIANT_COUNT;
    }
    let hovered = match (q_window.get_single(), q_cam.get_single()) {
        (Ok(window), Ok((cam, cam_tf))) => hovered_variant(window, &render, cam, cam_tf),
        _ => None,
    };
    if let Some(h) = hovered {
        selected.0 = h;
    }
    for (spot, mut light) in q_spots.iter_mut() {
        let lit = spot.0 == selected.0;
        let target = if lit { 2_600_000.0 } else { 0.0 };
        // quick ease so the light snaps on but doesn't pop
        light.intensity += (target - light.intensity) * 0.35;
    }
    if let Ok(mut text) = q_name.get_single_mut() {
        text.sections[0].value = VARIANT_NAMES[selected.0].into();
    }
    let armed_now = time.elapsed_seconds() >= armed.0;
    let clicked = mouse.just_pressed(MouseButton::Left) && hovered.is_some();
    let keyed = keys.just_pressed(KeyCode::Enter) || keys.just_pressed(KeyCode::Space);
    if armed_now && (clicked || keyed) {
        match starter.start(selected.0 as u8) {
            Ok(()) => next.set(Screen::Connect),
            Err(error) => {
                tracing::error!(%error, "could not start arena connection");
                standings.rows.clear();
                standings.lost = true;
                standings.message = Some(error);
                next.set(Screen::Over);
            }
        }
    }
}

fn connect_enter(mut commands: Commands) {
    commands
        .spawn((
            NodeBundle {
                style: Style {
                    width: Val::Percent(100.0),
                    height: Val::Percent(100.0),
                    align_items: AlignItems::Center,
                    justify_content: JustifyContent::Center,
                    ..default()
                },
                ..default()
            },
            ConnectUi,
        ))
        .with_children(|root| {
            root.spawn((
                TextBundle::from_section(
                    "CONNECTING",
                    TextStyle {
                        font_size: 38.0,
                        color: Color::srgb(1.0, 0.84, 0.30),
                        ..default()
                    },
                ),
                ConnectText,
            ));
        });
}

fn connect_pulse(time: Res<Time>, mut q: Query<&mut Text, With<ConnectText>>) {
    let dots = ((time.elapsed_seconds() * 2.0) as usize) % 4;
    if let Ok(mut text) = q.get_single_mut() {
        text.sections[0].value = format!("CONNECTING{}", ".".repeat(dots));
    }
}

fn play_enter(
    mut q_window: Query<&mut Window, With<PrimaryWindow>>,
    mut cap: ResMut<MouseCaptured>,
) {
    if let Ok(mut window) = q_window.get_single_mut() {
        window.cursor.grab_mode = CursorGrabMode::Locked;
        window.cursor.visible = false;
        cap.0 = true;
    }
}

// ----------------------------------------------------------------- audio

/// 0..1 user volumes, set from the Esc options menu. Not persisted: the
/// client deliberately writes no files (see the config.toml posture).
#[derive(Resource)]
struct AudioVolumes {
    music: f32,
    sfx: f32,
}

impl Default for AudioVolumes {
    fn default() -> Self {
        Self {
            music: 0.5,
            sfx: 0.8,
        }
    }
}

#[derive(Resource)]
struct Sounds {
    rail: Handle<bevy::audio::AudioSource>,
    shotgun: Handle<bevy::audio::AudioSource>,
    rocket: Handle<bevy::audio::AudioSource>,
    explosion: Handle<bevy::audio::AudioSource>,
    flame: Handle<bevy::audio::AudioSource>,
    melee: Handle<bevy::audio::AudioSource>,
    melee_hit: Handle<bevy::audio::AudioSource>,
    hurt: Handle<bevy::audio::AudioSource>,
    death: Handle<bevy::audio::AudioSource>,
    pickup: Handle<bevy::audio::AudioSource>,
    kill: Handle<bevy::audio::AudioSource>,
    hit_confirm: Handle<bevy::audio::AudioSource>,
    spawn: Handle<bevy::audio::AudioSource>,
    /// first_blood, double_kill, triple_kill, overkill, killing_spree,
    /// rampage, slayer
    announcer: [Handle<bevy::audio::AudioSource>; 7],
}

const ANN_FIRST_BLOOD: usize = 0;
const ANN_DOUBLE: usize = 1;
const ANN_TRIPLE: usize = 2;
const ANN_OVERKILL: usize = 3;
const ANN_SPREE: usize = 4;
const ANN_RAMPAGE: usize = 5;
const ANN_SLAYER: usize = 6;

#[derive(Component)]
struct MusicSink;

#[derive(Component)]
struct LavaSink;

/// Announcer state: multikill combo window + deathless spree, Halo-style.
#[derive(Resource, Default)]
struct Streaks {
    combo: u32,
    last_kill: f32,
    spree: u32,
    first_blood_done: bool,
    last_fire: std::collections::HashMap<PlayerId, f32>,
    last_pain: std::collections::HashMap<PlayerId, f32>,
    last_hurt: f32,
    last_confirm: f32,
}

/// Event-presentation state bundled so drain_net stays under Bevy's
/// 16-system-param limit.
#[derive(bevy::ecs::system::SystemParam)]
struct AudioCtx<'w> {
    sounds: Res<'w, Sounds>,
    vols: Res<'w, AudioVolumes>,
    streaks: ResMut<'w, Streaks>,
    kick: ResMut<'w, ViewKick>,
    lava: ResMut<'w, LavaState>,
    // Folded in to stay under Bevy's 16-param system limit on drain_net; the
    // kill feed is updated right alongside the kill audio/streak reactions.
    kill_feed: ResMut<'w, KillFeed>,
}

fn setup_audio(mut commands: Commands, asset_server: Res<AssetServer>) {
    let load = |n: &str| asset_server.load::<bevy::audio::AudioSource>(format!("audio/{n}.wav"));
    commands.insert_resource(Sounds {
        rail: load("rail"),
        shotgun: load("shotgun"),
        rocket: load("rocket"),
        explosion: load("explosion"),
        flame: load("flame"),
        melee: load("melee"),
        melee_hit: load("melee_hit"),
        hurt: load("hurt"),
        death: load("death"),
        pickup: load("pickup"),
        kill: load("kill"),
        hit_confirm: load("hit_confirm"),
        spawn: load("spawn"),
        announcer: [
            load("first_blood"),
            load("double_kill"),
            load("triple_kill"),
            load("overkill"),
            load("killing_spree"),
            load("rampage"),
            load("slayer"),
        ],
    });
    commands.spawn((
        AudioBundle {
            source: asset_server.load::<bevy::audio::AudioSource>("audio/music.ogg"),
            settings: PlaybackSettings::LOOP
                .with_volume(bevy::audio::Volume::new(AudioVolumes::default().music)),
        },
        MusicSink,
    ));
    commands.spawn((
        AudioBundle {
            source: load("lava"),
            settings: PlaybackSettings::LOOP.with_volume(bevy::audio::Volume::new(0.0)),
        },
        LavaSink,
    ));
}

/// Fire-and-forget one-shot.
fn sfx(commands: &mut Commands, src: &Handle<bevy::audio::AudioSource>, vol: f32) {
    if vol < 0.01 {
        return;
    }
    commands.spawn(AudioBundle {
        source: src.clone(),
        settings: PlaybackSettings::DESPAWN.with_volume(bevy::audio::Volume::new(vol.min(1.4))),
    });
}

/// Simple distance falloff; no positional pan at 256x128 chunkiness.
fn att(my: Vec3, at: Vec3) -> f32 {
    (1.0 - my.distance(at) / 48.0).clamp(0.10, 1.0)
}

/// Live volume application: menu changes + lava ambient swell.
fn apply_volumes(
    vols: Res<AudioVolumes>,
    lava: Res<LavaState>,
    q_music: Query<&AudioSink, With<MusicSink>>,
    q_lava: Query<&AudioSink, (With<LavaSink>, Without<MusicSink>)>,
) {
    if let Ok(sink) = q_music.get_single() {
        sink.set_volume(vols.music);
    }
    if let Ok(sink) = q_lava.get_single() {
        sink.set_volume(vols.sfx * lava.level * 0.8);
    }
}

fn announce_slayer(mut commands: Commands, sounds: Res<Sounds>, vols: Res<AudioVolumes>) {
    sfx(&mut commands, &sounds.announcer[ANN_SLAYER], vols.sfx);
}

// ----------------------------------------------------------- options menu

#[derive(Resource, Default)]
struct MenuOpen(bool);

#[derive(Component)]
struct OptionsRoot;

#[derive(Component, Clone, Copy, PartialEq)]
enum SliderKind {
    Music,
    Sfx,
}

#[derive(Component)]
struct SliderFill(SliderKind);

fn spawn_options_menu(mut commands: Commands) {
    commands
        .spawn((
            NodeBundle {
                style: Style {
                    position_type: PositionType::Absolute,
                    left: Val::Percent(34.0),
                    right: Val::Percent(34.0),
                    top: Val::Percent(30.0),
                    flex_direction: FlexDirection::Column,
                    row_gap: Val::Px(14.0),
                    padding: UiRect::all(Val::Px(18.0)),
                    ..default()
                },
                background_color: BackgroundColor(Color::srgba(0.04, 0.05, 0.12, 0.95)),
                visibility: Visibility::Hidden,
                z_index: ZIndex::Global(50),
                ..default()
            },
            OptionsRoot,
        ))
        .with_children(|p| {
            p.spawn(TextBundle::from_section(
                "OPTIONS",
                TextStyle {
                    font_size: 26.0,
                    color: Color::srgb(1.0, 0.84, 0.30),
                    ..default()
                },
            ));
            for (label, kind) in [("MUSIC", SliderKind::Music), ("SFX", SliderKind::Sfx)] {
                p.spawn(NodeBundle {
                    style: Style {
                        flex_direction: FlexDirection::Row,
                        align_items: AlignItems::Center,
                        column_gap: Val::Px(12.0),
                        ..default()
                    },
                    ..default()
                })
                .with_children(|row| {
                    row.spawn(TextBundle {
                        style: Style {
                            width: Val::Px(80.0),
                            ..default()
                        },
                        ..TextBundle::from_section(
                            label,
                            TextStyle {
                                font_size: 20.0,
                                color: Color::WHITE,
                                ..default()
                            },
                        )
                    });
                    row.spawn((
                        NodeBundle {
                            style: Style {
                                width: Val::Px(240.0),
                                height: Val::Px(18.0),
                                padding: UiRect::all(Val::Px(2.0)),
                                ..default()
                            },
                            background_color: BackgroundColor(Color::srgb(0.16, 0.18, 0.26)),
                            ..default()
                        },
                        Interaction::default(),
                        bevy::ui::RelativeCursorPosition::default(),
                        kind,
                    ))
                    .with_children(|bar| {
                        bar.spawn((
                            NodeBundle {
                                style: Style {
                                    width: Val::Percent(50.0),
                                    height: Val::Percent(100.0),
                                    ..default()
                                },
                                background_color: BackgroundColor(Color::srgb(1.0, 0.84, 0.30)),
                                ..default()
                            },
                            SliderFill(kind),
                        ));
                    });
                });
            }
            p.spawn(TextBundle::from_section(
                "ESC TO CLOSE",
                TextStyle {
                    font_size: 14.0,
                    color: Color::srgba(0.7, 0.7, 0.8, 0.8),
                    ..default()
                },
            ));
        });
}

/// Esc toggles the options overlay. In-game it also swaps mouse capture,
/// replacing the old bare cursor-release toggle.
fn esc_menu(
    keys: Res<ButtonInput<KeyCode>>,
    screen: Res<State<Screen>>,
    mut open: ResMut<MenuOpen>,
    mut mouse_cap: ResMut<MouseCaptured>,
    mut q_window: Query<&mut Window, With<PrimaryWindow>>,
    mut q_menu: Query<&mut Visibility, With<OptionsRoot>>,
) {
    if keys.just_pressed(KeyCode::Escape) {
        open.0 = !open.0;
        if *screen.get() == Screen::Play {
            mouse_cap.0 = !open.0;
            if let Ok(mut w) = q_window.get_single_mut() {
                w.cursor.grab_mode = if mouse_cap.0 {
                    CursorGrabMode::Locked
                } else {
                    CursorGrabMode::None
                };
                w.cursor.visible = !mouse_cap.0;
            }
        }
        if let Ok(mut vis) = q_menu.get_single_mut() {
            *vis = if open.0 {
                Visibility::Visible
            } else {
                Visibility::Hidden
            };
        }
    }
}

fn slider_input(
    q: Query<(&Interaction, &bevy::ui::RelativeCursorPosition, &SliderKind)>,
    mut vols: ResMut<AudioVolumes>,
) {
    for (interaction, rel, kind) in &q {
        if *interaction != Interaction::Pressed {
            continue;
        }
        let Some(p) = rel.normalized else { continue };
        let v = p.x.clamp(0.0, 1.0);
        match kind {
            SliderKind::Music => vols.music = v,
            SliderKind::Sfx => vols.sfx = v,
        }
    }
}

fn slider_fill(vols: Res<AudioVolumes>, mut q: Query<(&SliderFill, &mut Style)>) {
    for (fill, mut style) in q.iter_mut() {
        let v = match fill.0 {
            SliderKind::Music => vols.music,
            SliderKind::Sfx => vols.sfx,
        };
        style.width = Val::Percent(v * 100.0);
    }
}

// ----------------------------------------------------------- minion icons

/// Live minion portraits for the scoreboard: four offscreen camera rigs
/// (one per variant) render idling minions to small textures. The icons
/// animate for free since they're the same skinned scenes.
#[derive(Resource)]
struct MinionIcons([Handle<Image>; 4]);

/// Marks an icon-rig root; the value is its private render layer.
#[derive(Component)]
struct IconRig(usize);

const ICON_LAYER_BASE: usize = 10;

fn setup_minion_icons(
    mut commands: Commands,
    asset_server: Res<AssetServer>,
    mut images: ResMut<Assets<Image>>,
) {
    use bevy::render::camera::RenderTarget;
    use bevy::render::render_resource::{
        Extent3d, TextureDescriptor, TextureDimension, TextureFormat, TextureUsages,
    };

    let mut handles: Vec<Handle<Image>> = Vec::with_capacity(4);
    for v in 0..4usize {
        let size = Extent3d {
            width: 96,
            height: 96,
            depth_or_array_layers: 1,
        };
        let mut image = Image {
            texture_descriptor: TextureDescriptor {
                label: None,
                size,
                dimension: TextureDimension::D2,
                format: TextureFormat::Bgra8UnormSrgb,
                mip_level_count: 1,
                sample_count: 1,
                usage: TextureUsages::TEXTURE_BINDING
                    | TextureUsages::COPY_DST
                    | TextureUsages::RENDER_ATTACHMENT,
                view_formats: &[],
            },
            ..default()
        };
        image.resize(size);
        let target = images.add(image);
        handles.push(target.clone());

        let layer = RenderLayers::layer(ICON_LAYER_BASE + v);
        // far below the arena so nothing leaks into the main view while
        // the scene loads (children carry layer 0 until propagation runs)
        let rig = Vec3::new(v as f32 * 8.0 - 12.0, -220.0, 0.0);
        commands.spawn((
            SceneBundle {
                scene: asset_server.load(format!("minion/minion.glb#Scene{v}")),
                transform: Transform::from_translation(rig)
                    .with_rotation(Quat::from_rotation_y(std::f32::consts::PI)),
                ..default()
            },
            PlayerAvatar {
                id: 0xE0 + v as PlayerId,
                variant: v,
                last_pos: rig,
                desired_clip: AnimClip::Idle,
                fire_until: 0.0,
                hit_until: 0.0,
                serum_active: false,
                held_weapon: protocol::WeaponKind::Rail,
                net_driven: false,
                interp_from: rig,
                interp_to: rig,
                yaw_from: std::f32::consts::PI,
                yaw_to: std::f32::consts::PI,
                interp_age: 0.0,
                lerp_dur: 1.0 / 64.0,
                last_snap_at: 0.0,
            },
            Showcase,
            IconRig(ICON_LAYER_BASE + v),
            layer.clone(),
        ));
        commands.spawn((
            PointLightBundle {
                point_light: PointLight {
                    intensity: 600_000.0,
                    range: 14.0,
                    shadows_enabled: false,
                    ..default()
                },
                transform: Transform::from_translation(rig + Vec3::new(1.2, 2.4, 2.2)),
                ..default()
            },
            layer.clone(),
        ));
        commands.spawn((
            Camera3dBundle {
                camera: Camera {
                    target: RenderTarget::Image(target),
                    order: -(10 + v as isize),
                    clear_color: ClearColorConfig::Custom(Color::NONE),
                    ..default()
                },
                transform: Transform::from_translation(rig + Vec3::new(0.25, 1.55, 1.55))
                    .looking_at(rig + Vec3::new(0.0, 1.15, 0.0), Vec3::Y),
                ..default()
            },
            layer,
        ));
    }
    commands.insert_resource(MinionIcons(handles.try_into().expect("4 icon handles")));
}

/// glb scene children spawn on layer 0; shove every icon-rig descendant
/// onto the rig's private layer (idempotent — runs until stable).
fn propagate_icon_layers(
    mut commands: Commands,
    q_rigs: Query<(Entity, &IconRig)>,
    q_children: Query<&Children>,
    q_meshes: Query<(), With<Handle<Mesh>>>,
    q_layered: Query<&RenderLayers>,
) {
    for (root, rig) in &q_rigs {
        let mut stack = vec![root];
        let mut pending = false;
        let mut all: Vec<Entity> = Vec::new();
        while let Some(e) = stack.pop() {
            if let Ok(children) = q_children.get(e) {
                stack.extend(children.iter().copied());
            }
            all.push(e);
            if q_meshes.get(e).is_ok() && q_layered.get(e).is_err() {
                pending = true;
            }
        }
        if pending {
            for e in all {
                commands.entity(e).insert(RenderLayers::layer(rig.0));
            }
        }
    }
}

// ------------------------------------------------------------- tab board

fn spawn_tab_board(mut commands: Commands, q: Query<Entity, With<TabBoardRoot>>) {
    if !q.is_empty() {
        return;
    }
    commands.spawn((
        NodeBundle {
            style: Style {
                position_type: PositionType::Absolute,
                left: Val::Percent(18.0),
                right: Val::Percent(18.0),
                top: Val::Percent(14.0),
                flex_direction: FlexDirection::Column,
                row_gap: Val::Px(4.0),
                padding: UiRect::all(Val::Px(14.0)),
                ..default()
            },
            background_color: BackgroundColor(Color::srgba(0.04, 0.05, 0.12, 0.92)),
            visibility: Visibility::Hidden,
            ..default()
        },
        TabBoardRoot,
    ));
}

/// Held-Tab scoreboard: team names (from rCTF via the server roster),
/// picked minion, confirmed kills / frags / deaths, and latency.
fn tab_board(
    mut commands: Commands,
    keys: Res<ButtonInput<KeyCode>>,
    roster: Res<Roster>,
    my: Res<MyPlayer>,
    icons: Res<MinionIcons>,
    mut q_root: Query<(Entity, &mut Visibility), With<TabBoardRoot>>,
) {
    let Ok((root, mut vis)) = q_root.get_single_mut() else {
        return;
    };
    let show = keys.pressed(KeyCode::Tab);
    *vis = if show {
        Visibility::Visible
    } else {
        Visibility::Hidden
    };
    if !show {
        return;
    }
    let mut rows = roster.0.clone();
    rows.sort_by(|a, b| protocol::rank_cmp(a, b));
    commands.entity(root).despawn_descendants();
    let cell = |text: &str, width: f32, color: Color| TextBundle {
        style: Style {
            width: Val::Px(width),
            ..default()
        },
        ..TextBundle::from_section(
            text.to_string(),
            TextStyle {
                font_size: 20.0,
                color,
                ..default()
            },
        )
    };
    let header = Color::srgb(1.0, 0.84, 0.30);
    commands.entity(root).with_children(|root| {
        root.spawn(NodeBundle {
            style: Style {
                flex_direction: FlexDirection::Row,
                align_items: AlignItems::Center,
                column_gap: Val::Px(10.0),
                ..default()
            },
            ..default()
        })
        .with_children(|row| {
            row.spawn(cell("", 30.0, header));
            row.spawn(cell("TEAM", 230.0, header));
            row.spawn(cell("C", 44.0, header));
            row.spawn(cell("K", 44.0, header));
            row.spawn(cell("D", 44.0, header));
            row.spawn(cell("PING", 80.0, header));
        });
        for r in &rows {
            let me = my.id == Some(r.id);
            let color = if !r.connected {
                Color::srgb(0.45, 0.45, 0.50)
            } else if me {
                Color::srgb(0.55, 0.95, 1.0)
            } else {
                Color::WHITE
            };
            let icon = icons.0[r.variant as usize % MINION_VARIANT_COUNT].clone();
            root.spawn(NodeBundle {
                style: Style {
                    flex_direction: FlexDirection::Row,
                    align_items: AlignItems::Center,
                    column_gap: Val::Px(10.0),
                    ..default()
                },
                ..default()
            })
            .with_children(|row| {
                row.spawn(ImageBundle {
                    style: Style {
                        width: Val::Px(30.0),
                        height: Val::Px(30.0),
                        ..default()
                    },
                    image: UiImage::new(icon),
                    ..default()
                });
                row.spawn(cell(&truncate(&r.name, 20), 230.0, color));
                row.spawn(cell(&r.confirmed_kills.to_string(), 44.0, color));
                row.spawn(cell(&r.kills.to_string(), 44.0, color));
                row.spawn(cell(&r.deaths.to_string(), 44.0, color));
                row.spawn(cell(&format!("{}ms", r.rtt_ms), 80.0, color));
            });
        }
    });
}

fn truncate(s: &str, n: usize) -> String {
    if s.chars().count() <= n {
        s.to_string()
    } else {
        s.chars().take(n.saturating_sub(1)).collect::<String>() + "~"
    }
}

/// If the roster says a player picked a different minion than the avatar
/// we spawned (roster can arrive after the first snapshot), respawn it.
fn fix_avatar_variants(
    mut commands: Commands,
    roster: Res<Roster>,
    mut avatars: ResMut<Avatars>,
    q: Query<(Entity, &PlayerAvatar)>,
) {
    if !roster.is_changed() {
        return;
    }
    for (ent, avatar) in &q {
        let Some(entry) = roster.0.iter().find(|r| r.id == avatar.id) else {
            continue;
        };
        if entry.variant as usize % MINION_VARIANT_COUNT != avatar.variant {
            commands.entity(ent).despawn_recursive();
            avatars.by_id.remove(&avatar.id);
        }
    }
}

// --------------------------------------------------- interpolation + over

fn my_render_pos(my: &MyPlayer) -> Vec3 {
    let t = (my.interp_age / my.lerp_dur.max(1.0 / 64.0)).clamp(0.0, 1.0);
    my.pos_from.lerp(my.pos_to, t)
}

/// Glide avatars between 64 Hz snapshots — fixes the stepping/teleport
/// look on high-refresh displays.
fn interpolate_avatars(
    time: Res<Time>,
    mut my: ResMut<MyPlayer>,
    mut q: Query<(&mut Transform, &mut PlayerAvatar)>,
) {
    my.interp_age += time.delta_seconds();
    for (mut t, mut avatar) in q.iter_mut() {
        if !avatar.net_driven {
            continue; // selection/showcase minions are hand-placed
        }
        avatar.interp_age += time.delta_seconds();
        let k = (avatar.interp_age / avatar.lerp_dur.max(1.0 / 64.0)).clamp(0.0, 1.0);
        t.translation = avatar.interp_from.lerp(avatar.interp_to, k);
        let mut dy = avatar.yaw_to - avatar.yaw_from;
        while dy > std::f32::consts::PI {
            dy -= std::f32::consts::TAU;
        }
        while dy < -std::f32::consts::PI {
            dy += std::f32::consts::TAU;
        }
        t.rotation = Quat::from_rotation_y(avatar.yaw_from + dy * k);
    }
}

/// Keep the selection-stage camera in place every frame: the initial
/// state's OnEnter fires before Startup spawns the camera, so a one-shot
/// placement silently missed it.
fn select_camera(mut q_cam: Query<&mut Transform, With<GameCamera>>) {
    if let Ok(mut cam) = q_cam.get_single_mut() {
        *cam = Transform::from_translation(SELECT_STAGE + Vec3::new(0.0, 1.4, 6.4))
            .looking_at(SELECT_STAGE + Vec3::Y * 0.8, Vec3::Y);
    }
}

#[derive(Component)]
struct OverUi;

#[derive(Component)]
struct OverReturn;

fn over_enter(
    mut commands: Commands,
    standings: Res<FinalStandings>,
    my: Res<MyPlayer>,
    mut q_window: Query<&mut Window, With<PrimaryWindow>>,
    mut cap: ResMut<MouseCaptured>,
) {
    if let Ok(mut window) = q_window.get_single_mut() {
        window.cursor.grab_mode = CursorGrabMode::None;
        window.cursor.visible = true;
        cap.0 = false;
    }
    let mut rows = standings.rows.clone();
    rows.sort_by(|a, b| protocol::rank_cmp(a, b));
    commands
        .spawn((
            NodeBundle {
                style: Style {
                    width: Val::Percent(100.0),
                    height: Val::Percent(100.0),
                    flex_direction: FlexDirection::Column,
                    align_items: AlignItems::Center,
                    justify_content: JustifyContent::Center,
                    row_gap: Val::Px(6.0),
                    ..default()
                },
                background_color: BackgroundColor(Color::srgba(0.03, 0.04, 0.10, 0.94)),
                ..default()
            },
            OverUi,
        ))
        .with_children(|root| {
            let (title, color) = if standings.lost {
                ("CONNECTION LOST", Color::srgb(1.0, 0.35, 0.25))
            } else {
                ("GAME OVER", Color::srgb(1.0, 0.84, 0.30))
            };
            root.spawn(TextBundle::from_section(
                title,
                TextStyle {
                    font_size: 52.0,
                    color,
                    ..default()
                },
            ));
            root.spawn(TextBundle::from_section(
                "CONFIRMED   FRAGS   DEATHS",
                TextStyle {
                    font_size: 18.0,
                    color: Color::srgb(0.62, 0.68, 0.80),
                    ..default()
                },
            ));
            for (i, r) in rows.iter().enumerate() {
                let me = my.id == Some(r.id);
                root.spawn(TextBundle::from_section(
                    format!(
                        "{}. {:<16} {:<8}    {:>3}      {:>3}      {:>3}",
                        i + 1,
                        truncate(&r.name, 16),
                        VARIANT_NAMES[r.variant as usize % MINION_VARIANT_COUNT],
                        r.confirmed_kills,
                        r.kills,
                        r.deaths
                    ),
                    TextStyle {
                        font_size: 24.0,
                        color: if me {
                            Color::srgb(0.55, 0.95, 1.0)
                        } else {
                            Color::WHITE
                        },
                        ..default()
                    },
                ));
            }
            let detail = standings.message.as_deref().unwrap_or(if standings.lost {
                "No official result was received. Your last roster is shown for reference."
            } else {
                "Confirmed kills decide rank; frags and fewer deaths break ties."
            });
            root.spawn(TextBundle::from_section(
                detail,
                TextStyle {
                    font_size: 18.0,
                    color: Color::srgb(0.74, 0.78, 0.86),
                    ..default()
                },
            ));
            root.spawn((
                ButtonBundle {
                    style: Style {
                        min_width: Val::Px(250.0),
                        min_height: Val::Px(52.0),
                        margin: UiRect::top(Val::Px(18.0)),
                        align_items: AlignItems::Center,
                        justify_content: JustifyContent::Center,
                        border: UiRect::all(Val::Px(1.0)),
                        ..default()
                    },
                    background_color: BackgroundColor(Color::srgb(0.14, 0.44, 0.33)),
                    border_color: BorderColor(Color::srgb(0.45, 0.72, 0.62)),
                    ..default()
                },
                OverReturn,
            ))
            .with_children(|button| {
                button.spawn(TextBundle::from_section(
                    "RETURN TO SECURE LOBBY",
                    TextStyle {
                        font_size: 19.0,
                        color: Color::WHITE,
                        ..default()
                    },
                ));
            });
            root.spawn(TextBundle::from_section(
                "Enter: return to lobby  |  Your KotH token remains saved in memory",
                TextStyle {
                    font_size: 14.0,
                    color: Color::srgb(0.52, 0.57, 0.68),
                    ..default()
                },
            ));
        });
}

#[allow(clippy::too_many_arguments)]
fn over_input(
    mut commands: Commands,
    keys: Res<ButtonInput<KeyCode>>,
    interactions: Query<&Interaction, (With<OverReturn>, Changed<Interaction>)>,
    args: Res<ClientArgs>,
    mut next: ResMut<NextState<Screen>>,
    mut standings: ResMut<FinalStandings>,
    mut my: ResMut<MyPlayer>,
    mut roster: ResMut<Roster>,
    mut feed: ResMut<KillFeed>,
    mut confirmations: ResMut<ActiveConfirmations>,
    mut avatars: ResMut<Avatars>,
    mut rockets: ResMut<RocketEnts>,
    mut confirmation_ents: ResMut<ConfirmationEnts>,
    mut last_snapshot: ResMut<LastSnapshotTick>,
) {
    let clicked = interactions
        .iter()
        .any(|interaction| *interaction == Interaction::Pressed);
    if !clicked && !keys.just_pressed(KeyCode::Enter) {
        return;
    }

    for (_, entity) in avatars.by_id.drain() {
        commands.entity(entity).despawn_recursive();
    }
    for (_, entity) in rockets.by_id.drain() {
        commands.entity(entity).despawn_recursive();
    }
    for (_, entity) in confirmation_ents.by_id.drain() {
        commands.entity(entity).despawn_recursive();
    }
    *standings = FinalStandings::default();
    *my = MyPlayer::default();
    roster.0.clear();
    feed.0.clear();
    confirmations.0.clear();
    last_snapshot.0 = 0;
    next.set(post_match_screen(&args));
}

fn post_match_screen(args: &ClientArgs) -> Screen {
    if args.demo {
        Screen::Select
    } else {
        Screen::Lobby
    }
}

#[derive(Resource)]
struct RocketAssets {
    body_mesh: Handle<Mesh>,
    body_mat: Handle<StandardMaterial>,
    nose_mesh: Handle<Mesh>,
    nose_mat: Handle<StandardMaterial>,
    fin_mesh: Handle<Mesh>,
    fin_mat: Handle<StandardMaterial>,
    exhaust_mesh: Handle<Mesh>,
    exhaust_mat: Handle<StandardMaterial>,
    serum_mesh: Handle<Mesh>,
    serum_mat: Handle<StandardMaterial>,
    confirmation_mesh: Handle<Mesh>,
    confirmation_own_mat: Handle<StandardMaterial>,
    confirmation_deny_mat: Handle<StandardMaterial>,
    confirmation_other_mat: Handle<StandardMaterial>,
}

#[derive(Component)]
struct SerumShell;

/// Purple berserk aura — everyone can see who's juiced.
fn serum_glow(
    q_avatars: Query<(&PlayerAvatar, &Children)>,
    mut q_shells: Query<&mut Visibility, With<SerumShell>>,
) {
    for (avatar, children) in &q_avatars {
        for child in children {
            if let Ok(mut vis) = q_shells.get_mut(*child) {
                *vis = if avatar.serum_active {
                    Visibility::Visible
                } else {
                    Visibility::Hidden
                };
            }
        }
    }
}

fn setup_rocket_assets(
    mut commands: Commands,
    mut meshes: ResMut<Assets<Mesh>>,
    mut materials: ResMut<Assets<StandardMaterial>>,
) {
    commands.insert_resource(RocketAssets {
        body_mesh: meshes.add(Cylinder::new(0.11, 0.46)),
        body_mat: materials.add(StandardMaterial {
            base_color: Color::srgb(0.30, 0.42, 0.30),
            perceptual_roughness: 0.6,
            ..default()
        }),
        nose_mesh: meshes.add(Cone {
            radius: 0.11,
            height: 0.22,
        }),
        nose_mat: materials.add(StandardMaterial {
            base_color: Color::srgb(0.95, 0.35, 0.10),
            emissive: LinearRgba::new(0.8, 0.25, 0.04, 1.0),
            ..default()
        }),
        fin_mesh: meshes.add(Cuboid::new(0.02, 0.16, 0.14)),
        fin_mat: materials.add(StandardMaterial {
            base_color: Color::srgb(0.85, 0.75, 0.20),
            ..default()
        }),
        exhaust_mesh: meshes.add(Cone {
            radius: 0.09,
            height: 0.30,
        }),
        exhaust_mat: materials.add(StandardMaterial {
            base_color: Color::srgba(1.0, 0.75, 0.25, 0.85),
            emissive: LinearRgba::new(6.0, 3.2, 0.5, 1.0),
            alpha_mode: AlphaMode::Blend,
            ..default()
        }),
        serum_mesh: meshes.add(Capsule3d::new(0.62, 0.7)),
        serum_mat: materials.add(StandardMaterial {
            base_color: Color::srgba(0.62, 0.20, 0.85, 0.30),
            emissive: LinearRgba::new(0.35, 0.06, 0.55, 1.0),
            alpha_mode: AlphaMode::Blend,
            ..default()
        }),
        confirmation_mesh: meshes.add(Cuboid::new(0.13, 0.13, 0.26)),
        confirmation_own_mat: materials.add(StandardMaterial {
            base_color: Color::srgb(1.0, 0.84, 0.16),
            emissive: LinearRgba::new(3.0, 1.8, 0.08, 1.0),
            ..default()
        }),
        confirmation_deny_mat: materials.add(StandardMaterial {
            base_color: Color::srgb(1.0, 0.24, 0.14),
            emissive: LinearRgba::new(2.5, 0.12, 0.03, 1.0),
            ..default()
        }),
        confirmation_other_mat: materials.add(StandardMaterial {
            base_color: Color::srgb(0.55, 0.58, 0.64),
            emissive: LinearRgba::new(0.12, 0.12, 0.14, 1.0),
            ..default()
        }),
    });
}

fn spawn_confirmation(
    commands: &mut Commands,
    fx: &RocketAssets,
    state: &protocol::ConfirmationState,
    my_id: Option<PlayerId>,
) -> Entity {
    let pos = Vec3::new(
        state.pos.x.to_f32(),
        state.pos.y.to_f32(),
        state.pos.z.to_f32(),
    );
    let material = if my_id == Some(state.killer) {
        fx.confirmation_own_mat.clone()
    } else if my_id == Some(state.victim) {
        fx.confirmation_deny_mat.clone()
    } else {
        fx.confirmation_other_mat.clone()
    };
    commands
        .spawn((
            SpatialBundle::from_transform(Transform::from_translation(pos + Vec3::Y * 0.55)),
            RenderLayers::layer(0),
            ConfirmationVis {
                base_y: pos.y + 0.55,
                expires_in: state.expires_in,
            },
        ))
        .with_children(|parent| {
            for (index, angle) in [-0.55f32, 0.0, 0.55].into_iter().enumerate() {
                parent.spawn((
                    PbrBundle {
                        mesh: fx.confirmation_mesh.clone(),
                        material: material.clone(),
                        transform: Transform::from_xyz(
                            angle.sin() * 0.20,
                            -angle.cos() * 0.10 + 0.10,
                            0.0,
                        )
                        .with_rotation(Quat::from_rotation_z(angle))
                        .with_scale(if index == 1 {
                            Vec3::ONE
                        } else {
                            Vec3::splat(0.92)
                        }),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
            }
        })
        .id()
}

fn animate_confirmations(time: Res<Time>, mut query: Query<(&mut Transform, &ConfirmationVis)>) {
    let elapsed = time.elapsed_seconds();
    for (mut transform, confirmation) in &mut query {
        transform.translation.y = confirmation.base_y + (elapsed * 3.2).sin() * 0.12;
        transform.rotation = Quat::from_rotation_y(elapsed * 1.8);
        let urgency = (confirmation.expires_in as f32 / (64.0 * 3.0)).clamp(0.65, 1.0);
        transform.scale = Vec3::splat(urgency);
    }
}

/// Spawn a finned rocket with glowing nose, exhaust flame, and engine
/// light, facing along `vel`.
fn spawn_rocket(commands: &mut Commands, fx: &RocketAssets, pos: Vec3, vel: Vec3) -> Entity {
    commands
        .spawn((
            SpatialBundle::from_transform(
                Transform::from_translation(pos).looking_to(vel.normalize_or_zero(), Vec3::Y),
            ),
            RenderLayers::layer(0),
            RocketVis { vel },
        ))
        .with_children(|p| {
            // meshes are authored +Y-up; tilt them onto the -Z flight axis
            let lay = Quat::from_rotation_x(-std::f32::consts::FRAC_PI_2);
            p.spawn((
                PbrBundle {
                    mesh: fx.body_mesh.clone(),
                    material: fx.body_mat.clone(),
                    transform: Transform::from_rotation(lay),
                    ..default()
                },
                RenderLayers::layer(0),
            ));
            p.spawn((
                PbrBundle {
                    mesh: fx.nose_mesh.clone(),
                    material: fx.nose_mat.clone(),
                    transform: Transform::from_xyz(0.0, 0.0, -0.34).with_rotation(lay),
                    ..default()
                },
                RenderLayers::layer(0),
            ));
            for k in 0..4 {
                let spin = Quat::from_rotation_z(k as f32 * std::f32::consts::FRAC_PI_2);
                p.spawn((
                    PbrBundle {
                        mesh: fx.fin_mesh.clone(),
                        material: fx.fin_mat.clone(),
                        transform: Transform::from_rotation(lay * spin)
                            .with_translation(Vec3::new(0.0, 0.0, 0.18)),
                        ..default()
                    },
                    RenderLayers::layer(0),
                ));
            }
            p.spawn((
                PbrBundle {
                    mesh: fx.exhaust_mesh.clone(),
                    material: fx.exhaust_mat.clone(),
                    transform: Transform::from_xyz(0.0, 0.0, 0.40)
                        .with_rotation(Quat::from_rotation_x(std::f32::consts::FRAC_PI_2)),
                    ..default()
                },
                RenderLayers::layer(0),
            ));
            p.spawn((
                PointLightBundle {
                    point_light: PointLight {
                        color: Color::srgb(1.0, 0.65, 0.25),
                        intensity: 60_000.0,
                        range: 9.0,
                        shadows_enabled: false,
                        ..default()
                    },
                    transform: Transform::from_xyz(0.0, 0.0, 0.45),
                    ..default()
                },
                RenderLayers::layer(0),
            ));
        })
        .id()
}

/// One-shot decaying light (rocket explosions).
#[derive(Component)]
struct Flash {
    ttl: f32,
}

fn flash_decay(
    time: Res<Time>,
    mut commands: Commands,
    mut q: Query<(Entity, &mut Flash, &mut PointLight)>,
) {
    for (ent, mut flash, mut light) in q.iter_mut() {
        flash.ttl -= time.delta_seconds();
        if flash.ttl <= 0.0 {
            commands.entity(ent).despawn_recursive();
        } else {
            light.intensity = 1_400_000.0 * (flash.ttl / 0.35).clamp(0.0, 1.0);
        }
    }
}

/// Extrapolate rockets between snapshots so they fly smoothly, leaving a
/// sputtering exhaust trail.
fn fly_rockets(
    time: Res<Time>,
    mut trails: ResMut<FireTrails>,
    mut q: Query<(&mut Transform, &RocketVis)>,
) {
    let t = time.elapsed_seconds();
    for (mut tf, r) in q.iter_mut() {
        tf.translation += r.vel * time.delta_seconds();
        let back = -*tf.forward();
        let tail = tf.translation + back * 0.5;
        let jitter = Vec3::new((t * 31.0).sin(), (t * 27.0).cos(), (t * 23.0).sin()) * 0.07;
        trails.entries.push(Trail {
            from: tail,
            to: tail + back * (0.7 + (t * 19.0).sin().abs() * 0.5) + jitter,
            life: 0.22,
            hit: false,
            flame: true,
        });
    }
}

// -------------------------------------------------------- pickups & guns

#[derive(Resource, Default)]
struct PowerupsUp(Vec<bool>);

#[derive(Component)]
struct PickupItem {
    index: usize,
    base_y: f32,
    phase: f32,
}

/// Quake-style rail: a core beam plus a helix winding around it.
fn push_rail_trail(trails: &mut FireTrails, from: Vec3, to: Vec3, hit: bool) {
    trails.entries.push(Trail {
        from,
        to,
        life: FIRE_TRAIL_SECONDS,
        hit,
        flame: false,
    });
    let dir = (to - from).normalize_or_zero();
    let len = (to - from).length();
    if len < 1.0 {
        return;
    }
    let side = dir.cross(Vec3::Y).normalize_or_zero();
    let up = dir.cross(side);
    if len < 4.0 {
        // claw swipe / point-blank: a short fan of slashes, no helix
        for k in [-0.35f32, 0.0, 0.35] {
            trails.entries.push(Trail {
                from: from + side * k * 0.4,
                to: from + dir * len + side * k + up * k.abs() * 0.3,
                life: 0.15,
                hit,
                flame: false,
            });
        }
        return;
    }
    let turns = (len * 0.8).min(40.0) as i32;
    let step = len / turns.max(1) as f32;
    let mut prev = from;
    for i in 1..=turns {
        let t = i as f32 * step;
        let a = t * 2.4;
        let p = from + dir * t + (side * a.cos() + up * a.sin()) * 0.12;
        trails.entries.push(Trail {
            from: prev,
            to: p,
            life: FIRE_TRAIL_SECONDS * 1.2,
            hit,
            flame: false,
        });
        prev = p;
    }
}

#[derive(Component)]
struct MuzzleFlash;

/// Muzzle flash light tied to recoil energy.
fn muzzle_flash(
    my: Res<MyPlayer>,
    kick: Res<ViewKick>,
    q_views: Query<(&WeaponView, &Children)>,
    mut q_lights: Query<&mut PointLight, With<MuzzleFlash>>,
) {
    for (view, children) in &q_views {
        let active = view.0 == my.weapon;
        for child in children {
            if let Ok(mut light) = q_lights.get_mut(*child) {
                light.intensity = if active && kick.0 > 0.25 {
                    kick.0 * 420_000.0
                } else {
                    0.0
                };
            }
        }
    }
}

/// Recoil + walk bob + switch-raise on the active viewmodel.
fn viewmodel_motion(
    time: Res<Time>,
    my: Res<MyPlayer>,
    mut kick: ResMut<ViewKick>,
    mut last_weapon: Local<Option<protocol::WeaponKind>>,
    mut raise: Local<f32>,
    mut q: Query<(&mut Transform, &WeaponView, &ViewAnchor)>,
) {
    kick.0 *= (-10.0 * time.delta_seconds()).exp();
    if *last_weapon != Some(my.weapon) {
        if last_weapon.is_some() {
            *raise = 0.35;
        }
        *last_weapon = Some(my.weapon);
    }
    *raise = (*raise - time.delta_seconds()).max(0.0);
    let lower = (*raise / 0.35).powi(2);
    let moving = (my.pos_to - my.pos_from).length() > 0.01;
    let t = time.elapsed_seconds();
    let bob = if moving {
        Vec3::new(
            (t * 7.4).sin() * 0.012,
            (t * 14.8).sin().abs() * -0.014,
            0.0,
        )
    } else {
        Vec3::new(0.0, (t * 1.8).sin() * 0.004, 0.0)
    };
    for (mut tf, view, anchor) in q.iter_mut() {
        if view.0 != my.weapon {
            continue;
        }
        // per-weapon heft: the bazooka slams, the rail snaps
        let (mult, roll) = match view.0 {
            protocol::WeaponKind::RocketLauncher => (1.8, 0.14),
            protocol::WeaponKind::Shotgun => (1.4, 0.06),
            _ => (1.0, 0.0),
        };
        let k = kick.0 * mult;
        tf.translation =
            anchor.0 + bob + Vec3::new(0.0, k * 0.03 - lower * 0.30, k * 0.10 + lower * 0.12);
        tf.rotation = Quat::from_rotation_y(-0.10)
            * Quat::from_rotation_x(k * 0.16 - lower * 0.85)
            * Quat::from_rotation_z(k * roll);
    }
}

/// The hall jumbotron's pitch deck: the glb carries the slides as named
/// nodes (slide_0..slide_3); show one at a time on a timer. Pure render
/// decoration — geometry stays single-sourced in the zone script.
fn jumbotron_slides(
    time: Res<Time>,
    q_names: Query<(Entity, &Name)>,
    mut q_vis: Query<&mut Visibility>,
) {
    const SLIDES: usize = 5;
    const SECS_PER_SLIDE: f32 = 6.0;
    let active = (time.elapsed_seconds() / SECS_PER_SLIDE) as usize % SLIDES;
    for (ent, name) in &q_names {
        let Some(rest) = name.as_str().strip_prefix("slide_") else {
            continue;
        };
        let Ok(idx) = rest.parse::<usize>() else {
            continue;
        };
        if let Ok(mut vis) = q_vis.get_mut(ent) {
            let target = if idx == active {
                Visibility::Inherited
            } else {
                Visibility::Hidden
            };
            if *vis != target {
                *vis = target;
            }
        }
    }
}

/// Show exactly the weapon_* glb node matching each avatar's held weapon.
fn sync_held_weapons(
    q_avatars: Query<(Entity, &PlayerAvatar)>,
    q_children: Query<&Children>,
    q_names: Query<&Name>,
    mut q_vis: Query<&mut Visibility>,
) {
    for (root, avatar) in &q_avatars {
        let want = match avatar.held_weapon {
            protocol::WeaponKind::Rail => "weapon_rail",
            protocol::WeaponKind::Flamethrower => "weapon_flamer",
            protocol::WeaponKind::Shotgun => "weapon_shotgun",
            protocol::WeaponKind::RocketLauncher => "weapon_bazooka",
        };
        let mut stack = vec![root];
        while let Some(e) = stack.pop() {
            if let Ok(children) = q_children.get(e) {
                stack.extend(children.iter().copied());
            }
            let Ok(name) = q_names.get(e) else { continue };
            if !name.as_str().starts_with("weapon_") {
                continue;
            }
            if let Ok(mut vis) = q_vis.get_mut(e) {
                let target = if name.as_str() == want {
                    Visibility::Inherited
                } else {
                    Visibility::Hidden
                };
                if *vis != target {
                    *vis = target;
                }
            }
        }
    }
}

/// Pickup pads bob/spin their item; taken items vanish until respawn
/// (with a grab blip scaled by distance).
fn pickup_anim(
    time: Res<Time>,
    up: Res<PowerupsUp>,
    mut commands: Commands,
    sounds: Option<Res<Sounds>>,
    vols: Res<AudioVolumes>,
    my: Res<MyPlayer>,
    mut prev: Local<Vec<bool>>,
    mut q: Query<(&mut Transform, &mut Visibility, &PickupItem)>,
) {
    let t = time.elapsed_seconds();
    for (mut tf, mut vis, item) in q.iter_mut() {
        let available = up.0.get(item.index).copied().unwrap_or(true);
        *vis = if available {
            Visibility::Visible
        } else {
            Visibility::Hidden
        };
        if prev.len() <= item.index {
            prev.resize(item.index + 1, true);
        }
        if prev[item.index] && !available {
            if let Some(sounds) = sounds.as_ref() {
                let vol = vols.sfx * 0.9 * att(my.pos_to, tf.translation);
                sfx(&mut commands, &sounds.pickup, vol);
            }
        }
        prev[item.index] = available;
        tf.translation.y = item.base_y + ((t * 2.1 + item.phase).sin()) * 0.09;
        tf.rotation = Quat::from_rotation_y(t * 1.3 + item.phase);
    }
}

/// Procedural mini-models for each pickup kind, parented to its pad item.
fn spawn_pickup_model(
    commands: &mut Commands,
    parent: Entity,
    kind: PowerupKind,
    meshes: &mut Assets<Mesh>,
    materials: &mut Assets<StandardMaterial>,
) {
    let mut child = |mesh: Handle<Mesh>, color: Color, emissive: f32, tf: Transform| {
        let mat = materials.add(StandardMaterial {
            base_color: color,
            emissive: LinearRgba::from(color.to_linear()) * emissive,
            ..default()
        });
        commands.entity(parent).with_children(|p| {
            p.spawn((
                PbrBundle {
                    mesh,
                    material: mat,
                    transform: tf,
                    ..default()
                },
                RenderLayers::layer(0),
            ));
        });
    };
    match kind {
        PowerupKind::Health | PowerupKind::MegaHealth => {
            let (size, body, cross) = if kind == PowerupKind::Health {
                (
                    0.34,
                    Color::srgb(0.95, 0.96, 0.94),
                    Color::srgb(0.90, 0.18, 0.18),
                )
            } else {
                (
                    0.48,
                    Color::srgb(0.20, 0.45, 0.95),
                    Color::srgb(0.95, 0.96, 0.98),
                )
            };
            child(
                meshes.add(Cuboid::new(size, size, size)),
                body,
                0.05,
                Transform::IDENTITY,
            );
            for (sx, sy) in [(size * 0.9, size * 0.34), (size * 0.34, size * 0.9)] {
                child(
                    meshes.add(Cuboid::new(sx, sy, size * 1.04)),
                    cross,
                    0.15,
                    Transform::IDENTITY,
                );
            }
        }
        PowerupKind::Armor => {
            child(
                meshes.add(Cuboid::new(0.40, 0.42, 0.16)),
                Color::srgb(0.45, 0.60, 0.92),
                0.1,
                Transform::IDENTITY,
            );
            for dx in [-0.24f32, 0.24] {
                child(
                    meshes.add(Cuboid::new(0.10, 0.18, 0.18)),
                    Color::srgb(0.60, 0.72, 0.98),
                    0.1,
                    Transform::from_xyz(dx, 0.16, 0.0),
                );
            }
        }
        PowerupKind::QuadDamage => {
            // purple crystal: two cones base to base
            child(
                meshes.add(Mesh::from(bevy::math::primitives::Cone {
                    radius: 0.20,
                    height: 0.30,
                })),
                Color::srgb(0.70, 0.25, 0.95),
                0.6,
                Transform::from_xyz(0.0, 0.15, 0.0),
            );
            child(
                meshes.add(Mesh::from(bevy::math::primitives::Cone {
                    radius: 0.20,
                    height: 0.30,
                })),
                Color::srgb(0.70, 0.25, 0.95),
                0.6,
                Transform::from_xyz(0.0, -0.15, 0.0)
                    .with_rotation(Quat::from_rotation_z(std::f32::consts::PI)),
            );
        }
        PowerupKind::Haste => {
            for (dz, scale) in [(0.0f32, 1.0f32), (0.16, 0.7)] {
                for sign in [-1.0f32, 1.0] {
                    child(
                        meshes.add(Cuboid::new(0.26 * scale, 0.07, 0.07)),
                        Color::srgb(0.20, 0.95, 0.45),
                        0.4,
                        Transform::from_xyz(sign * 0.10, 0.0, dz)
                            .with_rotation(Quat::from_rotation_y(sign * 0.6)),
                    );
                }
            }
        }
        PowerupKind::Banana => {
            for (i, a) in [-0.5f32, 0.0, 0.5].into_iter().enumerate() {
                child(
                    meshes.add(Cuboid::new(0.10, 0.10, 0.22)),
                    if i == 1 {
                        Color::srgb(1.0, 0.85, 0.20)
                    } else {
                        Color::srgb(0.95, 0.78, 0.15)
                    },
                    0.1,
                    Transform::from_xyz(a.sin() * 0.18, -a.cos() * 0.10 + 0.10, 0.0)
                        .with_rotation(Quat::from_rotation_z(a)),
                );
            }
        }
        PowerupKind::Flamethrower => {
            child(
                meshes.add(Cylinder::new(0.13, 0.38)),
                Color::srgb(0.95, 0.45, 0.10),
                0.15,
                Transform::IDENTITY,
            );
            child(
                meshes.add(Cylinder::new(0.05, 0.18)),
                Color::srgb(0.15, 0.16, 0.20),
                0.0,
                Transform::from_xyz(0.16, 0.0, 0.0)
                    .with_rotation(Quat::from_rotation_z(std::f32::consts::FRAC_PI_2)),
            );
        }
        PowerupKind::Shotgun => {
            for dy in [-0.04f32, 0.04] {
                child(
                    meshes.add(Cylinder::new(0.035, 0.52)),
                    Color::srgb(0.55, 0.58, 0.64),
                    0.05,
                    Transform::from_xyz(0.0, dy, 0.0)
                        .with_rotation(Quat::from_rotation_z(std::f32::consts::FRAC_PI_2)),
                );
            }
            child(
                meshes.add(Cuboid::new(0.20, 0.09, 0.08)),
                Color::srgb(0.45, 0.28, 0.12),
                0.0,
                Transform::from_xyz(0.22, -0.02, 0.0),
            );
        }
        PowerupKind::RocketLauncher => {
            child(
                meshes.add(Cylinder::new(0.085, 0.58)),
                Color::srgb(0.80, 0.16, 0.14),
                0.1,
                Transform::from_rotation(Quat::from_rotation_z(std::f32::consts::FRAC_PI_2)),
            );
            child(
                meshes.add(Cylinder::new(0.10, 0.06)),
                Color::srgb(0.15, 0.16, 0.20),
                0.0,
                Transform::from_xyz(-0.30, 0.0, 0.0)
                    .with_rotation(Quat::from_rotation_z(std::f32::consts::FRAC_PI_2)),
            );
        }
        PowerupKind::Serum => {
            child(
                meshes.add(Cylinder::new(0.10, 0.26)),
                Color::srgb(0.62, 0.20, 0.85),
                0.5,
                Transform::IDENTITY,
            );
            child(
                meshes.add(Cylinder::new(0.045, 0.10)),
                Color::srgb(0.80, 0.82, 0.88),
                0.1,
                Transform::from_xyz(0.0, 0.18, 0.0),
            );
            child(
                meshes.add(Cuboid::new(0.08, 0.05, 0.08)),
                Color::srgb(0.35, 0.22, 0.12),
                0.0,
                Transform::from_xyz(0.0, 0.25, 0.0),
            );
        }
    }
}
