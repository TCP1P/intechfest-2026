//! Per-zone JSON loader. Each file under `scenes_dir/<zone>.json` holds the
//! walls, jump pads, and spawns that live in that zone. `Arena::load_dir`
//! stitches them together into the same `Arena` shape `gru_lab()` produces.
//!
//! This exists so the editor's `/save` can write zone files back without a
//! recompile, and the client/server pick up edits on restart.

use crate::arena::{Aabb, Arena, Elevator, JumpPad, PowerupSpawn, Zone};
use glam::Vec3;
use protocol::PowerupKind;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io;
use std::path::Path;

#[derive(Serialize, Deserialize)]
pub struct ZoneFile {
    pub name: String,
    pub bounds: AabbJson,
    #[serde(default)]
    pub walls: Vec<AabbJson>,
    #[serde(default)]
    pub jump_pads: Vec<JumpPadJson>,
    #[serde(default)]
    pub powerups: Vec<PowerupJson>,
    #[serde(default)]
    pub spawn_points: Vec<[f32; 3]>,
    #[serde(default)]
    pub elevators: Vec<ElevatorJson>,
    #[serde(default)]
    pub props: Vec<PropJson>,
}

#[derive(Serialize, Deserialize, Clone, Copy)]
pub struct ElevatorJson {
    pub center: [f32; 2],
    pub half: [f32; 2],
    pub thickness: f32,
    pub y_bottom: f32,
    pub y_top: f32,
    pub speed: f32,
    pub dwell_s: f32,
}

impl From<ElevatorJson> for Elevator {
    fn from(j: ElevatorJson) -> Self {
        Elevator {
            center_x: j.center[0],
            center_z: j.center[1],
            half_x: j.half[0],
            half_z: j.half[1],
            thickness: j.thickness,
            y_bottom: j.y_bottom,
            y_top: j.y_top,
            speed: j.speed,
            dwell_s: j.dwell_s,
        }
    }
}

impl From<Elevator> for ElevatorJson {
    fn from(e: Elevator) -> Self {
        ElevatorJson {
            center: [e.center_x, e.center_z],
            half: [e.half_x, e.half_z],
            thickness: e.thickness,
            y_bottom: e.y_bottom,
            y_top: e.y_top,
            speed: e.speed,
            dwell_s: e.dwell_s,
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Copy)]
pub struct AabbJson {
    pub min: [f32; 3],
    pub max: [f32; 3],
}

#[derive(Serialize, Deserialize, Clone, Copy)]
pub struct JumpPadJson {
    pub center: [f32; 3],
    pub radius: f32,
    pub velocity: [f32; 3],
}

#[derive(Serialize, Deserialize, Clone, Copy)]
pub struct PowerupJson {
    pub kind: PowerupKind,
    pub center: [f32; 3],
    pub radius: f32,
}

/// A placed .glb (or other) asset reference. The sim ignores these; the
/// client looks them up under `assets/props/<path>` when rendering.
#[derive(Serialize, Deserialize, Clone)]
pub struct PropJson {
    pub path: String,
    pub pos: [f32; 3],
    #[serde(default = "one")]
    pub scale: f32,
    #[serde(default)]
    pub yaw: f32,
}

fn one() -> f32 {
    1.0
}

impl From<AabbJson> for Aabb {
    fn from(j: AabbJson) -> Self {
        Aabb {
            min: Vec3::from_array(j.min),
            max: Vec3::from_array(j.max),
        }
    }
}

impl From<Aabb> for AabbJson {
    fn from(a: Aabb) -> Self {
        AabbJson {
            min: a.min.to_array(),
            max: a.max.to_array(),
        }
    }
}

impl From<JumpPadJson> for JumpPad {
    fn from(j: JumpPadJson) -> Self {
        JumpPad {
            center: Vec3::from_array(j.center),
            radius: j.radius,
            velocity: Vec3::from_array(j.velocity),
        }
    }
}

impl From<JumpPad> for JumpPadJson {
    fn from(p: JumpPad) -> Self {
        JumpPadJson {
            center: p.center.to_array(),
            radius: p.radius,
            velocity: p.velocity.to_array(),
        }
    }
}

impl From<PowerupJson> for PowerupSpawn {
    fn from(j: PowerupJson) -> Self {
        PowerupSpawn {
            kind: j.kind,
            center: Vec3::from_array(j.center),
            radius: j.radius,
        }
    }
}

impl From<PowerupSpawn> for PowerupJson {
    fn from(p: PowerupSpawn) -> Self {
        PowerupJson {
            kind: p.kind,
            center: p.center.to_array(),
            radius: p.radius,
        }
    }
}

/// The python zone scripts (tools/zones/) are the single source of truth
/// for the level; they generate these JSONs alongside the visual glbs.
/// Embedding them at compile time keeps the server filesystem-free and
/// pins replay determinism to the binary.
const EMBEDDED_V3_MANIFEST: &str = include_str!("../../../assets/scenes/manifest.json");
const EMBEDDED_V3_ZONES: &[(&str, &str)] = &[
    ("sports", include_str!("../../../assets/scenes/sports.json")),
    (
        "sleeping",
        include_str!("../../../assets/scenes/sleeping.json"),
    ),
    (
        "machine_room",
        include_str!("../../../assets/scenes/machine_room.json"),
    ),
    (
        "kitchen",
        include_str!("../../../assets/scenes/kitchen.json"),
    ),
    (
        "bathrooms",
        include_str!("../../../assets/scenes/bathrooms.json"),
    ),
    ("hall", include_str!("../../../assets/scenes/hall.json")),
    ("secret", include_str!("../../../assets/scenes/secret.json")),
    ("sky", include_str!("../../../assets/scenes/sky.json")),
];

impl Arena {
    /// V3 "raise the roof" Gru's lab, parsed from the embedded scene JSONs.
    pub fn gru_lab_v3() -> Self {
        Self::from_json(EMBEDDED_V3_MANIFEST, |name| {
            EMBEDDED_V3_ZONES
                .iter()
                .find(|(n, _)| *n == name)
                .map(|(_, raw)| Ok((*raw).to_string()))
                .unwrap_or_else(|| {
                    Err(io::Error::new(
                        io::ErrorKind::NotFound,
                        format!("zone '{name}' not embedded"),
                    ))
                })
        })
        .expect("embedded v3 scenes are valid")
    }

    /// Load an arena from per-zone JSON files. `dir` must contain a
    /// `manifest.json` with `{"floor_y": ..., "ceiling_y": ..., "zones": [...]}`
    /// and one `<zone>.json` per listed zone.
    pub fn load_dir(dir: impl AsRef<Path>) -> io::Result<Self> {
        let dir = dir.as_ref();
        let manifest_raw = fs::read_to_string(dir.join("manifest.json"))?;
        Self::from_json(&manifest_raw, |name| {
            fs::read_to_string(dir.join(format!("{name}.json")))
        })
    }

    fn from_json(
        manifest_raw: &str,
        mut read_zone: impl FnMut(&str) -> io::Result<String>,
    ) -> io::Result<Self> {
        let manifest: Manifest = serde_json::from_str(manifest_raw)
            .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;

        let mut walls = Vec::new();
        let mut zones = Vec::new();
        let mut spawns = Vec::new();
        let mut pads = Vec::new();
        let mut powerups = Vec::new();
        let mut elevators = Vec::new();

        for name in &manifest.zones {
            let raw = read_zone(name)?;
            let z: ZoneFile = serde_json::from_str(&raw)
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;

            // Leak the zone name so we can keep `&'static str` on Zone without
            // re-plumbing the type. It's O(zones) once per load, trivial.
            let leaked: &'static str = Box::leak(z.name.into_boxed_str());
            zones.push(Zone {
                name: leaked,
                bounds: z.bounds.into(),
            });

            for w in z.walls {
                walls.push(w.into());
            }
            for p in z.jump_pads {
                pads.push(p.into());
            }
            for p in z.powerups {
                powerups.push(p.into());
            }
            for s in z.spawn_points {
                spawns.push(Vec3::from_array(s));
            }
            for e in z.elevators {
                elevators.push(e.into());
            }
        }

        Ok(Arena {
            floor_y: manifest.floor_y,
            ceiling_y: manifest.ceiling_y,
            walls,
            zones,
            spawn_points: spawns,
            jump_pads: pads,
            powerups,
            elevators,
        })
    }

    /// Write the current arena back out as per-zone JSON files. The inverse
    /// of `load_dir`. Used by the editor's `/save` endpoint and the
    /// `export_scenes` example.
    pub fn save_dir(&self, dir: impl AsRef<Path>) -> io::Result<()> {
        let dir = dir.as_ref();
        fs::create_dir_all(dir)?;

        let zone_names: Vec<&str> = self.zones.iter().map(|z| z.name).collect();
        let manifest = Manifest {
            floor_y: self.floor_y,
            ceiling_y: self.ceiling_y,
            zones: zone_names.iter().map(|s| s.to_string()).collect(),
        };
        fs::write(
            dir.join("manifest.json"),
            serde_json::to_string_pretty(&manifest).unwrap() + "\n",
        )?;

        for zone in &self.zones {
            let mut file = ZoneFile {
                name: zone.name.to_string(),
                bounds: zone.bounds.into(),
                walls: Vec::new(),
                jump_pads: Vec::new(),
                powerups: Vec::new(),
                spawn_points: Vec::new(),
                elevators: Vec::new(),
                props: Vec::new(),
            };
            for w in &self.walls {
                if Arena::classify_wall(w) == zone.name {
                    file.walls.push((*w).into());
                }
            }
            for p in &self.jump_pads {
                if classify_point(p.center) == zone.name {
                    file.jump_pads.push((*p).into());
                }
            }
            for p in &self.powerups {
                if classify_point(p.center) == zone.name {
                    file.powerups.push((*p).into());
                }
            }
            for s in &self.spawn_points {
                if classify_point(*s) == zone.name {
                    file.spawn_points.push(s.to_array());
                }
            }
            for e in &self.elevators {
                if classify_point(Vec3::new(e.center_x, 0.0, e.center_z)) == zone.name {
                    file.elevators.push((*e).into());
                }
            }
            fs::write(
                dir.join(format!("{}.json", zone.name)),
                serde_json::to_string_pretty(&file).unwrap() + "\n",
            )?;
        }
        Ok(())
    }
}

#[derive(Serialize, Deserialize)]
struct Manifest {
    floor_y: f32,
    ceiling_y: f32,
    zones: Vec<String>,
}

fn classify_point(p: Vec3) -> &'static str {
    // Match classify_wall but for points (pads, spawns). Reuses the same
    // zone boundaries as wall classification.
    let dummy = Aabb {
        min: Vec3::new(p.x - 0.01, p.y, p.z - 0.01),
        max: Vec3::new(p.x + 0.01, p.y, p.z + 0.01),
    };
    Arena::classify_wall(&dummy)
}
