import glob
import pyipng

# Converts weird Apple PNG CgBI format to normal PNGs

def fix_iPNG(file_path):        
    try:
        with open(file_path,'rb') as f:
            bytes = f.read()
            fix_bytes = pyipng.convert(bytes)
            with open(file_path,'wb') as f:
                f.write(fix_bytes)
                print(f'{file_path} fixed')
    except ValueError:
        print(f'{file_path} doesn\'t need fixing')


for file in glob.glob('../data/*/*.png'):
    fix_iPNG(file)