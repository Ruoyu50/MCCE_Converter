#!/usr/bin/env python3
"""
mcce — NetEase Bedrock → standard Bedrock → Java Edition converter.

Pipeline:
  netease_save/            (read-only, never touched)
    -> netease_save_decrypted/  (standard Bedrock layout, written by us)
    -> netease_save_java/       (Java Edition layout, written by Chunker CLI)

Running
-------
`convert` needs the project venv because the ~local_player patcher imports
amulet-leveldb. `inspect` and `decrypt` are stdlib-only and run with any
Python 3.

  One-time setup (needed only for `convert`):
      brew install leveldb snappy
      python3 -m venv .venv
      .venv/bin/pip install -r requirements.txt

  Commands:
      python3          mcce.py inspect <save>                # stdlib only
      python3          mcce.py decrypt <save>                # stdlib only
      .venv/bin/python mcce.py convert <save>                # needs venv

  Seed handling: NetEase stores the world's seed in level.dat:RandomSeed
  unchanged (matches what iPad's `/seed` shows). Chunker propagates it into
  Java's WorldGenSettings.seed by default, so `--seed` is normally not
  needed. Pass `--seed <N>` only to override (e.g. to regenerate unexplored
  chunks under a different seed).

  Chunker CLI is auto-located at ~/.local/share/mcce/chunker-cli-*.jar; if
  missing, `convert` prints download instructions and exits.

Verified encryption spec:

  | file                | XOR with derived key | strip 4-byte prefix |
  | db/*.ldb            | yes                  | yes                 |
  | db/MANIFEST-*       | yes                  | yes                 |
  | db/CURRENT          | yes                  | yes                 |
  | db/*.log            | NO (already plain)   | no                  |
  | db/{LOCK,*.bak,...} | no                   | no                  |
  | db/<subdir>/*       | NO (orphans, skip)   | no                  |
  | root files          | no                   | no                  |

The 8-byte XOR key is NOT a global constant — it varies per save. We recover
it via a known-plaintext attack on CURRENT (whose plain form is always
`MANIFEST-XXXXXX\\n`, 16 bytes), validate the 8-byte period, then sanity-check
that decrypting a .ldb file's last 8 bytes yields the SSTable footer magic
(0x57fb808b247547db). Sample keys we've seen:
  - 98518832  (bnqoY7hdBAA= — "B save")
  - 59x4hsx8  (b41410b4-49c3-… — UUID-named save; canonical NetEase form is
               'hsx859x4' rotated by 4, but the in-code keystream `KS` is
               aligned to file offset 0 so phase doesn't appear as a variable)

The 4-byte prefix that NetEase prepends to every sealed file is part of the
encrypted stream; after XOR it's a fixed 4-byte sentinel that LevelDB never
sees (we drop it). Without stripping it, SSTable footer block-offsets are
off by 4 and MANIFEST log-record CRC fails — iq80 LevelDB inside Chunker
then errors with "Descriptor does not contain a meta-nextfile entry".

If db/ contains a subdirectory (e.g. `lost/`), it's a leftover from a prior
LevelDB::RepairDB run. Those files aren't referenced by MANIFEST, so we
leave them encrypted in place and warn — Chunker will ignore them.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SSTABLE_MAGIC = bytes.fromhex("57fb808b247547db")
DEFAULT_JAVA_FORMAT = "JAVA_1_21_0"

# 4-byte sentinel NetEase writes verbatim at the start of every sealed db/ file
# (.ldb / MANIFEST-* / CURRENT). After the per-save XOR is applied during a
# normal decrypt, the first 4 bytes decode to a non-LevelDB pattern and get
# stripped. For encrypt we put these 4 raw bytes back at the head.
NETEASE_DISK_SENTINEL = b"\x80\x1d\x30\x01"

# Default encrypt-direction parameters: 8-byte XOR key for the current iPad
# account, and the byte that goes at the head of the 3-byte ~local_player
# trailer. Both can be overridden on the CLI in case the account's key changes
# or NetEase rolls a new trailer byte.
DEFAULT_ENCRYPT_KEYSTREAM = b"98518832"
DEFAULT_LOCAL_PLAYER_TRAILER_XX = 0x67
CHUNKER_RELEASES_URL = "https://github.com/HiveGamesOSS/Chunker/releases/latest"
CHUNKER_HOME = Path.home() / ".local/share/mcce"

# NetEase appends 3 trailing bytes <XX> 0xc0 0x00 to ~local_player's NBT value,
# outside the standard structure. Chunker reads XX as a tag-id and `c0 00` as
# u16 name_len (=192) with 0 bytes left → "Unknown tag type <XX>" → it drops
# the entire LocalPlayer, wiping inventory/position/etc. Strip the 3 trailing
# bytes and append TAG_End. Verified on U & B saves 2026-05-18 (XX varies:
# 0x35 in U, 0x67 in B; suffix `c0 00` is constant).
LOCAL_PLAYER_KEY = b"~local_player"
NETEASE_PLAYER_TRAILER_SUFFIX = b"\xc0\x00"
TAG_END = b"\x00"

# NetEase stores the world's seed in level.dat:RandomSeed unchanged — same
# value the in-game `/seed` command shows. Chunker propagates RandomSeed into
# Java's WorldGenSettings.seed automatically, so convert's `--seed` flag is a
# pure override (rarely needed) rather than a required input. An earlier
# version of this tool assumed NetEase transformed the seed and required
# --seed; that was based on a user-reported value that came from a different
# save (a blank test world they had created). On the actual save being
# converted, iPad's `/seed` matched level.dat:RandomSeed exactly.


# ---------------------------------------------------------------- crypto

def xor_with_keystream(data: bytes, ks: bytes) -> bytes:
    """XOR data byte-by-byte against an 8-byte keystream indexed by file offset."""
    k = len(ks)
    return bytes(b ^ ks[i % k] for i, b in enumerate(data))


def looks_decrypted_ldb(data: bytes) -> bool:
    """True if data already ends in standard SSTable magic (not encrypted)."""
    return len(data) >= 8 and data[-8:] == SSTABLE_MAGIC


def looks_decrypted_current(data: bytes) -> bool:
    """True if data looks like a standard CURRENT file (MANIFEST-xxxxxx\\n)."""
    return data.startswith(b"MANIFEST-") and data.endswith(b"\n") and len(data) < 64


def _latest_manifest(db_dir: Path) -> Path:
    candidates = [
        p for p in db_dir.iterdir()
        if p.is_file() and p.name.startswith("MANIFEST-")
        and p.name[len("MANIFEST-"):].isdigit()
    ]
    if not candidates:
        raise SystemExit(f"error: no MANIFEST-* file in {db_dir}; can't derive XOR key")
    return max(candidates, key=lambda p: int(p.name[len("MANIFEST-"):]))


def derive_keystream(db_dir: Path) -> tuple[bytes, str]:
    """
    Recover the 8-byte XOR keystream for this save via known-plaintext attack
    on CURRENT. Returns (ks, manifest_name) where `ks` satisfies
    `decrypted[i] = raw[i] ^ ks[i % 8]` for every sealed file in db/, and
    `manifest_name` is the MANIFEST-XXXXXX referenced by CURRENT.

    CURRENT is always exactly 20 bytes on disk: a 4-byte NetEase sentinel +
    16-byte encrypted "MANIFEST-XXXXXX\\n". The MANIFEST file is named (in
    plaintext) on disk in db/. XOR-ing the two gives a 16-byte stretch of
    keystream, which must repeat with period 8 — if it doesn't, this save
    uses a scheme we haven't seen and the caller should bail rather than
    guess.
    """
    current_path = db_dir / "CURRENT"
    if not current_path.is_file():
        raise SystemExit(f"error: {current_path} missing")
    current = current_path.read_bytes()
    if len(current) != 20:
        raise SystemExit(
            f"error: {current_path} is {len(current)} bytes; expected 20 "
            f"(4-byte NetEase prefix + 16-byte MANIFEST-XXXXXX\\n)"
        )
    manifest = _latest_manifest(db_dir)
    plain = manifest.name.encode("ascii") + b"\n"
    if len(plain) != 16:
        raise SystemExit(
            f"error: latest MANIFEST name {manifest.name!r} doesn't encode to "
            f"16 bytes — can't pair against CURRENT's 16-byte ciphertext."
        )
    window = bytes(c ^ p for c, p in zip(current[4:20], plain))
    if window[:8] != window[8:]:
        raise SystemExit(
            "error: CURRENT-derived keystream doesn't have an 8-byte period:\n"
            f"  window[0:8]  = {window[:8].hex()}\n"
            f"  window[8:16] = {window[8:].hex()}\n"
            f"This save's encryption may differ from the verified NetEase\n"
            f"scheme. Refusing to guess — investigate before proceeding."
        )
    # window[j] is the keystream byte applied at file offset (4+j), i.e.
    # ks[(4+j) % 8]. Rotate so ks[0] is the byte applied at offset 0.
    ks = bytes(window[(i + 4) % 8] for i in range(8))
    return ks, manifest.name


def verify_keystream_on_ldb(db_dir: Path, ks: bytes) -> Path | None:
    """
    Cross-check the keystream by decrypting one .ldb file's last 8 bytes and
    confirming they equal the SSTable footer magic. Returns the .ldb path
    used, or None if no .ldb files exist (e.g., brand-new save with only a
    .log file).
    """
    ldbs = sorted(
        p for p in db_dir.iterdir()
        if p.is_file() and p.name.endswith(".ldb") and not looks_decrypted_ldb(p.read_bytes()[-8:])
    )
    if not ldbs:
        return None
    sample = ldbs[0]
    data = sample.read_bytes()
    n = len(data)
    tail = bytes(data[n - 8 + j] ^ ks[(n - 8 + j) % 8] for j in range(8))
    if tail != SSTABLE_MAGIC:
        raise SystemExit(
            f"error: derived keystream {ks!r} fails the SSTable-magic check on "
            f"{sample.name}.\n"
            f"  got tail:  {tail.hex()}\n"
            f"  expected:  {SSTABLE_MAGIC.hex()}\n"
            f"CURRENT and .ldb may use different phase alignments in this save, "
            f"or the encryption scheme has changed. Bailing — needs analysis."
        )
    return sample


# ---------------------------------------------------------------- encrypt-direction

def encrypt_sealed(plain: bytes, ks: bytes) -> bytes:
    """
    Inverse of `xor_with_keystream(raw, ks)[4:]`.

    Layout NetEase writes for a sealed file: 4-byte literal disk sentinel
    `\\x80\\x1d\\x30\\x01` + (plain[i] XOR ks[(i + 4) % 8]) for each plaintext byte.

    Round-trip: decrypting the output with the same `ks` strips the sentinel
    and XOR-undoes the body, recovering the original plain.
    """
    body = bytes(plain[i] ^ ks[(i + 4) % 8] for i in range(len(plain)))
    return NETEASE_DISK_SENTINEL + body


def reseal_db_in_place(netease_root: Path, ks: bytes, verbose: bool) -> None:
    """
    Walk netease_root/db/ and rewrite each top-level file in NetEase encrypted
    form. Subdirectories are left untouched (they get copied verbatim by the
    earlier copytree step). Standard Bedrock has no `.log`-vs-`.ldb` separation
    other than the LevelDB-internal one: `.log` stays plaintext, sealed files
    get encrypted.
    """
    db_dir = netease_root / "db"
    counts: dict[str, int] = {}
    for entry in sorted(db_dir.iterdir()):
        if entry.is_dir():
            if verbose:
                n = sum(1 for _ in entry.rglob("*") if _.is_file())
                print(
                    f"  [skip-subdir              ] {entry.name}/  "
                    f"({n} files; left as-is)"
                )
            continue
        if not entry.is_file():
            continue
        name = entry.name
        plain = entry.read_bytes()
        if name == "CURRENT" or name.startswith("MANIFEST-") or name.endswith(".ldb"):
            out = encrypt_sealed(plain, ks)
            entry.write_bytes(out)
            action = "seal"
            counts[action] = counts.get(action, 0) + 1
            if verbose:
                print(f"  [{action:24s}] {name}  ({len(plain)} → {len(out)} bytes)")
        else:
            action = "copy"
            counts[action] = counts.get(action, 0) + 1
            if verbose:
                note = "  (.log stays plaintext)" if name.endswith(".log") else ""
                print(f"  [{action:24s}] {name}{note}")
    if verbose:
        print("summary:")
        for action, n in sorted(counts.items()):
            print(f"  {action:24s} {n}")


def add_netease_player_trailer(netease_root: Path, xx: int) -> None:
    """
    Append the 3-byte NetEase trailer `<XX> 0xc0 0x00` to `~local_player`'s
    LevelDB value so the iPad client recognizes the entry.

    Behavior depends on the current value's last bytes:
      - last 2 bytes == `\\xc0\\x00` → trailer already present, leave alone.
        (Useful when re-encrypting a save that was decrypted without the
        ~local_player patch step running, or when re-running encrypt.)
      - last byte == `0x00` (TAG_End) and second-to-last != `0xc0` → strip the
        TAG_End and append `<XX> 0xc0 0x00`. This is the standard
        Bedrock-NBT shape.
      - anything else → refuse to mutate; print a warning, leave value alone.

    Must run on the decrypted db/ BEFORE the sealed-file XOR pass, since
    amulet-leveldb operates on standard LevelDB and would refuse to open the
    encrypted form.
    """
    try:
        import leveldb  # amulet-leveldb
    except ImportError:
        raise SystemExit(
            "error: `import leveldb` (amulet-leveldb) failed. The encrypt step\n"
            "needs it to modify ~local_player. Install once:\n"
            "  brew install leveldb snappy\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/pip install -r requirements.txt\n"
            "Then re-run with the venv's python."
        )
    db_dir = netease_root / "db"
    db = leveldb.LevelDB(str(db_dir), create_if_missing=False)
    val = db.get(LOCAL_PLAYER_KEY)
    if val is None:
        print("  [patch ~local_player] key not in db; skipped")
        return
    if val[-2:] == NETEASE_PLAYER_TRAILER_SUFFIX:
        existing = val[-3]
        print(
            f"  [patch ~local_player] already has NetEase trailer "
            f"(existing XX=0x{existing:02x}); leaving alone"
        )
        return
    if val[-1:] != TAG_END:
        print(
            f"  [patch ~local_player] last byte 0x{val[-1]:02x} isn't TAG_End "
            f"and last 2 aren't `c0 00`; refusing to patch blindly. "
            f"Leaving value as-is."
        )
        return
    patched = val[:-1] + bytes([xx]) + NETEASE_PLAYER_TRAILER_SUFFIX
    db.put(LOCAL_PLAYER_KEY, patched)
    print(
        f"  [patch ~local_player] appended NetEase trailer (XX=0x{xx:02x}={xx}); "
        f"{len(val)} → {len(patched)} bytes"
    )


# ---------------------------------------------------------------- per-file rules

@dataclass
class FilePlan:
    rel_path: str               # path under db/
    action: str                 # "xor+trim4" | "copy" | "skip-already-decrypted"
    note: str = ""


def classify_db_file(name: str, raw: bytes) -> FilePlan:
    """Decide what to do with one file directly under db/."""
    if name == "CURRENT":
        if looks_decrypted_current(raw):
            return FilePlan(name, "skip-already-decrypted", "CURRENT already plain")
        return FilePlan(name, "xor+trim4", "decrypt + strip NetEase prefix")

    if name.startswith("MANIFEST-"):
        return FilePlan(name, "xor+trim4")

    if name.endswith(".ldb"):
        if looks_decrypted_ldb(raw):
            return FilePlan(name, "skip-already-decrypted", "SSTable magic at tail")
        return FilePlan(name, "xor+trim4")

    if name.endswith(".log"):
        return FilePlan(name, "copy", ".log is plaintext in NetEase saves")

    # LOCK, *.bak, anything else: copy verbatim
    return FilePlan(name, "copy", "unknown file in db/, copied verbatim")


def apply_plan(plan: FilePlan, raw: bytes, ks: bytes) -> bytes:
    if plan.action == "xor+trim4":
        return xor_with_keystream(raw, ks)[4:]
    if plan.action in ("copy", "skip-already-decrypted"):
        return raw
    raise ValueError(f"unknown action {plan.action!r}")


# ---------------------------------------------------------------- bedrock NBT (minimal)

class _NBTReader:
    """Little-endian Bedrock NBT reader — only what we need to find RandomSeed."""
    def __init__(self, data: bytes, off: int = 0):
        self.d = data
        self.o = off

    def _read(self, n: int) -> bytes:
        v = self.d[self.o:self.o + n]
        self.o += n
        return v

    def u1(self) -> int: return self._read(1)[0]
    def u2(self) -> int: return struct.unpack("<H", self._read(2))[0]
    def i2(self) -> int: return struct.unpack("<h", self._read(2))[0]
    def i4(self) -> int: return struct.unpack("<i", self._read(4))[0]
    def i8(self) -> int: return struct.unpack("<q", self._read(8))[0]
    def f4(self) -> float: return struct.unpack("<f", self._read(4))[0]
    def f8(self) -> float: return struct.unpack("<d", self._read(8))[0]

    def s(self) -> str:
        n = self.u2()
        return self._read(n).decode("utf-8", errors="replace")


def _skip_payload(r: _NBTReader, tag_id: int) -> None:
    """Advance r past one value of the given NBT tag type, ignoring its contents."""
    if tag_id == 1: r._read(1)
    elif tag_id == 2: r._read(2)
    elif tag_id == 3: r._read(4)
    elif tag_id == 4: r._read(8)
    elif tag_id == 5: r._read(4)
    elif tag_id == 6: r._read(8)
    elif tag_id == 7: r._read(r.i4())
    elif tag_id == 8: r._read(r.u2())
    elif tag_id == 9:
        it = r.u1()
        n = r.i4()
        for _ in range(n):
            _skip_payload(r, it)
    elif tag_id == 10:
        while True:
            t = r.u1()
            if t == 0:
                return
            _ = r.s()
            _skip_payload(r, t)
    elif tag_id == 11: r._read(4 * r.i4())
    elif tag_id == 12: r._read(8 * r.i4())
    else:
        raise ValueError(f"unknown NBT tag id {tag_id}")


def read_bedrock_random_seed(level_dat: Path) -> int | None:
    """
    Return the value of root.RandomSeed in a Bedrock level.dat, or None if
    parsing fails or the field is missing. Bedrock level.dat has an 8-byte
    file header (4-byte version + 4-byte size), then an uncompressed
    little-endian NBT compound.

    Caveat: this is NOT the seed NetEase iPad's /seed displays — see
    NETEASE_SEED_WARNING.
    """
    try:
        data = level_dat.read_bytes()
        r = _NBTReader(data, off=8)
        root_id = r.u1()
        if root_id != 10:
            return None
        _ = r.s()  # root name
        while True:
            t = r.u1()
            if t == 0:
                return None
            name = r.s()
            if name == "RandomSeed" and t == 4:
                return r.i8()
            _skip_payload(r, t)
    except Exception:
        return None


# ---------------------------------------------------------------- I/O

def assert_source_readonly_safe(src: Path) -> None:
    """Refuse to operate if src doesn't look like a Bedrock save."""
    if not src.is_dir():
        raise SystemExit(f"error: {src} is not a directory")
    if not (src / "level.dat").is_file():
        raise SystemExit(f"error: {src}/level.dat missing — not a Bedrock save?")
    if not (src / "db").is_dir():
        raise SystemExit(f"error: {src}/db/ missing — not a Bedrock save?")


def decrypted_dir_for(src: Path) -> Path:
    return src.parent / (src.name + "_decrypted")


def java_dir_for(src: Path) -> Path:
    return src.parent / (src.name + "_java")


def copy_save_tree(src: Path, dst: Path) -> None:
    """
    Copy the entire save tree to dst. The db/ contents will be overwritten
    file-by-file in the decrypt pass; copying first preserves any unknown
    files (LOCK, .bak, etc.) and the whole root.
    """
    if dst.exists():
        raise SystemExit(
            f"error: destination {dst} already exists. Remove it or pick a "
            f"different output path. (mcce never overwrites an existing dir.)"
        )
    shutil.copytree(src, dst, symlinks=False, dirs_exist_ok=False)


def decrypt_db_in_place(
    decrypted_root: Path, ks: bytes, verbose: bool
) -> list[FilePlan]:
    """
    Walk decrypted_root/db/ and rewrite each top-level file per the rules.
    Subdirectories (notably `lost/` from a prior LevelDB::RepairDB run) are
    left untouched — they aren't referenced by MANIFEST and LevelDB will
    ignore them, but we warn so the user knows partial data may be orphaned.

    Returns plans for reporting.
    """
    db_dir = decrypted_root / "db"
    plans: list[FilePlan] = []
    for entry in sorted(db_dir.iterdir()):
        if entry.is_dir():
            if verbose:
                n = sum(1 for _ in entry.rglob("*") if _.is_file())
                print(
                    f"  [skip-subdir              ] {entry.name}/  "
                    f"({n} files; leftover from LevelDB::RepairDB — "
                    f"not referenced by MANIFEST, left encrypted)"
                )
            continue
        if not entry.is_file():
            continue
        raw = entry.read_bytes()
        plan = classify_db_file(entry.name, raw)
        out = apply_plan(plan, raw, ks)
        if out is not raw:
            entry.write_bytes(out)
        plans.append(plan)
        if verbose:
            tag = plan.action
            extra = f"  ({plan.note})" if plan.note else ""
            print(f"  [{tag:24s}] {entry.name}{extra}")
    return plans


# ---------------------------------------------------------------- localplayer patch

def patch_local_player(decrypted_root: Path) -> None:
    """
    Strip NetEase's 3-byte trailer (`<XX> 0xc0 0x00`) from `~local_player` in
    the decrypted LevelDB and append TAG_End so the root compound closes
    cleanly. Without this, Chunker drops the entire LocalPlayer and the player
    spawns with default state (empty inventory, world spawn).

    Idempotent: only acts when the trailer signature is present. Lazy import
    of amulet-leveldb so `inspect` / `decrypt` work without it.
    """
    try:
        import leveldb  # amulet-leveldb
    except ImportError:
        raise SystemExit(
            "error: `import leveldb` (amulet-leveldb) failed. Install once:\n"
            "  brew install leveldb snappy\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/pip install -r requirements.txt\n"
            "Then re-run with the venv's python:\n"
            "  .venv/bin/python mcce.py convert <save> --seed <N>"
        )
    db_dir = decrypted_root / "db"
    db = leveldb.LevelDB(str(db_dir), create_if_missing=False)
    val = db.get(LOCAL_PLAYER_KEY)
    if val is None:
        print("  [patch ~local_player] key not in db; skipped")
        return
    if val[-2:] != NETEASE_PLAYER_TRAILER_SUFFIX:
        if val.endswith(TAG_END):
            print("  [patch ~local_player] already ends in TAG_End; skipped (no-op)")
        else:
            print(
                f"  [patch ~local_player] unexpected trailer {val[-3:].hex()} "
                f"— signature `c0 00` not present; skipped"
            )
        return
    xx = val[-3]
    patched = val[:-3] + TAG_END
    db.put(LOCAL_PLAYER_KEY, patched)
    print(
        f"  [patch ~local_player] stripped 3-byte NetEase trailer "
        f"(XX=0x{xx:02x}={xx}); {len(val)} → {len(patched)} bytes"
    )


# ---------------------------------------------------------------- chunker

def find_chunker_jar() -> Path | None:
    """Locate chunker-cli JAR. Order: $MCCE_CHUNKER_JAR, ~/.local/share/mcce/, $PATH."""
    env = os.environ.get("MCCE_CHUNKER_JAR")
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
    if CHUNKER_HOME.is_dir():
        candidates = sorted(CHUNKER_HOME.glob("chunker-cli*.jar"))
        if candidates:
            return candidates[-1]
    # Some users may install via brew or symlink into $PATH
    for name in ("chunker-cli.jar", "chunker.jar"):
        which = shutil.which(name)
        if which:
            return Path(which)
    return None


def chunker_install_instructions() -> str:
    return (
        f"Chunker CLI not found. Install it once with:\n\n"
        f"    mkdir -p {CHUNKER_HOME}\n"
        f"    cd {CHUNKER_HOME}\n"
        f"    # Look up the latest version at {CHUNKER_RELEASES_URL}\n"
        f"    # then download the chunker-cli-X.Y.Z.jar asset, e.g.:\n"
        f"    curl -LO https://github.com/HiveGamesOSS/Chunker/releases/download/1.17.0/chunker-cli-1.17.0.jar\n\n"
        f"Or set $MCCE_CHUNKER_JAR to point at the JAR somewhere else.\n"
        f"Java 17+ must be on $PATH (java -version)."
    )


def run_chunker(
    jar: Path,
    bedrock_dir: Path,
    java_dir: Path,
    fmt: str,
    seed: int | None,
) -> None:
    if java_dir.exists():
        raise SystemExit(f"error: {java_dir} already exists; refusing to overwrite")
    cmd = [
        "java", "-jar", str(jar),
        "-i", str(bedrock_dir),
        "-o", str(java_dir),
        "-f", fmt,
    ]
    if seed is not None:
        # Chunker's ChunkerLevelSettings.RandomSeed is a String field; the JSON
        # passed via -s is merged on top of the settings read from the source
        # before the Java writer puts it into WorldGenSettings.seed.
        cmd += ["-s", json.dumps({"RandomSeed": str(seed)})]
    print(f"running: {shlex.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        raise SystemExit("error: `java` not on $PATH. Install Java 17+.")
    if result.returncode != 0:
        raise SystemExit(f"error: chunker-cli exited {result.returncode}")
    if not (java_dir / "level.dat").is_file():
        raise SystemExit(
            f"error: chunker finished but {java_dir}/level.dat is missing — "
            f"check Chunker's stdout above for errors."
        )


# ---------------------------------------------------------------- subcommands

def _format_keystream(ks: bytes) -> str:
    try:
        ascii_form = ks.decode("ascii")
        if ascii_form.isprintable():
            return f"{ks.hex()}  ({ascii_form!r})"
    except UnicodeDecodeError:
        pass
    return ks.hex()


def cmd_inspect(args: argparse.Namespace) -> int:
    src = Path(args.save).resolve()
    assert_source_readonly_safe(src)
    print(f"save: {src}")
    stored_seed = read_bedrock_random_seed(src / "level.dat")
    print(f"level.dat:RandomSeed = {stored_seed}")
    print(f"  (Chunker preserves this into Java's WorldGenSettings.seed by default.)")
    print()
    db = src / "db"
    ks, manifest_name = derive_keystream(db)
    sample = verify_keystream_on_ldb(db, ks)
    print(f"derived keystream:   {_format_keystream(ks)}")
    print(f"  from CURRENT vs {manifest_name}")
    if sample is not None:
        print(f"  verified against {sample.name} tail = SSTable magic")
    else:
        print(f"  (no .ldb files to cross-check yet — derivation period passed)")
    print()
    print(f"db/ entries:")
    for entry in sorted(db.iterdir()):
        if entry.is_dir():
            n = sum(1 for _ in entry.rglob("*") if _.is_file())
            print(f"  {entry.name + '/':24s} {n} files  plan=skip-subdir (RepairDB orphan)")
            continue
        if not entry.is_file():
            continue
        raw_head = entry.read_bytes()[:8]
        plan = classify_db_file(entry.name, raw_head + b"\x00" * (8 - len(raw_head)))
        print(f"  {entry.name:24s} head8={raw_head.hex()}  plan={plan.action}")
    return 0


def cmd_decrypt(args: argparse.Namespace) -> int:
    src = Path(args.save).resolve()
    assert_source_readonly_safe(src)
    dst = Path(args.output).resolve() if args.output else decrypted_dir_for(src)
    print(f"source (read-only):  {src}")
    print(f"decrypted output:    {dst}")
    ks, manifest_name = derive_keystream(src / "db")
    sample = verify_keystream_on_ldb(src / "db", ks)
    print(f"derived keystream:   {_format_keystream(ks)} (from CURRENT/{manifest_name})")
    if sample is not None:
        print(f"verified on:         {sample.name} tail = SSTable magic")
    copy_save_tree(src, dst)
    plans = decrypt_db_in_place(dst, ks, verbose=True)
    counts: dict[str, int] = {}
    for p in plans:
        counts[p.action] = counts.get(p.action, 0) + 1
    print("summary:")
    for action, n in sorted(counts.items()):
        print(f"  {action:24s} {n}")
    print(f"done. standard-Bedrock save written to {dst}")
    return 0


def _parse_keystream_arg(s: str) -> bytes:
    """Accept either 8 ASCII chars (e.g. '98518832') or 16 hex chars."""
    raw = s.encode("ascii") if not all(c in "0123456789abcdefABCDEF" for c in s) else None
    if raw is not None and len(raw) == 8:
        return raw
    if all(c in "0123456789abcdefABCDEF" for c in s) and len(s) == 16:
        return bytes.fromhex(s)
    if all(c in "0123456789abcdefABCDEF" for c in s) and len(s) == 8:
        # 8 hex chars is ambiguous with 8 ASCII chars — treat as ASCII (matches
        # the keys we've seen so far, e.g. '98518832').
        return s.encode("ascii")
    raise argparse.ArgumentTypeError(
        f"--keystream must be 8 ASCII chars or 16 hex chars; got {s!r}"
    )


def _parse_trailer_byte_arg(s: str) -> int:
    base = 16 if s.lower().startswith("0x") else (16 if all(c in "0123456789abcdefABCDEF" for c in s) and len(s) <= 2 else 10)
    val = int(s, base)
    if not 0 <= val <= 0xFF:
        raise argparse.ArgumentTypeError(f"--trailer-byte must be 0..255; got {val}")
    return val


def netease_dir_for(src: Path) -> Path:
    return src.parent / (src.name + "_netease")


def cmd_encrypt(args: argparse.Namespace) -> int:
    src = Path(args.save).resolve()
    if not src.is_dir():
        raise SystemExit(f"error: {src} is not a directory")
    if not (src / "level.dat").is_file():
        raise SystemExit(f"error: {src}/level.dat missing — not a Bedrock save?")
    if not (src / "db").is_dir():
        raise SystemExit(f"error: {src}/db missing — not a Bedrock save?")
    dst = Path(args.output).resolve() if args.output else netease_dir_for(src)
    ks: bytes = args.keystream
    xx: int = args.trailer_byte
    print(f"source (standard Bedrock):  {src}")
    print(f"netease output:             {dst}")
    print(f"keystream:                  {_format_keystream(ks)}")
    print(f"~local_player trailer XX:   0x{xx:02x} = {xx}")
    print()
    print("=== step 1: copy save tree ===")
    copy_save_tree(src, dst)
    print()
    print("=== step 2: append NetEase trailer to ~local_player ===")
    add_netease_player_trailer(dst, xx)
    print()
    print("=== step 3: encrypt sealed files in db/ ===")
    reseal_db_in_place(dst, ks, verbose=True)
    print()
    print(f"done. NetEase-encrypted save at: {dst}")
    print(
        "To test on iPad: create a fresh world in the NetEase client to claim a "
        "slot, then replace its directory contents with this output."
    )
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    src = Path(args.save).resolve()
    assert_source_readonly_safe(src)
    decrypted = Path(args.decrypted).resolve() if args.decrypted else decrypted_dir_for(src)
    java_out = Path(args.output).resolve() if args.output else java_dir_for(src)
    jar = find_chunker_jar()
    if jar is None:
        print(chunker_install_instructions(), file=sys.stderr)
        return 2
    stored = read_bedrock_random_seed(src / "level.dat")
    ks, manifest_name = derive_keystream(src / "db")
    sample = verify_keystream_on_ldb(src / "db", ks)
    print(f"chunker jar:         {jar}")
    print(f"source (read-only):  {src}")
    print(f"bedrock intermediate:{decrypted}")
    print(f"java output:         {java_out}")
    print(f"derived keystream:   {_format_keystream(ks)} (from CURRENT/{manifest_name})")
    if sample is not None:
        print(f"verified on:         {sample.name} tail = SSTable magic")
    print(f"java format:         {args.format}")
    if args.seed is None:
        print(f"seed:                {stored} (from level.dat:RandomSeed, Chunker default)")
    else:
        print(f"seed (--seed):       {args.seed}  [override; stored is {stored}]")
    print()
    print("=== step 1: decrypt NetEase → standard Bedrock ===")
    copy_save_tree(src, decrypted)
    decrypt_db_in_place(decrypted, ks, verbose=True)
    print()
    print("=== step 2: patch NetEase ~local_player trailer ===")
    patch_local_player(decrypted)
    print()
    print("=== step 3: chunker Bedrock → Java ===")
    run_chunker(jar, decrypted, java_out, args.format, seed=args.seed)
    print()
    print(f"done. Java save at: {java_out}")
    print(f"to play: copy to ~/Library/Application Support/minecraft/saves/")
    return 0


# ---------------------------------------------------------------- argparse

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcce",
        description="Convert NetEase Bedrock saves to Java Edition saves.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("inspect", help="report per-file plan without writing anything")
    pi.add_argument("save", help="path to NetEase save directory")
    pi.set_defaults(func=cmd_inspect)

    pd = sub.add_parser("decrypt", help="decrypt to <name>_decrypted (standard Bedrock); no Chunker")
    pd.add_argument("save", help="path to NetEase save directory")
    pd.add_argument("-o", "--output", help="override output directory")
    pd.set_defaults(func=cmd_decrypt)

    pc = sub.add_parser("convert", help="full pipeline: decrypt + run Chunker to Java")
    pc.add_argument("save", help="path to NetEase save directory")
    pc.add_argument("-o", "--output", help="override Java output directory")
    pc.add_argument("--decrypted", help="override intermediate Bedrock directory")
    pc.add_argument(
        "-f", "--format", default=DEFAULT_JAVA_FORMAT,
        help=f"Chunker output format (default: {DEFAULT_JAVA_FORMAT})",
    )
    pc.add_argument(
        "--seed", type=int, default=None,
        help="Override the seed Chunker writes into Java's WorldGenSettings.seed. "
             "Normally not needed: Chunker preserves level.dat:RandomSeed by "
             "default and that's the seed the in-game NetEase /seed shows. Pass "
             "--seed only if you want to regenerate unexplored chunks under a "
             "different seed.",
    )
    pc.set_defaults(func=cmd_convert)

    pe = sub.add_parser(
        "encrypt",
        help="re-encrypt a standard Bedrock save into NetEase iPad format",
    )
    pe.add_argument("save", help="path to a standard Bedrock save directory")
    pe.add_argument("-o", "--output", help="override output directory (default: <save>_netease)")
    pe.add_argument(
        "--keystream",
        type=_parse_keystream_arg,
        default=DEFAULT_ENCRYPT_KEYSTREAM,
        help=f"8-byte XOR keystream — ASCII (e.g. 98518832) or 16-hex "
             f"(default: {DEFAULT_ENCRYPT_KEYSTREAM.decode()}, the current iPad account's key). "
             f"Change this if your account uses a different key (recover it by "
             f"running `inspect` on a save you exported from that account).",
    )
    pe.add_argument(
        "--trailer-byte",
        type=_parse_trailer_byte_arg,
        default=DEFAULT_LOCAL_PLAYER_TRAILER_XX,
        help=f"byte XX in the 3-byte ~local_player trailer 'XX c0 00' "
             f"(default: 0x{DEFAULT_LOCAL_PLAYER_TRAILER_XX:02x}, the value the current iPad client writes; "
             f"observed XX varies per save, but 0x67 is the most common).",
    )
    pe.set_defaults(func=cmd_encrypt)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
