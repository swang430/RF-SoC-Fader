# T2-01 · M1 协议编解码（功能设计）

> 第二册《功能设计》· 第 1 篇（L1 层 · 最底层，无内部依赖）
> 状态：草稿 v0.1 · 待评审
> 依据：《T1-A1 协议综述》（导读）+ 协议 docx（**权威**）；现有 `channel_simulator/protocol.py`（13/35 ID 基线）
> 消费方：M2（组帧/下发/回显比对）、M8（遥测解析消费）、M10（黄金样本 fixture）

---

## 1. 概述与定位

M1 是 **L1 纯编解码层**：把「结构化参数/物理量」与「协议 V3.0 字节流」互转。**纯函数、无 I/O、无状态副作用**（流式解析器除外，其状态仅为解析缓冲）。

范围：
- **上行编码**：全 35 参数 ID 的子帧编码 + 控制帧封装（现有 13 → 补 22）。
- **下行解码**（全新）：遥测帧(131B)/复制回显/错误帧/串口透传 4 类帧的流式解析。
- **单位换算**：全部「物理量↔码值」定标的唯一定义处（补 7 项新换算）。

**非职责**（边界）：TCP 传输、帧预算切分、事务/重试（→M2）；遥测的业务消费（→M8）；schema/canonical model（M1 只见"参数值"，不见"信道模型"）。

---

## 2. 代码结构（对现有包的扩展）

```
channel_simulator/
├── protocol.py        # 保留并扩展：常量/ParamID/SubFrame/单位换算/上行编码
├── protocol_rx.py     # 新增：下行帧数据结构 + 流式解析器 + 回显比对
└── (tests/ 见 §8)
```
沿用现有风格：`from __future__ import annotations`、frozen dataclass、中文 docstring、越界抛 `ValueError`。

---

## 3. 上行编码：35 ID 编码规格表（规范核心）

> 列义：**值编码** = payload 字节布局（全小端）；**io/info** = 子帧第 3/4 字节语义；✅=现有已实现。
> 完整物理含义/单位见《T1-A1》§4；实现时逐条与 docx 核对。

| ID | 名称 | 值编码 | io | info | 码值范围/定标 | 状态 |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| 1 | 全局使能 | u8 | 0 | 0 | 0/1 | ✅ |
| 2 | 多径使能 | u8 | in/out | path | 0/1 | ✅ |
| 3 | 多径主时延 | i16 | in/out | path | 0–1050 × 1000/120ns | ✅ |
| 4 | 多径多普勒 | i32 | in/out | path | ±2²⁷ × 1/35.791394133Hz | ✅ |
| 5 | 瑞利使能 | u8 | in/out | path | 0/1 | ➕ |
| 6 | 瑞利谱系数 | **1024B**=256×(I:i16,Q:i16)，**长度字段写 0** | in/out | path | 谱形系数 | ➕（长度特例已有） |
| 7 | 多径幅值 | u16 | in/out | path | 0–65535 × 1/32768 | ✅ |
| 8 | AWGN 使能 | u8 | 0/out | 0 | 0/1 | ✅ |
| 9 | AWGN 功率 | u16 | 0/out | 0 | k=100–65535 × 1/4096 | ➕（换算已有） |
| 10 | 输出使能 | u8 | 0/out | 0 | 0/1 | ✅ |
| 11 | 输出幅值 | u16 | 0/out | 0 | 0–65535 × 1/16384 | ✅ |
| 12 | 输入时延(DDR) | i32 | in/0 | 0 | 0–72,000,000 × 1/120µs；>10µs 生效 | ➕ |
| 13 | 参数复位 | u8 | 0 | 0 | 执行(1) | ✅ |
| 14 | 信息帧回传 | u8 | 0 | 0 | 0/1/2/3（关/0.5s/1s/单次） | ✅ |
| 15 | 复制帧回传 | u8 | 0 | 0 | 0/1 | ✅ |
| 16 | 路径扩展 | u8 | in/out(对角) | 0 | 0/1；对角信道×8 径，消耗配对信道 | ➕ |
| 17 | 扫频终点 | i32 | in/out | path | ±2²⁷ × 1/35.791394133Hz | ➕ |
| 18 | 扫频速度 | i32 | in/out | path | −2¹⁷–2¹⁷−1 × 1/35.79/2¹⁵ Hz/µs | ➕ |
| 19 | 扫频模式 | u8 | in/out | path | 0 关/1 扫停/2 折返 | ➕ |
| 20 | 扫频重启 | u8 | in/out | path | 执行；**参数重启后生效** | ➕ |
| 21 | 输入拓扑 | u8 | 0 | 0 | 0:8×8 / 1:4×8 / 2:2×8 / 3:1×8 | ➕ |
| 22 | 硬件复位 | u8 | 0 | 0 | 执行 | ➕ |
| 23 | 扫频起点 | i32 | in/out | path | ±2²⁷ × 1/35.79/2¹⁵ Hz | ➕ |
| 24 | 支持时延输入数 | u8 | 0 | 0 | 1–8（默认 4） | ➕ |
| 25 | 信号源使能 | u8 | 0(端口7) | 0 | 0/1 | ➕ |
| 26 | 信号源频率 | i32 | 0 | 0 | ±2³¹ × 1/71.58278827Hz | ➕ |
| 27 | 脉冲使能 | u8 | 0 | 0 | 0/1 | ➕ |
| 28 | 脉宽 | u32 | 0 | 0 | 0–2³²−1 × 25/3ns | ➕ |
| 29 | 脉冲周期 | u32 | 0 | 0 | 同上；须 > 脉宽（编码时校验） | ➕ |
| 30 | 串口透传 RX(J3) | bytes(1–255) | 0 | 0 | RF 前端参数串 | ➕ |
| 31 | 信道主时延 | i16 | in/out | 0 | 0–6090 × 50/3ns | ➕ |
| 32 | 相偏 | u16 | in/out | path | 0–4095 = 2π | ✅ |
| 33 | 频偏相位归零 | u8 | 0 | 0 | 执行 | ✅ |
| 34 | 分数时延 | u8 | in/out | path | 0 无 / 1–9=0.5–4.5ns / 10=0.25ns | ➕ |
| 35 | 串口透传 TX(J4) | bytes(1–255) | 0 | 0 | RF 前端参数串 | ➕ |

### 3.1 新增单位换算（补进 protocol.py，唯一定义处）

```python
SWEEP_FINE_SCALE   = 35.791394133 * 2**15   # 扫频起点/速度的细分定标
SIGSRC_HZ_SCALE    = 71.58278827            # 信号源频率
PULSE_UNIT_NS      = 25.0 / 3.0             # 脉宽/周期
CH_MAIN_DELAY_UNIT_NS = 50.0 / 3.0          # 信道主时延（0..6090）
INPUT_DELAY_UNIT_US   = 1.0 / 120.0         # 输入时延（0..72e6；>10µs 生效）

def sweep_start_hz_to_code(hz) -> int        # clip ±2**27
def sweep_speed_to_code(hz_per_us) -> int    # clip ±(2**17-1)
def sigsrc_hz_to_code(hz) -> int             # clip ±2**31
def pulse_ns_to_code(ns) -> int              # u32
def channel_main_delay_ns_to_code(ns) -> int # 0..6090
def input_delay_us_to_code(us) -> int        # 0..72_000_000
def rayleigh_coeffs_to_bytes(coeffs: complex[256]) -> bytes   # I/Q 各 i16 LE，1024B
def fractional_delay_ns_to_code(ns) -> int   # {0, 0.25, 0.5..4.5} → {0,10,1..9}；非法值 ValueError
```

### 3.2 编码器族（组合子帧的便捷层，仍纯函数）

```python
def encode_sweep(io, path, start_hz, end_hz, speed_hz_per_us, mode) -> list[SubFrame]
    # 产出 ID23,17,18,19（顺序无关）+ 调用方显式追加 ID20 重启（双缓冲语义，见《T1-A1》§7-4）
def encode_rayleigh(io, path, coeffs_or_spec) -> list[SubFrame]   # ID5 使能 + ID6 系数
def encode_signal_source(freq_hz, pulse=None) -> list[SubFrame]   # ID25/26(/27/28/29)；校验 width<period
def encode_rf_passthrough(direction: RX|TX, payload: bytes) -> SubFrame  # ID30/35；1≤len≤255
```

---

## 4. 下行解码（protocol_rx.py，全新）

### 4.1 数据模型

```python
@dataclass(frozen=True)
class TelemetryFrame:            # 0xFDB18541，payload 固定 131B
    adc_overrange: int           # 1B 位图（bit i = 输入 i 过载）
    input_level:  tuple[int,...] # 8×u16，0–2048
    input_power:  tuple[int,...] # 8×u32，0–8388608
    combiner_overflow: int       # 1B 位图（按输出端）
    awgn_overflow:     int       # 1B 位图
    output_power_clean: tuple[int,...]  # 8×u32（无噪）
    output_power_noisy: tuple[int,...]  # 8×u32（含噪）
    output_level: tuple[int,...] # 8×u16，0–32768
    # 1+16+32+1+1+32+32+16 = 131B ✅；字段次序以 docx 为权威，实现时逐字段核对

@dataclass(frozen=True)
class CopyEchoFrame:   raw: bytes            # 0xFDB18569，帧体=原控制帧改头
@dataclass(frozen=True)
class ErrorFrame:      pass                  # 0xFDB185FF 01 AE86（7B 全帧，无字段）
@dataclass(frozen=True)
class SerialPassthroughFrame: payload: bytes # 0xFDB18550（设备→上位机单向）

DownlinkFrame = TelemetryFrame | CopyEchoFrame | ErrorFrame | SerialPassthroughFrame
```

### 4.2 流式解析器（TCP 字节流 → 帧序列）

TCP 无消息边界，必须增量解析；这是 M1 唯一有内部状态的组件（状态=缓冲区）。

```python
class DownlinkParser:
    """增量解析器：feed() 喂入任意大小分片，产出完整帧；容错重同步。"""
    _buf: bytearray

    def feed(self, data: bytes) -> list[DownlinkFrame]:
        self._buf += data
        frames = []
        while True:
            i = self._buf.find(b"\xFD\xB1\x85")          # ① 扫描帧头前缀
            if i < 0:                                     #    无完整前缀：不能整段清空——
                keep = tail_prefix_len(self._buf)         #    尾部可能是被分片切断的帧头（"FD"/"FD B1"）
                drop(len(self._buf) - keep)               #    只丢弃确定无用的部分，保留 ≤2B 尾巴等下一分片
                break
    # tail_prefix_len(buf)：buf 的最长后缀同时是 b"FD B1 85" 真前缀的长度（0/1/2）
            if i > 0: drop(i)                             #    前缀前垃圾→丢弃并计数
            if len(self._buf) < 4: break                  # ② 等第 4 字节（帧型）
            kind = self._buf[3]
            need = expected_len(kind, self._buf)          # ③ 按帧型定长/取长度字段
            #    0x41→4+2+131+2；0xFF→7；0x69→4+2+len+2；0x50→4+1+len+2
            if need is UNKNOWN_KIND: resync_from(i+1); continue   # 未知帧型→跳过前缀重同步
            if len(self._buf) < need: break               # ④ 数据不足→等下一分片
            frame, ok = decode(kind, self._buf[:need])    # ⑤ 校验帧尾 AE86 + 长度一致
            if not ok: resync_from(i+1); continue         #    尾错→重同步（协议无 CRC，防错位）
            frames.append(frame); consume(need)
        return frames
```

要点：
- **重同步策略**：任何校验失败只前进 1 字节再扫描（不整段丢弃），保证单字节错位可恢复。
- **复制回显长度**：0x69 帧体是变长控制帧镜像，长度取其内部 length 字段。
- 解析器不解释语义（如遥测越限）——那是 M8 的事。

### 4.3 回显比对

```python
def verify_copy_echo(sent_control_frame: bytes, echo: CopyEchoFrame) -> bool:
    # 期望：echo.raw == sent 且帧头第4字节 0x40→0x69，其余逐字节相同
    return echo.raw[:3]==sent[:3] and echo.raw[3]==0x69 and echo.raw[4:]==sent[4:]
```

---

## 5. 错误处理

- **编码侧**：范围/枚举越界一律 `ValueError` 带字段名与合法范围（沿用现有风格）；`width>=period`、`len(coeffs)!=256`、透传 payload 越界同此。
- **解码侧**：不抛异常（链路脏数据是常态）——非法字节走重同步并累计 `parser.stats`（丢弃字节数/坏帧数），供 M2 观测上报。
- **编码不做设备能力校验**（如拓扑与在用信道冲突）——那是 M2/M3 按 capabilities 的职责；M1 只保证"字节格式合法"。

---

## 6. 与现有代码的差量（实现清单）

| 项 | 动作 |
| :-- | :-- |
| `protocol.py` | ➕ §3.1 七组常量/换算；➕ §3.2 编码器族；ParamID 枚举已全，无需改 |
| `protocol_rx.py` | 🆕 §4 全部（数据模型 + DownlinkParser + verify_copy_echo） |
| `commands.py` | 不动（属 M2 范畴的组帧编排，其扩展在 T2-02 设计） |
| `tests/` | ➕ §8 测试设计落地；现 `test_protocol.py` 为回归基线不动 |

---

## 7. 时序（M1 在下发闭环中的位置，供 M2 引用）

```
M2: render→[M1 encode]→bytes →TCP→ 设备
设备 → TCP 分片 → [M1 DownlinkParser.feed]→ CopyEcho → [M1 verify_copy_echo] → M2 事务判定
                                        └→ Telemetry → M8 消费
```

---

## 8. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **黄金帧（编码）** | 35 ID 各≥1 条已知输入→期望 hex（docx 示例优先，如 `0x0300`=25ns）；ID6 的 1024B+长度 0 特例 | 字节逐一相等 |
| **边界值** | 每换算函数 min/max/越界（0/1050、±2²⁷、k=100、width<period、透传 1/255B、分数时延枚举） | 越界必 `ValueError`；边界值正确 |
| **往返** | 物理量→码值→物理量 误差 ≤ 半个量化步长 | 量化一致性 |
| **遥测解码** | 构造 131B fixture（含过载/溢出位）→ 字段逐一断言 | 与布局表一致 |
| **流式解析** | 同一帧序列按 1B/7B/整帧/跨帧任意切片喂入 → 产出帧集相同；**分片恰好切在帧头内部**（缓冲尾余 `FD`/`FD B1`）帧不得丢失；帧间插入垃圾字节/截断帧 → 重同步且 stats 正确 | **TCP 分片鲁棒性**（M2 依赖的关键性质） |
| **回显比对** | 正/负例（改 1 字节、改帧头、截尾） | 判定正确 |
| **回归基线** | 现 `tests/test_protocol.py` 全绿不动 | 不破坏既有字节格式 |

覆盖目标：M1 行覆盖 100%（纯函数层应达成）；黄金帧 fixture 存 `tests/fixtures/`（M10 统一管理前先本地）。

---

## 9. 开放问题
1. 遥测 131B 的**字段次序**以 docx 为权威——实现前逐字段核对（本文布局来自《T1-A1》盘点，字节总和已对齐 131）。
2. ID16 路径扩展、ID21 拓扑的 io 字节取值细节（对角信道表示）——实现时对 docx 核对。
3. ID12 输入时延的符号性（范围非负，暂按 i32 编码、编码侧限 0..72e6）。

## 10. 本篇验收
- 35 ID 编码规格表与 docx 逐条对照无出入；4 类下行帧可从 TCP 分片流中稳定解析。
- §8 测试全绿后，M2 可仅依赖本模块接口完成组帧/下发/回显闭环设计。
