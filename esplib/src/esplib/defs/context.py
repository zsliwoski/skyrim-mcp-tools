"""Context object passed during schema resolution."""

from dataclasses import dataclass, field
from typing import List, Optional, Any


@dataclass
class EspContext:
    """Carries record-level context needed by schema resolution.

    Passed to from_bytes() methods so that conditional definitions
    (EspUnion deciders) can inspect record state.
    """
    # Record flags (e.g. compressed flag, etc.)
    flags: int = 0
    # Record's form version (from the version field in the record header)
    form_version: int = 0
    # Plugin master list (for FormID resolution display)
    masters: List[str] = field(default_factory=list)
    # Game identifier ('tes5', 'fo4', 'sf1')
    game: str = ''
    # Parent record signature (e.g. 'WEAP', 'NPC_')
    record_signature: str = ''
    # Arbitrary extra data that deciders can use
    extra: dict = field(default_factory=dict)
    # Sibling fields of the struct currently being read or written.
    #
    # EspStruct populates this so a nested EspUnion decider can branch on
    # another field of the same struct -- CTDA needs it, where the type of
    # Parameter #1 depends on the Function field alongside it. While
    # reading, the dict grows as members are parsed, so a decider sees
    # every field *before* its own; while writing, all fields are present
    # up front. Empty for a union that is not inside a struct.
    fields: dict = field(default_factory=dict)

    def for_struct(self, fields: dict) -> 'EspContext':
        """A child context scoped to one struct's fields.

        A copy rather than a mutation, so a nested struct cannot clobber
        its parent's fields. `extra` is deliberately shared: it carries
        subrecord-level state that nested deciders still need.
        """
        return EspContext(
            flags=self.flags,
            form_version=self.form_version,
            masters=self.masters,
            game=self.game,
            record_signature=self.record_signature,
            extra=self.extra,
            fields=fields,
        )
