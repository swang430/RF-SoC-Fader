# 06 · MIMO 空间相关性（总体设计）

> 第一册《总体设计》· 第 6 篇（旗舰链路下半段 · 技术核心）
> 状态：**v1.0 · 已冻结**（2026-07-15，tag: design-t1-v1.0）
> 前置：《05-rt-mpdb-to-tdl》；风险汇总《12-risks-open-items》

---

## 1. 概述与结论先行

**硬约束（协议事实）**：协议 V3.0 **无任何空间相关/Kronecker 字段**；瑞利衰落**逐信道逐径独立**；ID6 频域滤波系数**仅定义多普勒谱形**，无法注入指定的相干时变实现。

**因此分两档交付**（已与用户定档）：

| 档 | 含义 | 可行性 | 本期 |
| :--- | :--- | :--- | :--- |
| **B（统计等效）** | 几何/确定性空间相关**精确复现** + 衰落**边缘统计**匹配 | 协议内可行 | ✅ 交付基线 |
| **A（真实瞬时相关）** | 逐时刻相干的时变 CIR 注入，还原**瞬时**空间相关 | 需硬件支持 CIR 注入 | 🔒 预留接口 |

设计把「相关矩阵 → 设备参数」做成**可插拔策略 `CorrelationSynthesizer`**：B 为默认实现，A 为预留实现；`.asc` 后端天然承载 A 数据。

> 层级视角（《03b》）：**B 档 = 退化到 TDL + 相关矩阵 R（准静态，参数化 TDL 面）；A 档 = 停在 CIR 实现（时变，CIR 回放面）**。二者是同一条退化链的两个终点。本篇 §2/§3 即 `reduce_CDL_to_TDL` 的核心（角度塌缩为 R）。

---

## 2. 从 RT 角度到相关矩阵（B 档核心算法）

MPDB 提供每条径的**离去角（AoD/ZoD）**、**到达角（AoA/ZoA）**、**复增益 H**，加上**天线阵列几何**，即可算出真实的空间相关矩阵——这是 `.mat` 做不到、MPDB 独有的能力。

### 2.1 导向矢量（steering vector）
对含 `M` 个阵元、位置 `{p_m}` 的阵列，来波方向单位矢量 `k̂(φ,θ)`：

```
a_m(φ,θ) = exp( j · (2π/λ) · p_m · k̂(φ,θ) )        # 第 m 个阵元相位
a(φ,θ)   = [a_1, ..., a_M]ᵀ                          # 导向矢量
```
（角度用方位角 φ 与**天顶角 θ**（canonical 约定，见《03c》）；`k̂` 由 φ、θ 直接合成，无需转仰角。）

### 2.2 单端相关矩阵（发/收各一）
按各径功率对导向矢量外积加权求和：

```
R_tx = Σ_k  P_k · a_tx(φ_dep,k, el_dep,k) · a_tx(...)ᴴ      # 发射端 M_tx×M_tx
R_rx = Σ_k  P_k · a_rx(φ_arr,k, el_arr,k) · a_rx(...)ᴴ      # 接收端 M_rx×M_rx
归一化：R_tx /= trace(R_tx)/M_tx    （对角线归一）
```

### 2.3 Kronecker 合成
```
R = R_tx ⊗ R_rx                     # (M_tx·M_rx) × (同) 全相关矩阵
```
> Kronecker 是可分离近似；若需非可分离（联合角度）相关，保留 `corr` 元数据在 canonical model，后续可换全相关实现。策略接口不变。

---

## 3. B 档如何映射到设备（关键：可行性的诚实边界）

设备只有「逐信道对、逐径的确定性参数（幅度 amp + 相位 phase + 时延 + 多普勒谱）」和「独立瑞利」。B 档据此分两部分：

### 3.1 确定性/几何分量 —— **精确复现** ✅
- RT 快照本质是**确定性**的：每条径有确定的复增益 H、确定的到达/离去角。
- 把每个天线对 (tx_m, rx_n) 的信道，按导向矢量相位 + 径复增益，映射为该信道对各径的 **amp_code + phase_code**。
- 这样 64 信道栅格承载的**静态 MIMO 信道矩阵 H_MIMO** 的相关结构与 `R` 一致——**对 RT 确定性回放，这一部分就是"真值"**，不是近似。

```
for (tx_m, rx_n) in antenna_pairs:                # 映射到栅格 (input,output)
    for tap_k in taps:
        h_mn_k = H_k · a_tx,m(θ_dep,k) · conj(a_rx,n(θ_arr,k))   # 该阵元对该径复增益
        set_channel_tap(input=map(tx_m), output=map(rx_n), path=k,
                        amp = |h_mn_k|,  phase = arg(h_mn_k),
                        delay = tap_k.delay)
```

### 3.2 随机衰落分量 —— **边缘统计匹配**，瞬时相关不还原 ⚠️
- 若叠加移动性/瑞利衰落，硬件各信道**独立**生成 → 跨信道的**瞬时**衰落相关无法还原。
- B 档保证的是各支路**边缘统计正确**：功率时延谱（PDP）经 amp/delay、多普勒谱经 ID6 谱系数，逐信道配置匹配目标。
- 即：**"平均信道"的空间相关正确 + 每支路边缘分布正确**，但不保证两支路衰落**同一时刻**的相干。

> **一句话诚实结论**：对 RT 确定性快照（静止/准静态），B 档空间相关是**高保真**的（几何分量即真值）；一旦引入独立衰落的时变过程，B 档退化为"统计等效"。这正是 A 档存在的理由。

---

## 4. A 档（通用能力，接口保留）：时变相干矩阵输入

**设计方向**：A 档按**时变相干（相关）矩阵输入**设计，是平台作为**通用 fader 工具**的保留能力（《12》§0）。

- **硬件结论（2026-07-14）**：**当前 RF-SoC 不支持 CIR 注入**（无 CIR 回放模式/注入帧，《12》#1）。→ `RFSoCBackend.capabilities` 声明不支持 CIR；守卫拒绝 `correlation.mode=A` 下发到当前设备。**本期旗舰承诺锁定 B 档。**
- **接口全部保留、暂不对当前设备实现**：canonical model 的 `time_varying`+CIR 载荷（`gain_series`/`cir_ref`，《03c》§5.2）、`ATimeVaryingSynthesizer`、AscCirBackend 设计不变。
- **数据已就绪**：ChannelEgine 已能产出带 38.901 空间相关的**逐时刻时变 CIR**（`.asc`），即 A 档所需的相干时变矩阵序列。
- **A 档现实出口**：AscCirBackend 渲染 `.asc`（通用 CIR 交换格式）——离线分析、第三方 fader 回放、未来支持 CIR 的设备/固件（届时按 capabilities 启用）。
- **固件演进信号**：硬件方表示固件升级后「相关矩阵可拆解为每链路复系数」在设备内生效——与本篇 B 档软件塌缩同构；未来可经 capabilities 声明「设备侧拆解」，与软件塌缩二选一。

---

## 5. 可插拔策略接口

```
class CorrelationSynthesizer(Protocol):
    def apply(self, model: CanonicalChannelModel, array: ArrayGeometry) -> None: ...

class BStatisticalSynthesizer:      # 默认：§2/§3
    # 角度+几何 → R_tx,R_rx,R；确定性权重写入 taps.amp/phase；
    # 衰落边缘统计（PDP + 多普勒谱）写入 rayleigh_spec；R 存入 model.correlation
    ...

class ATimeVaryingSynthesizer:      # 预留：§4
    # 消费/生成 time_varying + CIR 载荷(gain_series/cir_ref)；交 AscCirBackend 或 RF-SoC CIR 帧
    ...
```
- 选择由场景配置 `correlation.mode = B|A` 决定；A 在硬件确认前不启用（守卫报错，见《12》）。
- `model.correlation` 始终保留 R（即使 B 档），供 GUI 可视化与校验。

---

## 6. 阵列映射（天线对 ↔ 8×8 栅格）

- 输入端口（0–7）↔ 发射阵元 / 输出端口（0–7）↔ 接收阵元（或反之，按产品定义）。
- `map_links_to_channel_pairs`（《05》§3）与本篇 §3.1 的 `map(tx_m)/map(rx_n)` 共用同一映射表；由阵列几何 + 栅格拓扑（8×8 / 4×8 ...）确定。
- 小规模 MIMO（如 2×2/4×4）占用栅格子集；拓扑切换（ID21）与路径扩展（ID16）在此消费。

---

## 6bis. B 方案端到端完整流程（首实现基线）

`time.mode = static`（准静态）。目标：一条 MPDB 链路 → 8×8 栅格 MIMO（每信道 ≤24 径），几何空间相关精确复现、衰落边缘统计匹配、下发 RF-SoC。

| Phase | 层 | 动作 | 要点 |
| :-- | :-- | :-- | :-- |
| 0 输入 | — | MPDB(HyperRT 直连) + 阵列几何 {p_tx,m}/{p_rx,n}+端口映射 + 配置(fc→λ, max_paths≤24, power_mode, 可选速度, mode=B) | 端口映射见 §6（归 M4） |
| 1 归一 | L1 | `delay_ns=DELAY×1e9`；角度保留**天顶角 θ**+方位角 φ(《03c》)；`λ=c/fc` | 唯一换算处；仰角转换延后到 .asc 导出 |
| 2 导向相位 | L3 | 逐阵元对 (m,n) 逐径 k：`h_{mn,k}=H_k·exp(j2π/λ·p_tx,m·k̂_dep,k)·exp(−j2π/λ·p_rx,n·k̂_arr,k)` | **★先导向、后合并**：顺序反了丢角度、破坏相关 |
| 3 TDL | L3 | 逐信道对：`delay_code=round(delay_ns/(1000/120))`裁剪 0..1050；同 bin 复增益相干叠加 `Σh_{mn,k}`；选最强 ≤24 径按时延排序 | 复用 `tdl.py` 量化—合并—选径 |
| 4 归一化 | L3 | 用**整个 MIMO 系统的共享参考**（如最强信道对最强径）归一：`amp=|h|/ref`、`phase=arg(h)` | **★共享基准**：现 tdl.py 逐信道自归一，B 必须改共享基准，否则信道间相对功率被抹平、相关失真 |
| 5 相关+损伤 | L3 | `R_tx=Σ P_k a_tx a_txᴴ`，`R_rx=Σ P_k a_rx a_rxᴴ`，`R=R_tx⊗R_rx`（存 correlation，供 §8 核验/GUI）；多普勒可选 `f_d=(v·k̂_arr)/λ`（默认 0）；可选瑞利谱形（ID6，边缘统计） | 瑞利跨支路瞬时相关不还原（诚实边界）；按《12》#2 补偿功率 |
| 6 装配 | L3 | canonical：`time.static`、grid、`channels[(in,out)].taps[]={delay_code,amp_code,phase_code,doppler?,rayleigh_spec?}`、`correlation=R` | — |
| 7 渲染下发 | L2/L1 | RESET→回传→GLOBAL/OUTPUT_ENABLE→OUTPUT_ATTEN→AWGN→清 24 径→逐径 ENABLE/DELAY/ATTEN/PHASE(/DOPPLER)(/瑞利)；分帧≤4000B；TCP→复制帧比对→遥测→commit/rollback | 分帧多帧语义《12》#5 |
| 8 校验 | — | dry-run 黄金对比；由已配 amp/phase 反构 `Ĥ_MIMO`→算 `R̂`→对比 R；遥测无 ADC 过载/合路溢出 | 相关保真度量 |

**三条不可违背的正确性要点**（传导到 M4/M5 实现）：
1. **先施加导向相位、再做时延合并**（Phase 2 先于 Phase 3）。
2. **全系统共享归一化基准**（Phase 4），非逐信道自归一——否则信道间相对功率失真、相关被抹平。
3. **诚实边界**：确定性/几何分量对 RT 快照是真值；叠加独立瑞利后，跨支路**瞬时**相关退化为统计等效。

---

## 7. 开放问题（头号风险，详见《12》）
1. **A 档硬门槛**：RF-SoC CIR 回放模式的**注入帧/接口是否存在**（方向已定为 RF-SoC CIR 模式 + `.asc` 预留，见 §4、《12》#1，**待硬件确认**）。
2. **ID6 谱系数精确语义**：256 复数 ↔ 多普勒谱/最大多普勒的映射；瑞利功率下降**机理已明**、确切标定值待硬件（《12》#2）。
3. **阵列↔栅格映射**：重要设计，归 M4 完善（§6、《12》#6）。
4. **Kronecker vs 全相关**：可分离近似是否满足精度要求；非可分离场景是否需要。
5. **交叉极化**：H 的极化维（VV/VH/HV/HH）如何进入相关与设备。

## 8. 本篇验收
- B 档：MPDB 角度 → R → 确定性权重，一条 MIMO 链路可复现几何空间相关（可对 R 做数值核验）。
- 诚实边界（几何精确 / 衰落统计等效）在文档与对外口径中一致表述。
- A 档接口预留就位，`.asc` 后端可承载；硬件确认前有守卫不误用。
