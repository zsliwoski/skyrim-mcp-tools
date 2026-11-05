"""Parse Papyrus .psc source files into their declarations (script header, functions, events, properties)."""
import re

HEADER = re.compile(r'^\s*scriptname\s+(\w+)(?:\s+extends\s+(\w+))?\s*(.*)$', re.I)
CALLABLE = re.compile(r'^\s*(?:(\w+(?:\[\])?)\s+)?(function|event)\s+(\w+)\s*\((.*)\)\s*(.*)$', re.I)
PROPERTY = re.compile(r'^\s*(\w+(?:\[\])?)\s+property\s+(\w+)\s*(?:=\s*\S+\s*)?(.*)$', re.I)
DOC = re.compile(r'\{(.*?)\}', re.S)
BLOCK_COMMENT = re.compile(r';/.*?/;', re.S)
END_OF = {'function': re.compile(r'^\s*endfunction\b', re.I), 'event': re.compile(r'^\s*endevent\b', re.I),
          'property': re.compile(r'^\s*endproperty\b', re.I)}


def _lines(text):
    text = BLOCK_COMMENT.sub('', text).replace('\r\n', '\n')
    text = re.sub(r'\\[ \t]*\n', ' ', text)
    return text.split('\n')


def _strip_comment(line):
    return re.sub(r';.*$', '', line)


def _take_doc(lines, i, rest):
    """A {docstring} on the declaration line or the line after it. Returns (doc, last line index used)."""
    m = DOC.search(rest)
    if m:
        return ' '.join(m.group(1).split()), i
    j = i + 1
    if j < len(lines) and lines[j].lstrip().startswith('{'):
        chunk = lines[j]
        while '}' not in chunk and j + 1 < len(lines):
            j += 1
            chunk += ' ' + lines[j]
        m = DOC.search(chunk)
        if m:
            return ' '.join(m.group(1).split()), j
    return '', i


def parse(text):
    """{'name', 'extends', 'flags', 'doc', 'members': [...]} for one script; None without a ScriptName line.
    A member is {'kind': 'function'|'event'|'property', 'name', 'returns', 'params', 'flags', 'doc'}."""
    lines = _lines(text)
    script = None
    members, seen = [], set()
    i = 0
    while i < len(lines):
        code = _strip_comment(lines[i])
        m = HEADER.match(code)
        if m and script is None:
            doc, i = _take_doc(lines, i, code)
            script = {'name': m.group(1), 'extends': m.group(2), 'flags': m.group(3).split(), 'doc': doc,
                      'members': members}
            i += 1
            continue
        m = CALLABLE.match(code)
        if m:
            returns, kind, name, params, tail = m.groups()
            kind = kind.lower()
            flags = tail.split()
            doc, last = _take_doc(lines, i, tail)
            if 'native' not in {f.lower() for f in flags}:
                end = END_OF[kind]
                while last + 1 < len(lines) and not end.match(_strip_comment(lines[last + 1])):
                    last += 1
                last += 1
            if (kind, name.lower()) not in seen:
                seen.add((kind, name.lower()))
                members.append({'kind': kind, 'name': name, 'returns': returns or '', 'params': params.strip(),
                                'flags': [f for f in flags if not f.startswith('{')], 'doc': doc})
            i = last + 1
            continue
        m = PROPERTY.match(code)
        if m:
            type_, name, tail = m.groups()
            flags = tail.split()
            doc, last = _take_doc(lines, i, tail)
            if not re.search(r'\bauto(?:readonly)?\b', tail, re.I):
                while last + 1 < len(lines) and not END_OF['property'].match(_strip_comment(lines[last + 1])):
                    last += 1
                last += 1
            if ('property', name.lower()) not in seen:
                seen.add(('property', name.lower()))
                members.append({'kind': 'property', 'name': name, 'returns': type_, 'params': '',
                                'flags': [f for f in flags if not f.startswith('{')], 'doc': doc})
            i = last + 1
            continue
        i += 1
    return script


def signature(member):
    """One line as written in a script: 'Int Function Foo(Form a) native', 'Event OnHit(...)', 'Int Property X Auto'."""
    kind = member['kind'].capitalize()
    if member['kind'] == 'property':
        head = f"{member['returns']} Property {member['name']}"
    else:
        head = (f"{member['returns']} " if member['returns'] else '') + f"{kind} {member['name']}({member['params']})"
    return ' '.join([head] + member['flags'])
