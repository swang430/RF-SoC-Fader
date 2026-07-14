# RF-SoC 信道仿真控制平台（CEP）· 设计文档

本目录为平台设计文档，分三册。**第一册（总体设计）为当前评审阶段产物**；第二、三册在总体设计冻结后展开。

## 编号约定

**册号前缀**：`T1-`=第一册·顶层设计，`T2-`=第二册·功能设计，`T3-`=第三册·测试设计。
（`T`=册（Tier/Top-down），特意避开正文中的**运行时分层 L1–L5**（编解码…客户端），互不冲突。）

## 阅读顺序（第一册 · 总体设计 `00-overall/`）

| # | 文档 | 内容 |
| :-- | :-- | :-- |
| T1-01 | [目标、范围与术语](00-overall/T1-01-goals-and-scope.md) | G1–G6 目标、In/Out/Non-Goals、术语表 |
| T1-02 | [系统上下文](00-overall/T1-02-system-context.md) | 使用者、外部接口、用例全景、上下文图 |
| T1-03 | [分层架构](00-overall/T1-03-architecture.md) | 五层单向依赖、canonical model 枢纽、ADR |
| T1-03b | [信道模型层级与退化](00-overall/T1-03b-model-hierarchy.md) | RT⊃GCM⊃CDL⊃TDL 嵌套、退化算子、用户任一层输入 |
| T1-03c | [信道 Schema（规范契约）](00-overall/T1-03c-channel-schema.md) | canonical model 字段级规范、MPDB↔schema 映射 |
| T1-04 | [API 策略](00-overall/T1-04-api-strategy.md) | REST 主 + SDK + SCPI，资源模型 |
| T1-05 | [RT/MPDB → TDL 导入](00-overall/T1-05-rt-mpdb-to-tdl.md) | 旗舰上半段：MPDB 管线 |
| T1-06 | [MIMO 空间相关性](00-overall/T1-06-correlation-mimo.md) | 旗舰下半段：B 档基线 / A 档预留 |
| T1-07 | [信道模型引擎集成](00-overall/T1-07-channel-model-engine.md) | ChannelEgine 微服务集成 |
| T1-08 | [多设备后端抽象](00-overall/T1-08-device-backends.md) | RFSoCBackend + AscCirBackend |
| T1-09 | [技术栈](00-overall/T1-09-tech-stack.md) | Python 全栈选型 |
| T1-10 | [可扩展架构](00-overall/T1-10-scalability.md) | 单机 → 多设备寻址 |
| T1-11 | [非功能与横切](00-overall/T1-11-nfr-cross-cutting.md) | 事务/可观测/安全/校准 |
| T1-12 | [风险与待确认清单](00-overall/T1-12-risks-open-items.md) | ★ 待硬件确认项 Q1–Q6 |
| T1-13 | [路线图](00-overall/T1-13-roadmap.md) | P0–P4 阶段与关键路径 |

### 附录（参考性）

| # | 文档 | 内容 |
| :-- | :-- | :-- |
| T1-A1 | [协议 V3.0 架构综述](00-overall/T1-A1-protocol-v3-overview.md) | 帧/子帧/参数三层、35 ID 七功能族、设备模型、可靠性缺口 |

## 后续（骨架已建，待展开）

- **第二册 · 功能设计** `10-modules/`（文件将以 `T2-` 前缀编号，如 `T2-01-protocol-codec.md`）：M1 协议编解码 · M2 设备后端/传输 · M3 引擎集成 · M4 MPDB 导入 · M5 相关性 · M6 场景会话 · M7 API 网关 · M8 遥测校准 · M9 SDK/客户端 · **M10 格式与持久化(File I/O)**。
- **第三册 · 测试设计** `20-dev-test/`（文件将以 `T3-` 前缀编号；原 T1–T5 模块代号为避免与册号撞名改为 `T3-01…T3-05`）：T3-01 流程/仓库 · T3-02 测试金字塔 · T3-03 硬件在环 · T3-04 黄金帧一致性 · T3-05 CI/发布/验收。

## 一句话架构

> 用一份**与设备无关的统一信道表示（canonical model）**把「模型来源（RT/MPDB · 38.901/ChannelEgine）」与「设备后端（RF-SoC 二进制 · .asc CIR）」解耦，以分层 API（REST/SDK/SCPI）同时服务自有软件与第三方。
