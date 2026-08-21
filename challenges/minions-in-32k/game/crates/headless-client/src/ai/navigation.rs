use glam::Vec3;

const NAV_NODE_COUNT: usize = 26;

const NAV_EDGES: &[(usize, usize)] = &[
    (0, 1),
    (1, 2),
    (1, 3),
    (2, 5),
    (3, 4),
    (5, 6),
    (4, 7),
    (6, 10),
    (7, 8),
    (7, 10),
    (8, 9),
    (10, 11),
    (11, 12),
    (9, 13),
    (12, 15),
    (13, 14),
    (15, 16),
    (14, 17),
    (16, 18),
    (17, 18),
    (17, 19),
    (11, 20),
    (20, 21),
    (21, 22),
    (23, 24),
    (24, 2),
    (25, 18),
];

pub(super) fn nodes() -> [Vec3; NAV_NODE_COUNT] {
    [
        Vec3::new(-24.0, 0.0, -8.0),
        Vec3::new(-16.5, 0.0, -0.25),
        Vec3::new(-16.5, 0.0, 1.0),
        Vec3::new(-13.5, 0.0, -1.4),
        Vec3::new(-10.5, 0.0, -1.4),
        Vec3::new(-13.5, 0.0, 1.0),
        Vec3::new(-10.5, 0.0, 1.0),
        Vec3::new(-7.0, 0.0, -4.0),
        Vec3::new(0.0, 0.0, -4.0),
        Vec3::new(7.0, 0.0, -4.0),
        Vec3::new(-7.0, 0.0, 3.3),
        Vec3::new(0.0, 0.0, 3.3),
        Vec3::new(7.0, 0.0, 3.3),
        Vec3::new(10.5, 0.0, -1.0),
        Vec3::new(13.5, 0.0, -1.0),
        Vec3::new(10.5, 0.0, 1.0),
        Vec3::new(13.5, 0.0, 1.0),
        Vec3::new(19.5, 0.0, -1.0),
        Vec3::new(19.5, 0.0, 1.0),
        Vec3::new(24.0, 0.0, -8.0),
        Vec3::new(0.0, 0.0, 12.0),
        Vec3::new(0.0, 0.0, 17.2),
        Vec3::new(0.0, 0.0, 21.0),
        Vec3::new(-22.0, 0.0, 10.0),
        Vec3::new(-22.0, 0.0, 6.0),
        Vec3::new(30.0, 0.0, 6.0),
    ]
}

/// Route to an arbitrary ground-floor location using only geometry visible to
/// an ordinary client. Direct paths stay direct; corners use the authored
/// navigation graph, which covers every ground-floor doorway.
pub(super) fn waypoint_to(arena: &sim::Arena, from: Vec3, goal: Vec3) -> Vec3 {
    if segment_clear(arena, from, goal) {
        return goal;
    }

    let nodes = nodes();
    let (distance, previous) = shortest_paths(arena, from, &nodes);
    let endpoint = (0..NAV_NODE_COUNT)
        .filter(|&i| distance[i].is_finite() && segment_clear(arena, nodes[i], goal))
        .min_by(|&a, &b| {
            let a_cost = distance[a] + flat_distance(nodes[a], goal);
            let b_cost = distance[b] + flat_distance(nodes[b], goal);
            a_cost.total_cmp(&b_cost)
        });

    endpoint
        .and_then(|target| {
            path_to(target, &previous)
                .into_iter()
                .map(|index| nodes[index])
                .find(|node| flat_distance(from, *node) > 1.4)
        })
        .unwrap_or(goal)
}

pub(super) fn patrol_goal(salt: u64, step: usize) -> Vec3 {
    // Offset and use a stride coprime to the 12-point patrol subset so the
    // seven NPCs spread through the arena instead of marching in a train.
    const PATROL: [usize; 12] = [23, 2, 6, 8, 13, 17, 25, 18, 16, 15, 20, 10];
    let index = (salt as usize * 5 + step * 7) % PATROL.len();
    nodes()[PATROL[index]]
}

fn shortest_paths(
    arena: &sim::Arena,
    from: Vec3,
    nodes: &[Vec3; NAV_NODE_COUNT],
) -> ([f32; NAV_NODE_COUNT], [Option<usize>; NAV_NODE_COUNT]) {
    let mut distance = [f32::INFINITY; NAV_NODE_COUNT];
    let mut previous = [None; NAV_NODE_COUNT];
    let mut visited = [false; NAV_NODE_COUNT];

    for (i, node) in nodes.iter().enumerate() {
        if segment_clear(arena, from, *node) {
            distance[i] = flat_distance(from, *node);
        }
    }

    for _ in 0..NAV_NODE_COUNT {
        let Some(current) = (0..NAV_NODE_COUNT)
            .filter(|&i| !visited[i] && distance[i].is_finite())
            .min_by(|&a, &b| distance[a].total_cmp(&distance[b]))
        else {
            break;
        };
        visited[current] = true;
        for &(a, b) in NAV_EDGES {
            let next = if a == current {
                Some(b)
            } else if b == current {
                Some(a)
            } else {
                None
            };
            let Some(next) = next else { continue };
            let candidate = distance[current] + flat_distance(nodes[current], nodes[next]);
            if candidate < distance[next] {
                distance[next] = candidate;
                previous[next] = Some(current);
            }
        }
    }
    (distance, previous)
}

fn path_to(target: usize, previous: &[Option<usize>; NAV_NODE_COUNT]) -> Vec<usize> {
    let mut reversed = vec![target];
    let mut cursor = target;
    while let Some(parent) = previous[cursor] {
        reversed.push(parent);
        cursor = parent;
    }
    reversed.reverse();
    reversed
}

pub(super) fn flat_distance(a: Vec3, b: Vec3) -> f32 {
    Vec3::new(a.x - b.x, 0.0, a.z - b.z).length()
}

pub(super) fn segment_clear(arena: &sim::Arena, from: Vec3, to: Vec3) -> bool {
    let padding = sim::PLAYER_RADIUS + 0.08;
    let head = from.y + sim::PLAYER_HEIGHT;
    for wall in &arena.walls {
        if head <= wall.min.y || from.y >= wall.max.y {
            continue;
        }
        if segment_intersects_rect(
            from,
            to,
            wall.min.x - padding,
            wall.max.x + padding,
            wall.min.z - padding,
            wall.max.z + padding,
        ) {
            return false;
        }
    }
    true
}

fn segment_intersects_rect(
    from: Vec3,
    to: Vec3,
    min_x: f32,
    max_x: f32,
    min_z: f32,
    max_z: f32,
) -> bool {
    let mut enter: f32 = 0.0;
    let mut exit: f32 = 1.0;
    for (start, delta, min, max) in [
        (from.x, to.x - from.x, min_x, max_x),
        (from.z, to.z - from.z, min_z, max_z),
    ] {
        if delta.abs() < 1e-6 {
            if start < min || start > max {
                return false;
            }
            continue;
        }
        let mut a = (min - start) / delta;
        let mut b = (max - start) / delta;
        if a > b {
            std::mem::swap(&mut a, &mut b);
        }
        enter = enter.max(a);
        exit = exit.min(b);
        if enter > exit {
            return false;
        }
    }
    exit >= 0.0 && enter <= 1.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn authored_edges_only_require_traversal_at_bleachers() {
        let arena = sim::Arena::gru_lab_v3();
        let nodes = nodes();
        let blocked: Vec<_> = NAV_EDGES
            .iter()
            .copied()
            .filter(|&(a, b)| !segment_clear(&arena, nodes[a], nodes[b]))
            .collect();
        assert_eq!(blocked, vec![(0, 1), (1, 3)]);
    }

    #[test]
    fn every_spawn_can_enter_navigation_routes() {
        let arena = sim::Arena::gru_lab_v3();
        for spawn in &arena.spawn_points {
            for goal in [
                Vec3::new(-24.0, 0.0, -8.0),
                Vec3::new(24.0, 0.0, -8.0),
                Vec3::new(0.0, 0.0, 21.0),
                Vec3::new(30.0, 0.0, 6.0),
            ] {
                let waypoint = waypoint_to(&arena, *spawn, goal);
                assert!(waypoint.is_finite());
                assert!(flat_distance(*spawn, waypoint) > 0.1);
            }
        }
    }
}
