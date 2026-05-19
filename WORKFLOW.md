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

## What Worked Well

1. **Known-plaintext attack** — CURRENT file was perfect for key recovery
2. **Footer arithmetic** — discriminated 4-byte-prefix theory from false positives
3. **Comparative analysis** — two saves' trailer patterns confirmed a rule (XX varies, suffix constant)
4. **End-to-end testing** — ran full pipeline on real saves to verify each stage

## What Went Wrong

1. **Seed transformation theory** — wasted time on exhaustive searches for non-existent relationships
2. **plyvel dependency** — spent hours on RTTI debugging before switching packages
3. **Assumption about all files being encrypted** — had to be tested and discriminated from the .log plaintext case
