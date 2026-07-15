# T2-03 · M3 信道模型引擎集成（功能设计）

> 第二册《功能设计》· 第 3 篇（L3 `ChannelEngineClient` + 引擎侧服务契约）
> 状态：草稿 v0.1 · 待评审
> 依据：《T1-07 引擎集成（微服务，ADR-5）》《T1-03b/03c》（冻结基线）；ChannelEgine 现有 `ChannelSimulator` API 与 `.asc` 产物
> 消费方：M6（统计信道场景任务）、M5/M2（消费转换后的 canonical model）
> 跨仓依赖：**ChannelEgine repo 需新增服务层**（本篇定义其契约；实现为独立工作项）

---

## 1. 概述与定位

M3 落实 ChannelEgine 的**微服务集成**：CEP 主进程经 **`ChannelEngineClient`（薄封装）** 调用独立运行的 **ChannelEgine 服务**，把 38.901 统计信道（GCM/CDL 层，或时变 CIR）转换为 canonical model——与 M4 的 MPDB 导入殊途同归（《T1-07》§4 两源统一）。

**非职责**：38.901 算法本体（引擎侧）；退化编排（→M5 消费 GCM/CDL 模型时复用）；引擎 GUI（弃用，《T1-14》）。

---

## 2. 服务契约（引擎侧 REST API，本篇为规范）

**选型决策**（《T1-07》§6-1 收口）：**REST + JSON（控制面）+ NPZ 二进制（大数组）**。理由：Python↔Python、分钟级任务、无双向流需求——gRPC 的强类型收益小于其运维成本；大张量（时变 CIR）走独立二进制端点避免 JSON 膨胀。

```
GET  /v1/version            → {api_version, engine_version, standard:"3GPP TR 38.901 V19.0.0"}
GET  /healthz               → 200/503
POST /v1/jobs               → 提交生成任务（body=GenerateRequest）→ {job_id}   # 异步
GET  /v1/jobs/{id}          → {status: queued|running|done|failed, error?}
GET  /v1/jobs/{id}/result   → GenerateResult（JSON：簇/径/LSP，见 §3）
GET  /v1/jobs/{id}/cir      → application/octet-stream（NPZ：时变 CIR 张量；仅 want_cir 任务）
```

```python
GenerateRequest = {
  scenario: "UMa"|"UMi"|"RMa"|"InH" | "CDL-A".."CDL-E" | "TDL-A".."TDL-E",
  fc_hz, bandwidth_hz,
  arrays: {tx: AntennaArray, rx: AntennaArray},      # schema 同《T1-03c》§4（序列化复用 M10）
  mobility: {speed_mps, direction_deg} | None,
  lsp_mode: "random" | "median",                     # median=确定性 LSP（可重复测试，引擎已支持）
  seed: int,                                         # ★确定性契约：同 seed+config → 逐比特相同结果
  want_cir: {update_rate_hz, duration_s} | None,     # 时变 CIR（A 档数据）
}
GenerateResult = {
  pathloss_db, is_los, lsps: {...},                  # 引擎 OUTPUT_FORMAT_SPEC 字段
  clusters: [{delay_s, power_linear, aod_az/zen_deg, aoa_az/zen_deg, xpr_db, k_factor?}, ...],
  cir_meta: {n_snapshots, n_taps, update_rate_hz} | None,
}
```

- **确定性（seed）是一等契约**：写入 provenance，支撑「配置即数据、可重放」（《T1-11》§5）与回归测试。
- 鉴权：内网部署默认无鉴权 + 网络隔离；暴露公网时加 token（部署项，T3）。

---

## 3. ChannelEngineClient（L3 薄封装）与 canonical 转换

```python
class ChannelEngineClient:
    async def generate(self, req: GenerateRequest) -> tuple[ChannelModel, EngineReport]:
        self._check_version_compat()                       # /v1/version：api_version 兼容范围校验，不符拒
        job = await self._post("/v1/jobs", req, timeout=SUBMIT_TIMEOUT)
        res = await self._poll_until_done(job, timeout=JOB_TIMEOUT)   # 指数退避轮询
        model = to_canonical(res, req)                      # ↓转换规则
        if req.want_cir:
            cir = await self._get_npz(f"/v1/jobs/{job}/cir")
            attach_cir(model, cir)                          # gain_series 内联 / cir_ref 外置（10MB 规则，《T1-03c》§7）
        return model, report

def to_canonical(res, req) -> ChannelModel:
    # 统计场景(UMa/UMi/...) → level="GCM"；CDL-x 定表 → level="CDL"；TDL-x → level="TDL"（直落 taps）
    # clusters → Environment.links[].clusters[]（字段一一对应：delay_s/power_linear/角度[天顶]/xpr_db/k_factor）
    # want_cir → time.mode="time_varying" + realization="CIR"
    # meta.arrays=req.arrays（自包含）；provenance={source_type:"ChannelEgine_38901",
    #   source_ref:f"{engine_version}", import_config:{**req, seed}}   # seed 入 provenance（可复现）
```

- **两源统一兑现**：产出与 M4 同一 schema——GCM/CDL 模型交 **M5** 沿退化链降到 TDL（`reduce_GCM_to_CDL/reduce_CDL_to_TDL`，《T1-03b》§3）；TDL-x 直落 `channels[].taps`；CIR 交 AscCirBackend。
- 角度约定核对：引擎输出 `zod_theta/zoa_theta` 即天顶角（OUTPUT_FORMAT_SPEC），**零换算**入 schema。

---

## 4. 可靠性（《T1-07》§5 落地）

| 机制 | 规格 |
| :-- | :-- |
| 超时 | 提交/轮询/下载分级超时；任务级总超时 JOB_TIMEOUT（场景可配） |
| 重试 | 提交与查询幂等（job_id 去重）；网络错误指数退避 ≤N 次 |
| 熔断 | 连续失败 → OPEN（快速失败 M6 可见），半开探测 /healthz 恢复 |
| 隔离 | 引擎不可达只影响统计信道生成——**不影响设备控制链路与已有模型**（错误显式上抛，不静默降级） |
| 版本 | api_version 语义化；client 声明兼容范围，不符拒并提示升级路径 |

---

## 5. 部署与跨仓工作项

- 引擎独立容器（含 PyTorch），CEP 主进程零引擎依赖（《T1-09》）。
- **跨仓工作项（ChannelEgine repo）**：新增 `service/` 层——用 FastAPI 包装现有 `ChannelSimulator` 类为 §2 契约；不改动算法与现有 CLI/GUI。该 PR 在 ChannelEgine 仓库进行，契约以本篇为准。
- 兼容基线：以 ChannelEgine 当前 `run_simulation.py`/`OUTPUT_FORMAT_SPEC.md` 的输出字段为 GenerateResult 基础，缺口（seed 贯穿、CIR 端点）在服务层补。

---

## 6. 错误处理

- 请求校验：场景枚举、fc/带宽范围（对照引擎能力声明）、arrays 完整性——提交前在 client 侧预检，错误定位到字段。
- 任务失败：引擎异常栈摘要随 `failed` 状态返回，client 包装为 EngineError（不泄内部路径）。
- 大结果：CIR 超 10MB 走 `cir_ref` 外置（M10 blob），JSON 结果本体保持轻量。

---

## 7. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **契约测试（假引擎）** | 本地 stub 实现 §2 全端点（含 failed/超时/503 剧本） | client 状态机与错误分类正确 |
| **确定性** | 同 seed+config 生成两次 | GenerateResult 与转换后 model 逐字段相等 |
| **转换黄金** | 已知 CDL-A 定表任务 → clusters 与 3GPP 表逐行对照；TDL-A → 直落 taps 对照 | 数值一致（角度天顶零换算） |
| **CIR 附着** | 小 CIR（内联）与 >10MB CIR（外置 ref）各一例 | 10MB 规则正确；NPZ 往返无损 |
| **熔断/恢复** | stub 连续失败→OPEN→healthz 恢复→半开→CLOSED | 状态迁移与 M6 可见错误正确 |
| **版本协商** | api_version 不符 | 拒且提示明确 |
| **隔离性** | 引擎 stub 宕机时执行 M2 设备链路用例 | 设备链路零影响 |

---

## 8. 开放问题
1. 引擎能力声明端点（支持的场景/频率范围/最大阵列）——首版硬编码在 client 预检，后续引擎提供 `/v1/capabilities`。
2. NPZ vs Arrow（大张量格式）——首版 NPZ（numpy 原生零依赖）；与 M10 blob 格式统一时复评。
3. 任务并发/队列深度（引擎侧资源管理）——引擎服务层实现项。
4. ChannelEgine 服务层的仓库 PR 计划与版本发布节奏（跨仓协调）。

## 9. 本篇验收
- 假引擎契约测试全绿；真引擎冒烟：一个 UMa 任务与一个 CDL-A 任务端到端产出合法 canonical model（provenance 含 seed 可复现）。
- GCM/CDL 产物可被 M5 直接消费退化；CIR 产物可被 AscCirBackend 渲染。
- 引擎宕机不影响设备控制链路（隔离性用例）。
