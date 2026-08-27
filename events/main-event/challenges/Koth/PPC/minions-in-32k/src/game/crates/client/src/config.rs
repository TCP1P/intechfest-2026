//! Client-side configuration loaded from a sibling `config.toml`.
//!
//! We do **not** ship this file. The default internal render resolution is
//! 256 × 128 (the canonical "32K pixels"). If a competitor discovers that
//! the binary reads a `config.toml` and supplies one, they unlock higher
//! resolutions — which is a legitimate vision advantage in a 3D game. This
//! is intentional and forms one rung of the CTF's cheat-progression ladder.
//!
//! Do not advertise this mechanism in public-facing docs.

use bevy::prelude::Resource;
use serde::Deserialize;

#[derive(Debug, Clone, Copy, Resource)]
pub struct RenderConfig {
    pub width: u32,
    pub height: u32,
}

impl Default for RenderConfig {
    fn default() -> Self {
        Self {
            width: common::DEFAULT_RENDER_WIDTH,
            height: common::DEFAULT_RENDER_HEIGHT,
        }
    }
}

#[derive(Debug, Default, Deserialize)]
#[serde(default)]
struct FileConfig {
    render: Option<FileRender>,
}

#[derive(Debug, Deserialize)]
struct FileRender {
    width: Option<u32>,
    height: Option<u32>,
}

impl RenderConfig {
    pub fn load() -> Self {
        for path in candidate_paths() {
            let Ok(text) = std::fs::read_to_string(&path) else {
                continue;
            };
            match toml::from_str::<FileConfig>(&text) {
                Ok(cfg) => {
                    let mut out = RenderConfig::default();
                    if let Some(r) = cfg.render {
                        if let Some(w) = r.width {
                            out.width = sanitize_dim(w);
                        }
                        if let Some(h) = r.height {
                            out.height = sanitize_dim(h);
                        }
                    }
                    tracing::info!(?path, "loaded config");
                    return out;
                }
                Err(e) => {
                    tracing::warn!(?path, error = %e, "config parse failed, using defaults");
                    return RenderConfig::default();
                }
            }
        }
        RenderConfig::default()
    }
}

fn candidate_paths() -> Vec<std::path::PathBuf> {
    let mut v = Vec::with_capacity(2);
    v.push(std::path::PathBuf::from("config.toml"));
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            v.push(dir.join("config.toml"));
        }
    }
    v
}

fn sanitize_dim(x: u32) -> u32 {
    x.clamp(16, 8192)
}
