"""NPC lookup helpers: decode an NPC_ record and gather related data."""
import struct

from esplib.vmad import VmadObject
from tools.core.quest_model import ALIAS_FORM_SUBS, u32, vmad_objects

SKILLS = ('OneHanded', 'TwoHanded', 'Marksman', 'Block', 'Smithing', 'HeavyArmor', 'LightArmor',
          'Pickpocket', 'Lockpicking', 'Sneak', 'Alchemy', 'Speech', 'Alteration', 'Conjuration',
          'Destruction', 'Illusion', 'Restoration', 'Enchanting')
ACBS_FLAGS = {0: 'Female', 1: 'Essential', 2: 'Is CharGen Face Preset', 3: 'Respawn',
              4: 'Auto-calc Stats', 5: 'Unique', 6: "Doesn't Affect Stealth Meter",
              7: 'PC Level Mult', 8: 'Use Template', 11: 'Protected', 14: 'Summonable',
              16: "Doesn't Bleed", 18: 'Bleedout Override', 19: 'Opposite Gender Anims',
              20: 'Simple Actor', 23: 'Is Ghost', 28: 'Invulnerable'}
TEMPLATE_FLAGS = {0: 'Traits', 1: 'Stats', 2: 'Factions', 3: 'Spell List', 4: 'AI Data',
                  5: 'AI Packages', 6: 'Model/Animation', 7: 'Base Data', 8: 'Inventory',
                  9: 'Script', 10: 'Def Pack List', 11: 'Attack Data', 12: 'Keywords'}
AGGRESSION = ('Unaggressive', 'Aggressive', 'Very Aggressive', 'Frenzied')
CONFIDENCE = ('Cowardly', 'Cautious', 'Average', 'Brave', 'Foolhardy')
MORALITY = ('Any Crime', 'Violence Against Enemies', 'Property Crime Only', 'No Crime')
ASSISTANCE = ('Helps Nobody', 'Helps Allies', 'Helps Friends and Allies')


def bits(value, names):
    """Names of the flag bits set in value; names maps bit index to label."""
    return [n for b, n in names.items() if value & (1 << b)]


def pick(seq, i):
    """seq[i], or the raw index when it falls outside a known enum."""
    return seq[i] if i < len(seq) else i


class NpcTools:
    """Decodes NPC_ records into readable dicts: stats, AI, factions, items, scripts and where else the NPC is used."""

    def __init__(self, model):
        self.m = model

    def ref(self, fid):
        """Describe an absolute form ID as a dict (type, editor ID, name); None for 0, resolved False if unknown."""
        if not fid:
            return None
        rec = self.m.get(fid)
        if rec is None:
            return {'form_id': f"{fid:08X}", 'resolved': False}
        out = {'form_id': f"{fid:08X}", 'type': rec.signature}
        if rec.editor_id:
            out['editor_id'] = rec.editor_id
        name = rec.full_name
        if name:
            out['name'] = name
        return out

    def one(self, rec, sig):
        """ref() for the form ID in the record's first sig subrecord, remapped from plugin-local to absolute."""
        sr = rec.get_subrecord(sig)
        return self.ref(self.m.absid(rec.plugin, u32(sr.data))) if sr and len(sr.data) >= 4 else None

    def many(self, rec, sig):
        """ref() for every sig subrecord on the record, in order."""
        return [self.ref(self.m.absid(rec.plugin, u32(sr.data)))
                for sr in rec.subrecords if sr.signature == sig and len(sr.data) >= 4]

    def find(self, query, limit=20):
        """NPC_ records matching a form ID, exact editor ID, or name/editor ID substring."""
        q = query.strip()
        npcs = self.m.by_sig['NPC_']
        try:
            raw = int(q, 16) if q.lower().startswith('0x') or (len(q) == 8 and q.isalnum()) else None
        except ValueError:
            raw = None
        if raw is not None:
            if raw in npcs:
                return [npcs[raw]]
            hits = [r for f, r in npcs.items() if f & 0xFFFFFF == raw & 0xFFFFFF]
            if hits:
                return hits[:limit]
        low = q.lower()
        exact = [r for r in npcs.values() if (r.editor_id or '').lower() == low]
        if exact:
            return exact
        hits = [r for r in npcs.values()
                if low in (r.editor_id or '').lower() or low in (r.full_name or '').lower()]
        return sorted(hits, key=lambda r: (r.editor_id or '').lower())[:limit]

    def summary(self, rec):
        """form_id, editor_id, name and source plugin, omitting empty values."""
        d = {'form_id': f"{self.m.rid(rec):08X}", 'editor_id': rec.editor_id,
             'name': rec.full_name, 'plugin': rec.plugin.file_path.name}
        return {k: v for k, v in d.items() if v}

    def decode(self, rec):
        """Full readable view of an NPC_ record; empty sections are dropped. Level is a multiplier of x/1000 when the PC level mult flag is set."""
        m = self.m
        fid = m.rid(rec)
        out = self.summary(rec)
        out['record_flags'] = f"0x{int(rec.flags):08X}"

        acbs = rec.get_subrecord('ACBS')
        if acbs and len(acbs.data) >= 24:
            (flags, mag, stam, level, cmin, cmax, speed, disp, tflags, health, bleed) = \
                struct.unpack_from('<IhhHHHHhHhH', acbs.data)
            out['stats'] = {
                'flags': bits(flags, ACBS_FLAGS),
                'level': f"x{level / 1000:g} (player level multiplier)" if flags & 0x80 else level,
                'calc_min_level': cmin, 'calc_max_level': cmax,
                'speed_multiplier': speed, 'disposition_base': disp,
                'magicka_offset': mag, 'stamina_offset': stam, 'health_offset': health,
                'bleedout_override': bleed if flags & (1 << 18) else None,
            }
            out['template_flags'] = bits(tflags, TEMPLATE_FLAGS)

        out['race'] = self.one(rec, 'RNAM')
        out['class'] = self.one(rec, 'CNAM')
        out['voice_type'] = self.one(rec, 'VTCK')
        out['template'] = self.one(rec, 'TPLT')
        out['combat_style'] = self.one(rec, 'ZNAM')
        out['death_item'] = self.one(rec, 'INAM')
        out['worn_armor'] = self.one(rec, 'WNAM')

        for key, sig in (('default_outfit', 'DOFT'), ('sleep_outfit', 'SOFT')):
            ref = self.one(rec, sig)
            if ref:
                ofid = m.absid(rec.plugin, u32(rec.get_subrecord(sig).data))
                orec = m.get(ofid)
                inam = orec.get_subrecord('INAM') if orec else None
                if inam:
                    ref['items'] = [self.ref(m.absid(orec.plugin, v))
                                    for v in struct.unpack_from(f'<{len(inam.data) // 4}I', inam.data)]
            out[key] = ref

        kw = rec.get_subrecord('KWDA')
        if kw:
            out['keywords'] = [self.ref(m.absid(rec.plugin, v))
                               for v in struct.unpack_from(f'<{len(kw.data) // 4}I', kw.data)]

        out['factions'] = [
            {**self.ref(m.absid(rec.plugin, u32(sr.data))), 'rank': struct.unpack_from('<b', sr.data, 4)[0]}
            for sr in rec.subrecords if sr.signature == 'SNAM' and len(sr.data) >= 5]
        out['spells'] = self.many(rec, 'SPLO')
        out['perks'] = [
            {**self.ref(m.absid(rec.plugin, u32(sr.data))), 'rank': sr.data[4] if len(sr.data) > 4 else 0}
            for sr in rec.subrecords if sr.signature == 'PRKR' and len(sr.data) >= 4]
        out['ai_packages'] = self.many(rec, 'PKID')
        out['inventory'] = [
            {**self.ref(m.absid(rec.plugin, u32(sr.data))), 'count': struct.unpack_from('<i', sr.data, 4)[0]}
            for sr in rec.subrecords if sr.signature == 'CNTO' and len(sr.data) >= 8]

        dnam = rec.get_subrecord('DNAM')
        if dnam and len(dnam.data) >= 42:
            vals = struct.unpack_from('<18B', dnam.data, 0)
            offs = struct.unpack_from('<18B', dnam.data, 18)
            hp, mp, sp = struct.unpack_from('<3H', dnam.data, 36)
            out['skills'] = {n: {'value': v, 'offset': o} for n, v, o in zip(SKILLS, vals, offs)}
            out['base_attributes'] = {'health': hp, 'magicka': mp, 'stamina': sp}

        aidt = rec.get_subrecord('AIDT')
        if aidt and len(aidt.data) >= 6:
            a, c, e, mo, mood, assist = aidt.data[:6]
            out['ai'] = {'aggression': pick(AGGRESSION, a), 'confidence': pick(CONFIDENCE, c),
                         'energy_level': e, 'morality': pick(MORALITY, mo), 'mood': mood,
                         'assistance': pick(ASSISTANCE, assist)}

        vm = m.vmad(rec)
        if vm is not None:
            out['scripts'] = [{
                'name': s.name,
                'properties': {p.name: self.value(p.value) for p in s.properties},
            } for s in vm.scripts]

        out['related'] = self.related(fid)
        return {k: v for k, v in out.items() if v not in (None, [], {})}

    def value(self, v):
        """Script property value made JSON-friendly: object references become ref() dicts, lists recurse."""
        if isinstance(v, VmadObject):
            return self.ref(v.form_id) or 'None'
        if isinstance(v, (list, tuple)):
            return [self.value(x) for x in v]
        return v

    def related(self, fid):
        """Quests (aliases and script properties), dialogue lines spoken, and NPCs templated from this NPC."""
        m = self.m
        rel = {}

        quests = []
        for qfid, q in m.by_sig['QUST'].items():
            hits = []
            cur = None
            for sr in q.subrecords:
                s = sr.signature
                if s in ('ALST', 'ALLS'):
                    cur = u32(sr.data)
                elif s == 'ALID':
                    cur = sr.data.rstrip(b'\0').decode('cp1252', 'replace')
                elif s in ALIAS_FORM_SUBS and len(sr.data) >= 4 and m.absid(q.plugin, u32(sr.data)) == fid:
                    hits.append(f"alias {cur}: {ALIAS_FORM_SUBS[s]}")
            if any(v.form_id == fid for v in vmad_objects(m.vmad(q))):
                hits.append('quest script property')
            if hits:
                quests.append({**self.ref(qfid), 'via': hits})
        if quests:
            rel['quests'] = quests

        topics = []
        for dial, infos in (t for lst in m.dialogue.values() for t in lst):
            n = 0
            for info in infos:
                spk = info.get_subrecord('ANAM')
                if spk and m.absid(info.plugin, u32(spk.data)) == fid:
                    n += 1
            if n:
                qn = dial.get_subrecord('QNAM')
                topics.append({'topic': dial.editor_id or f"{m.rid(dial):08X}", 'responses': n,
                               'quest': self.ref(m.absid(dial.plugin, u32(qn.data))) if qn else None})
        if topics:
            rel['dialogue_as_speaker'] = topics

        users = [self.summary(r) for r in m.by_sig['NPC_'].values()
                 if (t := r.get_subrecord('TPLT')) is not None and m.absid(r.plugin, u32(t.data)) == fid]
        if users:
            rel['used_as_template_by'] = users[:50]
        return rel
