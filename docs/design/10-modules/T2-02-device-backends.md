# T2-02 · M2 设备后端与传输（功能设计）

> 第二册《功能设计》· 第 2 篇（L2 层）
> 状态：草稿 v0.1 · 待评审
> 依据：《T1-08 多设备后端》《T1-11 §1 事务化下发》《T1-12 §0/#1/#5/N4》（冻结基线）；依赖 **M1（T2-01）** 的编码/解析/回显比对
> 消费方：M6（会话编排调用 apply）、M8（消费遥测回调）

---

## 1. 概述与定位

M2 是 **L2 设备后端层**：把 canonical model（`level=TDL`）渲染为设备原生产物并**事务化**送达设备。两个实现：

- **`RFSoCBackend`**：协议 V3.0 帧序列 → TCP 下发 → 回显/遥测闭环（找回并升级原脚本丢失的下发能力）。
- **`AscCirBackend`**：time_varying 模型 → `.asc` 文件集（通用 CIR 交换格式，《T1-12》#3）。

**非职责**：字节编解码与单位换算（→M1）；模型计算/退化（→L3）；会话/场景生命周期（→M6）；遥测的业务解释（→M8）；`DeviceRegistry` 多设备管理（→L3，《T1-10》）。

---

## 2. 接口契约

```python
@dataclass(frozen=True)
class BackendCapabilities:
    # ★无设备专属默认值——每个后端显式实例化，防止文件类后端静默继承 RF-SoC 限制
    supports_time_varying: bool
    supports_cir: bool
    max_paths: int | None            # None = 无硬件上限（文件后端）
    grid: str | None                 # None = 无栅格概念
    if_freq_max_hz: float | None     # None = 无频段约束
    polarization: str
    multi_frame_atomic: bool

RFSOC_CAPS = BackendCapabilities(   # 当前 RF-SoC 设备（《T1-12》#1/#5/N4）
    supports_time_varying=False, supports_cir=False,
    max_paths=24, grid="8x8", if_freq_max_hz=2.6e9,
    polarization="xpr_conducted", multi_frame_atomic=False)
ASC_CAPS = BackendCapabilities(     # .asc 文件后端：无设备物理限制
    supports_time_varying=True, supports_cir=True,
    max_paths=None, grid=None, if_freq_max_hz=None,
    polarization="full", multi_frame_atomic=True)

class DeviceBackend(Protocol):
    capabilities: BackendCapabilities
    def render(self, model: CanonicalChannelModel, addr: ChannelAddress|None) -> Artifact: ...
        # 纯函数（同步）：模型→产物；不触设备（支撑 dry-run 与黄金对比）
    async def apply(self, artifact: Artifact) -> ApplyResult: ...
        # ★异步：送达设备（事务化，内部全 async TCP）或写文件；与 §4 实现签名一致
    async def readback(self) -> TelemetryFrame | None: ...   # asc: None

# 产物与结果
@dataclass(frozen=True)
class FramePlan:                    # RFSoC 产物
    frames: tuple[bytes, ...]       # 每帧自包含 ≤4000B payload（§4）
    manifest: tuple[FrameInfo, ...] # 每帧摘要（组列表/字节数），供审计与 dry-run 展示
@dataclass(frozen=True)
class AscFileSet:                   # Asc 产物：{(in,out): asc_text}
    files: Mapping[tuple[int,int], str]

@dataclass(frozen=True)
class ApplyResult:
    committed: bool
    device_state: Literal["committed", "rolled_back", "dirty"]
    #   dirty = 回滚不可达（如事务中断连，RESET 发不出去）：设备状态未知、可能残留部分配置，
    #           不得谎称已回滚；重连后须先 RESET/重新 apply 才可信（§4/§7）
    frames_sent: int; frames_verified: int
    failure: FailureInfo | None     # 首个失败帧/原因（echo 不符/超时/错误帧/溢出）
    telemetry: TelemetryFrame | None
```

- **render/apply 分离**（《T1-08》§2）：`dry_run` = 只调 render。
- L3 下发前按 `capabilities` 校验（time_varying→rfsoc 拒绝等，能力协商见《T1-08》§6）。

---

## 3. RFSoCBackend.render：子帧编组 → 帧预算切分

设备**不支持多帧原子性**（《T1-12》#5）→ 切分的正确性要求：**每帧自包含、语义组不跨帧、顺序保持**。

### 3.1 子帧分组（组=不可拆的原子单位）

```
G0 前导组   : RESET(13) [+COPY_RETURN(15)=1]                       # 必须整组位于第一帧之首
             # ★INFO_RETURN(14) 不入 G0：单次遥测请求(0x03)由 apply 在 VERIFYING 阶段独立下发——
             #  若随首帧发出，多帧场景下设备在第一帧后即回遥测（反映部分配置），污染核验（§4）
G1[out] 输出参数组: OUTPUT_ATTEN(11)+AWGN_POWER(9)                 # 按输出端（io=0/out），每使用输出端一组
             # ★不含任何使能（AWGN_ENABLE(8) 也是使能，归 G6）——见 G6
G6 使能组   : [AWGN_ENABLE(8)×需开噪声的输出端 +] OUTPUT_ENABLE(10)×每使用输出端 + GLOBAL_ENABLE(1)
             # ★全部写入完成后才使能（最后一帧之尾）——
             # 帧独立生效（T1-12 #5）：若使能在首帧，设备会带着半套配置**提前对外发射**；
             # 「先配置、后使能」使多帧中间态始终静默
G2[ch,k] 清径组: PATH_ENABLE(2)=0                                   # ★仅清「本计划不写」的残留径——
             # 将写的径**不需要前置清**（G0 RESET 已全局归零 + G3 直接覆盖写）；
             # 于是同一条径要么只清、要么只写，「清+写」配对**结构性不存在跨帧拆散**（T1-12 #5 约束）。
             # 有 G0 时 G2 本为防御性冗余（可配开关）；无 G0（reset_first=False）时必须保留。
G3[k] 径组  : ENABLE(2)+DELAY(3)+ATTEN(7)+PHASE(32) (+DOPPLER(4)) (+分数时延(34))   # 同径参数不跨帧
G4[k] 瑞利组: RAYLEIGH_ENABLE(5)+COEFFS(6,1028B 子帧)              # 大组，单独成帧的主要因素
G5 收尾组   : FREQ_PHASE_ZERO(33)（若写了多普勒）                   # 位于最后一帧之尾
```

### 3.2 切分算法（贪心装帧，伪代码）

```python
def plan_frames(groups: list[Group], budget=4000) -> FramePlan:
    frames, cur = [], []
    for g in groups:                        # groups 已按 G0..G5 语义顺序排列
        if size(g) > budget:                # 单组超帧长（理论上仅可能是未来大组）
            raise ValueError("group exceeds frame budget")   # 设计期即保证不发生：1028B 瑞利组 < 4000
        if sum(size(x) for x in cur) + size(g) > budget:
            frames.append(seal(cur)); cur = []
        cur.append(g)
    frames.append(seal(cur))
    assert frames[0].starts_with(G0) and order_preserved(frames)
    return FramePlan(frames=..., manifest=...)
```

- **容量核算**（设计期验证可行性）：单信道满配 24 径 = G0(12B)+G1(~16B)+24×G3(~28B)+G5+G6(~14B) ≈ 0.7KB → **单帧即可**；带瑞利 24×G4(1032B) ≈ 24.8KB → **约 8 帧**。
- **★多信道 = 单一事务单一 FramePlan**：**RESET(13) 是全局复位**——若逐信道对各自成 FramePlan（各带 G0），配第 k 个信道会抹掉前 k−1 个的配置。正确结构：`G0 一次（事务首帧）→ G1[out]×每使用输出端（参数,不含使能）→ 逐信道对的 G2/G3/G4 → G5 → G6 使能组一次（尾帧之尾）`；64 信道满配为多帧、但**整个事务只含一个 RESET**。`plan_frames` 断言补充：**G0 恰一次且在首帧之首；G6 使能组恰一次且在尾帧之尾**（先配置后使能）。
- 每帧经 M1 `build_control_frame` 封装（payload≤4000 由预算保证，双保险仍走 M1 校验）。

---

## 4. RFSoCBackend.apply：事务状态机（核心）

无跨帧原子性 → 事务由上位机补偿（《T1-11》§1）。**回滚基线 = RESET**（协议无参数读回，《T1-A1》§6）。

```
IDLE ──apply(plan)──► APPLYING ──全帧 echo 通过──► VERIFYING ──遥测通过──► COMMITTED
                        │                              │
                        │ echo 不符/超时/错误帧          │ 溢出/电平异常
                        ▼                              ▼
                     ROLLING_BACK ──发 RESET──► ROLLED_BACK(带 FailureInfo)
                        │
                        │ RESET 发送失败/连接已死（无法在原连接回滚）
                        ▼
                      DIRTY（设备状态未知：可能残留部分配置——重连后必须先 RESET/重新 apply，
                            方可再次进入可信状态；不得谎称已回滚）
```

```python
async def apply(self, plan: FramePlan) -> ApplyResult:
    await self._ensure_connected()
    for idx, frame in enumerate(plan.frames):
        await self._tx(frame)
        echo = await self._await_echo(timeout=ECHO_TIMEOUT)     # M1 DownlinkParser 产出
        if echo is ERROR_FRAME or echo is TIMEOUT or not verify_copy_echo(frame, echo):
            state = await self._rollback()   # 尝试 RESET；连接已死/发送失败→返回 "dirty"
            return ApplyResult(committed=False, device_state=state,   # "rolled_back" | "dirty"
                               failure=at(idx, reason), ...)
    # —— VERIFYING：单次遥测请求（不在 G0）。请求帧自身也是控制帧 → 先取走并核验它的回显，
    #    再等遥测帧；否则该 echo 残留队列，会被后续 cadence-restore 的等待错误消费（★P1）：
    req = build_control_frame([SubFrame(ParamID.INFO_RETURN, u8(0x03))])
    await self._tx(req)
    q_echo = await self._await_echo(timeout=ECHO_TIMEOUT)
    if q_echo is ERROR_FRAME or q_echo is TIMEOUT or not verify_copy_echo(req, q_echo):
        state = await self._rollback()                          # 请求未被正确接收 → 按协议错误回滚
        return ApplyResult(committed=False, device_state=state, failure=at("telemetry_req", reason), ...)
    tele = await self._await_telemetry(timeout=TELEMETRY_TIMEOUT)   # 等 0xFDB18541 遥测帧
    if tele is TIMEOUT:                                              # 遥测帧未到 → 无法核验 → 回滚
        state = await self._rollback()
        return ApplyResult(committed=False, device_state=state,
                           failure=at("telemetry_wait", "timeout"), ...)
    # ★先判核验结果，再谈恢复周期档——失败即回滚，给将被 RESET 的配置恢复节奏毫无意义：
    #   核验 = 溢出位图 + 电平合理性（T1-11 冻结契约「无溢出、电平合理」缺一不可）；
    #   电平判据（期望范围/阈值）由 M8 校准模块提供，M2 只调用不定义：
    if (tele.adc_overrange or tele.combiner_overflow or tele.awgn_overflow
            or not self._levels_plausible(tele)):               # M8 注入的电平合理性谓词
        state = await self._rollback()                          # 同样可能 "dirty"，不得丢弃返回值
        return ApplyResult(committed=False, device_state=state,
                           failure=verify_fail(tele), telemetry=tele, ...)
    # —— 成功路径：恢复周期节奏。0x03 = 「单次后关闭」（《T1-A1》），不恢复则 §5 活性信号熄灭。
    #    恢复帧自身也是控制帧（会产生回显），必须同样等待并核验其 echo，防残留污染下一事务：
    restore = build_control_frame([SubFrame(ParamID.INFO_RETURN,   # 子帧经 M1 封装为完整控制帧
                                            u8(self._telemetry_cadence))])   # 恢复 0x01/0x02（配置的周期档）
    await self._tx(restore)
    r_echo = await self._await_echo(timeout=ECHO_TIMEOUT)
    if r_echo is ERROR_FRAME or r_echo is TIMEOUT or not verify_copy_echo(restore, r_echo):
        log.warning("cadence-restore echo 异常")                # 不回滚配置（已核验通过），但记告警并标记活性待观察
    return ApplyResult(committed=True, device_state="committed", telemetry=tele, ...)
```

- **`_rollback()`（可达路径）在 RESET 后 best-effort 重发 INFO_RETURN 周期档**：0x03 已关闭周期遥测、RESET 亦将该参数复位——不恢复则回滚后 §5 活性信号熄灭。恢复帧的 echo 以短超时 best-effort 处理（RESET 后 COPY_RETURN 回默认态，可能不再产生回显），失败仅告警不改变 device_state。
- **逐帧确认**：发下一帧前必须收到上一帧回显并比对通过（简单、可定位失败帧；吞吐留待实测,见 §9-3）。
- **幂等**：同一 FramePlan 重放结果一致（G0 的 RESET 保证从已知基线开始）。
- **回滚语义注意**：协议 RESET **保留瑞利系数与输出衰减**（《T1-A1》§4）→ 回滚后这两类残留；事务上以「回滚 = 回到安全基线（全局/多径禁用）」定义，不承诺字节级还原（记入 ApplyResult 提示）。

---

## 5. Transport（asyncio TCP）与连接管理

```python
class TcpTransport:
    async def connect(host, port, timeout) ; async def close()
    async def send(data: bytes)
    # 接收循环：chunk → M1 DownlinkParser.feed() → 按帧型分发：
    #   CopyEcho → apply 的等待队列；
    #   Telemetry → ★捕获窗口+缓冲：apply 发出 0x03 请求即开启遥测捕获窗（不等 _await_telemetry 挂起）——
    #               窗内到达的遥测帧**入缓冲队列**（遥测可能先于请求回显到达，时序无保证）；
    #               _await_telemetry 从队列消费（已有则立即返回，无则等待）；窗外遥测回调 M8。
    #               不得用「挂起才交付」判定，否则早到帧只进 M8、核验伪超时（★P1 竞态）；
    #   Error → 当前事务失败信号；SerialPassthrough → 透传回调
```

- **活性判据**：协议无心跳——用**周期遥测**（ID14=0.5s/1s）作链路活性信号；超过 N 个周期无遥测 → 判定 degraded。
- **断连/重连**：重连成功后设备状态**未知** → 后端置 `dirty`，L3 需重新 apply 才能回到 COMMITTED（不静默恢复）。
- **并发**：同一设备同一时刻仅一个 apply 事务（后端内互斥）；多设备互斥归 L3（《T1-10》）。

---

## 6. AscCirBackend

```python
def render(model) -> AscFileSet:
    # 要求 model.realization=CIR 或由 static taps 采样生成；每信道对一份 .asc：
    # header: N CIRs / T Taps/CIR / update_rate / carrier_freq（《T1-08》§4.2）
    # 行: 每时刻 T 组 [delay_s, re, im]；天顶→仰角等消费者侧换算在此进行（《T1-03c》§2）
async def apply(fileset) -> ApplyResult:   # 写文件（路径策略经 M10 I/O 适配层）；committed=写盘成功
async def readback() -> None
```
- capabilities = `ASC_CAPS`（§2：无频段/径数/栅格约束，A 档数据的现实出口，《T1-12》#1/#3）。

---

## 7. 错误处理（分类）

| 类别 | 触发 | 处置 |
| :-- | :-- | :-- |
| 传输错误 | 连接失败/中途断连/发送异常 | 重试(有限次)→degraded；**事务中断连**：RESET 不可达→置 **dirty**（`device_state="dirty"`，不谎称已回滚）；重连后先 RESET/重 apply 才可信 |
| 协议错误 | 错误帧(FDB185FF)/echo 不符/echo 超时 | 立即回滚；FailureInfo 带帧号与差异摘要 |
| 设备异常 | 遥测溢出位/电平越限 | 回滚；遥测快照随结果返回（供 M8/GUI） |
| 能力拒绝 | 模型要求超出 capabilities | render 前即拒（不触设备），明确错误 |
- 所有 apply（含失败）写审计日志（《T1-11》§3）：时间/设备/FramePlan manifest 摘要/结果。

---

## 8. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **切分属性** | 随机模型（含**多信道对**）生成 FramePlan：每帧 payload≤4000B；组不跨帧；顺序保持；**G0 全计划恰一次**且在首帧头、**G6 使能组在尾帧尾（使能前所有帧不得含使能子帧）** | 属性全成立（含 24 径+瑞利满配 ≈8 帧、64 信道多帧单 RESET 的边界例） |
| **事务（假设备）** | 本地 asyncio TCP 假设备（回显/篡改 1 字节/超时/回错误帧/中途断连/遥测置溢出位六种剧本） | commit/rollback/**dirty** 判定与 FailureInfo 定位正确；可达时回滚必发 RESET；**断连剧本必须判 dirty 而非 rolled_back** |
| **幂等** | 同一 FramePlan 连发两次 | 帧序列字节一致、结果一致 |
| **dry-run** | render 不建立任何连接（socket 层断言） | 纯函数性 |
| **AscCir 黄金** | 已知 canonical → .asc 与 ChannelEgine 样例格式逐行比对 | 格式/数值一致 |
| **能力协商** | time_varying 模型 → RFSoCBackend | render 前被拒且不触网络 |
| **环回集成** | 假设备跑通「render→apply→commit」全链（T3-03 HIL 的软前身） | 端到端绿 |

---

## 9. 开放问题
1. **RESET 残留**（瑞利系数/输出衰减不被复位）与回滚语义的对外表述——是否需要「深度回滚」（显式写默认值覆盖）选项。
2. **遥测竞态**：apply 期间周期遥测与单次请求（ID14=0x03）并存时的归属判定（以请求后首帧为准？）——实现时与设备实测。
3. **逐帧确认的吞吐**：64 信道满配多帧串行 RTT 累积；若实测过慢，再评估流水化（需设备回显顺序保证，硬件确认）。
4. 帧间是否需要 pacing（设备处理间隔）——实测定。

## 10. 本篇验收
- FramePlan 切分满足「自包含/组不跨帧/顺序/预算」四属性且容量核算成立。
- 假设备六剧本下事务判定全部正确；dry-run 零副作用。
- M6 可仅凭本模块接口（render/apply/readback/capabilities）编排会话。
