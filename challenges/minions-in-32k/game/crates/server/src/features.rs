use serde::Deserialize;
use std::collections::HashMap;
use std::path::PathBuf;
use std::time::SystemTime;

#[derive(Debug)]
pub struct FeatureStore {
    path: PathBuf,
    last_modified: Option<SystemTime>,
    values: HashMap<String, bool>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(default)]
struct FeatureFile {
    features: FeatureTable,
}

#[derive(Debug, Default, Deserialize)]
#[serde(default)]
struct FeatureTable {
    powerups: Option<bool>,
    kitchen_lava: Option<bool>,
}

impl FeatureStore {
    pub fn new(path: PathBuf) -> Self {
        let values = HashMap::from([
            ("powerups".to_string(), true),
            ("kitchen_lava".to_string(), true),
        ]);
        Self {
            path,
            last_modified: None,
            values,
        }
    }

    pub async fn poll(&mut self) -> Vec<(String, bool)> {
        let Ok(meta) = tokio::fs::metadata(&self.path).await else {
            return Vec::new();
        };
        let modified = meta.modified().ok();
        if modified.is_some() && modified == self.last_modified {
            return Vec::new();
        }
        let Ok(raw) = tokio::fs::read_to_string(&self.path).await else {
            return Vec::new();
        };
        let parsed = match toml::from_str::<FeatureFile>(&raw) {
            Ok(parsed) => parsed,
            Err(e) => {
                tracing::warn!(path = ?self.path, error = %e, "feature flag parse failed");
                self.last_modified = modified;
                return Vec::new();
            }
        };
        self.last_modified = modified;

        let mut next = HashMap::new();
        if let Some(value) = parsed.features.powerups {
            next.insert("powerups".to_string(), value);
        }
        if let Some(value) = parsed.features.kitchen_lava {
            next.insert("kitchen_lava".to_string(), value);
        }

        let mut flips = Vec::new();
        for (key, value) in next {
            if self.values.get(&key).copied() != Some(value) {
                self.values.insert(key.clone(), value);
                flips.push((key, value));
            }
        }
        flips
    }
}
