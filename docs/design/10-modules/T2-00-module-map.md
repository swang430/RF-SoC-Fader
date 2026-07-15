# T2-00 · 第二册《功能设计》总览与模块地图

> 第二册《功能设计》· 开册篇
> 状态：草稿 v0.1 · 随各模块推进更新
> 基线：第一册《顶层设计》v1.0（tag `design-t1-v1.0`）——本册一切设计以其为准；如需变更第一册，走 PR 升版。

---

## 1. 本册定位与写法

- **粒度**：伪代码 + 时序图级（可据此直接开发）。
- **模板**（每模块统一）：概述与定位 → 职责边界 → 接口契约 → 数据模型 → 关键算法（伪代码）→ 错误处理 → **测试设计** → 与现有代码的差量 → 开放问题。
- **测试设计内置**：每模块文档自带该模块的功能测试设计（黄金样本、边界、回归、覆盖目标）；跨模块的集成/系统测试留第三册 T3。

## 2. 模块清单与状态

| # | 文档 | 模块 | 依赖 | 状态 |
| :-- | :-- | :-- | :-- | :-- |
| T2-01 | [协议编解码](T2-01-protocol-codec.md) | M1：V3.0 全 ID 编码 + 下行帧解码 + 单位换算 | 无（最底层） | ✅ 已合并（PR #7，Codex 13 条闭环） |
| T2-02 | [设备后端/传输](T2-02-device-backends.md) | M2：DeviceBackend 抽象、RFSoC/TCP、AscCir、帧预算切分、事务 | M1 | ✅ 已合并（PR #8，Codex 21 条闭环） |
| T2-03 | [引擎集成](T2-03-engine-integration.md) | M3：ChannelEngineClient 微服务契约 | schema | ✅ 已合并（PR #11，Codex 20 条闭环） |
| T2-04 | [RT-MPDB 导入](T2-04-mpdb-import.md) | M4：mpdb_reader、量化合并、**阵列↔栅格映射(port_map)** | M1, schema | ✅ 已合并（PR #9，Codex 27 条闭环） |
| T2-05 | [相关性合成](T2-05-correlation.md) | M5：CorrelationSynthesizer（B 落地/A 预留）、退化算子 | M4, M8（谱归一纯函数） | ✅ 已合并（PR #10，用户评审） |
| T2-06 | [场景/会话管理](T2-06-scenario-session.md) | M6：生命周期状态机、配置即数据 | M2 | ✅ 已合并（PR #12，Codex 3 条闭环） |
| T2-07 | [API 网关](T2-07-api-gateway.md) | M7：REST/OpenAPI、SCPI 映射 | M6 | ✅ 已合并（PR #13，Codex 24 条闭环） |
| T2-08 | [遥测与校准](T2-08-telemetry-calibration.md) | M8：遥测解析消费、谱型归一化、bypass 表、溢出保护 | M1, M2 | ✅ 已合并（PR #14，Codex 6 条闭环） |
| T2-09 | [SDK/客户端](T2-09-sdk.md) | M9：Python SDK | M7 | ✅ 已合并（PR #15，Codex 10 条闭环） |
| T2-10 | [格式与持久化](T2-10-formats-persistence.md) | M10：codec 注册表、I/O 适配层、配置 repository、blob | schema | **本 PR** |
| T2-11 | GUI | M11：五视图细化（依《T1-14》，Web） | M7, M9 | 待开 |

## 3. 依赖图与建议推进序

```
M1 ──► M2 ──► M6 ──► M7 ──► M9 ──► M11
 │      │                    ▲
 │      └──► M8              │
 └──(schema)◄── M10          │
      M4 ──► M5 ─────────────┘      M3 ──►(schema)
```
建议推进：**M1 → M2 → M4 → M5**（旗舰 B 档主线）与 **M3/M10** 并行，其后 M6–M9–M11。

## 4. 工作流约定

- 每模块（或紧密相关的一组）一个特性分支 + PR；**PR 标题以 ClickUp `#taskId` 开头**（自动关联任务）。
- 模块间契约变更需同步依赖方文档与测试章节。
