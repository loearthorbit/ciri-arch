"""Offline padded pixel decoder tests; no camera or recovered code execution."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ciri_p2_pixels', ROOT / 'scripts/compare-ciri-p2-pixels.py')
pixels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pixels)


def pack_row(values, bits):
    count, step = (4, 5) if bits == 10 else (2, 3)
    return b''.join(sum(values[start + i] << (i * bits) for i in range(count)).to_bytes(step, 'little')
                    for start in range(0, len(values), count))


class CameraP2PixelTests(unittest.TestCase):
    def test_padding_columns_and_scanlines_are_excluded(self):
        for bits, width, stride in ((10, 4, 10), (12, 2, 6)):
            values = list(range(width * 3))
            rows = [pack_row(values[start:start + width], bits) + b'\xff' * (stride // 2)
                    for start in range(0, len(values), width)]
            data = b''.join(rows) + b'\xff' * stride * 5
            self.assertEqual(list(pixels.unpack_padded(data, bits, width, 3, stride)), values)

    def test_small_chroma_plane_can_have_its_own_64_scanlines(self):
        # Observed 40x30 image: UV valid height 15, allocation height 64,
        # 64 packed samples per row, only 40 visible samples.
        data = (pack_row([2048] * 40, 12) + b'\xff' * 36) * 15 + b'\xff' * 96 * 49
        values = pixels.unpack_padded(data, 12, 40, 15, 96)
        self.assertEqual(list(values), [2048] * 600)
        stats = pixels.plane_stats(values, 40, 15)
        self.assertEqual(stats['upper_mean'], 2048)
        self.assertEqual(stats['lower_mean'], 2048)

    def test_invalid_layouts_refused(self):
        for args in ((b'\0' * 5, 10, 4, 2, 5), (b'\0' * 7, 12, 2, 1, 6),
                     (b'\0' * 6, 12, 4, 1, 3), (b'\0' * 6, 12, 3, 1, 6),
                     (b'\0' * 5, 10, 0, 1, 5), (b'\0' * 5, 8, 4, 1, 5)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                pixels.unpack_padded(*args)

    def test_luma_midpoint_and_odd_height_statistics(self):
        stats = pixels.plane_stats(bytes([10] * 8 + [80] * 8), 4, 4)
        self.assertEqual(stats['largest_row_mean_edges'][0], (70, 2))
        odd = pixels.plane_stats([10, 20, 30], 1, 3)
        self.assertEqual(odd['upper_mean'], 10)
        self.assertEqual(odd['lower_mean'], 25)

    def test_observed_label_and_padding_filename(self):
        name = '212032577-0004-0004-P2_MS_F3-IMG3O__160x120_12-cplane-PW192-PH128-BW288.packed_word'
        meta = pixels.PACKED_NAME.fullmatch(name).groupdict()
        self.assertEqual(meta['label'], 'P2_MS_F3-IMG3O')
        self.assertEqual(meta['width'], '160')
        self.assertEqual(meta['pw'], '192')
        self.assertEqual(meta['stride'], '288')
        self.assertIsNone(pixels.PACKED_NAME.fullmatch('../' + name))


if __name__ == '__main__':
    unittest.main()
