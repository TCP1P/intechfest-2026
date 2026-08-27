//! Static arena geometry — Gru's lab.
//!
//! Geometry is defined as axis-aligned boxes ("walls" = solids players collide
//! with) and named zones (purely for metadata / UI). Both the server (for
//! collision) and the client (for rendering) read from this module so the two
//! cannot drift.
//!
//! Phase 2 ships a minimal arena: a ground plane plus the outer bounding walls
//! that keep the 8-player FFA contained. Phase 3 populates the five Gru's-lab
//! zones with internal geometry.

use glam::Vec3;
use protocol::PowerupKind;

/// Axis-aligned box collider. `min` and `max` are inclusive corners in world
/// space. Players collide against these as solid.
#[derive(Copy, Clone, Debug)]
pub struct Aabb {
    pub min: Vec3,
    pub max: Vec3,
}

impl Aabb {
    pub fn from_center_size(center: Vec3, size: Vec3) -> Self {
        let half = size * 0.5;
        Self {
            min: center - half,
            max: center + half,
        }
    }

    pub fn contains(self, p: Vec3) -> bool {
        p.x >= self.min.x
            && p.x <= self.max.x
            && p.y >= self.min.y
            && p.y <= self.max.y
            && p.z >= self.min.z
            && p.z <= self.max.z
    }

    /// Slab-test raycast. Returns `t` along `dir` (unit) where the ray first
    /// enters the box, or `None` if it misses.
    pub fn ray(self, origin: Vec3, dir: Vec3, t_max: f32) -> Option<f32> {
        let inv = Vec3::new(
            if dir.x != 0.0 {
                1.0 / dir.x
            } else {
                f32::INFINITY
            },
            if dir.y != 0.0 {
                1.0 / dir.y
            } else {
                f32::INFINITY
            },
            if dir.z != 0.0 {
                1.0 / dir.z
            } else {
                f32::INFINITY
            },
        );
        let t1 = (self.min - origin) * inv;
        let t2 = (self.max - origin) * inv;
        let tmin = t1.min(t2);
        let tmax = t1.max(t2);
        let enter = tmin.x.max(tmin.y).max(tmin.z);
        let exit = tmax.x.min(tmax.y).min(tmax.z);
        if exit < 0.0 || enter > exit || enter > t_max {
            None
        } else {
            Some(enter.max(0.0))
        }
    }
}

#[derive(Copy, Clone, Debug)]
pub struct Zone {
    pub name: &'static str,
    pub bounds: Aabb,
}

/// Quake-style launch pad. When a grounded player enters the cylindrical
/// trigger volume their velocity is REPLACED by `velocity` — a full
/// ballistic vector aimed at a landing spot (like Q3's trigger_push +
/// AimAtTarget), so the arc is hands-off: no air-steering needed.
#[derive(Copy, Clone, Debug)]
pub struct JumpPad {
    pub center: Vec3,
    pub radius: f32,
    pub velocity: Vec3,
}

#[derive(Copy, Clone, Debug)]
pub struct PowerupSpawn {
    pub kind: PowerupKind,
    pub center: Vec3,
    pub radius: f32,
}

/// Fixed-loop vertical platform. Its position is a pure function of the
/// tick, so server, client, replay, and spectator all derive identical
/// state with zero protocol traffic and nothing extra in the replay stream.
#[derive(Copy, Clone, Debug)]
pub struct Elevator {
    pub center_x: f32,
    pub center_z: f32,
    pub half_x: f32,
    pub half_z: f32,
    pub thickness: f32,
    /// Platform *top surface* y at the bottom / top of the run.
    pub y_bottom: f32,
    pub y_top: f32,
    pub speed: f32,
    pub dwell_s: f32,
}

impl Elevator {
    pub fn top_y(&self, tick: u64, tick_hz: u16) -> f32 {
        let hz = tick_hz.max(1) as f32;
        let travel_ticks = ((self.y_top - self.y_bottom) / self.speed * hz).round() as u64;
        let dwell_ticks = (self.dwell_s * hz).round() as u64;
        let period = (2 * (travel_ticks + dwell_ticks)).max(1);
        let phase = tick % period;
        let t = if phase < dwell_ticks {
            0.0
        } else if phase < dwell_ticks + travel_ticks {
            (phase - dwell_ticks) as f32 / travel_ticks.max(1) as f32
        } else if phase < 2 * dwell_ticks + travel_ticks {
            1.0
        } else {
            1.0 - (phase - 2 * dwell_ticks - travel_ticks) as f32 / travel_ticks.max(1) as f32
        };
        self.y_bottom + (self.y_top - self.y_bottom) * t
    }

    pub fn aabb_at(&self, tick: u64, tick_hz: u16) -> Aabb {
        let top = self.top_y(tick, tick_hz);
        Aabb {
            min: Vec3::new(
                self.center_x - self.half_x,
                top - self.thickness,
                self.center_z - self.half_z,
            ),
            max: Vec3::new(
                self.center_x + self.half_x,
                top,
                self.center_z + self.half_z,
            ),
        }
    }
}

pub struct Arena {
    pub floor_y: f32,
    pub ceiling_y: f32,
    pub walls: Vec<Aabb>,
    pub zones: Vec<Zone>,
    pub spawn_points: Vec<Vec3>,
    pub jump_pads: Vec<JumpPad>,
    pub powerups: Vec<PowerupSpawn>,
    pub elevators: Vec<Elevator>,
}

impl Arena {
    /// Classify a wall into one of the themed Gru's-lab zones. Used by the
    /// editor, preview renderer, and per-zone JSON split so everyone labels
    /// walls the same way.
    pub fn classify_wall(w: &Aabb) -> &'static str {
        let cx = (w.min.x + w.max.x) * 0.5;
        let cz = (w.min.z + w.max.z) * 0.5;
        if cz > 16.5 {
            return "secret";
        }
        // Everything starting at deck height belongs to the v3 hall layer,
        // so the editor's save_dir round-trips it into hall.json.
        if w.min.y >= 7.9 {
            return "hall";
        }
        if cx.abs() >= 35.5 || cz.abs() >= 15.5 {
            return "outer";
        }
        if cx < -12.0 {
            return if cz < 0.0 { "sports" } else { "sleeping" };
        }
        if cx > 12.0 {
            return if cz < 0.0 { "kitchen" } else { "bathrooms" };
        }
        "machine_room"
    }

    pub fn phase2_minimal() -> Self {
        let floor_y = 0.0;
        let ceiling_y = 6.0;
        let half = 20.0;
        let thick = 0.5;
        let mut walls = Vec::new();
        let _pads: Vec<JumpPad> = Vec::new();
        // Four outer walls (full height). Boxes extend above ceiling for safety.
        walls.push(Aabb {
            min: Vec3::new(-half, floor_y, -half - thick),
            max: Vec3::new(half, ceiling_y, -half),
        });
        walls.push(Aabb {
            min: Vec3::new(-half, floor_y, half),
            max: Vec3::new(half, ceiling_y, half + thick),
        });
        walls.push(Aabb {
            min: Vec3::new(-half - thick, floor_y, -half),
            max: Vec3::new(-half, ceiling_y, half),
        });
        walls.push(Aabb {
            min: Vec3::new(half, floor_y, -half),
            max: Vec3::new(half + thick, ceiling_y, half),
        });
        // Eight FFA spawns on a ring of radius 8, facing origin.
        let mut spawn_points = Vec::with_capacity(8);
        for i in 0..8 {
            let a = (i as f32) * std::f32::consts::TAU / 8.0;
            let (sa, ca) = common::trig::sin_cos(a);
            spawn_points.push(Vec3::new(8.0 * ca, 0.0, 8.0 * sa));
        }
        Self {
            floor_y,
            ceiling_y,
            walls,
            zones: vec![],
            spawn_points,
            jump_pads: vec![],
            powerups: vec![],
            elevators: vec![],
        }
    }

    /// Phase-3 arena: five-zone Gru's lab. Zones are laid out in a 3x2 grid
    /// with the machine room in the center; doorway cutouts are implied by
    /// gaps in the wall segments (the wall Aabbs don't span doorways).
    pub fn gru_lab() -> Self {
        let mut b = ArenaBuilder::new(0.0, 8.0);

        // Layout (top-down, +X right, +Z down):
        //
        //   [  sports  ][ machine_room ][  kitchen  ]
        //   [ sleeping ][ machine_room ][ bathrooms ]
        //
        // Each side zone is 24 x 16. Machine room is 24 x 32 (spans both rows).
        let zone_w = 24.0;
        let zone_h = 16.0;

        // Side-zone anchors. `(center, size, name)`
        let left_sports = Vec3::new(-24.0, 0.0, -8.0);
        let left_sleep = Vec3::new(-24.0, 0.0, 8.0);
        let right_kitchen = Vec3::new(24.0, 0.0, -8.0);
        let right_bath = Vec3::new(24.0, 0.0, 8.0);
        let center_machine = Vec3::new(0.0, 0.0, 0.0);

        b.zone("sports", left_sports, Vec3::new(zone_w, 0.0, zone_h));
        b.zone("sleeping", left_sleep, Vec3::new(zone_w, 0.0, zone_h));
        b.zone("machine_room", center_machine, Vec3::new(zone_w, 0.0, 32.0));
        b.zone("kitchen", right_kitchen, Vec3::new(zone_w, 0.0, zone_h));
        b.zone("bathrooms", right_bath, Vec3::new(zone_w, 0.0, zone_h));
        b.zone(
            "secret",
            Vec3::new(0.0, 0.0, 21.0),
            Vec3::new(24.0, 0.0, 10.0),
        );
        b.zone(
            "outer",
            Vec3::new(0.0, 0.0, 0.0),
            Vec3::new(72.0, 0.0, 32.0),
        );

        // Outer bounding walls for the whole 72 x 32 arena. Corner-ownership
        // pattern to prevent top-face z-fighting where walls meet:
        //   - outer vertical walls (W/E) extend by half-thickness into each
        //     corner and OWN those four corners
        //   - outer horizontal walls (N/S) shorten by half-thickness at each
        //     end so they butt cleanly against the verticals' inner faces
        //   - interior walls stop at the inner face of the outer wall they
        //     meet (no top-face overlap at T-junctions)
        let outer_min_x = -36.0;
        let outer_max_x = 36.0;
        let outer_min_z = -16.0;
        let outer_max_z = 16.0;
        let t_o = 0.5;
        let h_o = t_o * 0.5; // 0.25
        let t_i = 0.4;
        let h_i = t_i * 0.5; // 0.20

        // Outer west + east (own their corners by extending ±h_o in z).
        b.wall_segment_z(outer_min_z - h_o, outer_max_z + h_o, outer_min_x, t_o);
        b.wall_segment_z(outer_min_z - h_o, outer_max_z + h_o, outer_max_x, t_o);
        // Outer north (shortened by h_o at each end).
        b.wall_segment_x(outer_min_x + h_o, outer_max_x - h_o, outer_min_z, t_o);
        // South outer wall with a 6 m doorway at x in [-3, 3] that leads to
        // the showcase ("secret") room behind the lab.
        b.wall_segment_x(outer_min_x + h_o, -3.0, outer_max_z, t_o);
        b.wall_segment_x(3.0, outer_max_x - h_o, outer_max_z, t_o);

        // Showcase room, x in [-12, 12], z in [16, 26]. Side walls own the
        // back corners (extended in z) and butt up against the south outer
        // wall at the south end. Back wall shortens by h_o at each end.
        let secret_min_z = outer_max_z;
        let secret_max_z = outer_max_z + 10.0;
        let secret_min_x = -12.0;
        let secret_max_x = 12.0;
        b.wall_segment_x(secret_min_x + h_o, secret_max_x - h_o, secret_max_z, t_o);
        b.wall_segment_z(secret_min_z + h_o, secret_max_z + h_o, secret_min_x, t_o);
        b.wall_segment_z(secret_min_z + h_o, secret_max_z + h_o, secret_max_x, t_o);

        // Internal walls at x=±12 between machine_room and the side zones.
        // Pull back from the outer N/S walls by h_o to avoid T-junction top
        // overlap, and leave doorways at z in [-3, 3] for the main atrium.
        let inner_n = outer_min_z + h_o; // -15.75
        let inner_s = outer_max_z - h_o; //  15.75
        b.wall_segment_z(inner_n, -3.0, -12.0, t_i);
        b.wall_segment_z(3.0, inner_s, -12.0, t_i);
        b.wall_segment_z(inner_n, -3.0, 12.0, t_i);
        b.wall_segment_z(3.0, inner_s, 12.0, t_i);
        // Horizontal dividers at z=0. Sports/sleeping: two segments with a
        // 3 m doorway at x in [-18, -15] (previously these ranges overlapped
        // at x in [-18, -15], a bug that caused top-face z-fighting).
        // Segments also shorten by h_i at the inner-wall end so their tops
        // don't overlap the perpendicular internal wall's top.
        let inner_w = -12.0 - h_i; // west face of the x=-12 internal wall
        let inner_e = 12.0 + h_i; // east face of the x=+12 internal wall
        b.wall_segment_x(outer_min_x + h_o, -18.0, 0.0, t_i);
        b.wall_segment_x(-15.0, inner_w, 0.0, t_i);
        // Kitchen/bathrooms divider: doorway at x in [18, 21].
        b.wall_segment_x(inner_e, 18.0, 0.0, t_i);
        b.wall_segment_x(21.0, outer_max_x - h_o, 0.0, t_i);

        // Machine-room central cylinder, approximated as a 4 x 4 tower.
        b.add_wall(Aabb::from_center_size(
            Vec3::new(0.0, 0.0, 0.0),
            Vec3::new(4.0, 8.0, 4.0),
        ));
        // Orbiting platforms (static, four around the cylinder at mid height).
        for i in 0..4 {
            let a = (i as f32) * std::f32::consts::TAU / 4.0 + std::f32::consts::FRAC_PI_4;
            let r = 6.0;
            let (sa, ca) = common::trig::sin_cos(a);
            b.add_wall(Aabb::from_center_size(
                Vec3::new(r * ca, 2.0, r * sa),
                Vec3::new(2.0, 0.3, 2.0),
            ));
        }

        // Kitchen counter strips.
        b.add_wall(Aabb::from_center_size(
            Vec3::new(20.0, 0.0, -12.0),
            Vec3::new(12.0, 1.0, 1.0),
        ));
        b.add_wall(Aabb::from_center_size(
            Vec3::new(28.0, 0.0, -4.0),
            Vec3::new(1.0, 1.0, 4.0),
        ));

        // Sleeping-quarters bunk rows (two long low blocks).
        b.add_wall(Aabb::from_center_size(
            Vec3::new(-28.0, 0.0, 8.0),
            Vec3::new(10.0, 1.5, 2.0),
        ));
        b.add_wall(Aabb::from_center_size(
            Vec3::new(-18.0, 0.0, 12.0),
            Vec3::new(4.0, 1.5, 2.0),
        ));

        // Bathroom stalls (three half-height partitions).
        for x in [18.0, 22.0, 26.0] {
            b.add_wall(Aabb::from_center_size(
                Vec3::new(x, 0.0, 10.0),
                Vec3::new(0.3, 1.5, 4.0),
            ));
        }

        // Sports-zone static ball pedestals (phase 4 will turn these into
        // physics spheres). Low cylinders approximated as boxes for now.
        for (x, z) in [(-28.0, -12.0), (-22.0, -10.0), (-30.0, -6.0)] {
            b.add_wall(Aabb::from_center_size(
                Vec3::new(x, 0.0, z),
                Vec3::new(1.2, 0.4, 1.2),
            ));
        }

        // v1 arena: no verticality pass. Ground-floor rail fights only.
        b.jump_pads = vec![];
        b.powerups = vec![
            PowerupSpawn {
                kind: PowerupKind::QuadDamage,
                center: Vec3::new(0.0, 0.0, -10.0),
                radius: 0.75,
            },
            PowerupSpawn {
                kind: PowerupKind::Haste,
                center: Vec3::new(24.0, 0.0, -8.0),
                radius: 0.75,
            },
            PowerupSpawn {
                kind: PowerupKind::Banana,
                center: Vec3::new(-24.0, 0.0, -8.0),
                radius: 0.75,
            },
        ];

        // Spawns sit clear of all colliders: (-28,-12) was on a ball
        // pedestal and (±18, 0) were flush against the z=0 divider wall
        // ends — players spawned wedged inside geometry and couldn't move.
        b.spawn_points = vec![
            Vec3::new(-26.0, 0.0, -14.0),
            Vec3::new(-28.0, 0.0, 12.0),
            Vec3::new(28.0, 0.0, -12.0),
            Vec3::new(28.0, 0.0, 12.0),
            Vec3::new(0.0, 0.0, -12.0),
            Vec3::new(0.0, 0.0, 12.0),
            Vec3::new(-16.5, 0.0, 0.0),
            Vec3::new(19.5, 0.0, 0.0),
        ];

        b.build()
    }
}

struct ArenaBuilder {
    floor_y: f32,
    ceiling_y: f32,
    walls: Vec<Aabb>,
    zones: Vec<Zone>,
    spawn_points: Vec<Vec3>,
    jump_pads: Vec<JumpPad>,
    powerups: Vec<PowerupSpawn>,
}

impl ArenaBuilder {
    fn new(floor_y: f32, ceiling_y: f32) -> Self {
        Self {
            floor_y,
            ceiling_y,
            walls: Vec::new(),
            zones: Vec::new(),
            spawn_points: Vec::new(),
            jump_pads: Vec::new(),
            powerups: Vec::new(),
        }
    }

    fn zone(&mut self, name: &'static str, center: Vec3, size_xz: Vec3) {
        let size = Vec3::new(size_xz.x, self.ceiling_y - self.floor_y, size_xz.z);
        let bounds = Aabb::from_center_size(
            Vec3::new(center.x, (self.floor_y + self.ceiling_y) * 0.5, center.z),
            size,
        );
        self.zones.push(Zone { name, bounds });
    }

    fn add_wall(&mut self, a: Aabb) {
        self.walls.push(a);
    }

    fn wall_segment_x(&mut self, x0: f32, x1: f32, z: f32, thick: f32) {
        if x1 <= x0 {
            return;
        }
        self.walls.push(Aabb {
            min: Vec3::new(x0, self.floor_y, z - thick * 0.5),
            max: Vec3::new(x1, self.ceiling_y, z + thick * 0.5),
        });
    }

    fn wall_segment_z(&mut self, z0: f32, z1: f32, x: f32, thick: f32) {
        if z1 <= z0 {
            return;
        }
        self.walls.push(Aabb {
            min: Vec3::new(x - thick * 0.5, self.floor_y, z0),
            max: Vec3::new(x + thick * 0.5, self.ceiling_y, z1),
        });
    }

    fn build(self) -> Arena {
        Arena {
            floor_y: self.floor_y,
            ceiling_y: self.ceiling_y,
            walls: self.walls,
            zones: self.zones,
            spawn_points: self.spawn_points,
            jump_pads: self.jump_pads,
            powerups: self.powerups,
            elevators: vec![],
        }
    }
}
