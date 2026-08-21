# assets/

Runtime-loaded assets. The client loads these `.glb` files at startup. Source files and the pipeline that produced each piece live under `tools/`.

## Layout

```
zones/
  sports.glb            visual mesh
  sports.collision.glb  simplified collision mesh
  sleeping.glb          ...
  machine_room.glb      central cylinder + orbiting platforms
  kitchen.glb
  bathrooms.glb
  hall.glb              v3 upper layer: decks, bridges, goo tube, rocket
minion/
  minion.glb            rigged character variants + gun
  animations.json       animation name → clip metadata
```

## Conventions

- glTF 2.0, Y-up, 1 unit = 1 meter.
- Every visual mesh has a matching `*.collision.glb` with a simplified mesh for future server-side use.
- Animation clip names match `animations.json`; client and server reference names, not indices.
- Minion scene 0 is the classic character. Scenes 1-3 are additional variants
  with the same skeleton and animation set.
