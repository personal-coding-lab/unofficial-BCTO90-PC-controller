import unittest

from bcto_protocol import (
    FrameParser,
    build_ack,
    build_frame,
    build_initialize,
    build_move,
    build_stop,
    calculate_crc,
    decode_motor_positions,
    decode_i24,
    encode_i24,
)


class ProtocolTests(unittest.TestCase):
    def test_initialize_matches_native_layout(self):
        self.assertEqual(calculate_crc(b"\x00\x11"), 0x0036)
        self.assertEqual(build_initialize(0), bytes.fromhex("5a 06 36 00 00 11"))

    def test_captured_apk_packets_have_valid_crc(self):
        packets = (
            "5a 09 54 be f0 02 00 00 0c",
            "5a 08 83 b2 f0 01 00 00",
            "5a 08 af 8f 01 13 06 60",
            "5a 0f 06 52 02 18 01 da d4 13 00 00 00 00 00",
            "5a 07 ba 26 03 16 01",
        )
        parser = FrameParser()
        for packet_hex in packets:
            frames = parser.feed(bytes.fromhex(packet_hex))
            self.assertEqual(len(frames), 1)
            self.assertTrue(frames[0].crc_valid)

    def test_ack_matches_captured_apk_packet(self):
        self.assertEqual(build_ack(0xF0), bytes.fromhex("5a 08 83 b2 f0 01 00 00"))

    def test_i24_round_trip(self):
        for value in (-0x7B0000, -10, 0, 10, 0x7B0000):
            self.assertEqual(decode_i24(encode_i24(value)), value)

    def test_stop_layout(self):
        packet = build_stop(7, 1)
        self.assertEqual(packet[0], 0x5A)
        self.assertEqual(packet[1], 7)
        self.assertEqual(packet[4:], bytes((7, 0x16, 1)))

    def test_normal_move_has_zero_target(self):
        packet = build_move(3, 0, True, 30)
        self.assertEqual(packet[1], 15)
        self.assertEqual(packet[4:7], bytes((3, 0x18, 0)))
        self.assertLess(decode_i24(packet[7:10]), 0)
        self.assertEqual(packet[10:12], b"\x00\x00")
        self.assertEqual(decode_i24(packet[12:15]), 0)

    def test_100_percent_uses_verified_move_command(self):
        packet = build_move(4, 1, False, 100)
        self.assertEqual(packet[1], 15)
        self.assertEqual(packet[4:7], bytes((4, 0x18, 1)))
        self.assertEqual(decode_i24(packet[7:10]), 0x7B0000)
        self.assertEqual(packet[10:12], b"\x00\x00")
        self.assertEqual(decode_i24(packet[12:15]), 0)

    def test_parser_handles_split_notification(self):
        packet = build_frame(9, 1, b"\x00\x00")
        parser = FrameParser()
        self.assertEqual(parser.feed(packet[:3]), [])
        frames = parser.feed(packet[3:])
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].crc_valid)
        self.assertEqual(frames[0].sequence, 9)

    def test_position_report_from_real_mount_log(self):
        packet = bytes.fromhex("5a 12 8d 8f 00 22 14 ad 00 00 ec 52 00 00 00 00 00 00")
        frame = FrameParser().feed(packet)[0]
        self.assertEqual(decode_motor_positions(frame), (0xAD14, 0x52EC))


if __name__ == "__main__":
    unittest.main()
