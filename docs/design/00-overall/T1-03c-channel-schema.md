# 03c · 信道 Schema（canonical model 规范契约）

> 第一册《总体设计》· 第 3c 篇（枢纽契约的字段级规范）
> 状态：**v1.2**（2026-07-16 二次升版：MPDB 手册 v1.1/HyperRT v3.2.6 新增逐径 DOPPLER 列 → `Ray.doppler_hz?` 可选字段）· **规范性(normative)**——冻结基线 v1.0（tag: design-t1-v1.0）后按升版纪律走 PR 升版（v1.1：第二册评审打包四项——①完整 PortMap 一等化 ②阵列坐标系声明 ③簇初相 ④coeffs 外置复评；变更明细与迁移规则见 §10）
> 前置：《03-architecture》§4、《03b-model-hierarchy》；被 L1/L2/L3/L4、两上游源、两后端共同依赖

---

## 1. 概述与设计原则

**信道 schema** 是全系统的枢纽数据契约（canonical model 的字段级规范）。一切模型来源收敛到它，一切设备后端从它渲染。本篇是它的**规范定义**（字段/类型/单位/范围/序列化/版本）。

设计原则（硬约束）：

1. **只存物理量，不存码值**：delay 秒、gain 线性复数、doppler Hz、angle 度。协议码值（如 delay_code 0..1050）由后端**渲染时**计算，不进 schema —— 保证设备无关。
2. **源无关 / 设备无关**：不出现 MPDB、38.901、协议 ID 等来源/设备专有概念（它们在 L1 导入/ L2 渲染的边界处映射）。
3. **层级相关载荷**：`level` 决定 payload 形态——RT/GCM/CDL 按**链路/环境**索引（rays/clusters），TDL 按**设备信道对**索引（taps）。见《03b》。
4. **自包含、可复现**：模型携带其**阵列几何**与 **provenance**，脱离上下文也能被理解、复算、重放。
5. **相关性以"生成量"为主、"矩阵"为辅**：角度 + 阵列几何是相关性的**主来源**（可复算 R）；R 矩阵是**物化缓存**（供核验/GUI/后端）。
6. **可序列化 + 版本化**：JSON 为主，`schema_version` 作为第三方合同；大体量时变 CIR 可外置引用。

---

## 2. MPDB 与本 schema 的关系（源 ↔ 契约）

**MPDB 是外部 RT 层源格式；本 schema 是内部统一契约。导入把 MPDB 无损映射进 schema 的 RT 层，是"退化链的 RT 入口"，非 schema 本身。**

| MPDB 字段 | → | schema 字段 | 说明 |
| :-- | :-- | :-- | :-- |
| `LINK.TX/RX` | → | `Link.tx_index/rx_index` | 链路端点 |
| `LINK.TX_ANT_POSITION/RX_ANT_POSITION` | → | `Link.tx_pos_m/rx_pos_m` + `Meta.arrays` | 天线世界坐标(米) |
| `CHANNEL.LINK_ID` | → | `Link` 分组 | 外键 |
| `CHANNEL.DELAY[s]` | → | `Ray.delay_s` | **同单位，无损** |
| `CHANNEL.H[复]` | → | `Ray.gain` | 复增益 |
| `CHANNEL.AOA/ZOA` | → | `Ray.aoa_az_deg / aoa_zen_deg` | 到达角(方位+天顶) |
| `CHANNEL.AOD/ZOD` | → | `Ray.aod_az_deg / aod_zen_deg` | 离去角 |
| `CHANNEL.DOPPLER`（v3.2.6+） | → | `Ray.doppler_hz?`（v1.2） | 逐径多普勒（Hz），**同单位无损**；旧库无列 → 字段缺省 |
| `CHANNEL.CHANNEL_TYPE` | → | `Ray.type` | 0=LoS |
| （无相关/损伤/设备概念） | | 退化链下游产生 | 导入时为空 |

- **单向**（MPDB→schema，不回写）、**无损**（RT payload 完整装下每条射线）、**schema 是超集**（还表达 GCM/CDL/TDL/相关/损伤/设备栅格/时变）。
- **坐标约定**：schema 用**天顶角 θ**（与 MPDB、3GPP 一致）；仰角转换（`el=90°−θ`）**延后到需要它的消费者**（如 `.asc`/PropSim GCM 导出），不在导入处做。

---

## 3. 顶层结构（Model Envelope）

```
ChannelModel {
  schema_version : string          # 当前 "1.2"（编码端只写当前版；旧数据经迁移钩子链向前兼容读，§10）
  id             : string          # 模型唯一标识
  level          : RT | GCM | CDL | TDL          # 表示层级（《03b》）
  realization    : none | CIR                    # 正交时变实现
  meta           : Meta
  grid           : Grid
  time           : TimeAxis
  provenance     : Provenance
  environment?   : Environment     # level ∈ {RT,GCM,CDL}: 链路索引
  channels?      : Channel[]        # level = TDL: 设备信道对索引
  correlation?   : Correlation
  impairments?   : Impairments
}
```
- `environment` 与 `channels` **互斥**（由 `level` 决定用哪个）；退化算子把前者变换为后者。

---

## 4. Meta（元数据，含单位/坐标/阵列）

```
Meta {
  center_frequency_hz : float
  bandwidth_hz?       : float
  units    : { delay:"s", gain:"linear_complex", doppler:"Hz",
               angle:"deg", frequency:"Hz" }        # 固定约定，规范强制
  angle_convention : { azimuth:"phi_deg[0..360)",
                       zenith:"theta_deg[0=+Z, 90=水平]" }   # 天顶角
  arrays?  : { tx: AntennaArray, rx: AntennaArray }  # 自包含，相关性所依——★按 level 限定：
                                   #   level ∈ {RT,GCM,CDL} 必填（导向/身份解析/相关性所依）；
                                   #   level=TDL 可缺省（定表直通无阵列语义——引擎 TDL-x 产出仍自带）
  port_map? : PortMap              # ★v1.1（升级项①）：阵元↔设备端口的完整映射一等化——
                                   #   置于 Meta 级（跨 tx/rx 的成对语义 + link_mode 无法归属单侧阵列）；
                                   #   ★按 level 限定：level ∈ {RT,GCM,CDL} 必填（M5 退化消费面）；
                                   #   level=TDL 可缺省（信道映射已固化在 channels[].addr）。
                                   #   v1.0 的 AntennaArray.port_map(int[]) 废止（迁移见 §10）
}

PortMap {                          # v1.1 新增（结构定义与 T2-04 §3.1 同一；校验规则 V1–V7 仍归 M4）
  tx_element_to_input  : Map[ElementKey, int(0..7)]   # 发射阵元(或阵元×极化) → 输入端口
  rx_element_to_output : Map[ElementKey, int(0..7)]   # 接收阵元(或阵元×极化) → 输出端口
  link_mode : per_element_pair | single_reference     # LINK 表形态声明（单一来源，防双源漂移）
}
ElementKey = int | [int, PolBranch]   # 传导：阵元号；OTA 双极化：(阵元号, 极化支路)
PolBranch  = "V" | "H" | "+45" | "-45"   # ★字符串枚举（JSON 编码即该字符串）——与 PolConfig.slant_deg
                                         #   数值对应：V=0°、H=90°、±45=±45°；与 T2-03 NPZ pairs 的
                                         #   pol 整数枚举（0=无 1=V 2=H 3=+45 4=−45）的映射由 codec 定
# ★JSON 编码形态（元组键不能做 JSON 对象键——与复数 (re,im) 同属 §10 内部表示约定）：
#   两个 Map 序列化为**条目数组** [{element:int, pol?:string, port:int}, ...]，
#   按 (element, pol) 排序（规范化键序——参与 model_id 内容寻址哈希，T2-10 §3）；
#   内存表示 Map、JSON 表示条目表，由 model-json codec 定死（M10）

AntennaArray {
  n_elements  : int
  positions_m : float[n_elements][3]   # 阵元坐标(米)——所属坐标系由 frame 声明（v1.1）
  frame       : world | local          # ★v1.1（升级项②）：positions_m 的坐标系声明——
                                       #   缺省 world（v1.0 数据语义不变）
  origin_m?   : [x,y,z]                # ★v1.1（升级项②）：阵列参考点的世界系落位——world 时省略；
                                       #   frame=local 且缺省 = **无世界锚定**（自由浮动阵列：局部几何
                                       #   自足的消费——导向/相关计算——可用；position 身份解析等
                                       #   需要世界系的消费显式拒）；需要世界落位时必填
  polarization : PolConfig             # 极化配置（含斜极化角）
  orientation?  : float[3]             # 阵列朝向（可选；frame=local 时与 origin_m 共同构成变换）
}

PolConfig {
  # 每个极化端口的斜极化角 ζ（度，相对垂直）：
  #   0=垂直(V)  90=水平(H)  +45/−45=斜 45°双极化(基站常用)
  slant_deg : float[]        # 如 [+45,-45] 双斜极化 · [0,90] 交叉 V/H · [0] 单垂直
}
```
> `port_map`（阵元↔栅格端口）是 MIMO 保真的**重要设计**——v1.1 起为 **Meta 级一等字段**（完整结构如上），不再依赖 provenance 过渡载体；**校验规则（V1–V7）与身份解析仍归 M4**（见《03b》§4、《12》#6、T2-04 §3）。
> `frame/origin_m` 使「本地系阵列+自动变换」在 schema 层可承载——**实现范围不因此自动扩大**：position 身份解析当期实现仍先支持 world（T2-04 §3.1），本地系支持为实现期增强。

---

## 5. 层级相关载荷

### 5.1 Environment（level ∈ RT/GCM/CDL，链路索引）
```
Environment { links : Link[] }

Link {
  link_id  : int
  tx_index : int ; rx_index : int
  tx_pos_m : [x,y,z] ; rx_pos_m : [x,y,z]
  rays?     : Ray[]      # level = RT
  clusters? : Cluster[]  # level = GCM / CDL
}

Ray {                                   # RT 层
  delay_s      : float
  gain         : Complex | PolMatrix    # 复增益（标量单极化 / 2×2 极化矩阵）
  aoa_az_deg, aoa_zen_deg : float       # 到达角（方位/天顶）
  aod_az_deg, aod_zen_deg : float       # 离去角
  doppler_hz?  : float                  # ★v1.2：逐径多普勒（Hz）——HyperRT ≥3.2.6 的 DOPPLER 列直读
                                        #   （上游按 f_d=(f_c/c)(v_TX·k̂_TX−v_RX·k̂_RX) 解析计算，双端投影）；
                                        #   旧数据缺省。消费优先级（列>velocity 重算>默认 0）见《T1-05》§5
  type         : int                    # 0=LoS, >0=反射/绕射
}

Cluster {                               # GCM / CDL 层
  delay_s       : float
  power_linear  : float
  phase_rad?    : float                 # ★v1.1（升级项③）：簇初相（簇→伪径退化时作复增益辐角，
                                        #   T2-05 §3）——引擎产出按 seed 生成、转换时必带（确定性契约
                                        #   T2-03 §2）；定表 CDL 无相位来源可缺省（消费端按
                                        #   cluster_phase_seed 确定性兜底，T2-05 §3 优先级③）
  aoa_az_deg, aoa_zen_deg : float       # 簇中心角
  aod_az_deg, aod_zen_deg : float
  az_spread_deg?, zen_spread_deg? : float   # 角度扩展（ASA/ASD/ZSA/ZSD）
  xpr_db?       : float                 # 交叉极化比
  k_factor?     : float                 # 莱斯 K（LoS）
  rays?         : Ray[]                 # 簇内子径（可选，展开时）
}

PolMatrix { vv:Complex, vh:Complex, hv:Complex, hh:Complex }   # 极化 2×2（V/H 场基）
```

### 5.2 Channel（level = TDL，设备信道对索引）
```
Channel {
  addr    : { device_id?: string, input: int(0..7), output: int(0..7) }
  taps    : Tap[]           # ≤24（time.mode=static 或 time_varying 内联）
  cir_ref? : BlobRef        # time_varying 且 >10MB：外置 CIR 引用（替代内联 gain_series，见 §7/§10）
}

Tap {
  delay_s      : float
  gain?        : Complex          # static：几何/确定性权重后的复增益（B 档核心产物）
  gain_series? : Complex[n_snapshots]  # time_varying 内联：逐时刻增益（隶属 time.snapshots）
  # 约束：gain / gain_series / Channel.cir_ref 三者按 time.mode 恰取其一——
  #   static → 必填 gain；time_varying ≤10MB → 必填 gain_series；>10MB → Channel.cir_ref
  power_linear : float
  doppler_hz   : float
  xpr_db?      : float            # 交叉极化比（传导测试的极化参数，硬件消费）
  rayleigh_spec? : RayleighSpec   # 衰落边缘统计（多普勒谱，ID6）
  angles?      : { aoa_az_deg, aoa_zen_deg, aod_az_deg, aod_zen_deg }  # 保留供核验/再退化
}

RayleighSpec {
  enabled          : bool
  doppler_spectrum : jakes | flat | custom
  max_doppler_hz   : float
  coeffs?          : Complex[256]   # 频域滤波系数（可由 spectrum+max_doppler 生成）
}

BlobRef { uri: string, format: string, shape: int[], dtype: string }   # 外置 CIR 句柄
```
> `phase` 不单列：相位即 `gain` 的辐角（`arg(gain)`）；渲染到协议时再拆出 `phase_code`。
> **CIR 承载（time_varying）**：内联走 `Tap.gain_series`；序列化 >10 MB 时改用 `Channel.cir_ref`（外置 blob，`gain_series` 置空）。`gain`/`gain_series`/`cir_ref` 按 `time.mode` **恰取其一**（`gain` 对 static 必填、对 time_varying 不出现）。见 §7/§10。

### 5.3 极化模型（含斜 45°）

- **信道极化耦合**存于 `PolMatrix`（V/H 场基的 2×2：VV/VH/HV/HH）——这是传播信道**本征**的极化响应（3GPP 风格），与天线无关。
- **天线极化**存于 `AntennaArray.polarization.slant_deg`（斜极化角 ζ）：`0=V`、`90=H`、**`±45=斜 45°双极化**（基站常用）。
- **逐端口增益**在退化/渲染时由「信道 `PolMatrix` **投影**到收发天线的 slant 基」得到：`h_port = e_rx(ζ_rx)ᵀ · PolMatrix · e_tx(ζ_tx)`，其中 `e(ζ)=[cosζ, sinζ]ᵀ`。斜 45° 即 `ζ=±45°`。
- 单极化场景：`slant_deg=[0]` 且 `gain` 用标量 `Complex`（PolMatrix 退化为 VV）。

**极化的设备消费——按测试模式分叉（已定，硬件确认）**：

| 测试模式 | 设备如何处理极化 | schema 渲染 |
| :-- | :-- | :-- |
| **传导 (conducted)** | **只需 XPR 标量，硬件支持** | 模型极化（PolMatrix/slant）归约为 `xpr_db`（每径/每模型）下发；不需双端口 |
| **OTA** | 两个极化分支**分开独立处理** | 每个极化分支映射为设备栅格上的**独立信道**，各自渲染（`port_map` 为每极化分配独立端口，归 M4） |

- `PolMatrix`/`slant_deg` 是**模型层**的完整极化表示（源侧、设备无关）；`xpr_db` 是它在**传导渲染**时的归约产物。
- OTA 模式下不归约为 XPR，而是走多信道独立映射——这是 `port_map`/栅格设计（M4）要承载的一个维度。

### 5.4 3GPP CDL/TDL 表的 JSON 表示（level=CDL/TDL 入口）

**已定**：CDL/TDL 表输入采用**自定义 JSON 体现 3GPP 表**（不依赖外部格式），映射到本 schema 的 clusters（CDL）/ taps（TDL）。示例：

```jsonc
// CDL 表（如 CDL-A）→ level=CDL, Environment.links[].clusters[]
{
  "model": "CDL-A", "level": "CDL",
  "spreads_deg": { "c_asd": 5, "c_asa": 11, "c_zsd": 3, "c_zsa": 3 },
  "xpr_db": 10,
  "clusters": [
    // 归一化时延、功率(dB)、离去/到达 方位&天顶角(度)
    { "delay_norm": 0.0000, "power_db": -13.4, "aod_az": -178.1, "aoa_az": 51.3, "zod": 50.2, "zoa": 125.4 }
    // …
  ]
}

// TDL 表（如 TDL-A）→ level=TDL, channels[].taps[]（无角度，仅时延+功率+谱）
{
  "model": "TDL-A", "level": "TDL",
  "doppler_spectrum": "jakes",
  "taps": [ { "delay_norm": 0.0000, "power_db": -15.5 } /* … */ ]
}
```
- 归一化时延 × RMS 时延扩展 → `delay_s`；`power_db` → 线性功率；`spreads/xpr` 进 cluster 字段。
- 由 `cdl_tdl_reader`（L1）解析（格式细节 M10，与《11》§6 文件 I/O 呼应）。
- **注意**：3GPP CDL/TDL 表本身**全是实数**（时延/功率 dB/角度/XPR/角度扩展），**不含复数**——复数只出现在由这些参数 + 随机相位**算出的信道系数**里。故本 JSON 忠于 3GPP、无复数写法问题；复数序列化见 §10。

---

## 6. Correlation（相关性）

```
Correlation {
  source : angles_geometry | provided     # 主来源：角度+几何(可复算) / 直接给定
  r_tx?  : Complex[Mtx][Mtx]              # 发射端相关（物化，可选）
  r_rx?  : Complex[Mrx][Mrx]              # 接收端相关
  r?     : Complex[Mtx*Mrx][Mtx*Mrx]      # Kronecker 合成 R（物化缓存）
}
```
- **主来源是角度+几何**（存于 rays/taps.angles + Meta.arrays），R 可随时复算；`r*` 是缓存，供核验/GUI/后端，非真值来源。
- Kronecker 可分离近似；非可分离场景保留升级空间（《06》§7）。

---

## 7. TimeAxis 与 CIR（时变实现）

```
TimeAxis {
  mode          : static | time_varying
  snapshots?    : float[]    # 时刻(s)，time_varying 时
  update_rate_hz? : float
  duration_s?   : float
}
```
- `static`（B 档默认）：`channels[].taps[].gain` 为标量复数。
- `time_varying`（A 档 / 移动性）：抽头增益随 `snapshots` 变化——`realization=CIR`，抽头增益为**随时刻的复序列**（`gain_series : Complex[n_snapshots]`），对应 `.asc` 的逐时刻 tap 数据。
- **生灭表示（语义附注，《T1-15》D3）**：time_varying 下 tap 槽位固定（`gain_series` 定长），路径生灭以**增益包络**表达——birth 起零 ramp-up、death 归零后保持 0；斜坡由生成方负责（防频谱溅射），schema 不增字段。
- **内联 vs 外置（阈值 = 10 MB）**：序列化后 **≤10 MB 内联**进 JSON；**>10 MB 外置** blob（JSON 只存元数据 + 引用句柄）。见 §10。

---

## 8. Grid / Impairments / Provenance

```
Grid {
  topology       : "8x8" | "4x8" | "2x8" | "1x8"
  channel_pairs  : [ {input, output}, ... ]   # 有效信道对
}

Impairments {                     # 设备无关的物理损伤描述
  awgn?          : { enable: bool, snr_db?: float, power_linear?: float }
  output_atten_linear? : float
  sweep?         : { start_hz, end_hz, speed_hz_per_us, mode }
  signal_source? : { enable, frequency_hz, pulse?: {width_s, period_s} }
}

Provenance {
  source_type   : MPDB | ChannelEgine_38901 | CDL_table | TDL_table | manual
  source_ref?   : string          # 文件/场景标识（可追溯）
  import_config? : object         # 导入/退化配置（power_mode、max_paths、多普勒策略…）
  reduced_from? : string          # 若由更高层退化而来，指向上游模型 id（退化溯源）
  # 时间戳/操作者等由服务层填充
}
```

---

## 9. 字段速查（类型 / 单位 / 范围）

| 字段 | 类型 | 单位 | 范围/约束 |
| :-- | :-- | :-- | :-- |
| `center_frequency_hz` | float | Hz | >0 |
| `delay_s` | float | 秒 | ≥0（渲染时量化到 0..1050 码值） |
| `gain` | complex | 线性 | 有限 |
| `power_linear` | float | 线性 | ≥0 |
| `doppler_hz` | float | Hz | 渲染时映射 `Hz=code/35.79…` |
| `aoa/aod_az_deg` | float | 度 | [0,360) |
| `aoa/aod_zen_deg` | float | 度 | [0,180]（0=+Z 天顶） |
| `input/output` | int | — | 0..7 |
| `taps` 长度 | — | — | ≤24 |
| `max_doppler_hz` | float | Hz | ≥0 |
| `coeffs` | complex[256] | — | 频域滤波系数 |
| `phase_rad`（v1.1） | float | 弧度 | 簇初相（可选；缺省时消费端种子兜底） |
| `Ray.doppler_hz`（v1.2） | float | Hz | 可选；上游 DOPPLER 列直读，缺省=无上游值（消费 fallback 链《T1-05》§5） |
| `frame`（v1.1） | enum | — | world \| local（缺省 world） |
| `origin_m`（v1.1） | float[3] | 米 | world 省略；local 可缺省=无世界锚定（需世界落位的消费必填，§4） |
| `port_map` 映射值（v1.1） | int | — | 0..7（键=ElementKey；V1–V7 校验归 M4） |

---

## 10. 序列化与版本

- **JSON** 为规范序列化。**复数序列化分两层（3GPP 无此标准）**：
  - **内部表示**（schema 自身）：用**直角 (re, im)**——计算友好（射线相干叠加即直角加法，避免极坐标↔直角与相位卷绕）。`{re,im}` vs `[re,im]` 由 M10 定死（批量抽头倾向 `[re,im]` 省体积）。
  - **导出格式**（落到目标）：由各 codec 自定，非 schema 统一——`.asc` 实/虚**分列**、PropSim `a+bi` 字符串、RF-SoC 设备 **amp+phase 极坐标码值**。
- `schema_version` 语义化，**版本纪律（内部契约口径）**：canonical model **不直出对外 API**（《04》§7——对外是 /v1 REST 投影），全部消费者随平台同版部署，**不存在旧 reader 读新数据的场景**——因此兼容承诺是单向的**向前兼容读**（新读旧，经迁移钩子）；**主版本升级保留给「无法编写无损迁移钩子的语义重构」**，可钩子化的结构迁移（如本次 PortMap 字段搬移）走次版本。防御面：读到高于当前支持的版本 → `SchemaTooNew` 显式拒（T2-10 §6），不猜读。
- **v1.0 → v1.1 变更与迁移**（读侧钩子归 M10 model-json codec，T2-10 §2；编码端只写 v1.1）：
  - **①port_map 一等化**：迁移**优先整体采用** `provenance.import_config["portmap"]`（v1.0 的规范载体，M4/M3 均按约定写入）升为 `Meta.port_map`；载体缺失时由各 AntennaArray 的 `int[]` 投影重建**键映射**（单极化键——重建属**降级假定**，迁移结果显式标注），但 **`link_mode` 不可虚构**（旧 `int[]` 不编码链路形态；把 single_reference 模型错标 per_element_pair 会让 T2-05 `reduce_to_tdl` 跳过导向合成分支、只映射参考链路——静默降质）——按模型自身 `environment.links` 结构做**集合级判定**（仅数量相等不足信：一条重复对+一条缺失对也能凑齐计数——与 T2-04 §3.1 分模式核验同口径）：links 的 `(tx,rx)` 端点对**集合恰等于**两侧阵元索引的**笛卡尔积**（逐对恰一条、无重复无缺失）且对数 >1 → `per_element_pair`；恰一条链路、其端点是合法阵元对、且任一侧阵元数 >1 → `single_reference`；1×1 阵列（两形态简并等价）→ `per_element_pair`；集合核验不满足任一形态（重复对/缺失对/非法端点）→ **拒**（`SchemaMigrationAmbiguous`，宁拒不猜）；TDL 级无 links 表可判 → 不重建、字段缺省（port_map 该层级本就可选，且角度已塌缩、无导向消费）。v1.0 的 `AntennaArray.port_map` 字段废止。
  - **②frame/origin_m**：**按来源判定，不盲补**——`source_type=MPDB`：优先读 v1.0 `import_config["frame"]`（M4 的 ImportConfig 字段，随 asdict(cfg) 入 provenance——声明在场按其值迁移）；声明缺失时 `identity_by="position"` → `world`（该模式 v1.0 即强制世界系），`identity_by="index"` 且无声明 → **拒**（v1.0 本就允许 local 或 world，index 模式无从判定——SchemaMigrationAmbiguous）；`source_type=ChannelEgine_38901` → **`frame=local`**（引擎请求的 `req.arrays` 元素坐标是**阵列局部系**——T2-03 §2），`origin_m` 分场景：统计场景（UMa/UMi/RMa/InH，geometry 必填）逐端回填——`import_config["geometry"]["tx_pos_m"]` → `arrays.tx.origin_m`、`import_config["geometry"]["rx_pos_m"]` → `arrays.rx.origin_m`（键名即 T2-03 §2 GenerateRequest 原文）；**CDL-x/TDL-x（geometry=None）→ `origin_m` 缺省=无世界锚定**（局部几何自足，§4 语义——簇伪径导向只需相对阵元位置，本就无世界落位可回填）；`source_type=CDL_table/TDL_table` → 无 `arrays` 则无字段可迁（TDL 定表直通本就无阵列语义）；`arrays` 在场（CDL 定表）→ 同引擎口径 `frame=local`、`origin_m` 缺省无锚定（定表阵列仅承担局部导向，无世界锚定语义可回填）；来源无法判定（如 `manual`）→ **拒绝自动迁移**（`SchemaMigrationAmbiguous`，要求显式声明坐标系——猜错坐标系会让 position 身份解析与导向计算整体错位，宁拒不猜）。
  - **③Cluster.phase_rad**：从 `provenance.import_config["cluster_phases"]`（v1.0 过渡载体，T2-03 §3）按链路×簇序回填；载体缺失则字段缺省（消费端按 T2-05 §3 优先级③种子兜底）。v1.1 起引擎转换**直写本字段**，过渡载体不再写入（读侧对 v1.0 旧数据保留兼容）。
  - **④coeffs 外置（复评结论，不改字段）**：`RayleighSpec.coeffs` 超阈值外置**不增设一等引用字段**——由 model-json codec 序列化层以 `$blob` 信封原位替换实现（T2-10 §2/§4）；本条 10MB 规则的强制性由 codec 层兑现，schema 字段形态不变。
- **v1.1 → v1.2 变更与迁移**：新增 `Ray.doppler_hz?`（可选，只加不改）——迁移钩子**平凡**：v1.1 及更旧数据该字段缺省、**不回填**（上游 DOPPLER 列是本字段唯一来源，不从角度重建——平台重算属消费期 fallback 而非迁移语义）；编码端只写 v1.2。
- **大体量**（time_varying CIR、256 系数×多径×多信道）支持**外置引用**（blob 句柄），JSON 只存元数据 + 引用。**阈值 = 10 MB**（已定）：序列化 ≤10 MB 内联，>10 MB 外置 blob。blob 格式（.npy/二进制）由 M10 定。
- 可序列化 ⇒ 配置即数据、可版本化、可重放（《11》§5、文件 I/O《11》§6、格式细节 M10）。

---

## 11. 一个走查示例（MPDB → RT 实例 → 退化 → TDL 实例）

```
① 导入: MPDB → ChannelModel{ level:RT, environment.links[].rays[], meta.arrays, provenance:MPDB }
② reduce_RT_to_CDL: rays 聚簇 → level:CDL, links[].clusters[]
③ reduce_CDL_to_TDL: 施加 arrays + 导向矢量 → 角度塌缩为 R，逐信道对产 taps
   → level:TDL, channels[].taps[], correlation.r（物化）, provenance.reduced_from=①.id
④ 渲染: RFSoCBackend 读 channels/taps/impairments → 物理量→码值→协议帧
```

---

## 12. 开放问题（schema 待钉项）
1. ~~极化深度 / 设备承载度~~ → **已定（硬件确认）**：模型层用 `PolMatrix`(V/H 场基) + 天线 `slant_deg`(含斜 **±45°**)；**设备消费按测试模式分叉**——**传导只需 XPR 标量（硬件支持）**，**OTA 分极化独立信道**（§5.3）。OTA 的每极化端口映射归 M4。
2. ~~CDL/TDL 表格式~~ → **已定**：自定义 JSON 体现 3GPP 表（§5.4），`cdl_tdl_reader` 解析。
3. **复数序列化**（3GPP 无标准）——内部表示用直角 (re,im)（计算友好）；`{re,im}` vs `[re,im]` 由 M10 定；导出格式(.asc 分列 / PropSim `a+bi` / 设备 amp+phase)由各 codec 定，非 schema 统一（§10）。
4. ~~CIR 外置存储边界~~ → **已定：10 MB**（序列化 ≤10 MB 内联，>10 MB 外置 blob，§7/§10）；blob 格式归 M10。
5. ~~`port_map`（阵元↔端口）承载形态~~ → **已定（v1.1 升级项①）**：完整 PortMap 结构一等化于 `Meta.port_map`（§4）；校验规则（V1–V7）与身份解析仍归 M4（T2-04 §3）。
6. **time_varying 载荷的 tap 上限（升版议题，《T1-15》Q2 裁定 2-①）**：现 `taps ≤24` 无条件（§5.2/§9），但 24 是 RF-SoC 设备约束、文件后端无上限（`ASC_CAPS.max_paths=None`）——议题：time_varying 载荷的 tap 上限改由后端 capabilities 裁决（static 维持 ≤24）；放宽落地前，实现遇超 24 的 A 档模型显式拒绝（不静默截断）。
7. **参数轨道一等化（v1.2 观察项，《T1-15》D8）**：B+ 播放的慢尺度参数轨迹现以编排层 `PlaybackPlan` 承载（schema 不动）；若实测后需要模型内持久化，再评估 `Tap.delay_series/power_series/doppler_series`。

## 13. 本篇验收
- schema 能**无损承载** MPDB 全部 RT 数据（§2 映射无丢失）。
- 四个 `level` 的 payload 形态明确、互斥、可被退化算子变换。
- 只含物理量、无码值/无源无设备专有概念（原则 1/2 达成）。
- 可序列化 + 版本化，作为第三方 API 契约基底。
