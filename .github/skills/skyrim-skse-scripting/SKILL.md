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
2. Design with vanilla first: `RegisterForSingleUpdate` polling, ability SPELs added and removed, `OnEffectStart` / `OnEffectFinish`, `Game.FindClosestActor`, `IsInDialogueWithPlayer`, quest stages and aliases, `OnHit`, `OnItemAdded` / `OnItemRemoved`, `OnLocationChange`. Say what the vanilla version gives up, if anything.
3. If SKSE is **present** and an idea needs it, or SKSE would clearly do it better, **ask the user** with `ask_user` before adding any code, e.g. "this needs `RegisterForMenu`. SKSE is installed. Use it, or keep vanilla with polling?". Describe the dependency cost in one line. Do the same when listing what is possible in the engine: label ideas "vanilla" or "needs SKSE".
4. If SKSE is **absent**, do not propose SKSE-only ideas as options. Offer the nearest vanilla variant. Mention SKSE only if the user's idea has no vanilla equivalent, and say they would have to install it.
5. When SKSE is used, say so in the plugin's description or README, and run `detect_skse` after building (`output_scripts_using_skse` should list the script).

## Typical SKSE-only gaps

Menu open/close events (`RegisterForMenu`, e.g. detecting a finished barter), key presses (`RegisterForKey`), animation events such as sprint footsteps (`RegisterForAnimationEvent(actor, "FootSprintLeft")`), `ModEvent` messaging, `StringUtil`, and JSON or file storage (PapyrusUtil). Try an idea with `check_script` before building, and build a whole item (boots, weapon) with `create_enchanted_item`.

## Rules

- Never assume SKSE from the user's setup alone. Run `detect_skse`.
- SKSE being installed does not mean it is wanted: still ask.
- A vanilla approximation that is good enough beats an SKSE hook.
