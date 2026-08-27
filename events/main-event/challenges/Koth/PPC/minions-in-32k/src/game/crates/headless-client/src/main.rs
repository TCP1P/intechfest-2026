//! Headless AI client.
//!
//! This is intentionally a real network client, not a server-side bot. It only
//! sees `ServerMessage`s and sends ordinary `ClientMessage::Input`s, which keeps
//! it inside the same trust boundary as a player-built CTF bot.

mod ai;

use ai::{profile_for, Bot, BotProfile};
use anyhow::{Context, Result};
use clap::Parser;
use protocol::{ClientMessage, ServerMessage, MAX_MSG_BYTES, PROTOCOL_VERSION};
use quinn::{ClientConfig, Endpoint, RecvStream, SendStream};
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

const DEFAULT_SERVER_NAME: &str = "localhost";

#[derive(Parser, Debug, Clone)]
#[command(
    version,
    about = "Run protocol-level AI players against a minions-in-32K server."
)]
struct Args {
    /// Remote game server as host:port.
    #[arg(long, default_value = "127.0.0.1:4433")]
    remote: SocketAddr,

    /// Base display name. Multiple bots are suffixed as `<name>_N`.
    #[arg(long, default_value = "bot")]
    name: String,

    /// Number of independent bot connections to run.
    #[arg(long, default_value_t = 1)]
    count: usize,

    /// Number of leading clients that use the elite aimbot profile.
    #[arg(long, default_value_t = 0)]
    elite_count: usize,

    /// Number of clients that use the human-like NPC combat profile.
    #[arg(long, default_value_t = 0)]
    npc_count: usize,

    /// Base display name for elite bots.
    #[arg(long, default_value = "aimbot")]
    elite_name: String,

    /// Base display name for arena NPCs.
    #[arg(long, default_value = "NPC")]
    npc_name: String,

    /// TLS server name. Dev servers use a self-signed cert.
    #[arg(long, default_value = DEFAULT_SERVER_NAME)]
    server_name: String,

    /// Pin the server's self-signed cert by SHA-256 of its DER bytes (hex),
    /// matching the `certhash` the server logs / the wasm client uses.
    #[arg(long)]
    cert_hash: Option<String>,

    /// Dev-only: accept ANY server cert without verification. Requires no
    /// --cert-hash. Off by default so a stray bot can't be MITM'd.
    #[arg(long)]
    insecure: bool,

    /// Comma-separated per-match access keys, one per bot (instance mode).
    #[arg(long, value_delimiter = ',')]
    keys: Vec<String>,

    /// Author regression hook for proving that legacy wire versions fail.
    #[arg(long, default_value_t = PROTOCOL_VERSION, hide = true)]
    protocol_version: u16,

    /// Supervisor-only, non-mutating readiness probe. It proves the pinned
    /// QUIC/TLS transport, protocol framing, and authoritative join path by
    /// requiring an intentionally invalid instance key to be rejected.
    #[arg(long, hide = true)]
    readiness_probe: bool,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    rustls::crypto::ring::default_provider()
        .install_default()
        .ok();

    let args = Args::parse();
    if args.readiness_probe {
        return run_readiness_probe(&args).await;
    }
    let count = args.count.clamp(1, protocol::MAX_PLAYERS);
    let npc_count = args.npc_count.min(count);
    let elite_count = args.elite_count.min(count.saturating_sub(npc_count));
    tracing::info!(server = %args.remote, count, "starting headless clients");

    let mut tasks = Vec::with_capacity(count);
    for i in 0..count {
        let bot_args = args.clone();
        let profile = profile_for(i, npc_count, elite_count);
        let name = match profile {
            BotProfile::Npc if npc_count == 1 => bot_args.npc_name.clone(),
            BotProfile::Npc => format!("{}_{}", bot_args.npc_name, i + 1),
            BotProfile::Elite if elite_count == 1 => bot_args.elite_name.clone(),
            BotProfile::Elite => format!("{}_{}", bot_args.elite_name, i + 1),
            BotProfile::Mediocre if count == 1 => bot_args.name.clone(),
            BotProfile::Mediocre => format!("{}_{}", bot_args.name, i + 1),
        };
        tasks.push(tokio::spawn(async move {
            let bot_number = i + 1;
            if let Err(error) = run_bot(bot_args, name, profile, i as u64).await {
                tracing::warn!(bot = bot_number, error = %error, "bot stopped");
            }
        }));
        tokio::time::sleep(Duration::from_millis(80)).await;
    }

    for task in tasks {
        task.await?;
    }
    Ok(())
}

async fn run_readiness_probe(args: &Args) -> Result<()> {
    let key = args
        .keys
        .first()
        .cloned()
        .context("--readiness-probe requires one intentionally invalid --keys value")?;
    let mut endpoint = Endpoint::client("0.0.0.0:0".parse()?)?;
    endpoint.set_default_client_config(client_config(args)?);
    let connection = tokio::time::timeout(
        Duration::from_secs(3),
        endpoint
            .connect(args.remote, &args.server_name)
            .context("connect readiness probe")?,
    )
    .await
    .context("readiness probe handshake timed out")?
    .context("readiness probe handshake")?;
    let (mut send, mut receive) =
        tokio::time::timeout(Duration::from_secs(2), connection.open_bi())
            .await
            .context("readiness probe stream timed out")?
            .context("open readiness probe stream")?;
    write_msg(
        &mut send,
        &ClientMessage::Join {
            protocol_version: args.protocol_version,
            name: "__rsctf_quality_probe__".to_string(),
            key: Some(key),
            variant: 0,
        },
    )
    .await?;
    let response = tokio::time::timeout(
        Duration::from_secs(2),
        read_msg::<ServerMessage>(&mut receive),
    )
    .await
    .context("readiness probe response timed out")??;
    validate_readiness_response(&response)?;
    connection.close(0u32.into(), b"readiness probe complete");
    endpoint.wait_idle().await;
    tracing::info!(
        protocol_version = PROTOCOL_VERSION,
        "readiness probe passed"
    );
    Ok(())
}

fn validate_readiness_response(response: &ServerMessage) -> Result<()> {
    match response {
        ServerMessage::Reject { reason } if reason == "invalid access key" => Ok(()),
        ServerMessage::Reject { reason } => {
            anyhow::bail!("readiness probe received unexpected rejection: {reason}")
        }
        ServerMessage::Welcome { .. } => {
            anyhow::bail!("readiness probe key was incorrectly admitted")
        }
        _ => anyhow::bail!("readiness probe received an unexpected server message"),
    }
}

async fn run_bot(args: Args, name: String, profile: BotProfile, salt: u64) -> Result<()> {
    let mut endpoint = Endpoint::client("0.0.0.0:0".parse()?)?;
    endpoint.set_default_client_config(client_config(&args)?);

    tracing::info!(%name, server = %args.remote, "connecting");
    let conn = endpoint
        .connect(args.remote, &args.server_name)
        .context("connect")?
        .await
        .context("handshake")?;

    let (mut send, mut recv) = conn.open_bi().await.context("open stream")?;
    write_msg(
        &mut send,
        &ClientMessage::Join {
            protocol_version: args.protocol_version,
            name: name.clone(),
            key: args.keys.get(salt as usize).cloned(),
            variant: (salt % 4) as u8,
        },
    )
    .await?;

    let mut bot = Bot::new(name, profile, salt);
    // Snapshots ride unreliable datagrams while control messages use the
    // stream. Independent readers avoid cancelling a framed stream read.
    let (msg_tx, mut msg_rx) = tokio::sync::mpsc::channel::<ServerMessage>(256);
    {
        let tx = msg_tx.clone();
        tokio::spawn(async move {
            loop {
                match read_msg::<ServerMessage>(&mut recv).await {
                    Ok(message) => {
                        if tx.send(message).await.is_err() {
                            return;
                        }
                    }
                    Err(error) => {
                        tracing::debug!(error = %error, "stream reader exit");
                        return;
                    }
                }
            }
        });
    }
    {
        let tx = msg_tx;
        let conn = conn.clone();
        tokio::spawn(async move {
            while let Ok(bytes) = conn.read_datagram().await {
                if bytes.len() > MAX_MSG_BYTES {
                    tracing::debug!(len = bytes.len(), "oversized datagram dropped");
                    continue;
                }
                let Ok(message) = protocol::decode::<ServerMessage>(&bytes) else {
                    continue;
                };
                match tx.try_send(message) {
                    Ok(()) => {}
                    Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => return,
                    Err(tokio::sync::mpsc::error::TrySendError::Full(_)) => {}
                }
            }
        });
    }

    let mut deferred: Option<ServerMessage> = None;
    loop {
        let message = match deferred.take() {
            Some(message) => message,
            None => match msg_rx.recv().await {
                Some(message) => message,
                None => {
                    tracing::info!(name = %bot.name, "connection closed");
                    return Ok(());
                }
            },
        };
        match message {
            ServerMessage::Events { tick, events } => {
                for event in events {
                    bot.observe_event(tick, event);
                    match event {
                        protocol::GameEvent::ConfirmationDropped { killer, victim, .. }
                            if bot.id == Some(killer) =>
                        {
                            tracing::info!(tick, victim, "confirmation dropped");
                        }
                        protocol::GameEvent::KillConfirmed { killer, victim, .. }
                            if bot.id == Some(killer) =>
                        {
                            tracing::info!(tick, victim, "kill confirmed");
                        }
                        protocol::GameEvent::KillDenied { killer, victim, .. }
                            if bot.id == Some(killer) =>
                        {
                            tracing::info!(tick, victim, "kill denied");
                        }
                        protocol::GameEvent::Kill { attacker, victim }
                            if bot.id == Some(attacker) =>
                        {
                            tracing::info!(tick, victim, "bot frag");
                        }
                        protocol::GameEvent::Kill { attacker, victim }
                            if bot.id == Some(victim) =>
                        {
                            tracing::info!(tick, attacker, "bot died");
                        }
                        _ => {}
                    }
                }
            }
            ServerMessage::Roster(_) => {}
            ServerMessage::GameOver { .. } => {
                tracing::info!(name = %bot.name, "game over — disconnecting");
                return Ok(());
            }
            ServerMessage::Welcome {
                your_id,
                tick_rate_hz,
            } => {
                bot.id = Some(your_id);
                bot.tick_rate_hz = tick_rate_hz.max(1);
                tracing::info!(
                    name = %bot.name,
                    profile = ?bot.profile,
                    id = your_id,
                    tick_rate_hz,
                    "joined"
                );
            }
            ServerMessage::Reject { reason } => anyhow::bail!("rejected: {reason}"),
            ServerMessage::Snapshot(mut snapshot) => {
                // Newest wins: act only on the freshest already-queued state.
                while let Ok(next) = msg_rx.try_recv() {
                    match next {
                        ServerMessage::Snapshot(candidate) => {
                            if candidate.tick >= snapshot.tick {
                                snapshot = candidate;
                            }
                        }
                        other => {
                            deferred = Some(other);
                            break;
                        }
                    }
                }
                if let Some(input) = bot.think(&snapshot) {
                    write_msg(&mut send, &ClientMessage::Input(input)).await?;
                }
            }
            ServerMessage::PlayerJoined { .. } | ServerMessage::PlayerLeft { .. } => {}
        }
    }
}

fn client_config(args: &Args) -> Result<ClientConfig> {
    let verifier: Arc<dyn rustls::client::danger::ServerCertVerifier> =
        match (args.cert_hash.as_deref(), args.insecure) {
            (Some(hash), false) => Arc::new(PinnedCert::from_hex(hash)?),
            (Some(_), true) => anyhow::bail!("--cert-hash and --insecure are mutually exclusive"),
            (None, true) => {
                tracing::warn!("--insecure: accepting any server cert without verification");
                Arc::new(NoVerify)
            }
            (None, false) => {
                tracing::warn!(
                    "no --cert-hash given: accepting any server cert (dev fallback). Pass \
                     --cert-hash <sha256-hex> (the server's logged cert_hash) to pin it."
                );
                Arc::new(NoVerify)
            }
        };

    let mut crypto = rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_no_client_auth();
    crypto.alpn_protocols = vec![protocol::ALPN.to_vec()];
    let quic = Arc::new(quinn::crypto::rustls::QuicClientConfig::try_from(crypto)?);
    Ok(ClientConfig::new(quic))
}

fn sha256(bytes: &[u8]) -> Vec<u8> {
    rustls::crypto::ring::cipher_suite::TLS13_AES_128_GCM_SHA256
        .tls13()
        .expect("tls13 suite")
        .common
        .hash_provider
        .hash(bytes)
        .as_ref()
        .to_vec()
}

fn parse_hex(value: &str) -> Result<Vec<u8>> {
    let value = value.trim();
    if value.len() % 2 != 0 {
        anyhow::bail!("cert hash must have an even number of hex digits");
    }
    (0..value.len() / 2)
        .map(|index| {
            u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)
                .map_err(|_| anyhow::anyhow!("cert hash is not valid hex"))
        })
        .collect()
}

#[derive(Debug)]
struct PinnedCert {
    expected: Vec<u8>,
}

impl PinnedCert {
    fn from_hex(hash: &str) -> Result<Self> {
        let expected = parse_hex(hash)?;
        if expected.len() != 32 {
            anyhow::bail!("--cert-hash must be a 32-byte (64 hex char) SHA-256 digest");
        }
        Ok(Self { expected })
    }
}

impl rustls::client::danger::ServerCertVerifier for PinnedCert {
    fn verify_server_cert(
        &self,
        end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp_response: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        if sha256(end_entity.as_ref()) == self.expected {
            Ok(rustls::client::danger::ServerCertVerified::assertion())
        } else {
            Err(rustls::Error::General(
                "server cert SHA-256 does not match --cert-hash".into(),
            ))
        }
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        verify_schemes()
    }
}

#[derive(Debug)]
struct NoVerify;

impl rustls::client::danger::ServerCertVerifier for NoVerify {
    fn verify_server_cert(
        &self,
        _end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp_response: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        Ok(rustls::client::danger::ServerCertVerified::assertion())
    }

    fn verify_tls12_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn verify_tls13_signature(
        &self,
        _message: &[u8],
        _cert: &rustls::pki_types::CertificateDer<'_>,
        _dss: &rustls::DigitallySignedStruct,
    ) -> Result<rustls::client::danger::HandshakeSignatureValid, rustls::Error> {
        Ok(rustls::client::danger::HandshakeSignatureValid::assertion())
    }

    fn supported_verify_schemes(&self) -> Vec<rustls::SignatureScheme> {
        verify_schemes()
    }
}

fn verify_schemes() -> Vec<rustls::SignatureScheme> {
    use rustls::SignatureScheme::*;
    vec![
        RSA_PKCS1_SHA256,
        RSA_PKCS1_SHA384,
        RSA_PKCS1_SHA512,
        ECDSA_NISTP256_SHA256,
        ECDSA_NISTP384_SHA384,
        ED25519,
        RSA_PSS_SHA256,
        RSA_PSS_SHA384,
        RSA_PSS_SHA512,
    ]
}

async fn read_msg<T: for<'de> serde::Deserialize<'de>>(recv: &mut RecvStream) -> Result<T> {
    let mut length = [0u8; 4];
    recv.read_exact(&mut length).await?;
    let length = u32::from_le_bytes(length) as usize;
    if length > MAX_MSG_BYTES {
        anyhow::bail!("oversized message ({length} bytes)");
    }
    let mut bytes = vec![0u8; length];
    recv.read_exact(&mut bytes).await?;
    Ok(protocol::decode(&bytes)?)
}

async fn write_msg<T: serde::Serialize>(send: &mut SendStream, message: &T) -> Result<()> {
    let bytes = protocol::encode(message)?;
    send.write_all(&(bytes.len() as u32).to_le_bytes()).await?;
    send.write_all(&bytes).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn readiness_probe_accepts_only_the_expected_non_mutating_rejection() {
        assert!(validate_readiness_response(&ServerMessage::Reject {
            reason: "invalid access key".to_string(),
        })
        .is_ok());
        assert!(validate_readiness_response(&ServerMessage::Reject {
            reason: "server full".to_string(),
        })
        .is_err());
        assert!(validate_readiness_response(&ServerMessage::Welcome {
            your_id: 0,
            tick_rate_hz: 64,
        })
        .is_err());
    }
}
