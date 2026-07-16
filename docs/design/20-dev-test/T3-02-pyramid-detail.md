# T3-02 · 测试金字塔详规（L1–L4 用例矩阵）

> 第三册《测试设计》· 第 2 篇（L1–L4 的用例面：收编 + 补缺 + 负向矩阵）
> 状态：草稿 v0.1 · 待评审
> 依据：《T3-00》§3 金字塔/§5-B 义务/§5-E 缺口/原则 4；《T3-01》ID 规范与矩阵载体；冻结基线 `design-t1-v1.0`（T1-03c 升版 v1.2）/`design-t2-v1.0`
> 消费方：追溯矩阵 `obligations.yaml` 初版（本篇 ID 分配即其种子）；P0–P2 各实现 PR 的用例义务来源

---

## 1. 概述与定位

本篇给 L1–L4 四层配齐**用例矩阵**，三类内容边界分明：

1. **收编**（§2/§4/§5 大部）：T2 各模块测试节已冻结的用例——本篇**只分配用例 ID 与条款映射，不复述判据**（判据以 T2 原文为准，防双源漂移；T2 判据变更须走升版并联动矩阵，《T3-00》§4）；
2. **补缺**（§3）：《T3-00》§5-E 盘出的五条缺口——本篇按 T2 同款「类别/内容/判据」三列风格**新增设计**；
3. **负向矩阵**（§6）：原则 4 的横切落地——全库「拒绝不猜/不静默」条款的负例总账。

**非职责**：L5 HIL 用例（→T3-03）、黄金资产逐项登记（→T3-04）、阈值数值与 gate 编排（→T3-05）。

---

## 2. L1/L2 收编规约（T2 测试节 → 矩阵 ID）

**收编规则**：每模块测试节的表行按**行序**编号入库——`U/C-M<n>-<行序三位>`；行内含多判据的可拆子 ID（`-001a` 不允许——拆行即拆 ID，序号顺延）。层归属按表行性质判定：纯函数/零 I/O 行入 `U`，消费 fakes/黄金 fixture/跨模块接口行入 `C`。

| 模块 | 测试节 | ID 段 | 备注 |
| :-- | :-- | :-- | :-- |
| M1 协议编解码 | T2-01 §8 | U-M1-001+ / C-M1-001+ | 黄金帧行归 C（消费 golden fixture）；回归基线（`test_protocol.py` 全绿不动）单列 U-M1-090 |
| M2 设备后端 | T2-02 §8 | U-M2-001+ / C-M2-001+ | 切分属性（hypothesis）入 U；假设备六剧本入 C；环回集成行升 L3（I-ORCH-001，§4） |
| M3 引擎集成 | T2-03 §7 | U-M3-001+ / C-M3-001+ | 假引擎契约/熔断/版本协商入 C；确定性/CIR 附着入 U；隔离性行升 L3（§4） |
| M4 MPDB 导入 | T2-04 §7 | U-M4-001+ / C-M4-001+ | reader 契约（合成 fixture）入 C；PortMap V1–V7/量化合并入 U |
| M5 相关性合成 | T2-05 §8 | U-M5-001+ / C-M5-001+ | 黄金几何/顺序纪律/共享基准入 U（解析对照）；A 档守卫入 C |
| M6 场景会话 | T2-06 §7 | C-M6-001+ | 全桩编排——整节属 L2/L3 界面，状态机矩阵入 C，编排链行升 L3（§4） |
| M7 API 网关 | T2-07 §7 | C-M7-001+ | stub L3 契约/鉴权矩阵/审计矩阵入 C；SSE/三前端一致性升 L4（§5） |
| M8 遥测校准 | T2-08 §7 | U-M8-001+ / C-M8-001+ | 谱归一/迟滞/电平指引入 U；黄金快照/续传入 C |
| M9 SDK | T2-09 §7 | C-M9-001+ | problem fixture 双向/wait 状态机/OpenAPI 黄金 diff |
| M10 格式持久化 | T2-10 §8 | U-M10-001+ / C-M10-001+ | codec 黄金/迁移矩阵入 C；10MB 判定/model_id 规范化入 U |
| M11 GUI | T2-11 §6 | C-M11-001+ | 组件级入 C；UC2 E2E（Playwright）升 L4（§5） |

- 收编时逐行核对判据仍与冻结文原文一致（抄录即校对）；发现表行与正文冲突按《T3-00》原则 1 处理（先升版后改矩阵）。

---

## 3. 补缺用例（《T3-00》§5-E 五条，本篇新增设计）

| ID | 类别 | 内容 | 判据 |
| :-- | :-- | :-- | :-- |
| C-M2-101 | apply_micro 契约 | 假设备上走 tweak 微帧：echo 正常/篡改/超时三剧本 | 失败分流同 apply（committed/rolled_back/dirty，T2-06 §4bis 三剧本的传输层承载）；微帧序列**不含 RESET、不动使能、不发 ID33**（字节级断言，B+ 前提《T1-15》§7-1） |
| C-M2-102 | apply_micro 能力门 | asc 后端收到 apply_micro | 显式拒（无设备语义），零副作用 |
| C-M2-103 | 遥测捕获窗竞态 | 单次遥测请求（ID14=0x03）发出后，周期遥测帧**先于**请求回显到达 | 归属判定正确（按 T2-02 §5 竞态规则），无错配快照；判定规则待设备实测确认前用例标 `building` |
| C-M2-104 | cadence-restore 异常 | close(disable)/tweak 后恢复遥测周期档的 echo 失败 | 不回滚已提交配置；活性标记「待观察」；告警可见（T2-02 §4 log 路径行为化） |
| U-M1-101 | 0x50 透传帧解析 | `SerialPassthroughFrame` 变长 1/255B 边界、任意切片流式喂入、与遥测/回显帧混流 | 帧不丢不粘；超长/零长畸形→重同步且 stats 正确（与 §8 流式解析行同判据面） |
| C-M2-105 | 传输层活性 degraded | 非事务期停喂遥测 > N 周期 | 传输层判 degraded（T2-02 §5）；恢复喂帧后清除；与 M8 device_silent（C-M8 段）各自独立触发 |
| C-M2-106 | 非事务期断连重连 | ACTIVE 静置期 TCP 断连→自动重连成功 | 会话/设备状态判 dirty（重连后须 RESET 才可信，T2-02 §5 语义）；不静默恢复「可信」 |
| C-M2-110+ | 事务失败注入二维矩阵 | **阶段维**（APPLYING 第 k 帧 echo 失败 / VERIFYING 遥测请求帧 echo 失败 `telemetry_req` / 遥测等待超时 `telemetry_wait` / 电平不合理 `_levels_plausible` 判负 / 回滚中再失败）× **位置维**（首帧/中间帧/尾帧/单帧计划） | 每格：终态正确（rolled_back/dirty）、`FailureInfo` 定位到（阶段, 帧序）、可达时回滚必发 RESET、回滚不可达置 dirty——覆盖六剧本（失败类型维）之外的**注入位置维**（T3-00 §5-E-5 差集） |

---

## 4. L3 集成用例矩阵（进程内多模块 + TCP 环回）

| ID | 内容 | 出处/性质 |
| :-- | :-- | :-- |
| I-ORCH-001 | TCP 环回假设备全链 render→apply→commit | 收编 T2-02 §8「环回集成」（HIL-C 软前身，剧本与 L5 共用只换对端） |
| I-ORCH-002 | 三源编排全桩（Mpdb/Engine/ModelRef 各一条 happy path，调用序①materialize→②reduce→③能力门→④render） | 收编 T2-06 §7 |
| I-ORCH-003 | 状态机全矩阵 + 互斥/重启恢复/close 三策略/tweak 分流 | 收编 T2-06 §7（多行，行序分配 I-ORCH-003…） |
| I-ORCH-010 | **数据面全链（新增）**：合成小 MPDB fixture → M4 导入(RT) → M5 导向/塌缩/选径(TDL+R) → M2 render(FramePlan) | 三层断言：taps 对解析预期（T2-05 黄金几何量级）、R 复现 err<1e-10（理想 fixture）、帧序列可被 M1 解码回物理量且往返≤半步长——把各模块 happy path **串成一条**，专抓接缝（单位/角度约定/归一化基准传递） |
| I-ORCH-011 | **统计链全链（新增）**：假引擎 CDL-A → M3 转换 → M5 退化 → M2-asc 渲染 `.asc` | `.asc` 对黄金样例格式一致；簇初相经 phase_rad 一等字段直读（v1.1 路径） |
| I-ORCH-012 | 隔离性：引擎 stub 宕机时跑 I-ORCH-010 | 设备链路零影响（收编 T2-03 §7 隔离行，落到全链语境） |
| I-MIG-001 | v1.0 样本迁移链：v1.0 model-json → M10 钩子 → v1.1 → M5 消费 | 收编 T2-10 §8/§10（黄金矩阵按来源分支）；迁移后模型可被正常退化消费（不止字段对，还能用） |

---

## 5. L4 系统用例矩阵（UC 驱动，REST 起全栈）

以《T1-02》UC1–UC8 为骨架（S-UC 段），栈形态：真 M7/M6/L3 + SQLite + 假设备（真引擎可切，CI 默认假引擎）：

| ID | UC | 走通判据 |
| :-- | :-- | :-- |
| S-UC1-001 | UC1 统计信道下发 | REST：场景选择→生成→apply→ACTIVE；审计完备 |
| S-UC2-001 | UC2 旗舰 RT 播放 | 导入 MPDB→resolve→dry-run→apply→遥测确认（对应 T2-11 §6 E2E 的后端面；GUI 面归 C-M11 段升格行） |
| S-UC3-001 | UC3 多后端同模型 | 同 scenario 产 rfsoc 帧 + `.asc`，`/artifact` 两种取回一致（T2-07 §7 产物行升格） |
| S-UC4-001 | UC4 事务闭环 | 注入假设备失败→rolled_back→重试 committed；audit begin/end 成对 |
| S-UC5-001 | UC5 遥测告警 | SSE 订阅→溢出位触发告警事件→断线重连续传（收编 T2-07/T2-08 SSE 行） |
| S-UC6-001 | UC6 第三方编排 | SDK 全流程（imports→scenario→session→apply→telemetry）；三前端一致性两个面（REST vs SCPI 调用序、GUI vs SDK 调用序——收编 T2-07 §10/T2-11 §6） |
| S-UC7-001 | UC7 版本化重放 | scenario@version 重放→artifact_hash/帧序列逐字节相同（幂等黄金升格到全栈） |
| S-UC8-001 | UC8 多设备 | 双假设备并行会话互不干扰；`ChannelAddress` 寻址正确（P4 前标 `planned`） |
| S-SEC-001 | 鉴权/限流矩阵 | 收编 T2-07 §7（3 scope×全端点、429+Retry-After） |
| S-PERF-001 | 性能冒烟 | 64×24+系数全量下发计时——阈值「秒级」（《T1-11》§7），精确数值 T3-05 定标；发布 gate 项 |

---

## 6. 负向用例矩阵（原则 4 总账）

全库「拒绝不猜 / 计数不静默 / 显式报错」条款的负例义务（`N` 不是层——负例分布在各层，此表是**横切索引**，`cases` 指向各层 ID）：

| 条款出处 | 必须发生的拒绝/上报 | 负例落点 |
| :-- | :-- | :-- |
| T2-06 §5 能力门 | `realization=CIR` → rfsoc 拒并指明 asc 出口；paths > max_paths 拒 | C-M6 段 |
| T2-02 §2 / T1-15 §7 | B+ 三 capability=False → 播放/参数流被拒（不入 allowed_ops） | C-M6 段 |
| T1-15 Q2 裁定 2-① | 超 24 径 A 档 time_varying 模型（schema 放宽前）→ 显式拒不静默截断 | C-M5/C-M10 段 |
| T2-04 §5 | 超范围码值丢弃**并计数上报**、不夹端点 bin；孤儿径默认报错、宽松模式计数不静默 | U-M4 段 |
| T2-04 §3.1 | link_mode 与 LINK 表不符拒（端点集合级：重复/缺失/非法端点明细）；V1–V7 各违例拒 | U-M4 段 |
| T1-03c §10 | 迁移歧义拒：`manual` 来源、index 模式无 frame 声明 → `SchemaMigrationAmbiguous`；高版本 → `SchemaTooNew` | C-M10 段 |
| T2-03 §6 | 引擎请求预检拒：`frame=world`、统计场景缺 geometry、采样充分性 `update_rate_hz < 2·f_d_max` | C-M3 段 |
| T2-08 §3/§7 | bypass 未标定 → `UncalibratedError` 绝不静默按 0；越域遥测帧丢弃+计数 | U-M8 段 |
| T2-10 §2/§6 | `$blob` 信封无解析器 → `BlobResolverRequired`；sha256 失败 → `CorruptData` 不返回部分数据 | U-M10/C-M10 段 |
| T2-06 §3/§6 | 非法状态迁移逐格拒；DEVICE_DIRTY 下 close(disable/leave) → `InvalidCloseError`；`DeviceBusy`/`OperationInFlight` 携扩展字段 | C-M6/C-M7 段 |
| T2-07 §2/§4/§7 | 401/403 含所需 scope；429 含 Retry-After；CREATED 态 dry_run/artifact → 409 指明先 resolve | C-M7/S-SEC 段 |
| T2-01 §8 | 越界换算 `ValueError`（phase 卷绕豁免）；畸形流重同步不停摆 | U-M1 段 |
| T2-09 §3/§4 | 未知 error_code → 保底 `CepApiError` 不吞原文；`StaleWait`/`WaitTimeout` 语义区分 | C-M9 段 |
| T2-04 §2（MPDB v1.1 修订） | CFR 模式库**显式拒收**（行语义非逐径，不静默错读）；DOPPLER 列与 velocity 双源冗余 → 列优先+偏差告警 | U-M4/C-M4 段 |

矩阵纪律：本表每行在 `obligations.yaml` 至少一条义务；新增「拒/不静默」设计条款的 PR 必须同步本表（评审检查点）。

---

## 7. 落地节奏

- 本篇合并后，`obligations.yaml` 初版按 §2–§6 生成（全部 `planned`）——**文档不逐条复制 T2 判据，矩阵条目引用条款号**（单一真源）；
- P0 实现推进时逐段转 `building/green`；ID 段冲突/容量不足（>99 行）时扩四位序号，规则不变。

## 8. 开放问题

1. L4 真引擎可切的 CI 触发条件（nightly？tag 前强制？）——T3-05 gate 编排定。
2. S-UC8（多设备）在 P4 前保持 `planned` 的豁免表达——按《T3-01》§6-5 waived 还是 planned，随 P0 矩阵初版定口径。
3. I-ORCH-010 的合成 MPDB fixture 规模（径数/链路数）与解析预期的推导文档——随 T3-04 黄金登记一并落。

## 9. 本篇验收

- L1/L2 收编规约可机械执行（给定 T2 表行→唯一 ID，无需判断）；
- §3 五条补缺用例判据完整（类别/内容/判据三列齐）且与 T2 正文行为定义一致；
- §6 负向矩阵覆盖《T3-00》原则 4 点名的全部条款类且落点无空挡；
- `obligations.yaml` 初版可由本篇直接生成（种子完备）。
