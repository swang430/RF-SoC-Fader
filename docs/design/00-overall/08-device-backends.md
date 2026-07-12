# 08 · 多设备后端抽象（总体设计）

> 第一册《总体设计》· 第 8 篇
> 状态：草稿 v0.1 · 待评审
> 前置：《03-architecture》（L2）

---

## 1. 概述

L2 用 **`DeviceBackend` 抽象**承载"多后端"：同一 canonical model 可渲染并下发到不同类型的信道仿真设备。本期两个实现：

- **`RFSoCBackend`**：协议 V3.0 二进制帧，over **TCP**。
- **`AscCirBackend`**：时变 CIR → `.asc` 文件，目标 = **RF-SoC 自身 CIR 回放模式**（已定，非 PropSim/Spirent）。

---

## 2. DeviceBackend 接口

```
class DeviceBackend(Protocol):
    def render(self, model: CanonicalChannelModel, addr: ChannelAddress) -> Artifact: ...
        # canonical → 设备原生产物（帧序列 / .asc）；纯函数，可 dry-run

    def apply(self, artifact: Artifact) -> ApplyResult: ...
        # 送达设备（TCP 下发 / 写文件）；含事务与确认

    def readback(self) -> Telemetry | None: ...
        # 读回遥测/回显（RFSoC 有；asc 无）

    capabilities: BackendCapabilities   # 支持 static/time_varying？相关档 A/B？端口数？
```
- **render/apply 分离**：render 纯产物生成（支撑 `dry_run` 与黄金对比测试），apply 才触达设备。
- **capabilities**：声明后端能力，L3 据此校验（如 asc 支持 time_varying，rfsoc 本期仅 static/B）。

---

## 3. RFSoCBackend（协议 V3.0 / TCP）

### 3.1 职责
- canonical model（static 或逐快照）→ 子帧序列 → 控制帧（复用/扩展 `commands.py` + `protocol.py`）。
- **TCP 传输**：连接管理、心跳、重连（找回重构丢失的下发能力，从串口升级为 TCP）。
- **事务化下发**：先 reset → 清 24 径 → 逐径写 → 复制帧比对 → 遥测确认；失败回滚（见《11》）。
- **分帧**：payload > 4000B 上限时拆多帧（64×24×多参数 + 1024B 瑞利系数）。
- **下行帧解码**：遥测（131B）、复制回显、错误帧、透传（交 L1 protocol 解码）。

### 3.2 渲染要点
- 全 35 参数 ID 编码（现仅 13/35）：补齐瑞利(5/6)、AWGN 功率(9)、扫频(17-20/23)、信号源(25-29)、拓扑(21)、扩展(16)、分数时延(34)、透传(30/35)等。
- IO 字节、path info、单位换算全部委托 L1（不就地写魔数）。
- **极化按测试模式渲染**（`test_mode`，见《03c》§5.3）：
  - **传导**：模型极化归约为 `xpr_db` 下发（硬件支持 XPR，单路即可）。
  - **OTA**：分极化分支映射为**独立信道**（占用栅格多端口，`port_map` 归 M4）。

### 3.3 时序（事务化下发）
```
render → connect(TCP) → [tx: RESET] → [tx: 清 24 径]
       → for tap in taps: [tx: enable/delay/amp/phase(/doppler)]
       → [tx: FREQ_PHASE_ZERO?] → 等复制帧回显 → 比对一致?
       → 请求信息帧 → 校验遥测(无溢出/电平合理) → commit / 否则 rollback
```

---

## 4. AscCirBackend（时变 CIR → .asc）

### 4.1 职责
- canonical model（`time.mode=time_varying` + CIR 载荷 `gain_series`/`cir_ref`（《03c》§5.2），或由 static taps 采样生成）→ `.asc` 文件。
- 承载 **A 档**数据（逐时刻相干 CIR），作为 **RF-SoC CIR 回放模式**的输入格式载体。

### 4.2 .asc 格式（据 ChannelEgine 样例）
```
***** Header *****
<N> CIRs                     # 时刻数
<T> Taps/CIR                 # 每时刻抽头数
<rate> CIR_Update_Rate       # 更新率(Hz)
<fc> Carrier_Frequency       # 载频(Hz)
***** Tap data *****
<delay> <re> <im>  <delay> <re> <im> ...   # 每行一个时刻，T 组抽头
```
- 按信道对 `In{i}_Out{j}` 命名（与 ChannelEgine 现有产物一致）。
- **无遥测/回显**（文件后端）：`readback()` 返回 None，`apply` 即写文件。

---

## 5. 多后端 × 多设备正交

- **后端类型**（rfsoc/asc）与**设备实例**（device_id）正交：`session` 绑定 `(device, backend)`。
- 同一 scenario 可开两个 session 产两种后端产物（G4：一份下 RF-SoC，一份导 `.asc`）。
- 新增后端（如第三方仿真器）= 加一个 `DeviceBackend` 实现，不动 L1/L3/L4。

---

## 6. 能力协商与校验

- L3 在下发前查 `backend.capabilities`：
  - scenario 为 time_varying 但 backend 仅 static → 报错或降级（明确提示）。
  - correlation.mode=A 但 backend=rfsoc 且硬件未确认 CIR 注入 → 守卫拒绝（《12》）。
- 保证「模型能力 > 设备能力」时不静默丢信息。

---

## 7. 开放问题
1. ~~`.asc` 目标设备~~ → **已定：RF-SoC CIR 回放模式**；仍需锁定该模式对 `.asc`/CIR 帧的确切接收字段（并入《12》#1 硬件确认）。
2. RFSoCBackend 事务边界与分帧策略归属 L2 还是 L3（与《11》协同）。
3. 时变模型下发到 RFSoC（逐快照流式）的可行性与节奏（依赖硬件，A 档）。

## 8. 本篇验收
- `DeviceBackend` 接口满足两后端；render/apply 分离支撑 dry-run。
- RFSoCBackend 找回 TCP 下发 + 全 ID 编码 + 事务确认。
- 多后端 × 多设备正交，新增后端不改其他层。
