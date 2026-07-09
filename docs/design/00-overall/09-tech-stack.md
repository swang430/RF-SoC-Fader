# 09 · 技术栈（总体设计）

> 第一册《总体设计》· 第 9 篇 · 状态：草稿 v0.1（技术栈已认可）
> 前置：《03-architecture》ADR-3

---

## 1. 结论

**Python 全栈**（已认可）。核心理由：**实时衰落在 RF-SoC/FPGA 内生成**，软件只做**帧级控制 + 系数计算**，无样本级实时压力；最大化复用现有 `channel_simulator` 包与两个上游引擎。

---

## 2. 选型

| 层/关注点 | 选型 | 说明 |
| :--- | :--- | :--- |
| API 网关（L4） | **FastAPI** + Uvicorn | REST/OpenAPI 自动生成；SCPI-over-TCP 用 asyncio server |
| 异步/传输（L2） | **asyncio** | TCP 下发/遥测、并发会话、超时重连 |
| 科学计算（L3） | **numpy / scipy** | 相关矩阵、导向矢量、滤波系数、量化 |
| 数据接口 | 结构化 `dataclass`（canonical model）；pandas 用于 TDL 中间态 | 升级现有「DataFrame 即接口」 |
| 上游引擎 | **ChannelEgine 微服务**（其内 PyTorch）；`ChannelEngineClient` | 依赖隔离于主进程（《07》） |
| MPDB 接入 | HyperRT SDK（PyTorch）**或** MPQL 导出 CSV | 见《12》待定；后者可让主进程免 PyTorch |
| 配置/持久化 | JSON/YAML + 版本化 | 配置即数据（《11》） |
| 测试 | unittest/pytest；黄金帧回归 | 复用 `tests/test_protocol.py` 基线 |
| 打包/部署 | 容器化；主服务与引擎服务分容器 | 微服务部署（《03》§6） |

---

## 3. 性能立场

- **控制面**：帧级操作（毫秒—秒），Python + asyncio 充裕。
- **计算面**：相关矩阵/系数为分钟级离线，numpy/scipy 足够；大规模可向量化。
- **实时面**：**不在软件**——衰落/多普勒/AWGN 由硬件内生。软件不进采样环路。
- **未来热点**：若出现证实的高频控制瓶颈，仅将 L2 传输热点下沉为 C 扩展，不改架构。

---

## 4. 依赖风险
- PyTorch/HyperRT 为重依赖：通过**微服务隔离**（引擎）与 **MPQL-CSV 备选**（MPDB）控制其对主进程的侵入。
- 版本锁定与可复现构建（lockfile/容器）。

## 5. 本篇验收
- 主进程依赖精简（无强制 PyTorch）。
- 选型与《03》分层、《07》微服务、《12》MPDB 待定一致。
