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

Phased rollout (this file is Phase 2):
  Phase 1b (done): Pos, Rotation, Health — overlaid onto a template.
  Phase 2 (now):   XP (level + progress), food (hunger/saturation/exhaustion),
                   dimension. abilities deliberately NOT overlaid (researched —
                   the template's survival abilities matched the Java player's
                   on gameplay-relevant fields; the Java↔Bedrock field-name and
                   case differences make blind copying risky, revisit in 2.5 if
                   a creative-mode save misbehaves).
  Phase 3 (later): equipment + inventory (needs Java↔Bedrock item-id mapping).

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
               _apply_xp, _apply_food, _apply_dimension):
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
