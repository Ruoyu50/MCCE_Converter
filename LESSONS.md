# Methodology Lessons from MCCE_Converter Project

Extracted from the reverse-engineering and implementation phases. Apply to future encryption-cracking, binary-analysis, and data-recovery projects.

## Lesson 1: Confirm Source Attribution When Multiple Instances Exist

**The Error:**
User reported "iPad /seed shows value X", I built an entire theory about NetEase seed transformation around that value. Later discovered the value came from a *different* save (a test world they'd created) — not the one being converted. On the actual save, iPad /seed matched the on-disk seed exactly. I'd wasted significant time on a theory chasing a phantom value.

**The Fix:**
When a user provides an externally-sourced numeric fact and multiple comparable instances exist in the conversation (multiple saves, multiple worlds, multiple environments), explicitly ask: "To confirm — that reading is from save X, not save Y?" One sentence. One round-trip cost. Prevents days of dead-end work.

**Application:**
- Multiple saves in play? Verify which one each /seed, log file, or external value came from.
- Multiple API responses? Confirm which endpoint/environment the value came from.
- Multiple tools involved? Confirm which tool produced the output (Chunker vs. user's manual measurement vs. mod output).

**Signal:** Whenever the user says "I ran tool X and got value Y", consider asking "which instance/world/environment?" if multiple are plausible.

---

## Lesson 2: Evidence Must Discriminate

**The Error:**
I verified that `db/000070.ldb`'s last 8 bytes were `57fb808b247547db` (SSTable footer magic). User had verified the same independently. I thought this proved "the encryption spec is correct." But it didn't: both the wrong spec (XOR-all, no strip) and the right spec (XOR-all, strip-4) produce files *ending* in the same 8 bytes, because the strip happens at the head, not the tail.

**The Fix:**
User's measurement was correct but non-discriminating. I should have immediately recognized this and not claimed "your check validates my spec." Instead:

1. State what their measurement *does* verify (key correctness, alignment, direction)
2. State what it *doesn't* exclude (offset shift, prefix bytes, file-length delta)
3. Produce the discriminating measurement (SSTable footer BlockHandle arithmetic showing expected file size matches post-strip, not pre-strip)

**Application:**
- Does the plaintext appear correctly decrypted? (validates key, not necessarily the scope/offset)
- Do the checksums match? (validates integrity, not necessarily structure)
- Does the file open in tool X? (validates broad format, not specific edge cases)

When multiple theories fit the same evidence, find the one that *doesn't*.

**Signal:** If "my theory A" and "your alternative theory B" both match the current evidence, look for a new test that breaks one of them.

---

## Lesson 3: Use Comparative Analysis for Pattern Discovery

**The Method:**
After B save LocalPlayer wouldn't parse, I took a different approach than exhaustive byte-scanning:
1. Collected two real-world examples (U save and B save, different sizes, different trailers)
2. Parsed both with the same error handling
3. Compared their failure points: both stopped exactly 3 bytes from end
4. Compared their final bytes: both had `<XX> c0 00` pattern (XX different, suffix identical)
5. Hypothesis: NetEase appends 3 trailing bytes with a constant suffix
6. Tested hypothesis: strip and re-parse with amulet_nbt → both parse cleanly

This was faster and more reliable than trying to reverse-engineer a single save's structure.

**Application:**
- When binary format is unknown, get 2-3 real examples of the format "broken"
- Don't try to understand one file in isolation; compare structure across files
- Differences tell you what varies (per-save sentinel, version, checksum), constants tell you what's fixed (overhead, magic numbers)

**Signal:** "Why doesn't this file parse?" → first step should be "let me get another example and compare."

---

## Lesson 4: Lazy Imports for Optional Dependencies

**The Pattern:**
amulet-leveldb is only needed for the `convert` subcommand (which modifies level.db's ~local_player key). But `inspect` and `decrypt` commands need only stdlib (files and shutil).

**Implementation:**
```python
def patch_local_player(decrypted_root: Path) -> None:
    try:
        import leveldb  # only import when this function is called
    except ImportError:
        raise SystemExit("error: install amulet-leveldb via: pip install -r requirements.txt")
    # ... rest of function
```

**Benefits:**
- Users can run `python3 mcce.py inspect` without installing anything (faster onboarding)
- Only users who run `convert` need to set up the venv
- If future versions add stdlib-only subcommands, they don't require the venv

**Application:**
- Dependencies that are used by only one code path → lazy import inside that path
- Dependencies used by all entry points → import at top level (fail fast)
- Optional integrations (plugins, extra formats) → lazy import when needed

**Signal:** "This library is only for feature X" → consider lazy import instead of top-level import.

---

## Lesson 5: Idempotency Through Signature Detection

**The Pattern:**
LocalPlayer patcher checks for a specific signature before acting:
```python
if val[-2:] != b'\xc0\x00':
    # trailer signature not present, skip
    if val.endswith(b'\x00'):
        print("already ends in TAG_End; likely already patched")
    return
```

**Benefit:** Safe to re-run without double-patching. If the suffix isn't there, either it was already patched or it's a non-NetEase save (shouldn't happen).

**Application:**
- Data transformations → check for pre-transform or post-transform signature
- Corrections → detect whether correction is already applied
- Migrations → detect the source format/version before migrating

This prevents data corruption from idempotent operations that should be no-ops on re-run.

**Signal:** "If someone runs this twice, what happens?" → design so second run is safe.

---

## Lesson 6: Known-Plaintext Attack for Key Recovery

**The Method:**
- Identify a file type you know the exact contents of (MANIFEST-XXXXXX in LevelDB, specific headers, magic numbers)
- Compare encrypted vs. known-plaintext via XOR
- Recover key bytes by: `encrypted_byte XOR plaintext_byte = key_byte`
- Verify key works across multiple files/offsets

**Why It Works:**
XOR is symmetric: if `encrypted = plaintext XOR key`, then `key = encrypted XOR plaintext`.

**Application:**
- File format magic numbers (first 4 bytes usually fixed)
- Protocol headers (same on every session)
- Database metadata (checksums, sizes that you can compute independently)
- Dictionary/password-based systems (common words, known plaintext)

**Signal:** "I have encrypted data but I don't know the key" → look for something whose plaintext you already know.

---

## Lesson 7: Tail Magic Is Necessary But Not Sufficient — Verify Internal Arithmetic

**The Pattern (Twice-Bitten Version):**
For any framed binary format whose end-marker is a fixed-offset magic — LevelDB SSTables (`57fb808b247547db` at the last 8 bytes), ZIP archives (`PK\x05\x06` near the end), tar archives (zero blocks), most compressed/sealed formats — a tail-magic check is **invariant under a head shift**. Add 4 bytes to the front of an SSTable and the magic is still where it always was: last 8 bytes. Subtract 4 from the front and it's still there. The check passes either way.

This makes tail magic a poor protocol-level validator. It catches "this is roughly the right kind of file" but it does not catch alignment errors, prefix corruption, or off-by-N framing — which are exactly the bugs you get when reverse-engineering a custom wrapper around a standard format.

**The Sufficient Check: Internal Structural Arithmetic.**
SSTables have a footer at the tail containing BlockHandles — `(offset, size)` pairs that point at the metaindex and index blocks earlier in the file. There's an invariant relating them:

```
index_offset + index_size + 5 (block trailer) + 48 (footer) == file_size
```

If your decryption / unframing left N extra bytes at the head, file_size is `N` too large and the equation fails by exactly `N`. The arithmetic check discriminates where the magic check cannot.

**This Project Got Caught Twice:**

1. **Phase 3 (decrypt).** A "decrypt by XOR alone, don't strip" hypothesis was tested by checking the SSTable magic at file end. Magic was correct. The hypothesis was wrong: 4 bytes of NetEase sentinel were sitting at the head. The footer arithmetic showed `5450 + 19 + 5 + 48 = 5522 ≠ 5526` — off by 4. Strip the 4 bytes and arithmetic matched. **The forward pipeline was rescued.**

2. **Phase 9 (encrypt).** A user-supplied "manual decryption" reference passed the tail-magic check on every `.ldb`. It also opened successfully with amulet-leveldb. It even returned `~local_player` correctly. The only signal of trouble was the **key count**: 928 visible keys vs 64,125 in the tool's cleanly-stripped output. The same 4-byte head shift was present, but it lived inside SSTable files this time, not just at file boundaries — amulet-leveldb silently fell back to reading only the `.log` memtable buffer, dropping 98.5% of the world data. **The same trap, in a different costume.**

**The Methodology:**
For any framed format with end-of-file magic, treat the magic as a triage signal — "this is plausibly the right file type" — and **always** follow with a protocol-level arithmetic check before declaring the parse correct:

- LevelDB SSTable: footer BlockHandle offsets + sizes must reconstruct the file size exactly.
- ZIP/JAR: end-of-central-directory record's offset+size must reach `file_size - eocd_size`.
- Container formats (PNG, RIFF, JPEG): chunk-length fields summed must equal total payload.
- Compressed streams: decompressed length matches header-declared length.
- Custom protocols: any "length prefix" inside the format gives a free arithmetic invariant — use it.

If the tail magic checks out but the arithmetic doesn't, you have an alignment bug, not a key bug.

**Application Beyond Binaries:**
The general principle: *signature checks (magic numbers, suffixes, type markers) are weaker than structural checks (size invariants, cross-reference consistency, schema fit) by exactly the amount of framing flexibility the signature ignores.* If the signature lives at a fixed offset, head-shift bugs evade it. If the signature is content-addressed (hash, MAC), it's strong — use those where available. Otherwise, follow up with arithmetic.

**Signal:** When a "this matches the magic" check feels suspiciously cheap — when it would pass even for a slightly-broken file — find the next invariant. There almost always is one.

---

## Lesson 8: Cross-Tool Verification

**The Pattern:**
Don't trust a single tool's output; verify with an independent tool:
- Decoded with custom XOR? Verify with amulet-nbt
- Converted with Chunker? Load in Minecraft and check player inventory
- Modified a binary? Verify with hex dump and a parser

**Application:**
- Tools have bugs; independent verification catches them
- Format parsers can be incomplete; alternate parsers fill gaps
- User expectations (does Java load the world correctly?) is the real test, not just tool exit codes

**Signal:** "Tool X says it worked" → test with independent means to confirm.

---

## Summary of Principles

1. **Source attribution matters** — confirm which instance an external value came from
2. **Evidence must discriminate** — find tests that break wrong theories, not just support right ones
3. **Comparative analysis beats single-case study** — get 2-3 examples and compare
4. **Lazy imports enable incremental adoption** — only install what you use
5. **Idempotency through signatures** — operations that run twice should be safe
6. **Known-plaintext attacks work** — find what you know, XOR to recover keys
7. **Tail magic is triage, not validation** — for any framed format, follow magic with protocol arithmetic; this project got bitten twice by skipping that step
8. **Cross-tool verification confirms correctness** — don't trust any single tool

These apply beyond encryption/binary analysis to any reverse-engineering, data recovery, or integration project.
