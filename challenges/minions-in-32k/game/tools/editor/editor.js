// Three.js arena editor for minions-in-16k.
//
// Data source: GET /arena.json which the serve.py script builds by
// running `cargo run --release -p sim --example dump_arena` and
// returning the result.
//
// Editing: uses TransformControls to drag objects. Property inputs let
// you type exact values. Save via POST /save which writes the edited
// JSON to `tools/editor/edits.json` for manual review/apply.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const ZONE_GLBS = ['sports', 'sleeping', 'machine_room', 'kitchen', 'bathrooms'];

// ----- themed Gru's-lab zones (matches sim::Arena::classify_wall) -----
const ZONE_COLORS = {
    outer:        0x585d68,
    machine_room: 0x5a6068,
    sports:       0x7a6a46,
    sleeping:     0x445066,
    kitchen:      0x507050,
    bathrooms:    0x7a4a4f,
    secret:       0x9070a0,
};
function classifyWall(w) {
    // Prefer the server-provided tag (dump_arena adds it); fall back to the
    // geometry rule so the editor still works on hand-edited JSON.
    if (w.zone) return w.zone;
    const cx = (w.min[0] + w.max[0]) * 0.5;
    const cz = (w.min[2] + w.max[2]) * 0.5;
    if (cz > 16.5) return 'secret';
    if (Math.abs(cx) >= 35.5 || Math.abs(cz) >= 15.5) return 'outer';
    if (cx < -12) return cz < 0 ? 'sports' : 'sleeping';
    if (cx > 12) return cz < 0 ? 'kitchen' : 'bathrooms';
    return 'machine_room';
}

// ----- state -----
let arena = null;        // { floor_y, ceiling_y, walls, jump_pads, spawn_points }
let dirty = false;
let selected = null;     // { kind: 'wall' | 'pad' | 'spawn', mesh, index }
let highlighted = null;

const wallMeshes = [];
const padMeshes = [];
const spawnMeshes = [];
const labelSprites = [];

// ----- three.js scaffolding -----
const container = document.getElementById('canvas-container');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.shadowMap.enabled = false;
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f1116);
scene.fog = new THREE.Fog(0x0f1116, 150, 260);

const camera = new THREE.PerspectiveCamera(55, 1, 0.5, 500);
const DEFAULT_CAM_POS = new THREE.Vector3(-60, 60, 80);
const DEFAULT_CAM_LOOK = new THREE.Vector3(0, 4, 0);
camera.position.copy(DEFAULT_CAM_POS);
camera.lookAt(DEFAULT_CAM_LOOK);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.copy(DEFAULT_CAM_LOOK);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 5;
controls.maxDistance = 250;

const transformControls = new TransformControls(camera, renderer.domElement);
transformControls.setSize(0.8);
transformControls.addEventListener('dragging-changed', ev => {
    controls.enabled = !ev.value;
});
transformControls.addEventListener('objectChange', () => {
    if (!selected) return;
    syncFromMesh(selected);
    refreshInspectorFromData();
    markDirty();
});
scene.add(transformControls);

// Lighting
scene.add(new THREE.AmbientLight(0xffffff, 0.35));
const key = new THREE.DirectionalLight(0xffffff, 0.9);
key.position.set(-40, 80, 30);
scene.add(key);
const fill = new THREE.DirectionalLight(0xaabbcc, 0.35);
fill.position.set(60, 40, -40);
scene.add(fill);

// Floor plane
const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(260, 180),
    new THREE.MeshStandardMaterial({ color: 0x191c23, roughness: 0.95 }),
);
floor.rotation.x = -Math.PI / 2;
floor.position.y = -0.05;
scene.add(floor);

// Grid
const grid = new THREE.GridHelper(240, 60, 0x334055, 0x222730);
grid.position.y = 0.0;
scene.add(grid);
// Axis helper
const axes = new THREE.AxesHelper(4);
axes.position.set(0, 0.02, 0);
scene.add(axes);

// Pickable groups
const wallsGroup = new THREE.Group();
const padsGroup = new THREE.Group();
const spawnsGroup = new THREE.Group();
const labelsGroup = new THREE.Group();
labelsGroup.visible = false;
// Non-pickable: themed zone glbs for visual context.
const glbGroup = new THREE.Group();
scene.add(wallsGroup, padsGroup, spawnsGroup, labelsGroup, glbGroup);

// Load the same zone glbs the game client renders, so the editor view
// matches what players see. Loaded once at startup; editing walls doesn't
// move these.
const gltfLoader = new GLTFLoader();
for (const name of ZONE_GLBS) {
    gltfLoader.load(`/assets/zones/${name}.glb`, gltf => {
        gltf.scene.traverse(obj => { obj.userData.glbZone = name; });
        glbGroup.add(gltf.scene);
    }, undefined, err => {
        console.warn(`editor: ${name}.glb load failed`, err);
    });
}

// Raycaster
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

// ----- build / rebuild scene -----
function clearGroups() {
    [wallsGroup, padsGroup, spawnsGroup, labelsGroup].forEach(g => {
        while (g.children.length) {
            const c = g.children.pop();
            if (c.geometry) c.geometry.dispose();
            if (c.material) {
                if (Array.isArray(c.material)) c.material.forEach(m => m.dispose());
                else c.material.dispose();
            }
        }
    });
    wallMeshes.length = 0;
    padMeshes.length = 0;
    spawnMeshes.length = 0;
    labelSprites.length = 0;
}

function buildScene() {
    clearGroups();
    if (!arena) return;

    const geom = new THREE.BoxGeometry(1, 1, 1);

    arena.walls.forEach((w, i) => {
        const zone = classifyWall(w);
        const mat = new THREE.MeshStandardMaterial({
            color: ZONE_COLORS[zone] ?? 0x888888,
            roughness: 0.85,
            metalness: 0.05,
            transparent: true,
            opacity: 1.0,
        });
        const m = new THREE.Mesh(geom, mat);
        m.userData = { kind: 'wall', zone, index: i };
        syncFromData(m);
        wallsGroup.add(m);
        wallMeshes.push(m);
    });

    const padCyl = new THREE.CylinderGeometry(1, 1, 0.1, 24);
    arena.jump_pads.forEach((p, i) => {
        const mat = new THREE.MeshStandardMaterial({
            color: 0x1acfd8,
            emissive: 0x0a7882,
            emissiveIntensity: 1.2,
            roughness: 0.3,
        });
        const m = new THREE.Mesh(padCyl, mat);
        m.userData = { kind: 'pad', index: i };
        syncFromData(m);
        padsGroup.add(m);
        padMeshes.push(m);
    });

    const spawnGeom = new THREE.SphereGeometry(0.5, 12, 8);
    arena.spawn_points.forEach((s, i) => {
        const mat = new THREE.MeshStandardMaterial({
            color: 0x3ce566,
            emissive: 0x1f7a34,
            emissiveIntensity: 0.5,
        });
        const m = new THREE.Mesh(spawnGeom, mat);
        m.userData = { kind: 'spawn', index: i };
        syncFromData(m);
        spawnsGroup.add(m);
        spawnMeshes.push(m);
    });

    // Labels (lazy — built once visibility toggles on)
    updateStats();
}

// Sync between data <-> mesh -------------------------------------------------
function syncFromData(mesh) {
    const ud = mesh.userData;
    if (ud.kind === 'wall') {
        const w = arena.walls[ud.index];
        const cx = (w.min[0] + w.max[0]) * 0.5;
        const cy = (w.min[1] + w.max[1]) * 0.5;
        const cz = (w.min[2] + w.max[2]) * 0.5;
        const sx = Math.max(0.01, w.max[0] - w.min[0]);
        const sy = Math.max(0.01, w.max[1] - w.min[1]);
        const sz = Math.max(0.01, w.max[2] - w.min[2]);
        mesh.position.set(cx, cy, cz);
        mesh.scale.set(sx, sy, sz);
    } else if (ud.kind === 'pad') {
        const p = arena.jump_pads[ud.index];
        mesh.position.set(p.center[0], p.center[1] + 0.06, p.center[2]);
        mesh.scale.set(p.radius, 1, p.radius);
    } else if (ud.kind === 'spawn') {
        const s = arena.spawn_points[ud.index];
        mesh.position.set(s[0], s[1] + 0.5, s[2]);
        mesh.scale.set(1, 1, 1);
    }
}

function syncFromMesh(sel) {
    const { mesh } = sel;
    const ud = mesh.userData;
    if (ud.kind === 'wall') {
        const w = arena.walls[ud.index];
        w.min = [
            mesh.position.x - mesh.scale.x * 0.5,
            mesh.position.y - mesh.scale.y * 0.5,
            mesh.position.z - mesh.scale.z * 0.5,
        ];
        w.max = [
            mesh.position.x + mesh.scale.x * 0.5,
            mesh.position.y + mesh.scale.y * 0.5,
            mesh.position.z + mesh.scale.z * 0.5,
        ];
    } else if (ud.kind === 'pad') {
        const p = arena.jump_pads[ud.index];
        p.center = [mesh.position.x, mesh.position.y - 0.06, mesh.position.z];
        p.radius = (mesh.scale.x + mesh.scale.z) * 0.5;
    } else if (ud.kind === 'spawn') {
        arena.spawn_points[ud.index] = [
            mesh.position.x,
            mesh.position.y - 0.5,
            mesh.position.z,
        ];
    }
}

// Inspector -----------------------------------------------------------------
const ins = {
    empty: document.getElementById('inspector-empty'),
    body: document.getElementById('inspector-body'),
    kind: document.getElementById('i-kind'),
    zone: document.getElementById('i-zone'),
    index: document.getElementById('i-index'),
    cx: document.getElementById('i-cx'),
    cy: document.getElementById('i-cy'),
    cz: document.getElementById('i-cz'),
    sx: document.getElementById('i-sx'),
    sy: document.getElementById('i-sy'),
    sz: document.getElementById('i-sz'),
    padHeader: document.getElementById('pad-header'),
    padR: document.getElementById('i-pad-r'),
    padV: document.getElementById('i-pad-v'),
    padRRow: document.getElementById('pad-radius-row'),
    padVRow: document.getElementById('pad-vel-row'),
};

function refreshInspectorFromData() {
    if (!selected) {
        ins.empty.hidden = false;
        ins.body.hidden = true;
        return;
    }
    ins.empty.hidden = true;
    ins.body.hidden = false;
    const ud = selected.mesh.userData;
    ins.kind.textContent = ud.kind;
    ins.zone.textContent = ud.zone ?? '—';
    ins.index.textContent = String(ud.index);

    if (ud.kind === 'wall') {
        const w = arena.walls[ud.index];
        ins.cx.value = ((w.min[0] + w.max[0]) * 0.5).toFixed(3);
        ins.cy.value = ((w.min[1] + w.max[1]) * 0.5).toFixed(3);
        ins.cz.value = ((w.min[2] + w.max[2]) * 0.5).toFixed(3);
        ins.sx.value = (w.max[0] - w.min[0]).toFixed(3);
        ins.sy.value = (w.max[1] - w.min[1]).toFixed(3);
        ins.sz.value = (w.max[2] - w.min[2]).toFixed(3);
        ins.padHeader.hidden = true;
        ins.padRRow.hidden = true;
        ins.padVRow.hidden = true;
    } else if (ud.kind === 'pad') {
        const p = arena.jump_pads[ud.index];
        ins.cx.value = p.center[0].toFixed(3);
        ins.cy.value = p.center[1].toFixed(3);
        ins.cz.value = p.center[2].toFixed(3);
        ins.sx.value = ins.sy.value = ins.sz.value = '';
        ins.padHeader.hidden = false;
        ins.padRRow.hidden = false;
        ins.padVRow.hidden = false;
        ins.padR.value = p.radius.toFixed(2);
        ins.padV.value = (p.velocity ? p.velocity[1] : 0).toFixed(1);
    } else if (ud.kind === 'spawn') {
        const s = arena.spawn_points[ud.index];
        ins.cx.value = s[0].toFixed(3);
        ins.cy.value = s[1].toFixed(3);
        ins.cz.value = s[2].toFixed(3);
        ins.sx.value = ins.sy.value = ins.sz.value = '';
        ins.padHeader.hidden = true;
        ins.padRRow.hidden = true;
        ins.padVRow.hidden = true;
    }
}

function applyInspectorToData() {
    if (!selected) return;
    const ud = selected.mesh.userData;
    const cx = parseFloat(ins.cx.value) || 0;
    const cy = parseFloat(ins.cy.value) || 0;
    const cz = parseFloat(ins.cz.value) || 0;
    if (ud.kind === 'wall') {
        const sx = Math.max(0.01, parseFloat(ins.sx.value) || 0.01);
        const sy = Math.max(0.01, parseFloat(ins.sy.value) || 0.01);
        const sz = Math.max(0.01, parseFloat(ins.sz.value) || 0.01);
        arena.walls[ud.index] = {
            min: [cx - sx * 0.5, cy - sy * 0.5, cz - sz * 0.5],
            max: [cx + sx * 0.5, cy + sy * 0.5, cz + sz * 0.5],
        };
    } else if (ud.kind === 'pad') {
        const p = arena.jump_pads[ud.index];
        p.center = [cx, cy, cz];
        p.radius = Math.max(0.1, parseFloat(ins.padR.value) || p.radius);
        p.velocity = p.velocity || [0, 0, 0];
        p.velocity[1] = parseFloat(ins.padV.value) || p.velocity[1];
    } else if (ud.kind === 'spawn') {
        arena.spawn_points[ud.index] = [cx, cy, cz];
    }
    syncFromData(selected.mesh);
    markDirty();
}

['i-cx','i-cy','i-cz','i-sx','i-sy','i-sz','i-pad-r','i-pad-v'].forEach(id => {
    document.getElementById(id).addEventListener('input', () => {
        applyInspectorToData();
    });
});

// Picking -------------------------------------------------------------------
function pickableMeshes() {
    const out = [];
    for (const m of wallMeshes) out.push(m);
    if (padsGroup.visible) for (const m of padMeshes) out.push(m);
    if (spawnsGroup.visible) for (const m of spawnMeshes) out.push(m);
    return out;
}

function select(mesh) {
    if (selected && selected.mesh !== mesh) clearHighlight(selected.mesh);
    if (!mesh) {
        selected = null;
        transformControls.detach();
        refreshInspectorFromData();
        return;
    }
    selected = { mesh, kind: mesh.userData.kind, index: mesh.userData.index };
    highlight(mesh);
    transformControls.attach(mesh);
    transformControls.setMode(mesh.userData.kind === 'wall' ? 'translate' : 'translate');
    refreshInspectorFromData();
}

function highlight(mesh) {
    if (!mesh.material) return;
    mesh.material.emissive = new THREE.Color(0x446688);
    mesh.material.emissiveIntensity = 0.55;
    mesh.material.needsUpdate = true;
}
function clearHighlight(mesh) {
    if (!mesh.material) return;
    if (mesh.userData.kind === 'wall') {
        mesh.material.emissive = new THREE.Color(0x000000);
        mesh.material.emissiveIntensity = 0.0;
    } else if (mesh.userData.kind === 'pad') {
        mesh.material.emissive = new THREE.Color(0x0a7882);
        mesh.material.emissiveIntensity = 1.2;
    } else if (mesh.userData.kind === 'spawn') {
        mesh.material.emissive = new THREE.Color(0x1f7a34);
        mesh.material.emissiveIntensity = 0.5;
    }
    mesh.material.needsUpdate = true;
}

renderer.domElement.addEventListener('pointerdown', ev => {
    if (ev.button !== 0) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects(pickableMeshes(), false);
    if (hits.length === 0) {
        // Click on empty space = deselect (but only if not dragging gizmo).
        if (!transformControls.dragging) select(null);
        return;
    }
    select(hits[0].object);
});

// Delete / duplicate --------------------------------------------------------
document.getElementById('btn-delete').addEventListener('click', () => {
    if (!selected) return;
    const { kind, index } = selected.mesh.userData;
    if (kind === 'wall') arena.walls.splice(index, 1);
    else if (kind === 'pad') arena.jump_pads.splice(index, 1);
    else if (kind === 'spawn') arena.spawn_points.splice(index, 1);
    select(null);
    buildScene();
    markDirty();
});
document.getElementById('btn-duplicate').addEventListener('click', () => {
    if (!selected) return;
    const { kind, index } = selected.mesh.userData;
    if (kind === 'wall') {
        const w = arena.walls[index];
        arena.walls.push({
            min: [w.min[0] + 2, w.min[1], w.min[2]],
            max: [w.max[0] + 2, w.max[1], w.max[2]],
        });
    } else if (kind === 'pad') {
        const p = arena.jump_pads[index];
        arena.jump_pads.push({
            center: [p.center[0] + 2, p.center[1], p.center[2]],
            radius: p.radius,
            velocity: p.velocity,
        });
    } else if (kind === 'spawn') {
        const s = arena.spawn_points[index];
        arena.spawn_points.push([s[0] + 2, s[1], s[2]]);
    }
    buildScene();
    markDirty();
});

// Toolbar -------------------------------------------------------------------
document.getElementById('btn-reset-cam').addEventListener('click', () => {
    camera.position.copy(DEFAULT_CAM_POS);
    controls.target.copy(DEFAULT_CAM_LOOK);
});
document.getElementById('toggle-grid').addEventListener('change', ev => {
    grid.visible = ev.target.checked;
});
document.getElementById('toggle-spawns').addEventListener('change', ev => {
    spawnsGroup.visible = ev.target.checked;
});
document.getElementById('toggle-pads').addEventListener('change', ev => {
    padsGroup.visible = ev.target.checked;
});
document.getElementById('toggle-labels').addEventListener('change', ev => {
    labelsGroup.visible = ev.target.checked;
});
document.getElementById('view-mode').addEventListener('change', ev => {
    const m = ev.target.value;
    for (const mesh of wallMeshes) {
        if (m === 'wire') {
            mesh.material.wireframe = true;
            mesh.material.transparent = false;
            mesh.material.opacity = 1;
        } else if (m === 'x-ray') {
            mesh.material.wireframe = false;
            mesh.material.transparent = true;
            mesh.material.opacity = 0.35;
            mesh.material.depthWrite = false;
        } else {
            mesh.material.wireframe = false;
            mesh.material.transparent = false;
            mesh.material.opacity = 1;
            mesh.material.depthWrite = true;
        }
    }
});

// Save / download -----------------------------------------------------------
function markDirty() {
    dirty = true;
    updateStatus(`edits pending — not saved`);
    updateStats();
}
function updateStatus(text, kind) {
    const s = document.getElementById('status');
    s.textContent = text;
    s.style.color = kind === 'ok' ? 'var(--ok)'
        : kind === 'err' ? 'var(--danger)'
        : kind === 'warn' ? 'var(--warn)'
        : 'var(--fg-dim)';
}
function updateStats() {
    const el = document.getElementById('stats');
    if (!arena) { el.textContent = '—'; return; }
    el.textContent =
        `walls:   ${arena.walls.length}\n` +
        `pads:    ${arena.jump_pads.length}\n` +
        `spawns:  ${arena.spawn_points.length}\n` +
        `floor_y: ${arena.floor_y}\n` +
        `ceil_y:  ${arena.ceiling_y}`;
}

document.getElementById('btn-download').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(arena, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'arena-edits.json';
    a.click();
    URL.revokeObjectURL(a.href);
});
document.getElementById('btn-apply').addEventListener('click', async () => {
    updateStatus('saving…');
    try {
        const res = await fetch('/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(arena, null, 2),
        });
        if (!res.ok) throw new Error(await res.text());
        dirty = false;
        updateStatus(`saved to ${await res.text()}`, 'ok');
    } catch (e) {
        updateStatus(`save failed: ${e.message}`, 'err');
    }
});

// Reload --------------------------------------------------------------------
async function loadArena() {
    updateStatus('running cargo dump_arena…');
    try {
        const res = await fetch('/arena.json', { cache: 'no-store' });
        if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
        arena = await res.json();
        buildScene();
        select(null);
        updateStatus(`${arena.walls.length} walls / ${arena.jump_pads.length} pads / ${arena.spawn_points.length} spawns`, 'ok');
        dirty = false;
    } catch (e) {
        updateStatus(`load failed: ${e.message}`, 'err');
    }
}
document.getElementById('btn-reload').addEventListener('click', () => {
    if (dirty && !confirm('discard unsaved edits?')) return;
    loadArena();
});

// Render loop ---------------------------------------------------------------
function resize() {
    const r = container.getBoundingClientRect();
    renderer.setSize(r.width, r.height, false);
    camera.aspect = r.width / r.height;
    camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
resize();

function tick() {
    controls.update();
    renderer.render(scene, camera);
    requestAnimationFrame(tick);
}
tick();

loadArena();
