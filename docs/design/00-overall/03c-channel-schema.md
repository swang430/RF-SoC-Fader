# 03c · 信道 Schema（canonical model 规范契约）

> 第一册《总体设计》· 第 3c 篇（枢纽契约的字段级规范）
> 状态：草稿 v0.1 · 待评审 · **规范性(normative)**
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
| `CHANNEL.CHANNEL_TYPE` | → | `Ray.type` | 0=LoS |
| （无相关/损伤/设备概念） | | 退化链下游产生 | 导入时为空 |

- **单向**（MPDB→schema，不回写）、**无损**（RT payload 完整装下每条射线）、**schema 是超集**（还表达 GCM/CDL/TDL/相关/损伤/设备栅格/时变）。
- **坐标约定**：schema 用**天顶角 θ**（与 MPDB、3GPP 一致）；仰角转换（`el=90°−θ`）**延后到需要它的消费者**（如 `.asc`/PropSim GCM 导出），不在导入处做。

---

## 3. 顶层结构（Model Envelope）

```
ChannelModel {
  schema_version : string          # 如 "1.0"
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
  arrays   : { tx: AntennaArray, rx: AntennaArray }  # 自包含，相关性所依
}

AntennaArray {
  n_elements  : int
  positions_m : float[n_elements][3]   # 阵元坐标(米，本地或世界)
  polarization : PolConfig             # 极化配置（含斜极化角）
  orientation?  : float[3]             # 阵列朝向（可选）
  port_map    : int[n_elements]        # ★ 阵元 ↔ 设备端口(0..7) 映射
}

PolConfig {
  # 每个极化端口的斜极化角 ζ（度，相对垂直）：
  #   0=垂直(V)  90=水平(H)  +45/−45=斜 45°双极化(基站常用)
  slant_deg : float[]        # 如 [+45,-45] 双斜极化 · [0,90] 交叉 V/H · [0] 单垂直
}
```
> `port_map`（阵元↔栅格端口）是 MIMO 保真的**重要设计**，规则细化归 M4（见《03b》§4、《12》#6）。schema 只规定它**在此承载**。

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
  type         : int                    # 0=LoS, >0=反射/绕射
}

Cluster {                               # GCM / CDL 层
  delay_s       : float
  power_linear  : float
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
  addr  : { device_id?: string, input: int(0..7), output: int(0..7) }
  taps  : Tap[]           # ≤24
}

Tap {
  delay_s      : float
  gain         : Complex          # 几何/确定性权重后的复增益（B 档核心产物）
  power_linear : float
  doppler_hz   : float
  rayleigh_spec? : RayleighSpec   # 衰落边缘统计（多普勒谱，ID6）
  angles?      : { aoa_az_deg, aoa_zen_deg, aod_az_deg, aod_zen_deg }  # 保留供核验/再退化
}

RayleighSpec {
  enabled          : bool
  doppler_spectrum : jakes | flat | custom
  max_doppler_hz   : float
  coeffs?          : Complex[256]   # 频域滤波系数（可由 spectrum+max_doppler 生成）
}
```
> `phase` 不单列：相位即 `gain` 的辐角（`arg(gain)`）；渲染到协议时再拆出 `phase_code`。

### 5.3 极化模型（含斜 45°）

- **信道极化耦合**存于 `PolMatrix`（V/H 场基的 2×2：VV/VH/HV/HH）——这是传播信道**本征**的极化响应（3GPP 风格），与天线无关。
- **天线极化**存于 `AntennaArray.polarization.slant_deg`（斜极化角 ζ）：`0=V`、`90=H`、**`±45=斜 45°双极化**（基站常用）。
- **逐端口增益**在退化/渲染时由「信道 `PolMatrix` **投影**到收发天线的 slant 基」得到：`h_port = e_rx(ζ_rx)ᵀ · PolMatrix · e_tx(ζ_tx)`，其中 `e(ζ)=[cosζ, sinζ]ᵀ`。斜 45° 即 `ζ=±45°`。
- 单极化场景：`slant_deg=[0]` 且 `gain` 用标量 `Complex`（PolMatrix 退化为 VV）。

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
- `time_varying`（A 档 / 移动性）：抽头增益随 `snapshots` 变化——`realization=CIR`，抽头增益为**随时刻的复序列**（`gain_series : Complex[n_snapshots]`），对应 `.asc` 的逐时刻 tap 数据。大体量可外置 blob 引用。

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

---

## 10. 序列化与版本

- **JSON** 为规范序列化；复数以 `{re, im}` 或 `[re, im]`（择一固定，M10 定）。
- `schema_version` 语义化；破坏性变更升主版本，对外 API 契约随之（《04》§7）。
- **大体量**（time_varying CIR、256 系数×多径×多信道）支持**外置引用**（blob 句柄），JSON 只存元数据 + 引用。
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
1. ~~极化深度~~ → **已定**：`PolMatrix`(V/H 场基) + 天线 `slant_deg`(含斜 **±45°**)，投影得逐端口增益（§5.3）；单极化用标量 `gain`。仍需确认**设备对极化/双端口的承载度**（硬件，与《05》《06》交叉极化项合并）。
2. ~~CDL/TDL 表格式~~ → **已定**：自定义 JSON 体现 3GPP 表（§5.4），`cdl_tdl_reader` 解析。
3. **复数序列化表示**（`{re,im}` vs `[re,im]` vs 字符串）——建议批量抽头用 `[re,im]`、顶层字段用对象；M10 定死。
4. **time_varying CIR 的外置存储**边界（多大走 blob；schema 同时支持内联与引用）——M10。
5. `port_map`（阵元↔端口）规则——归 M4（重要设计）。

## 13. 本篇验收
- schema 能**无损承载** MPDB 全部 RT 数据（§2 映射无丢失）。
- 四个 `level` 的 payload 形态明确、互斥、可被退化算子变换。
- 只含物理量、无码值/无源无设备专有概念（原则 1/2 达成）。
- 可序列化 + 版本化，作为第三方 API 契约基底。
