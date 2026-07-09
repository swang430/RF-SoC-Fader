"""命令行入口：MAT -> TDL 表 -> 控制帧文件。"""

from __future__ import annotations

import argparse
from pathlib import Path

from .commands import ChannelFrameConfig, make_channel_control_frame
from .tdl import TdlBuildConfig, build_tdl_from_rt, load_mat_file


def main(argv: list[str] | None = None) -> int:
    """解析命令行参数，执行完整转换流程。"""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    mat_file = Path(args.mat_file)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 命令行参数只负责描述用户意图，真正的算法配置交给两个 dataclass。
    data = load_mat_file(mat_file)
    tdl_config = TdlBuildConfig(
        max_paths=args.max_paths,
        use_rays_ctf=not args.use_rays_properties,
        freq_index=args.freq_index,
        power_mode=args.power_mode,
        default_doppler_hz=args.default_doppler_hz,
    )
    frame_config = ChannelFrameConfig(
        input_id=args.input_id,
        output_id=args.output_id,
        hardware_paths=args.hardware_paths,
        reset_first=not args.no_reset,
        enable_info_return=not args.no_info_return,
        enable_copy_return=not args.no_copy_return,
        output_attenuation_linear=args.output_attenuation,
        awgn_enable=args.awgn_enable,
        write_phase=not args.no_phase,
        max_phase_paths=args.max_phase_paths,
        write_doppler=args.write_doppler,
    )

    # 先生成便于人工检查的 TDL 表，再用同一张表生成设备二进制帧。
    tdl_df = build_tdl_from_rt(data, tdl_config)
    frame = make_channel_control_frame(tdl_df, frame_config)
    _write_outputs(mat_file, output_dir, tdl_df, frame)

    print(f"Generated {len(tdl_df)} TDL paths")
    print(f"Control frame length: {len(frame)} bytes")
    print(f"Output directory: {output_dir.resolve()}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """集中定义 CLI 参数，默认值与协议层默认配置保持一致。"""
    parser = argparse.ArgumentParser(description="Build channel simulator control frame from RT MAT data.")
    parser.add_argument("mat_file", help="Input MAT file, for example frame_7.mat")
    parser.add_argument("--output-dir", default=".", help="Directory for CSV/BIN/HEX outputs")
    parser.add_argument("--input-id", type=int, default=0, help="Simulator input port id, 0..7")
    parser.add_argument("--output-id", type=int, default=0, help="Simulator output port id, 0..7")
    parser.add_argument("--max-paths", type=int, default=24, help="TDL paths selected from RT data")
    parser.add_argument("--hardware-paths", type=int, default=24, help="Hardware paths to clear/configure")
    parser.add_argument("--freq-index", type=int, default=0, help="RaysCTF frequency point index")
    parser.add_argument(
        "--power-mode",
        choices=["coherent", "noncoherent"],
        default="noncoherent",
        help="Power merge mode for rays in the same delay bin",
    )
    parser.add_argument("--use-rays-properties", action="store_true", help="Use E_RE/E_Im columns instead of RaysCTF")
    parser.add_argument("--output-attenuation", type=float, default=1.0, help="Output amplitude multiplier")
    parser.add_argument("--awgn-enable", action="store_true", help="Enable AWGN without setting AWGN power")
    parser.add_argument("--default-doppler-hz", type=float, default=0.0, help="Default Doppler per path")
    parser.add_argument("--write-doppler", action="store_true", help="Write per-path Doppler subframes")
    parser.add_argument("--max-phase-paths", type=int, default=24, help="Number of phase-capable paths")
    parser.add_argument("--no-phase", action="store_true", help="Do not write phase subframes")
    parser.add_argument("--no-reset", action="store_true", help="Do not prepend parameter reset")
    parser.add_argument("--no-info-return", action="store_true", help="Do not request one information frame")
    parser.add_argument("--no-copy-return", action="store_true", help="Do not request copy-frame echo")
    return parser


def _write_outputs(mat_file: Path, output_dir: Path, tdl_df, frame: bytes) -> None:
    """输出三类文件：TDL CSV、可下发 BIN、便于查看的 HEX 文本。"""
    base = mat_file.stem
    csv_file = output_dir / f"{base}_channel_simulator_tdl.csv"
    bin_file = output_dir / f"{base}_control_frame.bin"
    hex_file = output_dir / f"{base}_control_frame_hex.txt"

    tdl_df.to_csv(csv_file, index=False, encoding="utf-8-sig")
    bin_file.write_bytes(frame)
    hex_file.write_text(frame.hex(" ").upper(), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
