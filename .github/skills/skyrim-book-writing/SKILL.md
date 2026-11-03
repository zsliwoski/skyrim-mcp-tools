---
name: skyrim-book-writing
description: "Use when writing the text of a Skyrim book, note, letter, journal or ledger for a plugin and choosing its in-world model: length, format, voice, BOOK MODL/INAM/OBND/DATA. Triggers: write a book for an NPC, books read like poetry, make the book look right, note vs book vs journal, change a book's model, flavour books for a guild, edit_book, read_book."
---

# Writing Skyrim-style books and picking their model

Vanilla books are prose documents with a purpose (treatise, journal, letter, history, how-to), not aphorisms or verse. A two-sentence "wise saying" reads like a poem and has no place on a bookshelf. Pair this with `skyrim-lore-writing` (calendar, oaths, units) and `skyrim-record-cloning` (cloning, `clone_records`).

## Tools
- `list_book_models`: each vanilla model with its kind, `INAM`, word-count stats (median, p10, p90) and an example book. Use it to pick a variant.
- `read_book(record, plugin=None)`: the resolved text of a vanilla (localized) or plugin book. Read two or three vanilla books of the kind you are writing before drafting.
- `edit_book(plugin, record, kind=|variant=, text=, title=, author=, value=, weight=, reformat=)`: sets `MODL`, `MODT`, `OBND`, `INAM` and `DATA` together from a vanilla reference book, and with `text` wraps your plain paragraphs in vanilla markup. `reformat=True` re-wraps the existing text (printed books: first line title, optional `by Author`, then body).
- `edit_texts(plugin, items)`: batch text edits, one load and one save, validated up front. Prefer it to many `edit_text` calls. `edit_text` returns only a short summary (`verbose=True` for before/after).
- `lint_text` / `lint_plugin`: besides lore checks, flags length that does not fit the model, missing paragraph breaks, real-world science words (carat, carbon, atom...) and weekday/date order in dated entries.

## Form by length (measured from Skyrim.esm)
| Kind | Typical words | Models (`MODL`) | Value / weight |
|---|---|---|---|
| note / letter | 10-100 (lint allows 6-150) | `Note01`-`Note04` (`Note01Blood`, `NoteTornPaper` for grim ones) | 0 / 0 |
| journal / ledger | median ~180, 60-600 | `JournalLowPoly01`-`03` | small / 1 |
| book | 150-2000, median 450-1000 | `BasicBook01/03/03a/04/04a/05/05a/06/07` | small / 1 |

Skill books (`BasicBook06a`/`07a`) and spell tomes (`SpellTome*`) carry teaching flags and are excluded from `edit_book` variants.

## Structure and voice
- Title, then `by <Author>` (printed books) or addressed text (letters: "Dearest Sister," ... "Your loving Mirielle"). Journals use dated entries with the Tamrielic calendar.
- Open with a concrete situation or claim, develop it over several paragraphs (a how-to as "First... Second... Third..." steps, a treatise with an example or anecdote), close with the author's opinion or a sign-off.
- The author's race, trade and quirks show in what they say. Mention real places, people and trade details; reference other NPCs lightly.
- Avoid stacked fragments, rhyme and metaphors for their own sake; every paragraph should carry information.
- Stay in-world: no `carbon`/`carats`; use `grains`, `septims`, existing Skyrim places and factions (`search_forms`). Days and months per `skyrim-lore-writing`.

## Markup (done for you by `edit_book(text=...)`)
- Printed books: `[pagebreak]`, a centred title page (`<p align="center">` title, `by`, author), `[pagebreak]`, then `<p align="left">` with an illuminated first letter image and the body.
- Notes, letters, journals: `<font face="$HandwrittenFont">` ... `</font>`. Line endings CRLF.
- Give each book a `FULL` name that reads as the item.

## Worked examples
- Note: `edit_book(plugin, rec, kind="note", text="Dearest Sister,\n\nThe shop is quiet...\n\nYour loving Mirielle")`
- Journal: `edit_book(plugin, rec, kind="journal", text="2nd of Sun's Dusk\n\nDelivered the ...")`
- Treatise: `edit_book(plugin, rec, variant="BasicBook05", title="Veins That Remember", author="Sees-Deep-Veins", text=body)`

## Plan the set first
| Record | Kind | Words | Angle (purpose) |
|---|---|---|---|
| ledger | journal | ~200 | dated shop entries |
| letter | note | ~100 | personal, to family |
| treatises | book | 250-600 | how-to, history, opinion |

## Concurrency
Write tools take a server-wide lock, so parallel calls are safe on an up-to-date server; if edits ever overwrite each other, restart the MCP server (it may predate the lock) and batch with `edit_texts`.

## Checklist
1. Plan kind, length and angle per book. 2. `read_book` a few vanilla examples. 3. `edit_book` each (or `edit_texts` + `edit_book`). 4. `lint_text`, `validate_plugin`, `package_plugin`, `install_plugin`.
