# T2-08 · M8 遥测与校准（功能设计）

> 第二册《功能设计》· 第 8 篇（L3：遥测消费服务 + 数值校准域）
> 状态：**v1.0 · 已冻结**（2026-07-16，tag: design-t2-v1.0）· §2/§3/§4/§6/§7 已随《T1-12》N5（输入功率声明制）修订——遥测输入字段语义降级 + §3.6 输出功率计划（2026-07-19 PR）
> 依据：《T1-11 §2 可观测性 · §4 校准》《T1-12 风险台账 #2/N1/N2》（冻结基线）；T2-01 `TelemetryFrame`、T2-02 遥测分发与连接级节奏、T2-07 声明的消费接口（本篇为其规范）
> 消费方：M7（`/telemetry` 快照 + SSE 流）、M11 GUI ⑤遥测面板、M6（活性/告警联动展示）；依赖：M1（帧解析与单位换算，唯一定义）、M2（遥测回调分发）、M10（告警规则/校准表配置承载）

---

## 1. 概述与定位

M8 由两半组成，共享「数值域」职责、零设备写操作：

- **遥测服务（TelemetryService）**：消费 M2 分发的 `TelemetryFrame`（131B，M1 已解析为类型化字段）→ 归一为物理量快照 → 环形缓冲（事件流）→ 快照查询 / SSE 订阅 / 告警引擎——兑现 T2-07 §2 先行声明的 `get_snapshot` / `subscribe` 接口。
- **校准域（CalibrationService）**：纯数值计算——瑞利谱型功率归一化、bypass 衰减表、输入电平指引、输出溢出建议、输出功率计划（§3.6 功率参考链）、量化误差聚合。**只产出数值与建议，不触设备**（自动动作列开放问题，同 T2-06 §9-2 口径：本期只告警）。

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
- **★输入侧语义裁定（《T1-12》N5，2026-07-19）**：设备**无输入功率实测能力**——`input_level`/输入功率字段仅作**相对指示**（基线漂移监视、§2.2 越限告警的迟滞输入），ADC 过载位图为**硬信号**（布尔可信）；两者均**不构成输入功率测量**，绝对功率语义（输出功率/SNR）一律锚定场景声明 P_in（§3.6 功率参考链，T2-06 §2 `InputPowerDecl`）。
- **域检查**：字段超出协议域（如 level>2048）视为帧疑损坏——丢弃该帧并计数告警（不入缓冲，不崩溃）。

### 2.2 告警引擎

| 规则源 | 触发 | 说明 |
| :-- | :-- | :-- |
| `adc_overrange` 位图 | bit 置位 → `alarm(adc_overload, input=i)` | 位图逐位映射输入端 |
| `combiner_overflow` / `awgn_overflow` | 置位 → `alarm(overflow, output=o, kind)` | 溢出定位到输出端与成因 |
| 电平越限 | `input_level` 超配置阈值（迟滞带，**原码域**） | ★N5 语义修订：阈值以**原码**配置（M10 承载，运营者按外部测量/经验设定）——**不由 §3.3 的 dBm 建议自动换算**（码↔dBm 映射未标定 §8-1，dBm 阈比原码=单位错配、告警误导）；映射标定落地后方可提供 dBm 配置面。ADC 过载位图始终为硬信号（布尔，无标定依赖） |
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
    # 产出为**用户面指引**（GUI 提示/信号源设定参考，dBm 人读）——★N5 修订：**不**自动转 §2.2
    #   码域告警阈值（码↔dBm 映射未标定 §8-1，单位错配会产生误导告警；码域阈值由运营者
    #   在 M10 原码配置，标定落地后方接通 dBm→码换算）
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

### 3.6 输出功率计划（功率参考链——《T1-12》N5：输入功率声明制）

```python
# 链条：P_in（场景声明，T2-06 §2 InputPowerDecl）→ 信道模型损耗（TDL 归一化，已知）
#       → 输出衰减/逐径幅值码值（code↔dB 经校准）→ P_out = 计算值 ± 不确定度

@dataclass(frozen=True)
class OutputPowerEntry:                   # 逐输出口评估条目
    mode: Literal["absolute", "relative"] # absolute=该口全部贡献输入已声明；relative=含未声明贡献
    predicted_pout_dbm: float | None      # absolute 时给（合路公式见下）；relative → None
    relative_loss_db: Mapping[int, float | None]
                                          # 逐贡献输入口的相对损耗（model_loss−norm−g_out 链，声明无关、
                                          #   恒可算）；None=零功率信道（T2-05 §3 守卫）
    snr_db: float | None                  # absolute 且 AWGN 在场（ID8 开）时给；分母仅 ID9（见下）
    uncertainty_db: Mapping[str, float] | None
                                          # absolute 时给，分解键：declared（声明精度，按来源标注）/
                                          #   calibration（code↔dB 定标，HR-CAL-001）/ bypass（N2 插损）
    missing_ports: tuple[int, ...] = ()   # 未声明的贡献输入口（GUI 引导补声明，T2-11 §3④）
    uncalibrated: tuple[str, ...] = ()    # 校准缺口清单（如 "n2_bypass"）——现状评估遇未标定**不抛错**，
                                          #   该口降 relative 并在此标注（GUI 明示「绝对预测待 N2 标定」；
                                          #   显式绝对 target 请求才抛 UncalibratedError）
    assumptions: tuple[str, ...] = ()     # 预测口径假设清单——★"incoherent_sum"：该口 ≥2 声明贡献时必标
                                          #   （合路按非相干功率和，见公式注；相关信源实际可偏差数 dB——
                                          #   诚实标注而非拒算，GUI 随功率链明示）

@dataclass(frozen=True)
class PowerPlan:                          # §3.6 产物——「现状评估」与「目标规划」共用形；
                                          #   ★进 /v1 OpenAPI 响应 schema（GET /sessions/{id} 的
                                          #   power_plan 字段形，T2-07 §2——黄金 diff 盯住，防三方漂移）
    scope: Literal["device", "model_only"]
                                          # model_only=asc 产物（无输出级设置，只承诺模型损耗链）
    per_output: Mapping[int, OutputPowerEntry]       # 键=协议输出端编号（0–7）
    target_advice: Mapping[str, float] | None = None # 目标规划形态的设置建议（本期无 API 接线，
                                          #   随功率工作流升版启用——范围裁定见下）
def output_power_plan(p_in_dbm: Mapping[int, float] | None,      # 端口→声明均值功率（dBm）——★M8 原生
                                                                 #   入参，不引 M6 类型：`InputPowerDecl`
                                                                 #   （T2-06 §2）由 M6 适配（decl.port_dbm）
                                                                 #   后传入，依赖保持 M6→M8 单向、不成环
                      model_loss_db: Mapping[ChannelKey, float | None],  # M5 产物：逐信道**绝对**模型损耗
                                                                     #   （None=零功率信道，T2-05 §3 守卫——生产侧
                                                                     #   即产 None，签名必须收，0 W 跳过在本函数内；
                                                                     #   快照取自全部幅度变更后（含瑞利归一），不陈旧）——
                                                                     #   FidelityReport.channel_loss_db（Phase 4 归一化
                                                                     #   前记录，T2-05 §2/§3；shared_norm_gain_db 供
                                                                     #   实现域还原）——共享归一化不丢绝对刻度的保证
                      shared_norm_gain_db: float = 0.0,              # M5 Phase 4 共享基准偏移（直通模型=0）——
                                                                     #   rendered 设置作用在【归一化域】。绝对链逐贡献：
                                                                     #   C[i,o] = P_in[i] − model_loss[i,o] + shared_norm_gain
                                                                     #            + g_out_db[o] − bypass_atten_db(rendered.rf_mode)
                                                                     #   （★bypass 项=设备固有插损基线（N2 校准表 §3.2，
                                                                     #   RF/IF 分模式）——端口绝对功率必须扣除，否则整体
                                                                     #   高报 P_out/SNR。★未标定分形态处置：**现状评估
                                                                     #   （target=None）不抛**——受影响输出口降 mode=
                                                                     #   "relative" 并在 entry.uncalibrated 标注 "n2_bypass"
                                                                     #   （只读注解不得把会话卡死在 READY 前，T2-06 §4；
                                                                     #   当前硬件 N2 恰未标定，抛错=resolve 全废）；
                                                                     #   **显式绝对 target 请求**（目标规划形态）才抛
                                                                     #   UncalibratedError（§3.2 语义收窄到绝对请求路径）；
                                                                     #   ★ID11 是**线性输出幅值系数**
                                                                     #            ——码值/16384，T2-01「输出幅值」语义——折
                                                                     #            **有符号增益** g_out_db=20·log10(coef)：
                                                                     #            coef<1 衰减（负 dB）、coef>1 增益（正 dB）；
                                                                     #            不得当「非负衰减 dB」作减也不得无符号作加，
                                                                     #            系数↔dB 换算经 M1 唯一定义）
                                                                     #   ★输出口合路（8×8 多输入并入同输出）：
                                                                     #   P_out[o] = 10·log10(Σ_i 10^(C[i,o]/10))——loss=None
                                                                     #   （零功率信道，T2-05 §3 守卫）的贡献按 0 W 跳过。
                                                                     #   ★非相干假设（声明制范围裁定）：合路取**非相干功率
                                                                     #   和**——InputPowerDecl 不采集信源互相关；相关信源
                                                                     #   （同源 CW/相关 MIMO 流）含交叉项、实际 P_out 可偏差
                                                                     #   数 dB——≥2 贡献的口必标 entry.assumptions=
                                                                     #   "incoherent_sum"（GUI 明示假设，mode 不降）；先线性瓦
                                                                     #   求和再回 dBm，单对公式仅单贡献特例（逐 dB 直加会
                                                                     #   低报多输入输出口）；SNR 分子=该合路和、分母=**仅
                                                                     #   ID9（AWGN_POWER）**折 dBm（经 M1；ID8 使能只门控
                                                                     #   在场与否、不入分母；不受 ID11 幅值系数影响——
                                                                     #   协议注明其不作用于 AWGN）
                                                                     #   ★部分声明（P_in 未覆盖全部在用输入口）：不失败、
                                                                     #   也绝不把未声明贡献按 0 W 计入——**逐输出口**判定：
                                                                     #   全部贡献输入已声明 → 该口 absolute；含未声明贡献 →
                                                                     #   该口降 relative 并列 missing_ports（引导补声明）
                      p_in_source: Literal["user_declared", "external_meter"] | None = None,
                                                                 # 来源标注（不确定度 declared 分量的精度依据；
                                                                 #   None=未声明）——原生字面量，置 defaulted 区
                                                                 #   （必填参数之后——排序纪律同 T2-05 §2 前例）
                      rendered: RenderedPowerSettings | None = None,
                      target: TargetPoutDbm | TargetLossDb | TargetSnrDb | None = None) -> PowerPlan:
    # rendered=产物**输出级**功率设置摘要（输出幅值系数 ID11（线性，写域 1/16384；<1 衰减）——ID10 为
    #   输出使能、不入预算——与 AWGN 功率码，**及 rf_mode: Literal["rf","if"] | None**（会话设备的 bypass
    #   模式，来自设备配置/manifest 投影——公式 bypass 项的校准表选择键，§3.2 RF/IF 分表）。
    #   ★rf_mode=None＝**离线预览会话**（rfsoc `device_id=None`，T2-06 §2——无设备即无模式来源）：
    #   ID11/AWGN 码仍可投影（产物自足），bypass 项无从选表 → 受影响口降 relative + uncalibrated 标注
    #   "bypass_mode_unknown"（与 N2 未标定同型处置，**预览不失败**——S2「无设备完整预览」保持）；
    #   取自 M2 render 产物的 manifest 摘要投影：产物已含全部帧参数，纯提取不新增信息；写域码值折 dB
    #   调 M1 唯一定义）。★不含逐径幅值：径幅即归一化模型本体，已由 model_loss_db+shared_norm_gain_db
    #   承载——再计入 rendered 即双计（低于共享基准 10 dB 的信道会被错报成 −20 dB）
    # ★target=None →「现状评估」形态：rendered 对 **rfsoc 帧产物必传**（预测将下发产物的实际 P_out，
    #   不是无设置的裸模型）；**asc 产物（AscFileSet 无帧/输出级设置）rendered=None 合法** → plan 标注
    #   scope="model_only"（.asc 绝对功率由回放设备定标，平台只承诺模型损耗链）。
    #   ★absolute/relative 是**逐输出口**属性（`per_output[o].mode`，无全局 mode——混合声明时
    #   各口独立判定）：该口全部贡献输入已声明 → mode="absolute"（predicted_pout±不确定度）；
    #   含未声明贡献 → 该口 mode="relative"（仅相对损耗，不报错——评估无绝对请求）。
    #   M6 resolve 即以此形态计算并挂 ResolvedArtifacts.power_plan（T2-06 §2，经 GET /sessions/{id} 直出）
    # ★target 给定 →「目标规划」形态（rendered 可 None）：反解输出衰减/幅值设置建议以达 target
    # ★范围裁定（诚实边界）：目标规划形态本期为**内部纯函数能力**（形态先行保留，同通用平台原则）——
    #   /v1 合同**未**暴露 target 载荷（T2-07 仅暴露场景 input_power 声明 + 会话只读 power_plan）；
    #   前端接线（载荷落点候选：Scenario.power_target「配置即数据」或会话级 plan 端点）随 DP-01
    #   功率工作流条目落地，落定走升版——本期任何前端不产生 target 调用路径
    # 产出 PowerPlan：输出衰减建议（dB，写域码值换算调 M1 唯一定义）+ predicted_pout_dbm
    #   + uncertainty（分解可见：声明精度（按 source 标注）⊕ code↔dB 定标（HR-CAL-001，T3-03）
    #     ⊕ bypass/插损（N2））——不确定度来源诚实呈现，GUI 直译（T2-11 §3④）
    # ★p_in_dbm=None（未声明）→ 降级：仅受理 TargetLossDb（相对损耗）；
    #   TargetPoutDbm/TargetSnrDb → 抛 InputPowerUndeclared（指引到场景声明字段）
    # ★SNR 目标同锚：AWGN 功率是绝对码值域——**噪声功率只取 ID9（AWGN_POWER，1/4096）**，
    #   ID8（AWGN_ENABLE）仅门控噪声是否在场、**不入分母**（ID8 关 → 无噪声，SNR 语义不可算，
    #   不得把使能位/关断态当功率码折算）；信号功率取自声明链——
    #   无声明 P_in 时 SNR 语义不可用（同上错误）；不得以遥测 input_level 顶替（§2 语义裁定）
    # ★N2 未标定的 bypass 依赖处置**分形态**（与上文公式注一致）：**仅显式绝对 target 请求**抛
    #   UncalibratedError（§3.2 语义，绝不按默认估算）；现状评估（target=None）不抛——受影响口降
    #   relative + entry.uncalibrated 标注（resolve 不受只读注解阻塞）
    # 消费点：M6 resolve 管线（「现状评估」形态挂 ResolvedArtifacts.power_plan，经既有会话查询面
    #   GET /sessions/{id} 直出——不新增 REST 端点，T2-07 §2 响应行已列）；纯函数，不触设备
def channel_loss_db_of(model: CanonicalChannelModel) -> Mapping[ChannelKey, float | None]:
    # ★直通 TDL/CIR 的损耗数据源（reduce 不跑、无 FidelityReport 时 M6 调此回退，T2-06 §4）：
    #   逐信道 Σ|gain|²（TDL taps）/ 能量（CIR）折 dB——直通表幅度即模型意图，无归一化偏移；
    #   与 M5 归一化前记录（channel_loss_db）同口径，两路殊途同源
    #   ★零功率守卫与 M5 路径镜像（T2-05 §3）：空表/全零 taps 的信道能量 ≤0 → 返 None（不取
    #   log10、不产 −inf），output_power_plan 对 None 按 0 W 贡献跳过——两路守卫语义一致
```

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
CalibrationService.output_power_plan(p_in_dbm, model_loss_db, shared_norm_gain_db=0.0,  # p_in 为原生映射
                                     p_in_source=None, rendered=None, target=None) -> PowerPlan
                                                             # §3.6 功率参考链——
                                                             # 现状评估：rfsoc rendered 必传（输出级设置，
                                                             #   不含径幅）；asc=None→scope=model_only；
                                                             # 归一化偏移随行（经 M5 时必传，直通=0）；
                                                             # InputPowerUndeclared / UncalibratedError 语义
CalibrationService.channel_loss_db_of(model) -> Mapping[ChannelKey, float | None]  # §3.6 直通 TDL/CIR 回退源
                                                             # （None=零功率信道守卫，与 §3.6 签名一致）
```

> `input_level_advice` 本期仅作 **GUI/运营者指引文案**（dBm 人读，《T1-12》N1）——★N5 修订：**不**转化为 §2.2 告警阈值（码↔dBm 未标定 §8-1；码域阈值由运营者在 M10 **原码**配置，与指引无换算关系）；无直接 REST 用户面，若需只读端点随 S1 回路向 M7 回馈。

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
| 未标定 bypass 表访问 | `UncalibratedError`（显式上抛；错误指明待硬件数值项 N2）——**适用面：直接表访问（`bypass_atten_db`）与显式绝对 target 规划**；★§3.6 现状评估（target=None）**除外**：不抛、受影响口降 relative + `uncalibrated` 标注（resolve 不失败，READY/apply 保持可达） |
| 未声明输入功率 | 绝对 P_out/SNR 目标请求而场景无 `InputPowerDecl` → `InputPowerUndeclared`（指引到场景声明字段；相对损耗路径不受限——§3.6 降级面） |
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
| **功率计划** | 声明 P_in × 三类目标（P_out/损耗/SNR）矩阵；未声明降级分支；N2 未标定 × {现状评估, 显式 target} 双分支 | 衰减建议与 predicted_pout 自洽且不确定度分解齐全；未声明时绝对目标抛 `InputPowerUndeclared` 而相对损耗可用；N2 未标定：**现状评估（target=None）不抛**——受影响口降 relative + `uncalibrated` 标注（resolve 可达 READY）；**显式绝对 target** 才抛 `UncalibratedError`（不按默认估算） |
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
