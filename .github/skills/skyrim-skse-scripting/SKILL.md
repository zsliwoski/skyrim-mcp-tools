---
name: skyrim-skse-scripting
description: "Use before writing any new Papyrus, and when assessing what is possible in the engine: check whether the Skyrim Script Extender (SKSE) is present and whether its hooks are worth asking the user about. Triggers: what can we do in script, is this possible, detect a menu closing or a sale, mod events, SKSE, script extender, PapyrusUtil, detect_skse, a vanilla script can't do X."
---

# SKSE and Papyrus

**Vanilla Papyrus is the default.** Use SKSE only when an idea cannot be done without it, and only with the user's agreement. SKSE adds a runtime dependency for everyone who installs the plugin, breaks on game updates, and does not exist on consoles.

## Steps

1. Call `detect_skse` once before writing a new script or answering "is this possible". It reports:
   - `installed`: loader and runtime DLLs beside the game, or SKSE script files in `Data/Scripts`.
   - `plugins`: DLLs in `Data/SKSE/Plugins` (PapyrusUtil, PowerOfThree's Papyrus Extender, etc. widen what is available).
   - `compiler_sees_skse_sources`: whether `ModEvent.psc` and friends are on the compiler's include path. `compile_script` (so every script tool) automatically adds `Data/Scripts/Source` and any folders in `SKYRIM_SKSE_SOURCES` once they contain `ModEvent.psc`, ahead of the vanilla sources (SKSE ships extended `Form.psc` and `Actor.psc`). If SKSE is installed but this is empty, SKSE calls will not compile: tell the user to install the SKSE source files (they ship in the SKSE download's `Data/Scripts/Source`) or point `SKYRIM_SKSE_SOURCES` at them.
   - `output_scripts_using_skse`: scripts already in `output/` that depend on SKSE types. Mention these so an existing dependency is not added to by accident.
2. Before writing any hook or call, confirm it exists with `api_get` (e.g. `api_get('Actor', 'OnHit')`; an empty result means it does not exist; `skse: true` means it needs SKSE) or `api_search` for intent. This needs the API database (`python -m tools.papyrus_api build scripts`); if missing, the error says how to build it.
3. Design with vanilla first: `RegisterForSingleUpdate` polling, ability SPELs added and removed, `OnEffectStart` / `OnEffectFinish`, `Game.FindClosestActor`, `IsInDialogueWithPlayer`, quest stages and aliases, `OnHit`, `OnItemAdded` / `OnItemRemoved`, `OnLocationChange`. Say what the vanilla version gives up, if anything.
4. If SKSE is **present** and an idea needs it, or SKSE would clearly do it better, **ask the user** with `ask_user` before adding any code, e.g. "this needs `RegisterForMenu`. SKSE is installed. Use it, or keep vanilla with polling?". Describe the dependency cost in one line. Do the same when listing what is possible in the engine: label ideas "vanilla" or "needs SKSE".
5. If SKSE is **absent**, do not propose SKSE-only ideas as options. Offer the nearest vanilla variant. Mention SKSE only if the user's idea has no vanilla equivalent, and say they would have to install it.
6. When SKSE is used, say so in the plugin's description or README, and run `detect_skse` after building (`output_scripts_using_skse` should list the script).

## Typical SKSE-only gaps

`Actor.IsSwimming()` (and other Actor state checks that look vanilla: confirm each with `api_get`), menu open/close events (`RegisterForMenu`, e.g. detecting a finished barter), key presses (`RegisterForKey`), animation events such as sprint footsteps (`RegisterForAnimationEvent(actor, "FootSprintLeft")`), `ModEvent` messaging, `StringUtil`, and JSON or file storage (PapyrusUtil). Try an idea with `check_script` before building, and build a whole item (boots, weapon) with `create_enchanted_item`.

## Approved behaviour is a contract

Once the user has approved an effect, trigger or delivery mechanism, build exactly that. Never swap it for something different because the approved version is not possible in vanilla Papyrus.

- **Verify the whole plan before building.** For every function, event and condition the script will use, run `api_get` / `api_search` (and `check_script` on the draft) *before* any create or place call. Check the `skse` flag on each one. Do not rely on memory: `Actor.IsSwimming()` looks vanilla and is SKSE-only.
- **If the approved behaviour needs SKSE**, stop and `ask_user` (see step 4): use SKSE for the exact behaviour, or choose a different behaviour. Do not build anything until they answer.
- **A vanilla substitute is a change of design, not an approximation.** Changing the trigger (swimming to rain), the delivery (on hit to polling, touch to ray), a stat, magnitude or drawback, or dropping part of an effect all require asking first. State what is lost and offer options, with the exact-behaviour SKSE option first when SKSE is installed.
- A vanilla route is only fine without asking when it delivers the same behaviour the user approved (e.g. polling `OnUpdate` for a vanilla-detectable condition). Hedged wording such as "it works, a bit crude" does not make a different behaviour acceptable.
- Vanilla can often detect the same condition another way (a spell or ability with a condition function such as `IsSwimming`, since condition functions are vanilla even where the Papyrus call is SKSE). Look for that before asking. If it matches the approved behaviour, use it and say how.
- After building, tell the user exactly what was built. List every difference from the approved design, or state that there are none.

## Rules

- Never assume SKSE from the user's setup alone. Run `detect_skse`.
- SKSE being installed does not mean it is wanted: still ask.
- Prefer a vanilla approach only when it gives the same behaviour the user approved. If it does not, ask; do not substitute on your own.
