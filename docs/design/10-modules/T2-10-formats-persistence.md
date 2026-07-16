# T2-10 · M10 格式与持久化（功能设计）

> 第二册《功能设计》· 第 10 篇（横切基础设施：codec 注册表 · I/O 适配层 · repository · blob 存储）
> 状态：草稿 v0.1 · 待评审
> 依据：《T1-03c §7 序列化/10MB 规则》《T1-11 §5 配置即数据》《T1-09 技术栈》（冻结基线）；文件 I/O 系统归属 M10 为第一册既定决策
> 消费方：全体模块——M4/M3（模型入库）、M5（模型/报告）、M6（scenario/session/审计/artifact 缓存）、M7（配置/密钥/审计）、M8（告警规则/校准表）、M2-Asc（.asc 落盘）；依赖：schema（T1-03c，只消费不定义）

---

## 1. 概述与定位

M10 是全平台的「数据落地面」，四件事：

1. **codec 注册表**：canonical model / 报告 / 产物的序列化格式统一注册（编码/解码/嗅探/版本迁移）。
2. **I/O 适配层**：本地文件系统首版，接口抽象可换对象存储（商用部署项）。
3. **repository**：scenario / session / 审计 / 运行配置的持久化仓（含并发裁决与重启恢复的数据面）。
4. **blob 存储**：大对象（时变 CIR、.asc 产物、帧序列）内容寻址存放——10MB 内联/外置规则的落点。

**非职责**：schema 定义本体（T1-03c 为规范，M10 只实现其序列化）、业务编排（M6）、协议帧编码（M1）、HTTP 表达（M7）。

---

## 2. codec 注册表

```python
registry = {
  "model-json/v1":    # canonical model ↔ JSON（T1-03c 全字段；复数按 (re,im) 直角坐标——内部表示约定）
  "npz-cir/v1":       # 时变 CIR 张量 ↔ NPZ（键/轴序/dtype 与 T2-03 §2 布局表逐字一致——同一契约两处引用）
  "asc/v1":           # {(in,out): asc_text} ↔ .asc 文件集（In{i}_Out{j} 命名；头/抽头行格式锚定
                      #   《T1-08》AscCir 后端契约 + ChannelEgine .asc 样例——T1-A1 是协议综述、不含 .asc 布局）
  "frameplan-bin/v1": # FramePlan ↔ 二进制（帧序列原样 + manifest JSON 边车——黄金帧对比的存档格式）
  "report-json/v1":   # Import/Engine/Fidelity/Quant 报告 ↔ JSON（GUI/验收复用）
  "cdl-tdl-table-json/v1":  # 3GPP CDL/TDL 定表 ↔ JSON（T1-03c 一等定表入口，cdl_tdl_reader 消费——
                      #   「任一层输入」的用户直录面；表无相位列，退化时相位兜底见 T2-05 §3 优先级③）
}
def encode(kind, obj) -> bytes ; def decode(kind, data, meta=None) -> obj
def sniff(data, meta=None) -> kind
    # ★镜像格式的版本在元数据边车而非载荷（下）——sniff/decode 须收 meta：载荷字节只能识别「族」
    #   （NPZ 容器/asc 文本/JSON），版本取自 meta；无 meta 的裸文件（外部导入场景）按族+源契约
    #   当前版处理并显式标注该假定（不静默猜版本）
```

- **版本化读写（版本主权分两类）**：**内部格式**在载荷内携带 `schema_version`，解码端**向前兼容读**（旧版数据 → 迁移钩子链升到当前版）、编码端只写当前版；**镜像格式载荷不加任何自有字段**（键集/头由源契约固定——NPZ 无 schema_version 键、asc 无额外头，设备/引擎按源契约直接消费），其版本记在 M10 **元数据边车/注册条目**上（与 sha256 边车同层，不进载荷）。
  - **内部格式**（model-json / report-json / frameplan-bin / **cdl-tdl-table-json**——定表 JSON 的 schema 归 T1-03c 平台自有）：M10 迁移钩子演进——T1-03c v1.1 升版（PortMap/origin/phase_rad 打包项）落地时，`model-json/v1 → v1.1` 钩子在此实现，旧库存模型无需重导；用户手写的定表裸文件（无 meta）按 sniff 裸文件规则处理（族+当前版显式假定，§上）。
  - **线缆契约镜像格式**（npz-cir 随 T2-03 §2、asc 随《T1-08》后端契约）：**布局主权在源契约，M10 只镜像注册、不独立演进**——升版=源契约先升（如引擎 v2 端点/键），M10 跟随注册新条目；黄金测试对照的就是源契约（§8）。
- **完整性**：所有落盘产物带 sha256 边车（读取时校验，损坏显式报错不静默截断）。

---

## 3. repository（SQLite 首版，接口抽象可换）

| 仓 | 内容 | 关键语义 |
| :-- | :-- | :-- |
| `ScenarioRepo` | Scenario 全版本 | **版本不可变**（追加式）；并发创建新版本由**乐观锁**裁决（冲突→VersionConflict，M7 映 409，T2-06 §6） |
| `SessionRepo` | Session 快照 | 每次 transition/set_artifacts/append_tweak 写新快照；**重启恢复数据面**（T2-06 §3：状态降级、孤儿 current_op 清理、completed_ops 有界历史的持久载体） |
| `AuditRepo` | 审计双源 | **append-only**（无 UPDATE/DELETE 路径——接口层面不提供）；网关受理记录（T2-07 §4）+ 会话生命周期记录（T2-06 §2）两源同仓不同 kind。**会话侧 begin/end 是两条 op_id 关联的追加记录**（append-only 不允许两阶段改同一条）——完整性不变式=每 begin 恰有一 end（进程崩溃的孤儿 begin 由重启清理补 `aborted` end，T2-06 §3） |
| `ModelRepo` | canonical model | **内容寻址**：model_id = **规范化载荷**的 sha256——规范化=剔除 `id` 字段自身（否则哈希依赖哈希、循环自指）与 provenance 墙钟字段（imported_at 等，不改变物理内容；seed/配置差异**保留**——不同来源配置就该不同 id）+ 键序/浮点表示规范化；入库后回填 `id=hash`（`put` 幂等）。**引用纪律**：各产生模块（M4/M3/M5）内部 `new_id()` 仅过程占位——凡**跨模块引用**（`reduced_from`/`model_ref`/provenance 链）必须用入库后的 hash id，否则溯源链指向库中不存在的瞬态 id（T2-06 §4 编排层在 materialize/reduce 后各 put 一次即保证此序）。同物理内容+同来源配置 → 同 model_id（幂等链存储面） |
| `ConfigRepo` | 告警规则/校准表/密钥表/运行参数 | 版本化配置（M8 阈值、bypass 表、M7 密钥与 scope、缓冲窗 N 等）；变更留痕 |

- **选型**：单机首版 **SQLite**（零运维、事务足够——写并发瓶颈在设备租约处天然串行）；接口层（Protocol）隔离，Postgres/对象存储为商用替换项（开放问题 §7-1），**业务模块只见接口**。
- blob 与元数据分离：repository 存引用（`blob_ref`），载荷入 §4 blob store——库文件不膨胀。

---

## 4. blob 存储与 10MB 规则

```python
class BlobStore:
    def put(data: bytes) -> BlobRef        # 内容寻址：ref = sha256（同内容自然去重）
    def get(ref) -> bytes ; def open_stream(ref) -> IO   # 大对象流式读（M7 /artifact 直通不进内存）
    def pin(ref, owner) / unpin(ref, owner)              # 引用计数：scenario/session/model 持有者登记
```

- **10MB 规则的唯一落点**（《T1-03c》§7）：判定量=**内联形态的实际体量**——即 `gain_series` 按 T1-03c JSON 形态（`(re,im)` 对）序列化后计入 model-json 的字节增量；**不按 npz-cir 字节判**（NPZ 是外置格式且更紧凑，用它判定会低估内联文档体量、漏触发外置——阈值语义所指本就是内联文档大小）。≤阈值：CIR **保持冻结 schema 的原字段形态内联**（逐 `Tap.gain_series: Complex[n_snapshots]`，无 M10 中间表示）；>阈值：**按 pair 切分为单对 NPZ**（仍 `npz-cir/v1`，n_pairs=1）逐一落 blob，`channels[(in,out)].cir_ref = BlobRef`——引用挂逐 Channel 且**保持冻结 schema 的单一 BlobRef 类型**（整卷共享+`(ref, index)` 复合引用需改 schema 字段类型，违反冻结基线，不取；内容寻址下切分不损去重语义），`gain_series` 不承载。阈值为 M10 常量、判定 helper 唯一（`attach_cir` 调用处），全平台不散落第二处。**`RayleighSpec.coeffs` 大载荷的诚实边界**：冻结 schema **未定义** coeffs 的外置引用字段（`cir_ref` 仅在 Channel 上）——升版前 coeffs **一律内联**（体量有界：256 系数×径×信道，极端配置数十 MB——超软阈值记体量告警、不拒不外置，因无字段可承载引用）；**coeffs 外置字段（coeffs_ref）列入 T1-03c v1.1 升版打包项④**（与 PortMap/origin/phase_rad 同批，T2-04 §8-5），升版落地后并入本 helper 覆盖域。
- **GC**：引用计数归零的 blob 进延迟回收队列（宽限期防误删——审计留痕）；`pin/unpin` 随持有者生命周期由 repository 层自动维护，业务不手工管理。
- .asc 产物与 FramePlan 存档同走 blob（M7 `/artifact` 端点经 `open_stream` 流式直出）。

---

## 5. I/O 适配层

- 首版本地文件系统：blob 目录（两级 sha 前缀分桶）+ SQLite 文件 + 配置文件；路径策略集中于此（AscCirBackend「写文件（路径策略经 M10 I/O 适配层）」的落点，T2-02 §4）。
- 接口抽象 `Storage`（read/write/stream/list/delete）——对象存储（S3 兼容）为替换实现（商用部署项），业务零改动。
- 原子写分两级：**单文件**=临时文件 + rename（同目录）；**多文件产物**（AscFileSet 等）=**目录级原子提交**——全部文件写入 `<target>.tmp/` → fsync → 单次目录 rename 落位（同文件系统内原子）：逐文件 rename 中途崩溃会留下「部分文件已提交」的产物目录（M2 asc 的 committed 语义是**整集**写盘成功，T2-02 §4），目录级提交下半写只可能是 `.tmp` 残留（启动清扫），final 目录要么全有要么没有。**已存在旧产物的替换**：目录 rename 无法原子覆盖非空目标——采用**版本目录+符号链接切换**（写 `<target>.v{N}/` → `symlink+rename` 原子替换链接 `<target>`；旧版本目录进延迟回收，同 §4 GC 宽限）——读方经链接看到的永远是完整旧集或完整新集。均与 §2 sha256 校验双保险。

---

## 6. 错误处理

| 场景 | 处置 |
| :-- | :-- |
| 解码版本高于当前支持 | `SchemaTooNew`（指明升级平台，不猜读） |
| sha256 校验失败 | `CorruptData`（显式，含 ref 与期望/实际摘要；不静默返回部分数据） |
| 乐观锁冲突 | `VersionConflict`（携 current_version，M7→409） |
| blob 缺失（悬空 ref） | `BlobMissing`（含 ref 与登记持有者——GC 误删可溯因） |
| 磁盘满/IO 错 | 显式上抛，按阶段归属终态：**resolve 阶段**失败→RESOLVE_FAILED；**apply 前置**（审计预检/产物读取）失败→操作拒绝、会话**保持 READY**+OpRecord/last_error 记因（可重试）——不误入 RESOLVE_FAILED（resolve 专属终态，T2-06 §3）；均不产生半状态 |
| audit 写失败 | **分级**：①**设备触达类**（apply/tweak/close 微帧/recover）——**审计先行**（write-ahead）：受理记录（begin）落盘失败 → **拒绝执行**（不触设备）——《T1-11 §3》「所有触达设备操作必须留痕」是冻结基线，不得先斩后奏。**失败的表达分同步/异步**：同步操作（tweak / close 微帧）→ 503+告警；异步提交（apply/recover 经 T2-06 submit 面）→ 202 已返回、无事后 503 通道——M6 运行器把该 op 以 `OpRecord{outcome=rejected, error=audit_unavailable}` 终局（设备零触达、会话保持原态），等待者经 op 关联获知；终局记录写失败 → 事实已发生不可拒：强告警+**落盘重试队列**（独立 WAL 文件——主库不可用正是入队诱因，不得与主库同存储；重启时**先重放 WAL 补齐**再开放服务）；**补齐前该设备的新触达操作一律拒，且拒绝门随 WAL 持久**——进程崩溃不消失（否则审计洞永久存在而设备照常接单）。②非触达类生命周期操作（resolve 受理/终局等）——不作执行前置（无物理副作用，不因审计库抖动拒服务），但**同样必须持久**：写失败进同一重试队列补齐——T2-06 §2「生命周期全记录、无轮询也闭合」的承诺不因此打折；仅纯读请求零审计（T2-07 §4） |

---

## 7. 开放问题

1. Postgres / S3 兼容对象存储的替换时点（多实例部署前提）。
2. 静态数据加密与备份策略（商用部署项）。
3. 审计 WAL 的轮转与容量水位策略（§6 已定落盘 WAL 与重放语义，此处只余运维参数）。
4. blob GC 宽限期与容量水位策略（实现期定，M10 配置承载）。

---

## 8. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **序列化往返黄金** | **六类** codec 各取代表样本（含满字段 canonical model 与 3GPP 定表 JSON） | 往返逐字节/逐字段无损；NPZ 布局与 T2-03 表逐键一致；裸文件无 meta 嗅探走「族+当前版」显式假定路径 |
| **版本迁移** | 构造 v1 旧样本 → 升级钩子 → 当前版 | 字段映射正确；SchemaTooNew 路径显式 |
| **完整性** | 篡改落盘字节 | CorruptData（不返回部分数据） |
| **乐观锁** | 并发创建同 scenario 新版本 | 恰一成功，余 VersionConflict 携 current_version |
| **append-only** | 尝试改写/删除审计行 | 接口不存在该路径（类型层面断言）+ 库约束拒绝 |
| **内容寻址去重** | 同模型两次入库（**不同墙钟时刻**）；仅 seed 不同的两模型 | 前者同 model_id、单份存储（id 与墙钟字段不参与哈希——循环自指/时间戳破坏去重两校验）；后者不同 model_id |
| **blob 生命周期** | pin/unpin/归零/宽限回收/悬空 ref | 引用计数正确；BlobMissing 可溯因 |
| **原子写** | 写入中途 kill（注入） | 无半写文件；重启后数据面自洽（配合 T2-06 重启恢复用例） |
| **流式直出** | >10MB 产物经 open_stream | 内存峰值有界（不整载） |

---

## 9. 与现有代码的差量

现 `channel_simulator` 仅 CLI 写 csv/bin/hex 输出文件（无持久化概念）：M10 全新。旧 CLI 的三类输出由 `frameplan-bin/v1`（bin/hex）与 manifest（csv 语义）承接（M9 §5 收敛路线的格式面）。

---

## 10. 本篇验收

- 六类 codec 往返黄金 + NPZ/asc 与源契约（NPZ→T2-03 §2；asc→《T1-08》+ChannelEgine 样例）逐键核对全绿。
- 乐观锁/append-only/内容寻址/blob 生命周期测试全绿。
- 原子写注入测试与 T2-06 重启恢复用例联合通过（数据面自洽）。
- T1-03c v1.1 迁移钩子演练：v1 样本无损升级（升版 PR 的存储侧预案）。
