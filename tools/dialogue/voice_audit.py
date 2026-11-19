"""Per-NPC voice coverage of a plugin: voice type, recorded lines, dialogue categories, and combat lines."""
import re
from pathlib import Path

from tools.dialogue.dialogue_tools import DialogueTools

AUDIO_EXTS = {'.wav', '.fuz', '.xwm'}
# Combat reaction subtypes added by combat_lines: attack, power attack, bash, block, hit, taunt, flee, bleedout, death.
COMBAT_CATEGORIES = ('ATCK', 'POAT', 'BASH', 'BLOC', 'HIT_', 'TAUT', 'FLEE', 'BLED', 'DETH')
INFO_ID = re.compile(r'_([0-9a-f]{8})_\d+$', re.I)


def voice_files(plugin_name, roots):
    """{info id (low 24 bits): {voice type folder, ...}} for recorded files under Sound/Voice/<plugin>/ in each root."""
    found = {}
    for root in roots:
        base = Path(root) / 'Sound' / 'Voice' / plugin_name
        if not base.is_dir():
            continue
        for folder in (d for d in base.iterdir() if d.is_dir()):
            for f in folder.iterdir():
                m = INFO_ID.search(f.stem)
                if m and f.suffix.lower() in AUDIO_EXTS:
                    found.setdefault(int(m.group(1), 16) & 0xFFFFFF, set()).add(folder.name)
    return found


def audit_voice(model, tools, plugin_path, data_dir=None):
    """Voice coverage for every NPC the plugin defines.

    Warns when an NPC has recorded voice lines or a custom voice type but no combat lines (vanilla combat barks are
    gated by voice type, so it fights silently or in the wrong voice), and when its recordings sit under a voice
    type folder other than its own (the game never plays them)."""
    plugin_path = Path(plugin_path)
    name = plugin_path.name.lower()
    roots = [plugin_path.parent] + ([Path(data_dir)] if data_dir else [])
    files = voice_files(plugin_path.name, roots)
    dialogue = DialogueTools(model)
    npcs, issues = [], []
    for rec in model.by_sig['NPC_'].values():
        if rec.plugin.file_path.name.lower() != name:
            continue
        label = rec.editor_id or f"{model.rid(rec):08X}"
        voice = tools.one(rec, 'VTCK') or {}
        vt_rec = model.get(int(voice['form_id'], 16)) if voice.get('form_id') else None
        custom_voice = vt_rec is not None and vt_rec.plugin.file_path.name.lower() == name
        vt_name = voice.get('editor_id') or voice.get('form_id')
        res = dialogue.lines(rec, include_generic=False)
        lines = res['direct'] + res['scene']
        categories = {}
        for ln in lines:
            categories[ln.get('category') or '?'] = categories.get(ln.get('category') or '?', 0) + 1
        recorded = [ln for ln in lines if int(ln['info'], 16) & 0xFFFFFF in files]
        folders = {f for ln in recorded for f in files[int(ln['info'], 16) & 0xFFFFFF]}
        combat = sum(categories.get(c, 0) for c in COMBAT_CATEGORIES)
        npcs.append({'npc': label, 'voice_type': vt_name, 'custom_voice_type': custom_voice,
                     'lines': len(lines), 'recorded': len(recorded), 'combat_lines': combat,
                     'categories': dict(sorted(categories.items()))})
        if not combat and (recorded or custom_voice):
            why = 'a custom voice type' if custom_voice else f"{len(recorded)} recorded line(s)"
            issues.append(f"{label}: has {why} but no combat lines; vanilla combat barks of voice type {vt_name} "
                          "play instead (use combat_lines: true)")
        if vt_name and folders and vt_name.lower() not in {f.lower() for f in folders}:
            issues.append(f"{label}: recordings are in voice folder(s) {sorted(folders)} but the NPC's voice type "
                          f"is {vt_name}, so the game will not play them")
    return {'plugin': plugin_path.name, 'npcs': npcs, 'issues': issues, 'ok': not issues}
