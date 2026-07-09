"""从 RT/MAT 射线结果构造硬件可配置的 TDL 表。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd
import scipy.io as sio

from .protocol import (
    DEFAULT_CONFIGURED_PATHS,
    PATH_DELAY_CODE_MAX,
    PATH_DELAY_CODE_MIN,
    PATH_DELAY_UNIT_NS,
    amplitude_to_path_code,
    delay_code_to_ns,
    doppler_hz_to_code,
    phase_rad_to_code,
)


@dataclass(frozen=True)
class TdlBuildConfig:
    """RT -> TDL 的算法配置。

    max_paths 控制最终选择多少条硬件路径，默认 24。
    power_mode 控制同一时延 bin 中射线功率如何合并。
    """

    max_paths: int = DEFAULT_CONFIGURED_PATHS
    use_rays_ctf: bool = True
    freq_index: int = 0
    power_mode: str = "noncoherent"
    default_doppler_hz: float = 0.0
    delay_seconds_column: int = 2
    rays_gain_real_column: int = 4
    rays_gain_imag_column: int = 5


def load_mat_file(mat_file: str | Path) -> dict[str, object]:
    """读取 MATLAB 文件，并去掉 scipy 自动生成的 __header__ 等元数据。"""
    data = sio.loadmat(str(mat_file))
    return {key: value for key, value in data.items() if not key.startswith("__")}


def extract_ray_gain(data: Mapping[str, object], config: TdlBuildConfig) -> np.ndarray:
    """提取每条射线的复增益。

    优先使用 RaysCTF：前半列为实部，后半列为虚部。
    如果关闭 use_rays_ctf，则退回 RaysProperties 的 E_RE/E_Im 列。
    """
    rays = np.asarray(data["RaysProperties"], dtype=np.float64)

    if config.use_rays_ctf and "RaysCTF" in data:
        rays_ctf = np.asarray(data["RaysCTF"], dtype=np.float64)
        if rays_ctf.ndim != 2:
            raise ValueError("RaysCTF must be a 2D array")

        # RaysCTF 采用 [real(freq0..N), imag(freq0..N)] 的横向拼接格式。
        n_col = rays_ctf.shape[1]
        if n_col % 2 != 0:
            raise ValueError("RaysCTF columns must be real parts followed by imaginary parts")

        freq_points = n_col // 2
        if not (0 <= config.freq_index < freq_points):
            raise ValueError(f"freq_index out of range; available points: {freq_points}")

        real = rays_ctf[:, config.freq_index]
        imag = rays_ctf[:, config.freq_index + freq_points]
        return real + 1j * imag

    real = rays[:, config.rays_gain_real_column]
    imag = rays[:, config.rays_gain_imag_column]
    return real + 1j * imag


def build_tdl_from_rt(data: Mapping[str, object], config: TdlBuildConfig | None = None) -> pd.DataFrame:
    """将 RT 射线表量化、合并并转换成设备参数表。

    核心流程：
    1. 读取每条射线的 delay 和复增益；
    2. 按硬件时延分辨率量化到 delay_code；
    3. 同一 delay_code 内合并射线；
    4. 按功率选出最强 max_paths 条；
    5. 归一化幅度并生成 delay/amp/phase/doppler 协议码值。
    """
    config = config or TdlBuildConfig()
    if config.max_paths <= 0:
        raise ValueError("max_paths must be positive")

    rays = np.asarray(data["RaysProperties"], dtype=np.float64)
    gain = extract_ray_gain(data, config)

    # 当前 RT 数据约定 RaysProperties 第 3 列为秒级 delay。
    delay_s = rays[:, config.delay_seconds_column]
    delay_ns = delay_s * 1e9
    power = np.abs(gain) ** 2

    # 剔除 NaN/Inf 和零功率射线，避免后续归一化或 log10 出错。
    valid = np.isfinite(delay_ns) & np.isfinite(power) & (power > 0)
    delay_ns = delay_ns[valid]
    gain = gain[valid]
    power = power[valid]

    # 将连续时延投影到硬件可配置的离散时延格点。
    delay_code = np.round(delay_ns / PATH_DELAY_UNIT_NS).astype(int)
    in_range = (delay_code >= PATH_DELAY_CODE_MIN) & (delay_code <= PATH_DELAY_CODE_MAX)
    delay_ns = delay_ns[in_range]
    gain = gain[in_range]
    power = power[in_range]
    delay_code = delay_code[in_range]

    if len(delay_ns) == 0:
        raise ValueError("no valid ray paths remain after delay/gain filtering")

    rows: list[dict[str, float | int]] = []
    for code in np.unique(delay_code):
        idx = np.where(delay_code == code)[0]
        gain_group = gain[idx]
        power_group = power[idx]
        delay_group = delay_ns[idx]

        # coherent_power 保留相位相干叠加结果；
        # noncoherent_power 保留 PDP 常用的功率非相干叠加结果。
        coherent_gain = np.sum(gain_group)
        coherent_power = float(np.abs(coherent_gain) ** 2)
        noncoherent_power = float(np.sum(power_group))
        weighted_delay_ns = float(np.sum(delay_group * power_group) / np.sum(power_group))

        selected_power = _select_power(
            coherent_power=coherent_power,
            noncoherent_power=noncoherent_power,
            mode=config.power_mode,
        )
        phase_rad = float(np.angle(coherent_gain)) if np.abs(coherent_gain) > 0 else 0.0

        rows.append(
            {
                "delay_code": int(code),
                "delay_actual_ns": delay_code_to_ns(int(code)),
                "delay_weighted_ns": weighted_delay_ns,
                "ray_count_in_bin": int(len(idx)),
                "coherent_gain_real": float(np.real(coherent_gain)),
                "coherent_gain_imag": float(np.imag(coherent_gain)),
                "coherent_power": coherent_power,
                "noncoherent_power": noncoherent_power,
                "selected_power": selected_power,
                "phase_rad": phase_rad,
                "doppler_Hz": float(config.default_doppler_hz),
            }
        )

    df = pd.DataFrame(rows)
    df = df[df["selected_power"] > 0].copy()
    if df.empty:
        raise ValueError("no valid TDL bins after power merge")

    # 先按功率截断，再按时延排序，便于硬件配置和人工检查 PDP。
    df = df.sort_values("selected_power", ascending=False).head(config.max_paths)
    df = df.sort_values("delay_code", ascending=True).reset_index(drop=True)

    # 以最强径为 0 dB/幅度 1，对其余径做相对归一化。
    p_max = float(df["selected_power"].max())
    df["relative_power_dB"] = 10.0 * np.log10(df["selected_power"] / p_max)
    df["amp_linear"] = np.sqrt(df["selected_power"] / p_max)
    df["amp_code"] = df["amp_linear"].apply(amplitude_to_path_code)
    df["phase_code"] = df["phase_rad"].apply(phase_rad_to_code)
    df["doppler_code"] = df["doppler_Hz"].apply(doppler_hz_to_code)
    df["hw_path_index"] = np.arange(len(df), dtype=int)
    df["hw_path_id"] = df["hw_path_index"] + 1

    return df[
        [
            "hw_path_id",
            "hw_path_index",
            "delay_weighted_ns",
            "delay_actual_ns",
            "delay_code",
            "relative_power_dB",
            "amp_linear",
            "amp_code",
            "phase_rad",
            "phase_code",
            "doppler_Hz",
            "doppler_code",
            "ray_count_in_bin",
            "coherent_gain_real",
            "coherent_gain_imag",
            "coherent_power",
            "noncoherent_power",
            "selected_power",
        ]
    ]


def _select_power(coherent_power: float, noncoherent_power: float, mode: str) -> float:
    """根据配置选择同一 delay bin 的合并功率定义。"""
    normalized = mode.strip().lower()
    if normalized == "coherent":
        return coherent_power
    if normalized == "noncoherent":
        return noncoherent_power
    raise ValueError("power_mode must be 'coherent' or 'noncoherent'")
