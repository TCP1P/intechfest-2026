//! Client-side QUIC connection bridged into Bevy via crossbeam channels.
//!
//! Cert handling mirrors the wasm `serverCertificateHashes` flow: by default
//! we pin the server's self-signed cert by SHA-256 of its DER encoding. The
//! expected hash comes from `MINIONS_CERTHASH` (64 hex chars) when present,
//! otherwise a dev fallback hash. Verification can only be disabled by an
//! explicit `MINIONS_INSECURE=1` opt-in.

use crate::ClientArgs;
use bevy::prelude::*;
use crossbeam_channel::{bounded, Receiver, Sender};
use protocol::{ClientMessage, ServerMessage, MAX_MSG_BYTES, PROTOCOL_VERSION};
use quinn::{ClientConfig, Endpoint, RecvStream, SendStream};
use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub use crate::channels::{NetIn, NetOut};

pub struct NetPlugin {
    pub args: ClientArgs,
}

/// One-shot handoff from the HTTPS lobby exchange to the QUIC transport.
/// The mutex is never held across I/O and is touched only twice per client:
/// once after authentication and once when the player picks a minion.
#[derive(Resource, Clone)]
pub struct SessionArgsSlot(Arc<Mutex<Option<ClientArgs>>>);

impl SessionArgsSlot {
    pub fn install(&self, args: ClientArgs) -> anyhow::Result<()> {
        let mut slot = self
            .0
            .lock()
            .map_err(|_| anyhow::anyhow!("arena session handoff was poisoned"))?;
        if slot.is_some() {
            anyhow::bail!("an arena session is already installed");
        }
        *slot = Some(args);
        Ok(())
    }
}

impl Plugin for NetPlugin {
    fn build(&self, app: &mut App) {
        let (in_tx, in_rx) = bounded::<ServerMessage>(256);
        let (out_tx, out_rx) = bounded::<ClientMessage>(64);
        let (lifecycle_tx, lifecycle_rx) = bounded::<crate::channels::NetLifecycleEvent>(16);
        let stale_in = in_rx.clone();
        app.insert_resource(NetIn { rx: in_rx });
        app.insert_resource(NetOut { tx: out_tx });
        app.insert_resource(crate::channels::NetLifecycle { rx: lifecycle_rx });

        let initial = self.args.remote.as_ref().map(|_| self.args.clone());
        let session_slot = SessionArgsSlot(Arc::new(Mutex::new(initial)));
        app.insert_resource(session_slot.clone());
        let active = Arc::new(AtomicBool::new(false));
        app.insert_resource(crate::channels::NetStarter::new(move |variant| {
            if active.swap(true, Ordering::AcqRel) {
                return Err("the previous arena connection is still closing".into());
            }
            let args = match session_slot.0.lock() {
                Ok(mut slot) => match slot.take() {
                    Some(args) => args,
                    None => {
                        active.store(false, Ordering::Release);
                        return Err("no authenticated arena session is ready".into());
                    }
                },
                Err(_) => {
                    active.store(false, Ordering::Release);
                    return Err("arena session handoff was poisoned".into());
                }
            };
            while stale_in.try_recv().is_ok() {}
            while out_rx.try_recv().is_ok() {}
            let thread_in = in_tx.clone();
            let thread_out = out_rx.clone();
            let thread_lifecycle = lifecycle_tx.clone();
            let thread_active = active.clone();
            lifecycle_tx
                .try_send(crate::channels::NetLifecycleEvent::Started)
                .ok();
            std::thread::Builder::new()
                .name("net".into())
                .spawn(move || {
                    let result = tokio::runtime::Builder::new_current_thread()
                        .enable_all()
                        .build()
                        .map_err(anyhow::Error::from)
                        .and_then(|runtime| {
                            runtime.block_on(run(args, variant, thread_in, thread_out))
                        });
                    let reason = result.err().map(|error| error.to_string());
                    if let Some(error) = reason.as_deref() {
                        tracing::error!(%error, "net thread exit");
                    }
                    thread_active.store(false, Ordering::Release);
                    let _ =
                        thread_lifecycle.send(crate::channels::NetLifecycleEvent::Ended { reason });
                })
                .map_err(|error| {
                    active.store(false, Ordering::Release);
                    format!("could not start network thread: {error}")
                })?;
            Ok(())
        }));
    }
}

async fn run(
    args: ClientArgs,
    variant: u8,
    in_tx: Sender<ServerMessage>,
    out_rx: Receiver<ClientMessage>,
) -> anyhow::Result<()> {
    rustls::crypto::ring::default_provider()
        .install_default()
        .ok();

    let remote = args.remote.as_deref().unwrap_or("127.0.0.1:4433");
    let router_addr: SocketAddr = tokio::net::lookup_host(remote)
        .await?
        .next()
        .ok_or_else(|| anyhow::anyhow!("no address resolved for {remote}"))?;

    let via_shim = args.instance_id.is_some();
    let connect_addr = match args.instance_id.as_deref() {
        Some(hex) => {
            let id = parse_instance_id(hex)?;
            let shim_addr = spawn_prefix_shim(id, router_addr).await?;
            tracing::info!(router = %router_addr, "udp-router prefix shim up");
            shim_addr
        }
        None => router_addr,
    };

    let mut endpoint = Endpoint::client("0.0.0.0:0".parse()?)?;
    endpoint.set_default_client_config(dev_client_config(via_shim, args.cert_hash.as_deref())?);

    tracing::info!(server = %router_addr, "connecting");
    let conn = endpoint.connect(connect_addr, "localhost")?.await?;
    tracing::info!("connected");

    let (mut send, mut recv) = conn.open_bi().await?;
    write_msg(
        &mut send,
        &ClientMessage::Join {
            protocol_version: PROTOCOL_VERSION,
            name: args.name.clone(),
            key: args.key.clone(),
            variant,
        },
    )
    .await?;

    let mut reader = {
        let in_tx = in_tx.clone();
        tokio::spawn(async move {
            loop {
                match read_msg::<ServerMessage>(&mut recv).await {
                    Ok(msg) => {
                        if in_tx.send(msg).is_err() {
                            return;
                        }
                    }
                    Err(e) => {
                        tracing::info!(error = %e, "reader exit");
                        return;
                    }
                }
            }
        })
    };

    // Snapshots arrive as unreliable datagrams (v13); the renderer drops
    // stale ticks itself, so we just decode and forward.
    let datagram_reader = {
        let in_tx = in_tx.clone();
        let conn = conn.clone();
        tokio::spawn(async move {
            loop {
                match conn.read_datagram().await {
                    Ok(bytes) => match protocol::decode::<ServerMessage>(&bytes) {
                        Ok(msg) => {
                            if in_tx.send(msg).is_err() {
                                return;
                            }
                        }
                        Err(e) => tracing::warn!(error = %e, "bad datagram"),
                    },
                    Err(e) => {
                        tracing::info!(error = %e, "datagram reader exit");
                        return;
                    }
                }
            }
        })
    };

    let mut writer = tokio::spawn(async move {
        loop {
            let msg = match out_rx.try_recv() {
                Ok(msg) => msg,
                Err(crossbeam_channel::TryRecvError::Empty) => {
                    tokio::time::sleep(Duration::from_millis(2)).await;
                    continue;
                }
                Err(crossbeam_channel::TryRecvError::Disconnected) => return,
            };
            if let Err(e) = write_msg(&mut send, &msg).await {
                tracing::info!(error = %e, "writer exit");
                return;
            }
        }
    });

    tokio::select! {
        _ = &mut reader => writer.abort(),
        _ = &mut writer => reader.abort(),
    }
    datagram_reader.abort();
    Ok(())
}

/// Dev fallback pin used when `MINIONS_CERTHASH` is unset. Matches the
/// self-signed cert the local instancer mints for development.
const DEV_CERTHASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

fn dev_client_config(via_shim: bool, cert_hash: Option<&str>) -> anyhow::Result<ClientConfig> {
    let insecure = matches!(
        std::env::var("MINIONS_INSECURE").ok().as_deref(),
        Some("1") | Some("true")
    );
    let verifier: Arc<dyn rustls::client::danger::ServerCertVerifier> = if insecure {
        tracing::warn!("MINIONS_INSECURE set: skipping server cert verification");
        Arc::new(NoVerify)
    } else if let Some(hex) = cert_hash {
        // Matchmade clients pin the backend-minted server cert: --cert-hash
        // wins over MINIONS_CERTHASH and the all-zeros dev fallback.
        Arc::new(PinVerify::from_hex(hex)?)
    } else {
        let hex = std::env::var("MINIONS_CERTHASH").unwrap_or_else(|_| DEV_CERTHASH.to_string());
        Arc::new(PinVerify::from_hex(&hex)?)
    };
    let mut crypto = rustls::ClientConfig::builder()
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_no_client_auth();
    crypto.alpn_protocols = vec![protocol::ALPN.to_vec()];
    let quic = Arc::new(quinn::crypto::rustls::QuicClientConfig::try_from(crypto)?);
    let mut cfg = ClientConfig::new(quic);
    if via_shim {
        let mut transport = quinn::TransportConfig::default();
        transport.initial_mtu(1200);
        transport.mtu_discovery_config(None);
        cfg.transport_config(Arc::new(transport));
    }
    Ok(cfg)
}

fn parse_instance_id(s: &str) -> anyhow::Result<[u8; 16]> {
    let s = s.trim();
    if s.len() != 32 {
        anyhow::bail!("--instance-id must be 32 hex chars (got {})", s.len());
    }
    let mut id = [0u8; 16];
    for (i, slot) in id.iter_mut().enumerate() {
        *slot = u8::from_str_radix(&s[i * 2..i * 2 + 2], 16)
            .map_err(|_| anyhow::anyhow!("--instance-id is not valid hex"))?;
    }
    Ok(id)
}

async fn spawn_prefix_shim(id: [u8; 16], router_addr: SocketAddr) -> anyhow::Result<SocketAddr> {
    use tokio::net::UdpSocket;

    let quic_facing = UdpSocket::bind("127.0.0.1:0").await?;
    let shim_addr = quic_facing.local_addr()?;
    let any = if router_addr.is_ipv6() {
        "[::]:0"
    } else {
        "0.0.0.0:0"
    };
    let router_facing = UdpSocket::bind(any).await?;
    router_facing.connect(router_addr).await?;

    tokio::spawn(async move {
        let mut from_quic = [0u8; 2048];
        let mut from_router = [0u8; 2048];
        let mut framed = [0u8; 16 + 2048];
        framed[..16].copy_from_slice(&id);
        let mut quic_peer: Option<SocketAddr> = None;
        loop {
            tokio::select! {
                r = quic_facing.recv_from(&mut from_quic) => {
                    let Ok((n, peer)) = r else { continue };
                    quic_peer = Some(peer);
                    framed[16..16 + n].copy_from_slice(&from_quic[..n]);
                    let _ = router_facing.send(&framed[..16 + n]).await;
                }
                r = router_facing.recv(&mut from_router) => {
                    let Ok(n) = r else { continue };
                    if let Some(peer) = quic_peer {
                        let _ = quic_facing.send_to(&from_router[..n], peer).await;
                    }
                }
            }
        }
    });

    Ok(shim_addr)
}

/// Pins the server's self-signed cert by SHA-256 of its DER encoding —
/// the native analogue of WebTransport `serverCertificateHashes`.
#[derive(Debug)]
struct PinVerify {
    expected: [u8; 32],
}

impl PinVerify {
    fn from_hex(s: &str) -> anyhow::Result<Self> {
        let s = s.trim();
        if s.len() != 64 || !s.bytes().all(|b| b.is_ascii_hexdigit()) {
            anyhow::bail!("MINIONS_CERTHASH must be 64 hex chars (got {})", s.len());
        }
        let mut expected = [0u8; 32];
        for (i, slot) in expected.iter_mut().enumerate() {
            *slot = u8::from_str_radix(&s[i * 2..i * 2 + 2], 16)
                .map_err(|_| anyhow::anyhow!("MINIONS_CERTHASH is not valid hex"))?;
        }
        Ok(Self { expected })
    }
}

impl rustls::client::danger::ServerCertVerifier for PinVerify {
    fn verify_server_cert(
        &self,
        end_entity: &rustls::pki_types::CertificateDer<'_>,
        _intermediates: &[rustls::pki_types::CertificateDer<'_>],
        _server_name: &rustls::pki_types::ServerName<'_>,
        _ocsp_response: &[u8],
        _now: rustls::pki_types::UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        let got = sha256(end_entity.as_ref());
        if got == self.expected {
            Ok(rustls::client::danger::ServerCertVerified::assertion())
        } else {
            Err(rustls::Error::General("server cert hash mismatch".into()))
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
        NoVerify.supported_verify_schemes()
    }
}

/// Self-contained SHA-256 (no extra deps) for cert-hash pinning.
fn sha256(data: &[u8]) -> [u8; 32] {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let bit_len = (data.len() as u64).wrapping_mul(8);
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());

    for block in msg.chunks_exact(64) {
        let mut w = [0u32; 64];
        for (i, word) in w.iter_mut().take(16).enumerate() {
            *word = u32::from_be_bytes([
                block[i * 4],
                block[i * 4 + 1],
                block[i * 4 + 2],
                block[i * 4 + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let mut v = h;
        for i in 0..64 {
            let s1 = v[4].rotate_right(6) ^ v[4].rotate_right(11) ^ v[4].rotate_right(25);
            let ch = (v[4] & v[5]) ^ ((!v[4]) & v[6]);
            let t1 = v[7]
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = v[0].rotate_right(2) ^ v[0].rotate_right(13) ^ v[0].rotate_right(22);
            let maj = (v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]);
            let t2 = s0.wrapping_add(maj);
            v[7] = v[6];
            v[6] = v[5];
            v[5] = v[4];
            v[4] = v[3].wrapping_add(t1);
            v[3] = v[2];
            v[2] = v[1];
            v[1] = v[0];
            v[0] = t1.wrapping_add(t2);
        }
        for (hi, vi) in h.iter_mut().zip(v.iter()) {
            *hi = hi.wrapping_add(*vi);
        }
    }
    let mut out = [0u8; 32];
    for (i, word) in h.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    out
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
}

async fn read_msg<T: for<'de> serde::Deserialize<'de>>(recv: &mut RecvStream) -> anyhow::Result<T> {
    let mut len_buf = [0u8; 4];
    recv.read_exact(&mut len_buf).await?;
    let len = u32::from_le_bytes(len_buf) as usize;
    if len > MAX_MSG_BYTES {
        anyhow::bail!("oversized message ({len} bytes)");
    }
    let mut buf = vec![0u8; len];
    recv.read_exact(&mut buf).await?;
    Ok(protocol::decode(&buf)?)
}

async fn write_msg<T: serde::Serialize>(send: &mut SendStream, msg: &T) -> anyhow::Result<()> {
    let bytes = protocol::encode(msg)?;
    send.write_all(&(bytes.len() as u32).to_le_bytes()).await?;
    send.write_all(&bytes).await?;
    Ok(())
}
