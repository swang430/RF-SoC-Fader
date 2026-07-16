# T2-05 · M5 相关性合成（功能设计）

> 第二册《功能设计》· 第 5 篇（L3 · 旗舰 B 方案核心算法）
> 状态：草稿 v0.1 · 待评审
> 依据：《T1-06 MIMO 相关性（§6bis B 方案端到端）》《T1-03b 退化链》《T1-03c schema》（冻结基线）；依赖 **M4（T2-04）** 的 RT 模型/PortMap/量化合并函数集
> 消费方：M6（编排调用）、M2（消费产出的 TDL 模型渲染）、M11（R 热图可视化）

---

## 1. 概述与定位

M5 实现 **`CorrelationSynthesizer` 策略**与 **RT→TDL 退化编排**：把 M4 产出的 `level=RT` 模型退化为 `level=TDL` 模型（taps + 相关矩阵 R），落实《T1-06》B 方案 Phase 2–6 与三条正确性要点。

- **B 档（默认实现）**：几何/确定性相关精确复现 + 衰落边缘统计——本篇主体。
- **A 档（预留 stub）**：time_varying+CIR 载荷,守卫按 capabilities 拒绝（《T1-12》#1）。

**非职责**：MPDB 读取/PortMap 校验/量化合并机制（→M4，本篇只编排调用）；协议码值与下发（→M2/M1）；瑞利谱型功率归一化的数值标定（→M8，本篇留 hook）。

---

## 2. 接口契约

```python
class CorrelationSynthesizer(Protocol):
    def reduce_to_tdl(self, model: ChannelModel, portmap: PortMap,
                      cfg: SynthesisConfig) -> tuple[ChannelModel, FidelityReport]: ...
    # 输入 level ∈ {RT, GCM, CDL}（M4 的 RT / M3 引擎的 GCM/CDL——两源统一消费，T2-03 修订）；
    # 输出 level=TDL（taps+correlation），provenance.reduced_from=model.id
    #
    # ★簇路径（level=GCM/CDL）：environment.links[].clusters[] 的簇中心角/功率/时延
    #   视作"径集"执行与 §3 相同的导向/塌缩/量化/共享归一编排（引擎产出的簇已是带 seed 的
    #   抽样实现，GCM 与 CDL 在退化上同构）；簇内角扩展本期不展开子径（记 FidelityReport，
    #   子径展开为后续增强）；XPR/K 按簇透传入 Tap

@dataclass(frozen=True)
class SynthesisConfig:
    mode: Literal["B"]                  # A 档另走 ATimeVaryingSynthesizer（§6）
    power_mode: Literal["coherent","noncoherent"]
    max_paths: int                      # ≤ 目标 capabilities.max_paths
    velocity_mps: Vec3 | None           # 几何多普勒（可选，《T1-05》§5）
    rayleigh: RayleighSpecConfig | None # 衰落边缘统计（可选；功率归一化 hook→M8）
    cluster_phase_seed: int = 0         # ★簇相位兜底种子：GCM/CDL 输入既无 Cluster.phase_rad 也无
                                        #   provenance 载体时（如用户直录 3GPP CDL 定表，表无相位列）
                                        #   按此确定性合成（§3 cluster_phase 优先级③）

@dataclass(frozen=True)
class FidelityReport:                   # §5 数值核验产物
    r_target: ndarray                   # 目标 R（角度+几何直接计算）
    r_realized: ndarray                 # 由产出 taps 反构的 R̂
    frobenius_rel_err: float            # ‖R̂−R‖F / ‖R‖F
    per_channel_tap_counts: Mapping[tuple[int,int], int]
    quant: QuantReport                  # 量化丢弃统计（透传 M4）
```

- 错误前置：`model.level ∉ {RT,GCM,CDL}` 拒（TDL 无需退化、CIR 走 A 档）；`single_reference` 而 `meta.arrays` 缺失拒；`mode=A` 走 §6 守卫。**portmap 入参与 `Meta.port_map`（v1.1）必须一致**——编排层（T2-06）传入的就是模型所携那份；不一致=调用方缺陷，拒（防两源漂移）。

---

## 3. B 方案编排（核心伪代码，落实《T1-06》§6bis）

```python
def reduce_to_tdl(model, portmap, cfg):                          # ★形参即 §2 契约的 model（level∈{RT,GCM,CDL}）
    lam = C / model.meta.center_frequency_hz                     # λ = c/fc
    pairs = {}                                                    # (in,out) → 阵元对射线集（复增益已含导向相位）

    def rays_of(link):                                            # ★层级适配：RT=真径；GCM/CDL=簇→伪径
        if model.level == "RT": return link.rays
        return [PseudoRay(delay_s=c.delay_s,
                          gain=sqrt(c.power_linear)*exp(1j*cluster_phase(model, link, k)),
                          # cluster_phase 取相位优先级（单一实现）：
                          # ①Cluster.phase_rad（v1.1 一等字段——主读，引擎产出必带）
                          # ②provenance.import_config["cluster_phases"][link][k]
                          #   （v1.0 旧模型兼容读——过渡载体，簇序=引擎输出序）
                          # ③两者皆缺（如用户直录 CDL 定表，表无相位）→
                          #   PRNG(cfg.cluster_phase_seed) 按 (link,k) 确定性合成 U(0,2π)，
                          #   种子记入输出 provenance 与 FidelityReport——可复现、不拒
                          #   （任一层输入原则，T1-03b）
                          angles=cluster_center_angles(c)) for k, c in enumerate(link.clusters)]
                          # ★k 由 enumerate 绑定：簇序号即 phase_rad 的索引键（引擎按簇序输出）
        # 簇初相 phase_rad 来自引擎（seed 确定，T2-03 §2）；★schema v1.1 起为 Cluster 一等字段
        # （升级项③已落地，T1-03c §5.1）——provenance 载体仅作 v1.0 旧模型兼容读；
        # 簇内角扩展本期不展开（§2 注）

    if portmap.link_mode == "per_element_pair":
        # RT 已逐阵元对算径（几何真值，含近场效应）——无需导向合成
        for link in model.environment.links:
            io = portmap.map(link.tx_index, link.rx_index)        # ElementKey→(input,output)
            pairs[io] = [(r.delay_s, r.gain, angles(r)) for r in rays_of(link)]
    else:  # single_reference：Phase 2 导向合成（★必须先于量化合并——正确性要点 1）
        ref = model.environment.links[0]
        for m in tx_elements(model.meta.arrays):
            for n in rx_elements(model.meta.arrays):
                io = portmap.map(m, n)
                pairs[io] = [
                    (r.delay_s,
                     r.gain * steer(model.meta.arrays.tx, m, r.aod_az_deg, r.aod_zen_deg, lam)
                            * conj(steer(model.meta.arrays.rx, n, r.aoa_az_deg, r.aoa_zen_deg, lam)),
                     angles(r))
                    for r in rays_of(ref)]

    # Phase 3：逐信道对 量化→合并→选径（调 M4 函数集；输入已带导向相位）
    binned = {}
    for io, rays in pairs.items():
        codes, quant = quantize_delays([d for d,_,_ in rays])     # 超范围丢弃+计数（M4 契约）
        bins = merge_bins(codes, gains)                           # 同 bin 复增益相干叠加（保相位）
        binned[io] = select_strongest(bins, k=cfg.max_paths, power_mode=cfg.power_mode)

    # Phase 4：★全系统共享归一化基准（正确性要点 2）——先全局扫描再归一
    ref_power = max(max(b.power for b in bins) for bins in binned.values())
    taps = {io: normalize(bins, ref=sqrt(ref_power)) for io, bins in binned.items()}

    # Phase 5：目标相关矩阵（供核验/GUI；R 不进设备帧——《T1-03b》）
    R_tx, R_rx = correlation_from_angles(model, model.meta.arrays, lam)  # §4
    R = kron(R_tx, R_rx)

    # 可选：几何多普勒 f_d=(v·k̂_arr)/λ 逐径；瑞利谱 spec（功率归一化交 M8 hook）
    apply_doppler_and_rayleigh(taps, cfg, lam)

    tdl = assemble_tdl_model(model, taps, correlation=(R_tx,R_rx,R), reduced_from=model.id)
    return tdl, verify_fidelity(tdl, R, quant)                    # §5
```

**方向矢量约定**（天顶角体系，《T1-03c》）：`k̂(φ,θ) = [sinθ·cosφ, sinθ·sinφ, cosθ]`；
`steer(array, m, φ, θ, λ) = exp(j·2π/λ · p_m · k̂(φ,θ))`。全程 complex128。

**两模式的语义差异**（诚实边界）：`per_element_pair` 是 RT 几何真值（含近场/遮挡差异）；`single_reference` 是**远场平面波近似**下的导向合成——近场场景两者会偏离，模型选择责任在导入方（M4 已核验形态，本篇在 FidelityReport 注记所用模式）。

---

## 4. 目标相关矩阵计算（《T1-06》§2 落地）

```python
def correlation_from_angles(model, arrays, lam):
    # 径集：per_element_pair 用参考功率谱（所有链路射线并集）；single_reference 用参考链路
    #   ——径集一律经 §3 rays_of 适配取得（GCM/CDL 即簇伪径，角度=簇中心角）
    R_tx = Σ_k P_k · a_tx(φ_dep,k, θ_dep,k) · a_tx(...)ᴴ         # M_tx×M_tx
    R_rx = Σ_k P_k · a_rx(φ_arr,k, θ_arr,k) · a_rx(...)ᴴ
    R_tx /= trace(R_tx)/M_tx ; R_rx /= trace(R_rx)/M_rx           # 对角归一
    return R_tx, R_rx
```
- 存入 `model.correlation{source="angles_geometry", r_tx, r_rx, r}`（物化缓存，《T1-03c》§6——主来源仍是角度+几何，可复算）。
- Kronecker 为可分离近似；非可分离升级路径见《T1-06》§2.3（接口不变）。

---

## 5. 数值核验（Phase 8 落地，验收核心）

```python
def verify_fidelity(tdl, R_target, quant) -> FidelityReport:
    H = reconstruct_H(tdl)          # 由 taps 复增益反构窄带 MIMO 矩阵：H[m,n] = Σ_k gain_k(io=map(m,n))
    R_hat = outer(vec(H), conj(vec(H)))                # 单快照实现相关（确定性分量）
    R_hat /= trace(R_hat)/dim
    err = frobenius(R_hat - R_target) / frobenius(R_target)
    return FidelityReport(..., frobenius_rel_err=err, ...)
```
- **验收阈值**：理想 fixture（无量化）err ≈ 0（<1e-10）；经 24 径截断+时延量化后 err 上报不判死（阈值由场景验收定，GUI 展示）。
- 该函数亦供 M11 可视化（R vs R̂ 热图并排）与 T3 回归。

---

## 6. A 档预留与守卫

```python
class ATimeVaryingSynthesizer:      # stub：接口冻结，不实现
    def reduce_to_tdl(...): raise CapabilityError(
        "correlation.mode=A 需设备支持 CIR 注入；当前 RF-SoC 不支持（T1-12 #1）。"
        "可改用 AscCirBackend 导出 .asc，或使用 mode=B")
```
- 选择逻辑：`mode=A` 且目标 backend `supports_cir=False` → 上述错误（render 前拒，不触设备）；`supports_cir=True`（未来设备/固件）→ 按 capabilities 放行（《T1-12》§0 通用 fader 原则）。

---

## 7. 错误处理

| 触发 | 处置 |
| :-- | :-- |
| `model.level ∉ {RT,GCM,CDL}` / **Meta.port_map 缺失**（v1.1 一等字段；v1.0 旧模型经迁移钩子从 provenance 载体升格，仍缺才拒） | 拒（指明合法入口：M4 导入或 M3 引擎生成） |
| 某信道对合并后 0 有效径 | 该信道对产出空 taps（M2 渲染为不使能该信道）+ FidelityReport 计数，不整体失败 |
| `mode=A` + 设备无 CIR 能力 | CapabilityError（§6） |
| 数值异常（NaN 传播/奇异 R） | 拒并报出问题径/信道对定位 |

---

## 8. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **黄金几何（解析例）** | 2×2 半波长 ULA、单径 broadside(θ=90°,φ=0)：R 应为全 1 秩 1 矩阵；斜入射角 θ 已知：阵元间相位差 = 2π/λ·d·cosθ（天顶角约定） | 与解析值逐元素 ≤1e-10 |
| **顺序纪律** | 同一 fixture "先导向后合并" vs 反序 | 反序结果偏离解析值、正序命中（要点 1 的可测化） |
| **共享基准** | 两信道对功率比已知的 fixture | 归一化后相对功率保持（非各自归 1）（要点 2） |
| **两模式一致性** | 远场平面波 fixture 同时构造 per_element_pair 真值与 single_reference 合成 | 两者 taps/R 偏差 ≤ 容差；近场 fixture 允许偏离并在报告注记 |
| **保真闭环** | verify_fidelity：理想 fixture err<1e-10；量化/截断后 err 单调合理 | 阈值与单调性 |
| **多普勒** | velocity 注入：f_d = v·k̂/λ 逐径解析对照 | 数值一致 |
| **A 档守卫** | mode=A × RFSOC_CAPS | CapabilityError，零副作用 |
| **空信道对** | 某 io 全径被量化丢弃 | 空 taps + 计数，整体不失败 |

---

## 9. 开放问题
1. **Kronecker vs 全相关**：可分离近似的精度边界（《T1-06》§7-4）；FidelityReport 已给量化手段，阈值随场景验收定。
2. **近场**：single_reference 平面波近似在近场的误差界；建议近场场景一律要求 per_element_pair 输入（导入侧提示归 M4/M11）。
3. **XPR 来源**：RT 单极化 H 无 XPR 信息，传导模式 Tap.xpr_db 留空/用户配置；HyperRT 极化输出落地后升级（《T2-04》§8-2）。
4. **瑞利谱与 M8 衔接**：spec 写入时的谱型功率归一化系数由 M8 提供（《T1-12》#2），本篇仅留 hook。

## 10. 本篇验收
- 黄金几何解析例全绿（含顺序纪律与共享基准的可测化验证）。
- 一条 MPDB 链路经 M4→M5 产出 level=TDL 模型：taps+R 完整、provenance 可溯、M2 可直接渲染。
- verify_fidelity 闭环可量化 B 档保真度，供 GUI/T3 消费。
