# T3-01 · 测试仓库与流程

> 第三册《测试设计》· 第 1 篇（治理章程的工程落地面）
> 状态：草稿 v0.1 · 待评审
> 依据：《T3-00》（宪法五原则/金字塔/追溯矩阵制度/gate）；冻结基线 `design-t1-v1.0`/`design-t2-v1.0`
> 消费方：P0 起的全部测试代码与 CI 配置；T3-02/03/04/05 按本篇的组织与命名落地

---

## 1. 概述与定位

T3-00 定了「管什么、凭什么管」；本篇定「**放哪、叫什么、用什么跑、怎么流转**」——测试树布局、用例 ID 与标记、fixture 主权通则、测试栈选型、开发流程纪律、追溯矩阵的机器可查载体。本篇先于 P0 代码基座就位（《T3-00》§8 测试先行），P0 写下的第一行测试就受本篇约束。

**非职责**：各层用例矩阵的内容（→T3-02）、黄金资产逐项登记（→T3-04）、HIL 台架与报告模板（→T3-03）、gate 阈值数值（→T3-05）。

---

## 2. 测试树布局

测试树与源码树**分离**（顶层 `tests/`，不散在业务包内——L1 纯函数 100% 可单测的前提是测试从不进业务包，《T1-11》§6）；树内按**金字塔分层**为第一维、模块/域为第二维：

```
tests/
├── unit/            # L1：纯函数（m1_protocol/ m4_import/ m5_correlation/ ...）
├── contract/        # L2：契约（消费 fakes/ 与 fixtures/golden/）
├── integration/     # L3：进程内编排 + TCP 环回假设备
├── system/          # L4：全栈（REST 起、SQLite、假设备/真引擎可切）
├── hil/             # L5：硬件在环（hc/ 控制环、hr/ 射频测量环）——默认收集但跳过，见 §5
├── fakes/           # 测试替身一等资产：fake_rfsoc/（假设备）、fake_engine/（假引擎）
├── fixtures/
│   ├── golden/      # 黄金资产（只读区，变更纪律见 §4；逐项登记归 T3-04）
│   └── shared/      # 非黄金共享数据（可随实现演进）
└── traceability/    # 追溯矩阵载体（obligations.yaml + 校验脚本，见 §7）
```

- **fakes 是一等资产**，其行为规格有出处：fake RF-SoC = 《T1-A1》§6「协议未提供」清单 + 六剧本及扩展（T2-02 §8、T3-00 §5-E）；fake ChannelEgine = 《T2-03》§2 契约全端点 + failed/超时/503 剧本。**fakes 自证原则**：假设备产生的每个响应帧必须能被**真 M1 解码器**解析（用真 codec 验证 fake，绝不反向）；假引擎响应必须过 client 侧 NPZ 一致性校验（T2-03 §2）——防「失真的 fake 验证出失真的实现」。
- **现有资产收编路径**：`ChannelSimulationCode/tests/test_protocol.py`（unittest，黄金帧断言）是 T2-01 §8 冻结的**回归基线（全绿不动）**——M1 收编时原文件迁入 `tests/unit/m1_protocol/`（pytest 原生兼容 unittest，非强制改写），迁移 PR 内新旧路径**双跑一次**证明等价后旧路径删除。
- 黄金帧 fixture 落 `tests/fixtures/golden/`——与 T2-01 §8「fixture 存 `tests/fixtures/`（M10 统一管理前先本地）」相容（本篇是其目录细化）。

---

## 3. 用例 ID 与标记体系

**用例 ID = 追溯矩阵（《T3-00》§4）的键**，格式：

```
<层代号>-<域>-<三位序号>     例：U-M1-007 · C-M2-005 · I-ORCH-002 · S-UC2-001 · HC-M2-001 · HR-CAL-003
```

- 层代号：`U`(unit) / `C`(contract) / `I`(integration) / `S`(system) / `HC`(HIL-C) / `HR`(HIL-R)。
- 域：模块号 `M1–M11`，或跨模块域代号——`ORCH`(编排链)、`UC`(用例场景，《T1-02》UC1–UC8)、`GOLD`(黄金资产对比)、`CAL`(定标/标定)、`MIG`(迁移)。域代号表随矩阵载体维护，新增走 PR。
- **序号一经分配不复用**：废弃用例在矩阵中保留行、状态置 `retired`（防历史报告中的 ID 悬空）。
- 代码侧标记（pytest marker，单一来源）：

```python
@pytest.mark.tc(id="C-M2-005", clause="T2-02 §8")   # clause=设计出处（册-篇-节）
async def test_disconnect_mid_apply_judged_dirty(fake_rfsoc): ...
```

一个用例恰一个 `tc` 标记；一个义务可由多个用例覆盖（矩阵 `cases` 列表）。

---

## 4. fixture 主权通则

| 区 | 规则 |
| :-- | :-- |
| `fixtures/golden/<域>/` | **只读区**。每个域目录含 `provenance.md`：逐文件登记出处（docx 页码/ChannelEgine 样例路径/3GPP 表号/推导文档）、首录 PR、重录程序。变更=设计事件：**独立 PR + 出处更新**（《T3-00》原则 2 红线），评审人核对出处而非实现输出。命名 `<资产>-<来源>-v<n>.<ext>`（如 `frame-id06-docx-v1.hex`） |
| `fixtures/shared/` | 非黄金共享数据（合成 MPDB 小表、随机种子样本等），随实现演进、普通评审 |
| `fakes/` | 行为变更须引用规格出处（§2）；fake 的自证用例（`U-M1-*` 消费 fake 输出）随 fake 同 PR 更新 |

- 黄金资产的**逐项清单**（哪些文件、对应 T3-00 §5-A 哪行）归 T3-04；本篇只立通则。
- 完整性由 git 保证（版本化+评审），不另加 sha256 边车——边车是运行时 M10 的事，测试仓库不重复。

---

## 5. 测试栈与运行选择器

| 件 | 选型 | 依据/用途 |
| :-- | :-- | :-- |
| 运行器 | **pytest** | 《T1-09》Python 全栈的事实标准；原生兼容现有 unittest 基线 |
| 异步 | pytest-asyncio | 假设备 asyncio TCP（T2-02）、M6 运行器、SSE |
| property-based | **hypothesis** | T2-02 §8「切分属性」行（随机模型→不变式断言）的承载；CI 内固定 profile（示例数/超时可配，防不稳定） |
| 覆盖率 | coverage.py（分层报告） | M1 行覆盖 100% 为冻结目标（T2-01 §8）；其余阈值数值归 T3-05 gate |
| GUI E2E | Playwright | T2-11 §6（UC2 旗舰全链 + HTTP 调用序录制）；独立 extras，核心开发不装 |
| 仪器控制 | PyVISA（HIL-R） | T3-03 台架编排；hil extras |

- **标记与选择器**：按层选跑用 pytest 布尔标记表达式——单层 `-m unit`，多层 `-m "unit or contract"`（MARKEXPR 语法为 `and/or/not`，**不是 `|`**——`|` 未加引号还会被 shell 当管道）；`hil` 用例默认**收集但跳过**（skip：未给 `--run-hil` 或台架环境变量缺失时自动 skip）——**必须保持可收集**，否则绿状态的 `HC-*/HR-*` 用例在 `pytest --collect-only` 中消失、§7 四查第 1 查误报；`slow` 标记供 PR gate 剪枝。层标记由目录自动注入（conftest），与 `tc` 标记并存。
- conftest 层级：根 `conftest.py`（tc 标记注册、层自动标记、追溯钩子）→ 各层 conftest（fakes 夹具、环回端口分配）→ 域 conftest。

---

## 6. 流程纪律（gate 的执行面）

1. **每 PR**：新功能必须携对应层用例 + 追溯矩阵 diff（《T3-00》§7 PR gate）——评审首先看矩阵 diff 是否与代码 diff 对得上。
2. **bug 修复先复现**：先提交失败用例（红），修复后同 PR 转绿——复现用例入矩阵，clause 指向被违反的设计条款。
3. **黄金变更独立 PR**（原则 2 红线）：不与实现变更混提；PR 描述必须含出处链。
4. **HIL 会话制**：`hil/` 用例不进日常 CI；台架会话手动触发（半自动，《T3-00》§9-3），产出 HIL 报告（模板归 T3-03）挂验收 gate。
5. **矩阵状态流转**：`planned → building → green`；`waived` 必须含批准人与期限（脚本强制，§7）；`retired` 保位不复用（§3）。

---

## 7. 追溯矩阵载体（机器可查）

`tests/traceability/obligations.yaml`——手工维护义务、脚本核对用例，条目：

```yaml
- clause: "T2-02 §8"                          # 设计出处（册-篇-节）
  obligation: "断连剧本必须判 dirty 而非 rolled_back"
  cases: [C-M2-005]                            # 覆盖用例 ID（可多个）
  layer: C
  status: green                                # planned | building | green | waived | retired
  # waived 时必填：waiver: {approver, expiry, reason}
```

`check_traceability.py`（进 CI gate，《T3-00》§7）四查：

1. `status: green` 的每个用例 ID 必须出现在 `pytest --collect-only` 结果中（防「纸面绿」）；
2. 每个带 `tc` 标记的用例，其 **id 必须出现在矩阵中某义务的 `cases` 列表**，且该义务的 `clause` 与标记的 `clause` 一致（防两类孤儿：无设计出处的噪音/越权用例，以及「借壳」宽泛 clause 而自身 ID 不入账、矩阵跟丢状态的用例）；
3. `waived` 必须含 `approver/expiry` 且未过期（过期即 CI 红）；
4. 用例 ID 全局唯一、`retired` 不被引用。

- 首版矩阵种子 = 《T3-00》§5 挂账盘点（T3-02/03/04/05 落地时逐条编号入库）。
- 矩阵变更与冻结文档升版**同 PR**（《T3-00》§4 变更联动）。

---

## 8. 与现有代码的差量

现状仅 `ChannelSimulationCode/tests/test_protocol.py`（unittest，13/35 ID 时代的黄金帧断言）。本篇落地即新建 `tests/` 分层树、`fakes/`、`fixtures/golden|shared/`、`traceability/`；现有文件按 §2 收编路径迁移；`.vscode`/conda 环境沿用（《CLAUDE.md》运行约定），pytest 依赖入开发 extras。

## 9. 开放问题

1. **测试树相对主包的位置**：顶层 `tests/` 已定，但相对 P0 新平台包（包名/单包 vs 多包）的仓库布局随 P0 落定——只影响 import 路径，不影响本篇分层与命名。
2. hypothesis 的 CI 预算（profile 示例数/deadline）与失败最小化样本的归档位置——T3-05 随 CI gate 定数。
3. 覆盖率分层阈值（除 M1=100% 已冻结外）——T3-05 gate 数值。
4. Playwright 浏览器矩阵（Chromium 单浏览器起步？）——随 M11 实现期定。

## 10. 本篇验收

- P0 首个测试 PR 能按本篇落位（目录/ID/标记/矩阵条目一次到位，无需临时决定）；
- `check_traceability.py` 四查可执行且进 CI；
- 现有 `test_protocol.py` 收编路径明确且保全绿承诺（T2-01 §8）；
- fakes 自证原则有对应用例位（fake 输出过真 codec）。
