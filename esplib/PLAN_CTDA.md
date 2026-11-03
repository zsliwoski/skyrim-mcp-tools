# Plan: handling CTDA (conditions)

Status: **DONE — Option C + D1 implemented.** Kept for the reasoning;
the shipped behaviour is described in FORMID_DESIGN.md under
"Data-dependent fields (EspUnion)". Outcome notes are at the end.

Two questions prompted this: is the condition-function table
self-contained, and why can't CTDA be described by a schema like
everything else. The second one has a real answer that constrains the
design, so it comes first.

---

## 1. Why CTDA isn't just another struct

CTDA is a fixed 32-byte block. That part is easy. The problem is that
**four of its fields change type depending on the value of another
field in the same block**:

```
offset  size  field              type
     0     1  Type (flags)       u8
     1     3  unused
     4     4  Comparison Value   float  OR  FormID(GLOB)     <- depends on Type & 0x04
     8     2  Function           u16
    10     2  unused
    12     4  Parameter #1       int/enum/float OR FormID    <- depends on Function
    16     4  Parameter #2       int/enum/float OR FormID    <- depends on Function
    20     4  Run On             u32
    24     4  Reference          unused u32 OR FormID(REFR)  <- depends on Run On == 2
    28     4  Parameter #3       s32
```

So a schema can describe the *layout* trivially, but it cannot say
which slots are FormIDs without reading the data. And getting that
wrong is not cosmetic: remapping a slot that actually holds an
actor-value index or a float silently corrupts the condition, while
failing to remap a slot that does hold a FormID silently retargets it.

`Parameter #1`/`#2` are the awkward ones. Whether they are forms
depends on the condition function, of which there are 402 in Skyrim
and 479 in Fallout 4. There is no pattern to exploit — it is a lookup
table, and it is genuinely game-specific (Skyrim's `GetActorValue`
takes an enum integer; Fallout 4's `GetValue` takes an AVIF reference).

### What esplib can express today

esplib already has `EspUnion` — a conditional field with a decider
callable — so the concept exists. It is used for the
localized-vs-inline string case:

```python
EspUnion.new('name',
    decider=lambda ctx: 0 if ctx.extra.get('subrecord_size', 0) == 4 else 1,
    members=[EspInteger.new('string_id', IntType.U32),
             EspString.new('name', 'zstring')])
```

Note what that decider looks at: `subrecord_size`, which is
*subrecord-level* context injected by `EspSubRecord.from_subrecord`.
Every existing decider is like this. **None of them looks at a sibling
field inside the same struct**, and that is exactly what CTDA needs.

Three concrete gaps stand between "EspUnion exists" and "CTDA is a
normal schema struct":

**Gap 1 — deciders can't see sibling fields.**
`EspStruct.from_bytes` parses members in order and collects results
into a local dict, but never threads them into the `EspContext` it
passes down. A union nested in a struct therefore cannot ask "what was
the Function field?".

*Mitigating fact, and it is a big one:* every CTDA decider depends on a
field that is **strictly earlier** in the block — Type(0) governs
offset 4, Function(8) governs 12 and 16, Run On(20) governs 24. A
single forward pass that accumulates parsed fields into the context is
sufficient. No backtracking, no two-pass parse, no reordering.

**Gap 2 — `EspUnion` has no `byte_size`.**
`_remap_struct_formids` walks a struct by advancing a static offset:

```python
size = getattr(field, 'byte_size', None) or getattr(field, 'size', None)
if size is None:
    break  # variable-size field, stop
```

A union hits that `break` and silently halts the walk — so a
union-based CTDA would stop the FormID scan at offset 4 and everything
after it would be missed. Fixable, and cheaply: every CTDA union has
members that are all exactly 4 bytes, so `byte_size` is well-defined
whenever all members agree.

**Gap 3 — the FormID walk is static; union resolution isn't.**
`_remap_struct_formids` decides what to remap purely from the schema,
never looking at the bytes. Resolving a union requires the data. So the
walk needs to run deciders against the subrecord's actual content, then
`isinstance`-check the *resolved* member rather than the declared one.

**Also worth knowing:** `EspUnion.to_bytes` currently serializes by
trying each member until one doesn't raise. That is fine for
string-or-int, but for CTDA it would happily write a FormID as a float.
Any option that makes CTDA writable through the schema has to fix this
too.

---

## 2. The two tiers esplib actually has

Worth stating plainly, because CTDA's placement should be a deliberate
choice rather than a default:

**Tier 1 — schema-described.** `EspStruct` / `EspFormID` / `EspArray`.
The generic walk in `_remap_subrecord_formids` finds FormIDs for free.
Most subrecords.

**Tier 2 — opaque byte array plus a dedicated parser.** For formats
whose internals are variable-length or data-dependent. Three members
today:

| Subrecord | Schema decl | Parser | Remap hook |
|---|---|---|---|
| VMAD | `EspByteArray` | `esplib/vmad.py` (`VmadData.parse/to_bytes`) | `_remap_vmad` |
| MO2S/3S/4S/5S | `EspAlternateTextures` | inline | `_remap_alternate_textures` |
| CTDA | `EspByteArray` | **none** | `_remap_ctda` |

CTDA is the odd one out: it got a remap hook but no parser type. The
byte-poking lives directly in `plugin.py` at hardcoded offsets. That's
an inconsistency in what currently ships, and it's the strongest
argument for Option B below.

---

## 3. Options

### Option A — dedicated `_remap_ctda` (what ships now)

`plugin.py` decodes the four conditional slots inline and calls
`_remap_at` on the ones that are forms.

- **Pro:** done, tested, no risk to the parse engine.
- **Con:** a third bespoke hook with no parser type behind it.
- **Con:** conditions still aren't readable or writable through any
  typed API. Consumers keep poking bytes — `furrifier/schlongs.py`
  already defines its own `_CTDA_PARAM1_OFFSET = 12` and unpacks the
  function at `data[8:12]`, and now `plugin.py` has a second, separate
  copy of the same offset constants.

### Option B — a `Condition` type, mirroring `vmad.py` *(recommended)*

Add `esplib/condition.py`:

```python
@dataclass
class Condition:
    type: int
    comparison: float | FormID
    function: int
    param1: int | float | FormID
    param2: int | float | FormID
    run_on: int
    reference: int | FormID
    param3: int

    @classmethod
    def parse(cls, data: bytes, game_id: str) -> 'Condition': ...
    def to_bytes(self) -> bytes: ...
    def form_id_offsets(self) -> list[int]: ...
```

`_remap_ctda` shrinks to delegating at `form_id_offsets()`. The offset
constants live in exactly one place.

- **Pro:** this *is* "parsed like everything else", in the sense that
  already exists in esplib — it's the same shape as VMAD, which is the
  established answer for a complex binary subrecord. One mechanism for
  the tier-2 cases rather than two different ones.
- **Pro:** furrifier gets `Condition.parse(sr.data, 'tes5').param1`
  instead of `struct.unpack_from('<I', d, 12)`, and
  `patch_sos_setup_races` stops carrying its own offset constants.
- **Pro:** doesn't touch the parse engine, so no regression surface
  across the other 450 tests.
- **Con:** it is another type module — a real cost if the answer to
  "does anything else need this?" stays "no".
- **Con:** doesn't give `record['CTDA']['function']`; access is via the
  helper, exactly as VMAD works today.

### Option C — make CTDA a first-class schema struct

Close gaps 1–3, fix `EspUnion.to_bytes`, then declare CTDA with nested
`EspUnion` members and delete the bespoke hook entirely.

- **Pro:** the genuinely principled answer. CTDA becomes ordinary, and
  FormID discovery falls out of the generic walk with no special case.
- **Pro:** typed access through the normal record API.
- **Pro:** the sibling-context and union-sizing work is reusable —
  every future data-dependent subrecord benefits.
- **Con:** the changes are load-bearing. Threading context through
  `EspStruct.from_bytes` and teaching `_remap_struct_formids` to
  resolve unions against data touches the parse and copy path for
  *every* record, to fix one subrecord.
- **Con:** `EspUnion.to_bytes`'s try-each-member fallback has to be
  replaced with something that knows which member is active, or
  writing a condition back can corrupt it.
- **Con:** the function table is still needed and still game-specific.
  C changes where the lookup is wired in, not whether it exists.

### Recommendation

**B now, C when a second subrecord needs it.** B gets the offset
constants into one place and gives consumers a typed API, at the cost
of one small module and no engine risk. C is the better end state, but
right now it would rework the parse path for every record to serve a
single subrecord — and the day a second union-shaped subrecord shows
up, B's `Condition` drops out cleanly and its tests carry over.

If you'd rather go straight to C, the sibling-context finding above is
the thing that makes it tractable: strictly-forward dependencies mean
it's an accumulate-as-you-go change, not a parser rewrite.

---

## 4. The function table and self-containment

**esplib does not read xEdit at runtime.** Nothing under `src/` opens a
`.pas` file; the only references are docstrings. `defs/condition_functions.py`
is a plain Python module of literals, imported like any other.

It's also worth noting that `defs/tes5.py`, `defs/fo4.py` and
`defs/common.py` all open with "Ported from wbDefinitions*.pas" — the
entire `defs` package is transcribed xEdit data. The condition table is
the same kind of artifact; the only difference is that it was
transcribed by a script instead of by hand, because it's ~900 rows.

So the real question isn't the table, it's the generator. Options:

**D1 — keep it, but fix the two actual smells** *(recommended)*
- Require the xEdit path as an explicit argument; drop the hardcoded
  `C:\Modding\xEditDev\TES5Edit-dev-4.1.6\Core` default, which is the
  thing that makes it look like a runtime dependency.
- Move it out of `esplib/scripts/`, which otherwise holds tools meant
  to be run against game data (`dump_dialog.py`, `dump_records.py`).
  A `tools/` or `devtools/` directory says "maintenance aid, not part
  of the package" more clearly.
- Keep the assertion that parsed entry count matches xEdit's declared
  array bound. It earned its place immediately: it caught FO4 mixing
  `ParamType1` and `Paramtype1` casing, and the SKSE entries
  (1024–1028) carrying a trailing `;`. Both would have silently
  dropped rows, and a dropped row means a FormID that stops being
  remapped — the exact bug this whole effort is about.

**D2 — delete the generator, hand-maintain the table.**
Maximally self-contained. But 881 rows across two games will rot, and
hand-transcription is precisely what the two traps above show is
error-prone. Not recommended.

**D3 — leave as-is.** Works, but keeps the hardcoded path.

### Staleness behaviour

If xEdit adds condition functions and the table isn't regenerated, the
new ones are simply unknown. `get_condition_function` returns `None`,
`_remap_ctda` logs at debug and leaves those parameters untouched —
i.e. it degrades to the old behaviour for new functions only. No crash,
no corruption of known ones. This is also how script-extender functions
that xEdit doesn't know are handled.

---

## 5. What's already in place

Regardless of which option is chosen, these stand:

- The four-slot analysis and its verification against real data.
- `defs/condition_functions.py` — 402 TES5 / 479 FO4 entries.
- `tests/test_ctda_remap.py` — 12 tests, including negative cases (a
  `GetActorValue` parameter whose bit pattern looks like a FormID must
  *not* move) and a real-data SOS regression. 6 fail without the fix.
- `furrifier/tests/unit/test_schlongs.py` — 4 tests on
  `patch_sos_setup_races`; 2 fail without the fix.
- `FORMID_DESIGN.md` — gap list updated.

Options B and C both keep the tests as-is; they change what sits behind
them.

---

## 6. Decisions needed

1. **A, B, or C** for how CTDA is handled.
2. **D1, D2, or D3** for the generator.
3. If B: should `Condition` be re-exported from `esplib/__init__.py`
   alongside `VmadData`, and should `furrifier/schlongs.py` be migrated
   onto it in the same change or a follow-up?


---

## 7. Outcome

Implemented **Option C** (CTDA as a schema struct with `EspUnion`) and
**D1** (generator kept, path required, moved to `devtools/`).

Engine changes: `EspContext.fields` + `for_struct()`,
`EspStruct.from_bytes`/`to_bytes` threading, `EspUnion.resolve()` /
`to_bytes(ctx)` / `byte_size`, a `_member_to_bytes` dispatcher, and
`Record.game_id`. `_remap_ctda` and its offset constants are gone;
`_remap_struct_formids` resolves unions against the data, parsing a
struct only when it actually contains one.

Validation: all 117,398 conditions in Skyrim.esm + DLC + SOS round-trip
byte-exact through `common.CONDITION`. esplib 462 tests, furrifier 718,
FO4 furrifier 325 — all green.

Three things the plan did not anticipate:

- **The write path needed `ctx.game`, not just sibling fields.** The
  prototype hid this by defaulting to `'tes5'` in its decider; the real
  schema surfaced it immediately. `Record.__getitem__`/`__setitem__`
  now populate `game` from the record's plugin registry.
- **`EspSubRecord.to_subrecord_data` had a `try: to_bytes(value, ctx)
  except TypeError: to_bytes(value)` fallback.** A union rejecting a
  value raises TypeError, so the retry would have dropped the very
  context the union needs — converting a clear error into wrong bytes.
  Replaced with type dispatch.
- **Records with no schema lost CTDA handling.** MGEF has no definition
  in `defs/tes5.py`, so moving CTDA into the schema walk skipped it
  entirely. The schema-less branch now applies `common.CONDITION`
  directly, the way it already did for alternate textures. The SOS
  regression test caught this.
