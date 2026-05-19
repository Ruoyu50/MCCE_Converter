#!/usr/bin/env python3
"""Dump db/ directory metadata for a decrypted snapshot."""
import sys, hashlib
from pathlib import Path

# LevelDB 文件读取的逻辑顺序:CURRENT → MANIFEST → log → ldb
TYPE_ORDER = {"CURRENT": 0, "MANIFEST": 1, "log": 2, "ldb": 3}

def sha256_short(path):
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return h[:12]

def file_type(name):
    if name == "CURRENT":
        return "CURRENT"
    if name.startswith("MANIFEST-"):
        return "MANIFEST"
    if name.endswith(".log"):
        return "log"
    if name.endswith(".ldb"):
        return "ldb"
    return "other"

def sort_key(f):
    t = file_type(f.name)
    return (TYPE_ORDER.get(t, 99), f.name)

def main(snap_dir):
    db = Path(snap_dir) / "db"
    if not db.is_dir():
        sys.exit(f"no db/ in {snap_dir}")
    files = [f for f in db.iterdir() if f.is_file()]
    files.sort(key=sort_key)

    total_bytes = 0
    for f in files:
        size = f.stat().st_size
        total_bytes += size
        print(f"  {f.name:30s} {size:>10} bytes  sha256:{sha256_short(f)}")
        if f.name == "CURRENT":
            print(f"      → {f.read_text().strip()}")

    print(f"  {'TOTAL':30s} {total_bytes:>10} bytes  ({len(files)} files)")

if __name__ == "__main__":
    main(sys.argv[1])