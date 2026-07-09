import math
import struct
import unittest

import pandas as pd

from channel_simulator.commands import ChannelFrameConfig, make_channel_control_frame
from channel_simulator.protocol import (
    DEFAULT_CONFIGURED_PATHS,
    FRAME_HEADER,
    FRAME_TAIL,
    ParamID,
    PHASE_SUPPORTED_PATHS,
    SubFrame,
    amplitude_to_output_code,
    amplitude_to_path_code,
    build_control_frame,
    delay_ns_to_code,
    doppler_hz_to_code,
    io_byte,
    phase_rad_to_code,
)


class ProtocolTests(unittest.TestCase):
    """协议层轻量回归测试，防止后续改动破坏字节格式。"""

    def test_frame_wraps_payload_with_header_length_and_tail(self):
        frame = build_control_frame([SubFrame(ParamID.GLOBAL_ENABLE, b"\x01")])

        self.assertTrue(frame.startswith(FRAME_HEADER))
        self.assertTrue(frame.endswith(FRAME_TAIL))
        self.assertEqual(struct.unpack("<H", frame[4:6])[0], 5)
        self.assertEqual(frame[6:-2], bytes([1, 1, 0, 0, 1]))

    def test_io_byte_uses_high_input_low_output_nibbles(self):
        self.assertEqual(io_byte(3, 5), 0x35)

    def test_protocol_examples_and_unit_conversions(self):
        self.assertEqual(delay_ns_to_code(25.0), 3)
        self.assertEqual(amplitude_to_path_code(3 / 32768.0), 3)
        self.assertEqual(amplitude_to_output_code(0.5), 8192)
        self.assertEqual(doppler_hz_to_code(3 / 35.791394133), 3)

    def test_phase_wraps_to_12_bit_turn(self):
        self.assertEqual(phase_rad_to_code(0.0), 0)
        self.assertEqual(phase_rad_to_code(2.0 * math.pi), 0)
        self.assertEqual(phase_rad_to_code(math.pi), 2048)

    def test_default_frame_config_clears_24_paths(self):
        tdl_df = _make_tdl_df(1)

        frame = make_channel_control_frame(tdl_df, ChannelFrameConfig())
        payload = frame[6:-2]
        subframes = _parse_fixed_subframes(payload)

        disabled_paths = [
            item
            for item in subframes
            if item["param_id"] == ParamID.PATH_ENABLE
            and item["length"] == 1
            and item["payload"] == b"\x00"
        ]
        self.assertEqual(DEFAULT_CONFIGURED_PATHS, 24)
        self.assertEqual(len(disabled_paths), 24)

    def test_default_frame_config_writes_phase_for_24_paths(self):
        tdl_df = _make_tdl_df(24)
        frame = make_channel_control_frame(tdl_df, ChannelFrameConfig())
        subframes = _parse_fixed_subframes(frame[6:-2])

        phase_subframes = [
            item
            for item in subframes
            if item["param_id"] == ParamID.PHASE
        ]

        self.assertEqual(PHASE_SUPPORTED_PATHS, 24)
        self.assertEqual(len(phase_subframes), 24)
        self.assertEqual([item["info"] for item in phase_subframes], list(range(24)))


def _make_tdl_df(path_count):
    return pd.DataFrame(
        [
            {
                "hw_path_index": index,
                "delay_code": index,
                "amp_code": 32768,
                "phase_code": index,
                "doppler_code": 0,
            }
            for index in range(path_count)
        ]
    )


def _parse_fixed_subframes(payload):
    """测试辅助：解析当前测试中只含普通长度字段的子帧。"""
    subframes = []
    offset = 0
    while offset < len(payload):
        param_id = payload[offset]
        length = payload[offset + 1]
        io = payload[offset + 2]
        info = payload[offset + 3]
        body = payload[offset + 4 : offset + 4 + length]
        subframes.append(
            {
                "param_id": param_id,
                "length": length,
                "io": io,
                "info": info,
                "payload": body,
            }
        )
        offset += 4 + length
    return subframes


if __name__ == "__main__":
    unittest.main()
