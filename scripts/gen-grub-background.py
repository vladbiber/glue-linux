#!/usr/bin/env python3
"""Generate a deterministic solid-color PNG background for GRUB from palette.json."""

import json
import struct
import zlib
from pathlib import Path

def hex_to_rgb(hex_color):
    """Convert #RRGGBB hex to (R, G, B) tuple."""
    hex_color = hex_color.lstrip('#').upper()
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def crc32_chunk(chunk_type, chunk_data):
    """Calculate CRC32 for PNG chunk."""
    crc = zlib.crc32(chunk_type + chunk_data) & 0xffffffff
    return struct.pack('>I', crc)

def create_solid_png(width, height, r, g, b, output_path):
    """Generate a solid-color PNG with deterministic output.

    Args:
        width, height: image dimensions
        r, g, b: color components (0-255)
        output_path: where to write the PNG file
    """
    # PNG signature
    png_data = b'\x89PNG\r\n\x1a\n'

    # IHDR chunk: image header
    ihdr_data = struct.pack('>IIBBBBB',
        width, height,           # width, height
        8,                        # bit depth
        2,                        # color type (2 = RGB)
        0,                        # compression method (0 = deflate)
        0,                        # filter method (0 = adaptive)
        0                         # interlace method (0 = none)
    )
    ihdr_chunk = (struct.pack('>I', len(ihdr_data)) +
                  b'IHDR' +
                  ihdr_data +
                  crc32_chunk(b'IHDR', ihdr_data))
    png_data += ihdr_chunk

    # IDAT chunk: image data
    # Construct raw image data: each row is filter_type (1 byte) + RGB pixels
    raw_data = b''
    pixel_bytes = bytes([r, g, b])
    for _ in range(height):
        # Filter type 0 (None) + 1024 repetitions of the RGB pixel
        row = b'\x00' + pixel_bytes * width
        raw_data += row

    # Compress with maximum compression for determinism
    compressed_data = zlib.compress(raw_data, 9)
    idat_chunk = (struct.pack('>I', len(compressed_data)) +
                  b'IDAT' +
                  compressed_data +
                  crc32_chunk(b'IDAT', compressed_data))
    png_data += idat_chunk

    # IEND chunk: image end
    iend_chunk = struct.pack('>I', 0) + b'IEND' + crc32_chunk(b'IEND', b'')
    png_data += iend_chunk

    # Write to file
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(png_data)

def main():
    # Load palette
    palette_path = Path('packages/glue-branding/palette.json')
    if not palette_path.exists():
        raise FileNotFoundError(f"palette.json not found: {palette_path}")

    palette = json.loads(palette_path.read_text())
    bg_color = palette['bg']
    r, g, b = hex_to_rgb(bg_color)

    # Generate PNG at both locations
    width, height = 1024, 768

    output_paths = [
        'packages/glue-branding/grub-background.png',
        'iso-profile/glue/root-overlay/usr/share/grub/themes/artix/background.png'
    ]

    for path in output_paths:
        create_solid_png(width, height, r, g, b, path)

if __name__ == '__main__':
    main()
