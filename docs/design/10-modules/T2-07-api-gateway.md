# T2-07 · M7 API 网关（功能设计）

> 第二册《功能设计》· 第 7 篇（L4：REST/OpenAPI 主力 + SCPI 兼容层 + 认证/审计/限流）
> 状态：草稿 v0.1 · 待评审
> 依据：《T1-04 API 策略》（冻结基线：三前端共享 L3、REST 资源模型、SCPI P4）《T1-11 §3 审计》
> 消费方：M9 SDK、M11 GUI、第三方集成、CI；依赖：M6（场景/会话服务）、M4（导入 job）、M8（遥测服务——接口先行声明，T2-08 落实现）、M10（审计/配置存储）

---

## 1. 概述与定位

M7 是 L4 网关：把 L3 服务（M6 为主）表达为三种前端——**REST/OpenAPI**（主力，P0/P2）、**SCPI-over-TCP**（P4，本篇契约先行）、**SSE 遥测流**。

**零业务逻辑铁律**（《T1-04》§1「共享同一 L3 服务层，杜绝逻辑分叉」）：每个端点 = 参数校验 + 鉴权 + **调用 L3** + 错误翻译 + 审计。任何编排、状态判断、数值处理都不得出现在 M7——三前端产生同一结果的唯一保证。

**非职责**：编排与状态机（M6）、遥测采集与校准（M8）、模型算法（M3/M4/M5）、持久化实现（M10）、SDK 封装（M9）、GUI（M11）。

---

## 2. REST 资源 → L3 映射（`/v1` 前缀）

| 端点 | 方法 | L3 调用 | scope | 模式 |
| :-- | :-- | :-- | :-- | :-- |
| `/version` | GET | 网关标识 + `api_version`（SDK/第三方**版本协商面**，T2-09 §2 首调探测的即此端点）——静态元数据，零 L3 | **免鉴权**（公开探测；不泄内部拓扑） | 同步 |
| `/devices` · `/devices/{id}` | GET | registry 列表/详情 + `readback` 健康 | read | 同步 |
| `/devices` | POST/DELETE | ——预留 P4（《T1-10》多设备）：**501 + Problem**（feature-flag 关闭态） | control | —— |
| `/imports` | POST `{kind, ...}` | ImportJobService.submit（异步 job）——`kind=mpdb`（M4 导入）\| `kind=cdl_tdl_table`（定表 JSON 直录，经 `cdl_tdl_reader` 物化入 ModelRepo，T2-10——「任一层输入」的 API 落点） | write | **202** + job_id |
| `/imports/validate` | POST `{portmap, ctx}` | **同步预检**：复用 M4 纯函数 `validate_portmap`（V1–V7，T2-04 §3.2——ctx 含 topology/path_expansion/test_mode/arrays/目标后端）→ 200 或 422+field_errors。**只校验不落任何状态**（向导即时回显的 API 通道；LINK 表分模式核验依赖库数据，仍在提交后的 job 内） | write | 同步 |
| `/imports/{job}` | GET | job 状态/结果（成功=model_id 句柄） | read | 轮询 |
| `/models/{id}` | GET | model_repo 元数据 + 报告（**不回吐张量**；imports 句柄的解引用面——《T1-04》隐含） | read | 同步 |
| `/models/{id}/view` | GET | **渲染投影**（`viz-json/v1`——服务端由 canonical model 投影出的渲染数据集：PDP 序列、角度散点、R 矩阵、栅格占用、taps 概要、CIR 引用；可视化台取数面，T2-11 ③/S2）。**canonical 本体不直出**——《T1-04》§7 冻结口径「canonical model 是内部契约，不直接暴露给 API 版本」：投影 schema 随 /v1 合同演进，canonical 变更由投影层吸收；投影是 REST 响应 shape（OpenAPI 内），非持久化格式、不入 M10 注册表 | read | 同步 |
| `/auth/me` | GET | 当前凭据自述 `{key_id, scope, expires, csrf_token}`——**HttpOnly Cookie 下前端无法解析令牌**，scope 门控与 CSRF 双提交 token（§4）的共同自举面（T2-11 §2）；`csrf_token` 仅 Cookie 会话下发（API-Key 调用无 CSRF 面，字段缺省） | 任一已认证 | 同步 |
| `/blobs/{ref}` | GET | 内容寻址 blob **只读流式**下载（CIR 播放预览、超限系数等引用取数面；经 M10 open_stream 直出不进内存） | read | 同步 |
| `/scenarios` | GET `?query=&tag=&level=` /POST | scenario_repo 列表/创建（version=1）。检索参数：`query`=名称模糊、`tag`=标签（T2-06 §2 Scenario.tags）、`level`=**服务端由 source 推导**（Mpdb→RT；Engine→按场景枚举映射 UMa/UMi/RMa/InH→GCM、CDL-x→CDL、TDL-x→TDL，**且 want_cir 置位时一律 TDL+CIR 徽标**——T2-03 §3 转换即 level=TDL/realization=CIR；ModelRef→模型元数据 level）——列表响应含该推导字段（①视图徽标数据源，GUI 不自行推导） | read/write | 同步 |
| `/scenarios/{id}/versions` | GET | **版本历史列表**（version / created_at / created_by / 变更摘要——①视图版本树数据源；`?version=` 单版读取仍走 `/scenarios/{id}`） | read | 同步 |
| `/scenarios/{id}` | GET(`?version=`)/PUT/DELETE | 读指定版；**PUT=创建新 version**（版本不可变，T2-06 §2）；DELETE=**归档**（被会话/审计引用，禁止物理删） | read/write | 同步 |
| `/sessions` | POST `{scenario_id, version, device_id?, backend}` | M6 create（锁定版本） | control | 同步 |
| `/sessions/{id}` | GET | 状态机态 + **allowed_ops**（M6 按 T2-06 §3 状态表裁决的当前态允许操作集——与 §3 的 409 扩展字段**同源同名**；GUI/客户端按钮可用性的前置驱动面，S1 缺口回馈产物：客户端不得自行由 state 推导）+ **current_op / completed_ops**（有界终局历史——客户端 wait 的 op 关联锚点，T2-06 §2，防 re-apply 旧态误判与续等挂死）+ reports + last_apply + **last_error**（异步失败的结构化错误，T2-06 §2——RESOLVE_FAILED 等终态的唯一定位来源，直译 §3 problem+json 扩展字段）+ tweaks | read | 轮询 |
| `/sessions/{id}/resolve` | POST | M6 `submit_resolve`（任务在 M6 运行器内，网关不持协程，T2-06 §4 提交面） | control | **202** |
| `/sessions/{id}/apply` | POST `?dry_run=` | M6 `submit_apply(auto_resolve=True)`——CREATED 态由 **M6 内部**先 resolve 再 apply；dry_run=true 走同步 `apply(dry_run=True)` 直返 manifest（**仅 READY/ACTIVE**：CREATED 时 M6 抛 InvalidState → 409 指明先 resolve——物化长耗时不得混入同步路径）。网关不检查状态、不编排、不持协程（单次 L3 调用） | control | 202（dry_run 同步返 manifest） |
| `/sessions/{id}/artifact` | GET `?channel=&format=` | **产物下载**（READY 起可用，M6 artifacts 缓存直出、零设备触达）：asc→全信道 zip 或 `?channel=i_o` 单文件 text/plain；rfsoc→帧序列 octet-stream 或 `?format=manifest` 摘要 JSON。CI 黄金对比与《T1-04》G4「同 scenario 产两种后端产物」的取回面（SDK `apply(out=...)` 即封装此端点落盘）；CREATED 态 → 409 指明先 resolve | read | 同步 |
| `/sessions/{id}/channels/{in}_{out}` | GET/PATCH | GET=artifact 参数视图+tweaks 叠加；PATCH=M6 tweak（T2-06 §4bis，仅 ACTIVE） | read/control | 同步 |
| `/channels` | GET/PATCH | **《T1-04》原路径兼容别名**：映射到当前唯一**持设备的 ACTIVE 会话**（backend=rfsoc）——asc 会话（device_id=None，写文件即 committed）不参与判定（tweak 本为 rfsoc 限定，T2-06 §4bis）；候选 0 个或 ≥2 个 → 409 指明改用嵌套路径 | 同上 | 同步 |
| `/sessions/{id}/close` | POST `{release}` | M6 close（DIRTY 强制 reset 由 M6 裁决，网关只透传） | control | 同步 |
| `/sessions/{id}/recover` | POST | M6 `submit_recover`（RESET+重 apply，任务在 M6 运行器内） | control | 202 |
| `/telemetry` | GET | M8 快照服务 | read | 同步 |
| `/telemetry/stream` | GET `?last_event_id=` | M8 订阅（SSE；`Last-Event-ID` 窗内续传，窗外流内首发 `resync` 事件指示快照重拉——EventSource 无非 200 语义）。**查询参数 `last_event_id` 与同名头等价**（头优先）——浏览器原生 EventSource 无法自设任意头：在线重连用头（浏览器自动），**回放游标用查询参数**（T2-11 ⑤ 历史回看的可实现面） | read | SSE |

- **长耗时异步化**（《T1-04》§3 约定）：202 响应体分两类——**会话操作**（resolve/apply/recover）返回 `{session_id, op_id, poll_url}`：`op_id` 来自 M6 提交面 OperationRef（T2-06 §4），`poll_url` 指向 GET `/sessions/{id}`（**Session.state 即进度**，不设独立任务端点）；**导入任务**（POST `/imports`）返回 `{job_id, poll_url}`：`poll_url` 指向 GET `/imports/{job_id}`（job 非会话，无 OperationRef）。同会话在途操作冲突 → M6 OperationInFlight → 409。
- **M8 依赖接口先行声明**（同 T2-03 定义引擎侧契约的做法）：M7 消费 `TelemetrySnapshot get_snapshot(device_id)` 与 `AsyncIterator[TelemetryEvent] subscribe(device_id, last_event_id?)`——具体语义 T2-08 为规范。

---

## 3. Schema 与错误模型

- 请求/响应 schema：pydantic v2 双向校验；**OpenAPI 自动生成即第三方合同**——`openapi.json` 黄金 diff 进 CI，破坏性变更必须升 `/v2`（《T1-04》§7）。
- 错误统一 **RFC 9457 problem+json**：`{type, title, status, detail, instance}` + 扩展字段 `{error_code, field_errors[], device_state?, holder_session_id?, allowed_ops?, current_version?}`。

| L3 异常（各模块已定义） | HTTP | 扩展字段 |
| :-- | :-- | :-- |
| 参数校验失败 / ScenarioError / CapabilityError | 422 | field_errors；能力差距+替代出口（M6 能力门原文透传） |
| DeviceBusy（租约被持有，T2-06） | 409 | holder_session_id |
| InvalidCloseError / 状态机非法迁移 | 409 | allowed_ops（当前态允许操作表） |
| scenario 版本冲突（repo 乐观锁） | 409 | current_version |
| 资源不存在 | 404 | —— |
| ImportError / EngineError（源侧数据错） | 422 | 源错误摘要（不泄内部路径，T2-03 §6 同口径） |
| 引擎/设备不可达（熔断 OPEN 含） | 503 + Retry-After | —— |
| DEVICE_DIRTY 态拒操作 | 409 | device_state="dirty"，detail 指明 recover |
| 未认证 / 越权 | 401 / 403 | 所需 scope |
| 限流 | 429 + Retry-After | —— |

---

## 4. 认证 / 鉴权 / 审计 / 限流（《T1-04》§6 落地）

- **认证**：API-Key（第三方，Header）＋短时会话令牌（GUI，HttpOnly Cookie 承载——T2-11 §2）。本地部署默认静态密钥表（M10 配置承载）；OIDC/IdP 为可插拔后续项（商用部署）。**Cookie 会话的 CSRF 防护**：Cookie 置 `HttpOnly`+`Secure`+`SameSite=Strict`（同源部署、无跨站合法场景），且网关对一切**非安全方法**（POST/PATCH/DELETE）校验 `X-CSRF-Token` 头——token 经 `/auth/me` 下发（双提交模式），缺失/不符 → 403；API-Key 头认证的调用不受此约束（非环境凭据、无 CSRF 面）。
- **鉴权三档 scope**（递进包含）：
  `read`（遥测/场景/模型/会话状态 GET）⊂ `write`（imports/scenarios 写）⊂ `control`（sessions 的 apply/tweak/close/recover 及一切**触达设备**操作）。
- **审计中间件**：control 域每次调用（**含失败与被拒**）→ `AuditRecord{key_id?, when, op, session_id?, manifest_digest?, outcome}` → M10 append-only。三个可空字段的语义：`key_id?`——未认证或认证失败的调用无已解析 key，为空并在 outcome 细节记拒因与来源标识（remote addr/连接 id）；`session_id?`——会话尚不存在或创建被拒的调用（如未授权 `POST /sessions`）为空；`manifest_digest?`——仅**产物就绪的设备触达操作**（apply / tweak / close 微帧）填写，resolve 提交与创建类为空（此时无产物可摘要）。**同步操作记终局 outcome；异步提交记「受理」（含 op_id）**——终局结果由 M6 任务运行器在任务完成时写入会话审计（T2-06 §2：审计域=会话生命周期全记录，**含 resolve 这类非设备触达任务的终局**），网关**不依赖后续轮询闭合审计**；GET 类请求纯读、零审计写。与 M6 会话内审计**互补不重复**：网关记「谁经哪个门做了什么请求」，M6 记「会话/设备上实际发生了什么」。
- **限流**：per-key 令牌桶（read/write/control 分桶）；apply 类在 M6 设备租约处天然串行——网关**不做隐式排队**（同 M6 语义），队列深度超限直接 429。

---

## 5. SCPI-over-TCP 兼容层（P4，契约先行）

- 传输：TCP 行协议；**一连接 = 一隐式会话上下文**（绑定默认设备，适配仪器测试台单机习惯）；IEEE 488.2 最小集。多会话/多设备编排**不覆盖**——走 REST/SDK。
- **认证（SCPI 同为外部信任边界，《T1-04》§6 无豁免）**：连接建立即处于**未认证态**，仅允许 `*IDN?`、`:SYSTem:ERRor?` 与 `:SYSTem:AUTH "api-key"`；`AUTH` 校验通过后连接绑定该 key 的 scope（与 REST 同一密钥表与三档 scope，§4）——scope 不足的指令按 SCPI 惯例错误入队列。未认证态下发其他指令 → 错误入队列、不断连。内网免认证为部署级 feature-flag（默认关，与 REST 同栈裁决）。
- 指令表（每条转译为与 REST 相同的 L3 调用，零逻辑分叉）：

| SCPI | 语义 | L3 |
| :-- | :-- | :-- |
| `*IDN?` | 平台/设备标识 | version + registry |
| `*CLS` / `:SYSTem:ERRor?` | 错误队列清除 / FIFO 弹出 | 网关侧队列（problem 摘要按 SCPI 习惯入队） |
| `:SYSTem:AUTH "api-key"` | 连接认证（绑定 key 的 scope；未认证态唯三合法指令之一） | 认证栈（与 REST 同密钥表） |
| `*OPC?` | 操作完成同步 | 会话终态轮询封装 |
| `:SCENario:LOAD "scenario_id[@version]"` | 按**稳定标识**加载（缺省 @version=最新）——`name` 非唯一身份（T2-06 §2 仅 id+version 稳定），不作 LOAD 键；按名检索走 REST/SDK | scenario_repo |
| `:SESSion:BACKend RFSOC\|ASC` | 绑定后端（隐式会话） | M6 create |
| `:SESSion:APPLy` / `:SESSion:STATe?` | 下发 / 状态查询 | M6 `submit_apply(auto_resolve=True)`（与 REST 同一调用；`*OPC?` 轮询终态）/ get |
| `:SESSion:CLOSe [DISable\|RESet\|LEAVe]` | 关闭（缺省 DISable；DIRTY 强制 RESet 由 M6 裁决） | M6 close |
| `:TELemetry:OUTPut:POWer? (@n)` 等 | 遥测查询 | M8 快照 |

- 错误处理遵 SCPI 惯例：错误不中断连接、入队列待 `:SYST:ERR?` 读取；越权/设备错误与 REST 同一 L3 异常栈映射。
- P4 feature-flag 启用；本篇先行冻结指令契约，供 M9/T3 提前规划测试资产。

---

## 6. 时序图（复合 apply 与 SSE）

```
第三方/SDK        M7(REST)              M6                    M8
  │ POST /sessions/{id}/apply
  │──────────────►│ 鉴权(control) + 审计(begin)
  │                │── submit_apply(auto_resolve=True) ─►│ 入 M6 任务运行器（网关不持协程）：
  │  202 {op_id,   │◄─ OperationRef ─────────────────────│  CREATED?→先 resolve 再 apply → M2 事务
  │    poll_url}   │                                     │
  │◄───────────────│                                     │
  │ GET /sessions/{id}（轮询——纯读，零审计写）         │
  │──────────────►│── get() ────────────────────────►│
  │  ACTIVE + 报告 │◄─────────────────────────────────│
  │◄───────────────│ ※异步操作终局审计＝M6 运行器在任务完成时写会话审计；
  │                │   网关审计只记「受理 + op_id」（不依赖轮询闭合）
  │
  │ GET /telemetry/stream (SSE, Last-Event-ID?)
  │──────────────►│── subscribe(device, last_id) ──────────────────────►│
  │ ◄─事件流（心跳/电平/溢出告警）────────────────────────────────────────│
  │  （断线）重连带 Last-Event-ID → M8 缓冲窗内续传；窗外：仍 200 开流，
  │   首发 `event: resync`（EventSource 收不到非 200 语义）指示先 GET /telemetry
  │   取全量快照，随后自当前位置续流
```

---

## 7. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **契约（stub L3）** | 全端点 happy + 全错误分类 | 状态码与 problem+json 逐字段正确 |
| **OpenAPI 黄金** | `openapi.json` diff | 破坏性变更被 CI 拦截 |
| **鉴权矩阵** | 3 scope × 全端点（含 SSE、SCPI） | 401/403 无遗漏、错误含所需 scope |
| **审计完备** | control 域全操作 × 成功/失败/被拒；异步含「提交后不轮询」剧本 | 同步非触达：非会话操作（scenario 写等）=网关恰一条含 outcome；会话生命周期类（close(leave)/asc close）=网关 1 条 + 会话终局 1 条（T2-06 §3、T2-10 §6 ②）；**同步设备触达（tweak / close 微帧）=网关 1 条含 outcome + 会话侧 begin/end 成对**（T2-06 §4bis tweak 与 §3 close 的 begin/end 包裹）；异步设备触达（apply/recover）=网关受理 1 条 + 会话侧 begin/end **成对**（op_id 关联、「每 begin 恰一 end」不变式，无轮询也闭合——T2-10 §3 append-only 语义）；**异步非触达（resolve）=网关受理 1 条 + 会话终局 1 条**（无 begin 前置，T2-10 §6 ②）；GET 零审计写 |
| **限流** | 突发+持续超额 | 429 + Retry-After；不影响其他 key |
| **复合 apply 透传** | CREATED 态直接 apply | 网关仅发**一次** L3 调用 `submit_apply(auto_resolve=True)` 即返 202；状态检查与任务承载均在 M6（stub 断言零状态读取、网关无协程持有） |
| **/channels 别名** | 0/1/2 个 ACTIVE 会话三剧本 | 唯一时等价嵌套路径；否则 409 指明嵌套路径 |
| **SSE** | 断线重连（窗内/窗外）、心跳 | 窗内 Last-Event-ID 续传正确；窗外首事件为 `resync` 且后续事件自当前位置连续 |
| **SCPI 黄金** | 指令表全集 + 错误队列剧本 | 响应逐字节；与 REST 同输入同 L3 结果 |
| **产物下载** | asc 全信道 zip / `?channel` 单文件；rfsoc 帧流 / manifest；CREATED 态请求 | 内容与 M6 artifacts 逐字节一致；CREATED → 409 指明先 resolve；零设备触达 |
| **dry-run 透传** | `apply?dry_run=true` | 同步返 manifest；传输层零调用（M6 保证的网关回归） |

---

## 8. 与现有代码的差量

全新 L4 模块：FastAPI app（REST+SSE）与 SCPI adapter（asyncio TCP server）进程内共存、共享 L3 服务实例。现 `channel_simulator` CLI 不经网关；M9 后 CLI 收敛为 SDK 薄壳。

---

## 9. 开放问题

1. SSE 是否长期足够，或需 gRPC 双向流（《T1-04》§8-1 遗留）——SSE 先行，M8 订阅接口不锁死传输形态，P2 复评。
2. OIDC/多租户配额与隔离（商用部署项；《T1-04》§8-2 与 M6 租约协同已解本期并发问题）。
3. SCPI 最小子集边界随测试台真实需求收敛（《T1-04》§8-3）。
4. `/devices` POST/DELETE 启用（P4）时 control scope 是否再细分设备管理权限。

---

## 10. 本篇验收

- OpenAPI 黄金契约测试全绿；鉴权矩阵与审计完备性测试全绿。
- REST 资源对照《T1-02》UC1–UC8 逐用例可走通（映射表核查）。
- SCPI 契约表评审通过（实现排 P4）；SSE 遥测流在 HIL 冒烟跑通。
- 三前端一致性：同一 L3 stub 下，REST 与 SCPI 对同语义操作产生相同 L3 调用序（录制断言）。
