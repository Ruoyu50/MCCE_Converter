# MCCE — NetEase Minecraft Save Converter (Bedrock ↔ NetEase ↔ Java)

Bridge worlds between NetEase iPad Minecraft, standard Bedrock, and Java Edition — both directions.

## What This Does

NetEase's iPad Minecraft app uses a modified Bedrock edition with a per-save XOR-encrypted LevelDB. This tool:

- **Decrypts** NetEase saves to standard Bedrock format.
- **Converts** Bedrock → Java Edition via [Chunker](https://github.com/HiveGamesOSS/Chunker) (playable on macOS, Windows, Linux).
- **Encrypts** a standard Bedrock save back into NetEase format for iPad import (verified end-to-end on real iPad client load).

Pipeline: `NetEase save  ⇄  standard Bedrock  →  Java Edition`. The Bedrock↔Java leg is one-way through Chunker; the NetEase↔Bedrock leg is fully invertible.

## Setup (One Time)

### Prerequisites
- macOS with Apple Silicon or Intel
- Python 3.9+
- Java 17+ (for Chunker; check with `java -version`)

### Installation Steps

1. **Install system dependencies:**
   ```bash
   brew install leveldb snappy
   ```

2. **Set up the project's Python environment:**
   ```bash
   cd /path/to/MCCE_Converter
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

3. **Download Chunker CLI** (one-time; ~30 MB):
   ```bash
   mkdir -p ~/.local/share/mcce
   cd ~/.local/share/mcce
   curl -LO https://github.com/HiveGamesOSS/Chunker/releases/download/1.17.0/chunker-cli-1.17.0.jar
   ```

## Usage

### Three-Step Workflow

1. **Export the save from iPad:**
   - Open NetEase Minecraft app → your world → Edit → Export
   - You'll get a directory with a base-64 name (e.g., `uya1ZzFiBwA=`)
   - Move it to your Mac

2. **Convert to Java Edition:**
   ```bash
   .venv/bin/python mcce.py convert /path/to/uya1ZzFiBwA=
   ```
   This creates:
   - `uya1ZzFiBwA=_decrypted/` — intermediate Bedrock save (standard format)
   - `uya1ZzFiBwA=_java/` — Java Edition save (ready to play)

3. **Copy to Minecraft:**
   ```bash
   cp -r /path/to/uya1ZzFiBwA=_java ~/Library/Application\ Support/minecraft/saves/MyWorld
   ```
   Then open Minecraft Java Edition → Select World → play.

## Subcommands

### `inspect` — Preview Without Converting
```bash
python3 mcce.py inspect /path/to/uya1ZzFiBwA=
```
Shows the stored seed and what the decryption plan will do. Safe to run on the original save (read-only). Uses system Python (no venv needed).

### `decrypt` — Decrypt Only (No Chunker)
```bash
python3 mcce.py decrypt /path/to/uya1ZzFiBwA=
```
Produces `uya1ZzFiBwA=_decrypted/` — a standard Bedrock save. Useful if you want to inspect or edit the intermediate before Java conversion. Uses system Python (no venv needed).

### `convert` — Full Pipeline (Decrypt + Convert to Java)
```bash
.venv/bin/python mcce.py convert /path/to/uya1ZzFiBwA=
```
Decrypt → patch ~local_player → run Chunker → done. **Requires .venv Python** (amulet-leveldb dependency).

### `encrypt` — Standard Bedrock → NetEase iPad Format (Reverse Pipeline)

```bash
.venv/bin/python mcce.py encrypt /path/to/some_bedrock_save
```

Produces `some_bedrock_save_netease/` — a NetEase-format save that the iPad NetEase client loads as a normal world. Verified end-to-end: tested by exporting from iPad → decrypting → re-encrypting → re-importing → iPad loads the full world playable.

#### When to Use

- **Move a friend's NetEase save onto your iPad** — they export, you decrypt with their account's key, then re-encrypt with *your* account's key. (Different iPad accounts use different per-save XOR keys.)
- **Round-trip a Java save back to iPad** — Chunker can convert Java → Bedrock; `encrypt` packages that Bedrock save for NetEase. For the full one-shot pipeline use [`java-to-netease`](#java-to-netease--java-edition--netease-ipad-one-shot) instead of running these two steps by hand.
- **Manually edit an iPad save** — decrypt, mess with NBT or chunks using standard Bedrock tools, re-encrypt, push back to iPad.
- **Recover from an iPad save you've already exported and modified** — as long as you kept the standard-Bedrock intermediate, you can always rebuild the NetEase form.

#### Full Round-Trip Example

```bash
# 1. Export the world from iPad NetEase (Edit → Export). Get a UUID-named dir.
# 2. Decrypt to standard Bedrock.
python3 mcce.py decrypt /path/to/iPad_export
# → /path/to/iPad_export_decrypted/  (standard Bedrock)

# 3. Do whatever you need: convert to Java, edit with mcedit, run a script, ...
.venv/bin/python mcce.py convert /path/to/iPad_export   # → _java/
# ... or hand-edit /path/to/iPad_export_decrypted/db/ ...

# 4. Re-encrypt for iPad import.
.venv/bin/python mcce.py encrypt /path/to/iPad_export_decrypted
# → /path/to/iPad_export_decrypted_netease/

# 5. On iPad: create a fresh empty world in NetEase to claim a save slot.
#    Find its export directory, replace its contents with
#    iPad_export_decrypted_netease/'s contents, re-open the world.
```

#### What `encrypt` Does, In Order

1. **Copy tree** — `<save>_netease/` mirrors the input.
2. **Patch `~local_player`** — open `<save>_netease/db/` with amulet-leveldb, append NetEase's 3-byte trailer (`<XX> 0xc0 0x00`, default `XX = 0x67`) if it isn't already present (signature-idempotent — no-op if already there).
3. **Encrypt sealed files** — for every `db/*.ldb`, `db/MANIFEST-*`, and `db/CURRENT`: prepend the 4-byte NetEase sentinel `\x80\x1d\x30\x01`, XOR the body with the 8-byte keystream. `.log`, `LOCK`, `presets.json`, and root-level files are copied verbatim. Subdirectories (e.g. `lost/`) are left untouched.

#### Flags

- `--keystream <8-char-or-16-hex>` — the per-account XOR key. Default `98518832`.
- `--trailer-byte <0xNN>` — the `XX` byte in the 3-byte `~local_player` trailer. Default `0x67`.

#### ⚠️ Important: Keystream Is Per-Account

The default `--keystream 98518832` is **verified for one specific iPad NetEase account**. Different accounts use different keys. Symptom of a wrong key: iPad opens the save slot, world generation hangs or the save shows as corrupted. Recovery:

```bash
# Export ANY save from the target account and recover that account's key:
python3 mcce.py inspect /path/to/save_from_target_account
# Look for "derived keystream:" in the output.
# Then pass it to encrypt:
.venv/bin/python mcce.py encrypt /path/to/your_bedrock_save --keystream <recovered>
```

The recovered key works for any save you re-encrypt for that account, indefinitely (the key seems tied to account, not save).

#### ⚠️ Important: Use `mcce.py decrypt`'s Output, Not a Hand-Decrypted Copy

A "manual decryption" that XOR-undoes the bytes but skips the 4-byte sentinel strip looks correct (`.ldb` files still end in the SSTable magic, sizes are sensible) but is **silently broken**: its SSTable footer arithmetic is off by 4. amulet-leveldb still opens the directory but only enumerates the keys in the `.log` memtable buffer — older keys living in malformed `.ldb` files are invisible. On the test save this meant **928 keys readable instead of 64,125** (≈98.5% of world data quietly missing) before `encrypt` even gets to its XOR pass.

So: always feed `encrypt` either `mcce.py decrypt`'s output, or a save that originated as standard Bedrock from the start (a Chunker Java→Bedrock conversion, an Amulet/MCEdit export, etc.). Don't feed it a custom-decrypted intermediate unless you know its `.ldb` files pass `idx_off + idx_sz + 5 + 48 == file_size`.

**Requires .venv Python** (amulet-leveldb is needed to modify `~local_player`).

### `java-to-netease` — Java Edition → NetEase iPad (One Shot)

```bash
.venv/bin/python mcce.py java-to-netease /path/to/your_java_save
```

Mirror of `convert` in the opposite direction. Internally runs Chunker (Java → standard Bedrock) and then the `encrypt` pipeline (Bedrock → NetEase) as one command. The intermediate Bedrock save is kept by default so you can inspect or re-run encryption with different flags without redoing Chunker.

Outputs:
- `your_java_save_bedrock_intermediate/` — Chunker's Bedrock output, kept by default for debugging.
- `your_java_save_netease/` — NetEase-format save ready to push to iPad.

#### When to Use

- **Build a world in Java, play it on iPad.** Edit terrain or use Java-only mods, then port back to the NetEase client.
- **Repair a NetEase save by round-tripping through Java.** If the NetEase save has issues a Bedrock-side tool can fix, this is the shortest path back to iPad.
- **Migrate Java saves to NetEase as gifts/shares**, same caveat about per-account keystream as `encrypt`.

#### Full Round-Trip Example (NetEase → Java → NetEase)

```bash
# 1. Pull off iPad and convert to Java.
.venv/bin/python mcce.py convert /path/to/iPad_export
# → /path/to/iPad_export_java/

# 2. Edit / play in Java Edition. (Make a copy first if you care.)
cp -r /path/to/iPad_export_java ~/Library/Application\ Support/minecraft/saves/MyEdit

# 3. After editing, send back to iPad in one shot:
.venv/bin/python mcce.py java-to-netease ~/Library/Application\ Support/minecraft/saves/MyEdit
# → MyEdit_bedrock_intermediate/  (kept for debugging)
# → MyEdit_netease/                (push to iPad)
```

#### Flags

- `-o, --output <path>` — final NetEase output dir (default: `<save>_netease/`).
- `--bedrock-intermediate <path>` — where Chunker's intermediate Bedrock save goes (default: `<save>_bedrock_intermediate/`).
- `-f, --format <STRING>` — Chunker output format for the Bedrock intermediate (default: `BEDROCK_R21_90`, verified on iPad NetEase 3.8.15 / Bedrock 1.21.90). Override if Chunker rejects the default for your Bedrock target version.
- `--player-template <path>` — path to a known-good NetEase save (encrypted or decrypted). **Required unless `--no-translate-player`.** Chunker's Java→Bedrock player is a 9-field stub the NetEase client rejects (no `identifier` / `definitions` / `format_version` / `internalComponents`, so the engine can't instantiate the player and spawns a default at (0,-2,0)). This flag points at a real NetEase save whose `~local_player` is used as a complete entity skeleton; the Java player's fields (position, health, XP, food, dimension, inventory, equipment, enchantments, ender chest, game mode, …) are overlaid onto it. See [Template requirements](#template-requirements) for what makes a good template.
- `--no-translate-player` — skip player translation entirely (produces Chunker's default player; the NetEase client spawns a fresh one). Use when you don't have a template or want to compare.
- `--keystream <8-char-or-16-hex>` — passed through to encrypt (default: `98518832`, current iPad account). Same per-account caveat as `encrypt` — see [⚠️ Important: Keystream Is Per-Account](#%EF%B8%8F-important-keystream-is-per-account).
- `--trailer-byte <0xNN>` — passed through to encrypt (default: `0x67`).
- `--keep-intermediate` / `--no-keep-intermediate` — control whether `<save>_bedrock_intermediate/` survives after a successful encrypt. Default is **keep** (debug-friendly: re-run encrypt with different keystream/trailer without redoing the slow Chunker step).

#### Template requirements

The `--player-template` save donates the `~local_player` entity skeleton that the Java player's fields are overlaid onto. A good template is:

- **A real iPad-exported NetEase save** (encrypted or decrypted). Saves that have been processed by early versions of mcce or similar tools may have lost the NetEase trailer on `~local_player` — those work in some cases but are risky.
- **From the same iPad account whose keystream you're using** — different accounts use different keystreams.
- **A save you've actually played in survival for a few seconds, then saved-and-exited.** Fresh "just-created, never-entered" worlds end up with `PlayerGameMode=5` (an invalid sentinel) which used to fail before Phase 3d; even with the fix, the player's **abilities** inherit whatever the template recorded, so a played-survival save is the cleanest default.
- The example save `B` (`bnqoY7hdBAA=`) used throughout this doc fits the profile: a real survival save, empty ender chest, `PlayerGameMode=0` (survival), survival-default abilities. Any save matching that profile works.

#### Player State: Restored

Chunker's Java → Bedrock pass emits only a minimal player stub, so player state used to be lost entirely. The `--player-template` overlay (player_translate.py) now restores it. For a typical survival save, `java-to-netease` is end-to-end equivalent to playing the Java save itself. Verified on iPad NetEase 3.8.15 (Bedrock 1.21.90):

**✓ Restored (Phase 1b + 2 + 3a + 3b + 3c):**
- **Player position** (Pos, with the Bedrock +1.62 eye-height offset) and **rotation**.
- **Health** (written as the `minecraft:health` attribute, not the legacy top-level short).
- **XP** (level + progress).
- **Food** (hunger / saturation / exhaustion).
- **Dimension** (overworld / nether / end).
- **Inventory** items — id, count, and durability, rebuilt as Bedrock's fixed 36-slot list with slot order preserved.
- **Armor + Offhand** equipment — Java's `equipment` compound mapped to Bedrock `Armor[0..3]` (head/chest/legs/feet) + `Offhand`.
- **Selected hotbar slot** — Java `SelectedItemSlot` → Bedrock `SelectedInventorySlot`, so the held item matches.
- **Enchantments** — Java string enchant IDs → Bedrock numeric `tag.ench` IDs (a ~41-entry table using Bedrock's own 0..37 scheme, e.g. sharpness=9). Enchanted gear and books carry their enchants and levels.
- **Custom-named items** — anvil-renamed items: Java JSON text component → Bedrock `tag.display.Name` plain string.
- **ID-differing items** — the handful of items whose IDs differ between editions (`cobweb`→`web`, `lily_pad`→`waterlily`) are remapped via a conservative override table; everything else passes through unchanged.
- **Ender Chest** contents — uses the same translation pipeline as Inventory (`EnderItems` → `EnderChestInventory`, 27 slots), so enchants, custom names, and id overrides are preserved too.

**Known limitations:**
- **Game mode** — Out of scope. The iPad NetEase client decides the player's game mode based on **what you select when creating the empty placeholder world on iPad** (the new-world dialog's Game Mode dropdown), not the fields in the save data. We tried writing both `level.dat.GameType` and `~local_player.PlayerGameMode` to match the Java player's current mode, but iPad ignores both on load. **Workaround**: when creating the placeholder world on iPad before replacing its db/ contents, select the same game mode as your Java save. If the mode is wrong after import, switch it in Settings → Game Mode (you may need to toggle default first to unlock personal mode).
- **Player skin** — account-bound, not stored in the save; out of scope.
- **Persistent potion/buff effects** — Bedrock doesn't persist `active_effects` in the player NBT the way Java does; out of scope.
- **Fine-grained abilities** — abilities (walk/fly speed, fly permission) are kept from the template, not the Java save. For survival players this is correct; a creative-mode save may show subtle ability discrepancies even once its game mode is set correctly (see above).
- **Old Java save format** (pre-1.20.5, armor in inventory slots 100-103, items using the legacy `tag`/`Count`/`id` shape) — not supported; only the 1.20.5+ component format is handled.
- **Some Java-only blocks** (1.21.4's `leaf_litter`, `bush`, `firefly_bush`) — Chunker replaces with the nearest Bedrock equivalent or air (a Chunker block-level limitation, not player state).

The world itself (terrain, buildings, chests, tile entities) round-trips faithfully.

#### Notes

- Java input must look like a Java save (has `level.dat`, no `db/`). If you accidentally pass a Bedrock save, the command refuses and points you at `encrypt`.
- The keystream caveat from `encrypt` applies verbatim: the default key is verified for one specific iPad account. Use `mcce.py inspect` on a save from the target account to recover the correct key, then pass it through with `--keystream`.
- iPad install procedure is identical to `encrypt`: create a fresh empty world on the iPad NetEase client to claim a slot, then replace its directory contents with `<save>_netease/`.
- **Requires .venv Python** (the encrypt step needs amulet-leveldb to patch `~local_player`). Also requires Chunker JAR at `~/.local/share/mcce/chunker-cli-*.jar` — see [Setup](#setup-one-time).

## What's Preserved vs What's Lost

### ✓ Preserved in Java
- **World terrain, structures, blocks** — everything you built
- **Inventory items** — all 36 slots plus offhand
- **Player position and rotation** — spawn point and view angle
- **Chest contents, furnaces, droppers, etc.** — all tile entity data
- **Mobs and entities** — animals, item frames, etc. (some may look slightly different due to Bedrock↔Java differences)

### ✗ Lost / Defaulted in Java
- **Player Health** — starts at full (10 hearts), must re-eat if you were hurt
- **Hunger/Saturation** — starts at full (10 shanks)
- **XP levels** — resets to 0
- **Ender Chest contents** — empty
- **Abilities** (fly mode, invulnerability, etc.) — lost
- **Some rare Bedrock-specific blocks** — converts to nearest Java equivalent

This is a **Chunker limitation**, not a tool bug. If you need full player state, you'd need a manual post-conversion level.dat editor (not provided here).

## The `--seed` Parameter

You **usually do NOT need `--seed`**. NetEase stores the world seed in `level.dat:RandomSeed`, and Chunker propagates it automatically. Your Java world will have the same seed as your iPad world.

Pass `--seed <N>` only if:
- You want to **regenerate unexplored chunks** with a different seed (e.g., for new terrain generation)
- You explicitly want a custom seed for some reason

Example:
```bash
.venv/bin/python mcce.py convert /path/to/save --seed 12345
```

## How It Works (High Level)

### NetEase → Bedrock (decrypt)

1. **Per-save XOR key recovery.** NetEase encrypts each save's `db/` files with a cyclic 8-byte XOR key that **varies per save** (not a global constant). We recover it via known-plaintext attack on `CURRENT` (always `MANIFEST-XXXXXX\n`), validate the 8-byte period, and cross-check by decrypting a `.ldb` tail and confirming it equals the LevelDB SSTable magic.
2. **Strip the 4-byte sentinel.** Every sealed file (`.ldb`, `MANIFEST-*`, `CURRENT`) has a `\x80\x1d\x30\x01` head NetEase puts on disk; LevelDB never sees it. We drop those 4 bytes after the XOR pass.
3. **`~local_player` trailer.** NetEase appends `<XX> 0xc0 0x00` outside the NBT root compound (a runtime field of NetEase's engine, not corruption). We strip the 3 trailing bytes and append a TAG_End so standard parsers can read it.

### Bedrock → Java (convert)

4. Chunker translates block types, biomes, entities, and player data. Output lands in `~/Library/Application Support/minecraft/saves/<world>/`.

### Bedrock → NetEase (encrypt)

The reverse of decrypt — same primitives in inverse order:

5. **Re-add the `~local_player` trailer** (via amulet-leveldb, before re-encryption — amulet can't open the encrypted form).
6. **Prepend the 4-byte sentinel + XOR with the keystream** for each sealed file. Default keystream is the current iPad account's key; pass `--keystream` for other accounts.

Observed keys so far: `98518832` (current iPad account), `hsx859x4` (a different account; recovered from a UUID-named save export). The decrypt key auto-derives per save; the encrypt key is a flag because the *target* account isn't visible from the *source* save.

## Project Structure

```
MCCE_Converter/
├── README.md                    ← you are here
├── WORKFLOW.md                  ← how I reverse-engineered this
├── LESSONS.md                   ← methodology for future projects
├── mcce.py                      ← main CLI tool
├── patch_localplayer.py         ← standalone LocalPlayer patcher (optional)
├── requirements.txt             ← amulet-leveldb (only dep)
├── .venv/                       ← Python venv (created after setup)
├── .claude/projects/.../memory/ ← session notes (ignore)
└── tests/                       ← future test suite (todo)
```

## Troubleshooting

**"error: chunker-cli not found"**
→ Download Chunker JAR to `~/.local/share/mcce/chunker-cli-*.jar`. See Setup step 3.

**"error: `import leveldb` failed"**
→ You're using system Python instead of `.venv`. Run: `.venv/bin/python mcce.py convert ...`

**"Conversion complete but Java world shows default player state"**
→ This is expected (see "What's Preserved" above). Re-open the world to trigger Minecraft's normal initialization.

**"World looks completely different / wrong seed"**
→ You passed `--seed` with the wrong number, or there's a save-attribution mismatch. Verify with: `python3 mcce.py inspect /path/to/save`

**"CURRENT-derived keystream doesn't have an 8-byte period" or "derived keystream fails the SSTable-magic check"**
→ This save uses an encryption variant we haven't validated. The tool bails rather than guess. `mcce.py inspect <save>` prints what it could derive; if you see this on a real NetEase save, share the first 32 bytes of `CURRENT` and one `.ldb`'s last 64 bytes so the spec can be extended.

**Save's `db/` contains a `lost/` (or other) subdirectory**
→ It's a leftover from a prior `LevelDB::RepairDB` run inside the NetEase client — the world was once corrupted and partially salvaged. Files in `lost/` aren't referenced by `MANIFEST`, so LevelDB and Chunker ignore them; we leave them encrypted in place. Conversion still works on the main save, but any blocks that were only stored in those orphans are gone (the NetEase client lost them, not us).

## License & Notes

Personal project. Use at your own risk. Always keep backups of your original iPad saves.

Tested on:
- macOS 14+ (Apple Silicon, ARM64)
- Python 3.9+
- Java 17+
- Chunker 1.17.0
