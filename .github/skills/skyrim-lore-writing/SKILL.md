---
name: skyrim-lore-writing
description: Use when writing any player-facing text for a Skyrim plugin (books, dialogue, quest log, names, descriptions) so it stays lore friendly. Triggers: write a book, write dialogue, flavor text, oaths, dates, "Tuesday", "oh my god", lint_text.
---

# Lore-friendly writing

Run `lint_text(plugin=...)` after writing text (`validate_plugin` also reports lint warnings). Fix with `edit_text`, no rebuild needed.

## Checklist
- **Days**: Morndas, Tirdas, Middas, Turdas, Fredas, Loredas, Sundas. Never Monday–Sunday.
- **Months**: Morning Star, Sun's Dawn, First Seed, Rain's Hand, Second Seed, Midyear, Sun's Height, Last Seed, Hearthfire, Frostfall, Sun's Dusk, Evening Star. Never January–December.
- **Oaths**: "By the Eight!" for Imperial-line speakers (Nords of the Empire, Imperials, townsfolk who do not follow Talos); "By the Nine!" if the speaker reveres Talos or Divines with him (Stormcloaks, many Nords). Never "Oh my god", "God", "Jesus", "hell", "heaven"; use Oblivion, Sovngarde or "the Void" as fits.
- **Oath frequency**: use oaths sparingly. Most NPCs and lines should have none; add one only when the speaker would genuinely react that way, and not in calm, non-religious contexts like crafts or trade (see `skyrim-dialogue`, "Voice and religion").
- **Affirmations**: "very well", "all right", "fine" instead of "okay".
- **Currency**: septims or gold, never dollars.
- **Distance**: leagues, paces, "a day's walk"; not miles, feet or meters. Time: no "o'clock"; use morning, dusk, "the third bell".
- **Holidays**: no Christmas or Halloween; use Skyrim festivals (Sun's Dawn, Heartfires, etc.).
- Match the speaker's voice and keep it in character (Nevs is vain and comic).

- **Books**: write prose documents of fitting length and model, not poetry or one-liners; see `skyrim-book-writing`.

## Tools
`lint_text`, `list_texts`, `edit_text`, `edit_texts` (batch), `read_book`, `edit_book` (see `skyrim-quest-authoring`, `skyrim-book-writing`). `lint_text` also flags real-world science words (carat, carbon...), weekday/date order in dated entries, and book length/paragraphs that do not fit the model.
