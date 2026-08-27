//! WebTransport → QUIC loopback bridge for browser (wasm) clients.
//!
//! Browsers can't open raw QUIC, so wasm clients connect here over
//! WebTransport; each session is bridged byte-for-byte onto a loopback
//! QUIC connection into our own endpoint. One code path keeps owning
//! game connections (anti-cheat, takeover, telemetry) and the bridge
//! adds only loopback latency.
//!
//! TLS: browsers require either a CA-trusted cert or
//! `serverCertificateHashes` with an ECDSA cert valid ≤14 days. The
//! backend mints one per instance and passes it via WT_CERT_DER_B64 /
//! WT_KEY_DER_B64 (and embeds the hash in the /wasm URL); without those
//! we self-sign and log the hash.

use anyhow::{Context, Result};
use base64::Engine;
use protocol::{ClientMessage, MAX_MSG_BYTES, MAX_NAME_LEN};
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;
use wtransport::endpoint::IncomingSession;
use wtransport::tls::{Certificate, CertificateChain, PrivateKey, Sha256DigestFmt};
use wtransport::{Endpoint, Identity, ServerConfig};

/// Browser sessions a single bridge will carry at once. A few above the
/// player cap to allow reconnect churn without unbounded session/endpoint
/// growth from a flood of WT handshakes.
const MAX_WT_SESSIONS: usize = protocol::MAX_PLAYERS + 4;
/// A browser session must deliver its first (Join) frame this fast or be
/// dropped before we spend a loopback QUIC connection on it.
const WT_HANDSHAKE_DEADLINE: Duration = Duration::from_secs(5);
/// Idle ceiling on the whole bridged session (mirrors the QUIC idle timeout).
const WT_IDLE_TIMEOUT: Duration = Duration::from_secs(10);
/// Datagram up-pump token bucket (snapshots are downstream; upstream is just
/// input acks/keepalives — anything more is a flood).
const WT_DG_RATE_PER_SEC: f64 = 256.0;
const WT_DG_BURST: f64 = 512.0;

pub async fn run_bridge(bind: SocketAddr, quic_target: SocketAddr) -> Result<()> {
    let identity = identity_from_env().await?;
    let hash = identity.certificate_chain().as_slice()[0]
        .hash()
        .fmt(Sha256DigestFmt::BytesArray);
    tracing::info!(%bind, cert_hash = %hash, "webtransport bridge ready");

    let config = ServerConfig::builder()
        .with_bind_address(bind)
        .with_identity(identity)
        .keep_alive_interval(Some(std::time::Duration::from_secs(3)))
        .max_idle_timeout(Some(WT_IDLE_TIMEOUT))
        .context("wt idle timeout")?
        .build();
    let server = Endpoint::server(config)?;

    // One loopback QUIC client endpoint shared across every bridged session,
    // instead of standing up a fresh UDP socket + endpoint per browser.
    let quic_endpoint = Arc::new(make_loopback_client()?);
    let sessions = Arc::new(tokio::sync::Semaphore::new(MAX_WT_SESSIONS));

    loop {
        let session = server.accept().await;
        let quic_endpoint = quic_endpoint.clone();
        let Ok(permit) = sessions.clone().try_acquire_owned() else {
            tracing::warn!("wt session cap hit; dropping session");
            continue;
        };
        tokio::spawn(async move {
            if let Err(e) = bridge_session(session, quic_target, quic_endpoint).await {
                tracing::debug!(error = %e, "wt session ended");
            }
            drop(permit);
        });
    }
}

/// Shared loopback QUIC client endpoint (cert is ours; skip verification).
fn make_loopback_client() -> Result<quinn::Endpoint> {
    let mut tls = rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(NoVerify))
        .with_no_client_auth();
    tls.alpn_protocols = vec![protocol::ALPN.to_vec()];
    let quic_tls = quinn::crypto::rustls::QuicClientConfig::try_from(tls)?;
    let mut endpoint = quinn::Endpoint::client("127.0.0.1:0".parse()?)?;
    endpoint.set_default_client_config(quinn::ClientConfig::new(Arc::new(quic_tls)));
    Ok(endpoint)
}

async fn identity_from_env() -> Result<Identity> {
    match (
        std::env::var("WT_CERT_DER_B64"),
        std::env::var("WT_KEY_DER_B64"),
    ) {
        (Ok(cert), Ok(key)) => {
            let b64 = base64::engine::general_purpose::STANDARD;
            let cert = Certificate::from_der(b64.decode(cert).context("WT_CERT_DER_B64")?)
                .map_err(|e| anyhow::anyhow!("invalid WT cert: {e}"))?;
            let key = PrivateKey::from_der_pkcs8(b64.decode(key).context("WT_KEY_DER_B64")?);
            Ok(Identity::new(CertificateChain::new(vec![cert]), key))
        }
        _ => Identity::self_signed(["localhost"])
            .map_err(|e| anyhow::anyhow!("self-signed identity: {e}")),
    }
}

async fn bridge_session(
    session: IncomingSession,
    quic_target: SocketAddr,
    endpoint: Arc<quinn::Endpoint>,
) -> Result<()> {
    // Bound the WT handshake + first-Join: a session that never speaks must
    // not park a session permit (and never reaches the loopback QUIC connect).
    let (conn, mut wt_recv, wt_send, first_frame) =
        match tokio::time::timeout(WT_HANDSHAKE_DEADLINE, accept_and_read_join(session)).await {
            Ok(r) => r?,
            Err(_) => anyhow::bail!("wt handshake/Join deadline"),
        };
    let peer = conn.remote_address();
    tracing::info!(%peer, "wt session — first Join validated, bridging to quic");

    let quic = endpoint
        .connect(quic_target, "localhost")?
        .await
        .context("loopback quic connect")?;

    let (mut q_send, q_recv) = quic.open_bi().await?;
    // Replay the validated Join onto the QUIC stream first, then splice.
    q_send.write_all(&first_frame).await?;

    let up = async {
        let mut q_send = q_send;
        tokio::io::copy(&mut wt_recv, &mut q_send).await
    };
    let down = async {
        let mut q_recv = q_recv;
        let mut wt_send = wt_send;
        tokio::io::copy(&mut q_recv, &mut wt_send).await
    };
    // Datagram pumps (v11 snapshots) — best-effort by design, so send
    // failures are dropped, not fatal.
    let dg_down = async {
        loop {
            match quic.read_datagram().await {
                Ok(bytes) => {
                    let _ = conn.send_datagram(&bytes);
                }
                Err(e) => break anyhow::Error::from(e),
            }
        }
    };
    let dg_up = async {
        // Token bucket on the up-pump: a malicious browser can't flood the
        // server-side datagram queue faster than honest input cadence.
        let mut budget = WT_DG_BURST;
        let mut last = std::time::Instant::now();
        loop {
            match conn.receive_datagram().await {
                Ok(dg) => {
                    let now = std::time::Instant::now();
                    budget = (budget + now.duration_since(last).as_secs_f64() * WT_DG_RATE_PER_SEC)
                        .min(WT_DG_BURST);
                    last = now;
                    if budget >= 1.0 {
                        budget -= 1.0;
                        let _ = quic.send_datagram(dg.payload().clone());
                    }
                }
                Err(e) => break anyhow::Error::from(e),
            }
        }
    };
    // Idle is enforced by the WT `max_idle_timeout` (server config) and the
    // loopback QUIC `max_idle_timeout`: a silent session times out at the
    // transport and one of these pumps errors out, ending the select.
    tokio::select! {
        r = up => { r.ok(); }
        r = down => { r.ok(); }
        _ = dg_down => {}
        _ = dg_up => {}
    }
    tracing::info!(%peer, "wt session closed");
    Ok(())
}

/// Accept the WT session, open the bidi stream, and read + validate the first
/// frame as a `Join` BEFORE the caller stands up the loopback QUIC connection.
/// Returns the connection, the (post-Join) recv/send halves, and the raw Join
/// frame to replay onto QUIC.
async fn accept_and_read_join(
    session: IncomingSession,
) -> Result<(
    wtransport::Connection,
    wtransport::RecvStream,
    wtransport::SendStream,
    Vec<u8>,
)> {
    let request = session.await?;
    let conn = request.accept().await?;
    let (wt_send, mut wt_recv) = conn.accept_bi().await?;

    let mut len_buf = [0u8; 4];
    wt_recv.read_exact(&mut len_buf).await?;
    let len = u32::from_le_bytes(len_buf) as usize;
    if len == 0 || len > MAX_MSG_BYTES {
        anyhow::bail!("wt first frame bad length ({len})");
    }
    let mut body = vec![0u8; len];
    wt_recv.read_exact(&mut body).await?;
    match protocol::decode::<ClientMessage>(&body)? {
        ClientMessage::Join {
            protocol_version,
            name,
            ..
        } => {
            if protocol_version != protocol::PROTOCOL_VERSION {
                anyhow::bail!("wt Join protocol mismatch");
            }
            if name.is_empty() || name.len() > MAX_NAME_LEN {
                anyhow::bail!("wt Join invalid name");
            }
        }
        _ => anyhow::bail!("wt first frame not a Join"),
    }

    let mut frame = Vec::with_capacity(4 + len);
    frame.extend_from_slice(&len_buf);
    frame.extend_from_slice(&body);
    Ok((conn, wt_recv, wt_send, frame))
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
        rustls::crypto::ring::default_provider()
            .signature_verification_algorithms
            .supported_schemes()
    }
}
