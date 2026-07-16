# T2-11 · M11 GUI（功能设计）

> 第二册《功能设计》· 第 11 篇（L5 Web 前端：五视图细化 · 交互↔API 映射 · 前端零业务逻辑）
> 状态：草稿 v0.1 · 待评审
> 依据：《T1-14 GUI 顶层概念》（冻结基线：Web 已定档、五视图、三立场 S1/S2/S3）《T1-02 用例》《T1-04 API 策略》
> 消费方：信道工程师（主）、RT/算法研究者（次）；依赖：M7（REST `/v1`——**运行时唯一上游**）、M9（语义对照，见 §2）

---

## 1. 概述与定位

M11 把《T1-14》的五视图概念细化到「界面结构 + 交互→API 映射 + 状态与错误呈现」。三条顶层立场的落地方式：

- **S1（API 第一客户）**：本篇每个交互都带一行「→ API」映射；映射不到的交互 = API 缺口，回馈 T2-07 修订，不在前端绕行。
- **S2（可视化围绕 canonical model）**：③可视化台全部从 schema 字段渲染（含物化的 `correlation.r*`）——**dry-run 无设备也能完整预览**是本篇验收项。
- **S3（层级入口即导航）**：②信道编辑器按 RT/GCM/CDL/TDL 分入口（《T1-03b》退化链的交互化）。

**前端零业务逻辑**（与 M7 零业务逻辑对偶）：GUI 不复制状态机、不本地校验业务规则（表单格式校验除外）——按钮可用性由会话 GET 的 `allowed_ops` 驱动（该字段为本篇 **S1 回路回馈 T2-07 新增**：M6 按 T2-06 §3 状态表裁决、含 tweak 的 rfsoc 限定等后端条件，网关序列化输出——GUI 得以前置灰置而非等 409），错误呈现直译 problem+json。

**非职责**：API 定义（M7）、编排（M6）、SDK（M9）、设备直连（禁止，一切经 L4）。

---

## 2. 技术选型与运行形态

| 项 | 选型 | 理由 |
| :-- | :-- | :-- |
| 框架 | **React + TypeScript + Vite** | 生态最广、可视化组件充沛；静态产物由平台 FastAPI 直接服务（《T1-14》§4 零安装） |
| 组件库 | Ant Design（中文文档/表格编辑生态强）| 定表编辑器、向导、抽屉等重型组件开箱 |
| 图表 | ECharts | PDP/角度谱/热图/实时曲线一库覆盖；SVG 自绘仅 8×8 栅格拓扑图 |
| API 层 | **OpenAPI 生成 TS 类型 + TanStack Query** | 类型与 M7 黄金合同同源——合同 diff 破坏性变更时前端编译期即红（S1 的工程化） |
| 遥测 | 原生 EventSource | `Last-Event-ID` 重连、`resync` 事件→自动重拉快照后续流（T2-07 §2 语义，连接层吸收，视图只见 snapshot/alarm/advice） |
| 鉴权 | 短时会话令牌（M7 §4），**HttpOnly Cookie 承载** | 同源部署（静态资源由平台 FastAPI 直服）——REST 与 SSE 同一凭据：**原生 EventSource 无法自设 Authorization 头**，Cookie 随请求自动携带（SSE 鉴权的唯一可行载体）；scope 随令牌下发：**read 令牌隐藏一切 control 操作入口**（菜单级），服务端仍兜底 403 |

- **与 M9 的关系**：浏览器无 Python，GUI **不经 SDK**、直接消费 REST；M9 是「语义对照物」——三前端一致性测试录制 GUI 与 SDK 对同语义操作的 HTTP 调用序并断言等价（§6）。

---

## 3. 五视图细化（交互 → API 映射为规范）

### ① 场景库（UC7）

| 交互 | → API | 呈现要点 |
| :-- | :-- | :-- |
| 列表/检索（名称、标签、层级） | GET `/scenarios?query=&tag=&level=`（T2-07——tags 为 T2-06 Scenario 字段、level 由服务端从 source 推导，GUI 不自行推导） | 卡片含 level 徽标（RT/GCM/CDL/TDL）与最新 version |
| 版本历史 | GET `/scenarios/{id}/versions`（版本树数据源）+ GET `/scenarios/{id}?version=`（单版详情） | **版本树只读**——「编辑」按钮文案即『另存新版本』（不可变语义前置到 UI 认知） |
| 编辑保存 | PUT `/scenarios/{id}`（=创建新 version） | 并发创建冲突 409(current_version) → 提示刷新对比后重存（乐观锁直译） |
| 归档 | DELETE（归档非物理删） | 被会话/审计引用的提示语（不可删的原因可见） |
| 任一版本重放 | 跳转④以 scenario@version 建会话 | 「一次下发=版本+后端+设备」的入口（《T1-11》§5） |

### ② 信道编辑器（UC1/UC2，S3 分层入口）

**RT 入口 = MPDB 导入向导（四步——配置先于映射编辑：V1–V7 的判定上下文须先在场，T2-04 §3.2 不得隐式读取）**：
1. 选库（服务端路径/上传经 M10 I/O）；
2. **导入配置**——`test_mode`（conducted/OTA，**前置采集**：决定步骤 3 的 PortMap 键型与 V4）、目标后端（rfsoc/asc→其 capabilities 供 V6）、拓扑（V3）、`path_expansion`（V5）、`identity_by`/`frame`（position 模式强制 world 的提示）/容差；
3. **阵列与 PortMap 编辑器**——阵元表 + 端口映射矩阵；OTA 时键型切 (element, pol)（test_mode 已于步骤 2 采集）；V1–V7 校验**即时回显并定位到冲突项**：→ POST `/imports/validate`（同步预检端点，T2-07——复用 M4 纯函数、直译 field_errors，**不前端复算**）；LINK 表分模式核验依赖库数据，不在此步（提交后 job 内执行，失败经 job 状态展示）；
4. 预览 + 提交 → POST `/imports`（`kind=mpdb`，202）→ 进度条轮询 GET `/imports/{job}` → 成功得 model 句柄 → 引导创建 scenario（ModelRefSource）。ImportReport（丢弃计数/孤儿径）全量展示。

**GCM 入口 = 38.901 参数面板**：场景枚举/fc/带宽/**geometry（统计场景必填，表单必填校验与 client 预检同源字段）**/mobility/lsp_mode/**seed 显式可见**（确定性契约的用户认知面）；delay_spread_s 仅 CDL/TDL-x 显示；**tx/rx 阵列 + PortMap 配置区**（GenerateRequest.arrays 无条件必填、EngineSource 携 portmap——复用 RT 向导步骤 3 的编辑器组件与 `/imports/validate` 预检，同源直译）；`want_cir`（A 档时变 CIR）按目标设备 capabilities 灰置（当前 RF-SoC 不支持、asc 出口可用——通用 fader 原则）。→ 组装 EngineSource 存 scenario（提交时 client_key 由服务端注入，UI 不感知）。

**CDL/TDL 入口 = 定表表格编辑器**：3GPP 定表 JSON（`cdl-tdl-table-json/v1`，T2-10）行编辑；**物化参数区**（随提交传入、不入表载荷）：`delay_spread_s`（**必填**——表时延为归一化 delay_norm，×RMS 时延扩展得物理 delay_s，与引擎路径同一缩放语义）、`phase_seed`（TDL 初相种子，默认 0）；**CDL 定表另需 arrays + PortMap 配置区**（复用 RT 向导步骤 3 组件与 `/imports/validate` 预检——物化时由 reader 写入 `meta.arrays` 与 `provenance.import_config["portmap"]`（M4/M3 的同一落位——冻结 Provenance 仅有 import_config），与 M4/M3 对称：**M5 退化前置校验要求 provenance 携 portmap、簇伪径导向需要阵列几何**，缺则 resolve 必拒）；**TDL 定表无需 arrays/PortMap**（无阵列语义）**但必须采集栅格拓扑 + 目标信道对**——拓扑（8×8/4×8/2×8/1×8，默认 8×8——`grid.topology` 为 schema 必填、信道对本身推不出拓扑）与 (in,out) 多选（默认 (0,0)，**须 ⊆ 所选拓扑的有效对**——同 V3 相容语义即时校验）：level=TDL **不经 M5**（T2-06 仅对 RT/GCM/CDL 调退化）直通 M2 渲染，渲染按信道对键取 taps——物化时 taps 落 `channels[(in,out)]`、`grid`（topology+channel_pairs）同步（无键则 taps 无处落）→ 提交 POST `/imports`（`kind=cdl_tdl_table`，202，T2-07——定表直录的 API 落点）→ model 句柄 → scenario（ModelRefSource）。**无相位列的提示条分两支**（T2-10 注册表口径）：CDL 定表→经 M5 退化时按 `cluster_phase_seed` 兜底（T2-05 §3 ③）；TDL 定表→直通不经 M5，由 `cdl_tdl_reader` 物化时按**导入请求参数** `phase_seed`（默认 0，随提交传入——不入表载荷，表 schema 冻结零自有字段，T2-10）确定性合成 Tap 初相——两支都把兜底行为告知用户而非静默。

**三入口的共同末步 = 创建 scenario 的「退化参数」面板**：source 物化层级 ∈ {RT, GCM, CDL} 时 **`Scenario.synthesis` 必填**（T2-06 §2——缺则 resolve 期 ScenarioError）——SynthesisConfig 全字段采集：`power_mode`（coherent/noncoherent）、`max_paths`（≤ 目标 capabilities.max_paths，超限即时提示）、`velocity_mps`（可选几何多普勒）、`rayleigh` 谱配置（可选）、`cluster_phase_seed`（CDL 相位兜底种子，默认 0）；**TDL 直通 / CIR 源不显示该面板**（synthesis 可 None）。→ POST `/scenarios`。

### ③ 可视化台（UC2/UC3，S2——全部渲染自 canonical model）

**取数面**：→ GET `/models/{id}/payload`（完整 canonical 载荷，T2-07——本篇 S1 回路回馈新增：`/models/{id}` 元数据端点明文不回吐张量，渲染必须有独立载荷面）；CIR 播放等引用型数据 → GET `/blobs/{ref}`（流式）。model 句柄来自 scenario（source/物化结果）或会话（`ResolvedArtifacts.model_id`）。

| 图 | 数据源（schema 字段） |
| :-- | :-- |
| PDP 功率时延谱 | `links[].rays[]` / `channels[].taps[]`（delay, |gain|²） |
| 角度谱（方位/天顶散点） | rays/clusters 的 aoa/aod（天顶角坐标轴标注） |
| **R 热图**（R_tx/R_rx/R） | `correlation.r_tx/r_rx/r`（物化字段，S2 注明来源=角度+几何可复算） |
| 8×8 栅格拓扑图 | `grid.topology`+`grid.channel_pairs`+portmap（占用/映射/扩展消耗着色） |
| FidelityReport 面板 | **会话 reports**（GET `/sessions/{id}` 的 ResolvedArtifacts.reports，字段定义见 T2-05 FidelityReport——**非 schema 字段**，属报告透传、不在 S2 黄金快照范围）：`frobenius_rel_err`、量化丢弃计数、link_mode 模式注记（per_element_pair=几何真值 / single_reference=远场近似——诚实边界原文展示） |
| 时变 CIR 播放预览（A 档） | `gain_series`/`cir_ref`——**按设备 capabilities 灰置**（通用 fader 原则：界面保留、当前设备不支持则禁用并注明） |

### ④ 下发面板（UC3/UC4/UC8）

| 交互 | → API | 呈现要点 |
| :-- | :-- | :-- |
| 会话创建 | POST `/sessions` | scenario@version × device × backend（asc 可无设备） |
| 状态机可视化 | GET `/sessions/{id}` 轮询 | T2-06 §3 全态横条；**按钮可用性= `allowed_ops` 直译**（前端不复制状态机） |
| resolve / 进度 | POST `/sessions/{id}/resolve`（202） | RESOLVING 进度 + 失败时 `last_error` problem 字段定位展示 |
| dry-run 预览 | POST `/sessions/{id}/apply?dry_run=true` | rfsoc=帧摘要表（manifest）；asc=文件预览；产物下载→GET `/sessions/{id}/artifact`（zip/单信道） |
| apply / 事务进度 | POST `/sessions/{id}/apply`（202）+ GET `/sessions/{id}` 轮询 | **以 202 返回的 op_id 对齐 `completed_ops`**（同 SDK wait 纪律，防 re-apply 旧态误读）；rolled_back → 展示 OpRecord.result.failure（首个失败帧/原因） |
| DEVICE_DIRTY | —— | 醒目横幅 + **recover 单一引导**（allowed_ops 只剩 recover/close(reset)——UI 不给第二条路） |
| tweak 微调抽屉 | PATCH `/sessions/{id}/channels/{in}_{out}` | **仅 ACTIVE 且 backend=rfsoc** 可开（asc 无「运行中设备」语义，T2-06 §4bis——该条件已折算进 allowed_ops，此处为语义注记）；逐径物理量滑条/输入；tweaks 列表展示（committed 序列=重放语义提示；re-apply/recover 会清空的说明） |
| close | POST `/sessions/{id}/close` | 三策略选择；DIRTY 态仅 reset 可选（服务端拒的前置呈现） |
| 设备管理子视图（UC8，四项齐备、整体随 **P4 flag** 启用） | `/devices` (+POST/DELETE P4) | ①健康列表（readback）；②注册/注销（P4 端点）；③**跨设备会话编排**——多设备会话创建矩阵（依赖《T1-10》P4 多设备会话，本期单设备、UI 隐藏）；④**`(device_id,in,out,path)` 寻址**——④面板的信道/微调视图在多设备启用后前缀 device 维度（T1-14 §3 四项完整承载；映射不到的编排端点按 S1 回馈 T2-07 的 P4 修订） |

### ⑤ 遥测仪表盘（UC5）

- SSE 订阅 `/telemetry/stream`：视图消费 **snapshot / alarm / advice** 三类事件；heartbeat 由连接层吸收；**resync 的吸收限定在线订阅路径**（断线重连/慢消费——自动 GET `/telemetry` 重拉后续流），主动回看路径的 resync 处理见「历史」条。
- 实时曲线：8 输入电平/功率、8 输出含噪/无噪功率与电平；ADC 过载/合路/AWGN 溢出**位图逐位定位端口**；告警条呈现迟滞状态（raised/cleared，不闪烁风暴——服务端已迟滞，前端不再加逻辑）。
- advice 卡片（如溢出建议衰减 X dB）：**只展示建议**；「去微调」跳转④ tweak 抽屉（control 权限可见时）——动作永远是人发起。
- 历史回看 → API：**另开一路** GET `/telemetry/stream?last_event_id={目标}` 读至追平——用**查询参数**传游标（T2-07 定义其与 `Last-Event-ID` 头等价；浏览器原生 EventSource 无法自设任意头，头仅由浏览器在断线重连时自动携带）；该路首事件为 resync（=目标已出缓冲窗）时，连接层**吸收事件但向视图上报「超出缓冲窗」状态**（区别于在线订阅的静默吸收——否则该提示永无触发路径）。

---

## 4. 横切呈现约定

- **错误**：problem+json 直译——`field_errors` 定位到表单项；`DeviceBusy` 附持有会话跳转链接；`allowed_ops` 附当前态可用操作；429/503 的 `Retry-After` 转倒计时提示。
- **权限**：read/write/control 三档驱动菜单与按钮可见性（服务端 403 兜底；GUI 隐藏仅是体验层）。
- **审计视图**：会话详情页展示服务端会话审计（begin/end 配对、终局），GUI 不自造记录。
- **长任务**：所有 202 交互统一「进度条 + 可离开页面（任务在服务端跑，回来继续轮询）」——与 SDK WaitTimeout 可续等同语义。

---

## 5. S1 反向验收（GUI ↔ API 缺口回路）

- 本篇 §3 的「交互→API」映射表 = 验收清单：每个交互必须映射到 T2-07 既有端点；开发中出现映射不到的交互 → **先改 T2-07（API 缺口）再实现**，不允许前端私解。
- UC 覆盖：UC1–UC5、UC7、UC8（P4）对照《T1-14》§7——UC6（第三方纯 API）显式不经 GUI。

---

## 6. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **契约 mock**（msw 按 OpenAPI） | 全交互 happy + problem 全分类 | 请求与 OpenAPI 逐字段一致；错误呈现直译（field_errors 定位断言） |
| **E2E（Playwright）** | **UC2 旗舰全链** ②导入→③PDP/R 热图→④dry-run→apply→⑤遥测确认（stub 网关） | 全链通过；每步 HTTP 调用序录制 |
| **三前端一致性** | 同语义操作 GUI vs SDK 调用序对比 | 逐调用等价（端点/参数），S1 验收 |
| **allowed_ops 驱动** | 全状态 stub 会话 | 按钮可用性与 allowed_ops 完全一致；前端无独立状态判断（代码审查断言） |
| **可视化黄金** | 固定 canonical model → PDP/R 热图/栅格图快照 | 像素/结构快照对比；dry-run 无设备完整渲染（S2 验收） |
| **SSE 剧本** | 断线重连（窗内/窗外 resync）、告警 raised/cleared、慢消费 | 曲线无缝/自动重拉；告警不闪烁 |
| **权限矩阵** | read/write/control 三档令牌 | 菜单/按钮可见性正确；403 兜底路径呈现 |
| **op 对齐** | 已 ACTIVE 会话 re-apply | 进度以 op_id 对齐 completed_ops，不因旧 ACTIVE 即示成功 |

---

## 7. 与现有代码的差量

全新 `web/` 前端包（独立 Vite 工程，产物由 FastAPI 静态服务）；ChannelEgine 的 `gui.py` **不复用**（《T1-14》已定）。

---

## 8. 开放问题

1. 多用户并发编辑同一场景：本期乐观锁+409 刷新提示（《T1-04》§8-2）；实时协同（锁/在线状态）商用复评。
2. 遥测高频渲染节流参数（帧率/降采样）随真机数据率实测定。
3. A 档 CIR 播放预览的渲染方案（抽头动画 vs 瀑布图）随 capabilities 启用时细化。
4. 组件库主题定制与品牌视觉（商用包装项）。

---

## 9. 本篇验收

- §3 交互→API 映射表 100% 命中 T2-07 既有端点（缺口清单为零或已回馈 T2-07）。
- UC2 旗舰 E2E 全链通过（stub 网关）；dry-run 无设备完整预览（S2）。
- 三前端一致性录制断言通过（S1）；allowed_ops 驱动测试通过（前端零业务逻辑）。
- 五视图 × UC 覆盖表对照《T1-14》§7 全绿。
