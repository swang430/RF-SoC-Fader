"""根据 TDL 表组装信道模拟器控制帧。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .protocol import (
    DEFAULT_CONFIGURED_PATHS,
    MAX_CHANNEL_PATHS,
    PHASE_SUPPORTED_PATHS,
    ParamID,
    SubFrame,
    amplitude_to_output_code,
    build_control_frame,
    i16,
    i32,
    io_byte,
    path_info,
    u8,
    u16,
)


@dataclass(frozen=True)
class ChannelFrameConfig:
    """控制帧组装配置。

    input_id/output_id 选择要配置的输入-输出信道。
    hardware_paths 表示本次会先清空并可配置的硬件多径数量，默认 24。
    max_phase_paths 表示写入相偏的多径数量，默认与 24 条硬件多径一致。
    """

    input_id: int = 0
    output_id: int = 0
    hardware_paths: int = DEFAULT_CONFIGURED_PATHS
    reset_first: bool = True
    enable_info_return: bool = True
    enable_copy_return: bool = True
    output_attenuation_linear: float = 1.0
    awgn_enable: bool = False
    write_phase: bool = True
    max_phase_paths: int = PHASE_SUPPORTED_PATHS
    write_doppler: bool = False


def make_channel_control_frame(tdl_df: pd.DataFrame, config: ChannelFrameConfig | None = None) -> bytes:
    """把 TDL DataFrame 转换成可直接下发的二进制控制帧。"""
    config = config or ChannelFrameConfig()
    _validate_config(config)

    subframes: list[SubFrame] = []
    # 信道参数使用真实输入/输出端；输出端参数只关心输出端，输入端写 0。
    channel_io = io_byte(config.input_id, config.output_id)
    output_io = io_byte(0, config.output_id)

    # 复位和回传控制放在最前面，便于设备先清状态，再回显/回传诊断信息。
    if config.reset_first:
        subframes.append(SubFrame(ParamID.RESET, u8(1)))

    if config.enable_copy_return:
        subframes.append(SubFrame(ParamID.COPY_RETURN, u8(1)))

    if config.enable_info_return:
        subframes.append(SubFrame(ParamID.INFO_RETURN, u8(0x03)))

    subframes.extend(
        [
            # 全局和输出端都必须使能，否则即使多径配置正确也不会输出。
            SubFrame(ParamID.GLOBAL_ENABLE, u8(1)),
            SubFrame(ParamID.OUTPUT_ENABLE, u8(1), io=output_io),
            SubFrame(
                ParamID.OUTPUT_ATTENUATION,
                u16(amplitude_to_output_code(config.output_attenuation_linear)),
                io=output_io,
            ),
            SubFrame(ParamID.AWGN_ENABLE, u8(1 if config.awgn_enable else 0), io=output_io),
        ]
    )

    # 先关闭所有可用多径，清除设备上一次配置留下的残余路径。
    for path_index in range(config.hardware_paths):
        subframes.append(
            SubFrame(
                ParamID.PATH_ENABLE,
                u8(0),
                io=channel_io,
                info=path_info(path_index, config.hardware_paths),
            )
        )

    for _, row in tdl_df.iterrows():
        path_index = int(row["hw_path_index"])
        if path_index >= config.hardware_paths:
            raise ValueError(
                f"TDL path index {path_index} exceeds configured hardware_paths={config.hardware_paths}"
            )

        subframes.extend(
            [
                # 每条有效径至少需要：使能、主时延、幅度衰减。
                SubFrame(
                    ParamID.PATH_ENABLE,
                    u8(1),
                    io=channel_io,
                    info=path_info(path_index, config.hardware_paths),
                ),
                SubFrame(
                    ParamID.PATH_DELAY,
                    i16(int(row["delay_code"])),
                    io=channel_io,
                    info=path_info(path_index, config.hardware_paths),
                ),
                SubFrame(
                    ParamID.PATH_ATTENUATION,
                    u16(int(row["amp_code"])),
                    io=channel_io,
                    info=path_info(path_index, config.hardware_paths),
                ),
            ]
        )

        if config.write_phase and path_index < config.max_phase_paths:
            # 相偏表格在文档中是简写；当前按设备确认的 24 条多径全部支持处理。
            subframes.append(
                SubFrame(
                    ParamID.PHASE,
                    u16(int(row["phase_code"])),
                    io=channel_io,
                    info=path_info(path_index, config.hardware_paths),
                )
            )

        if config.write_doppler:
            # 当前 RT 单快照通常没有真实多普勒；开启后会写入 TDL 表中的 doppler_code。
            subframes.append(
                SubFrame(
                    ParamID.PATH_DOPPLER,
                    i32(int(row["doppler_code"])),
                    io=channel_io,
                    info=path_info(path_index, config.hardware_paths),
                )
            )

    if config.write_doppler:
        # 写多普勒后执行一次频偏相位归零，便于多通道/多径同步起相。
        subframes.append(SubFrame(ParamID.FREQUENCY_PHASE_ZERO, u8(1)))

    return build_control_frame(subframes)


def _validate_config(config: ChannelFrameConfig) -> None:
    """检查配置是否超过协议和当前设备支持范围。"""
    if not (1 <= config.hardware_paths <= MAX_CHANNEL_PATHS):
        raise ValueError(f"hardware_paths must be in 1..{MAX_CHANNEL_PATHS}")
    if not (0 <= config.max_phase_paths <= config.hardware_paths):
        raise ValueError("max_phase_paths must be in 0..hardware_paths")
