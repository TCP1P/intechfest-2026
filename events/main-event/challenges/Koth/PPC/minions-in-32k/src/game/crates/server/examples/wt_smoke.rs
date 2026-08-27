//! Smoke: WebTransport client -> bridge -> QUIC game server -> Welcome.
//! Run a server first (default ports), then: cargo run -p server --example wt_smoke

use wtransport::{ClientConfig, Endpoint};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let config = ClientConfig::builder()
        .with_bind_default()
        .with_no_cert_validation()
        .build();
    let conn = Endpoint::client(config)?
        .connect("https://127.0.0.1:4434")
        .await?;
    let opening = conn.open_bi().await?;
    let (mut send, mut recv) = opening.await?;

    let join = protocol::encode(&protocol::ClientMessage::Join {
        protocol_version: protocol::PROTOCOL_VERSION,
        name: "wasm_smoke".into(),
        key: std::env::var("KEY").ok(),
        variant: 0,
    })?;
    send.write_all(&(join.len() as u32).to_le_bytes()).await?;
    send.write_all(&join).await?;

    let mut len = [0u8; 4];
    recv.read_exact(&mut len).await?;
    let mut buf = vec![0u8; u32::from_le_bytes(len) as usize];
    recv.read_exact(&mut buf).await?;
    let msg: protocol::ServerMessage = protocol::decode(&buf)?;
    match msg {
        protocol::ServerMessage::Welcome {
            your_id,
            tick_rate_hz,
        } => println!("WT_SMOKE_OK id={your_id} hz={tick_rate_hz}"),
        other => anyhow::bail!("expected Welcome, got {other:?}"),
    }
    Ok(())
}
