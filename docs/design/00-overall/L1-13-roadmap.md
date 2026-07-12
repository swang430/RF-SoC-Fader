# 13 · 路线图（总体设计）

> 第一册《总体设计》· 第 13 篇 · 状态：草稿 v0.1
> 前置：全册；对应第二册模块（M1–M9）、第三册测试（T1–T5）。

---

## 1. 阶段总览

| 阶段 | 主题 | 交付 | 依赖/风险 |
| :--- | :--- | :--- | :--- |
| **P0** | 基座 | L1 全 ID 编码 + 下行帧解码；L2 `DeviceBackend` 抽象 + RFSoCBackend(TCP 下发+回显校验) + AscCirBackend 骨架；`mpdb_reader` | 修复下发退化；Q4/Q5 |
| **P1** | 单信道 TDL 闭环 | MPDB→TDL→TCP 下发→遥测确认端到端；B 档相关最小实现 | Q4/Q6 |
| **P2** | 引擎集成 + API 产品化 | ChannelEgine 微服务 + `ChannelEngineClient`；REST + SDK；场景/会话管理；GUI 对接 | 《07》契约 |
| **P3** | MIMO 相关 + 高级能力 | `CorrelationSynthesizer` B 落地；扫频/瑞利谱/信号源；AscCirBackend 完善（承载 A） | Q2/Q3 |
| **P4** | 多设备 + SCPI + 校准验收 | 可扩展寻址、SCPI 兼容层、校准与验收体系；A 档（若 Q1 确认） | Q1 |

---

## 2. 里程碑与验收信号

- **M-P0**：`tests/test_protocol.py` 扩展到全 35 ID 通过；TCP 环回可收发一帧并解码回显。
- **M-P1**：一条真实 MPDB 链路下发到设备，遥测确认无溢出（旗舰最小闭环）。
- **M-P2**：REST 三步（import→scenario→apply）跑通；GUI 可视化 PDP。
- **M-P3**：一条 MIMO 链路复现几何空间相关（对 R 数值核验）；`.asc` 导出被目标设备接收。
- **M-P4**：多设备寻址 + SCPI 冒烟；校准/溢出保护验收。

---

## 3. 与三册文档的对应

- 本册（总体）冻结后 → 第二册**模块设计 M1–M9**（伪代码级）→ 第三册**开发测试 T1–T5**。
- P0 代码基座在模块设计 M1/M2/M4 就绪后启动，作为设计与实现的连接点。

---

## 4. 关键路径（旗舰优先）

```
L1 补全 + mpdb_reader ─► RFSoCBackend(TCP 下发) ─► MPDB→TDL 管线 ─► B 档相关 ─► 单链路闭环(P1)
                                    │
                         (并行) AscCirBackend 骨架 ─► .asc 导出(P3)
```
- 旗舰（RT→TDL 播放）走 P0→P1→P3 主线；引擎集成与 API（P2）可与 P1 并行。

## 5. 本篇验收
- 每阶段交付物、依赖、风险闭环点清晰。
- 关键路径以旗舰功能为先。
- 阶段与 M/T 两册可追溯。
