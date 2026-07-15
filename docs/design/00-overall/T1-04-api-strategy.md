# 04 · API 策略（总体设计）

> 第一册《总体设计》· 第 4 篇
> 状态：**v1.0 · 已冻结**（2026-07-15，tag: design-t1-v1.0）
> 前置：《02-system-context》《03-architecture》

---

## 1. 概述

平台对外提供三种 API 前端，**共享同一 L3 服务层**，杜绝逻辑分叉：

| 前端 | 定位 | 主要用户 | 优先级 |
| :--- | :--- | :--- | :--- |
| **REST / OpenAPI** | 主力、语言无关、资源化 | 第三方集成、GUI（经 SDK） | P0/P2 |
| **Python SDK** | 封装 REST，import 即用 | 自有 GUI、测试脚本、第三方 | P2 |
| **SCPI-over-TCP** | 仪器行业习惯，测试台对接 | 自动化测试工程师 | P4 |
| gRPC 流（可选） | 实时遥测/高频控制 | 后续按需 | P2+ |

> **硬件原生的私有二进制帧协议不对外暴露**——它只是 L1/L2 的对接面。对外一律走上述高层 API。

---

## 2. 为什么是「REST 主 + SDK + SCPI」

**回应「硬件用什么 API 形态、适合什么」**：
- 协议 V3.0 文档显示，硬件原生是**自定义二进制 TLV 帧**（`FDB18540...AE86`）over 链路，**不是 SCPI**，也不是 REST。它面向设备寄存器级控制，不适合直接给第三方。
- 因此对外 API 是**平台自定义的高层抽象**，把「设备寄存器语义」提升为「信道/场景/会话语义」。
- REST 覆盖面最广、第三方门槛最低，作主力；SDK 提升自有软件与脚本体验；SCPI 兼容层照顾仪器行业既有测试资产（替换/并列传统仪表）。

---

## 3. REST 资源模型（主力）

资源化、无状态请求、状态存于服务端会话。核心资源：

| 资源 | 方法 | 说明 |
| :--- | :--- | :--- |
| `/devices` `/devices/{id}` | GET；POST/DELETE（**P4 多设备阶段启用**） | 设备列表/详情/健康；设备注册/注销（多设备预留《T1-10》；单机默认单设备，注册端点不启用） |
| `/imports` | POST | 提交 MPDB 导入任务（异步）→ 返回 job |
| `/imports/{job}` | GET | 导入进度/结果（canonical model 句柄） |
| `/scenarios` | GET/POST | 场景（统计信道参数或导入结果）的 CRUD、版本化 |
| `/scenarios/{id}` | GET/PUT/DELETE | 单场景 |
| `/sessions` | POST | 创建下发会话（绑定设备+场景+后端） |
| `/sessions/{id}/apply` | POST | 事务化下发（可选 dry-run 仅生成帧/asc 不下发） |
| `/sessions/{id}` | GET | 会话状态（下发结果、校验、事务态） |
| `/channels` | GET/PATCH | 逐信道对参数查询/微调（栅格内 64 信道） |
| `/telemetry` | GET | 最近遥测快照（电平/功率/溢出） |
| `/telemetry/stream` | GET(SSE) | 遥测订阅（SSE；gRPC 流后续可选） |

**约定**：
- **多层级输入**（《03b》）：`imports`/`scenarios` 带 `level`（RT/GCM/CDL/TDL）声明输入层级；`session` 的实现面（TDL 帧 / CIR）决定平台退化深度。用户可在任一层输入。
- **长耗时操作异步化**（MPDB 导入、批量下发）：返回 job/session，轮询或订阅。
- **dry-run**：`apply?dry_run=true` 只产出后端产物（帧/`.asc`）不触设备，便于 CI 与黄金对比。
- **后端选择**：`session` 绑定 `backend=rfsoc|asc`，同一 scenario 可开两个会话产两种后端产物（G4）。
- **错误**：统一 problem+json（含协议错误帧、范围校验失败、设备不可达等分类）。

---

## 4. Python SDK（封装 REST）

```python
from cep_sdk import Client

cep = Client("https://host:8443", token=...)

# UC2：RT→TDL 播放
job = cep.imports.create_mpdb("etoile_radio.mpdb", array=my_array_geometry)
model = job.wait()                       # canonical model 句柄
scen  = cep.scenarios.create(model, name="etoile-uma")
sess  = cep.sessions.create(device="dev0", scenario=scen, backend="rfsoc")
res   = sess.apply()                     # 事务化下发 + 闭环确认
print(res.verified, res.telemetry.output_power)

# 多后端（G4）：同一 scenario 导出 .asc
cep.sessions.create(device=None, scenario=scen, backend="asc").apply(out="etoile.asc")
```
- SDK 是 GUI 与第三方脚本的统一入口；语义与 REST 一一对应，便于文档与测试复用。

---

## 5. SCPI-over-TCP 兼容层（P4）

把最常用操作映射为 SCPI 风格指令，接入仪器自动化测试台：

```
*IDN?                                   → 平台/设备标识
:SCENario:LOAD "etoile-uma"             → 加载场景
:SESSion:BACKend RFSoc                  → 选后端
:SESSion:APPLy                          → 下发
:TELemetry:OUTPut:POWer? (@1)           → 查输出端1功率
:SYSTem:ERRor?                          → 错误队列
```
- 仅覆盖高频操作子集；复杂编排仍建议 REST/SDK。映射表与语法在 M7 细化。

---

## 6. 认证 / 鉴权 / 审计 / 限流（横切）

- **认证**：Token/API-Key（对第三方）；本地 GUI 可用会话令牌。
- **鉴权**：下发类操作（`apply`、设备控制）需更高权限；只读遥测/场景可低权限。
- **审计**：**所有触达设备的操作**（apply、通道微调、复位）写审计日志（谁/何时/下发了什么帧摘要）——因直接影响物理射频输出。
- **限流**：保护设备与服务；批量下发排队。

---

## 7. 版本化与兼容

- REST 走 `/v1` 前缀；OpenAPI 契约作为第三方合同，破坏性变更升版本。
- SCPI 指令集与 SDK 版本与 REST `/v1` 对齐。
- canonical model 与设备协议是内部契约，可独立演进，不直接暴露给 API 版本。

---

## 8. 开放问题
1. 遥测订阅：SSE 是否足够，还是需要 gRPC 双向流（高频/低延迟场景）。→ P2。
2. 多租户/并发下发的隔离粒度（按设备锁？按会话事务？）。→ 与《11》事务化下发协同。
3. SCPI 指令集覆盖范围（最小可用子集边界）。→ M7。

## 9. 本篇验收
- 三前端映射到同一 L3 服务接口，无重复业务逻辑。
- REST 资源模型覆盖 UC1–UC8 全部顶层用例。
- 鉴权/审计对「触达设备」操作无遗漏。
