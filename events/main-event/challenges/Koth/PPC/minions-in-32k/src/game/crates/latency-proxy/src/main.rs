//! UDP datagram proxy with synthetic delay / jitter / loss, for netcode
//! latency testing. QUIC rides on UDP, so shaping datagrams here simulates
//! a bad network end-to-end without root or tc:
//!
//!   cargo run -p latency-proxy -- --upstream 127.0.0.1:4433 \
//!       --listen 127.0.0.1:4434 --delay-ms 60 --jitter-ms 15 --loss 0.02
//!
//! `--delay-ms` is one-way (applied each direction => RTT += 2x). Jitter
//! is uniform in ±jitter_ms; with jitter > 0 packets can reorder, which
//! is exactly what real networks do to UDP.

use clap::Parser;
use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::net::UdpSocket;

#[derive(Parser)]
#[command(about = "UDP latency/jitter/loss proxy for netcode testing")]
struct Args {
    #[arg(long, default_value = "127.0.0.1:4434")]
    listen: SocketAddr,
    #[arg(long, default_value = "127.0.0.1:4433")]
    upstream: SocketAddr,
    /// One-way added delay in milliseconds.
    #[arg(long, default_value_t = 0)]
    delay_ms: u64,
    /// Uniform jitter in ±ms on top of the delay.
    #[arg(long, default_value_t = 0)]
    jitter_ms: u64,
    /// Packet loss probability per datagram (0.0 - 1.0), each direction.
    #[arg(long, default_value_t = 0.0)]
    loss: f64,
    #[arg(long, default_value_t = 0x1600)]
    seed: u64,
}

struct Shaper {
    delay_ms: u64,
    jitter_ms: u64,
    loss: f64,
    rng: AtomicU64,
    forwarded: AtomicU64,
    dropped: AtomicU64,
}

impl Shaper {
    fn next_rand(&self) -> u64 {
        // xorshift*; atomically advanced so all tasks share one stream
        let mut x = self.rng.load(Ordering::Relaxed);
        loop {
            let mut n = x;
            n ^= n >> 12;
            n ^= n << 25;
            n ^= n >> 27;
            match self
                .rng
                .compare_exchange_weak(x, n, Ordering::Relaxed, Ordering::Relaxed)
            {
                Ok(_) => return n.wrapping_mul(0x2545F4914F6CDD1D),
                Err(cur) => x = cur,
            }
        }
    }

    /// None = drop the packet; Some(d) = forward after d.
    fn plan(&self) -> Option<Duration> {
        if self.loss > 0.0 {
            let roll = self.next_rand() as f64 / u64::MAX as f64;
            if roll < self.loss {
                self.dropped.fetch_add(1, Ordering::Relaxed);
                return None;
            }
        }
        self.forwarded.fetch_add(1, Ordering::Relaxed);
        let jitter = if self.jitter_ms > 0 {
            (self.next_rand() % (2 * self.jitter_ms + 1)) as i64 - self.jitter_ms as i64
        } else {
            0
        };
        let ms = (self.delay_ms as i64 + jitter).max(0) as u64;
        Some(Duration::from_millis(ms))
    }
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    tracing_subscriber::fmt::init();
    let args = Args::parse();
    let shaper = Arc::new(Shaper {
        delay_ms: args.delay_ms,
        jitter_ms: args.jitter_ms,
        loss: args.loss,
        rng: AtomicU64::new(args.seed | 1),
        forwarded: AtomicU64::new(0),
        dropped: AtomicU64::new(0),
    });
    let listen = Arc::new(UdpSocket::bind(args.listen).await?);
    tracing::info!(
        listen = %args.listen, upstream = %args.upstream,
        delay_ms = args.delay_ms, jitter_ms = args.jitter_ms, loss = args.loss,
        "latency proxy up (one-way delay; RTT gains 2x)"
    );

    {
        let shaper = shaper.clone();
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(10)).await;
                tracing::info!(
                    forwarded = shaper.forwarded.load(Ordering::Relaxed),
                    dropped = shaper.dropped.load(Ordering::Relaxed),
                    "proxy stats"
                );
            }
        });
    }

    let mut clients: HashMap<SocketAddr, Arc<UdpSocket>> = HashMap::new();
    let mut buf = [0u8; 2048];
    loop {
        let (n, from) = listen.recv_from(&mut buf).await?;
        let up = match clients.get(&from) {
            Some(up) => up.clone(),
            None => {
                let up = Arc::new(UdpSocket::bind("0.0.0.0:0").await?);
                up.connect(args.upstream).await?;
                tracing::info!(client = %from, "new flow");
                clients.insert(from, up.clone());
                // return path: upstream -> shaped -> client
                let listen = listen.clone();
                let shaper = shaper.clone();
                let up_rx = up.clone();
                tokio::spawn(async move {
                    let mut buf = [0u8; 2048];
                    while let Ok(n) = up_rx.recv(&mut buf).await {
                        let Some(delay) = shaper.plan() else { continue };
                        let pkt = buf[..n].to_vec();
                        let listen = listen.clone();
                        tokio::spawn(async move {
                            tokio::time::sleep(delay).await;
                            let _ = listen.send_to(&pkt, from).await;
                        });
                    }
                });
                up
            }
        };
        let Some(delay) = shaper.plan() else { continue };
        let pkt = buf[..n].to_vec();
        tokio::spawn(async move {
            tokio::time::sleep(delay).await;
            let _ = up.send(&pkt).await;
        });
    }
}
