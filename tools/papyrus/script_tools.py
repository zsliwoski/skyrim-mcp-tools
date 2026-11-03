"""Interpret compiled Papyrus scripts and map quest structure (stages, fragments, scripts) together."""
import re
from pathlib import Path

from tools.papyrus.pex_tools import Pex, parse, render, val
from tools.quests.quest_tools import QuestTools

QUEST_EFFECTS = {
    'setstage', 'setcurrentstageid', 'setobjectivedisplayed', 'setobjectivecompleted',
    'setobjectivefailed', 'completeallobjectives', 'failallobjectives', 'completequest',
    'start', 'stop', 'reset', 'setactive', 'forcereffo', 'forcerefifempty', 'clear',
}
BUILTIN_TYPES = {'int', 'float', 'bool', 'string', 'var', 'none'}


def short(name):
    """Last segment of a namespaced script name (Fragments:Quests:QF_X -> QF_X)."""
    return re.split(r'[:\\/]', name)[-1]


def clean(name):
    """Readable variable name: strips the compiler's :: prefix and _var suffix; any self alias becomes Self."""
    n = name[2:] if name.startswith('::') else name
    if name.startswith('::') and n.endswith('_var'):
        n = n[:-4]
    return 'Self' if n.lower() == 'self' else n


def type_name(t):
    """Type name as shown in signatures; array types keep their [] suffix."""
    return t[:-2] + '[]' if t.endswith('[]') else t


class FunctionAnalysis:
    """Turns one function's bytecode into resolved call strings and property accesses."""

    def __init__(self, fn):
        self.fn = fn
        self.exprs = {}
        self._build()

    def _dest(self, op, a):
        if op in ('iadd', 'fadd', 'isub', 'fsub', 'imul', 'fmul', 'idiv', 'fdiv', 'imod', 'strcat',
                  'cmp_eq', 'cmp_lt', 'cmp_le', 'cmp_gt', 'cmp_ge', 'not', 'ineg', 'fneg', 'assign',
                  'cast', 'array_create', 'array_length'):
            return a[0]
        if op in ('callmethod', 'callstatic', 'propget'):
            return a[2]
        if op == 'callparent':
            return a[1]
        if op in ('array_get', 'array_find', 'array_rfind'):
            return a[1]
        return None

    def _build(self):
        counts = {}
        for op, a, _e in self.fn.code:
            d = self._dest(op, a)
            if d and d[0] == 'id':
                counts[d[1]] = counts.get(d[1], 0) + 1
        temps = {n for n, c in counts.items() if c == 1 and n.startswith('::temp')}
        for op, a, extra in self.fn.code:
            d = self._dest(op, a)
            if not d or d[1] not in temps:
                continue
            e = self.expr_for(op, a, extra)
            if e is not None:
                self.exprs[d[1]] = e

    def v(self, x):
        if x[0] == 'id':
            return self.exprs.get(x[1], clean(x[1]))
        return val(x)

    def expr_for(self, op, a, extra):
        """Source expression an instruction computes, or None for ops that don't yield a value we inline."""
        sym = {'iadd': '+', 'fadd': '+', 'isub': '-', 'fsub': '-', 'imul': '*', 'fmul': '*',
               'idiv': '/', 'fdiv': '/', 'imod': '%', 'strcat': '+', 'cmp_eq': '==',
               'cmp_lt': '<', 'cmp_le': '<=', 'cmp_gt': '>', 'cmp_ge': '>='}
        if op in sym:
            return f"({self.v(a[1])} {sym[op]} {self.v(a[2])})"
        if op in ('assign', 'cast'):
            return self.v(a[1])
        if op == 'not':
            return f"!{self.v(a[1])}"
        if op == 'propget':
            return f"{self.v(a[1])}.{a[0][1]}"
        if op == 'callmethod':
            return self.call_str(a[1], a[0][1], extra)
        if op == 'callstatic':
            return f"{a[0][1]}.{a[1][1]}({', '.join(self.v(x) for x in extra)})"
        if op == 'array_length':
            return f"{self.v(a[1])}.Length"
        return None

    def call_str(self, target, method, extra):
        return f"{self.v(target)}.{method}({', '.join(self.v(x) for x in extra)})"

    def analyze(self):
        """(summary dict, structured calls). Summary lists unique calls and property reads/writes plus
        branch and instruction counts; structured calls are (target, method, args) for method and static calls."""
        fn = self.fn
        calls, reads, writes = [], [], []
        structured = []
        branches = 0
        for op, a, extra in fn.code:
            if op == 'callmethod':
                target, method = self.v(a[1]), a[0][1]
                calls.append(self.call_str(a[1], method, extra))
                structured.append((target, method, [self.v(x) for x in extra]))
            elif op == 'callparent':
                calls.append(f"Parent.{a[0][1]}({', '.join(self.v(x) for x in extra)})")
            elif op == 'callstatic':
                calls.append(f"{a[0][1]}.{a[1][1]}({', '.join(self.v(x) for x in extra)})")
                structured.append((a[0][1], a[1][1], [self.v(x) for x in extra]))
            elif op == 'propget':
                reads.append(f"{self.v(a[1])}.{a[0][1]}")
            elif op == 'propset':
                writes.append(f"{self.v(a[1])}.{a[0][1]} = {self.v(a[2])}")
            elif op in ('jmpt', 'jmpf'):
                branches += 1
        return {
            'calls': list(dict.fromkeys(calls)),
            'property_reads': list(dict.fromkeys(reads)),
            'property_writes': list(dict.fromkeys(writes)),
            'branches': branches,
            'instructions': len(fn.code),
        }, structured


def function_entry(name, fn, state=''):
    """(entry dict, structured calls) for one function; non-global functions named On* with no return are Events."""
    info, structured = FunctionAnalysis(fn).analyze()
    is_event = not (fn.flags & 1) and name.lower().startswith('on') and fn.ret.lower() == 'none'
    out = {'name': name, 'kind': 'Event' if is_event else 'Function',
           'params': [f"{type_name(t)} {n}" for n, t in fn.params]}
    if fn.ret.lower() != 'none':
        out['returns'] = fn.ret
    if fn.flags & 1:
        out['global'] = True
    if fn.flags & 2:
        out['native'] = True
    if fn.doc:
        out['doc'] = fn.doc
    if state:
        out['state'] = state
    out.update({k: v for k, v in info.items() if v})
    return out, structured


def analyze_pex(x, bindings=None):
    """Structured summary of a parsed script. bindings: {property: resolved value} from the record."""
    bindings = bindings or {}
    out = {'source': x.source, 'compiled_by': x.user}
    objs = []
    for o in x.objects:
        entry = {'name': o.name, 'extends': o.parent or None, 'auto_state': o.auto_state or None}
        if o.doc:
            entry['doc'] = o.doc
        entry['variables'] = [
            {'name': n, 'type': t, **({'initial': val(i)} if i[0] != 'none' else {})}
            for n, t, _f, i in o.variables if not n.endswith('_var') or not n.startswith('::')]
        props = []
        for p in o.properties:
            pe = {'name': p.name, 'type': p.type, 'auto': p.auto_var is not None}
            if p.auto_var is None:
                pe['access'] = '/'.join(k for k, f in (('get', p.get), ('set', p.set)) if f)
            if p.name in bindings:
                pe['value'] = bindings[p.name]
            props.append(pe)
        entry['properties'] = props
        funcs = []
        for p in o.properties:
            for label, f in (('Get', p.get), ('Set', p.set)):
                if f is not None and f.code:
                    e, _ = function_entry(f"{p.name}.{label}", f)
                    e['kind'] = 'PropertyAccessor'
                    funcs.append(e)
        for sname, fns in o.states:
            for fname, fn in fns:
                funcs.append(function_entry(fname, fn, sname)[0])
        entry['functions'] = funcs
        entry['events'] = sorted({f['name'] for f in funcs if f['kind'] == 'Event'})
        deps = {o.parent} if o.parent else set()
        deps |= {p.type.removesuffix('[]') for p in o.properties}
        deps |= {t.removesuffix('[]') for _n, t, _f, _i in o.variables}
        for sname, fns in o.states:
            for _n, fn in fns:
                for op, a, _e in fn.code:
                    if op == 'callstatic':
                        deps.add(a[0][1])
        entry['depends_on'] = sorted(d for d in deps if d and d.lower() not in BUILTIN_TYPES
                                     and not d.startswith('::') and d.lower() != o.name.lower())
        objs.append({k: v for k, v in entry.items() if v not in (None, [], '')})
    out['objects'] = objs
    return {k: v for k, v in out.items() if v}


class ScriptTools:
    """Describes compiled scripts and ties them to a quest's stages, aliases, scenes and dialogue fragments."""

    def __init__(self, model, store):
        self.m = model
        self.store = store
        self.q = QuestTools(model)

    def load(self, name):
        """(Pex, origin file name) for a script, ignoring any namespace prefix, or None."""
        found = self.store.get_pex(short(name))
        return found

    def describe(self, name, include_source=False, bindings=None, chain=True):
        """Structured analysis of one script, optionally with its parent chain and pseudo-source."""
        found = self.load(name)
        if found is None:
            return {'name': name, 'error': 'compiled script (.pex) not found in loose files or BSAs'}
        x, origin = found
        out = {'name': name, 'origin': origin, **analyze_pex(x, bindings)}
        if chain:
            parents, cur = [], x.objects[0].parent if x.objects else None
            while cur and cur.lower() not in BUILTIN_TYPES:
                hit = self.load(cur)
                if hit is None:
                    parents.append(f"{cur} (native or not found)")
                    break
                parents.append(cur)
                cur = hit[0].objects[0].parent if hit[0].objects else None
            if parents:
                out['inheritance'] = parents
        if include_source:
            out['pseudo_source'] = render(x)
        return out

    # ---- quest structure -------------------------------------------------------------------------

    def fragment_effects(self, script_name, fragment_name, bindings):
        """Calls made by one fragment function, with notable quest-state effects pulled out."""
        found = self.load(script_name)
        if found is None:
            return {'script': script_name, 'function': fragment_name, 'error': 'script not found'}
        x, _origin = found
        for o in x.objects:
            for _s, fns in o.states:
                for fname, fn in fns:
                    if fname.lower() == fragment_name.lower():
                        entry, structured = function_entry(fname, fn)
                        out = {'script': script_name, 'function': fname, 'calls': entry.get('calls', [])}
                        effects = []
                        for target, method, args in structured:
                            if method.lower() in QUEST_EFFECTS:
                                bound = bindings.get(target)
                                tgt = target
                                if isinstance(bound, dict):
                                    tgt = f"{target} [{bound.get('editor_id') or bound.get('form_id')}]"
                                effects.append(f"{tgt}.{method}({', '.join(args)})")
                        if effects:
                            out['effects'] = list(dict.fromkeys(effects))
                        for k in ('property_reads', 'property_writes'):
                            if entry.get(k):
                                out[k] = entry[k]
                        return out
        return {'script': script_name, 'function': fragment_name, 'error': 'function not found'}

    def bindings(self, vm):
        """{script name: {property: value}} from a record's VMAD."""
        out = {}
        if vm is None:
            return out
        for s in vm.scripts:
            out[short(s.name).lower()] = {p.name: self.q.n.value(p.value) for p in s.properties}
        return out

    def structure(self, rec):
        """Full quest map: stages with what their fragments do, stage transitions, objectives, aliases, scenes,
        dialogue fragments and each distinct custom script analysed once under script_definitions."""
        m, n, q = self.m, self.q.n, self.q
        out = q.summary(rec)
        vm = m.vmad(rec)
        binds = self.bindings(vm)
        full = q.decode(rec)

        def frag_bindings(script):
            return binds.get(short(script).lower(), {})

        stage_frags = {}
        fd = vm.fragment_data if vm is not None else None
        if fd is not None:
            out['fragment_script'] = fd.filename or None
            for fr in fd.fragments or []:
                st = getattr(fr, 'quest_stage', None)
                stage_frags.setdefault(st, []).append(fr)

        stages, edges, obj_refs = [], [], {}
        for st in full.get('stages', []):
            idx = st['stage']
            entry = {'stage': idx}
            if st.get('log'):
                entry['log'] = st['log']
            frs = []
            for fr in stage_frags.get(idx, []):
                fx = self.fragment_effects(fr.script_name, fr.fragment_name, frag_bindings(fr.script_name))
                frs.append(fx)
                for e in fx.get('effects', []):
                    mt = re.search(r'\.(\w+)\((.*)\)$', e)
                    if not mt:
                        continue
                    method, args = mt.group(1).lower(), mt.group(2)
                    if method in ('setstage', 'setcurrentstageid'):
                        edges.append({'from': idx, 'to': args, 'via': e})
                    elif method.startswith('setobjective') and args.split(',')[0].strip().isdigit():
                        obj_refs.setdefault(int(args.split(',')[0]), []).append(f"stage {idx}: {e}")
            if frs:
                entry['fragments'] = frs
            stages.append(entry)
        out['stages'] = stages
        if edges:
            out['stage_transitions'] = edges
        if full.get('objectives'):
            out['objectives'] = [{'index': 0, **o, 'driven_by': obj_refs.get(o.get('index', 0), [])}
                                 for o in full['objectives']]

        defs = {}

        def attach(s):
            """Compact script reference; full analysis goes once into script_definitions."""
            b = {p.name: n.value(p.value) for p in s.properties
                 if not p.name.startswith('Alias_') and n.value(p.value) not in (None, 'None', 0, '', False)}
            entry = {'name': s.name}
            if b:
                entry['bound_properties'] = b
            nm = short(s.name)
            if not re.match(r'(qf|tif|sf|pf|tf)_', nm, re.I) and nm.lower() not in defs:
                defs[nm.lower()] = self.describe(s.name)
            return entry

        scripts = [attach(s) for s in vm.scripts] if vm is not None else []
        out['scripts'] = scripts

        aliases = []
        alias_vm = {}
        if vm is not None:
            for a in vm.alias_scripts:
                alias_vm.setdefault(a.alias_obj.alias, []).extend(a.scripts)
        for a in full.get('aliases', []):
            entry = {k: a[k] for k in ('id', 'name', 'type', 'flags', 'fills') if k in a}
            descs = [attach(s) for s in alias_vm.get(a['id'], [])]
            if descs:
                entry['scripts'] = descs
            aliases.append(entry)
        out['aliases'] = aliases

        scenes = []
        for sc in m.scenes.get(m.rid(rec), []):
            entry = n.ref(m.rid(sc))
            svm = m.vmad(sc)
            if svm is not None:
                sb = self.bindings(svm)
                if svm.scripts:
                    entry['scripts'] = [attach(s) for s in svm.scripts]
                sfd = svm.fragment_data
                if sfd is not None and sfd.phase_fragments:
                    entry['phase_fragments'] = [
                        {'phase': getattr(p, 'phase_index', None),
                         **self.fragment_effects(p.script_name, p.fragment_name, sb.get(short(p.script_name).lower(), {}))}
                        for p in sfd.phase_fragments]
            scenes.append(entry)
        if scenes:
            out['scenes'] = scenes

        topics = []
        for dial, infos in m.dialogue.get(m.rid(rec), []):
            for info in infos:
                ivm = m.vmad(info)
                ifd = ivm.fragment_data if ivm is not None else None
                if ifd is None or not ifd.fragments:
                    continue
                ib = self.bindings(ivm)
                topics.append({
                    'topic': dial.editor_id or f"{m.rid(dial):08X}",
                    'info': f"{m.rid(info):08X}",
                    'fragments': [self.fragment_effects(f.script_name, f.fragment_name,
                                                        ib.get(short(f.script_name).lower(), {}))
                                  for f in ifd.fragments]})
        if topics:
            out['dialogue_fragments'] = topics[:200]
        if defs:
            out['script_definitions'] = defs
        out['involved'] = full.get('involved')
        return {k: v for k, v in out.items() if v not in (None, [], {})}
