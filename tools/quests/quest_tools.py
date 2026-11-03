"""Quest lookup helpers: decode a QUST record and gather related data."""
import struct

from tools.npcs.npc_tools import NpcTools, bits
from tools.core.quest_model import ALIAS_FORM_SUBS, u32, vmad_objects

QUEST_TYPES = ('None', 'Main Quest', 'Mages Guild', 'Thieves Guild', 'Dark Brotherhood',
               'Companion Quests', 'Miscellaneous', 'Daedric Quests', 'Side Quests',
               'Civil War', 'DLC01 Vampire', 'DLC02 Dragonborn')
ALIAS_FLAGS = {0: 'Reserved', 1: 'Optional', 2: 'Quest Object', 3: 'Allow Reuse in Quest',
               4: 'Allow Dead', 5: 'In Loaded Area', 6: 'Essential', 7: 'Allow Disabled'}


class QuestTools:
    """Read-only lookup and decoding of QUST records from a loaded plugin model."""
    def __init__(self, model):
        self.m = model
        self.n = NpcTools(model)

    def find(self, query, limit=20):
        """Look up quests by form id (full or load-order-agnostic), exact editor id, then editor id or name substring."""
        q = query.strip()
        quests = self.m.by_sig['QUST']
        try:
            raw = int(q, 16) if q.lower().startswith('0x') or (len(q) == 8 and q.isalnum()) else None
        except ValueError:
            raw = None
        if raw is not None:
            if raw in quests:
                return [quests[raw]]
            hits = [r for f, r in quests.items() if f & 0xFFFFFF == raw & 0xFFFFFF]
            if hits:
                return hits[:limit]
        low = q.lower()
        exact = [r for r in quests.values() if (r.editor_id or '').lower() == low]
        if exact:
            return exact
        hits = [r for r in quests.values()
                if low in (r.editor_id or '').lower() or low in (r.full_name or '').lower()]
        return sorted(hits, key=lambda r: (r.editor_id or '').lower())[:limit]

    def summary(self, rec):
        """The NPC-style summary plus quest_type from DNAM."""
        out = self.n.summary(rec)
        dnam = rec.get_subrecord('DNAM')
        if dnam and len(dnam.data) >= 12:
            t = struct.unpack_from('<I', dnam.data, 8)[0]
            out['quest_type'] = QUEST_TYPES[t] if t < len(QUEST_TYPES) else t
        return out

    def text(self, rec, sr):
        return self.m.string(sr.data, rec)

    def decode(self, rec, include_responses=False, max_topics=200):
        """Full quest structure as a dict: flags, stages, objectives, aliases, scripts, scenes and dialogue topics (capped at max_topics)."""
        m, n = self.m, self.n
        qfid = m.rid(rec)
        out = self.summary(rec)
        dnam = rec.get_subrecord('DNAM')
        if dnam and len(dnam.data) >= 4:
            flags, priority = struct.unpack_from('<HB', dnam.data)
            out['priority'] = priority
            out['data_flags'] = f"0x{flags:04X}"
            out['start_game_enabled'] = bool(flags & 1)
        ev = rec.get_subrecord('ENAM')
        if ev:
            out['event'] = ev.data.rstrip(b'\0').decode('cp1252', 'replace')

        subs = rec.subrecords
        conds = sum(1 for s in subs[:next((i for i, s in enumerate(subs) if s.signature == 'NEXT'), 0)]
                    if s.signature == 'CTDA')
        out['quest_condition_count'] = conds or None

        vm = m.vmad(rec)
        frag_by_stage = {}
        if vm is not None:
            out['scripts'] = [self.script(s) for s in vm.scripts]
            fd = vm.fragment_data
            if fd is not None:
                for fr in fd.fragments or []:
                    st = getattr(fr, 'quest_stage', None)
                    frag_by_stage.setdefault(st, []).append(f"{fr.script_name}.{fr.fragment_name}")
                out['fragment_script'] = getattr(fd, 'filename', None)

        aliases = self.aliases(rec, vm)
        names = {a['id']: a['name'] for a in aliases}
        out['aliases'] = aliases

        stages, cur = [], None
        for s in subs:
            if s.signature == 'INDX' and len(s.data) >= 2:
                idx = struct.unpack_from('<H', s.data)[0]
                cur = {'stage': idx, 'log': [], 'fragments': frag_by_stage.get(idx, [])}
                stages.append(cur)
            elif s.signature == 'CNAM' and cur is not None:
                t = self.text(rec, s)
                if t:
                    cur['log'].append(t)
            elif s.signature in ('QOBJ', 'ANAM', 'ALST', 'ALLS'):
                cur = None
        out['stages'] = [{k: v for k, v in st.items() if v or k == 'stage'} for st in stages]

        objectives, cur = [], None
        for s in subs:
            if s.signature == 'QOBJ':
                cur = {'index': struct.unpack_from('<H', s.data)[0] if len(s.data) >= 2 else 0,
                       'text': None, 'targets': []}
                objectives.append(cur)
            elif cur is not None and s.signature == 'NNAM':
                cur['text'] = self.text(rec, s)
            elif cur is not None and s.signature == 'QSTA' and len(s.data) >= 4:
                al = u32(s.data)
                cur['targets'].append(names.get(al, f"alias {al}"))
            elif s.signature in ('ANAM', 'ALST', 'ALLS'):
                cur = None
        out['objectives'] = [{k: v for k, v in o.items() if v or k == 'index'} for o in objectives]

        out['scenes'] = []
        for sc in m.scenes.get(qfid, []):
            entry = n.ref(m.rid(sc))
            svm = m.vmad(sc)
            if svm is not None and svm.scripts:
                entry['scripts'] = [s.name for s in svm.scripts]
            out['scenes'].append(entry)

        topics, speakers, total = [], {}, 0
        for dial, infos in m.dialogue.get(qfid, []):
            total += len(infos)
            t = {'topic': dial.editor_id or f"{m.rid(dial):08X}", 'prompt': dial.full_name,
                 'responses': len(infos)}
            if include_responses:
                lines = []
                for info in infos:
                    resp = next((m.string(s.data, info) for s in info.subrecords if s.signature == 'NAM1'), None)
                    if resp:
                        lines.append(resp)
                t['lines'] = lines
            for info in infos:
                spk = info.get_subrecord('ANAM')
                if spk and u32(spk.data):
                    sf = m.absid(info.plugin, u32(spk.data))
                    speakers[sf] = speakers.get(sf, 0) + 1
            topics.append({k: v for k, v in t.items() if v})
        if topics:
            out['dialogue'] = {'topic_count': len(topics), 'response_count': total,
                               'topics': topics[:max_topics]}
        if speakers:
            out['speakers'] = [{**n.ref(f), 'responses': c} for f, c in
                               sorted(speakers.items(), key=lambda kv: -kv[1])]

        out['involved'] = self.involved(rec, vm, aliases)
        return {k: v for k, v in out.items() if v not in (None, [], {})}

    def script(self, s):
        """Script summary; Alias_* properties (self-references to the quest) are collapsed to names."""
        props, alias_props = {}, []
        for p in s.properties:
            if p.name.startswith('Alias_'):
                alias_props.append(p.name[6:])
            else:
                props[p.name] = self.n.value(p.value)
        out = {'name': s.name, 'properties': props}
        if alias_props:
            out['alias_properties'] = alias_props
        return out

    def aliases(self, rec, vm):
        """Decode the ALST/ALLS alias blocks, attaching any alias scripts found in the VMAD."""
        m, n = self.m, self.n
        scripts = {}
        if vm is not None:
            for a in vm.alias_scripts:
                scripts.setdefault(a.alias_obj.alias, []).extend(self.script(s) for s in a.scripts)
        out, cur = [], None
        for sr in rec.subrecords:
            s = sr.signature
            if s in ('ALST', 'ALLS'):
                cur = {'id': u32(sr.data), 'name': '', 'type': 'reference' if s == 'ALST' else 'location',
                       'flags': [], 'fills': []}
                out.append(cur)
            elif cur is None:
                continue
            elif s == 'ALID':
                cur['name'] = sr.data.rstrip(b'\0').decode('cp1252', 'replace')
            elif s == 'FNAM' and len(sr.data) >= 4:
                cur['flags'] = bits(u32(sr.data), ALIAS_FLAGS)
            elif s in ALIAS_FORM_SUBS and len(sr.data) >= 4:
                fid = m.absid(rec.plugin, u32(sr.data))
                if fid:
                    cur['fills'].append({'how': ALIAS_FORM_SUBS[s], **n.ref(fid)})
            elif s == 'ALED':
                cur['scripts'] = scripts.get(cur['id'])
                cur = None
        return [{k: v for k, v in a.items() if v or k == 'id'} for a in out]

    def involved(self, rec, vm, aliases):
        """NPCs and locations referenced by aliases or quest-script properties."""
        found = {}
        for a in aliases:
            for f in a.get('fills', []):
                found[f['form_id']] = f
        for v in vmad_objects(vm):
            ref = self.n.ref(v.form_id)
            if ref:
                found.setdefault(ref['form_id'], ref)
        return {
            'npcs': [f for f in found.values() if f.get('type') == 'NPC_'],
            'locations': [f for f in found.values() if f.get('type') == 'LCTN'],
        }
