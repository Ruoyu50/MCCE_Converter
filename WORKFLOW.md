# MCCE Reverse-Engineering Workflow

How this tool was built from "I have an encrypted save file" to "working Java save with preserved inventory."

## Phase 1: Reconnaissance

### The Starting Point
```
Raw iPad save at: /Users/shiruoyu/Downloads/uya1ZzFiBwA=/
Contains:
  - level.dat (2.8 KB, binary)
  - level.dat_old (backup)
  - db/ (LevelDB directory with .ldb, .log, MANIFEST-*, CURRENT)
  - behavior_packs/, resource_packs/ (unencrypted config)
  - levelname.txt, netease_world_*.json (plaintext)
```

**Observation:** `level.dat` looked mostly random-ish binary, but the `db/` files looked even more random. Hypothesis: `level.dat` might be uncompressed NBT (standard Bedrock), `db/` files might be encrypted.

### Validation
```bash
xxd db/000070.ldb | head   # hex dump shows no recognizable pattern
xxd level.dat | head       # also random, but let's check the last 8 bytes
```

**Finding:** The very last 8 bytes of `db/000070.ldb` were `57fb808b247547db` — that's the well-known LevelDB SSTable footer magic. So the file IS LevelDB underneath, but its *content* is encrypted.

## Phase 2: Known-Plaintext Attack

### The Key Question
If we know what some data *should* be (plaintext), and we have the encrypted version, we can XOR to recover the key.

**Candidate plaintext:** The `db/CURRENT` file in LevelDB is always tiny (~20 bytes) and always contains `MANIFEST-XXXXXX\n` (exactly 16 bytes after 4-byte framing). This is a strong known-plaintext candidate.

### Executing the Attack
```
db/CURRENT raw hex: 0x801d3001 75797d7b...
Known plaintext: "MANIFEST-000004\n" (16 bytes)
XOR the first 16 bytes of raw against the plaintext:
  0x801d3001 XOR 0x4d4146 = b'9', b'8', b'5', ...
  Pattern: b'98518832' repeating
```

**Result:** XOR key is `b'98518832'` (8 bytes, repeats cyclically). Verified by decrypting the rest of CURRENT and confirming it ends in the known pattern.

**Correction (later, on a second save):** The 8-byte key is **per-save, not a global NetEase constant**. A second save (`b41410b4-…`) recovered key `b'hsx859x4'`. The known-plaintext attack itself generalizes — only the assumption "98518832 is universal" was wrong. `mcce.py` now derives the keystream from `CURRENT + MANIFEST-XXXXXX\n` at every run and cross-checks it against the SSTable magic on a `.ldb` tail. If either check fails it bails rather than guess. See [Phase 8](#phase-8-per-save-keys-and-orphan-data) below.

## Phase 3: Understanding the Encryption Scope

### Question: Does the key apply to ALL db/ files?

Tested on `db/000070.ldb`:
```
Decrypt full file: decrypt(raw, key=b'98518832', offset=0)
Check last 8 bytes: should be 57fb808b247547db (SSTable magic)
Result: YES — last 8 bytes are exactly the magic
```

Tested on `db/000071.log`:
```
Decrypt first 7 bytes (LevelDB log record header):
  Raw: c864e532f97f02...
  Decrypted: f15cd003c147... (looks like random junk)
  But looking at the raw bytes in plain English:
    Byte 0-3: CRC32, looks like any 4 bytes
    Byte 4-5: 0x7ff9 (little-endian) = 32761
    Byte 6: 0x02 (type = kFirstType)
    Raw interpretation: CRC(0xf15c...), length=32761, type=kFirstType
    Expected: length + 7 bytes of overhead = 32768 (exactly one log block)
  Conclusion: .log is NOT encrypted (raw bytes already parse as valid LevelDB log)
```

**Finding:** .log files are plaintext. Decrypting them would destroy them. Only .ldb and MANIFEST-* are encrypted.

### The 4-Byte Prefix

After decrypting `db/000070.ldb`, the first 16 bytes were:
```
b9 25 05 30 ec bd 0b 74 ...
```

That `b9 25 05 30` looked like garbage (not valid SSTable header). Standard SSTable data blocks don't start with those bytes. Hypothesis: NetEase prepended 4 garbage bytes to every sealed file.

**Verification via SSTable footer arithmetic:**
- SSTable footer is last 48 bytes, contains BlockHandle (varint offset + varint size)
- For `db/000070.ldb`: footer shows `metaindex_off=1297162 sz=50`, `index_off=1297217 sz=798`
- Expected file size = 1297217 (index offset) + 798 (index size) + 5 (block trailer) + 48 (footer) = 1298068
- Raw decrypted size: 1298072 (4 bytes too long)
- **After stripping first 4 bytes:** 1298068 ✓ exact match

This proved the 4-byte prefix must be stripped from every encrypted file, not just CURRENT.

## Phase 4: LocalPlayer Corruption

### The Problem
After converting B save with Chunker, the Java world loaded but player spawned with:
- Empty inventory (should have 18 items)
- Default position (not saved location)
- Default health/hunger (expected given Chunker's limitations)
- Chunker's stderr: `Unknown tag type 103` during `LocalPlayer` parsing

### Investigation: Byte-Level Inspection
Extracted `~local_player` value from LevelDB (25,091 bytes for B save).
```
Parsed Bedrock NBT with amulet_nbt library: stops at offset 25088 (3 bytes from end)
Error: "Descriptor does not contain a meta-nextfile entry" (MANIFEST corruption symptom)
```

**Hypothesis:** The last 3 bytes are NetEase corruption.

### Comparative Analysis: U Save vs B Save
U save `~local_player` (11,730 bytes):
```
Last 3 bytes: 0x35 c0 00
Trim to 11,728 bytes, append 0x00 (TAG_End)
amulet_nbt parses cleanly: 112 top-level fields
```

B save `~local_player` (25,091 bytes):
```
Last 3 bytes: 0x67 c0 00
Trim to 25,089 bytes, append 0x00 (TAG_End)
amulet_nbt parses cleanly: 112 top-level fields
```

**Pattern:** Both saves have structure `<XX> 0xc0 0x00` where XX varies (0x35 vs 0x67). The suffix `0xc0 0x00` is identical (and it's not a valid NBT tag or name-length — it's exactly the u16 value 49344 / 0xc0 00, which makes no sense structurally).

**Fix:** Strip the 3-byte trailer, append TAG_END. Result: Chunker no longer errors, Java player has inventory + position.

## Phase 5: The Seed Transformation Myth

### Original Theory (WRONG)
User reported: "iPad /seed shows 2351985503704928881, but level.dat:RandomSeed is 1644545031153813197. Chunker converts with the stored seed, so Java shows wrong biome (Badlands instead of Forest)."

Built entire flow around `--seed` being required. Exhaustive testing:
```
Brute-force byte search: 2351985503704928881 appears nowhere in either save (LE or BE)
Transform tests: Java Random.nextLong(), ~bitwise, SHA-256, MD5 of stored seed → none produce the iPad value
```

No relationship found. Theory: NetEase transforms seeds at runtime in their modified client.

### The Correction
User clarified: The iPad seed value (2351985503704928881) **came from a different save** (a blank test world they'd created earlier). On the actual save being converted (uya1ZzFiBwA=), iPad's `/seed` and level.dat:RandomSeed **both show 1644545031153813197** — they match exactly.

**Root cause:** Save attribution error in the conversation (user had multiple saves in progress, I didn't ask which one the /seed was from).

**Consequence:** Revoked the entire seed-transformation theory. `--seed` became optional (override only). Chunker handles the default case.

### Lesson
When users provide externally-sourced values and multiple instances exist in conversation, **confirm which instance the value came from** before building theory. Saved weeks of work pursuing a phantom transformation.

## Phase 6: Dependency Hell

### Problem 1: plyvel Installation
Tried `pip install plyvel` → compile failed:
```
fatal error: 'leveldb/db.h' file not found
```
Solution: `brew install leveldb snappy`, then set CPPFLAGS + LDFLAGS explicitly → still failed with RTTI mismatch on macOS.

### Problem 2: Dynamic Linking on macOS
plyvel compiled but `dlopen()` failed:
```
symbol not found in flat namespace '__ZTIN7leveldb10ComparatorE'
```
This is RTTI incompatibility between plyvel's C++ and Homebrew's leveldb ABI.

### Solution: amulet-leveldb
Switched to `amulet-leveldb` (used by the Amulet Editor project, designed for Bedrock saves specifically). Installed cleanly:
```bash
pip install amulet-leveldb
```
Works perfectly for read/write of LevelDB keys.

## Phase 7: Tool Integration

### Architecture Decisions
1. **Read-only source:** Source save never modified; all output to `<name>_decrypted/` and `<name>_java/`
2. **Lazy imports:** amulet-leveldb only needed for `convert` (which runs Chunker); `inspect` and `decrypt` stay stdlib-only
3. **Chunker integration:** Invoke via subprocess with `-s` JSON to override seeds when needed (but not needed by default)
4. **Idempotency:** LocalPlayer patcher checks trailer signature before acting; safe to re-run

### End-to-End Test Results
B save (large played world, 25 KB LocalPlayer):
- Decryption: 70 .ldb files, 1 .log, MANIFEST → all handled correctly
- LocalPlayer patch: XX=0x67, stripped and TAG_END appended
- Chunker conversion: 100% completion, no "Unknown tag" errors
- Java output: 18-item inventory ✓, position (-206.47, 71, -2137.61) ✓, furnaces still burning ✓

## Phase 8: Per-Save Keys and Orphan Data

### Surprise: Second Save, Different Key
Tested on a third save (`b41410b4-49c3-4570-9926-f2174faaf4e5/`, UUID-named — likely an older NetEase export format). Running the existing tool with the hardcoded `b'98518832'`:
- `CURRENT` decrypts to garbage (not `MANIFEST-XXXXXX\n`)
- `.ldb` tail decrypts to garbage (not SSTable magic)

Re-ran the known-plaintext attack on this save's `CURRENT`:
```
raw[4..20] XOR "MANIFEST-000890\n" =
  68 73 78 38 35 39 78 34 68 73 78 38 35 39 78 34
  = b"hsx859x4" (repeated twice) — a different 8-byte key
```

**Conclusion:** Each NetEase save has its own 8-byte XOR key. The B save's `98518832` was just one instance, not a NetEase-wide constant. Likely tied to user account, device, or world creation time — we don't need to know which; we just recover it per save.

### Implementation: Auto-Derive Every Run
```
1. Read CURRENT (must be 20 bytes; 4-byte sentinel + 16-byte ciphertext).
2. Find largest-numbered MANIFEST-XXXXXX in db/. Build plaintext "MANIFEST-XXXXXX\n".
3. window = raw[4..20] XOR plaintext (16 bytes).
4. Assert window[0:8] == window[8:16] (8-byte cyclic period sanity).
5. Rotate window so it indexes from file offset 0 → that's the keystream KS.
6. Cross-check: decrypt last 8 bytes of any .ldb with KS; must equal SSTable magic.
7. Apply KS to every sealed file in db/; strip 4-byte sentinel.
```
If either check fails, bail — better than guessing.

### Side-Note: `db/lost/` Subdirectory
The UUID save had a `db/lost/` directory containing orphan `.log` and `MANIFEST-*` files (also encrypted, but referenced by no MANIFEST). This is the signature of a prior `LevelDB::RepairDB` run inside the NetEase client — the database was corrupted at some point and the client salvaged what it could into `lost/` before continuing. LevelDB and Chunker ignore unreferenced files, so we leave `lost/` encrypted in place and emit a one-line notice. Any chunks that lived only in those orphans are already gone from the user's perspective; conversion proceeds on the rest of the save.

### Bedrock 1.18 → Java 1.21 Quirks Surfacing
On this older save Chunker produced two new categories of non-fatal warnings during conversion:
- `Unknown dimension key 1769108563` — a NetEase-specific dimension ID Chunker doesn't recognize (1769108563 = 0x6964_6174 = ASCII `idat`). Chunker still produces output; the dimension is skipped.
- `Missing entity mapping for ChunkerCustomEntityType{identifier='netease:warthog' | 'netease:ender_dragon' | 'netease:wither' | 'netease:ender_man'}` — NetEase reskins of vanilla mobs. Chunker writes them as the closest vanilla equivalents or drops them.

Neither breaks the world; they're a Chunker-side limitation, not a converter bug.

## Phase 9: Reverse Encryption and an Old Trap Re-Caught

### Goal: Push Bedrock Back Into iPad
Original tool was one-way: NetEase → Bedrock → Java. Use case for the reverse: a friend's NetEase save needs to land on a *different* iPad account, or a Java world should be round-tripped back to iPad after editing. The inverse must reseal `db/` and re-add anything our forward pass stripped.

### The Trailer Wasn't Corruption — It's a Runtime Field
Going into Phase 4 we believed the 3-byte `<XX> 0xc0 0x00` trailer on `~local_player` was one-time corruption Chunker tripped on. To invert, we needed to *re-add* it — which forced us to look at the trailer across more saves than just U and B. Inventory across four saves:

| save | XX byte |
|---|---|
| U save | 0x35 |
| B save | 0x67 |
| kEx (today's iPad state, freshly saved) | 0x67 |
| b41410b4 (UUID, older export) | 0x67 |

`XX` varies but `0x67` is what the current iPad client writes; three of four saves carry it. The byte's meaning is still unknown — not a checksum, not monotonic given U/B differ — but **the iPad client writes this trailer every save, and re-reads it on every load**. It's not damage to repair; it's an engine field outside the standard NBT structure. The implication is that the previous mental model ("strip and forget") was incomplete: for the round-trip to work we have to reproduce the trailer on the way back. `mcce.py encrypt` defaults to `XX = 0x67`; both values verified loadable.

### `encrypt` Implementation: Inverse Primitives, Inverse Order
The encrypt pipeline mirrors decrypt + patch in reverse:

```
1. Copy save tree → <save>_netease/
2. amulet-leveldb opens <save>_netease/db/ and appends `<XX> 0xc0 0x00` to
   ~local_player (signature-idempotent: skips if already present).
   MUST run BEFORE step 3 — amulet can't open the encrypted form.
3. For each db/*.ldb, db/MANIFEST-*, db/CURRENT:
     output = b"\x80\x1d\x30\x01" + xor(plain[i] with ks[(i+4) % 8])
   .log / LOCK / root files: copy verbatim. Subdirs: leave alone.
```

End-to-end verification: exported a NetEase save → `mcce.py decrypt` → `mcce.py encrypt` (with the default `98518832` key, the verified current-account key) → "new + replace" import on iPad → iPad loaded the full world playable, inventory and position preserved.

### The Old Trap, Re-Caught: Tail Magic Is Not Enough
The reverse direction exposed a quiet bug in a *different* artifact that the forward direction had been hiding. Two candidate inputs for `encrypt`:

| input | source | `.ldb` size | last 8 bytes | footer arithmetic | amulet-leveldb keys |
|---|---|---|---|---|---|
| `b41410b4_manual_decrypted/` | hand-rolled XOR-only "decrypt" | 5526 | SSTable magic ✓ | `5450 + 19 + 5 + 48 = 5522 ≠ 5526` (off by 4) ✗ | **928** |
| `b41410b4-…_decrypted/` | `mcce.py decrypt` (XOR + strip-4) | 5522 | SSTable magic ✓ | `5450 + 19 + 5 + 48 = 5522 ✓` | **64125** |

Both files pass the tail-magic check. Both even look fine when opened with amulet-leveldb — *it opens, it returns a handle, it returns `~local_player`*. The only signal that something is wrong is the **key count**: 928 vs 64,125 — a 98.5% silent data loss. amulet-leveldb falls back to reading just the `.log` memtable buffer when the `.ldb` files are malformed, exposing only the most recent writes; older keys living in the off-by-4 SSTables are invisible.

This is exactly the same shape of trap as Phase 3: the SSTable magic is at a *fixed offset from the end*, so it's invariant under a 4-byte head shift. Phase 3 caught it during decrypt by computing footer arithmetic; Phase 9 caught it again during encrypt because amulet-leveldb's key count exposed the gap. Both incidents are the same lesson, restated below in `LESSONS.md`: **for any framed format whose end-marker is a fixed-offset magic, the tail-magic check is necessary but never sufficient**. You must verify the internal structural invariants (footer offsets, length fields, BlockHandle arithmetic) before believing decryption produced a valid file.

### Bonus: amulet-leveldb Silently Compacts On Open
amulet-leveldb runs internal LevelDB compaction during its open/close cycle. On the `manual_decrypted` input it consolidated the 928 visible keys into freshly-written `000895.ldb` / `000896.ldb`, replacing the malformed `.ldb`s — those compacted SSTables have correct footer arithmetic, so our subsequent XOR pass produces a well-formed NetEase file. But the well-formed output only contains the 928 keys amulet ever saw; the other 63,000 keys never made it to the round-trip. The fix is one level up: always feed `encrypt` a *cleanly-decrypted* source, i.e. `mcce.py decrypt`'s output, not a hand-rolled XOR-only version.

## Phase 10: Reverse Pipeline as a Subcommand (`java-to-netease`)

### Goal
Phase 9 made the NetEase↔Bedrock leg invertible, but a Java→iPad round-trip still meant running two commands by hand: Chunker (Java → standard Bedrock), then `mcce.py encrypt`. The goal was a single subcommand mirroring `convert` in the opposite direction.

### Implementation
`java-to-netease` chains the two halves internally: Java → Chunker → standard Bedrock → `encrypt` → NetEase (commit `b1f2ffb`). The intermediate Bedrock directory (`<save>_bedrock_intermediate/`) is kept by default, so the slow Chunker step doesn't have to be repeated when re-running encryption with a different keystream/trailer. The default Chunker output format for that intermediate is `BEDROCK_R21_90`, chosen from real-world testing against iPad NetEase 3.8.15 / Bedrock 1.21.90.

### Finding: world round-trips, player doesn't
First iPad test of the new subcommand: terrain and buildings came back intact, but player state did not. Chunker's Java→Bedrock pass loses position, inventory, health, XP — and on the iPad NetEase client the player spawned at the hard-default `(0, -2, 0)` with an empty inventory rather than at world spawn. `b1f2ffb` shipped this as a documented known limitation with "fix coming in a follow-up tool."

### Lesson
A round-trip that preserves *world* data is not a round-trip that preserves *player* data — the two travel through different parts of the format and must be verified independently. The (0,-2,0) spawn was the thread that unraveled into Phase 11.

## Phase 11: Why Chunker's Player Stub Fails on iPad

### Goal
Find out why the Phase 10 player ended up at (0,-2,0) instead of carrying over, and design a fix.

### Investigation
The first hypothesis — "our tool corrupted the player NBT" — was ruled out: the `java-to-netease` output spawned the default player whether or not any Phase-1 field edits were applied (player_translate.py docstring), which points away from our edits and at the structure Chunker produced. Comparing Chunker's `~local_player` against a real iPad NetEase player:

| source | `~local_player` fields |
|---|---|
| Chunker Java→Bedrock stub | **9** (Pos, Motion, Rotation, Inventory, Armor, Offhand, DimensionId, PlayerGameMode, Attributes) |
| real NetEase player | **95 to 112 across the saves we examined** (P/1.18 = 95, B-iPad/1.20.50 = 112, B-Desktop/1.21.90 = 112) |

### Finding: missing identity fields, not missing data
The 9-field stub lacks the entity-identity fields the NetEase Bedrock engine needs to instantiate a LocalPlayer: `identifier`, `definitions`, `format_version`, `UniqueID`, `internalComponents`, abilities, and the dozens of `Is*`/`Spawn*` fields. Without them the engine can't build the player at all and falls back to a fresh default at (0,-2,0). The problem isn't that Chunker wrote *wrong* values; it wrote *too few fields* for NetEase to instantiate anything.

### The fix concept: template overlay
Instead of synthesizing ~100 missing fields from spec, borrow them. Read a real, iPad-accepted NetEase `~local_player` as a complete skeleton, then overlay only the Java-derived fields onto it (commit `d62bff5` introduces `--player-template`). Three ground-truth facts (from reading real NetEase iPad 3.8.15 / Bedrock 1.21.90 players in saves P, B-iPad, B-Desktop) made this safe rather than merely convenient:

1. **`UniqueID` is a fixed sentinel `-4294967295`** (0xFFFFFFFF00000001), byte-for-byte identical across every NetEase save examined. It is the designated local-player value, not a per-world allocation — and `~local_player` is a single per-world slot — so copying the template's UniqueID can't collide. `identifier="minecraft:player"` and `format_version="1.12.0"` are likewise constant.
2. **Health lives in the `minecraft:health` attribute** (Current/Max floats), not a legacy top-level `Health` short.
3. **Real NetEase player strings contain raw 0x00 bytes** (non-standard modified-UTF-8) that a strict mutf-8 decoder rejects; the template is parsed and re-serialized with amulet_nbt's escape codec, verified byte-identical, so the overlay stays faithful to NetEase's on-disk format.

### Lesson
When a downstream consumer rejects your output, check field *presence* before field *values* — this failure was structural (too few fields to instantiate), not numeric. And "borrow a known-good skeleton and overlay" beats "synthesize from spec" when the spec surface is ~100 fields wide and only a handful matter.

## Phase 12: Player Field Overlay (Phases 1b → 3c)

### Goal
With the template-overlay mechanism in place, restore each user-visible player field in small, individually-verifiable increments, each validated end-to-end on iPad NetEase 3.8.15 before moving on.

### The increments
- **Phase 1b** (`d62bff5`): `Pos` (with the Bedrock +1.62 eye-height offset — Bedrock stores eye level, Java stores feet; Chunker applies the same offset, observed Java feet 63.0 → Bedrock 64.62), `Rotation`, and `Health` written as the `minecraft:health` attribute Current (not the legacy short).
- **Phase 2** (`d62bff5`): XP (`PlayerLevel` + `PlayerLevelProgress`, mirrored into the level/experience attributes), food (`player.hunger`/`saturation`/`exhaustion` attributes — no top-level food fields exist in Bedrock), and Dimension (Java string → Bedrock `DimensionId` int).
- **Phase 3a + tail** (`f9d26e2`, then `f9885de`): Inventory rebuilt as Bedrock's fixed 36-slot list (empty slots filled `{Count:0, Name:""}`, slot order preserved), and the new Java 1.20.5+ `equipment` compound mapped to Bedrock `Armor[0..3]` (head/chest/legs/feet) + `Offhand[0]`. Items in 3a were basic only — id (Java string → Bedrock string, ~95% identical), count clamped to byte range, durability from `components.minecraft:damage` → top-level `Damage` short. `SelectedInventorySlot` shipped separately as a small follow-on commit (`f9885de`) closing out 3a: Java `SelectedItemSlot` → Bedrock `SelectedInventorySlot`, field names confirmed by reading a real NetEase player.
- **Phase 3b** (`be07a84`): the harder item conversions — enchantments (Java flat `{"minecraft:sharpness":5}` 1.20.5+ format → Bedrock `tag.ench [{id, lvl, modEnchant}]`, a ~41-entry table using Bedrock's own 0..37 scheme e.g. sharpness=9, double-checked against the B template's diamond_pickaxe fortune=18/mending=26), stored enchantments on enchanted books, custom names (Java JSON text → Bedrock `tag.display.Name` plain string), and a conservative item-id override table (`cobweb`→`web`, `lily_pad`→`waterlily`).
- **Phase 3c** (`4127a68`): ender chest (Java `EnderItems` → Bedrock `EnderChestInventory`, 27 slots) — reuses `translate_item_java_to_bedrock` end-to-end, so enchants / custom names / id overrides apply for free. If Java `EnderItems` is absent (old-format save) it's skipped and the template's chest kept; if present (even empty), all 27 slots are rebuilt so Java state wins.

### Two deliberate ground-truth calls
- **Emit NetEase's empty `modEnchant:""` field.** NetEase adds an empty-string `modEnchant` to each `ench` element that standard Bedrock doesn't have; we emit it anyway to match the template's byte-layout (`be07a84`).
- **Never overlay `abilities`.** abilities (walk/fly speed, fly permission) are kept from the template, not the Java save. Research showed survival-mode players match the template on gameplay-relevant fields, and Java↔Bedrock field-name/case differences make blind copying risky; the documented caveat is that a creative-mode save against a survival template can show ability discrepancies (`d62bff5`, revisited in Phase 13).

### Lesson
Reusing one item-translation function across Inventory, Equipment, and EnderChest meant the enchant / custom-name / id-override logic was written once and three features got it for free (Phase 3c explicitly reuses `translate_item_java_to_bedrock` end-to-end). Small, individually-verified increments kept every regression localized to one field.

## Phase 13: Game Mode — A Failed Theory and Its Documentation

### Goal
Make the iPad load the *Java save's* current game mode directly, so a creative Java world shows creative on import instead of requiring a manual in-app switch.

### The attempts
- **Phase 3d** (`8b40e87`): copy Java `playerGameType` → Bedrock `~local_player.PlayerGameMode` (both editions share 0=survival/1=creative/2=adventure/3=spectator for the explicit values). This was implicit before — every prior iPad test used the B template whose `PlayerGameMode=0`, so output always showed survival regardless of the Java save.
- **Phase 3e** (worktree change, no standalone commit): also write `level.dat.GameType`, in case the world-default field was the one the loader honored.

### Investigation: three controlled iPad experiments
Holding the save bytes fixed and varying only the iPad new-world UI selection (EXPERIMENTS/02 §4):

| experiment | level.dat.GameType | PlayerGameMode | iPad UI pick | iPad loaded mode |
|---|---|---|---|---|
| 3d test 1 | 0 (Chunker) | 1 (we wrote) | Survival | **Survival** |
| 3d test 2 | 0 (Chunker) | 1 (we wrote) | Creative | **Creative** |
| 3e | 1 (we wrote) | 1 (we wrote) | Survival | **Survival** |

test 1 vs test 2 hold the save identical and change only the UI pick — the result follows the UI. 3e sets `level.dat.GameType=1` against a Survival UI pick — still Survival.

### Finding
Both fields we can write — `~local_player.PlayerGameMode` and `level.dat.GameType` — are **ignored by the iPad NetEase client on world load**. The mode the client loads instead comes from **the game mode the user picked when creating the blank placeholder world in the iPad UI**. We do not know where that selection is stored — plausibly a file inside the NetEase app sandbox, or device-level state — but it is demonstrably **outside the save data mcce can write** (`db/` + `level.dat`). (iPad *does* write `PlayerGameMode` when the player switches mode in-game; it just isn't read as the load-time source of truth.)

### Decision: forward-only revert
Phase 3d/3e were removed in commit `75f7465` — as a new commit that deletes the dead code, not a `git revert`/`reset`, so history stays linear and the reasoning is preserved in the commit message. Game mode was documented as out-of-scope in the README with the workaround "pick the matching mode in iPad's new-world dialog." All other player-state fields remained restored and verified. The research write-up was moved out of git history into `OPEN_QUESTIONS.md` #1 and `EXPERIMENTS/02` (commit `eaa9194`).

### Postscript: online research corrected a mis-attribution (`cdd3cc5`)
A fresh blank iPad world's player carries `PlayerGameMode = 5`, which earlier notes (EXPERIMENTS/02 §3.3) had called an "invalid sentinel" responsible for the (0,-2,0) failure. Online research against the authoritative minecraft.wiki "Bedrock Edition level format" page corrected this:
- Bedrock's GameType enum is **0/1/2/5/6** (0=Survival, 1=Creative, 2=Adventure, **5=Default**, 6=Spectator), not Java's 0..3.
- **`5 = "Default"`** is a normal, valid value meaning the player follows the world's default game mode — not a corrupt sentinel.
- So the real cause of the (0,-2,0) instantiation failure was the Phase 11 Chunker 9-field stub (missing identity fields), **not** `PlayerGameMode=5`.

This correction **does not change Phase 13's conclusion**: valid value or not, the fields we wrote in Phase 3d/3e are still ignored by iPad on load, so the revert stands. It does add one falsifiable follow-up (`OPEN_QUESTIONS.md`): now that `=5` is understood as valid, a fresh blank world might actually work as a clean template — worth a dedicated re-test, since it had been dismissed on the mis-attributed `=5` failure.

### Lesson
Two suspect causes co-occurred (the 9-field stub and `PlayerGameMode=5`); we initially named the wrong one. Isolate co-occurring causes with controlled experiments before attributing a failure. And don't ship fields with no user-observable effect: code whose output the consumer ignores is worse than no code, because it implies a guarantee that isn't there — if you can't verify a field is read, document it as unread instead of shipping it.

## Phase 14: Documentation Cleanup

### Goal
The README had been written as a standalone user-facing artifact and described iPad export/import as if they acted on directory *objects*. Correct that to match how the operations actually work (commit `276f92f`).

### What changed
Export and import are **user actions through the iOS Files app**, not directory objects:
- **Export** = open Files → On My iPad → Minecraft → minecraftWorlds → long-press the base64-named world directory → Share to the Mac. (There is no in-app "Edit → Export" on iOS NetEase.)
- **Import** = create a fresh blank world in the NetEase client (which makes a new base64-named directory under minecraftWorlds), exit the client so it flushes state, then replace that directory's **contents** with the tool output's **contents** (contents-into-contents, not directory-over-directory), then **open** the slot in NetEase — a first open, not a "re-open", since the client was already exited.
- All flows are labeled iOS; the Android flow is not verified by this project.

### Three known README inconsistencies left on record (not fixed this session)
- **base-64 vs UUID naming** of the exported directory.
- **README's "output lands in `~/.../saves/<world>/`"** vs the tool's actual `<save>_java/`.
- **Two coexisting example save names** (`uya1ZzFiBwA=` and `bnqoY7hdBAA=`).

### Meta-insight
These inconsistencies have authoritative answers earlier in this workflow (Phases 1, 7, 8): Phase 8 recorded that UUID-named dirs are the *older* NetEase export format; Phase 7 recorded that all converter output goes to `<name>_decrypted/` and `<name>_java/` next to the source; and the two save names trace the real testing history — Phase 1 started on `uya1ZzFiBwA=`, Phase 4 introduced the B save (`bnqoY7hdBAA=`). The gap is structural: README was written from scratch as a user-facing artifact, without back-references to the relevant Phases. Resolving the README inconsistencies is straightforward when the WORKFLOW source of truth is consulted; left undone in this session by user choice (treating the README inconsistencies as preserved testing history rather than typos).

## What Worked Well

1. **Known-plaintext attack** — CURRENT file was perfect for key recovery
2. **Footer arithmetic** — discriminated 4-byte-prefix theory from false positives
3. **Comparative analysis** — two saves' trailer patterns confirmed a rule (XX varies, suffix constant)
4. **End-to-end testing** — ran full pipeline on real saves to verify each stage
5. **Template overlay pattern** (Phase 11) — instead of synthesizing the full entity skeleton from scratch, borrow a known-good one and overlay only the fields that matter
6. **Reuse of `translate_item_java_to_bedrock`** across Inventory / Equipment / EnderChest (Phase 12: 3a/3b/3c) — enchant + custom-name + id-override logic written once, applied to all item-bearing fields
7. **Forward-only revert** (Phase 13) — when Phase 3d/3e proved ineffective, removed the code as a new commit rather than `git revert`/`git reset`, so history stays linear and the learning is preserved in the commit message

## What Went Wrong

1. **Seed transformation theory** — wasted time on exhaustive searches for non-existent relationships
2. **plyvel dependency** — spent hours on RTTI debugging before switching packages
3. **Assumption about all files being encrypted** — had to be tested and discriminated from the .log plaintext case
4. **Mis-attribution of `PlayerGameMode = 5` as "invalid sentinel"** (Phase 13) — the (0,-2,0) failure was actually Chunker's 9-field stub (the Phase 11 root cause), not `PlayerGameMode = 5`; online research later showed 5 = "Default" is a valid value. Lesson: when two suspect causes co-occur, isolate them with controlled experiments before naming one as "the" cause
5. **Wrote-but-not-read fields** (Phase 13) — Phase 3d/3e wrote game-mode fields that iPad NetEase ignores on load. Code without user-observable effect is worse than no code; if you can't verify the field is read, document it as such instead of shipping under false pretenses
