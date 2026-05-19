#!/usr/bin/env python3
"""Dump db/ directory metadata for a decrypted snapshot."""
import sys, hashlib
from pathlib import Path

def sha256_short(path):
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return h[:12]

def main(snap_dir):
    db = Path(snap_dir) / "db"
    if not db.is_dir():
        sys.exit(f"no db/ in {snap_dir}")
    for f in sorted(db.iterdir()):
        if f.is_file():
            print(f"  {f.name:30s} {f.stat().st_size:>10} bytes  sha256:{sha256_short(f)}")
            if f.name == "CURRENT":
                print(f"      → {f.read_text().strip()}")

if __name__ == "__main__":
    main(sys.argv[1])