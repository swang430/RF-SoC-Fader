# T2-06 · M6 场景与会话管理（功能设计）

> 第二册《功能设计》· 第 6 篇（L3 编排核心：Scenario / Session / 编排管线）
> 状态：**v1.0 · 已冻结**（2026-07-16，tag: design-t2-v1.0）· §2 已随《T1-12》N5（输入功率声明制）增 `InputPowerDecl` 修订（2026-07-19 PR）
> 依据：《T1-04 API 策略》《T1-08 设备后端》《T1-10 可扩展性》《T1-11 §1 事务 · §3 审计 · §5 配置即数据》（冻结基线）
> 消费方：M7（REST 资源直映本篇服务）、M11 GUI ④下发面板；依赖：M2 apply 契约、M3/M4/M5 产模契约、M10 持久化（契约级）

---

## 1. 概述与定位

M6 是 L3 的编排核心，回答三个问题：

- **配什么**：**Scenario**——「配置即数据」聚合根，捕获从输入源到退化参数的全部意图（可序列化/版本化/可重放，《T1-11》§5）。
- **下到哪**：**Session**——一次「scenario@version × 设备 × 后端」的绑定与生命周期（《T1-08》§5：后端类型与设备实例正交，同一 scenario 可开两个 session 产两种后端产物）。
- **怎么走**：**编排管线**——materialize（M4 导入 / M3 生成 / 模型库直引）→ reduce（M5，按层级）→ 能力门 → render（M2 纯函数）→ apply（M2 事务）。

**非职责**：算法本体（M3/M4/M5）、帧构造与传输（M1/M2）、REST 表达与鉴权（M7）、持续遥测与告警（M8）、持久化实现（M10，本篇只依赖其 repository 契约）。

---

## 2. 数据模型（配置即数据）

```python
@dataclass(frozen=True)
class Scenario:                          # 聚合根：一份可重放的「信道意图」
    scenario_id: str
    version: int                         # ★不可变版本：任何修改产生新 version（旧版永不改写）——
                                         #   「一次下发 = scenario 版本 + 后端 + 设备」可完整复现（T1-11 §5）
    name: str; description: str
    source: ScenarioSource               # ↓输入源三选一（任一层输入原则，T1-03b）
    synthesis: SynthesisConfig | None    # M5 退化参数（materialize 产物 level∈{RT,GCM,CDL} 时必填；
                                         #   TDL/CIR 直通可 None——校验在 resolve ②步，缺则拒）
    created_at: datetime; created_by: str
    tags: tuple[str, ...] = ()           # 检索标签（T1-14 ①场景库检索面）——缺省空、创建不强制
                                         #   （置末尾带默认值，不打破前列必填字段的 dataclass 顺序）；
                                         #   与其他字段同受不可变版本纪律（改标签=新 version，无特例）
    input_power: InputPowerDecl | None = None
                                         # ★功率参考链锚点（《T1-12》N5：输入功率声明制，2026-07-19）——
                                         #   设备无输入实测能力，本声明是输出功率/SNR 语义的唯一锚；
                                         #   缺省 None=未声明：功率功能降级（M8 §3.6——仅相对损耗，
                                         #   绝对 P_out/SNR 请求被拒）；改声明=新 version（无特例）

ScenarioSource =(联合类型)
  | MpdbSource(mpdb_ref, arrays, portmap, import_cfg: ImportConfig)   # → M4 import_mpdb（RT）
  | EngineSource(generate_spec: GenerateSpec, portmap)                # → M3 generate（GCM/CDL/TDL/CIR；
                                                                      #   seed 在 spec 内=确定性契约）
                                                                      # ★GenerateSpec = GenerateRequest 去
                                                                      #   client_key 的物理配置投影——完整
                                                                      #   请求由 M6 提交时注入新键构成（§2 注）
  | ModelRefSource(model_id: str)                                     # → 模型库直引（外部导入的任一层模型）

@dataclass(frozen=True)
class InputPowerDecl:                    # 输入功率声明（《T1-12》N5）——场景级、逐输入端口
    port_dbm: Mapping[int, float]        # 输入端口→声明均值功率（dBm）；键=协议输入端编号（0–7）
    source: Literal["user_declared", "external_meter"] = "user_declared"
                                         # 来源标注（审计与 GUI 明示用）；「设备实测」不在此枚举——
                                         #   随 capabilities.supports_input_power_measurement（M2）解锁再扩

@dataclass(frozen=True)
class Session:
    session_id: str
    scenario_id: str; scenario_version: int   # ★锁定版本：不跟随 scenario 的后续编辑
    device_id: str | None                     # asc 后端可 None（纯文件产物，无设备语义）；
                                              #   ★rfsoc 亦可 None=「**离线预览会话**」——resolve/dry_run/
                                              #   artifact 全可用（render 纯函数只需 RFSOC_CAPS 静态能力、
                                              #   零设备触达——S2「无设备完整预览」的会话形态，T2-11 §1）；
                                              #   其 allowed_ops 恒排除 apply/tweak/recover（无设备可触），
                                              #   上机=以同 scenario@version 新建带设备会话（产物缓存命中）
    backend: Literal["rfsoc", "asc"]
    state: SessionState                       # §3 状态机
    artifacts: ResolvedArtifacts | None       # resolve 产物缓存
    last_apply: ApplyResult | None            # M2 结果原样保留（含 device_state / telemetry）
    current_op: OperationRef | None           # ★在途异步操作：submit 面受理即置位、终局清空——
                                              #   OperationInFlight 拒并发的判据；轮询方见此知「仍在跑」
    completed_ops: tuple[OpRecord, ...]       # ★终局操作历史（有界，最近 N 条；OpRecord={op_id, kind,
                                              #   outcome, at, error?, result?}——★outcome 全集（SDK/GUI 判据词表，
                                              #   T2-09 §3 消费）：completed | failed | rejected | aborted_by_restart | aborted_by_close
                                              #   ——completed=操作跑完（apply 类细节在 result.device_state：
                                              #   committed/rolled_back/dirty）；failed=操作异常终局（resolve 失败等）；
                                              #   rejected=受理后审计不可用零触达终局（T2-10 §6）；
                                              #   aborted_by_restart=重启中止（§3）；aborted_by_close=在途 resolve
                                              #   被会话 close 中止（§3 RESOLVING 行）。★终局细节随记录：error=
                                              #   该操作的结构化错误、result=其 ApplyResult 摘要。会话级
                                              #   last_error/last_apply 会被后续操作覆盖，晚归续等只能从
                                              #   OpRecord 取本操作的失败因/结果）：任务终局时运行器追加——
                                              #   客户端 wait 以「op_id ∈ completed_ops」关联终局取细节
                                              #   （只存最近一条时，超时续等期间有后续操作完成会让旧 op
                                              #   永远匹配不上；re-apply 场景纯看状态会把旧 ACTIVE 误判成功）。
                                              #   N 有界防膨胀（M10 配置）；被逐出的 op 视为过期→客户端
                                              #   StaleWait（指引查会话审计）
    last_error: StructuredError | None        # ★最近一次失败的结构化错误（ScenarioError/CapabilityError/
                                              #   EngineError/ImportError 原样）：异步任务失败时由运行器写入——
                                              #   RESOLVE_FAILED 时 reports/last_apply 皆空，这是轮询方唯一的
                                              #   定位来源（M7 直译 problem+json）；下次操作成功时清空
    tweaks: tuple[TweakRecord, ...]           # ACTIVE 期微调记录（§4bis）：设备现态=artifact+tweaks 按序重放
    audit: tuple[AuditRecord, ...]            # ★会话生命周期操作全记录（resolve/apply/tweak/close/recover）：
                                              #   谁·何时·终局结果；触达设备类另含帧摘要（T1-11 §3 为最低要求，
                                              #   此处放宽为全生命周期——异步任务由 M6 运行器在终局写入，
                                              #   含 RESOLVE_FAILED 等非设备触达终局，保证无轮询也闭合）

@dataclass(frozen=True)
class ResolvedArtifacts:
    model_id: str                        # 物化后 canonical model 的内容寻址 ID（provenance 链锚点）
    artifact: FramePlan | AscFileSet     # M2 render 产物（★dry-run 与 apply 共用同一份——所见即所下）
    artifact_hash: str                   # 幂等判据：同 scenario@version × caps → 同 hash
    reports: Reports                     # {import|engine, fidelity?, quant?}——M3/M4/M5 报告透传（GUI 展示）
    power_plan: PowerPlan | None = None  # ★功率参考链评估（M8 §3.6「现状评估」形态，《T1-12》N5）——resolve 期
                                         #   以场景声明 P_in + 模型损耗（含共享归一偏移）+ 产物衰减设置计算；
                                         #   未声明 P_in 时为相对损耗模式（PowerPlan.mode="relative"）；经
                                         #   GET /sessions/{id} 直出（T2-07 §2），GUI 功率链呈现数据源（T2-11 §3④）。
                                         #   ★不参与 artifact_hash/缓存键——校准数据（M10 版本化）变更时
                                         #   缓存命中重算（§4），plan 永远反映当前校准
```

- **client_key 不入 Scenario**：EngineSource 持 **GenerateSpec**（GenerateRequest 去 `client_key` 的物理配置投影，类型层面排除、而非约定不填）；每次提交由 M6 生成新 UUID 补全为完整 GenerateRequest（与 T2-03 §3「provenance 剔除 client_key」同口径）——物理配置与传输键分离，scenario 判等不受提交次数影响。
- **portmap 单源**：`source` 内的 portmap 即 M4/M3/M5 全链共用的那一份（T2-04 单源约定），M6 不另存副本、不做二次投影。

---

## 3. 会话状态机

```
                resolve(异步)                 apply(异步, M2 事务)
 CREATED ─────► RESOLVING ─────► READY ─────► APPLYING ─────► ACTIVE
                   │失败           ▲│dry-run     │               │ re-apply（同产物重下发）；
                   ▼               │└─(不迁移状态) │失败           │ 换 version → 新建/重 resolve
              RESOLVE_FAILED       │             ├ rolled_back ─► READY（附 failure，可重试）
                   │修 scenario=新 version        └ dirty ───────► DEVICE_DIRTY
                   ▼                                               │ recover() = RESET + 重 apply（经 M2）
               (新 session)                                        ▼
                                     CLOSED ◄──（close 可自任何非 APPLYING 态进入；释放设备互斥）
```

| 状态 | 含义 | 允许操作 |
| :-- | :-- | :-- |
| CREATED | 已绑定 scenario@version × device × backend，未物化 | resolve / apply（auto_resolve：内部先 resolve）/ close |
| RESOLVING | 编排管线运行中（§4；长耗时异步 job，《T1-04》） | 查询进度 / close（对在途 resolve 的语义同 §6「RESOLVING cancel」行：放弃本地编排、引擎任务自然过期——close 前先将 current_op 以 `aborted_by_close` 终局（§2 词表值，等待者得到定义内终局），零设备触达直接 CLOSED。cancel 本身仍无 REST 端点，**提供前不入 allowed_ops 序列化**，不向客户端广告不可执行的操作） |
| READY | 产物就绪（FramePlan/AscFileSet + 报告），设备零触达 | dry_run / apply / close |
| APPLYING | M2 事务进行中 | 查询进度（同会话禁止并发第二个 apply） |
| ACTIVE | committed——设备射频输出已按该产物生效 | readback / dry_run（§4 明写 {READY, ACTIVE}——列此保证 allowed_ops 不漏播合法操作）/ tweak（§4bis）/ re-apply / close |
| DEVICE_DIRTY | M2 返回 dirty：设备状态未知（T2-02 §契约） | recover / close(reset) |
| RESOLVE_FAILED | 物化失败（last_error 定位，可换 version 新建会话） | close（无租约、零设备触达，直接 CLOSED——不给 close 则僵尸会话不可终结，与图注「close 可自任何非 APPLYING 态进入」一致） |
| CLOSED | 终态（释放设备租约） | —— |

- **「允许操作」列是对外 `allowed_ops` 的唯一裁决源**：会话 GET（T2-07）把本表当前态的允许操作集序列化为 `allowed_ops` 输出（与 409 错误扩展字段同源）——GUI/SDK **不得自行由 state 推导**可用操作（前端零业务逻辑，T2-11 §1）；tweak 的 rfsoc 限定（§4bis）等后端条件同样折算进裁决。

- **设备互斥＝租约（lease），不是锁**：首次 `apply` 时向 `DeviceLeaseRegistry` **非阻塞 try-acquire**——已被他会话持有 → 立即 `DeviceBusy`（含持有者 session_id），**不排队**（asyncio.Lock 会排队等待，语义不符，不采用）；本会话已持有（re-apply）→ 幂等继续。**租约生命周期与 apply 调用解耦**：一经取得，无论结果（ACTIVE / rolled_back 回 READY / DEVICE_DIRTY）都由本会话持续持有——设备的后续状态由本会话负责收拾——直至 close（或 asc 后端无设备语义）才释放。不同设备并行不受限（《T1-10》单机多会话）。`readback` 只读，不受租约限制（M8 遥测同理）。
- **close 策略**：`close(release="disable" | "leave" | "reset")`，默认 **disable**——经 M2 微帧通道（`apply_micro`，T2-02 契约）下发「全局使能关」（echo 纪律同 apply，同遥测节奏恢复机制），配置保留、可快速重启；`reset`=RESET 清态；`leave`=仅释放租约（交接给外部测量流程时用）。**★自 DEVICE_DIRTY 进入 close 强制 `reset`**：`disable`/`leave` 一律拒（`InvalidCloseError`）——设备状态未知时，「仅关使能」预设了残留配置可信、「原样交接」把未知状态转嫁给下一租约持有者，均不成立；必须 RESET 重建已知基线后才释放租约（与 M2 dirty 语义「重连后须先 RESET 才可信」一致）。**close 的审计包裹**：`disable`/`reset` 分支触达设备——与 §4 apply / §4bis tweak 同一纪律（`audit_begin(blocking=持设备)` → 微帧/RESET → `audit_end`，异常路径同样以 try/except 闭合）；`leave` 与 asc 分支零设备触达——按生命周期终局单条记录（T2-10 §6 ②）。asc 后端 close 无设备语义（直接 CLOSED）。
- **重启恢复**：会话经 M10 持久化；进程重启后 ACTIVE/APPLYING 一律降为 **DEVICE_DIRTY**（重启期间设备真实状态未知，须 recover 重建信任——与 M2 dirty 语义一致，不得谎称仍然可信）；**RESOLVING 回退 CREATED**（resolve 零设备触达、无 dirty 语义——重启后重新 resolve 可幂等命中 artifact 缓存，不遗留无活任务的僵死中间态）。**★孤儿 current_op 清理**：重启时 `current_op` 非空（202 已受理、运行器未及终局即崩溃）→ 向 completed_ops 追加 `OpRecord{op_id, outcome="aborted_by_restart", error=重启中止}` 并清空 current_op——等待者命中该记录得到明确终局（而非永久挂起），会话也不再被幽灵在途操作卡死（OperationInFlight 永拒）。

---

## 4. 编排管线（resolve / apply 伪代码）

```python
async def resolve(sess) -> ResolvedArtifacts:
    transition(sess, to=RESOLVING)       # ★状态迁移在 resolve 本体内——单独 resolve 与 apply(auto_resolve)
                                         #   复合路径的可观测状态严格一致（CREATED→RESOLVING→READY；
                                         #   失败由运行器落 RESOLVE_FAILED + last_error）
    scen = scenario_repo.get(sess.scenario_id, sess.scenario_version)    # 不可变读（M10）
    backend = registry.backend_of(sess)                                  # rfsoc(device) | asc
    caps = backend.capabilities
    if (hit := artifact_cache.get(scen.scenario_id, scen.version, sess.backend, digest(caps))):
        hit = replace(hit, power_plan=recompute_power_plan(scen, hit))   # ★命中重算 power_plan：帧产物不依赖
                                                         #   校准、可复用；plan 依赖校准表/code↔dB（M10 版本化配置，
                                                         #   可能已更新）而缓存键不含校准 digest——命中时以当前校准
                                                         #   重算（loss 源：hit.reports.fidelity 或 model_repo 取
                                                         #   模型直算，同 ④ 步三行的封装），绝不回吐陈旧 plan
        set_artifacts(sess, hit)                                         # ★幂等缓存：命中即跳 ①②④
        transition(sess, to=READY); return hit

    # ① materialize：三源归一到 canonical model（各附报告）
    match scen.source:
        case MpdbSource(ref, arrays, pm, icfg):
            model, rep = import_mpdb(ref, arrays, pm, icfg, target_caps=caps)          # M4（同步，线程池跑）
        case EngineSource(spec, pm):
            model, rep = await engine.generate(with_fresh_client_key(spec), pm)        # M3（异步）——
                                                 # with_fresh_client_key：spec+新 UUID → 完整 GenerateRequest
        case ModelRefSource(mid):
            model, rep = model_repo.get(mid), None                                     # M10
    model = model_repo.put(model)        # ★materialize 后立即入库：回填内容寻址 id（T2-10 §3，put 幂等）
                                         #   ——后续 reduced_from 必须引用此 hash id（模块内 new_id 仅占位，
                                         #   否则溯源链指向库中不存在的瞬态 id）

    # ② reduce：按层级降到硬件可实现面（T1-03b 退化链；TDL/CIR 直通）
    fidelity = None
    if model.level in {"RT", "GCM", "CDL"}:
        if scen.synthesis is None: raise ScenarioError("synthesis 必填：模型层级需退化")
        model, fidelity = synthesizer.reduce_to_tdl(model, portmap_of(scen, model), scen.synthesis)  # M5
        # ★portmap_of 三源定义：Mpdb/EngineSource → source 自带（与入库后 model.meta.port_map
        #   同一份，T2-05 §2 一致性裁决）；ModelRefSource → **model.meta.port_map**（v1.1 模型
        #   自携——直引模型无独立 portmap 来源）；需退化而两处皆缺 → ScenarioError

    # ③ 能力门：产物形态 × 设备能力（通用 fader 原则：不满足→显式拒，不静默降级）
    check_capabilities(model, caps)      # 例：realization=CIR 而 caps.supports_cir=False
                                         #     → 拒并指明替代出口（asc 后端）；paths 超 max_paths → 拒

    # ④ render：纯函数产物（dry-run 与 apply 同源）
    artifact = backend.render(model, addr_of(sess))                                    # M2
    model = model_repo.put(model)                        # 退化产物入库并回填 hash id（ResolvedArtifacts
                                                         #   用它——与 ① 处 put 同一引用纪律，T2-10 §3）
    loss, norm = ((fidelity.channel_loss_db, fidelity.shared_norm_gain_db) if fidelity
                  else (calibration.channel_loss_db_of(model), 0.0))
                                                         # ★经 M5 退化：绝对损耗 + 共享归一偏移一并取归一化前记录
                                                         #   （T2-05 §2）——rendered 设置作用在【归一化域】，偏移
                                                         #   必须随行入 plan，否则绝对预测带系统性刻度差
                                                         # ★直通 TDL/CIR（reduce 不跑、fidelity=None）：损耗从
                                                         #   canonical model 直取（T2-08 §3.6 纯函数——直通表幅度
                                                         #   即模型意图），无归一化偏移=0；不解引用 None
    plan = calibration.output_power_plan(scen.input_power, loss, shared_norm_gain_db=norm,
                rendered=(power_settings_of(artifact)    # ★rfsoc：帧产物的**输出级**设置投影（ID11 衰减/AWGN 码，
                          if sess.backend == "rfsoc"     #   不含径幅——径幅已由 loss+norm 承载，防双计）
                          else None))                    # ★asc：AscFileSet 无帧设置——rendered=None 合法，plan
                                                         #   降为 scope="model_only"（.asc 绝对功率由回放设备
                                                         #   定标，平台只承诺模型损耗链，T2-08 §3.6）
                                                         # ★N5「现状评估」（target=None）：评估**将下发的产物**；
                                                         #   未声明 P_in → mode="relative"（不报错）；纯函数零设备触达
    arts = ResolvedArtifacts(model.id, artifact, hash_of(artifact), collect_reports(rep, fidelity),
                             power_plan=plan)
    artifact_cache.put(..., arts)
    set_artifacts(sess, arts)            # ★先落 Session 再发布 READY——轮询方见 READY 即产物可取
    transition(sess, to=READY); return arts        #   （READY 早于 artifacts 落库=下载/dry-run 竞态）

async def apply(sess, dry_run=False, auto_resolve=True) -> ApplyResult | Manifest:
    if dry_run:                          # dry-run：返回缓存 manifest——零设备触达（T1-04）、同步快返
        require_state(sess, {READY, ACTIVE})             # ★仅产物已缓存态；CREATED 不隐式 resolve——
                                                         #   物化是长耗时（引擎可达分钟级），不得混入同步
                                                         #   路径：InvalidStateError(allowed_ops=[resolve])
        return manifest_of(sess.artifacts.artifact)      # RFSoC：帧摘要/字节数；asc：文件预览
    if sess.state == CREATED and auto_resolve:            # ★复合语义归 M6：CREATED 直接 apply 时先物化再下发
        await resolve(sess)                               #   （M7 零业务逻辑；artifacts 落库与状态迁移都在
        sess = session_repo.get(sess.session_id)          #   resolve 本体——与单独 resolve 一致可观测）
        # ★Session 不可变（frozen）：resolve 经 repo 产生新快照，必须重载——旧绑定仍是
        #   CREATED 快照，artifacts=None，直接往下走会取空产物
    require_state(sess, {READY, ACTIVE})                 # ★非 dry-run 前置：CREATED 且 auto_resolve=False
                                                         #   在此显式拒（InvalidStateError, allowed_ops=[resolve]）
                                                         #   ——不得带着 artifacts=None 落到租约/下发
    audit_begin(sess, who, manifest_digest,
                blocking=(sess.device_id is not None))   # ★审计先行（T2-10 §6）且【先于租约】：受理落盘失败
                                                         #   →拒绝执行——预检失败不得留下已占租约的副作用。
                                                         #   ★「预检拒绝」仅对真实设备触达（rfsoc）生效；asc
                                                         #   （device_id=None，纯文件产物）属 T2-10 §6 ②非触达类：
                                                         #   审计同样持久（失败入重试队列）但不作执行前置——
                                                         #   不因审计库抖动拒文件导出
    try:
        if sess.device_id is not None:                   # asc 无设备语义，跳过租约
            leases.try_acquire(sess.device_id, owner=sess.session_id)
            # ★非阻塞租约（§3）：他会话持有→立即 raise DeviceBusy(holder)；本会话已持有→幂等继续。
            #   租约取得后【不随本函数返回而释放】——跨越 ACTIVE 全程直至 close()/终态清理才
            #   leases.release()（rolled_back/dirty 亦保持持有，§3）
        result = await backend.apply(sess.artifacts.artifact)            # M2 事务（echo/遥测在其内）
    except BaseException as e:
        audit_end(sess, outcome_of(e)); raise            # ★「每 begin 恰一 end」不变式：DeviceBusy/
                                                         #   传输异常等一切 begin 后异常路径都闭合审计
    audit_end(sess, result)
    transition(sess, by=result.device_state)             # committed→ACTIVE / rolled_back→READY
                                                         #   / dirty→DEVICE_DIRTY
    if result.device_state in {"committed", "rolled_back"}:
        clear_tweaks(sess)   # ★基线不变式（§4bis）：committed=设备回 artifact 基线、rolled_back=设备被
                             #   RESET 清空——旧 tweaks 均已不在设备上；dirty 暂留供诊断（recover 终局再清）
    return result

# ★异步提交面（供 M7 的 202 语义）：长耗时操作由 M6 任务运行器承载——网关不持协程、不追踪任务
def submit_resolve(sess) -> OperationRef: ...
def submit_apply(sess, auto_resolve=True) -> OperationRef: ...
def submit_recover(sess) -> OperationRef: ...
    # 本体即上述 resolve()/apply()/recover(RESET+重 apply)；dry-run 不走提交面（无设备触达，
    #   同步 apply(dry_run=True) 直返 manifest）。OperationRef={op_id}。
    # 进度不设独立端点：Session.state 即进度（RESOLVING/APPLYING → 终点态），GET session 观察
    #   （M7 poll_url 指向它）。受理即置 current_op；任务终局时运行器向 completed_ops 追加 OpRecord
    #   并清 current_op——客户端以 op_id 关联「本操作」终局（§2 字段注）。
    #   同会话已有在途操作（current_op 非空）→ 立即拒 OperationInFlight（状态机禁并发的显式化）
```

- **幂等**（《T1-11》§1）：同 scenario@version（seed 固定）→ 同 model_id → 同 artifact_hash → 重复 apply 下发**逐字节相同**的帧序列。

### 4bis. ACTIVE 期微调（tweak——支撑《T1-04》`/channels PATCH` 与 GUI 微调、《T1-11》§3「apply/微调/复位」审计口径）

```python
async def tweak(sess, channel: tuple[int,int], path: int | None,
                params: TweakParams) -> ApplyResult:
    # 前置：sess.state == ACTIVE（已持有租约）；rfsoc 后端限定（asc 无「运行中设备」语义）
    # TweakParams 仅限【真·逐径/逐信道】物理量：主时延/幅度/相位/多普勒（逐径）——
    #   ★AWGN/输出衰减为输出口级旋钮（ID8/9/11 作用于整个输出端、影响该口所有信道），
    #   不入 per-channel tweak（跨信道副作用）：修改走 scenario 新 version 的 re-apply；
    #   使能类与 RESET 同样禁入：配置面不变式（configure-then-enable，T2-02 G0/G6）只归 apply 管
    frames = encode_tweak_frames(channel, path, params)     # M1 编码（物理量→码值→子帧→控制帧）
    audit_begin(sess, who, digest(frames))                  # ★审计先行（T2-10 §6）：受理记录落盘失败
                                                            #   → 拒绝执行（不触设备）——顺序不可倒，
                                                            #   与 §4 apply 的 audit_begin→backend.apply 一致
    try:
        result = await backend.apply_micro(frames)          # M2 微帧通道（echo 纪律；device_state 语义同 apply）
    except BaseException as e:
        audit_end(sess, outcome_of(e)); raise               # ★begin/end 不变式同 §4：断连/超时等
                                                            #   apply_micro 异常路径同样闭合审计
    record = TweakRecord(now, who, channel, path, params, result)
    audit_end(sess, record)                                 # 终局补记（含失败；写失败→重试队列，T2-10 §6）
    match result.device_state:                              # 状态更新经 repo（Session 不可变，同 transition 机制）
        case "committed":   session_repo.append_tweak(sess, record)
            # ★仅 committed 进 tweaks 重放列表——未生效的微调不得成为「设备现态」的一部分
        case "rolled_back": transition(sess, to=READY, clear_tweaks=True)
            # M2 回滚=RESET 基线：base 配置已不在设备上，会话退回 READY 待重新 apply（tweaks 清空，审计仍在）
        case "dirty":       transition(sess, to=DEVICE_DIRTY)
            # 设备状态未知：tweaks 暂留仅供诊断展示；recover()=RESET+重 apply 回 artifact 基线，
            # ★完成时必须清空 tweaks（否则「现态=artifact+tweaks」不变式失真）——审计记录不清
    return result
```

- **复现语义**：设备现态 = artifact（base apply）+ `tweaks` 按序重放（**仅含 committed 微调**）——tweak 不改 scenario、不改 artifact_hash，偏离被**显式记录**而非篡改基线（配置即数据不破坏；重放=`apply → 逐条 tweak`）。
- **视图**：逐信道参数视图 = artifact 参数 + tweaks 叠加（M7 `/channels` GET 以此表达）。
- **基线重置即清 tweaks**：re-apply 与 recover()（RESET+重 apply）都把设备重置到 artifact 基线——完成后 `tweaks` 一律清空（其效果已被覆盖；审计记录保留）。
- **报告透传**：Import/Engine/Fidelity/Quant 报告随 ResolvedArtifacts 全量保留——GUI ④面板与验收都以此为据，M6 不摘要不吞。

---

## 5. 时序图（正常路径 + 回滚分支）

```
M7/GUI          M6(Session)              M3/M4/M5                M2(Backend)            设备
  │ create(scen@v, dev, rfsoc)
  │──────────►│ CREATED
  │ resolve   │
  │──────────►│ RESOLVING
  │           │── materialize ─────────►│ import / generate
  │           │◄─ model + report ───────│
  │           │── reduce_to_tdl ───────►│ (M5，level∈{RT,GCM,CDL} 时)
  │           │◄─ TDL + fidelity ───────│
  │           │── render(model) ────────────────────────────────►│ 纯函数
  │           │◄─ FramePlan + manifest ──────────────────────────│
  │           │ READY
  │ dry_run   │
  │──────────►│──► manifest（零设备触达）
  │ apply     │
  │──────────►│ try-acquire 设备租约 → APPLYING
  │           │── apply(plan) ──────────────────────────────────►│── 帧 → echo → 遥测 ──►│
  │           │◄─ ApplyResult{device_state} ─────────────────────│◄──────────────────────│
  │           │ committed → ACTIVE ／ rolled_back → READY ／ dirty → DEVICE_DIRTY
  │ close(disable)
  │──────────►│── 使能关微帧 + echo ────────────────────────────►│──────────────────────►│
  │           │ CLOSED（释放设备租约）
```

---

## 6. 错误处理

| 场景 | 处置 |
| :-- | :-- |
| materialize 失败（EngineError / ImportError / 模型库缺失） | RESOLVE_FAILED，结构化错误定位到源与字段（显式上抛，《T1-07》隔离原则） |
| 需退化而 `synthesis=None` | RESOLVE_FAILED（ScenarioError，指明缺参） |
| 能力门不满足 | RESOLVE_FAILED，错误指明能力差距与替代出口（如 CIR→asc 后端） |
| apply rolled_back | READY + failure（M2 FailureInfo 原样透传），可直接重试 |
| apply dirty | DEVICE_DIRTY：唯一出路 recover()（RESET→重 apply，经 M2）或 close(reset)——不得跳过 |
| 设备忙 | DeviceBusy（含持有者 session_id），不排队、立即返回 |
| scenario 并发编辑 | 版本不可变，「编辑」=创建新 version；并发创建由 repo 乐观锁裁决（M7 映射 409） |
| RESOLVING cancel | 取消本地编排；EngineSource 的引擎侧任务取消联动 T2-03 开放问题 3（首版：放弃轮询、任务自然过期） |
| 进程重启 | §3 重启恢复：ACTIVE/APPLYING → DEVICE_DIRTY |

---

## 7. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **状态机矩阵** | 全状态 × 全操作（含全部非法迁移） | 非法操作显式拒；迁移与 §3 表逐格一致 |
| **编排契约（全桩）** | M2/M3/M4/M5 stub：三种 source 各一条 happy path | 调用序 ①→②→③→④ 正确；报告透传完整无摘要 |
| **幂等/复现** | 同 scenario@version resolve 两次 + 重复 apply | model_id / artifact_hash 相等；帧序列逐字节相同 |
| **能力门** | CIR→rfsoc（不支持）；paths 超 max_paths | 显式拒且错误指明替代出口 |
| **dirty 恢复** | stub apply 返 dirty → recover() | RESET+重 apply 后回 ACTIVE；审计含全程记录 |
| **设备互斥** | 双会话同设备并发 apply；异设备并行 | 后者 DeviceBusy 含持有者 id；异设备两条都成功 |
| **close 三策略** | disable / leave / reset；另加 DEVICE_DIRTY 下 close(disable) 与 close(leave) | disable 仅发使能关微帧（echo 校验）；leave 零触达；reset 发 RESET；DIRTY 下非 reset 被拒（InvalidCloseError） |
| **重启恢复** | 持久化会话重载 | ACTIVE→DEVICE_DIRTY 降级；审计连续不丢 |
| **tweak 失败分流** | stub apply_micro 返 committed / rolled_back / dirty 三剧本 | 仅 committed 入 tweaks；rolled_back 清空 tweaks 回 READY；dirty 冻结进 DEVICE_DIRTY；三者审计皆有记录 |
| **dry-run 隔离** | dry_run 全流程跑桩设备 | 传输层零调用（mock 断言） |

---

## 8. 与现有代码的差量

现 `channel_simulator` 无会话概念（CLI 一次性离线转换、无设备交互）：M6 为全新 L3 模块。CLI 后续演进为 SDK（M9）之上的薄壳，其「一次转换」语义等价于 `create → resolve → dry_run → close(leave)`。

---

## 9. 开放问题

1. 跨设备会话（多机箱级联）的事务一致性——P4 项（《T1-10》开放问题 2），本期单设备会话。
2. ACTIVE 期间遥测越限（M8 告警）是否自动降幅/回滚——本期只告警不自动动作，安全联动策略实现期与 M8 协同定。
3. ~~scenario schema 随 T1-03c v1.1 升版的迁移~~ → **已闭合（v1.1 已于 2026-07-16 落地）**：scenario 侧变更仅 ImportConfig 新增带默认值的 `frame` 字段（默认 "world"，T2-04 §4）——「只加不改」成立，旧 Scenario 反序列化兼容；旧场景缺 frame 的读入**与 T1-03c §10 迁移规则同口径、不盲补**：`identity_by="position"` → world（v1.0 该模式即强制世界系）；`identity_by="index"` → **拒**（`SchemaMigrationAmbiguous`，要求用户显式声明——v1.0 的 index 模式 local/world 皆可能，默认 world 会静默错标局部几何、导向计算整体错位）。**（2026-07-16 追记，MPDB 手册 v1.1 PR）**：SynthesisConfig 的 `velocity_mps` 改双端 `velocity_tx/rx_mps`——旧 scenario 读侧**别名迁移（符号保持）**：`velocity_mps` → `velocity_rx_mps = −velocity_mps`、`velocity_tx_mps=None`——旧式 `+v·k̂`（k̂=到达方向）与新式 RX 项 `−v_RX·k̂_RX` 符号相反，取反保证旧场景回放的多普勒符号不变（T2-05 §2 同注）；写侧只写新字段。
4. artifact_cache 的失效策略（caps 变更/固件升级后 digest 变化即自然失效；容量上限实现期定）。
5. **B+ 播放调度器（保留接口设计，《T1-15》§7 D7）**：会话状态机增 PLAYING 子态；`PlaybackPlan` 预编译（微事务时间表+吞吐校验）与定时调度归本模块；tweak 通道复用为参数流（不 reset/不动使能/不发 ID33）。随 H1–H4 硬件确认排期（《T1-12》§8b），未确认前能力门拒（不入 allowed_ops 序列化，同 RESOLVING cancel 先例）。

---

## 10. 本篇验收

- 状态机矩阵测试全绿；三源编排契约测试全绿（全桩）。
- 幂等链验证：同 scenario@version 两次 resolve+apply 产出逐字节相同帧序列。
- dirty→recover 与 close(disable) 在 HIL 冒烟通过（真机或 TCP 环回）。
- dry-run 全程零设备触达（传输层 mock 断言）。
