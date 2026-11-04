---
name: skyrim-enchantments
description: "Use when creating or attaching a custom enchantment in a Skyrim plugin: ENCH records, MGEF magic effects, enchanted weapon/armor/jewelry, constant effects, staffs, spells, spell tomes, scrolls, or a custom effect driven by a Papyrus script. Triggers: custom enchantment, enchanted unique sword, ring that gives +health, new magic effect, spell tome, scroll, staff, create_enchantment, create_spell_set, EITM, EAMT, ActiveMagicEffect."
---

# Custom enchantments

An enchantment is data, not script. Three records are involved:

| Record | Role |
|---|---|
| `MGEF` | The effect itself (archetype, delivery, cast type, art, optional script). |
| `ENCH` | A named bundle of effects: `ENIT` header + one `EFID`/`EFIT` pair per effect. |
| `WEAP`/`ARMO` | Carries the enchantment in `EITM` (form of the ENCH). Weapons also need `EAMT` (u16 charge). |

Papyrus is only needed when the effect must do something no vanilla MGEF archetype can (see the last section).

## Workflow (form IDs only, no Papyrus)
1. Pick effects: `search_forms("MGEF", "Fire Damage")`. Weapon enchantments use *fire-and-forget, touch* MGEFs (editor IDs usually end `FFContact`); worn ones use *constant effect, self* MGEFs (`Fortify...`). Prefer the vanilla `Ench...` ones.
2. Make the plugin if needed (`create_record`, `create_npc`...).
3. `create_enchantment(target_plugin, editor_id, name, effects=[{effect, magnitude, area, duration}], kind="weapon"|"armor"|"staff", cost=)`.
4. `enchant_item(item, target_plugin, enchantment, charge=None, replace=False)` attaches it to a WEAP or ARMO (vanilla items get an override; clone first with `clone_record` for a new item). It writes `EITM`, plus `EAMT` (default 500) on weapons, refuses charge on armor and refuses an already enchanted item unless `replace=true`.
5. `validate_plugin`, then `install_plugin`. Check in game with `player.additem <formid>`.

## Field facts (ENIT verified against Skyrim.esm)
- `ENIT` is 36 bytes: cost i32, flags u32 (bit 0 = no auto-calc), cast type u32 (0 constant, 1 fire-and-forget, 2 concentration), amount i32 (vanilla = cost), delivery u32 (0 self, 1 touch, 2 aimed), enchant type u32 (6 enchantment, 0x0C staff), charge time float, base enchantment formid, worn restrictions formid.
- With auto-calc on (default) the CK recomputes cost from the effects, so `cost=0` is fine; set `auto_calc=False` with an explicit `cost` to control the enchanter price.
- `EFIT` is magnitude float, area u32, duration u32. Constant (armor) effects use duration 0; weapon effects use 0 for instant damage or seconds for DoTs.
- A weapon `kind` needs MGEFs whose own cast type/delivery is fire-and-forget/touch, armor `kind` constant/self; a mismatch does nothing in game.
- `create_enchantment` leaves base enchantment and worn restrictions at 0, so the ENCH cannot be learned by disenchanting; hand such items out directly or via quests.
- `EFID` is resolved against MGEF only. The plugin must not be localized (inline strings).

## Custom effect behaviour
- Tweaked numbers/art: `clone_record` an MGEF, `edit_record` its fields, reference the clone in `effects`.
- New behaviour: set the cloned MGEF's archetype to **Script** and attach an `ActiveMagicEffect` script in its `VMAD` (see `skyrim-script-patterns`). Typical events: `OnEffectStart(Actor akTarget, Actor akCaster)` and `OnEffectFinish`. Constant armor effects run `OnEffectStart` on equip and `OnEffectFinish` on unequip, so undo any state there.
- Fast path: `create_script_effect(target_plugin, editor_id, name, script_name, source, description, kind)` compiles the `ActiveMagicEffect` source (a compile error changes nothing), builds the script MGEF (archetype Script, VMAD, no stray form refs) and an ENCH `<editor_id>Ench`; then `enchant_item`. It adds Skyrim.esm as a master up front so later overrides never renumber the new records.
- Worked example (Moonsong Circlet, Gem Cutters Guild, built by hand before that tool existed): spell power that follows the weather. Clone `EnchFortifyDestructionConstantSelf`, then `edit_record` its `DATA`: archetype (offset 64) = 1 (Script), actor value (68) and magic skill (12) = -1; remove `KSIZ`/`KWDA`; give `DNAM` inline text (a cloned 4-byte lstring is not dropped for MGEF). Attach the script with a `VMAD` right after `EDID`, create the ENCH with `kind="armor"`, then add `EITM` to the circlet (it sorts after `FULL`).
- The script never touches actor values (the power-mod names are unreliable). It adds/removes ability SPELs (SPIT type 4, EFIDs from the vanilla `EnchFortify<School>ConstantSelf` MGEFs, magnitude = % power) chosen by `Weather.GetCurrentWeather().GetClassification()` (0 pleasant, 1 cloudy, 2 rain, 3 snow), polling with `RegisterForSingleUpdate`. Find the SPELs with `Game.GetFormFromFile(0x000B26, "Plugin.esp")` (local id, no load-order byte) so no VMAD properties are needed. Remove leftover abilities in `OnEffectStart` and undo in `OnEffectFinish`.
- `install_plugin` and `validate_plugin` find scripts through the names in the plugins' `VMAD`, so any script name ships; `validate_plugin` errors if a script with a `Scripts/Source/<name>.psc` beside the plugin has no `.pex`.
- Put behaviour scripts on the MGEF, not the weapon: `clone_record` drops scripts from cloned base forms.

## Spells and standalone scripts
- `create_spell(target_plugin, editor_id, name, effects, kind="ability"|"spell"|"power"|"lesser_power", delivery=...)` builds a SPEL. `SPIT` is `<IIIfIIffI`: cost, flags (0 = auto-calc), spell type (0 spell, 2 power, 3 lesser power, 4 ability), charge time, cast type (0 constant, 1 fire-and-forget, 2 concentration), delivery (0 self, 1 touch, 2 aimed, 3 target actor, 4 target location), cast duration, range, perk. Abilities need constant-self MGEFs (e.g. `EnchFortify<School>ConstantSelf`, magnitude = % power); give them with `Actor.AddSpell`.
- `create_script(target_plugin, script_name, source)` compiles any script to `Scripts/` beside the plugin (source kept in `Scripts/Source`). It attaches nothing; a VMAD must name it.
- Find a spell by local id without VMAD properties: `Game.GetFormFromFile(0x000B26, "Plugin.esp") as Spell`.

## Weapon recipe (one-of-a-kind enchanted weapon on the ground)
1. Behaviour: `create_script_effect(kind="weapon", enchantment_editor_id=None, extra_effects=[{"effect": "EnchParalysisFFContact", "duration": 600}], ...)` builds the script MGEF and one ENCH holding the script effect (duration 1) plus the vanilla effects. Or `create_enchantment` when no script is needed.
2. Visuals (optional): `create_shader(target_plugin, editor_id, "#FFC832")` clones the Ebony Flesh shader and recolours it (EFSH `DATA` offset 16 fill key, 56 main colour). A script plays it with `(Game.GetFormFromFile(0x000B2C, "Plugin.esp") as EffectShader).Play(akTarget)`. Vanilla has no gold shader.
3. `create_unique_item(source="IronDagger", ..., enchantment=..., charge=..., cell=..., x, y, z, rotation_x=90)` clones, enchants and places in one call.
4. `item_console_commands(plugin)`, then `deploy_plugin(plugin)` (validate plus Vortex zip; `remove_loose=true` deletes stale Data copies).

Facts: an enchanted weapon REFR without `XCHG` spawns with no charge, so `place_item` writes `XCHG` = the base's `EAMT` unless `charge` is given. Vanilla paralysis releases the target when its duration ends, so a "permanent" hold needs a long duration plus `SetActorValue("Paralysis", 1)` in the script. A script effect on a weapon needs a non-zero duration in the ENCH. Searching `EFSH` works (it is in `LOAD_SIGS`); a MCP server restart is needed after code changes.

Pitfall (no charge bar, no recharge option): a weapon ENCH that showed its name and effects but no charge UI was fixed by making it mirror a working vanilla one. Keep `ENIT` cost = amount = about the vanilla value (200 for paralysis, never 0) and use a short duration (10) on the effect; a 3600-second duration and cost 226 coincided with the failure, and only the pair was changed, so it is not known which one mattered. Weapon script effects use the vanilla paralysis flags (`0x401403`), skill 0x12 and the MagicAlchHarmful keyword. If a weapon shows no charge, A/B test against a vanilla-enchanted copy made with `create_unique_item`.

## Spell family: effects, spells, tomes, scrolls, staves

One script effect can back every way of casting. In a single call: `create_spell_set(target_plugin, prefix, name, script_name, source, variants=[touch, bolt, ray, burst, nova], cell=..., x, y, z)` builds a script effect per delivery, a spell per variant, a teach-spell tome per spell, a scroll, a staff and places the tomes, scroll and staff on free floor spots. Build the pieces by hand with the tools below when you need something different.

| Piece | Tool | Template and gotchas |
|---|---|---|
| Effect per delivery | `create_script_effect(..., delivery="touch"\|"aimed"\|"ray"\|"self")` | Delivery and cast type live on the **MGEF**, not only the spell, and must match it. It copies the vanilla spell effect (`ParalysisFFAimed`, `ParalysisFFSelfArea`), which carry the flags, keywords and hidden conditions and have no random-chance condition. A ray is the aimed effect with MGEF cast type 2 (concentration). **Touch-delivery spells do not work for script effects in game** (the hand glows, nothing happens), so `delivery="touch"` builds a short-range ray: the script gets a `GetDistance` check (default 192 units, `touch_range`) at the top of `OnEffectStart`, and the spell is aimed/concentration. A self effect copies a hostile paralysis archetype: for a benign self spell set archetype to 1 (script), clear the hostile flag and drop its conditions with `edit_record`/`drop_conditions`. |
| Spell | `create_spell(kind="spell", delivery=..., cast="fire_and_forget"\|"concentration", cost=...)` | A hand spell also needs `MDOB` (display object) and `ETYP` (either hand); the tool adds both. Area comes from the effect's `area`. A burst reuses the aimed effect with an area; a nova is the self effect with an area. |
| Tome | `create_spell_tome(spell=..., text=...)` | Template `SpellTomeParalyze`: BOOK `DATA` flag 4 = teaches the spell, then the spell's form ID. One tome teaches one spell. Powers and lesser powers cannot be taught by tomes. |
| Scroll | `create_scroll(effects, delivery="aimed")` | Template `ParalyzeScroll`: `SPIT` cast type 3. |
| Staff | `create_enchantment(kind="staff")` + `create_unique_item("StaffParalyze", ..., replace_enchantment=True, charge=3000)` | Staff ENCH uses an aimed effect; its charge is `EAMT` and the placed copy gets `XCHG`. |

Placing: `find_free_spots(cell, x, y, z, count)` returns floor points clear of existing references (including your own plugin's); `place_items(bases, target_plugin, cell, x, y, z)` places a list of items on them; `get_cell_contents(cell, x=, y=, radius=)` shows what is nearby. Check named fields of any record with `get_record` (`fields`, `full=true` for whole hex) and change them with `edit_record(edits=[{"field": "cast_type", "value": "concentration"}])` (MGEF, SPEL/SCRL, ENCH, BOOK, WEAP charge, REFR charge) instead of byte offsets.

Verify in game (not proven by data alone). Confirmed in play: weapon-delivered script effect with a charge bar. Not yet confirmed: concentration beams re-applying the effect (an `OnEffectStart` script fires once per cast, so use `OnUpdate` for continuous effects), a self-delivered effect with an area (nova), the scroll and tome templates, and per-delivery effect shaders. Test with `item_console_commands(plugin)`: `player.addspell` for spells, `player.additem` for tomes, scrolls and staves, `player.moveto` to teleport to each placed item, `coc <cell>` for the room.

Imitating a vanilla effect: `create_script_effect(..., base_effect="EnchParalysisFFContact", drop_conditions=[0])` copies the vanilla effect's data, keywords and conditions and attaches your script. Vanilla paralysis carries hidden conditions: `GetRandomPercent <= 25` (a 25% chance, so it is not a 100% effect), not-a-dragon and not-`ImmuneParalysis`. Drop the first for a guaranteed hit. Debug with `diff_records` (names MGEF DATA fields and decodes conditions), `list_conditions` (own records, with function names), `copy_subrecords`, `drop_conditions` and `remove_records` (deletes test items and their placed refs). `validate_plugin` warns about header versions other than 1.70, zero-cost ENCHs, enchanted weapons without `EAMT` and placed ones without `XCHG`.

## Plugin header version

Plugins must have HEDR version 1.70. esplib defaults to 1.71, and with that the game ignores armor biped slots and addons: worn circlets have no model and stack with vanilla ones. Tools create plugins through `new_plugin` in `tools/core/plugin_edit.py`, which writes 1.70. Repair an older plugin with `p.header.version = 1.7; p.modified = True; p.save()`.

## Reference: script MGEF and weather
- MGEF `DATA` (152 bytes) offsets: 12 magic skill (i32, -1 none), 16 resist AV, 36 shader form ID (must be 0 in a new plugin with no masters), 64 archetype (1 Script, 34 Peak Value Mod), 68 actor value (-1 none), 80 cast type, 84 delivery.
- Weather classification (`Weather.GetClassification()`): 0 pleasant, 1 cloudy, 2 rainy, 3 snow, -1 none. It cannot read real temperature, so a clear sky in a cold region counts as pleasant.
- Power mod actor values are unreliable by name (no `AVDestructionPowerMod`); prefer ability spells built from Fortify MGEFs.
