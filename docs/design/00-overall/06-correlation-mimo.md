# 06 · MIMO 空间相关性（总体设计）

> 第一册《总体设计》· 第 6 篇（旗舰链路下半段 · 技术核心）
> 状态：草稿 v0.1 · 待评审
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

---

## 2. 从 RT 角度到相关矩阵（B 档核心算法）

MPDB 提供每条径的**离去角（AoD/ZoD）**、**到达角（AoA/ZoA）**、**复增益 H**，加上**天线阵列几何**，即可算出真实的空间相关矩阵——这是 `.mat` 做不到、MPDB 独有的能力。

### 2.1 导向矢量（steering vector）
对含 `M` 个阵元、位置 `{p_m}` 的阵列，来波方向单位矢量 `k̂(φ,θ)`：

```
a_m(φ,θ) = exp( j · (2π/λ) · p_m · k̂(φ,θ) )        # 第 m 个阵元相位
a(φ,θ)   = [a_1, ..., a_M]ᵀ                          # 导向矢量
```
（角度用《05》归一后的方位角 φ 与仰角 el；`k̂` 由 φ、el 合成。）

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

## 4. A 档（预留）：时变 CIR 注入

- **数据已就绪**：ChannelEgine 已能产出带 38.901 空间相关的**逐时刻时变 CIR**（`.asc`：N 个 CIR × T 抽头 + 更新率）。
- **落地路径两条**：
  1. **AscCirBackend**：直接把 time_varying 的 canonical model 渲染为 `.asc`，交 CIR 回放设备（PropSim/Spirent 或 RF-SoC 的 CIR 模式）——**若目标设备吃 `.asc`，A 档即刻可达**。
  2. **RF-SoC 专用 CIR 帧**：若 RF-SoC 提供"时变抽头/相干种子流式注入"能力（协议 V3.0 未暴露，**待硬件确认**），则经扩展帧注入。
- 二者都消费 canonical model 的 `time.mode=time_varying` + `cir`，策略接口与 B 档统一。

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
    # 消费/生成 time_varying + cir；交 AscCirBackend 或 RF-SoC CIR 帧
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

## 7. 开放问题（头号风险，详见《12》）
1. **A 档可行性**：RF-SoC 是否支持时变 CIR/抽头流式注入？（决定 A 档能否经 RF-SoC 而非仅 `.asc`）。
2. **ID6 谱系数精确语义**：256 复数 ↔ 多普勒谱/最大多普勒的映射；启用瑞利后功率下降量的定标补偿。
3. **Kronecker vs 全相关**：可分离近似是否满足精度要求；非可分离场景是否需要。
4. **交叉极化**：H 的极化维（VV/VH/HV/HH）如何进入相关与设备。

## 8. 本篇验收
- B 档：MPDB 角度 → R → 确定性权重，一条 MIMO 链路可复现几何空间相关（可对 R 做数值核验）。
- 诚实边界（几何精确 / 衰落统计等效）在文档与对外口径中一致表述。
- A 档接口预留就位，`.asc` 后端可承载；硬件确认前有守卫不误用。
