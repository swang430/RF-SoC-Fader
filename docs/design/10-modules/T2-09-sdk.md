# T2-09 · M9 Python SDK（功能设计）

> 第二册《功能设计》· 第 9 篇（L5 客户端：`cep_sdk`——REST 的 Python 封装）
> 状态：草稿 v0.1 · 待评审
> 依据：《T1-04 §4 Python SDK》（冻结基线：封装 REST、语义与 REST 一一对应、GUI 与第三方统一入口）
> 消费方：M11 GUI、第三方脚本、CI、演进后的 CLI；依赖：M7（REST/OpenAPI 契约——SDK 的唯一上游，**不直连 L3**）

---

## 1. 概述与定位

`cep_sdk` 是 M7 REST `/v1` 的薄封装：资源对象化、长任务等待、SSE 消费、错误类型化——**零业务逻辑**（一切裁决在服务端；SDK 只做传输、映射与便捷语义）。语义与 REST 一一对应（《T1-04》§4），文档与契约测试复用同一 OpenAPI。

**非职责**：业务编排（M6）、SCPI（M7 网关侧）、GUI（M11）、OpenAPI 服务端契约（M7 为规范）。

---

## 2. 客户端形态

- **async 核心 + sync 门面**：核心用 `httpx.AsyncClient` 实现全部调用；同步门面逐方法委托（事件循环内衬），**两面共用同一实现与测试**（调用图一致性有专项测试，§7）。
- **版本协商**：首次调用惰性 GET `/v1/version`（**网关端点，T2-07 §2 随本篇补入**——区别于 T2-03 引擎侧同名端点），校验 `api_version` 兼容范围（缓存）；不符抛 `VersionMismatchError`（指明升级路径——与 T2-03 client 版本纪律同款）。
- **重试纪律**：仅**幂等 GET** 自动重试（指数退避 ≤N 次）；POST/PATCH **不自动重试**（服务端未定义提交幂等键——显式留给调用方，开放问题 §8-3）。429/503 的 `Retry-After` 透出到异常字段，由调用方决策。

---

## 3. API 面（资源对象，与 REST 一一对应）

```python
cep = Client("https://host:8443", token=..., timeout=...)     # token=API-Key（M7 §4）

# —— 导入（UC2 前半）：POST /imports → 202 {job_id, poll_url}
job   = cep.imports.create_mpdb(mpdb_ref, arrays=..., portmap=..., import_cfg=...)
model = job.wait(timeout=...)                 # 轮询 GET /imports/{job}；失败抛 ImportFailedError（含源错误摘要）

# —— 场景：版本不可变（T2-06 §2）
scen  = cep.scenarios.create(source=..., synthesis=...)       # POST → version=1
scen2 = scen.new_version(synthesis=...)                       # PUT → 新 version（原版永不变）
scen  = cep.scenarios.get(scenario_id, version=None)          # 缺省最新

# —— 会话（UC2 后半 + G4 多后端）
sess = cep.sessions.create(scenario=scen, device="dev0", backend="rfsoc")   # POST /sessions
res  = sess.apply()                    # POST apply（202）+ wait 到终态：ACTIVE→ApplyOutcome；
                                       #   RESOLVE_FAILED/rolled_back → 抛类型化异常（携 last_error/failure）
man  = sess.apply(dry_run=True)        # 同步返 Manifest（仅 READY/ACTIVE，否则 InvalidStateError——服务端裁决）
sess.tweak(channel=(0,1), path=3, delay_ns=..., amp=..., phase_rad=..., doppler_hz=...)
                                       # PATCH /sessions/{id}/channels/{in}_{out}（仅真逐径量，T2-06 §4bis）
sess.artifact(out="etoile.zip", channel=None, format=None)    # GET /artifact 流式落盘（asc→zip；
                                       #   《T1-04》§4 示例 apply(out=...) 即此封装的便捷别名）
sess.recover(); sess.close(release="disable")                 # DIRTY 下 close 非 reset 由服务端拒（透传 409）

# —— 遥测（SSE 客户端语义全自动）
snap = cep.telemetry.snapshot(device="dev0")                  # GET /telemetry（无数据→None）
for ev in cep.telemetry.stream(device="dev0"):                # GET /telemetry/stream：
    ...                                # 断线自动重连携 Last-Event-ID；收到 resync 事件→自动补拉快照后继续；
                                       # heartbeat 超时→重连。ev ∈ {snapshot, alarm, advice}（心跳/重同步被 SDK 吸收）
```

- **`wait()` 语义（终态按操作限定，不设通则）**：轮询 `poll_url`（指数退避，尊重服务端 202 约定）；**以 op_id 关联终局**——202 返回的 op_id 是等待锚点，`op_id ∈ session.completed_ops`（有界历史）才算「本操作」终局，outcome **及失败细节（OpRecord.error/result）**都从对应记录取——会话级 last_error/last_apply 可能已被后续操作覆盖（`current_op == op_id`=仍在途）：re-apply 等复用状态场景，纯看状态会把**旧 ACTIVE** 误判为新操作成功；续等跨越后续操作也能命中历史，被逐出（超 N 条）→ 抛 `StaleWait`（指引查会话审计，T2-06 §2）；
  - **resolve**：终局判据=本 op 的 `OpRecord.outcome`（失败细节=OpRecord.error）；会话态 READY/RESOLVE_FAILED 仅辅助展示。
  - **apply / recover**：终局判据=本 op 的 `OpRecord.result.device_state`——committed=成功；rolled_back=抛 `ApplyRolledBackError`（携 OpRecord.result.failure）；dirty=抛 `DeviceDirtyError`；outcome=aborted_by_restart=抛 `OperationAborted`（服务端重启中止，T2-06 §3）。**绝不以「状态可继续/ACTIVE」当成功，绝不读会话级 last_apply/last_error 判本 op**（可能已被后续操作覆盖——op_id 关联的意义所在）。
  - **导入 job（另一族，无 op_id）**：`job.wait()` 轮询 GET `/imports/{job_id}`，以 job 自身 `status` 终态判定——done=成功返 model 句柄、failed=抛 `ImportFailedError`（携源错误摘要）。job 一次性、状态不复用，**无需也无从 op_id 关联**（202 响应本就只有 `{job_id, poll_url}`，T2-07 §2）。
  - **等待超时 ≠ 任务失败**：抛 `WaitTimeout`（任务仍在服务端跑，句柄可继续 wait）——两族通用。
- **产物即文件**：`artifact()` 流式写盘 + 校验 Content-Length；不把大产物读进内存。

---

## 4. 错误类型化（problem+json → 异常，单一映射表）

| problem `error_code` / HTTP | SDK 异常 | 携带字段 |
| :-- | :-- | :-- |
| 422 校验/能力门 | `ValidationError` / `CapabilityError` | field_errors / 能力差距+替代出口 |
| 409 DeviceBusy | `DeviceBusyError` | holder_session_id |
| 409 非法迁移/InvalidClose/版本冲突 | `InvalidStateError` / `VersionConflictError` | allowed_ops / current_version |
| 409 device_state=dirty | `DeviceDirtyError` | detail 指明 recover |
| 404 | `NotFoundError` | —— |
| 401/403 | `AuthError` | 所需 scope |
| 429 / 503 | `RateLimitedError` / `ServiceUnavailableError` | retry_after |
| 网络/超时 | `TransportError`（含重试痕迹） | —— |

- 映射表与 T2-07 §3 错误表**同源维护**（黄金 fixture 双向测试：每个 problem 样本 ↔ 恰一异常类型）；未知 error_code → 保底 `CepApiError`（不吞原文）。

---

## 5. 与 CLI 的收敛（《T2-06》§8 约定的落点）

现 `channel_simulator.cli`（离线 `.mat→帧文件`）演进为 SDK 之上的薄壳：`cep-cli import|resolve|dry-run|apply|artifact` 子命令逐一映射 SDK 调用；「纯离线一次转换」= `create → resolve → dry_run/artifact → close(leave)`（零设备触达路径）。旧 CLI 保留至新壳覆盖其全部输出（csv/bin/hex 由 artifact+manifest 面承接，格式细节归 M10）。

---

## 6. 打包与版本

- 独立包 `cep_sdk`（不依赖服务端代码）；依赖仅 `httpx`（+`anyio`）。numpy/pandas 为**可选 extras**（报告数据帧化辅助，核心路径零重依赖）。
- SDK 版本与 REST `/v1` 对齐（《T1-04》§7）：`cep_sdk 1.x ↔ /v1`；破坏性变更随 `/v2` 升主版本。

---

## 7. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **契约（stub 服务）** | 以 T2-07 stub L3 起真 FastAPI，SDK 全资源 happy path | 请求/响应与 OpenAPI 逐字段一致 |
| **错误映射黄金** | T2-07 §3 全部 problem fixture | 每样本恰映射一种异常、字段透传完整；未知 code→CepApiError |
| **wait 状态机** | resolve 成功/失败；apply→ACTIVE / **apply→READY(rolled_back)** / DEVICE_DIRTY / 超时；**re-apply 于已 ACTIVE 会话** | apply 回 READY 判失败抛 ApplyRolledBackError（**绝不误报成功**）；re-apply 不因旧 ACTIVE 即刻返回（op_id 关联终局）；异常携 last_error/failure；WaitTimeout 可续等 |
| **SSE 客户端** | 断线重连（窗内/窗外 resync）、heartbeat 丢失 | 事件序列无缝/自动补拉快照；心跳超时触发重连 |
| **产物下载** | zip/octet 大文件流式 | 不读入内存（内存峰值断言）；长度校验 |
| **重试纪律** | GET 网络抖动 / POST 失败 | GET 自动重试；POST 绝不自动重试 |
| **sync/async 一致** | 同用例双门面录制调用图 | 逐调用一致（参数/顺序） |
| **版本协商** | api_version 不符 stub | VersionMismatchError 且指明升级路径 |

---

## 8. 开放问题

1. OpenAPI 代码生成 vs 手写薄客户端——首版**手写**（面小、可控），OpenAPI 黄金 diff 防漂移；面扩大后复评 codegen。
2. 报告（Import/Engine/Fidelity/Quant）的 numpy/pandas 便捷视图范围（extras 边界）。
3. POST 提交幂等键（REST 层扩展 `Idempotency-Key`）→ 允许 SDK 安全自动重试提交类操作——与 M7/M6 协同（T2-03 client_key 同思路），P2 复评。
4. SDK 发布渠道与内部索引（商用部署项）。

---

## 9. 与现有代码的差量

全新包 `cep_sdk`（仓内 `sdk/` 或独立 repo，随 M10 仓库布局定）；现 `channel_simulator` 不受影响，其 CLI 按 §5 路线渐进收敛。

---

## 10. 本篇验收

- stub 契约测试全绿；错误映射黄金全覆盖（与 T2-07 §3 同源）。
- UC2 全流程脚本（《T1-04》§4 示例逐行）在 stub 与 HIL 各跑通一次。
- SSE 重连三剧本与产物流式下载（内存峰值）达标。
- sync/async 调用图一致性测试全绿。
