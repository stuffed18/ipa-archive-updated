from PIL import Image
import os
import logging
import subprocess
from pathlib import Path

# Set up logging
logging.basicConfig(filename='conversion_errors.log', level=logging.ERROR)

# Use dynamic path based on file location
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_DIR = SCRIPT_DIR / 'data'
PNGDEFRY_BIN = PROJECT_ROOT / 'node-pngdefry' / 'lib' / 'pngdefry' / 'source' / 'pngdefry'

def is_cgbi(file_path):
    """Check if file is an Apple-optimized PNG (CGBI)."""
    try:
        with open(file_path, 'rb') as f:
            return b'CGBI' in f.read(32)
    except:
        return False

def defry_png(file_path):
    """Convert CGBI PNG to standard PNG using pngdefry."""
    if not PNGDEFRY_BIN.exists():
        logging.error(f"pngdefry binary not found at {PNGDEFRY_BIN}")
        return False
    
    file_path = Path(file_path)
    temp_dir = file_path.parent / 'temp_defry'
    temp_dir.mkdir(exist_ok=True)
    
    try:
        cmd = [str(PNGDEFRY_BIN), f'-o{temp_dir}', str(file_path)]
        subprocess.run(cmd, capture_output=True, check=True)
        output_file = temp_dir / file_path.name
        if output_file.exists():
            output_file.replace(file_path)
            return True
    except Exception as e:
        logging.error(f"Error defrying {file_path}: {e}")
    finally:
        if temp_dir.exists():
            for f in temp_dir.iterdir(): f.unlink()
            temp_dir.rmdir()
    return False

# Traverse all directories and subdirectories
print(f"Starting conversion in {DATA_DIR}...")
for root, dirs, files in os.walk(DATA_DIR):
    for file_name in files:
        if file_name.lower().endswith('.png'):
            file_path = os.path.join(root, file_name)
            
            # Step 1: Fix Apple CGBI PNGs if needed
            if is_cgbi(file_path):
                print(f"Defrying {file_path}...")
                defry_png(file_path)

            try:
                # Step 2: Convert standard PNG to JPG
                with Image.open(file_path) as img:
                    jpg_file_name = os.path.splitext(file_name)[0] + '.jpg'
                    jpg_file_path = os.path.join(root, jpg_file_name)
                    img.convert('RGB').save(jpg_file_path, 'JPEG')
                    # Log successful conversion
                    logging.info(f'Converted {file_path} to {jpg_file_path}')
                    
                    # Step 3: Delete the original PNG file
                    os.remove(file_path)
                    
            except Exception as e:
                # Log the error
                logging.error(f'Error converting {file_path}: {e}')

print('Conversion completed. Errors logged in conversion_errors.log.')
