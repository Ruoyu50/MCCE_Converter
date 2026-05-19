#!/usr/bin/env python3
"""
patch_localplayer.py — strip the NetEase 3-byte trailer from ~local_player.

What it does
------------
NetEase appends 3 trailing bytes (`<XX> 0xc0 0x00`) to the standard NBT value
stored under the LevelDB key `~local_player`. Chunker's NBT parser reads the
first byte as a tag-id and the following `c0 00` as a u16 name-length (=192)
with no bytes left, throws "Unknown tag type <XX>", and abandons the entire
LocalPlayer — wiping inventory/position/etc. on the Java side.

The fix is to strip the 3-byte trailer and append a proper TAG_End (`0x00`)
so the root compound closes cleanly. Verified on the U and B saves
(2026-05-18): both have an identical structural trailer (XX varies — `0x35`
in U, `0x67` in B; `c0 00` is constant).

Usage
-----
    .venv/bin/python patch_localplayer.py <path-to-decrypted-save-or-db-dir>

Path may be either the save root (containing `db/`) or the `db/` dir directly.
Operates in place on the given directory — never touches the source NetEase
save. Re-running is safe (idempotent: only patches if the trailer signature
matches).

Dependency
----------
amulet-leveldb (pip-installed in the project's .venv). plyvel does not work
against Homebrew's leveldb on macOS due to an RTTI mismatch — use the .venv.

Limitations
-----------
This restores Bedrock LocalPlayer fields that Chunker knows how to translate:
Inventory, Pos, Rotation, Motion, Dimension, playerGameType, DataVersion.
Health / foodLevel / XpLevel / EnderItems / abilities are NOT translated by
Chunker from Bedrock's Attributes / EnderChestInventory — that would need a
separate post-Chunker level.dat patcher, not done here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

NETEASE_TRAILER_SUFFIX = b"\xc0\x00"
TAG_END = b"\x00"
LOCAL_PLAYER_KEY = b"~local_player"


def resolve_db_dir(arg: str) -> Path:
    """Accept either a save root (with db/) or a db/ dir directly."""
    p = Path(arg).expanduser().resolve()
    if not p.is_dir():
        raise SystemExit(f"error: {p} is not a directory")
    if (p / "db").is_dir() and (p / "level.dat").is_file():
        return p / "db"
    if p.name == "db" and (p.parent / "level.dat").is_file():
        return p
    # tolerate plain db/ dir without level.dat sibling
    if (p / "CURRENT").is_file():
        return p
    raise SystemExit(
        f"error: {p} doesn't look like a Bedrock save or its db/. "
        f"Expected either: <save>/ containing level.dat + db/, "
        f"or <save>/db/ directly."
    )


def patch_db(db_dir: Path) -> int:
    try:
        import leveldb  # amulet-leveldb
    except ImportError:
        raise SystemExit(
            "error: `import leveldb` failed. This script requires "
            "amulet-leveldb installed in a venv. From the project root:\n"
            "  brew install leveldb snappy\n"
            "  python3 -m venv .venv\n"
            "  .venv/bin/pip install amulet-leveldb\n"
            "Then run:  .venv/bin/python patch_localplayer.py <save>"
        )
    db = leveldb.LevelDB(str(db_dir), create_if_missing=False)
    val = db.get(LOCAL_PLAYER_KEY)
    if val is None:
        print(f"  {LOCAL_PLAYER_KEY.decode()}: NOT PRESENT in {db_dir}")
        print("  (singleplayer save without player data? nothing to patch)")
        return 0
    print(f"  {LOCAL_PLAYER_KEY.decode()}: {len(val)} bytes")
    print(f"  current trailer (last 3 bytes): {val[-3:].hex()}")
    if val[-2:] != NETEASE_TRAILER_SUFFIX:
        print("  trailer signature `c0 00` NOT present.")
        if val.endswith(TAG_END):
            print("  value ends in TAG_End (0x00) — likely already patched. No-op.")
        else:
            print(
                "  value has an unexpected ending — refusing to patch blindly. "
                "If this save has a different NetEase variant, share the last 16 "
                "bytes for analysis before changing it."
            )
            return 2
        return 0
    xx = val[-3]
    patched = val[:-3] + TAG_END
    db.put(LOCAL_PLAYER_KEY, patched)
    print(f"  stripped 3-byte NetEase trailer (XX=0x{xx:02x} = {xx})")
    print(f"  appended TAG_End; new size: {len(patched)} bytes")
    print(f"  new trailer (last 3 bytes): {patched[-3:].hex()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="patch_localplayer.py",
        description=__doc__.split("\n\n", 1)[0],
    )
    p.add_argument(
        "path",
        help="path to <save>_decrypted/ or <save>_decrypted/db/",
    )
    args = p.parse_args(argv)
    db_dir = resolve_db_dir(args.path)
    print(f"db dir: {db_dir}")
    return patch_db(db_dir)


if __name__ == "__main__":
    sys.exit(main())
