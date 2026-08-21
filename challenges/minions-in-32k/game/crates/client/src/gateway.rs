//! RSCTF capability exchange for the native event client.
//!
//! The stable KotH capability is sent only to the HTTPS gateway. The arena
//! receives a separate per-team key, so it never handles or stores the RSCTF
//! credential.

use crate::ClientArgs;
use anyhow::{bail, Context, Result};
use reqwest::blocking::Client;
use serde::Deserialize;
use std::{
    fmt,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use zeroize::Zeroize;

const MAX_REPLY_BYTES: usize = 8 * 1024;
const MAX_RETRY_SECONDS: u64 = 2 * 60 * 60;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct ArenaSession {
    protocol: String,
    remote: String,
    name: String,
    key: String,
    cert_hash: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct GatewayProblem {
    title: Option<String>,
    status: Option<u16>,
    code: Option<String>,
    retry_at_unix_ms: Option<i64>,
}

#[derive(Debug)]
pub(crate) struct ArenaWindowPending {
    retry_at_unix_ms: i64,
}

impl fmt::Display for ArenaWindowPending {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Crown reset protection is preparing the next safe arena")
    }
}

impl std::error::Error for ArenaWindowPending {}

#[derive(Debug)]
struct CapabilityRejected;

impl fmt::Display for CapabilityRejected {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("the KotH token is invalid, expired, or belongs to another event")
    }
}

impl std::error::Error for CapabilityRejected {}

pub(crate) fn retry_at_unix_ms(error: &anyhow::Error) -> Option<i64> {
    error
        .downcast_ref::<ArenaWindowPending>()
        .map(|pending| pending.retry_at_unix_ms)
}

pub(crate) fn token_was_rejected(error: &anyhow::Error) -> bool {
    error.downcast_ref::<CapabilityRejected>().is_some()
}

pub fn resolve(args: &mut ClientArgs) -> Result<()> {
    let Some(mut token) = args.token.take() else {
        return Ok(());
    };
    let result = resolve_capability(args, &token);
    token.zeroize();
    result
}

pub(crate) fn resolve_capability(args: &mut ClientArgs, token: &str) -> Result<()> {
    let session = exchange(args, token)?;
    args.remote = Some(session.remote);
    args.name = session.name;
    args.key = Some(session.key);
    args.cert_hash = Some(session.cert_hash);
    Ok(())
}

fn exchange(args: &ClientArgs, token: &str) -> Result<ArenaSession> {
    validate_capability(token)?;
    let endpoint = session_endpoint(&args.gateway)?;
    let client = Client::builder()
        .connect_timeout(Duration::from_secs(8))
        .timeout(Duration::from_secs(15))
        .user_agent(concat!("minions-in-32k/", env!("CARGO_PKG_VERSION")))
        .build()
        .context("could not initialize the secure event connection")?;
    let response = client
        .post(endpoint)
        .bearer_auth(token)
        .header(reqwest::header::ACCEPT, "application/json")
        .send()
        .context("the event gateway is unreachable")?;
    let status = response.status();
    let retry_after = response
        .headers()
        .get(reqwest::header::RETRY_AFTER)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    if response
        .content_length()
        .is_some_and(|length| length > MAX_REPLY_BYTES as u64)
    {
        bail!("the event gateway returned an oversized response");
    }
    let body = response
        .bytes()
        .context("could not read the event gateway response")?;
    if body.len() > MAX_REPLY_BYTES {
        bail!("the event gateway returned an oversized response");
    }
    if !status.is_success() {
        return Err(gateway_failure(
            status,
            retry_after.as_deref(),
            &body,
            unix_time_millis(),
        ));
    }
    let session: ArenaSession =
        serde_json::from_slice(&body).context("the event gateway returned invalid metadata")?;
    validate_session(&session)?;
    Ok(session)
}

fn gateway_failure(
    status: reqwest::StatusCode,
    retry_after: Option<&str>,
    body: &[u8],
    now_unix_ms: i64,
) -> anyhow::Error {
    if status == reqwest::StatusCode::UNAUTHORIZED || status == reqwest::StatusCode::FORBIDDEN {
        return CapabilityRejected.into();
    }

    let problem = serde_json::from_slice::<GatewayProblem>(body).ok();
    let valid_problem = problem
        .as_ref()
        .filter(|problem| problem.status == Some(status.as_u16()));
    let structured_retry = valid_problem
        .filter(|problem| problem.code.as_deref() == Some("arena_window_pending"))
        .and_then(|problem| problem.retry_at_unix_ms)
        .filter(|retry_at| {
            *retry_at > now_unix_ms && *retry_at <= now_unix_ms + MAX_RETRY_SECONDS as i64 * 1_000
        });
    let legacy_retry = (status == reqwest::StatusCode::CONFLICT)
        .then(|| retry_after?.parse::<u64>().ok())
        .flatten()
        .filter(|seconds| (1..=MAX_RETRY_SECONDS).contains(seconds))
        .map(|seconds| now_unix_ms + seconds as i64 * 1_000);

    if let Some(retry_at_unix_ms) = structured_retry.or(legacy_retry) {
        return ArenaWindowPending { retry_at_unix_ms }.into();
    }

    if let Some(title) = valid_problem
        .and_then(|problem| problem.title.as_deref())
        .filter(|title| {
            !title.is_empty() && title.len() <= 160 && !title.chars().any(char::is_control)
        })
    {
        return anyhow::anyhow!("{title}");
    }
    anyhow::anyhow!("the event gateway returned HTTP {}", status.as_u16())
}

fn unix_time_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .min(i64::MAX as u128) as i64
}

pub(crate) fn validate_capability(token: &str) -> Result<()> {
    if !(13..=133).contains(&token.len())
        || !token.starts_with("koth_")
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        bail!("the KotH token has an invalid format");
    }
    Ok(())
}

fn session_endpoint(gateway: &str) -> Result<reqwest::Url> {
    let mut url = reqwest::Url::parse(gateway).context("--gateway is not a valid URL")?;
    let local_http =
        url.scheme() == "http" && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"));
    if url.scheme() != "https" && !local_http {
        bail!("--gateway must use HTTPS");
    }
    if url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !matches!(url.path(), "" | "/")
    {
        bail!("--gateway must be an origin without credentials, path, query, or fragment");
    }
    url.set_path("/api/v1/arena-session");
    Ok(url)
}

fn validate_session(session: &ArenaSession) -> Result<()> {
    if session.protocol != "minions/0" {
        bail!("the event gateway selected an incompatible arena protocol");
    }
    let remote = reqwest::Url::parse(&format!("udp://{}/", session.remote))
        .context("the event gateway returned an invalid arena address")?;
    if remote.host_str().is_none()
        || remote.port().is_none()
        || !remote.username().is_empty()
        || remote.password().is_some()
        || remote.query().is_some()
        || remote.fragment().is_some()
    {
        bail!("the event gateway returned an invalid arena address");
    }
    if session.name.is_empty()
        || session.name.len() > 64
        || session.name.chars().any(char::is_control)
        || !(32..=256).contains(&session.key.len())
        || session.key.chars().any(char::is_control)
        || session.cert_hash.len() != 64
        || !session
            .cert_hash
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        bail!("the event gateway returned invalid arena credentials");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn valid_session() -> ArenaSession {
        ArenaSession {
            protocol: "minions/0".into(),
            remote: "tcp.1pc.tf:4532".into(),
            name: "team alpha".into(),
            key: "a".repeat(48),
            cert_hash: "0123456789abcdef".repeat(4),
        }
    }

    #[test]
    fn accepts_canonical_session() {
        validate_session(&valid_session()).unwrap();
    }

    #[test]
    fn rejects_unpinned_or_malformed_session() {
        let mut session = valid_session();
        session.cert_hash.clear();
        assert!(validate_session(&session).is_err());

        let mut session = valid_session();
        session.remote = "tcp.1pc.tf".into();
        assert!(validate_session(&session).is_err());

        let mut session = valid_session();
        session.protocol = "M32/3".into();
        assert!(validate_session(&session).is_err());
    }

    #[test]
    fn gateway_rejects_non_tls_and_paths() {
        assert!(session_endpoint("http://example.com").is_err());
        assert!(session_endpoint("https://example.com/not-an-origin").is_err());
        assert_eq!(
            session_endpoint("https://example.com").unwrap().as_str(),
            "https://example.com/api/v1/arena-session"
        );
        assert!(session_endpoint("http://127.0.0.1:8080").is_ok());
    }

    #[test]
    fn capability_format_is_narrow() {
        assert!(validate_capability("koth_abcdefgh-123456").is_ok());
        assert!(validate_capability("koth_contains/slash").is_err());
        assert!(validate_capability("not_koth_abcdefgh").is_err());
    }

    #[test]
    fn structured_crown_reset_exposes_exact_retry_time() {
        let now = 1_000_000_i64;
        let retry_at = now + 92_000;
        let body = format!(
            r#"{{"title":"Crown reset protection is active","detail":"wait","status":409,"code":"arena_window_pending","retryAtUnixMs":{retry_at}}}"#
        );
        let error = gateway_failure(
            reqwest::StatusCode::CONFLICT,
            Some("1"),
            body.as_bytes(),
            now,
        );
        assert_eq!(retry_at_unix_ms(&error), Some(retry_at));
        assert!(!error.to_string().contains("409"));
    }

    #[test]
    fn legacy_retry_after_remains_compatible() {
        let now = 1_000_000_i64;
        let error = gateway_failure(
            reqwest::StatusCode::CONFLICT,
            Some("30"),
            br#"{"title":"arena reset is imminent; retry after the next crown reset","status":409}"#,
            now,
        );
        assert_eq!(retry_at_unix_ms(&error), Some(now + 30_000));
    }

    #[test]
    fn arbitrary_or_unbounded_retry_metadata_is_not_accepted() {
        let now = 1_000_000_i64;
        let body = format!(
            r#"{{"title":"Try later","status":409,"code":"arena_window_pending","retryAtUnixMs":{}}}"#,
            now + (MAX_RETRY_SECONDS as i64 + 1) * 1_000
        );
        let error = gateway_failure(
            reqwest::StatusCode::CONFLICT,
            Some("999999"),
            body.as_bytes(),
            now,
        );
        assert_eq!(retry_at_unix_ms(&error), None);
        assert_eq!(error.to_string(), "Try later");
    }

    #[test]
    fn rejected_capability_is_distinct_from_a_transient_failure() {
        let error = gateway_failure(reqwest::StatusCode::UNAUTHORIZED, None, b"{}", 0);
        assert!(token_was_rejected(&error));
        assert_eq!(retry_at_unix_ms(&error), None);
    }
}
