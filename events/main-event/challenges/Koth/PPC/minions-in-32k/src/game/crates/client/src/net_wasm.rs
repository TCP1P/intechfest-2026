//! Browser net backend: WebTransport → the server's WT bridge.
//!
//! Feeds the same `NetIn`/`NetOut` channels as the native QUIC path and
//! the demo sim, so the renderer doesn't know it's in a browser. Wire
//! format is identical (length-prefixed bincode over one bidi stream).
//!
//! Connection parameters come from the page URL:
//! `/wasm?host=H&port=P&key=K&certhash=<sha256hex>` — the cert hash pins
//! the instance's self-signed cert via serverCertificateHashes.

use crate::channels::{NetIn, NetLifecycle, NetLifecycleEvent, NetOut};
use crate::ClientArgs;
use bevy::prelude::*;
use crossbeam_channel::{bounded, Receiver, Sender};
use js_sys::{Array, Object, Promise, Reflect, Uint8Array};
use protocol::{ClientMessage, ServerMessage, MAX_MSG_BYTES, PROTOCOL_VERSION};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use wasm_bindgen::prelude::*;
use wasm_bindgen::JsCast;
use wasm_bindgen_futures::JsFuture;

pub struct WasmNetPlugin {
    pub args: ClientArgs,
}

impl Plugin for WasmNetPlugin {
    fn build(&self, app: &mut App) {
        let (in_tx, in_rx) = bounded::<ServerMessage>(256);
        let (out_tx, out_rx) = bounded::<ClientMessage>(64);
        let (lifecycle_tx, lifecycle_rx) = bounded::<NetLifecycleEvent>(16);
        app.insert_resource(NetIn { rx: in_rx });
        app.insert_resource(NetOut { tx: out_tx });
        app.insert_resource(NetLifecycle { rx: lifecycle_rx });
        let args = self.args.clone();
        let active = Arc::new(AtomicBool::new(false));
        app.insert_resource(crate::channels::NetStarter::new(move |variant| {
            if active.swap(true, Ordering::AcqRel) {
                return Err("the previous arena connection is still closing".into());
            }
            let args = args.clone();
            let in_tx = in_tx.clone();
            let out_rx = out_rx.clone();
            let lifecycle_tx = lifecycle_tx.clone();
            let active = active.clone();
            let _ = lifecycle_tx.try_send(NetLifecycleEvent::Started);
            wasm_bindgen_futures::spawn_local(async move {
                let reason = match run(args, variant, in_tx, out_rx).await {
                    Ok(()) => None,
                    Err(error) => {
                        web_sys::console::error_1(&error);
                        Some(
                            error
                                .as_string()
                                .unwrap_or_else(|| "browser transport failed".into()),
                        )
                    }
                };
                active.store(false, Ordering::Release);
                let _ = lifecycle_tx.send(NetLifecycleEvent::Ended { reason });
            });
            Ok(())
        }));
    }
}

pub fn query(name: &str) -> Option<String> {
    let search = web_sys::window()?.location().search().ok()?;
    let params = web_sys::UrlSearchParams::new_with_str(&search).ok()?;
    params.get(name)
}

/// Read `name` from the URL fragment (`#a=1&b=2`). Secrets (the per-match
/// access key) ride here so they never hit the query string / referer / logs.
fn fragment(name: &str) -> Option<String> {
    let hash = web_sys::window()?.location().hash().ok()?;
    let hash = hash.strip_prefix('#').unwrap_or(&hash);
    let params = web_sys::UrlSearchParams::new_with_str(hash).ok()?;
    params.get(name)
}

/// Strict 32-byte SHA-256 decode: exactly 64 hex chars, hex-only.
fn cert_hash_bytes(s: &str) -> Result<[u8; 32], JsValue> {
    let s = s.trim();
    if s.len() != 64 || !s.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(JsValue::from_str("certhash must be 64 hex chars"));
    }
    let mut out = [0u8; 32];
    for (i, slot) in out.iter_mut().enumerate() {
        *slot = u8::from_str_radix(&s[i * 2..i * 2 + 2], 16)
            .map_err(|_| JsValue::from_str("certhash must be hex"))?;
    }
    Ok(out)
}

async fn sleep_ms(ms: i32) {
    let promise = Promise::new(&mut |resolve, _| {
        web_sys::window()
            .unwrap()
            .set_timeout_with_callback_and_timeout_and_arguments_0(&resolve, ms)
            .unwrap();
    });
    let _ = JsFuture::from(promise).await;
}

/// Per-match access key: fragment first (`#key=…`, kept out of query/referer/
/// logs), falling back to `?key=` and finally whatever main.rs parsed.
fn access_key(args: &ClientArgs) -> Option<String> {
    fragment("key")
        .or_else(|| query("key"))
        .or_else(|| args.key.clone())
}

async fn run(
    args: ClientArgs,
    variant: u8,
    in_tx: Sender<ServerMessage>,
    out_rx: Receiver<ClientMessage>,
) -> Result<(), JsValue> {
    let host = query("host").ok_or("missing ?host")?;
    let port = query("port").ok_or("missing ?port")?;
    let key = access_key(&args);
    let url = format!("https://{host}:{port}/");

    let hash = query("certhash").ok_or("missing ?certhash (cert pinning is mandatory)")?;
    let bytes = cert_hash_bytes(&hash)?;
    let entry = Object::new();
    Reflect::set(&entry, &"algorithm".into(), &"sha-256".into())?;
    Reflect::set(
        &entry,
        &"value".into(),
        &Uint8Array::from(&bytes[..]).into(),
    )?;
    let opts = Object::new();
    Reflect::set(
        &opts,
        &"serverCertificateHashes".into(),
        &Array::of1(&entry),
    )?;
    let wt = web_sys::WebTransport::new_with_options(&url, opts.unchecked_ref())?;
    JsFuture::from(wt.ready()).await?;

    let bidi = JsFuture::from(wt.create_bidirectional_stream()).await?;
    let writable: web_sys::WritableStream =
        Reflect::get(&bidi, &"writable".into())?.unchecked_into();
    let readable: web_sys::ReadableStream =
        Reflect::get(&bidi, &"readable".into())?.unchecked_into();
    let writer = writable.get_writer()?;
    let reader: web_sys::ReadableStreamDefaultReader = readable.get_reader().unchecked_into();

    send_msg(
        &writer,
        &ClientMessage::Join {
            protocol_version: PROTOCOL_VERSION,
            name: args.name.clone(),
            key: key.clone(),
            variant,
        },
    )
    .await?;

    // Datagram pump (v11): each datagram is one raw-encoded ServerMessage
    // (snapshots — unreliable by design, stale ticks dropped by the
    // renderer).
    {
        let in_tx = in_tx.clone();
        let datagrams: web_sys::WebTransportDatagramDuplexStream = wt.datagrams();
        let dg_readable: web_sys::ReadableStream = datagrams.readable();
        let dg_reader: web_sys::ReadableStreamDefaultReader =
            dg_readable.get_reader().unchecked_into();
        wasm_bindgen_futures::spawn_local(async move {
            loop {
                let result = match JsFuture::from(dg_reader.read()).await {
                    Ok(r) => r,
                    Err(_) => break,
                };
                let done = Reflect::get(&result, &"done".into())
                    .ok()
                    .and_then(|d| d.as_bool())
                    .unwrap_or(true);
                if done {
                    break;
                }
                if let Ok(value) = Reflect::get(&result, &"value".into()) {
                    let chunk: Uint8Array = value.unchecked_into();
                    let bytes = chunk.to_vec();
                    if let Ok(msg) = protocol::decode::<ServerMessage>(&bytes) {
                        let _ = in_tx.try_send(msg);
                    }
                }
            }
        });
    }

    // Inbound pump: chunks -> length-prefixed frames -> ServerMessage.
    wasm_bindgen_futures::spawn_local(async move {
        let mut buf: Vec<u8> = Vec::with_capacity(64 * 1024);
        loop {
            let result = match JsFuture::from(reader.read()).await {
                Ok(r) => r,
                Err(_) => break,
            };
            let done = Reflect::get(&result, &"done".into())
                .ok()
                .and_then(|d| d.as_bool())
                .unwrap_or(true);
            if done {
                break;
            }
            if let Ok(value) = Reflect::get(&result, &"value".into()) {
                let chunk: Uint8Array = value.unchecked_into();
                let start = buf.len();
                buf.resize(start + chunk.length() as usize, 0);
                chunk.copy_to(&mut buf[start..]);
            }
            loop {
                if buf.len() < 4 {
                    break;
                }
                let len = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]) as usize;
                if len > MAX_MSG_BYTES {
                    web_sys::console::error_1(&"oversized server frame".into());
                    return;
                }
                if buf.len() < 4 + len {
                    break;
                }
                if let Ok(msg) = protocol::decode::<ServerMessage>(&buf[4..4 + len]) {
                    let _ = in_tx.try_send(msg);
                }
                buf.drain(..4 + len);
            }
        }
        web_sys::console::warn_1(&"server stream closed".into());
    });

    // Outbound pump: NetOut -> stream, polled just above tick rate.
    loop {
        while let Ok(msg) = out_rx.try_recv() {
            send_msg(&writer, &msg).await?;
        }
        sleep_ms(8).await;
    }
}

async fn send_msg<T: serde::Serialize>(
    writer: &web_sys::WritableStreamDefaultWriter,
    msg: &T,
) -> Result<(), JsValue> {
    let body = protocol::encode(msg).map_err(|e| JsValue::from_str(&e.to_string()))?;
    let mut framed = Vec::with_capacity(4 + body.len());
    framed.extend_from_slice(&(body.len() as u32).to_le_bytes());
    framed.extend_from_slice(&body);
    JsFuture::from(writer.write_with_chunk(&Uint8Array::from(&framed[..]).into())).await?;
    Ok(())
}
