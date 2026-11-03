"""Find every dialogue line (INFO response) an NPC speaks: direct, scene, and generic."""
import struct

from tools.npcs.npc_tools import NpcTools
from tools.core.quest_model import u32

FN_GET_IS_ID = 72
FN_GET_IS_ALIAS_REF = 566
FN_GET_IS_VOICE_TYPE = 426
FN_GET_IS_RACE = 69
FN_IN_FACTION = 71
SPECIFIC_FNS = (FN_GET_IS_ID, FN_GET_IS_ALIAS_REF)
GENERIC_FNS = {FN_GET_IS_VOICE_TYPE: 'voice type', FN_GET_IS_RACE: 'race', FN_IN_FACTION: 'faction'}


def conditions(rec):
    """Yield (function, param1, equals_true) for each CTDA subrecord."""
    for sr in rec.subrecords:
        if sr.signature == 'CTDA' and len(sr.data) >= 16:
            value = struct.unpack_from('<f', sr.data, 4)[0]
            fn, = struct.unpack_from('<H', sr.data, 8)
            p1, = struct.unpack_from('<I', sr.data, 12)
            yield fn, p1, (sr.data[0] >> 5) == 0 and value != 0


class DialogueTools:
    """Collects an NPC's spoken INFO lines from direct, scene and generic (voice/race/faction) matches."""
    def __init__(self, model):
        self.m = model
        self.n = NpcTools(model)
        self.topics = {model.rid(d): (d, i) for lst in model.dialogue.values() for d, i in lst}

    def aliases_of(self, qrec, npc_fid):
        """Alias IDs in a quest filled by the NPC as a unique actor."""
        ids, cur = set(), None
        for sr in qrec.subrecords:
            if sr.signature in ('ALST', 'ALLS'):
                cur = u32(sr.data)
            elif sr.signature == 'ALUA' and cur is not None \
                    and self.m.absid(qrec.plugin, u32(sr.data)) == npc_fid:
                ids.add(cur)
        return ids

    def entry(self, qname, dial, info, how, **extra):
        """Result row for one INFO: quest, topic, prompt, SNAM category (e.g. CUST), info ID, match reason and NAM1 text; empty values dropped."""
        m = self.m
        text = [t for sr in info.subrecords if sr.signature == 'NAM1'
                for t in [m.string(sr.data, info)] if t]
        snam = dial.get_subrecord('SNAM')
        e = {'quest': qname, 'topic': dial.editor_id or f"{m.rid(dial):08X}", 'prompt': dial.full_name,
             'category': snam.data.decode('ascii', 'replace') if snam and len(snam.data) == 4 else None,
             'info': f"{m.rid(info):08X}", 'matched_by': how, **extra, 'text': text}
        return {k: v for k, v in e.items() if v}

    def lines(self, npc_rec, include_generic=True):
        """{'direct', 'scene', 'generic'} line lists for an NPC; a line found by an earlier route is not repeated in scene."""
        m = self.m
        npc_fid = m.rid(npc_rec)
        voice = self.n.one(npc_rec, 'VTCK')
        race = self.n.one(npc_rec, 'RNAM')
        fac = {m.absid(npc_rec.plugin, u32(s.data)) for s in npc_rec.subrecords
               if s.signature == 'SNAM' and len(s.data) >= 5}
        generic_keys = {FN_GET_IS_VOICE_TYPE: {int(voice['form_id'], 16)} if voice else set(),
                        FN_GET_IS_RACE: {int(race['form_id'], 16)} if race else set(),
                        FN_IN_FACTION: fac}

        direct, scene_lines, generic, claimed = [], [], [], set()
        alias_map = {}  # quest fid -> alias ids the NPC fills
        for qfid, topics in m.dialogue.items():
            qrec = m.get(qfid)
            aliases = self.aliases_of(qrec, npc_fid) if qrec else set()
            if aliases:
                alias_map[qfid] = aliases
            qname = (qrec.editor_id or f"{qfid:08X}") if qrec else f"{qfid:08X}"
            for dial, infos in topics:
                for info in infos:
                    how = self.direct(dial, info, npc_fid, aliases)
                    if how:
                        direct.append(self.entry(qname, dial, info, how))
                        claimed.add(m.rid(info))
                    elif include_generic:
                        g = self.generic(info, generic_keys)
                        if g:
                            generic.append(self.entry(qname, dial, info, f"{g} condition (shared line)"))

        for sc_fid, sc in m.by_sig['SCEN'].items():
            pnam = sc.get_subrecord('PNAM')
            qfid = m.absid(sc.plugin, u32(pnam.data)) if pnam else 0
            aliases = alias_map.get(qfid, set())
            if not aliases:
                continue
            qrec = m.get(qfid)
            qname = (qrec.editor_id if qrec else None) or f"{qfid:08X}"
            for topic_fid in self.scene_topics(sc, aliases):
                dial, infos = self.topics.get(topic_fid, (None, []))
                if dial is None:
                    continue
                for info in infos:
                    if m.rid(info) in claimed:
                        continue
                    claimed.add(m.rid(info))
                    scene_lines.append(self.entry(
                        qname, dial, info, 'scene action', scene=sc.editor_id or f"{sc_fid:08X}"))
        return {'direct': direct, 'scene': scene_lines, 'generic': generic}

    def scene_topics(self, sc, aliases):
        """Topic form IDs of dialogue actions in a scene performed by one of the aliases."""
        out, kind, alias, started = [], None, None, False
        for sr in sc.subrecords:
            s = sr.signature
            if s == 'ANAM' and len(sr.data) == 2:
                started, kind, alias = True, struct.unpack('<H', sr.data)[0], None
            elif not started:
                continue
            elif s == 'ALID':
                alias = u32(sr.data)
            elif s == 'DATA' and len(sr.data) == 4 and kind == 0 and alias in aliases:
                t = self.m.absid(sc.plugin, u32(sr.data))
                if t:
                    out.append(t)
        return out

    def direct(self, dial, info, npc_fid, aliases):
        """Why this line is the NPC's own (speaker, GetIsID or alias condition), or None. Only conditions that must be true count."""
        m = self.m
        spk = info.get_subrecord('ANAM')
        if spk and u32(spk.data) and m.absid(info.plugin, u32(spk.data)) == npc_fid:
            return 'speaker'
        for rec in (info, dial):
            for fn, p1, eq in conditions(rec):
                if not eq:
                    continue
                if fn == FN_GET_IS_ID and m.absid(rec.plugin, p1) == npc_fid:
                    return 'GetIsID condition'
                if fn == FN_GET_IS_ALIAS_REF and p1 in aliases:
                    return 'alias condition'
        return None

    def generic(self, info, keys):
        """Voice type / race / faction match, only when the line isn't tied to a specific actor."""
        hit = None
        for fn, p1, eq in conditions(info):
            if not eq:
                continue
            if fn in SPECIFIC_FNS:
                return None
            if fn in GENERIC_FNS and self.m.absid(info.plugin, p1) in keys[fn]:
                hit = hit or GENERIC_FNS[fn]
        return hit
