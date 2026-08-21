//! Dev-mode self-signed certificate. Production will plumb real certs via
//! config in `deploy/`; this exists so the walking skeleton runs without
//! any out-of-band setup.

use anyhow::{Context, Result};
use base64::Engine;
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};

pub fn self_signed() -> Result<(Vec<CertificateDer<'static>>, PrivateKeyDer<'static>)> {
    let cert = rcgen::generate_simple_self_signed(vec!["localhost".into()])
        .context("rcgen self-signed")?;
    let cert_der = CertificateDer::from(cert.cert.der().to_vec());
    let key_der = PrivateKeyDer::from(PrivatePkcs8KeyDer::from(cert.key_pair.serialize_der()));
    Ok((vec![cert_der], key_der))
}

/// Reuse the backend-minted cert when `WT_CERT_DER_B64`/`WT_KEY_DER_B64` are
/// present (matchmade instances) so the QUIC-presented cert hash equals the
/// `cert_hash_hex` the backend pins on clients; otherwise fall back to a fresh
/// self-signed cert (dev).
pub fn from_env_or_self_signed() -> Result<(Vec<CertificateDer<'static>>, PrivateKeyDer<'static>)> {
    let (Ok(cert_b64), Ok(key_b64)) = (
        std::env::var("WT_CERT_DER_B64"),
        std::env::var("WT_KEY_DER_B64"),
    ) else {
        return self_signed();
    };
    let b64 = base64::engine::general_purpose::STANDARD;
    let cert_der = b64
        .decode(cert_b64.trim())
        .context("base64-decode WT_CERT_DER_B64")?;
    let key_der = b64
        .decode(key_b64.trim())
        .context("base64-decode WT_KEY_DER_B64")?;
    let cert = CertificateDer::from(cert_der);
    let key = PrivateKeyDer::try_from(key_der)
        .map_err(|e| anyhow::anyhow!("WT_KEY_DER_B64 is not a valid private key: {e}"))?;
    Ok((vec![cert], key))
}
