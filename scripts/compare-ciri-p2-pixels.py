#!/usr/bin/env -S python3 -I
"""Offline labelled Ciri P2 dumps: crop allocation padding, never run camera code.

Uses the contiguous little-endian packing supported by the known-pattern P1
pixels. Preview conversion is not a colour-calibration measurement.
"""
import argparse
from array import array
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil

spec = importlib.util.spec_from_file_location(
    'ciri_p1_pixels', Path(__file__).with_name('compare-ciri-p1-pixels.py'))
p1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p1)

PACKED_NAME = re.compile(
    r'(?P<prefix>\d+-\d+-\d+)-(?P<label>[A-Z0-9_]+-[A-Z0-9_]+)__'
    r'(?P<width>\d+)x(?P<height>\d+)_(?P<bits>10|12)-(?P<plane>yplane|cplane)'
    r'-PW(?P<pw>\d+)-PH(?P<ph>\d+)-BW(?P<stride>\d+)\.packed_word')


def unpack_padded(data, bits, width, height, stride):
    if bits not in (10, 12) or min(width, height, stride) <= 0:
        raise ValueError('Invalid packed plane dimensions/bit depth')
    count = 4 if bits == 10 else 2
    row_bytes = width * bits // 8
    if width % count or stride < row_bytes or len(data) % stride or len(data) < stride * height:
        raise ValueError('Incomplete groups, short rows or invalid allocation length')
    # Each plane may have its own extra scanlines, including extra chroma rows.
    compact = b''.join(data[y * stride:y * stride + row_bytes] for y in range(height))
    return p1.unpack(compact, bits, width, height, row_bytes)


def plane_stats(values, width, height):
    if min(width, height) <= 0 or len(values) != width * height:
        raise ValueError('Invalid valid-image dimensions')
    rows = [sum(values[y * width:(y + 1) * width]) / width for y in range(height)]
    midpoint = height // 2
    upper, lower = values[:midpoint * width], values[midpoint * width:]
    return {'mean': sum(values) / len(values), 'min': min(values), 'max': max(values),
            'upper_mean': sum(upper) / len(upper) if upper else None,
            'lower_mean': sum(lower) / len(lower),
            'largest_row_mean_edges': sorted(
                ((abs(rows[y] - rows[y - 1]), y) for y in range(1, height)), reverse=True)[:3]}


def yuv_stats(y, uv, width, height, bits):
    divisor = 1 << (bits - 8)
    return {name: plane_stats(array('d', (v / divisor for v in values)), w, h)
            for name, values, w, h in (('Y', y, width, height),
                                      ('U', uv[0::2], width // 2, height // 2),
                                      ('V', uv[1::2], width // 2, height // 2))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('retrieved_directory', type=Path)
    parser.add_argument('output_directory', type=Path, help='New directory; existing paths refused')
    args = parser.parse_args()
    if not shutil.which('ffmpeg'):
        parser.error('Host FFmpeg required')
    run = p1.one((args.retrieved_directory / 'captures').glob('run.*'))
    dump = p1.one((run / 'p1-dump').glob('UKey*'))
    groups = {}
    for path in sorted(dump.glob('*.packed_word')):
        match = PACKED_NAME.fullmatch(path.name)
        if not match:
            raise ValueError('Unrecognized packed filename: ' + path.name)
        item = match.groupdict()
        key = (item['prefix'], item['label'])
        planes = groups.setdefault(key, {})
        if item['plane'] in planes:
            raise ValueError('Duplicate plane: ' + path.name)
        planes[item['plane']] = (path, item)
    if len({key[0] for key in groups}) != 1 or len(groups) != 18:
        raise ValueError('Expected one internal dump ID and 18 labelled packed images')
    out = args.output_directory
    out.mkdir(mode=0o700)
    report = {'run': str(run), 'dump': str(dump), 'sources': {}, 'images': {},
              'packing': 'Contiguous little-endian 10/12-bit samples, supported by known-pattern P1 pixels',
              'statistics': '8-bit-equivalent valid-image samples; allocation padding excluded',
              'limitations': 'Preview is not colour calibration. Internal IDs are not client frame sequences.'}
    def read(path):
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        report['sources'][str(path)] = {'bytes': len(data), 'sha256': digest}
        return data

    for (_, label), planes in sorted(groups.items()):
        if set(planes) != {'yplane', 'cplane'}:
            raise ValueError('Missing Y/chroma plane: ' + label)
        ypath, meta = planes['yplane']
        cpath, cmeta = planes['cplane']
        for field in ('prefix', 'label', 'width', 'height', 'bits', 'pw', 'ph', 'stride'):
            if meta[field] != cmeta[field]:
                raise ValueError('Plane metadata disagreement: ' + label)
        width, height, bits, pw, ph, stride = (int(meta[k]) for k in
                                             ('width', 'height', 'bits', 'pw', 'ph', 'stride'))
        if width % 2 or height % 2 or pw < width or ph < height or stride * 8 != pw * bits:
            raise ValueError('Invalid visible/allocation layout: ' + label)
        ydata, cdata = read(ypath), read(cpath)
        if len(ydata) != ph * stride:
            raise ValueError('Luma allocation disagrees with filename: ' + label)
        y = unpack_padded(ydata, bits, width, height, stride)
        uv = unpack_padded(cdata, bits, width, height // 2, stride)
        item = {'width': width, 'height': height, 'bits': bits, 'stride': stride,
                'allocated_y_rows': len(ydata) // stride, 'allocated_uv_rows': len(cdata) // stride,
                'uv_allocation_bytes': len(cdata),
                'uv_allocation_nonzero_bytes': sum(byte != 0 for byte in cdata),
                'planes': yuv_stats(y, uv, width, height, bits),
                'source_planes': {'Y': str(ypath), 'UV': str(cpath)}}
        report['images'][label] = item
        frame = out / (label + '.nv12')
        frame.write_bytes(bytes(v >> (bits - 8) for v in y) + bytes(v >> (bits - 8) for v in uv))
        p1.preview(frame, 'nv12', f'{width}x{height}', out / (label + '.png'))
        print(label, 'Y/U/V upper/lower:', ', '.join(
            f'{name} {stats["upper_mean"]:.2f}/{stats["lower_mean"]:.2f}'
            for name, stats in item['planes'].items()))

    final = p1.one(dump.glob('*-WPE_P2_PQDIP_MS_F0-WDMAO__640x480_8-PW640-PH480-BW640.nv12'))
    final_data = read(final)
    if len(final_data) != 460800:
        raise ValueError('ODT final does not match traced 640x480 NV12 layout')
    report['images']['ODT-final'] = {'width': 640, 'height': 480, 'bits': 8,
                                    'planes': yuv_stats(final_data[:307200], final_data[307200:], 640, 480, 8)}
    p1.preview(final, 'nv12', '640x480', out / 'ODT-final.png')
    matches = []
    for path in sorted(run.glob('colour-bars-cam0-stream0-*.nv12')):
        data = read(path)
        if len(data) != 460800:
            raise ValueError('Invalid client NV12 size: ' + path.name)
        if data == final_data:
            matches.append(path.name)
        if path.name.endswith(('000000.nv12', '000063.nv12')):
            label = 'client-' + path.stem.rsplit('-', 1)[1]
            report['images'][label] = {'width': 640, 'height': 480, 'bits': 8,
                                      'planes': yuv_stats(data[:307200], data[307200:], 640, 480, 8)}
            p1.preview(path, 'nv12', '640x480', out / (label + '.png'))
    report['odt_final_exact_client_matches'] = matches
    report['same_payload_checks'] = {}
    for a, b in (('P1-YUVO_R1', 'WPE_P2_PQDIP_MS_F0-IMGI_D1'),
                 ('P1-YUVO_R2', 'P2_MS_F1-IMGI_D1'),
                 ('P2_MS_F1-IMG3O', 'WPE_P2_PQDIP_MS_F0-RECI_D1')):
        report['same_payload_checks'][a + ' -> ' + b] = {
            plane: report['sources'][report['images'][a]['source_planes'][plane]]['sha256'] ==
                   report['sources'][report['images'][b]['source_planes'][plane]]['sha256']
            for plane in ('Y', 'UV')}
    (out / 'pixel-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Same-payload checks:', report['same_payload_checks'])
    print('ODT final exact client matches:', matches)
    print('Offline comparison:', out)


if __name__ == '__main__':
    main()
