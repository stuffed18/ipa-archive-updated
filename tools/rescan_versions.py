#!/usr/bin/env python3
from main import CacheDB, diskPath
import os

def rescan_unknown_versions():
    db = CacheDB()
    # Find entries that are marked done but have no OS version
    cursor = db._db.execute("SELECT pk FROM idx WHERE done=1 AND min_os IS NULL")
    rows = cursor.fetchall()
    total = len(rows)
    print(f"Found {total} entries with unknown iOS version.")
    
    updated = 0
    for i, (pk,) in enumerate(rows):
        if i % 100 == 0:
            print(f"\rProcessing {i}/{total}...", end="")
        
        # This will reload the plist and use the new fallback logic
        db.setDone(pk)
        
        # Check if it was updated
        res = db._db.execute("SELECT min_os FROM idx WHERE pk=?", [pk]).fetchone()
        if res and res[0] is not None:
            updated += 1
            
    print(f"\nDone! Updated {updated} entries with new version info.")

if __name__ == "__main__":
    rescan_unknown_versions()
