# 05 · RT/MPDB → TDL 导入管线（总体设计）

> 第一册《总体设计》· 第 5 篇（旗舰链路上半段）
> 状态：**v1.0 · 已冻结**（2026-07-15，tag: design-t1-v1.0）· §5 多普勒来源已随 MPDB 手册 v1.1（HyperRT v3.2.6 逐径 DOPPLER 列）修订（2026-07-16 PR）
> 前置：《03-architecture》；配套下半段《06-correlation-mimo》

---

## 1. 概述

本篇定义**旗舰功能 UC2 的上半段**：把 RT 射线结果（经 MPDB）转成 canonical model 的 TDL 抽头。相关性合成（角度→R）在《06》，多后端渲染在《08》。

输入：MPDB（HyperRT）；输出：canonical model（`time.mode` 默认 static，taps + 角度元数据）。**不支持 .mat**。

> 层级视角（《03b》）：MPDB 导入是**在 RT 层入口进入退化链**，本篇管线执行 RT→(CDL)→TDL 的退化，落到参数化 TDL 面。用户亦可在 GCM/CDL/TDL 层直接输入（《03b》§4）。

---

## 2. 输入契约：MPDB（见《MPDB接口的使用.md》）

- **LINK 表**：`TX, RX, TX_ANT_POSITION(x,y,z), RX_ANT_POSITION(x,y,z)`。
- **CHANNEL 表**（每行一条径）：`LINK_ID, DELAY[s], H[复], AOA, ZOA, AOD, ZOD[deg], CHANNEL_TYPE(0=LoS)`。
- **接入方式（已定）**：**HyperRT 直接导入，无中间件**——`mpdb_reader` 直接 `from HyperRT.MiRT.MPDB import MPDB; MPDB.load(path)`（依赖 HyperRT SDK + PyTorch）。**不采用** MPQL 导出 CSV 的解耦路径。
- 依赖后果：导入侧引入 HyperRT SDK + PyTorch（可置于独立导入进程隔离，见《03》§6、《09》）。
- `mpdb_reader`（L1）产出与来源无关的**原始径表**，供上层复用。

---

## 3. 管线阶段（伪代码）

```
def import_mpdb_to_canonical(source, array_geometry, cfg) -> CanonicalChannelModel:
    # 1. 读取（L1 mpdb_reader）
    links, rays = mpdb_reader.load(source)      # rays: LINK_ID,DELAY,H,AOA,ZOA,AOD,ZOD,TYPE

    # 2. 单位归一（L1，唯一定义处）
    rays.delay_ns   = rays.DELAY * 1e9          # s → ns（渲染时再量化到码值）
    #   角度：canonical 保留【天顶角 θ】(与 MPDB/3GPP 一致，见《03c》)；
    #        方位角 AOA/AOD 直接保留；H 为复增益。
    #        仰角转换 el=90°−θ 延后到需要它的消费者（如 .asc 导出），不在此处做。

    model = CanonicalChannelModel(meta=..., time=Static(), grid=cfg.grid)

    # 3. 逐信道对（input,output）构造 TDL —— 信道对由 LINK/阵列映射决定（见《06》§阵列映射）
    for (inp, out, link_ids) in map_links_to_channel_pairs(links, array_geometry, cfg):
        sub = rays.filter(LINK_ID in link_ids)

        # 3a. 量化到硬件时延格点（沿用现有 tdl.py 逻辑）
        sub.delay_code = round(sub.delay_ns / PATH_DELAY_UNIT_NS)   # 1000/120 ns
        sub = sub.filter(0 <= delay_code <= 1050)                   # 协议范围
        sub = drop_invalid(sub)                                     # NaN/Inf/零功率

        # 3b. 同一 delay bin 合并（相干/非相干可配）
        taps = []
        for code, grp in sub.groupby(delay_code):
            coh   = sum(grp.H)                       # 相干叠加（复）
            ncoh  = sum(abs(grp.H)**2)               # 非相干功率
            power = ncoh if cfg.power_mode=="noncoherent" else abs(coh)**2
            # ★对齐注记（《T1-15》D6）：合并后 tap 复增益恒为相干叠加 coh（保相位）——
            #   power_mode 只影响排序/统计功率，模块级精化以《T2-04》§5 为准
            taps.append(Tap(
                delay_s   = code * PATH_DELAY_UNIT_NS * 1e-9,
                gain      = coh,                     # 复增益（保相位）
                power     = power,
                doppler_hz= bin_doppler(grp, cfg),   # ★按 §5 优先级链：上游 DOPPLER 列（bin 内功率
                                                     #   加权平均，T2-04 §5）> velocity_tx/rx 双端重算 > default
                angles    = weighted_angles(grp),    # 角度→交《06》算相关
            ))

        # 3c. 选最强 ≤24 径，按时延排序，归一化（最强径为幅度 1）
        taps = topk_by_power(taps, k=min(cfg.max_paths, 24))
        taps = normalize_amplitude(sort_by_delay(taps))

        model.channels[(inp,out)] = Channel(taps=taps)

    # 4. 相关性合成（交《06》：角度+阵列几何 → R → 逐信道确定性权重）
    CorrelationSynthesizer(cfg.correlation).apply(model, array_geometry)

    return model
```

> 阶段 3a/3b/3c **复用现有 `channel_simulator/tdl.py`** 的量化—合并—选径—归一化逻辑（当前针对 .mat/RaysCTF；改造为消费 mpdb_reader 的径表 + 保留角度列）。

---

## 4. 与现有 tdl.py 的差异（改造点）

| 方面 | 现 `tdl.py`（.mat/RaysCTF） | 本管线（MPDB） |
| :--- | :--- | :--- |
| 数据源 | `sio.loadmat` + RaysProperties/RaysCTF | `mpdb_reader`（Python API/MPQL） |
| 角度信息 | **无** | **有**（AoA/ZoA/AoD/ZoD）→ 支撑真实相关 |
| 天线坐标 | 无 | LINK 表提供 → 阵列映射 |
| 输出 | pandas DataFrame（单信道） | canonical model（多信道对 + 角度元数据） |
| 相关性 | 无 | 一等属性（《06》） |
| 时延单位 | s→ns（相同） | s→ns（相同） |

保留：量化 `1000/120 ns`、`0..1050` 范围裁剪、相干/非相干合并、topk 选径、幅度归一化——这些是资产，直接迁移。

---

## 5. 多普勒来源（时变的前提）

- **多普勒来源与优先级**（2026-07-16 修订：MPDB 手册 v1.1/HyperRT v3.2.6 起 CHANNEL 有逐径 `DOPPLER` 列）：
  0. **上游 DOPPLER 列直读（最优先）**：HyperRT ≥3.2.6 在 RT 求解后按 `f_d = (f_c/c)(v_TX·k̂_TX − v_RX·k̂_RX)` 解析计算逐径多普勒（**双端投影**，LOS/NLOS 均成立、散射体静止假设）——列在场即零换算（Hz）直读入 `Ray.doppler_hz`（《T1-03c》§2，v1.2）；退化到 TDL 时 bin 内按**功率加权线性平均**聚合入 `Tap.doppler_hz`（T2-04 §5，与手册 POWER_WEIGHTED_MEAN_DOPPLER 同口径）。上游解析值优于平台从量化后角度重建 k̂ 再投影。
  1. **默认 0**（B 档准静态回放，`time.mode=static`——无列且无 velocity）。
  2. **平台几何重算（fallback）**：列缺失且给 `velocity_tx`/`velocity_rx`（各自可选，缺省视为静止——与上游生成语义一致）时由角度+双端速度重算——公式与上游同式（双端投影；旧文单端 `f_d=(v·k̂)/λ` 是 RX-only 特例，`λ=c/f_c`）。列与速度矢量同在 → **列优先**，冗余一致性校验（偏差超容差告警——双源纪律同 portmap，T2-04 §4）。
  3. **多快照序列**：多个 MPDB 时刻 → `time.mode=time_varying`，由相位/位置演化估多普勒（A 档时间维前提）；逐快照 DOPPLER 列同样按 (0) 直读。
- 本期 P1 落 (0)/(1)/(2)；(3) 随 A 档推进（见《06》《13》）。上游 `MAX_ABS_DOPPLER` 聚合是采样充分性校验（《T1-15》Q0 裁定 1）对 MPDB 源的 **f_d_max 权威来源**。多快照/轨迹的**设计基线已立**（跨快照跟踪/槽位稳定/两档出口《T1-15》Q2；B+ 参数流式播放《T1-15》§7）。

---

## 6. 校验与边界（错误处理）

- **schema 校验**：MPDB 必需列存在、类型正确；缺列/空表 → 明确错误。
- **范围裁剪**：时延超 `0..1050` 的径丢弃并计数上报（不静默）。
- **退化保护**：全被过滤 → 报「无有效径」而非产空模型（沿用现有 `ValueError` 风格）。
- **单位/坐标**：canonical 保留天顶角 θ（《03c》）；s→ns 在导入边界换算一次；仰角换算延后到需要它的消费者（如 `.asc` 导出），只换一次，杜绝重复换算。
- **可复现**：导入配置（power_mode、max_paths、多普勒策略、阵列几何）随 scenario 持久化。

---

## 7. 开放问题
1. ~~MPDB 接入方式~~ → **已定：HyperRT 直连（无中间件）**（见 §2、《12》#4）。
2. LINK→信道对（input,output）的映射规则（**重要设计**）：取决于天线阵列与栅格如何对应；本期**留给本模块 M4 完善**（与《06》§6 阵列映射共用 `map_links_to_channel_pairs`，《12》#6）。
3. 交叉极化（H 的极化分量 / VV,VH,HV,HH）在 canonical model 的表示与设备可承载度。

## 8. 本篇验收
- MPDB 一条链路可端到端产出 canonical model（taps+角度），单位/坐标正确。
- 现有 tdl.py 的量化—合并—选径逻辑成功迁移并被测试覆盖。
- 与《06》的接口（角度元数据、阵列几何）契合无缝。
