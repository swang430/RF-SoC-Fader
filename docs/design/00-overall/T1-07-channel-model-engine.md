# 07 · 信道模型引擎集成（ChannelEgine · 微服务）

> 第一册《总体设计》· 第 7 篇
> 状态：**v1.0 · 已冻结**（2026-07-15，tag: design-t1-v1.0）
> 前置：《03-architecture》（ADR-5 微服务集成）

---

## 1. 概述

统计信道能力（3GPP TR 38.901：UMa/UMi/RMa/InH、CDL/TDL、LSP、簇、XPR、K 因子、MIMO 空间映射、时变 CIR）**不重造**，集成兄弟项目 `ChannelEgine`。

**集成形态：微服务**（已定，ADR-5）。ChannelEgine 作为独立进程/容器运行，CEP 主进程经薄封装 `ChannelEngineClient`（RPC/REST）调用，**不在主进程加载其算法与 PyTorch 依赖**。

**只集成引擎/服务能力，不复用其 GUI**（`gui.py` 弃用，界面全新设计）。

> 层级视角（《03b》）：ChannelEgine 是**在 GCM/CDL 层入口进入退化链**（38.901 统计模型；可产 CDL 定表或时变 CIR）。其产出经 `ChannelEngineClient` 转为 canonical model（`level=GCM|CDL`，或 `realization=CIR`），再由平台退化到目标实现面。

---

## 2. 为什么是微服务（回顾 ADR-5）

| 维度 | 微服务（选） | 同进程库调用（弃） |
| :--- | :--- | :--- |
| 依赖隔离 | ✅ PyTorch 等重依赖不入主进程 | ❌ 污染主进程、拖慢启动 |
| 故障隔离 | ✅ 引擎崩溃不拖垮 API/设备控制 | ❌ 共命运 |
| 独立伸缩/部署 | ✅ 引擎可单独扩容/升级 | ❌ 绑定发布 |
| 版本解耦 | ✅ 按契约演进 | ❌ 紧耦合 |
| 代价 | 需定义网络契约、序列化开销 | 无网络开销 |
- 信道生成是**分钟级、非实时**任务，网络/序列化开销可忽略，微服务收益远大于代价。

---

## 3. 集成边界与契约

```
CEP L3                                   ChannelEgine 微服务
┌───────────────────────┐   RPC/REST    ┌────────────────────────────┐
│ ChannelEngineClient    │──────────────►│ 引擎 API                    │
│ （薄封装：请求→响应）  │◄──────────────│  ├ /models  生成信道         │
│  → 转换为 canonical    │               │  ├ /scenarios 场景参数       │
└───────────────────────┘               │  └ /cir     时变 CIR 序列    │
        │ 产出                            └────────────────────────────┘
        ▼
   CanonicalChannelModel（taps/角度/相关/可选 time_varying+cir）
```

**契约要点**：
- **请求**：场景类型（UMa/CDL...）、载频、带宽、阵列几何、移动性、确定性/随机 LSP、是否需时变 CIR。
- **响应**：簇/径（时延、功率、AoD/ZoD/AoA/ZoA、XPR、K）与/或时变 CIR（对应 `.asc` 内容）。
- **转换层**：`ChannelEngineClient` 把响应映射为 canonical model（与《05》MPDB 导入产出同一契约）——**下游后端对"来自 RT 还是 38.901"无感**。
- **传输/序列化选型**（gRPC / REST+JSON / 消息队列）在 M3 定；张量类数据考虑二进制/Arrow 以省带宽。

---

## 4. 两上游源的统一（关键价值）

| 能力 | MPDB 导入（《05》） | ChannelEgine（本篇） |
| :--- | :--- | :--- |
| 性质 | 确定性射线（真实场景） | 统计模型（标准场景） |
| 角度 | 有（几何真值） | 有（38.901 生成） |
| 时变 | 默认 static（可几何注入） | 可 time_varying（原生时变 CIR） |
| 相关性 | 角度→R（《06》B 档） | 38.901 空间相关（可承载 A 档数据） |
| 产出 | **canonical model** | **canonical model** |

→ 两源殊途同归到 canonical model，下游多后端与 API 完全复用。这是「模型/设备解耦」在**来源侧**的兑现。

---

## 5. 部署与运维

- ChannelEgine 服务与 CEP 主服务可同机不同进程（容器），或分机部署。
- 健康检查、超时、重试、熔断在 `ChannelEngineClient` 内处理；引擎不可达时 API 返回明确错误（不影响已下发设备状态）。
- 版本：客户端与引擎契约版本对齐；引擎独立升级需契约兼容或版本协商。

---

## 6. 开放问题
1. 传输/序列化选型（gRPC vs REST vs MQ）与张量编码（Arrow/二进制）。
2. ChannelEgine 现有 API 面（`ChannelSimulator` 类等）到微服务契约的适配工作量（是否需在其仓库加服务层）。
3. 时变 CIR 的数据量与传输策略（大对象走对象存储句柄，还是内联）。

## 7. 本篇验收
- `ChannelEngineClient` 契约明确，产出与《05》同一 canonical model。
- 主进程无 PyTorch/引擎重依赖（依赖隔离达成）。
- 引擎故障不影响 API 与设备控制链路（故障隔离达成）。
