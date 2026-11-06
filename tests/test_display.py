import unittest
from fleet.display import duration


class DurationTests(unittest.TestCase):
    def test_duration_boundaries_and_missing_values(self):
        for seconds, expected in ((None, '—'), (-1, '—'), (0, '0s'), (59, '59s'),
                                  (60, '1m'), (3599, '59m'), (3600, '1h'),
                                  (3661, '1h 1m'), (86399, '23h 59m'),
                                  (86400, '1d'), (176460, '2d 1h')):
            with self.subTest(seconds=seconds):
                self.assertEqual(duration(seconds), expected)


if __name__ == '__main__':
    unittest.main()
