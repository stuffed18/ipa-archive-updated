import sys
import struct
import zlib

def fix_png(path):
    with open(path, "rb") as f:
        data = f.read()

    if b'CgBI' not in data:
        return False

    # Remove CgBI chunk (first 16 bytes after PNG header)
    data = data[:8] + data[24:]

    # Fix zlib stream (recompress)
    new_data = bytearray()
    i = 8

    while i < len(data):
        length = struct.unpack(">I", data[i:i+4])[0]
        chunk_type = data[i+4:i+8]
        chunk_data = data[i+8:i+8+length]

        if chunk_type == b'IDAT':
            try:
                decompressed = zlib.decompress(chunk_data)
                recompressed = zlib.compress(decompressed)
                chunk_data = recompressed
                length = len(chunk_data)
            except:
                pass

        new_data += struct.pack(">I", length)
        new_data += chunk_type
        new_data += chunk_data
        new_data += data[i+8+length:i+12+length]

        i += length + 12

    with open(path, "wb") as f:
        f.write(data[:8] + new_data)

    return True


if __name__ == "__main__":
    for file in sys.argv[1:]:
        if fix_png(file):
            print("Fixed:", file)
