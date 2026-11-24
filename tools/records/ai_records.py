"""Factions and AI packages: the records a follower's behaviour settings are stored in and acted on through."""
import re
import struct

from esplib.record import Record
from esplib.utils import FormID

from tools.core.plugin_edit import Target, hi, resolve_target_path
from tools.npcs.npc_builder import CONDITION_FUNCTIONS, CONDITION_OPS, PLAYER_REF, RUN_ON, Plan, fid_bytes, z

FACTION_HIDDEN = 0x1
FACT_CRVA = bytes([1, 1]) + bytes(18)  # crime values, unused by a settings faction
DONOR_FOLLOW_PACKAGE = 'PlayerFollowerPackage'  # a Follow-template package; its first two floats are the radii
INSERT_AFTER = ('PKDT', 'PSDT', 'CTDA', 'CIS1', 'CIS2')  # conditions belong after the package data header


def ctda(func, fid, op, value, run_on=0, param2=0):
    """One 32-byte CTDA: function index, local form ID parameter, operator index, value, run-on, second parameter."""
    return struct.pack('<B3xfH2xIIIIi', op << 5, value, func, fid, param2, run_on, PLAYER_REF if run_on == 2 else 0, -1)


def make_faction(fid, editor_id, name, ranks, hidden=True):
    """FACT with named ranks (rank number = position in `ranks`)."""
    rec = Record('FACT', FormID(fid), 0)
    rec.add_subrecord('EDID', z(editor_id))
    if name:
        rec.add_subrecord('FULL', z(name))
    rec.add_subrecord('DATA', struct.pack('<I', FACTION_HIDDEN if hidden else 0))
    rec.add_subrecord('CRVA', FACT_CRVA)
    for i, title in enumerate(ranks):
        rec.add_subrecord('RNAM', struct.pack('<I', i))
        rec.add_subrecord('MNAM', z(title))
    rec.add_subrecord('VENV', bytes(12))
    return rec


def make_formlist(fid, editor_id, members):
    """FLST holding the given local form IDs."""
    rec = Record('FLST', FormID(fid), 0)
    rec.add_subrecord('EDID', z(editor_id))
    for m in members:
        rec.add_subrecord('LNAM', fid_bytes(m))
    return rec


def schedule_bytes(hour, duration, days=None):
    """PSDT: month any, day of week (-1 any, 0 Sunday .. 6 Saturday), date 0, start hour/minute, duration stored
    in minutes (vanilla DefaultEatEvening19x1 holds 60 for one hour)."""
    hour = float(hour)
    if not 0 <= hour < 24:
        raise ValueError("start hour must be from 0 up to 24")
    if not 1 <= int(duration) <= 24:
        raise ValueError("duration must be 1 to 24 hours")
    dow = -1 if days is None else int(days)
    if not -1 <= dow <= 6:
        raise ValueError("day must be 0 (Sunday) to 6 (Saturday)")
    return struct.pack('<bbBbb3xi', -1, dow, 0, int(hour), int(round((hour % 1) * 60)), int(duration) * 60)


def clone_package(donor, fid, editor_id, conditions=(), radii=None, combat_style=None, leash=None, schedule=None):
    """PACK copied from donor with a new editor ID, extra conditions (CTDA bytes), the first float parameters
    replaced by radii, an optional combat style form ID (local) written before the data inputs, and the location
    radius set to `leash` (for the combat-override donor).

    Form IDs inside the copy are kept as they are, so donor must come from an official master (slot 0).
    """
    rec = Record('PACK', FormID(fid), donor.flags if hasattr(donor, 'flags') else 0)
    subs = [(s.signature, bytes(s.data)) for s in donor.subrecords if s.signature != 'EDID']
    # CIS2 stays with its CTDA, so conditions go after the last condition-related subrecord before PKCU
    pkcu = next((i for i, (sig, _) in enumerate(subs) if sig == 'PKCU'), len(subs))
    last = max([i for i, (sig, _) in enumerate(subs[:pkcu]) if sig in INSERT_AFTER], default=-1)
    subs[last + 1:last + 1] = [('CTDA', c) for c in conditions]
    if combat_style:
        at = next(i for i, (sig, _) in enumerate(subs) if sig == 'PKCU')
        subs.insert(at, ('CNAM', fid_bytes(combat_style)))
    if radii:
        pkcu = next(i for i, (sig, _) in enumerate(subs) if sig == 'PKCU')
        floats = [i + 1 for i, (sig, d) in enumerate(subs) if i > pkcu and sig == 'ANAM' and d.startswith(b'Float')]
        if len(radii) > len(floats):
            raise ValueError(f"package has {len(floats)} float parameters; radii has {len(radii)} values")
        for idx, value in zip(floats, radii):
            subs[idx] = ('CNAM', struct.pack('<f', float(value)))
    if leash is not None:
        at = next((i for i, (sig, _) in enumerate(subs) if sig == 'PLDT'), None)
        if at is None:
            raise ValueError("package has no location (PLDT) to apply a leash radius to")
        kind, ref, _ = struct.unpack('<iIi', subs[at][1])
        subs[at] = ('PLDT', struct.pack('<iIi', kind, ref, int(leash)))
    if schedule is not None:
        at = next((i for i, (sig, _) in enumerate(subs) if sig == 'PSDT'), None)
        if at is None:
            raise ValueError("package has no schedule (PSDT) to change")
        subs[at] = ('PSDT', schedule)
    rec.add_subrecord('EDID', z(editor_id))
    for sig, data in subs:
        rec.add_subrecord(sig, data)
    return rec


def _next_id(b, plugin):
    n = len(plugin.header.masters)
    used = [r.form_id.value & 0xFFFFFF for r in plugin.records if hi(r.form_id.value) == n]
    return max([plugin.header.next_object_id, 0x800] + [u + 1 for u in used])


def _check_new(b, plugin, editor_id):
    if not re.fullmatch(r'[A-Za-z0-9_]+', str(editor_id)):
        raise ValueError("editor_id may contain only letters, digits and underscores")
    taken = any((r.editor_id or '').lower() == editor_id.lower() for r in b.m.records.values()) or \
        any((r.editor_id or '').lower() == editor_id.lower() for r in plugin.records)
    if taken:
        raise ValueError(f"editor_id {editor_id!r} already exists")


def create_faction(b, target_plugin, editor_id, name=None, ranks=None, hidden=True):
    """Add a faction to target_plugin. ranks: rank titles in order (rank 0 first); hidden hides it from the player."""
    ranks = ranks or ['Default']
    if not (isinstance(ranks, list) and all(isinstance(r, str) and r for r in ranks)):
        raise ValueError("ranks must be a list of non-empty rank titles")
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=True)
    plugin = target.plugin
    _check_new(b, plugin, editor_id)
    b.target, b.plugin = target, plugin
    oid = _next_id(b, plugin)
    rec = make_faction(b.own(oid), editor_id, name, ranks, hidden)
    plugin.add_record(rec)
    plugin.header.next_object_id = oid + 1
    target.save()
    return {'path': str(path), 'record': b.describe(rec), 'ranks': dict(enumerate(ranks))}


def package_conditions(b, target, plan, specs):
    """CTDA bytes for condition dicts ({function, form, op, value, run_on}); `form` may be a record in the target
    plugin (matched by editor ID) or any loaded record. Own records need a form-ID parameter function only."""
    out = []
    for n, c in enumerate(specs or []):
        func = CONDITION_FUNCTIONS.get(c.get('function'))
        op, run_on = CONDITION_OPS.get(c.get('op', '==')), RUN_ON.get(c.get('run_on', 'subject'))
        if func is None or op is None or run_on is None:
            raise ValueError(f"conditions[{n}]: needs function {list(CONDITION_FUNCTIONS)}, op and run_on")
        own = target.find_own(c['form'], None) if isinstance(c.get('form'), str) else None
        if own is not None:
            out.append(ctda(func, own.form_id.value, op, float(c.get('value', 1)), run_on, int(c.get('param2', 0))))
            continue
        planned = b.plan_conditions(plan, {'conditions': [c]}, n)
        if plan.errors:
            raise ValueError("; ".join(plan.errors))
        fn, fid, op_, val, run, p2 = planned[0]
        out.append(('late', fn, fid, op_, val, run, p2))
    return out


def resolve_late(b, conds):
    """Turn the 'late' tuples from package_conditions into CTDA bytes once the masters are decided."""
    return [c if isinstance(c, bytes) else ctda(c[1], b.lid(c[2]), c[3], c[4], c[5], c[6]) for c in conds]


def create_package(b, target_plugin, editor_id, donor=DONOR_FOLLOW_PACKAGE, conditions=None, radii=None,
                   combat_style=None, schedule=None):
    """Clone a vanilla AI package into target_plugin, gated by extra conditions.

    donor: package editor ID or form ID (default PlayerFollowerPackage, a Follow package). conditions: as for
    edit_npc dialogue conditions. radii: replacement values for the package's first float parameters (for the
    default donor: [minimum distance, maximum distance]). combat_style: a CSTY applied while the package runs
    (untested in game). Packages only run when something puts them on an actor, e.g. an alias package list.
    """
    path = resolve_target_path(target_plugin, b.output_dir, b.data_dir)
    target = Target(b.m, b.names, path, create=True)
    plugin = target.plugin
    _check_new(b, plugin, editor_id)
    dfid, donor_rec = b.resolver.lookup(donor, ['PACK'])
    if dfid >> 24 != 0:
        raise ValueError("donor must be a package from Skyrim.esm (form IDs are copied unchanged)")
    p = Plan()
    p.abs_ids.add(dfid)
    conds = package_conditions(b, target, p, conditions)
    style = None
    if combat_style:
        style, _ = b.resolver.lookup(combat_style, ['CSTY'])
        p.abs_ids.add(style)
    b.target, b.plugin = target, plugin
    b.add_masters(plugin, p)
    oid = _next_id(b, plugin)
    rec = clone_package(donor_rec, b.own(oid), editor_id, resolve_late(b, conds), radii,
                        b.local(style) if style else None, schedule=schedule)
    plugin.add_record(rec)
    plugin.header.next_object_id = oid + 1
    target.save()
    return {'path': str(path), 'record': b.describe(rec), 'donor': donor_rec.editor_id,
            'masters': list(plugin.header.masters)}


SCHEDULE_DONORS = {'sandbox': 'DefaultSandboxCurrentLocation1024', 'sleep': 'DefaultSleepPackageAnyTime',
                   'eat': 'DefaultEatMorning7x1'}


def set_npc_schedule(b, target_plugin, npc, entries, prefix=None, refresh=None):
    """Give an NPC a daily routine: one cloned package per entry, each active for a window of the day.

    entries: [{activity or donor, start (hour 0-24, e.g. 21.5), hours (1-24), day?}]. activity is one of
    sandbox/sleep/eat (vanilla default packages), or donor names any Skyrim.esm package instead. The new packages are appended to the NPC's package list in order.
    """
    if not entries or not isinstance(entries, list):
        raise ValueError("entries must be a non-empty list of {activity, start, hours}")
    prefix = prefix or f"{npc}Sched"
    made, names = [], []
    for n, e in enumerate(entries, 1):
        donor = e.get('donor') or SCHEDULE_DONORS.get(e.get('activity'))
        if not donor:
            raise ValueError(f"entries[{n - 1}]: give donor or activity {list(SCHEDULE_DONORS)}")
        if 'start' not in e or 'hours' not in e:
            raise ValueError(f"entries[{n - 1}]: needs start and hours")
        sched = schedule_bytes(e['start'], e['hours'], e.get('day'))
        eid = f"{prefix}{n}"
        res = create_package(b, target_plugin, eid, donor, schedule=sched)
        made.append(res['record'])
        names.append(eid)
    b = refresh() if refresh else b
    out = b.edit_npc(npc, {'add_packages': names}, target_plugin)
    return {'packages': made, 'npc': out}
