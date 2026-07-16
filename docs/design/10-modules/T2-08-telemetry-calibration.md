# T2-08 · M8 遥测与校准（功能设计）

> 第二册《功能设计》· 第 8 篇（L3：遥测消费服务 + 数值校准域）
> 状态：**v1.0 · 已冻结**（2026-07-16，tag: design-t2-v1.0）
> 依据：《T1-11 §2 可观测性 · §4 校准》《T1-12 风险台账 #2/N1/N2》（冻结基线）；T2-01 `TelemetryFrame`、T2-02 遥测分发与连接级节奏、T2-07 声明的消费接口（本篇为其规范）
> 消费方：M7（`/telemetry` 快照 + SSE 流）、M11 GUI ⑤遥测面板、M6（活性/告警联动展示）；依赖：M1（帧解析与单位换算，唯一定义）、M2（遥测回调分发）、M10（告警规则/校准表配置承载）

---

## 1. 概述与定位

M8 由两半组成，共享「数值域」职责、零设备写操作：

- **遥测服务（TelemetryService）**：消费 M2 分发的 `TelemetryFrame`（131B，M1 已解析为类型化字段）→ 归一为物理量快照 → 环形缓冲（事件流）→ 快照查询 / SSE 订阅 / 告警引擎——兑现 T2-07 §2 先行声明的 `get_snapshot` / `subscribe` 接口。
- **校准域（CalibrationService）**：纯数值计算——瑞利谱型功率归一化、bypass 衰减表、输入电平指引、输出溢出建议、量化误差聚合。**只产出数值与建议，不触设备**（自动动作列开放问题，同 T2-06 §9-2 口径：本期只告警）。

**非职责**：字节解析（M1）、TCP 传输与遥测节奏（M2 连接级配置，见 §5）、码值↔物理量换算的**定义**（L1 唯一定义，M8 只调用）、会话编排（M6）、物理 RF 定标测量（《T1-01》本期范围外）。

---

## 2. 遥测服务（TelemetryService）

### 2.1 数据流与模型

```
M2 dispatcher ──TelemetryFrame──► normalize（M1 换算：码值→物理量，原码保留）
      │                                │
      │（apply 期的 0x03 单次遥测归 M2 事务──► TelemetrySnapshot{device_id, event_id, ts,
      │  捕获队列消费——见 §5；M8 只收      │    inputs[8]{level, power, adc_overrange},
      │  dispatcher 转发帧，容忍突发/乱序， │    outputs[8]{power_clean, power_noisy, level,
      │  event_id 由 M8 单调赋值）        │             combiner_overflow, awgn_overflow},
      ▼                                │    raw: TelemetryFrame, origin: periodic|apply_verify}
   活性监控（周期帧超时→alarm）          ▼
                                  环形缓冲（N 条，event_id 单调递增）──► get_snapshot / subscribe / 告警引擎
```

```python
class TelemetryService:
    def get_snapshot(self, device_id) -> TelemetrySnapshot | None      # 最近一帧（无数据=None，非报错）
    def subscribe(self, device_id, last_event_id=None) -> AsyncIterator[TelemetryEvent]
        # TelemetryEvent = snapshot | alarm | advice | heartbeat | resync（T2-07 SSE 语义的数据源；
        #   advice=校准域溢出建议 §3.4——与告警同流，单一订阅面）：
        # - last_event_id 在缓冲窗内 → 自该点续传；窗外/无效 → 首发 resync 事件（指示先取快照）再自当前续流
        # - heartbeat：无新帧时按固定间隔合成（下游连接保活；不入环形缓冲）
        # - 慢消费者：队列满 → 丢最旧 + 强制补发 resync（不阻塞采集路径，不静默丢失）
```

- **码值→物理量**：`input_level`(0–2048)/`output_level`(0–32768)/功率字段的标定映射**补充定义于 M1 单位换算层**（M8 调用不重定义，CLAUDE.md 约定）；docx 未明含义的字段先以原码透出 + `raw` 全保留（开放问题 §8-1），**不猜换算**。
- **域检查**：字段超出协议域（如 level>2048）视为帧疑损坏——丢弃该帧并计数告警（不入缓冲，不崩溃）。

### 2.2 告警引擎

| 规则源 | 触发 | 说明 |
| :-- | :-- | :-- |
| `adc_overrange` 位图 | bit 置位 → `alarm(adc_overload, input=i)` | 位图逐位映射输入端 |
| `combiner_overflow` / `awgn_overflow` | 置位 → `alarm(overflow, output=o, kind)` | 溢出定位到输出端与成因 |
| 电平越限 | `input_level` 超配置阈值（迟滞带） | 阈值来自 §3.3 输入电平指引，M10 配置承载 |
| 活性 | 周期遥测帧超时（> K×节奏周期） | `alarm(device_silent)`——T2-02 §5 活性信号的消费端 |

- **迟滞（hysteresis）**：越限告警配置进入/退出双阈值——抖动不产生告警风暴；同一告警持续期间不重复发（状态机：raised→cleared）。
- 告警作为 `TelemetryEvent(kind=alarm)` 入同一事件流（SSE 客户端单一订阅面）；**只告警不动作**。

---

## 3. 校准域（CalibrationService）

### 3.1 瑞利谱型功率归一化（《T1-12》#2，硬件已确认）

```python
def rayleigh_norm_gain(taps_coeffs: Sequence[complex[256]],
                       mode: Literal["per_tap","total"]) -> tuple[float, ...]:
    # 硬件行为：ID6 谱型改变 → 信道总功率随谱能量改变（2026-07-14 确认）
    # 入参=该信道【全部】启用瑞利的 Tap 的系数（total 模式必须跨 Tap 联合——单 Tap 入参无从归一）
    # 补偿（E(c)=Σ|c_k|²），返回与入参等长的增益序列：
    #   per_tap（默认）：gain_i = sqrt(E_ref / E(c_i))——逐 Tap 独立归一（各异）
    #   total：g = sqrt(E_ref_total / Σ_i E(c_i))，全部 Tap 共用同一 g（保持 Tap 间相对功率分布）
    # 消费点：★L3 退化管线（T2-05 apply_doppler_and_rayleigh 的「功率归一化 hook→M8」落点，
    #   M5 调用本【纯函数】）——归一化在入模前完成，交给 M2 的模型已含补偿后幅度；
    #   M2 纯渲染不回调 M8（否则 M2↔M8 循环依赖——M8 依赖 M2 仅遥测分发单向）。实测校核 E_ref 于实现期
```

### 3.2 bypass 衰减校准表（《T1-12》N2，结构先行）

```python
@dataclass(frozen=True)
class BypassTable:                      # RF 与 IF 模式衰减不同（硬件确认）——分别建表
    rf_atten_db: float | None           # 数值由硬件方提供（当前未标定 → None）
    if_atten_db: float | None
def bypass_atten_db(mode: Literal["rf","if"]) -> float:
    # ★未标定（None）→ 抛 UncalibratedError（显式，绝不静默按 0 处理——功率预算会整体偏移）
```

### 3.3 输入电平指引（《T1-12》N1，参数化设计）

```python
def input_level_advice(signal_papr_db: float = DEFAULT_PAPR_5G) -> LevelAdvice:
    # ADC 输入参考 −20 ～ −10 dBm（硬件确认）；建议均值功率 = 上限 − PAPR 余量：
    #   upper = −10 − signal_papr_db
    #   ★倒置守卫：PAPR > 10 dB 时 upper < −20（参考窗只有 10 dB，容不下该 PAPR）——
    #     不返回倒置区间：LevelAdvice(feasible=False, peak_safe_mean_dbm=upper,
    #       note="建议削峰/CFR；或按峰值约束取均值 ≤ upper 并接受低于参考下限的 SNR 损失")
    #   否则 recommended = (−20 dBm, upper)，feasible=True
    # DEFAULT_PAPR_5G 为保守占位（5G OFDM PAPR 调研未决，N1）——参数化：调研结论落地只改默认值
    # 产出同时给出告警阈值（§2.2 电平越限规则的数据源，经 M10 配置下发）
```

### 3.4 输出溢出建议（overflow guard——只建议不动作）

```python
def overflow_guard(snap: TelemetrySnapshot) -> list[Advice]:
    # combiner/awgn 溢出位 → 定位输出端与成因 → 建议：
    #   「输出 o 合路溢出：建议输出衰减增加 ≥X dB」
    #   ★X 全程在【遥测读数域】反推：X = 20·log10(output_level_o / target_level)，
    #     满幅参考=遥测 level 域上限 32768，target=满幅−安全余量——读数域自洽，
    #     不掺写域刻度；写侧落地时 X dB→ID11 衰减码值（1/16384 为【写域】步进）另行
    #     换算（调 M1 唯一定义）——两域不得混算
    # Advice 作为事件入遥测流（GUI 展示、用户决策）；自动降幅列开放问题（§8-4，与 M6 协同）
```

### 3.5 量化误差聚合

- 消费 M4/M5 的 `QuantReport`（时延格点丢弃）+ 幅度（1/32768）/相位（2π/4096）/多普勒格点的舍入统计 → 每模型「校准注记」（数值保真报告，随 FidelityReport 同面展示）。M8 **不重算**编码——聚合上游报告。

---

## 4. 接口汇总（按消费方分组）

```python
# —— 对 M7（REST 暴露面，T2-07 §2 声明消费的仅此两个）——
TelemetryService.get_snapshot(device_id) -> TelemetrySnapshot | None
TelemetryService.subscribe(device_id, last_event_id=None) -> AsyncIterator[TelemetryEvent]
# —— 对 M5/M6/M10（进程内纯函数与配置产出，无对应 REST 端点）——
CalibrationService.rayleigh_norm_gain(taps_coeffs, mode="per_tap") -> tuple[float, ...]
CalibrationService.bypass_atten_db(mode) -> float            # UncalibratedError 语义
CalibrationService.input_level_advice(papr_db=...) -> LevelAdvice
CalibrationService.overflow_guard(snapshot) -> list[Advice]
```

> `input_level_advice` 本期仅经 M10 配置转化为告警阈值与 GUI 提示文案（《T1-12》N1），无直接 REST 用户面；若需只读端点随 S1 回路向 M7 回馈。

---

## 5. 与 M2 的采集契约

- **遥测节奏是 M2 连接级配置**（0x01/0x02 周期档，连接建立即设、apply 后恢复——T2-02 §4）：M8 **不发任何控制帧**；节奏变更=部署配置项（经 M2 连接参数），非运行时 API。
- **apply 期 0x03 单次遥测归 M2 事务捕获队列**（T2-02 §4 VERIFYING 的 `_await_telemetry` 消费它做核验——M8 不得截流）；M2 核验完成后把该帧**转发** dispatcher 一份（`origin=apply_verify` 标注），M8 作普通快照入流——不转发则 M8 无感知。M8 容忍突发与间隔抖动，`event_id` 由 M8 单调赋值（与设备节奏解耦）。
- 活性监控以「配置节奏周期 × K」为超时基准（K 默认 3，M10 配置）；`device_silent` 告警清除条件=恢复收帧。

---

## 6. 错误处理

| 场景 | 处置 |
| :-- | :-- |
| 帧字段超协议域 | 丢弃 + 计数 +（超率阈值时）`frame_corrupt` 告警——不入缓冲不崩溃 |
| 未标定 bypass 表访问 | `UncalibratedError`（显式上抛；错误指明待硬件数值项 N2） |
| 订阅者慢消费 | 丢最旧 + 补 `resync`（采集路径永不被下游阻塞） |
| 设备静默 | `device_silent` 告警（活性）；`get_snapshot` 返回最近帧+陈旧时长标注 |
| 无任何遥测数据 | `get_snapshot` 返 None（区分「无数据」与「设备错误」）——HTTP 语义已由 T2-07 §2 裁决：已注册无数据→204、未注册→404 |

---

## 7. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **黄金快照** | 构造 131B fixture（含过载/溢出位，复用 M1 测试样本）→ snapshot | 字段逐一对应；位图逐位映射输入/输出端 |
| **域检查** | level=2049 等越域帧 | 丢弃+计数；缓冲无污染 |
| **告警迟滞** | 阈值上下抖动序列 | 单次 raised/cleared，无告警风暴 |
| **续传/重同步** | 窗内 last_event_id / 窗外 / 慢消费者 | 窗内无缝续传；窗外与慢消费均首发 resync |
| **活性** | 停喂帧 > K×周期 | `device_silent` 触发；恢复喂帧后清除 |
| **谱归一黄金** | Jakes 谱与平坦谱混合的多 Tap 系数组 | 增益与解析能量比一致（1e-12 容差）；per_tap 逐 Tap 各异、total 全同且保持 Tap 间相对功率 |
| **bypass 未标定** | None 表调用 | UncalibratedError 且指明 N2 |
| **电平指引** | PAPR 参数扫描（含 >10 dB） | 建议区间单调、不超 ADC 上限；PAPR>10 dB 返回 feasible=False（绝不产生倒置区间） |
| **溢出建议** | 构造合路溢出快照 | Advice 定位正确、建议衰减量 ≥ 反推下限 |

---

## 8. 开放问题

1. 遥测码值→物理量（dBm/dBFS）的精确标定映射——docx 未尽字段以原码透出，映射定义补入 M1（实现期与硬件对表）。
2. N1：5G OFDM PAPR 调研结论 → `DEFAULT_PAPR_5G` 落定（参数化已就位）。
3. N2：bypass RF/IF 衰减数值待硬件方提供（表结构已就位，未标定显式报错）。
4. 溢出自动降幅（advice→action）策略与安全边界——与 M6（T2-06 §9-2）协同，实现期定。
5. 多设备遥测聚合视图（P4，随《T1-10》）。

---

## 9. 与现有代码的差量

现 `channel_simulator` 无任何下行消费（原脚本只发不收）：M8 全新。M1 的 `TelemetryFrame` 解析（T2-01 §4）是其唯一上游数据契约。

---

## 10. 本篇验收

- 黄金快照/告警迟滞/续传重同步/活性测试全绿（stub dispatcher）。
- 谱归一黄金双模式通过；bypass 未标定路径显式报错。
- 与 M7 联调：SSE 断线重连（窗内/窗外）行为与 T2-07 §7 判据一致。
- HIL 冒烟：真机周期遥测 → 快照物理量合理、过载位人为触发可告警。
