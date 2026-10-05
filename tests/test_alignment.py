import unittest

from alignment import (
    AlignmentPoint,
    MultiStarAlignment,
    TwoStarAlignment,
    equatorial_to_horizontal,
    horizontal_to_equatorial,
    wrap_degrees,
)


class AlignmentTests(unittest.TestCase):
    def test_equatorial_horizontal_round_trip(self):
        timestamp = 1791216000.0
        az, alt = equatorial_to_horizontal(5.5, 20.0, 37.5, 127.0, timestamp)
        ra, dec = horizontal_to_equatorial(az, alt, 37.5, 127.0, timestamp)
        self.assertAlmostEqual(wrap_degrees((ra - 5.5) * 15.0), 0.0, places=7)
        self.assertAlmostEqual(dec, 20.0, places=7)

    def test_two_star_count_mapping(self):
        model = TwoStarAlignment()
        model.add(AlignmentPoint(100.0, 30.0, 10000.0, 20000.0))
        model.add(AlignmentPoint(140.0, 50.0, 14000.0, 18000.0))
        self.assertTrue(model.ready)
        self.assertEqual(model.counts_for(120.0, 40.0), (12000.0, 19000.0))
        az, alt = model.horizontal_for_counts(12000.0, 19000.0)
        self.assertAlmostEqual(az, 120.0)
        self.assertAlmostEqual(alt, 40.0)

    def test_rejects_nearly_same_alignment_stars(self):
        model = TwoStarAlignment()
        model.add(AlignmentPoint(100.0, 30.0, 10000.0, 20000.0))
        with self.assertRaises(ValueError):
            model.add(AlignmentPoint(102.0, 31.0, 12000.0, 19000.0))

    def test_multistar_affine_fit_and_undo(self):
        model = MultiStarAlignment()
        # Counts include small cross-axis terms, as expected from mount tilt.
        for az, alt in ((100.0, 20.0), (140.0, 35.0), (115.0, 60.0), (165.0, 50.0)):
            x = wrap_degrees(az - 100.0)
            model.add(
                AlignmentPoint(
                    az,
                    alt,
                    10000.0 + 100.0 * x + 5.0 * alt,
                    20000.0 - 3.0 * x - 90.0 * alt,
                )
            )
        self.assertTrue(model.ready)
        self.assertAlmostEqual(model.rms_error_deg, 0.0, places=7)
        counts = model.counts_for(130.0, 45.0)
        az, alt = model.horizontal_for_counts(*counts)
        self.assertAlmostEqual(az, 130.0, places=7)
        self.assertAlmostEqual(alt, 45.0, places=7)
        self.assertEqual(model.remove_last(), 3)


if __name__ == "__main__":
    unittest.main()
