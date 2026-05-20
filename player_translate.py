#!/usr/bin/env python3
"""player_translate.py — copy Java Edition player state into a Bedrock
`~local_player`, so a Java→Bedrock→NetEase round-trip (mcce.py
`java-to-netease`) keeps the player instead of spawning fresh.

Chunker's Java→Bedrock pass emits a *minimal* 9-field `~local_player`
(Pos, Motion, Rotation, Inventory, Armor, Offhand, DimensionId,
PlayerGameMode, Attributes). The NetEase Bedrock engine needs a full player
entity — `identifier`, `definitions`, `format_version`, `UniqueID`,
`internalComponents`, abilities, the dozens of Is*/Spawn*/etc. fields — to
instantiate the LocalPlayer. Given Chunker's stub it can't, and falls back to
a fresh default player (verified on iPad NetEase 3.8.15: java-to-netease
output, with or without Phase-1 field edits, spawned the player at the
hard-default (0,-2,0), NOT the world spawn).

So Phase 1b uses a *template overlay*: read a real NetEase save's
`~local_player` (a complete, iPad-accepted skeleton), then overlay only the
Java-derived fields onto it. The result is a genuine NetEase player entity
carrying the Java player's position/health.

Phased rollout (this file is Phase 3b):
  Phase 1b (done): Pos, Rotation, Health — overlaid onto a template.
  Phase 2 (done):  XP (level + progress), food (hunger/saturation/exhaustion),
                   dimension + SelectedInventorySlot. abilities deliberately NOT
                   overlaid (researched — the template's survival abilities
                   matched the Java player's on gameplay-relevant fields).
  Phase 3a (done): inventory + equipment, BASIC items — id, count, durability.
  Phase 3b (now):  enchantments (Java string → Bedrock numeric ench id),
                   enchanted-book stored enchants, custom names (Java JSON text
                   → Bedrock tag.display.Name), item-id override table for the
                   handful of genuinely-different ids (cobweb/web, lily_pad/...).

Ground-truth facts (confirmed by reading real NetEase iPad 3.8.15 /
Bedrock 1.21.90 `~local_player` values from saves P, B-iPad, B-Desktop):

  1. Health lives in the `minecraft:health` *attribute* (Current/Max floats),
     NOT a top-level `Health` short. Modern Bedrock dropped the legacy short.
  2. Bedrock LocalPlayer.Pos.y is the *eye* position = Java feet y + 1.62.
     Chunker applies this same +1.62; we match it. (BEDROCK_PLAYER_EYE_HEIGHT.)
  3. UniqueID is a FIXED sentinel `-4294967295` (0xFFFFFFFF00000001), byte-for
     -byte identical across every NetEase save examined (P/1.18, B-iPad/1.20.50,
     B-Desktop/1.21.90). It is the designated local-player value, NOT a
     per-world unique allocation. `~local_player` is a single per-world slot,
     so copying the template's UniqueID is safe — no collision possible.
     identifier="minecraft:player" and format_version="1.12.0" are likewise
     constant. Hence copying the whole template player (UniqueID and all) and
     overlaying a few fields is correct, not just convenient.

  4. Real NetEase player strings contain raw 0x00 bytes (non-standard
     modified-UTF-8), which the strict mutf-8 decoder rejects. We parse and
     re-serialize the template with amulet_nbt's escape codec, which preserves
     those bytes verbatim (verified byte-identical round-trip), so the overlaid
     player stays faithful to NetEase's on-disk format.

The Pos/Health decisions are easy to flip if iPad testing shows otherwise —
see the constants below.
"""
from __future__ import annotations

from pathlib import Path

# Bedrock stores the LocalPlayer's Pos.y at eye level; Java stores feet level.
# Standing eye height is 1.62. Chunker's Java→Bedrock pass adds the same offset
# (observed: Java feet 63.0 → Bedrock 64.62). Flip to 0.0 if iPad testing shows
# the player floating/sinking.
BEDROCK_PLAYER_EYE_HEIGHT = 1.62

# Bedrock base max health. Java health can exceed this with absorption, but the
# base attribute caps at 20; we clamp Current into [0, HEALTH_MAX].
HEALTH_MAX = 20.0

LOCAL_PLAYER_KEY = b"~local_player"

# NetEase appends a 3-byte trailer <XX> 0xc0 0x00 to ~local_player, in place of
# the root compound's TAG_End. We detect it by the trailing 0xc0 0x00 and
# restore a clean TAG_End before parsing the template.
NETEASE_TRAILER_SUFFIX = b"\xc0\x00"


# ---------------------------------------------------------------- Java player location

def _load_java_nbt(path: Path):
    """Load a gzip-compressed big-endian (Java) NBT file → NamedTag."""
    import amulet_nbt  # lazy: keeps `inspect`/`decrypt` stdlib-only
    # Java NBT is big-endian and gzip-compressed; standard modified-UTF-8 strings.
    return amulet_nbt.load(str(path), compressed=True, little_endian=False)


def find_java_player(java_save: Path):
    """Locate the single-player's NBT compound inside a Java save.

    Returns (player_compound, source_description). Tries, in order:
      1. level.dat → Data.Player          (vanilla single-player)
      2. playerdata/<uuid>.dat            (server / split-out player)
      3. players/data/<uuid>.dat          (seen in some non-vanilla saves)
      4. players/<name>.dat               (legacy)
    For the directory fallbacks: if exactly one .dat exists, use it; if several,
    pick the most recently modified and warn (single-player ambiguity).
    """
    level_dat = java_save / "level.dat"
    if level_dat.is_file():
        try:
            root = _load_java_nbt(level_dat).compound
            data = root.get("Data")
            if data is not None and "Player" in data:
                return data["Player"], "level.dat:Data.Player"
        except Exception as e:
            print(f"  [player-translate] warning: could not read {level_dat}: {e}")

    for sub in ("playerdata", "players/data", "players"):
        d = java_save / sub
        if not d.is_dir():
            continue
        dats = sorted(d.glob("*.dat"))
        if not dats:
            continue
        if len(dats) > 1:
            dats.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            print(
                f"  [player-translate] warning: {len(dats)} player files in "
                f"{sub}/; using most recent ({dats[0].name})"
            )
        chosen = dats[0]
        try:
            return _load_java_nbt(chosen).compound, f"{sub}/{chosen.name}"
        except Exception as e:
            print(f"  [player-translate] warning: could not read {chosen}: {e}")

    return None, None


# ---------------------------------------------------------------- field converters

def _build_health_attribute(current: float):
    """Construct a `minecraft:health` attribute compound matching the layout a
    real NetEase save uses (Base/Current/DefaultMax/DefaultMin/Max/Min/Name)."""
    import amulet_nbt as anbt
    a = anbt.CompoundTag()
    a["Base"] = anbt.FloatTag(HEALTH_MAX)
    a["Current"] = anbt.FloatTag(current)
    a["DefaultMax"] = anbt.FloatTag(HEALTH_MAX)
    a["DefaultMin"] = anbt.FloatTag(0.0)
    a["Max"] = anbt.FloatTag(HEALTH_MAX)
    a["Min"] = anbt.FloatTag(0.0)
    a["Name"] = anbt.StringTag("minecraft:health")
    return a


def _apply_pos(java_player, bedrock_player) -> str | None:
    import amulet_nbt as anbt
    jpos = java_player.get("Pos")
    if jpos is None or len(jpos) != 3:
        return None
    x = float(jpos[0])
    y = float(jpos[1]) + BEDROCK_PLAYER_EYE_HEIGHT
    z = float(jpos[2])
    bedrock_player["Pos"] = anbt.ListTag(
        [anbt.FloatTag(x), anbt.FloatTag(y), anbt.FloatTag(z)]
    )
    return f"Pos = [{x:.3f}, {y:.3f} (feet {y - BEDROCK_PLAYER_EYE_HEIGHT:.3f} + {BEDROCK_PLAYER_EYE_HEIGHT}), {z:.3f}]"


def _apply_rotation(java_player, bedrock_player) -> str | None:
    import amulet_nbt as anbt
    jrot = java_player.get("Rotation")
    if jrot is None or len(jrot) != 2:
        return None
    yaw = float(jrot[0])
    pitch = float(jrot[1])
    bedrock_player["Rotation"] = anbt.ListTag(
        [anbt.FloatTag(yaw), anbt.FloatTag(pitch)]
    )
    return f"Rotation = [{yaw:.2f}, {pitch:.2f}]"


def _apply_health(java_player, bedrock_player) -> str | None:
    jhealth = java_player.get("Health")
    if jhealth is None:
        return None
    current = max(0.0, min(HEALTH_MAX, float(jhealth)))
    attrs = bedrock_player.get("Attributes")
    import amulet_nbt as anbt
    if attrs is None:
        attrs = anbt.ListTag()
        bedrock_player["Attributes"] = attrs
    # Update existing minecraft:health attribute, or append a new one.
    for a in attrs:
        if str(a.get("Name")) == "minecraft:health":
            a["Current"] = anbt.FloatTag(current)
            return f"Health -> minecraft:health.Current = {current:.2f} (updated existing attr)"
    attrs.append(_build_health_attribute(current))
    return f"Health -> minecraft:health.Current = {current:.2f} (added new attr)"


def _set_attr_current(bedrock_player, name: str, value: float) -> bool:
    """Set the Current of the named Bedrock attribute. Returns True if found and
    updated, False if the attribute is absent. We don't fabricate missing
    attributes here (unlike health): the NetEase template always carries the
    standard XP/food attributes, and inventing Max/Default values we haven't
    verified is riskier than skipping with a warning."""
    import amulet_nbt as anbt
    attrs = bedrock_player.get("Attributes")
    if attrs is None:
        return False
    for a in attrs:
        if str(a.get("Name")) == name:
            a["Current"] = anbt.FloatTag(value)
            return True
    return False


def _apply_xp(java_player, bedrock_player) -> str | None:
    """Java XpLevel (int) + XpP (float, [0,1) progress) → Bedrock. XP lives in
    two redundant places in a NetEase player; update both for consistency:
      - top-level PlayerLevel (int) / PlayerLevelProgress (float)
      - attributes minecraft:player.level.Current / minecraft:player.experience.Current
    (Java XpTotal has no direct Bedrock field — Bedrock derives total from
    level+progress — so it is not copied.)"""
    import amulet_nbt as anbt
    jlevel = java_player.get("XpLevel")
    jprog = java_player.get("XpP")
    if jlevel is None and jprog is None:
        return None
    notes = []
    if jlevel is not None:
        lv = int(jlevel)
        bedrock_player["PlayerLevel"] = anbt.IntTag(lv)
        found = _set_attr_current(bedrock_player, "minecraft:player.level", float(lv))
        notes.append(f"PlayerLevel={lv}" + ("" if found else " (player.level attr absent)"))
    if jprog is not None:
        pr = float(jprog)
        bedrock_player["PlayerLevelProgress"] = anbt.FloatTag(pr)
        found = _set_attr_current(bedrock_player, "minecraft:player.experience", pr)
        notes.append(f"PlayerLevelProgress={pr:.4f}" + ("" if found else " (player.experience attr absent)"))
    return "XP -> " + ", ".join(notes)


def _apply_food(java_player, bedrock_player) -> str | None:
    """Java food fields → Bedrock attributes (no top-level food fields exist):
      foodLevel (int)            → minecraft:player.hunger.Current
      foodSaturationLevel (float)→ minecraft:player.saturation.Current
      foodExhaustionLevel (float)→ minecraft:player.exhaustion.Current
    foodTickTimer has no Bedrock player-NBT counterpart (runtime only) — skipped."""
    mapping = [
        ("foodLevel", "minecraft:player.hunger"),
        ("foodSaturationLevel", "minecraft:player.saturation"),
        ("foodExhaustionLevel", "minecraft:player.exhaustion"),
    ]
    notes = []
    for jfield, attr in mapping:
        jv = java_player.get(jfield)
        if jv is None:
            continue
        found = _set_attr_current(bedrock_player, attr, float(jv))
        short = attr.split(".")[-1]
        notes.append(f"{short}={float(jv):.2f}" + ("" if found else f" ({attr} absent)"))
    if not notes:
        return None
    return "Food -> " + ", ".join(notes)


def _apply_dimension(java_player, bedrock_player) -> str | None:
    """Java Dimension (string) → Bedrock DimensionId (int)."""
    import amulet_nbt as anbt
    jdim = java_player.get("Dimension")
    if jdim is None:
        return None
    name = str(jdim)
    mapping = {
        "minecraft:overworld": 0,
        "minecraft:the_nether": 1,
        "minecraft:the_end": 2,
    }
    if name not in mapping:
        print(f"    [player-translate] warning: unknown Java Dimension {name!r}; "
              f"leaving template DimensionId unchanged")
        return None
    bedrock_player["DimensionId"] = anbt.IntTag(mapping[name])
    return f"Dimension {name!r} -> DimensionId = {mapping[name]}"


# ---------------------------------------------------------------- items (Phase 3a)
#
# Ground truth from a real NetEase ~local_player (B template, Bedrock 1.21.90):
#
#   Inventory: ListTag, FIXED length 36, Slot 0..35 all present (0-8 hotbar,
#     9-35 main). Each item has a `Slot` byte. Empty slot is a real item with
#     Count=0 and Name="" (NOT absent):
#       {Count:byte, Damage:short, Name:string, Slot:byte, WasPickedUp:byte, tag?:compound}
#   Armor:   ListTag, FIXED length 4 = [head, chest, legs, feet]. Items here
#     have NO Slot field (positional). Empty = {Count:0,Damage:0,Name:"",WasPickedUp:0}.
#   Offhand: ListTag, FIXED length 1, no Slot field, same empty shape.
#   Durability lives in the TOP-LEVEL `Damage` short (tools may also carry a
#     tag.Damage, but top-level is what we set). Enchants/custom names live in
#     `tag` — NOT handled here (Phase 3b).
#
# Java 1.20.5+ source format (New World 01 player file):
#   Inventory[i] = {Slot:byte, id:string, count:int, components:compound}
#   Armor is NOT in Inventory; it's a separate `equipment` compound:
#     equipment = {head?, chest?, legs?, feet?, offhand?} (only filled slots present),
#     each item = {id:string, count:int, components:compound} (no Slot).
#   Durability is components["minecraft:damage"] (int).

# Java enchantment string id -> Bedrock numeric id. Bedrock uses its OWN
# contiguous 0..37 ordering (sharpness=9), NOT Java's legacy pre-1.13 numeric
# ids (where sharpness=16). Confirmed against minecraft.wiki Bedrock data values
# AND a real NetEase save (B template's diamond_pickaxe carried ench id=18 and
# id=26 = fortune + mending, matching this table). 38-40 are the 1.21 mace
# enchants. Unknown enchants are skipped with a warning, never guessed.
ENCHANTMENT_JAVA_TO_BEDROCK = {
    "protection": 0, "fire_protection": 1, "feather_falling": 2,
    "blast_protection": 3, "projectile_protection": 4, "thorns": 5,
    "respiration": 6, "depth_strider": 7, "aqua_affinity": 8, "sharpness": 9,
    "smite": 10, "bane_of_arthropods": 11, "knockback": 12, "fire_aspect": 13,
    "looting": 14, "efficiency": 15, "silk_touch": 16, "unbreaking": 17,
    "fortune": 18, "power": 19, "punch": 20, "flame": 21, "infinity": 22,
    "luck_of_the_sea": 23, "lure": 24, "frost_walker": 25, "mending": 26,
    "binding_curse": 27, "vanishing_curse": 28, "impaling": 29, "riptide": 30,
    "loyalty": 31, "channeling": 32, "multishot": 33, "piercing": 34,
    "quick_charge": 35, "soul_speed": 36, "swift_sneak": 37,
    "density": 38, "breach": 39, "wind_burst": 40,
}

# Java item id -> Bedrock item id, ONLY for ids that genuinely differ. Modern
# Bedrock unified most ids; this is a deliberately conservative, curated set
# (mis-mapping is worse than pass-through). Extend from the Bedrock data-values
# wiki as broken items surface.
ITEM_ID_JAVA_TO_BEDROCK = {
    "minecraft:cobweb": "minecraft:web",
    "minecraft:lily_pad": "minecraft:waterlily",
}


def _bedrock_ench_id(java_ench_id: str):
    """Map a Java enchant id (with or without minecraft: prefix) to Bedrock's
    numeric id, or None if unknown."""
    name = java_ench_id.split(":", 1)[1] if ":" in java_ench_id else java_ench_id
    return ENCHANTMENT_JAVA_TO_BEDROCK.get(name)


def _build_ench_list(java_ench_compound, item_label: str):
    """Java enchantments compound {"minecraft:sharpness": level, ...} (flat, new
    1.20.5+ format — no `levels` wrapper) -> Bedrock tag.ench ListTag of
    {id:short, lvl:short, modEnchant:""}. Returns (ListTag, n_unknown)."""
    import amulet_nbt as anbt
    elems = []
    n_unknown = 0
    for jid, level in java_ench_compound.items():
        bid = _bedrock_ench_id(str(jid))
        if bid is None:
            n_unknown += 1
            print(f"    [player-translate] warning: unknown enchant {jid!r} on "
                  f"{item_label}; skipped")
            continue
        e = anbt.CompoundTag()
        e["id"] = anbt.ShortTag(bid)
        e["lvl"] = anbt.ShortTag(max(0, min(32767, int(level))))
        e["modEnchant"] = anbt.StringTag("")   # NetEase-specific field, kept empty
        elems.append(e)
    return anbt.ListTag(elems) if elems else None, n_unknown


def _java_text_to_plain(raw: str) -> str:
    """Java custom_name is a JSON text component serialized as a string, e.g.
    '"Excalibur"' or '{"text":"Excalibur"}'. Extract plain text."""
    import json
    try:
        parsed = json.loads(raw)
    except Exception:
        return raw.strip('"')
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict):
        text = parsed.get("text", "")
        for extra in parsed.get("extra", []) or []:
            if isinstance(extra, str):
                text += extra
            elif isinstance(extra, dict):
                text += extra.get("text", "")
        return text
    return raw.strip('"')


def _empty_bedrock_item(slot: int | None = None):
    import amulet_nbt as anbt
    it = anbt.CompoundTag()
    it["Count"] = anbt.ByteTag(0)
    it["Damage"] = anbt.ShortTag(0)
    it["Name"] = anbt.StringTag("")
    it["WasPickedUp"] = anbt.ByteTag(0)
    if slot is not None:
        it["Slot"] = anbt.ByteTag(slot)
    return it


def translate_item_java_to_bedrock(java_item, keep_slot: bool = True):
    """Java 1.20.5+ component-format item → Bedrock item compound (or None for
    air/empty/unconvertible). Phase 3a: id (+ override table), count, durability.
    Phase 3b: enchantments, stored enchantments (books), custom names."""
    import amulet_nbt as anbt
    jid = java_item.get("id")
    if jid is None:
        return None
    orig = str(jid)
    if orig in ("", "minecraft:air"):
        return None

    out = anbt.CompoundTag()
    out["Name"] = anbt.StringTag(ITEM_ID_JAVA_TO_BEDROCK.get(orig, orig))
    cnt = int(java_item.get("count", 1))
    out["Count"] = anbt.ByteTag(max(0, min(127, cnt)))

    comps = java_item.get("components")
    has_comps = comps is not None and hasattr(comps, "get")

    dmg = 0
    if has_comps:
        jd = comps.get("minecraft:damage")
        if jd is not None:
            try:
                dmg = int(jd)
            except (TypeError, ValueError):
                dmg = 0
    out["Damage"] = anbt.ShortTag(max(0, min(32767, dmg)))
    out["WasPickedUp"] = anbt.ByteTag(0)

    # ---- Phase 3b: build tag (enchants / stored enchants / custom name) ----
    if has_comps:
        tag = anbt.CompoundTag()

        # held-item enchantments and enchanted-book stored enchantments both map
        # to Bedrock tag.ench. (No enchanted book in the test save, so the book
        # path is best-effort/unverified; Bedrock held-item enchants use ench.)
        for comp_key in ("minecraft:enchantments", "minecraft:stored_enchantments"):
            jench = comps.get(comp_key)
            if jench is not None and hasattr(jench, "items"):
                ench_list, _ = _build_ench_list(jench, orig)
                if ench_list is not None:
                    if "ench" in tag:
                        for e in ench_list:
                            tag["ench"].append(e)
                    else:
                        tag["ench"] = ench_list

        jname = comps.get("minecraft:custom_name")
        if jname is not None:
            text = _java_text_to_plain(str(jname))
            if text:
                disp = anbt.CompoundTag()
                disp["Name"] = anbt.StringTag(text)
                tag["display"] = disp

        if len(tag) > 0:
            out["tag"] = tag

    if keep_slot and "Slot" in java_item:
        out["Slot"] = anbt.ByteTag(int(java_item["Slot"]))
    return out


def _apply_inventory(java_player, bedrock_player) -> str | None:
    """Replace the template's 36-slot Inventory with the Java player's items.
    Only main inventory slots 0..35 (new-format armor lives in `equipment`,
    handled by _apply_equipment; legacy armor slots 100-103/150 are ignored
    here — that's a Phase 3b concern)."""
    import amulet_nbt as anbt
    jinv = java_player.get("Inventory")
    if jinv is None:
        return None
    slots = {i: _empty_bedrock_item(i) for i in range(36)}
    n_items = 0
    n_skipped = 0
    for jit in jinv:
        sl = jit.get("Slot")
        if sl is None:
            continue
        s = int(sl)
        if not (0 <= s <= 35):
            continue  # legacy armor/offhand slot numbering — not handled in 3a
        bit = translate_item_java_to_bedrock(jit, keep_slot=True)
        if bit is None:
            if str(jit.get("id", "")) not in ("", "minecraft:air"):
                n_skipped += 1
                print(f"    [player-translate] skipped unconvertible item in slot {s}: {jit.get('id')}")
            continue
        bit["Slot"] = anbt.ByteTag(s)
        slots[s] = bit
        n_items += 1
    bedrock_player["Inventory"] = anbt.ListTag([slots[i] for i in range(36)])
    return f"Inventory -> {n_items} items ({n_skipped} unknown skipped)"


def _apply_equipment(java_player, bedrock_player) -> str | None:
    """Java new-format `equipment` compound → Bedrock Armor[4] + Offhand[1].
    Old-format saves (armor in Inventory, no `equipment`) are skipped (3b)."""
    import amulet_nbt as anbt
    jeq = java_player.get("equipment")
    if jeq is None:
        return None
    n_armor = 0
    armor = []
    for slot in ("head", "chest", "legs", "feet"):   # Bedrock Armor index 0..3
        jit = jeq.get(slot)
        bit = translate_item_java_to_bedrock(jit, keep_slot=False) if jit is not None else None
        if bit is None:
            armor.append(_empty_bedrock_item())
        else:
            armor.append(bit)
            n_armor += 1
    bedrock_player["Armor"] = anbt.ListTag(armor)

    joff = jeq.get("offhand")
    boff = translate_item_java_to_bedrock(joff, keep_slot=False) if joff is not None else None
    bedrock_player["Offhand"] = anbt.ListTag([boff if boff is not None else _empty_bedrock_item()])
    return f"Equipment -> {n_armor} armor piece(s)" + (", offhand" if boff is not None else "")


def _apply_selected_slot(java_player, bedrock_player) -> str | None:
    """Java SelectedItemSlot (IntTag, 0-8 hotbar index) → Bedrock
    SelectedInventorySlot (IntTag). Confirmed field names by reading a real
    NetEase player (Bedrock uses SelectedInventorySlot; Java uses
    SelectedItemSlot). Pass-through int, clamped to [0, 8]."""
    import amulet_nbt as anbt
    jsel = java_player.get("SelectedItemSlot")
    if jsel is None:
        return None
    s = max(0, min(8, int(jsel)))
    bedrock_player["SelectedInventorySlot"] = anbt.IntTag(s)
    return f"SelectedItemSlot {int(jsel)} -> SelectedInventorySlot = {s}"


# ---------------------------------------------------------------- template loading

def _read_local_player_value(db_dir: Path) -> bytes:
    import leveldb
    db = leveldb.LevelDB(str(db_dir), create_if_missing=False)
    v = db.get(LOCAL_PLAYER_KEY)
    db.close()
    if v is None:
        raise SystemExit(
            f"error: template db {db_dir} has no ~local_player — "
            f"is this a real NetEase player save?"
        )
    return bytes(v)


def load_template_local_player(template: Path):
    """Return (clean_nbt_value_bytes, source_kind) for the template's
    ~local_player. Accepts an encrypted NetEase save or a decrypted one;
    decrypts to a throwaway temp dir if needed (never touches the original).

    `clean_nbt_value_bytes` has the NetEase 3-byte trailer (if any) stripped and
    a proper root TAG_End restored, ready for amulet_nbt.load.
    """
    import shutil
    import tempfile
    import mcce  # lazy: mcce is already imported when java-to-netease runs

    db_dir = template / "db"
    if not db_dir.is_dir():
        raise SystemExit(f"error: template {template} has no db/ — not a Bedrock save?")
    current = (db_dir / "CURRENT").read_bytes()

    if mcce.looks_decrypted_current(current):
        raw = _read_local_player_value(db_dir)
        kind = "decrypted (read directly)"
    else:
        ks, _ = mcce.derive_keystream(db_dir)
        tmp = Path(tempfile.mkdtemp(prefix="mcce_tmpl_"))
        try:
            dst = tmp / "decrypted"
            mcce.copy_save_tree(template, dst)
            mcce.decrypt_db_in_place(dst, ks, verbose=False)
            raw = _read_local_player_value(dst / "db")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        kind = "encrypted (decrypted to temp, then discarded)"

    if raw[-2:] == NETEASE_TRAILER_SUFFIX:
        clean = raw[:-3] + b"\x00"   # drop NetEase <XX> c0 00, restore TAG_End
    else:
        clean = raw                   # already a standard NBT (ends in TAG_End)
    return clean, kind


# ---------------------------------------------------------------- entry point

def translate_player_java_to_bedrock(
    java_save: Path,
    bedrock_db_dir: Path,
    template: Path | None,
    verbose: bool = True,
) -> bool:
    """Overlay the Java player's Pos/Rotation/Health onto a real NetEase
    `~local_player` template, then write the result into the Bedrock LevelDB at
    `bedrock_db_dir`'s `~local_player` (replacing Chunker's stub).

    `template` must point at a known-good NetEase save (encrypted or decrypted).
    Returns True on success. Raises SystemExit on hard errors (missing deps,
    missing template, unreadable player).
    """
    try:
        import leveldb  # amulet-leveldb
        import amulet_nbt
    except ImportError:
        raise SystemExit(
            "error: player translation needs amulet-leveldb + amulet-nbt.\n"
            "  brew install leveldb snappy\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/pip install -r requirements.txt\n"
            "Then re-run with the venv's python."
        )

    if template is None:
        raise SystemExit(
            "error: --player-template is required for player translation.\n"
            "Chunker's Java→Bedrock player is a 9-field stub that the NetEase\n"
            "client rejects (it lacks identifier/definitions/format_version/\n"
            "internalComponents, so the engine can't instantiate the player and\n"
            "falls back to a default at (0,-2,0)). Pass a real NetEase save to\n"
            "use its ~local_player as a skeleton, e.g.:\n"
            "  --player-template /path/to/an/exported/NetEase/save\n"
            "Or pass --no-translate-player to skip translation entirely."
        )

    java_player, src = find_java_player(java_save)
    if java_player is None:
        raise SystemExit(
            "error: no Java player NBT found in "
            f"{java_save} (checked level.dat:Data.Player, playerdata/, "
            "players/data/, players/). Cannot translate."
        )

    clean, tmpl_kind = load_template_local_player(template)
    nt = amulet_nbt.load(
        clean, compressed=False, little_endian=True,
        string_decoder=amulet_nbt.utf8_escape_decoder,
    )
    bedrock_player = nt.compound  # fresh tree parsed from the template bytes

    if verbose:
        print(f"  [player-translate] template source: {template}  [{tmpl_kind}]")
        print(f"  [player-translate] template ~local_player fields: {len(bedrock_player)}")
        print(f"  [player-translate] template identifier: {bedrock_player.get('identifier')!r}")
        print(f"  [player-translate] Java player from: {src}")

    applied = []
    for fn in (_apply_pos, _apply_rotation, _apply_health,
               _apply_xp, _apply_food, _apply_dimension,
               _apply_inventory, _apply_equipment,
               _apply_selected_slot):
        msg = fn(java_player, bedrock_player)
        if msg:
            applied.append(msg)
            if verbose:
                print(f"    overlaid: {msg}")
        elif verbose:
            print(f"    (skipped {fn.__name__}: Java source field absent)")

    out = nt.to_nbt(
        compressed=False, little_endian=True,
        string_encoder=amulet_nbt.utf8_escape_encoder,
    )
    db = leveldb.LevelDB(str(bedrock_db_dir), create_if_missing=False)
    chunker_stub = db.get(LOCAL_PLAYER_KEY)
    db.put(LOCAL_PLAYER_KEY, out)
    db.close()

    if verbose:
        stub_len = len(chunker_stub) if chunker_stub else 0
        print(
            f"  [player-translate] replaced Chunker stub "
            f"({stub_len} bytes) with template+overlay ({len(out)} bytes); "
            f"final fields: {len(bedrock_player)}; overlaid {len(applied)} group(s)."
        )
    return True
