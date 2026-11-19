---
name: skyrim-shopkeeper
description: "Use when making a custom Skyrim NPC a vendor or shopkeeper: vendor faction, vendor chest, buy/sell list, trade topic, empty stock, no trade option, NPC barking over the innkeeper. Triggers: add a vendor, NPC should sell items, shop doesn't open, trade window empty, vendor chest, VEND/VENC/VENV, JobMerchantFaction, add an NPC to an inn."
---

# Skyrim shopkeeper

Lessons from building a custom vendor in a vanilla inn. Compare against a working vanilla vendor (Orgnar `00013479`, Belethor) with `get_npc` and `get_record` before debugging. Vanilla vendors have no vendor script; none is missing.

## Required pieces
1. **NPC factions:** `JobMerchantFaction` plus the NPC's own vendor faction. Cloned factions can come with unwanted behaviour (see below).
2. **Vendor faction** (`create_faction` or a hand-built `FACT`):
   - `DATA` flags `0x4000` (vendor).
   - `VEND`: the buy/sell list (FLST), e.g. `VendorItemsInnkeeper` (`000914F0`).
   - `VENC`: the vendor chest REFR (the stock and the gold).
   - `VENV`: hours, start 0, end 23 (24 gave no stock).
   - `PLVD`: vendor location, the cell or location where the shop is.
   - `CRVA` as in vanilla.
3. **Vendor chest:** a container (CONT) with the stock in `CNTO` entries and `DATA` as the last subrecord. Place its REFR in the shop's cell below floor level so players don't see it, and make it persistent with an `XOWN` naming the NPC.
4. **Trade topic:** vanilla's "What do you have for sale?" (`OfferServicesTopic`, `0007F6BB`) appears by itself once the NPC is in the vendor faction and the vendor is open. A custom topic is not needed.

## Gotchas
- **Trade topic missing:** it shows only while the vendor counts as open (hours in `VENV`), so test after waiting for morning and check `VENV`. Do not add a custom `ShowBarterMenu()` topic: the player would get two trade options. If the user wants one at all hours, a custom topic with `akSpeaker.ShowBarterMenu()` and `goodbye: true` works, but vanilla's cannot be hidden.
- **Trade window opens but stock is empty:** the vendor faction needs `VENC` pointing at the chest REFR, `PLVD`, a chest REFR `XOWN`, and `DATA` last in the chest record. Skyrim saves the chest's state in the save, so test on a fresh save or run `resetinterior <CellEditorId>` in the console.
- **Chest items not offered for sale:** the `VEND` list is a FLST of vendor keywords (`VendorItemFood`, `VendorItemWeapon`, ...), and the trade window shows only items carrying one of them. `VendorItemsInnkeeper` has only the two food keywords, so weapons in the chest never appear. Create a FLST (`create_record`, `LNAM` per keyword) with the categories needed and point `VEND` at it with `edit_record`. Check the items' keywords (`get_record`), since a custom item needs the matching keyword (cloned vanilla weapons already carry `VendorItemWeapon`). Then `resetinterior <CellEditorId>` or a new save.
- **NPC barks over the real innkeeper when the player enters:** do not copy `JobInnkeeperFaction` from the inn's innkeeper. The vanilla entry greeting is tied to it. `JobMerchantFaction` is enough for a shop; trade still works without the innkeeper faction.
- **Stray topics:** a clone inherits the source's factions and so its topics. Set `factions` explicitly with `edit_npc` and check `get_npc_dialogue`.
- `remove_records` leaves references behind. Remove the DLBR of a removed topic as well, and delete its `TIF__` `.pex` and `.psc`.

## Workflow
1. Create or edit the NPC (`create_npc`, `edit_npc`, see `skyrim-dialogue`), with `JobMerchantFaction` and the vendor faction only.
2. Build the chest, REFR and vendor faction; link them as above.
3. `validate_plugin`, `package_plugin`, `install_plugin`, then test: talk to him, wait until morning, open the trade window, and enter the building to check nobody else is spoken over.
4. If it fails, use `get_record` on the vendor faction, chest and REFR against Orgnar's (`skyrim-quest-debugging` for the in-game checks).
