# 03b · 信道模型层级与退化（顶层设计）

> 第一册《总体设计》· 第 3b 篇（紧接《03-architecture》的顶层原则）
> 状态：草稿 v0.1 · 待评审
> 前置：《03-architecture》；下游《05-rt-mpdb-to-tdl》《06-correlation-mimo》《07-channel-model-engine》

---

## 1. 核心原则

信道表示存在一条**自然嵌套的抽象层级**，由外到内（信息量递减、离硬件递近）：

> **RT（确定性） ⊃ GCM/GSCM（统计） ⊃ CDL ⊃ TDL**

本平台的顶层设计据此确立三条原则：

1. **用户可在任一层级输入**（RT / GCM / CDL / TDL）——入口不被绑死在最外层。
2. 平台沿**退化链**把模型逐级降到**目标硬件能实现的层级**（reduce down to what hardware can realize）。
3. **由 RF-SoC 硬件去实现**——硬件提供两个"实现面"：参数化 TDL（协议帧）与 CIR 回放。平台只负责把模型退化/实现到对应面。

这条原则统摄了「多源输入」「A/B 档」「多后端」——它们都是"沿这条链走到第几层、落到哪个实现面"的不同切法。

---

## 2. 四个层级（+ CIR 实现轴）

| 层级 | 定义 | 数据载荷 | 含角度 | 确定/统计 | 离硬件 | 典型来源 |
| :-- | :-- | :-- | :-- | :-- | :-- | :-- |
| **RT** | 确定性射线，特定环境 | 逐径：delay/H(复)/AoD·ZoD·AoA·ZoA | ✅ | 确定性 | 最远 | MPDB(HyperRT) |
| **GCM/GSCM** | 几何统计模型，按分布抽簇/径 | 簇/径分布参数、LSP | ✅ | 统计 | 远 | 38.901（ChannelEgine） |
| **CDL** | 固定簇（角度保留）；可视为 GCM 的"冻结实例"或 RT 的聚簇 | 簇：power/delay/角度/XPR | ✅ | 统计(定表) | 中 | 3GPP CDL-A..E / RT 聚簇 |
| **TDL** | 抽头：时延+功率(+多普勒)，**角度塌缩为相关矩阵 R** | taps + R | ❌(→R) | 统计 | **最近** | 3GPP TDL-A..E / 统计 TDL 表 |

**CIR（时变复抽头，正交实现轴）**：上面任一层的**波形实现**——逐时刻复抽头序列（`.asc`）。它是最具体的形式，是 RF-SoC **CIR 回放面**播放的对象（A 档）。

> 关键事实：**协议 V3.0 的 RF-SoC 本质是一台"统计 TDL 机"**（24 抽头 delay+amp+phase+doppler，原生无角度）。所以任何上层模型要经**参数化 TDL 面**落地，都必须退化到 TDL + 相关矩阵 R。

---

## 3. 退化链与算子

```
   RT ──reduce_RT_to_CDL──►  CDL ──reduce_CDL_to_TDL──►  TDL ──► [参数化 TDL 面]
   (聚簇: 角度/时延邻近)        (施加阵列几何+空间相关,
                                角度塌缩为 R, 见《06》B 方案)
   GCM ──reduce_GCM_to_CDL──► CDL ──────────────────────► TDL
        (抽样实现)

   任一层 ──realize_to_CIR──► CIR ──► [CIR 回放面]   (时变波形实现, A 档)
```

**退化算子是物理发生地**，各自独立、可组合、可单测：
- `reduce_RT_to_CDL`：按角度/时延邻近把射线聚为簇（RT→CDL）。
- `reduce_CDL_to_TDL`：施加天线阵列 + 空间相关 → 角度塌缩为 R → 产出抽头（3GPP 的 CDL→TDL 推导；即《06》B 方案核心）。
- `reduce_GCM_to_CDL` / `reduce_GCM_to_TDL`：38.901 抽样/组合。
- `realize_to_CIR`：抽样时变复抽头（A 档 / `.asc`）。

> 3GPP 一致性：CDL 与 TDL 是并列的两种退化，且标准给出**由 CDL 推导 TDL** 的过程（空间相关 + 天线假设）。RT→CDL 是其类比（把确定性射线聚簇）。我们的链条与标准同构。

---

## 4. 用户可选输入层级（产品能力）

| 输入层级 | 入口 | 平台动作 |
| :-- | :-- | :-- |
| **RT** | 导入 MPDB（《05》） | RT→(CDL)→TDL 或 →CIR |
| **GCM** | 选 38.901 场景（ChannelEgine，《07》） | GCM→CDL→TDL 或 →CIR |
| **CDL** | 导入/选 CDL 表 | CDL→TDL 或 →CIR |
| **TDL** | 直接给统计 TDL 表（抽头参数） | 直接落 TDL 面（最短路） |

- 用户**声明输入层级**（或平台按数据识别）；平台按「目标实现面 + 期望保真（A/B 档）」自动决定**退化深度**。
- 「统计 TDL」不再是"GCM 的隐式副产物"，而是一个**一等输入入口**（最贴硬件、最短路径）。

---

## 5. 硬件实现面（让 RF-SoC 去实现）

RF-SoC 提供两个实现面，平台把模型退化/实现到对应面：

| 实现面 | 承载 | 层级要求 | 档 | 状态 |
| :-- | :-- | :-- | :-- | :-- |
| **参数化 TDL 面** | 协议 V3.0 帧：24 抽头 delay/amp/phase/doppler + 相关（确定性权重） | 退化到 **TDL + R** | B | ✅ 本期 |
| **CIR 回放面** | 时变复抽头（`.asc`/CIR 帧） | 实现到 **CIR** | A | 🔒 待硬件确认注入接口（《12》#1） |

**退化深度 = f(输入层级, 目标实现面)**，二维视图：

```
                 参数化 TDL 面(B)              CIR 回放面(A)
   RT   │  RT→CDL→TDL + R                │  RT→…→realize_to_CIR
   GCM  │  GCM→CDL→TDL + R              │  GCM→realize_to_CIR (ChannelEgine 原生时变)
   CDL  │  CDL→TDL + R                  │  CDL→realize_to_CIR
   TDL  │  直接(+假设 R)                │  TDL→realize_to_CIR
```

---

## 6. canonical model 的层级化（精化《03》§4）

canonical model 从"以 TDL 为中心的并集"升级为**带层级标记 + 匹配载荷**：

```
CanonicalChannelModel {
  level:        RT | GCM | CDL | TDL        # 当前所处表示层级
  realization:  none | CIR                  # 正交的时变波形实现(.asc / A 档)
  payload:      rays | clusters | taps      # 与 level 匹配
  correlation:  R                           # 退化到 TDL 时产生
  time:         static | time_varying       # 见《03》ADR-7
  meta/grid/impairments: …
}
```
- 一个模型对象在**退化链上前进**时更新 `level` 与 `payload`；退化算子是 `level_i → level_{i+1}` 的纯变换。
- 下游后端按 `capabilities` 要求的层级消费（TDL 面要 `level=TDL`；CIR 面要 `realization=CIR`）。

---

## 7. 与 A/B 档、多源、多后端的统一

- **A/B 档** = 沿链退化多深：**B = 退到 TDL + R（准静态）**；**A = 停在 CIR 实现（时变）**。不再是两种并列策略，而是同一条链的两个终点。
- **多源** = 在不同层级进入同一条链（RT/GCM/CDL/TDL 入口）。
- **多后端** = 落到不同实现面（参数化 TDL 面 / CIR 回放面）。
- 三者正交，都被"层级 + 退化 + 实现面"这一套语言统一。

---

## 8. 映射到分层架构（《03》）

| 关注点 | 归属层 |
| :-- | :-- |
| 输入层级选择（入口） | L4 API / L5（`imports`/`scenarios` 带 `level` 参数，《04》） |
| 退化算子 `reduce_*` / `realize_to_CIR` | L3 服务/编排 |
| canonical model（带 level） | L3 枢纽契约 |
| 实现面渲染（TDL 帧 / CIR） | L2 设备后端（《08》） |
| 层级/单位/坐标换算 | L1 |

---

## 9. 开放问题
1. 「统计 TDL 直接输入」与「CDL 输入」的入口格式（复用 3GPP 表？自定义 JSON？）。
2. `reduce_RT_to_CDL` 的聚簇准则（角度/时延阈值、簇数上限）——与《06》§6 阵列映射、M4 协同。
3. 各退化算子的黄金用例与数值核验（RT→CDL→TDL 与直接 RT→TDL 的一致性）。
4. 是否允许"跳级"退化（RT→TDL 直达）作为 RT→CDL→TDL 的等价快捷路径。

## 10. 本篇验收
- 四层级 + CIR 实现轴 + 退化算子构成闭合、与 3GPP 同构的顶层框架。
- 「用户任一层输入 / 平台退化 / 硬件实现」三原则被 03/05/06/07/08 一致引用。
- A/B 档、多源、多后端都能用"层级+退化+实现面"解释，无例外。
