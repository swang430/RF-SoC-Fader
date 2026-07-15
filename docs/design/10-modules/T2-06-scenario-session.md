# T2-06 · M6 场景与会话管理（功能设计）

> 第二册《功能设计》· 第 6 篇（L3 编排核心：Scenario / Session / 编排管线）
> 状态：草稿 v0.1 · 待评审
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

ScenarioSource =(联合类型)
  | MpdbSource(mpdb_ref, arrays, portmap, import_cfg: ImportConfig)   # → M4 import_mpdb（RT）
  | EngineSource(generate_spec: GenerateSpec, portmap)                # → M3 generate（GCM/CDL/TDL/CIR；
                                                                      #   seed 在 spec 内=确定性契约）
                                                                      # ★GenerateSpec = GenerateRequest 去
                                                                      #   client_key 的物理配置投影——完整
                                                                      #   请求由 M6 提交时注入新键构成（§2 注）
  | ModelRefSource(model_id: str)                                     # → 模型库直引（外部导入的任一层模型）

@dataclass(frozen=True)
class Session:
    session_id: str
    scenario_id: str; scenario_version: int   # ★锁定版本：不跟随 scenario 的后续编辑
    device_id: str | None                     # asc 后端可 None（纯文件产物，无设备语义）
    backend: Literal["rfsoc", "asc"]
    state: SessionState                       # §3 状态机
    artifacts: ResolvedArtifacts | None       # resolve 产物缓存
    last_apply: ApplyResult | None            # M2 结果原样保留（含 device_state / telemetry）
    audit: tuple[AuditRecord, ...]            # 触达设备操作全记录：谁·何时·帧摘要·结果（T1-11 §3）

@dataclass(frozen=True)
class ResolvedArtifacts:
    model_id: str                        # 物化后 canonical model 的内容寻址 ID（provenance 链锚点）
    artifact: FramePlan | AscFileSet     # M2 render 产物（★dry-run 与 apply 共用同一份——所见即所下）
    artifact_hash: str                   # 幂等判据：同 scenario@version × caps → 同 hash
    reports: Reports                     # {import|engine, fidelity?, quant?}——M3/M4/M5 报告透传（GUI 展示）
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
| CREATED | 已绑定 scenario@version × device × backend，未物化 | resolve / close |
| RESOLVING | 编排管线运行中（§4；长耗时异步 job，《T1-04》） | 查询进度 / cancel |
| READY | 产物就绪（FramePlan/AscFileSet + 报告），设备零触达 | dry_run / apply / close |
| APPLYING | M2 事务进行中 | 查询进度（同会话禁止并发第二个 apply） |
| ACTIVE | committed——设备射频输出已按该产物生效 | readback / re-apply / close |
| DEVICE_DIRTY | M2 返回 dirty：设备状态未知（T2-02 §契约） | recover / close(reset) |
| RESOLVE_FAILED · CLOSED | 终态（CLOSED 释放设备租约） | —— |

- **设备互斥＝租约（lease），不是锁**：首次 `apply` 时向 `DeviceLeaseRegistry` **非阻塞 try-acquire**——已被他会话持有 → 立即 `DeviceBusy`（含持有者 session_id），**不排队**（asyncio.Lock 会排队等待，语义不符，不采用）；本会话已持有（re-apply）→ 幂等继续。**租约生命周期与 apply 调用解耦**：一经取得，无论结果（ACTIVE / rolled_back 回 READY / DEVICE_DIRTY）都由本会话持续持有——设备的后续状态由本会话负责收拾——直至 close（或 asc 后端无设备语义）才释放。不同设备并行不受限（《T1-10》单机多会话）。`readback` 只读，不受租约限制（M8 遥测同理）。
- **close 策略**：`close(release="disable" | "leave" | "reset")`，默认 **disable**——经 M2 微帧通道下发「全局使能关」（复用 T2-02 build_control_frame + echo 纪律，同遥测节奏恢复机制），配置保留、可快速重启；`reset`=RESET 清态；`leave`=仅释放租约（交接给外部测量流程时用）。**★自 DEVICE_DIRTY 进入 close 强制 `reset`**：`disable`/`leave` 一律拒（`InvalidCloseError`）——设备状态未知时，「仅关使能」预设了残留配置可信、「原样交接」把未知状态转嫁给下一租约持有者，均不成立；必须 RESET 重建已知基线后才释放租约（与 M2 dirty 语义「重连后须先 RESET 才可信」一致）。asc 后端 close 无设备语义（直接 CLOSED）。
- **重启恢复**：会话经 M10 持久化；进程重启后 ACTIVE/APPLYING 一律降为 **DEVICE_DIRTY**（重启期间设备真实状态未知，须 recover 重建信任——与 M2 dirty 语义一致，不得谎称仍然可信）。

---

## 4. 编排管线（resolve / apply 伪代码）

```python
async def resolve(sess) -> ResolvedArtifacts:
    scen = scenario_repo.get(sess.scenario_id, sess.scenario_version)    # 不可变读（M10）
    backend = registry.backend_of(sess)                                  # rfsoc(device) | asc
    caps = backend.capabilities
    if (hit := artifact_cache.get(scen.scenario_id, scen.version, sess.backend, digest(caps))):
        return hit                                                       # ★幂等缓存：命中即跳 ①②④

    # ① materialize：三源归一到 canonical model（各附报告）
    match scen.source:
        case MpdbSource(ref, arrays, pm, icfg):
            model, rep = import_mpdb(ref, arrays, pm, icfg, target_caps=caps)          # M4（同步，线程池跑）
        case EngineSource(spec, pm):
            model, rep = await engine.generate(with_fresh_client_key(spec), pm)        # M3（异步）——
                                                 # with_fresh_client_key：spec+新 UUID → 完整 GenerateRequest
        case ModelRefSource(mid):
            model, rep = model_repo.get(mid), None                                     # M10

    # ② reduce：按层级降到硬件可实现面（T1-03b 退化链；TDL/CIR 直通）
    fidelity = None
    if model.level in {"RT", "GCM", "CDL"}:
        if scen.synthesis is None: raise ScenarioError("synthesis 必填：模型层级需退化")
        model, fidelity = synthesizer.reduce_to_tdl(model, portmap_of(scen), scen.synthesis)  # M5

    # ③ 能力门：产物形态 × 设备能力（通用 fader 原则：不满足→显式拒，不静默降级）
    check_capabilities(model, caps)      # 例：realization=CIR 而 caps.supports_cir=False
                                         #     → 拒并指明替代出口（asc 后端）；paths 超 max_paths → 拒

    # ④ render：纯函数产物（dry-run 与 apply 同源）
    artifact = backend.render(model, addr_of(sess))                                    # M2
    model_repo.put(model)                                # 内容寻址入库（provenance 链锚点，M10）
    arts = ResolvedArtifacts(model.id, artifact, hash_of(artifact), collect_reports(rep, fidelity))
    artifact_cache.put(..., arts); return arts

async def apply(sess, dry_run=False) -> ApplyResult | Manifest:
    if dry_run:                          # dry-run：返回 READY 缓存的 manifest——零设备触达（T1-04）
        return manifest_of(sess.artifacts.artifact)      # RFSoC：帧摘要/字节数；asc：文件预览
    if sess.device_id is not None:                       # asc 无设备语义，跳过租约
        leases.try_acquire(sess.device_id, owner=sess.session_id)
        # ★非阻塞租约（§3）：他会话持有→立即 raise DeviceBusy(holder)；本会话已持有→幂等继续。
        #   注意作用域：租约在此取得后【不随本函数返回而释放】——跨越 ACTIVE 全程直至
        #   close()/终态清理才 leases.release()（rolled_back/dirty 亦保持持有，§3）
    audit_begin(sess, who, manifest_digest)
    result = await backend.apply(sess.artifacts.artifact)                # M2 事务（echo/遥测在其内）
    audit_end(sess, result)
    transition(sess, by=result.device_state)             # committed→ACTIVE / rolled_back→READY
    return result                                        #   / dirty→DEVICE_DIRTY
```

- **幂等**（《T1-11》§1）：同 scenario@version（seed 固定）→ 同 model_id → 同 artifact_hash → 重复 apply 下发**逐字节相同**的帧序列。
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
| **dry-run 隔离** | dry_run 全流程跑桩设备 | 传输层零调用（mock 断言） |

---

## 8. 与现有代码的差量

现 `channel_simulator` 无会话概念（CLI 一次性离线转换、无设备交互）：M6 为全新 L3 模块。CLI 后续演进为 SDK（M9）之上的薄壳，其「一次转换」语义等价于 `create → resolve → dry_run → close(leave)`。

---

## 9. 开放问题

1. 跨设备会话（多机箱级联）的事务一致性——P4 项（《T1-10》开放问题 2），本期单设备会话。
2. ACTIVE 期间遥测越限（M8 告警）是否自动降幅/回滚——本期只告警不自动动作，安全联动策略实现期与 M8 协同定。
3. scenario schema 随 T1-03c v1.1 升版的迁移（version 字段已隔离旧数据，预计只加不改；升版 PR 时一并评估）。
4. artifact_cache 的失效策略（caps 变更/固件升级后 digest 变化即自然失效；容量上限实现期定）。

---

## 10. 本篇验收

- 状态机矩阵测试全绿；三源编排契约测试全绿（全桩）。
- 幂等链验证：同 scenario@version 两次 resolve+apply 产出逐字节相同帧序列。
- dirty→recover 与 close(disable) 在 HIL 冒烟通过（真机或 TCP 环回）。
- dry-run 全程零设备触达（传输层 mock 断言）。
