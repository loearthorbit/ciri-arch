#!/usr/bin/env -S python3 -I
"""Offline Ciri P1 pixel comparison. Never load or execute the camera runtime.

Unpack contiguous little-endian 10/12-bit samples. The current dump's known bars,
neutral chroma and agreement between both P1 outputs support this interpretation;
the libcamera format table alone specifies group sizes, not bit order.
"""
import argparse
from array import array
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def unpack(data, bits, width, height, stride):
    if bits not in (10, 12) or width % (4 if bits == 10 else 2):
        raise ValueError('Only complete 10-bit/4-sample or 12-bit/2-sample groups supported')
    if len(data) != stride * height or stride * 8 != width * bits:
        raise ValueError('Dump length/stride does not match the unpadded packed layout')
    count, step = (4, 5) if bits == 10 else (2, 3)
    mask = (1 << bits) - 1
    result = array('H')
    for offset in range(0, len(data), step):
        word = int.from_bytes(data[offset:offset + step], 'little')
        result.extend((word >> (i * bits)) & mask for i in range(count))
    return result


def stats(values, width, height):
    if len(values) != width * height or height % 2:
        raise ValueError('Invalid plane dimensions')
    halves = (values[:len(values) // 2], values[len(values) // 2:])
    result = {name: {'mean': sum(part) / len(part), 'min': min(part), 'max': max(part)}
              for name, part in zip(('upper', 'lower'), halves)}
    means = [sum(values[y * width:(y + 1) * width]) / width for y in range(height)]
    result['largest_row_mean_edges'] = sorted(
        ((abs(means[y] - means[y - 1]), y) for y in range(1, height)), reverse=True)[:3]
    return result


def one(paths):
    paths = list(paths)
    if len(paths) != 1:
        raise ValueError(f'Expected one input, found {len(paths)}')
    return paths[0]


def preview(path, pixel_format, size, output):
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-n',
                    '-f', 'rawvideo', '-pixel_format', pixel_format,
                    '-video_size', size, '-i', str(path), '-frames:v', '1',
                    '-vf', 'scale=640:480', str(output)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('retrieved_directory', type=Path)
    parser.add_argument('output_directory', type=Path, help='New directory; existing paths refused')
    args = parser.parse_args()
    if not shutil.which('ffmpeg'):
        parser.error('Host ffmpeg is required for offline previews')
    run = one((args.retrieved_directory / 'captures').glob('run.*'))
    dump = one((run / 'p1-dump').glob('UKey*'))
    out = args.output_directory
    out.mkdir(mode=0o700)  # No replacement or cleanup of previous analysis.
    report = {'packing': 'contiguous little-endian 10/12-bit samples; supported by known-pattern pixels',
              'preview': 'RAW shifted to 16-bit; YUV shifted to 8-bit, then standard FFmpeg decoding',
              'limitations': 'Preview is not colour calibration. Internal dump IDs differ from client sequences because of padding requests.',
              'sources': {}, 'planes': {}}

    def read(path):
        data = path.read_bytes()
        report['sources'][str(path)] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        return data

    raw_path = one(dump.glob('*3264x2448*.packed_word'))
    raw = unpack(read(raw_path), 10, 3264, 2448, 4080)
    report['planes']['p1-raw'] = stats(raw, 3264, 2448)
    scaled = array('H', (sample << 6 for sample in raw))
    if sys.byteorder != 'little':
        scaled.byteswap()
    raw_preview = out / 'p1-raw.rggb16le'
    raw_preview.write_bytes(scaled.tobytes())
    preview(raw_preview, 'bayer_rggb16le', '3264x2448', out / 'p1-raw.png')

    for label, bits, width, height, stride in (
            ('p1-yuvo1', 10, 1280, 960, 1600), ('p1-yuvo2', 12, 640, 480, 960)):
        y = unpack(read(one(dump.glob(f'*{width}x{height}*-yplane*.packed_word'))),
                   bits, width, height, stride)
        uv = unpack(read(one(dump.glob(f'*{width}x{height}*-cplane*.packed_word'))),
                    bits, width, height // 2, stride)
        report['planes'][label] = {'bits': bits, 'Y': stats(y, width, height),
                                  'U': stats(uv[0::2], width // 2, height // 2),
                                  'V': stats(uv[1::2], width // 2, height // 2)}
        frame = out / (label + '.nv12')
        frame.write_bytes(bytes(sample >> (bits - 8) for sample in y) +
                          bytes(sample >> (bits - 8) for sample in uv))
        preview(frame, 'nv12', f'{width}x{height}', out / (label + '.png'))

    for number in (0, 1, 2, 3, 4, 5, 6, 63):
        path = run / f'colour-bars-cam0-stream0-{number:06d}.nv12'
        data = read(path)
        if len(data) != 460800:
            raise ValueError('Final NV12 length does not match 640x480')
        report['planes'][f'final-{number}'] = {
            'bits': 8, 'Y': stats(data[:307200], 640, 480),
            'U': stats(data[307200::2], 320, 240), 'V': stats(data[307201::2], 320, 240)}
        if number in (0, 63):
            preview(path, 'nv12', '640x480', out / f'final-{number}.png')
    (out / 'pixel-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Offline comparison:', out)
    for name in ('p1-raw', 'p1-yuvo1', 'p1-yuvo2', 'final-0', 'final-63'):
        print(name, json.dumps(report['planes'][name]))


if __name__ == '__main__':
    main()
