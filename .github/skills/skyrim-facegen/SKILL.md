---
name: skyrim-facegen
description: "Use when reading or changing a Skyrim NPC's face shape: long/narrow/wide face, nose size, jaw, brows, eyes, lips, chin, weight, nose/eye/lip presets, NAM9/NAMA/NAM7, FaceGen nif. Triggers: give this character a long face, smaller nose, change facial features, get_npc_face, edit_npc_face. For hair colour use edit_npc hair_color."
---

# Skyrim FaceGen

The game displays a baked FaceGen nif (`Meshes\Actors\Character\FaceGenData\FaceGeom\<plugin>\<formid>.nif`), not the NPC_ morph values, so a face edit must change both. `edit_npc_face` does both.

## Workflow
1. `get_npc_face(npc, target_plugin)` – returns the 18 sliders (-1..1) with meanings, presets and weight.
2. Translate the request into absolute slider values (current value ± 0.3–0.5 for "a bit", up to ±1 for "very"), then `edit_npc_face(npc, {name: value}, target_plugin)`.
3. `package_plugin`, reinstall in Vortex.

## Request → slider hints
- Longer face: `jaw_height` + , `chin_height` + (lower jaw/chin). Shorter: negative.
- Narrow face: `jaw_width` -, `cheek_forward` -, `chin_width` -.
- Small nose: `nose_length` - (short), `nose_height` + (tip up). Big nose: opposite. `nose_type` picks a preset shape (1-24).
- Eyes wider apart `eye_spacing` +; bigger brow ridge `brow_forward` +; fuller/forward lips `lip_depth` +.
- Weight (0-100) changes head fullness too.

## Limits
- Models head, eyes, mouth and brows; hair and hairline keep their shape.
- `beard`: pass `{"beard": "HumanBeard13"}` to edit_npc_face to add facial hair (updates PNAM and grafts the beard shape from a vanilla FaceGen mesh, tinted to the hair colour). HumanBeard13 is a goatee (moustache + chin patch); 11/12 are chin strips, 31/45 fuller beards. Names are not descriptive, so check candidates by plotting vertices. Replacing an existing beard is not supported. SE meshes only.
- Beast races (Argonian, Khajiit) refused. Female sliders and `jaw_forward` are unverified.
- The regenerated geometry matches vanilla bakes to ~0.005 units on the head; edits are applied as a delta so unmodelled detail is kept.
- Only works in place on a plugin with a loose nif beside it (e.g. NPCs made by create_npc). Otherwise only the record changes: regenerate in the Creation Kit (Ctrl+F4 in the NPC window).
- A `.bak` of the nif is written next to it; restart the MCP server after code changes.
