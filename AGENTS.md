# Agent guidelines

## Edit plugins in place; don't rebuild from scratch

Build scripts (for example `output/build_questpoc.py`) delete and recreate the plugin and can take many minutes.
They are for the first run only.

- For a spot change to an existing plugin, call the editing tools directly against the existing `.esp`
  (`edit_npc`, `edit_record`, `edit_quest_alias`, `add_quest_stage`, `add_npc_dialogue`, `place_item`, `remove_chat`,
  `staged_fight` with `replace`, and so on). Use `snapshot_plugin` first if the change is risky.
- Do not re-run a full build script just to apply one change. Rebuilding also changes form IDs, which forces a fresh
  save in game.
- Still update the build script with the same change so a future from-scratch build matches, but do not run it
  unless the user asks or the plugin does not exist yet.
- After editing in place, run `validate_plugin`, then `package_plugin` and `install_plugin` as needed.
- Always package a plugin (`package_plugin` or `deploy_plugin`) every time it is updated, so the user can install the
  new version through Vortex. Check the zip also holds any loose files the packager might skip.
- Never run a build script that may take more than a few minutes synchronously; run it detached or ask first.
