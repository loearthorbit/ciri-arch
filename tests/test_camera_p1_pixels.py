"""Offline packed-pixel decoder tests; no vendor code or camera execution."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ciri_pixels', ROOT / 'scripts/compare-ciri-p1-pixels.py')
pixels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pixels)


class CameraP1PixelTests(unittest.TestCase):
    def test_known_white_and_neutral_chroma_groups(self):
        self.assertEqual(list(pixels.unpack(bytes.fromhex('fcf3cf3fff'), 10, 4, 1, 5)), [1020] * 4)
        self.assertEqual(list(pixels.unpack(bytes.fromhex('0002082080'), 10, 4, 1, 5)), [512] * 4)
        self.assertEqual(list(pixels.unpack(bytes.fromhex('fccfff'), 12, 2, 1, 3)), [4092] * 2)
        self.assertEqual(list(pixels.unpack(bytes.fromhex('000880'), 12, 2, 1, 3)), [2048] * 2)

    def test_full_value_ranges_round_trip(self):
        for bits, count, stride in ((10, 4, 5), (12, 2, 3)):
            values = [i % (1 << bits) for i in range((1 << bits) * count)]
            data = b''.join(sum(values[offset + i] << (i * bits) for i in range(count)).to_bytes(stride, 'little')
                            for offset in range(0, len(values), count))
            self.assertEqual(list(pixels.unpack(data, bits, count, len(values) // count, stride)), values)

    def test_invalid_lengths_or_group_layouts_refused(self):
        for args in ((b'\0' * 5, 8, 4, 1, 5), (b'\0' * 4, 10, 4, 1, 5),
                     (b'\0' * 6, 10, 4, 1, 6), (b'\0' * 3, 12, 1, 1, 3)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                pixels.unpack(*args)

    def test_midpoint_edge_is_measured_in_luma(self):
        result = pixels.stats(bytes([10] * 8 + [80] * 8), 4, 4)
        self.assertEqual(result['upper']['mean'], 10)
        self.assertEqual(result['lower']['mean'], 80)
        self.assertEqual(result['largest_row_mean_edges'][0], (70, 2))


if __name__ == '__main__':
    unittest.main()
