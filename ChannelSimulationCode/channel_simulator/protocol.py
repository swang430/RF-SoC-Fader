"""信道模拟器3协议 V3.0 的底层编码工具。

协议文档中的控制帧格式为：

    header + payload_length_le16 + subframes + tail

普通参数子帧格式为：

    parameter_id + parameter_payload_length + io_address + extra_info + payload
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import math
import struct


# 不同方向/类型帧的固定头尾。当前下发控制使用 FRAME_HEADER。
FRAME_HEADER = bytes.fromhex("FD B1 85 40")
COPY_FRAME_HEADER = bytes.fromhex("FD B1 85 69")
ERROR_FRAME_HEADER = bytes.fromhex("FD B1 85 FF")
SERIAL_FRAME_HEADER = bytes.fromhex("FD B1 85 50")
FRAME_TAIL = bytes.fromhex("AE 86")

# 协议规定：帧长度字段只统计子帧 payload 总长度，不含帧头、长度字段和帧尾。
MAX_FRAME_PAYLOAD_LEN = 4000

# 设备当前确认支持每信道 24 条多径；协议表中的相偏条目是简写，也按 24 条处理。
MAX_CHANNEL_PATHS = 24
DEFAULT_CONFIGURED_PATHS = 24
PHASE_SUPPORTED_PATHS = 24

# 多径主时延：code * (1000/120) ns，协议范围 0..1050。
PATH_DELAY_UNIT_NS = 1000.0 / 120.0
PATH_DELAY_CODE_MIN = 0
PATH_DELAY_CODE_MAX = 1050

# 各物理量到协议整数码值的缩放系数。
PATH_AMP_SCALE = 32768.0
OUTPUT_AMP_SCALE = 16384.0
AWGN_POWER_SCALE = 4096.0
PHASE_SCALE = 4096.0
DOPPLER_CODE_PER_HZ = 35.791394133


class ParamID(IntEnum):
    """协议 V3.0 参数 ID 枚举，名字与文档章节含义对应。"""

    GLOBAL_ENABLE = 1
    PATH_ENABLE = 2
    PATH_DELAY = 3
    PATH_DOPPLER = 4
    PATH_RAYLEIGH_ENABLE = 5
    PATH_RAYLEIGH_FILTER = 6
    PATH_ATTENUATION = 7
    AWGN_ENABLE = 8
    AWGN_POWER = 9
    OUTPUT_ENABLE = 10
    OUTPUT_ATTENUATION = 11
    INPUT_DELAY = 12
    RESET = 13
    INFO_RETURN = 14
    COPY_RETURN = 15
    PATH_EXPANSION = 16
    SWEEP_END = 17
    SWEEP_SPEED = 18
    SWEEP_MODE = 19
    SWEEP_RESTART = 20
    INPUT_TOPOLOGY = 21
    HARDWARE_RESET = 22
    SWEEP_START = 23
    DELAY_SUPPORTED_INPUTS = 24
    SIGNAL_SOURCE_ENABLE = 25
    SIGNAL_SOURCE_FREQUENCY = 26
    SIGNAL_SOURCE_PULSE_ENABLE = 27
    SIGNAL_SOURCE_PULSE_WIDTH = 28
    SIGNAL_SOURCE_PULSE_PERIOD = 29
    RX_SERIAL_TRANSFER = 30
    CHANNEL_MAIN_DELAY = 31
    PHASE = 32
    FREQUENCY_PHASE_ZERO = 33
    CHANNEL_FRACTIONAL_DELAY = 34
    TX_SERIAL_TRANSFER = 35


@dataclass(frozen=True)
class SubFrame:
    """一个参数子帧。

    io 字节高 4 bit 表示输入端，低 4 bit 表示输出端。
    info 通常表示多径编号，协议中从 0 开始对应“多径1”。
    """

    param_id: int | ParamID
    payload: bytes
    io: int = 0
    info: int = 0

    def to_bytes(self) -> bytes:
        """按协议顺序编码为 ID/长度/IO/补充信息/参数内容。"""
        payload = bytes(self.payload)
        length = _subframe_length_field(int(self.param_id), len(payload))
        return bytes(
            [
                int(self.param_id) & 0xFF,
                length & 0xFF,
                self.io & 0xFF,
                self.info & 0xFF,
            ]
        ) + payload


def _subframe_length_field(param_id: int, payload_len: int) -> int:
    """计算子帧长度字段。

    普通参数长度字段只有 1 字节，因此最多 255。
    瑞利频域滤波系数是文档里的特殊项：长度字段写 0，但实际跟 1024 字节。
    """
    if payload_len <= 255:
        return payload_len

    if param_id == ParamID.PATH_RAYLEIGH_FILTER and payload_len == 1024:
        return 0

    raise ValueError(f"subframe payload is too long: {payload_len} bytes")


def build_control_frame(subframes: list[SubFrame] | tuple[SubFrame, ...]) -> bytes:
    """把若干参数子帧封装为完整控制帧。"""
    payload = b"".join(subframe.to_bytes() for subframe in subframes)
    if len(payload) > MAX_FRAME_PAYLOAD_LEN:
        raise ValueError(
            f"control frame payload is {len(payload)} bytes, "
            f"over protocol limit {MAX_FRAME_PAYLOAD_LEN}"
        )
    return FRAME_HEADER + struct.pack("<H", len(payload)) + payload + FRAME_TAIL


def io_byte(input_id: int, output_id: int) -> int:
    """生成输入输出端编码：高半字节为输入端，低半字节为输出端。"""
    if not (0 <= input_id <= 7):
        raise ValueError("input_id must be in 0..7")
    if not (0 <= output_id <= 7):
        raise ValueError("output_id must be in 0..7")
    return ((input_id & 0x0F) << 4) | (output_id & 0x0F)


def path_info(path_index: int, max_paths: int = MAX_CHANNEL_PATHS) -> int:
    """生成多径补充信息字段，path_index=0 对应协议里的多径1。"""
    if not (0 <= path_index < max_paths <= MAX_CHANNEL_PATHS):
        raise ValueError(f"path_index must be in 0..{max_paths - 1}")
    return path_index


def clip_int(value: float, lower: int, upper: int) -> int:
    """四舍五入后限制到协议允许的整数范围。"""
    return int(max(lower, min(upper, round(value))))


def delay_ns_to_code(delay_ns: float) -> int:
    """把 ns 时延换算为多径主时延码值。"""
    return clip_int(delay_ns / PATH_DELAY_UNIT_NS, PATH_DELAY_CODE_MIN, PATH_DELAY_CODE_MAX)


def delay_code_to_ns(code: int) -> float:
    """把多径主时延码值反算为实际硬件时延 ns。"""
    if not (PATH_DELAY_CODE_MIN <= code <= PATH_DELAY_CODE_MAX):
        raise ValueError(f"delay code must be in {PATH_DELAY_CODE_MIN}..{PATH_DELAY_CODE_MAX}")
    return code * PATH_DELAY_UNIT_NS


def amplitude_to_path_code(amplitude_linear: float) -> int:
    """多径线性幅度系数 -> 协议码值，单位 1/32768。"""
    if not math.isfinite(amplitude_linear):
        raise ValueError("amplitude must be finite")
    return clip_int(amplitude_linear * PATH_AMP_SCALE, 0, 65535)


def amplitude_to_output_code(amplitude_linear: float) -> int:
    """输出端线性幅度系数 -> 协议码值，单位 1/16384。"""
    if not math.isfinite(amplitude_linear):
        raise ValueError("output amplitude must be finite")
    return clip_int(amplitude_linear * OUTPUT_AMP_SCALE, 0, 65535)


def phase_rad_to_code(phase_rad: float) -> int:
    """相位弧度 -> 0..4095，其中一整圈 2pi 对应 4096 个刻度。"""
    if not math.isfinite(phase_rad):
        raise ValueError("phase must be finite")
    phase = phase_rad % (2.0 * math.pi)
    return int(round(phase / (2.0 * math.pi) * PHASE_SCALE)) % 4096


def doppler_hz_to_code(doppler_hz: float) -> int:
    """多普勒 Hz -> 协议码值，满足 Hz = code / 35.791394133。"""
    if not math.isfinite(doppler_hz):
        raise ValueError("doppler must be finite")
    return clip_int(doppler_hz * DOPPLER_CODE_PER_HZ, -(2**27), 2**27 - 1)


def awgn_k_to_code(k_linear: float) -> int:
    """AWGN 功率参数 k -> 协议码值，单位 1/4096。"""
    if not math.isfinite(k_linear):
        raise ValueError("awgn k must be finite")
    return clip_int(k_linear * AWGN_POWER_SCALE, 100, 65535)


def u8(value: int) -> bytes:
    """无符号 8 位小端编码；用于 1 字节开关类参数。"""
    return struct.pack("<B", value)


def u16(value: int) -> bytes:
    """无符号 16 位小端编码；用于幅值、相偏等非负参数。"""
    return struct.pack("<H", value)


def i16(value: int) -> bytes:
    """有符号 16 位小端编码；用于多径主时延等 int16 参数。"""
    return struct.pack("<h", value)


def i32(value: int) -> bytes:
    """有符号 32 位小端编码；用于多普勒和扫频类参数。"""
    return struct.pack("<i", value)
