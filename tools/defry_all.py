#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_DIR = SCRIPT_DIR.parent / 'data'
PNGDEFRY_BIN = PROJECT_ROOT / 'node-pngdefry' / 'lib' / 'pngdefry' / 'source' / 'pngdefry'

def is_cgbi(file_path):
    """Check if a file has the CGBI chunk in its first 32 bytes."""
    try:
        with open(file_path, 'rb') as f:
            header = f.read(32)
            return b'CGBI' in header
    except Exception:
        return False

def defry_file(file_path):
    """Run pngdefry on a single file."""
    # pngdefry usage: pngdefry -o(path) file.png
    # It creates a new file with the same name in the output directory
    # so we'll use a temp directory or just replace the original.
    
    file_path = Path(file_path)
    temp_dir = file_path.parent / 'temp_defry'
    temp_dir.mkdir(exist_ok=True)
    
    try:
        # pngdefry -o[path] [input_file]
        cmd = [str(PNGDEFRY_BIN), f'-o{temp_dir}', str(file_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # Check if the output file was created (it has the same name)
        output_file = temp_dir / file_path.name
        if output_file.exists():
            # Replace original with defried version
            output_file.replace(file_path)
            print(f"Fixed: {file_path}")
            return True
        else:
            print(f"Failed to fix: {file_path}")
            if result.stderr:
                print(f"Error: {result.stderr.strip()}")
            return False
    finally:
        # Cleanup temp dir
        if temp_dir.exists():
            for f in temp_dir.iterdir():
                f.unlink()
            temp_dir.rmdir()

def main():
    if not PNGDEFRY_BIN.exists():
        print(f"Error: pngdefry binary not found at {PNGDEFRY_BIN}")
        print("Please compile it first (I did this earlier, check the path).")
        return

    print("Scanning for CGBI (crushed) PNGs...")
    count = 0
    fixed = 0
    
    for root, dirs, files in os.walk(DATA_DIR):
        for file in files:
            if file.lower().endswith('.png'):
                full_path = Path(root) / file
                if is_cgbi(full_path):
                    count += 1
                    if defry_file(full_path):
                        fixed += 1
    
    print(f"\nScan complete.")
    print(f"Found {count} CGBI files.")
    print(f"Successfully fixed {fixed} files.")

if __name__ == "__main__":
    main()
