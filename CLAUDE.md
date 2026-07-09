# CLAUDE.md

本文件为在此仓库中工作的 Claude Code 提供指引。

## 项目概览

RF-SoC-Fader 是一套**信道模拟器（Channel Simulator）控制帧生成工具**。核心流程：

```
RT 射线仿真结果 (.mat)  →  TDL 多径模型  →  设备控制帧 (二进制 / HEX)
```

即把光线追踪（RT）仿真得到的射线数据，量化合并成硬件可配置的抽头延迟线（TDL）模型，再按「信道模拟器3协议 V3.0」编码成可直接下发给设备的控制帧。

## 目录结构

- `信道模拟器3协议V3.0.docx` — 权威协议文档，是所有编码逻辑的来源（帧格式、参数 ID、单位换算）。
- `ChannelSimulationCode/` — 实际代码目录：
  - `channel_simulator/` — 重构后的分层 Python 包（**当前主线，改动写这里**）。
    - `protocol.py` — 协议 V3.0 底层编码：帧头/帧尾、`ParamID` 枚举、`SubFrame` 子帧、单位换算函数（ns↔码值、幅度、相位、多普勒）。
    - `tdl.py` — 读取 RT/MAT 数据 → 按硬件时延分辨率量化合并射线 → 选出最强 N 条径 → 生成 TDL DataFrame。
    - `commands.py` — 把 TDL 表组装成完整控制帧（复位、使能、逐径写延迟/幅度/相位/多普勒）。
    - `cli.py` — 命令行入口，生成 `csv/bin/hex` 三类输出。
  - `Code` — **原始单文件脚本**（672 行，遗留参考）。包已经把它拆成上述四层；除非做历史对照，否则不要在此文件上开发。
  - `tests/test_protocol.py` — 协议层与控制帧的 unittest 回归测试。
  - `README.md` — 设计说明与关键换算常量。

## 运行与测试

代码在 `ChannelSimulationCode/` 目录下运行（包名为 `channel_simulator`）。

```bash
cd ChannelSimulationCode

# 生成控制帧（示例）
python -m channel_simulator.cli frame_7.mat --input-id 0 --output-id 0 --max-paths 24

# 运行测试
python -m unittest discover tests
```

依赖：`numpy`、`pandas`、`scipy`（`.vscode/settings.json` 指向 conda 环境）。当前系统默认 `python3` 未装这些依赖，运行前需激活正确的 conda 环境。

## 架构约定

- **分层单向依赖**：`cli` → (`commands`, `tdl`) → `protocol`。`protocol` 不依赖上层，是纯编码层。
- **算法配置用 dataclass 承载**：`TdlBuildConfig`（RT→TDL）和 `ChannelFrameConfig`（TDL→帧）。CLI 参数只负责表达用户意图，再映射到这两个 dataclass；新增可调项时优先加到对应 dataclass 而非散落在 CLI。
- **数据契约是 pandas DataFrame**：`build_tdl_from_rt` 产出的 DataFrame（含 `hw_path_index`、`delay_code`、`amp_code`、`phase_code`、`doppler_code` 等列）是 `tdl` 与 `commands` 之间的接口。改列名需同步两侧及测试。
- **所有数值编码集中在 `protocol.py`**：延迟/幅度/相位/多普勒的物理量↔码值换算只在此定义，其他模块调用，不要就地写魔数。

## 协议关键常量（改动前务必核对 docx 文档）

- 帧结构：`FRAME_HEADER(4B) + payload_length_le16 + subframes + FRAME_TAIL(2B)`；长度字段**只统计子帧 payload**，不含头/长度/尾。
- 子帧：`param_id + length(1B) + io + info + payload`。`io` 高 4 bit=输入端、低 4 bit=输出端；`info` 通常是多径编号（0 对应「多径1」）。
- 长度字段特例：瑞利频域滤波系数（`PATH_RAYLEIGH_FILTER`）长度字段写 0，实际跟 1024 字节。
- 设备当前确认**单信道 24 条多径**，且 24 条全部支持相偏；`MAX_CHANNEL_PATHS = DEFAULT_CONFIGURED_PATHS = PHASE_SUPPORTED_PATHS = 24`。
- 单位换算：主时延 `1000/120 ns`（码值范围 0..1050）；多径幅值 `1/32768`；输出端幅值 `1/16384`；AWGN 功率 `1/4096`；相位一圈 2π=4096 刻度；多普勒 `Hz = code / 35.791394133`。

## 编码风格

- 全中文 docstring 与注释，注释解释「为什么」（协议约束、设备约定），与现有风格保持一致。
- 使用 `from __future__ import annotations` + 类型注解；`@dataclass(frozen=True)` 表示不可变配置/数据。
- 边界检查抛 `ValueError` 并带明确信息（见 `io_byte`、`_validate_config`、`_subframe_length_field`）。
- 改动协议编码或换算逻辑后，务必运行 `tests/test_protocol.py` 确认字节格式未被破坏。
