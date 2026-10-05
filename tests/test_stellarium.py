import struct
import unittest

from stellarium_server import decode_goto, encode_position


class StellariumProtocolTests(unittest.TestCase):
    def test_decode_goto(self):
        ra_hours = 6.0
        dec_degrees = -30.0
        ra_raw = round(ra_hours / 24 * 2**32)
        dec_raw = round(dec_degrees / 180 * 2**31)
        packet = struct.pack("<HHQIi", 20, 0, 123456, ra_raw, dec_raw)
        target = decode_goto(packet)
        self.assertAlmostEqual(target.ra_hours, ra_hours, places=6)
        self.assertAlmostEqual(target.dec_degrees, dec_degrees, places=6)

    def test_encode_position_layout(self):
        packet = encode_position(18.0, 45.0)
        self.assertEqual(len(packet), 24)
        length, packet_type = struct.unpack_from("<HH", packet)
        self.assertEqual((length, packet_type), (24, 0))


if __name__ == "__main__":
    unittest.main()
