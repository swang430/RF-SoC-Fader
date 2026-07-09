# 05 · RT/MPDB → TDL 导入管线（总体设计）

> 第一册《总体设计》· 第 5 篇（旗舰链路上半段）
> 状态：草稿 v0.1 · 待评审
> 前置：《03-architecture》；配套下半段《06-correlation-mimo》

---

## 1. 概述

本篇定义**旗舰功能 UC2 的上半段**：把 RT 射线结果（经 MPDB）转成 canonical model 的 TDL 抽头。相关性合成（角度→R）在《06》，多后端渲染在《08》。

输入：MPDB（HyperRT）；输出：canonical model（`time.mode` 默认 static，taps + 角度元数据）。**不支持 .mat**。

---

## 2. 输入契约：MPDB（见《MPDB接口的使用.md》）

- **LINK 表**：`TX, RX, TX_ANT_POSITION(x,y,z), RX_ANT_POSITION(x,y,z)`。
- **CHANNEL 表**（每行一条径）：`LINK_ID, DELAY[s], H[复], AOA, ZOA, AOD, ZOD[deg], CHANNEL_TYPE(0=LoS)`。
- **接入方式**（二选一，见《12》待定）：
  - Python API：`from HyperRT.MiRT.MPDB import MPDB; MPDB.load(path)`（依赖 HyperRT SDK + PyTorch）。
  - MPQL 导出 CSV：`./HyperRT -m ...MPQL --db x.mpdb -e "SELECT ... WRITE_CSV(...)"` → 平台读 CSV（解耦 SDK）。
- `mpdb_reader`（L1）抽象上述差异，产出与来源无关的**原始径表**。

---

## 3. 管线阶段（伪代码）

```
def import_mpdb_to_canonical(source, array_geometry, cfg) -> CanonicalChannelModel:
    # 1. 读取（L1 mpdb_reader）
    links, rays = mpdb_reader.load(source)      # rays: LINK_ID,DELAY,H,AOA,ZOA,AOD,ZOD,TYPE

    # 2. 单位/坐标归一（L1，唯一定义处）
    rays.delay_ns   = rays.DELAY * 1e9          # s → ns
    rays.el_arr     = 90 - rays.ZOA             # Zenith → Elevation
    rays.el_dep     = 90 - rays.ZOD
    #   方位角 AOA/AOD 直接保留；H 为复增益（幅度+相位）

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
            taps.append(Tap(
                delay_s   = code * PATH_DELAY_UNIT_NS * 1e-9,
                gain      = coh,                     # 复增益（保相位）
                power     = power,
                doppler_hz= cfg.default_doppler,     # 单快照无多普勒，见 §5
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

- 单 MPDB 快照**无多普勒**（几何静止）。选项：
  1. **默认 0**（B 档准静态回放，`time.mode=static`）。
  2. **几何速度注入**：给 TX/RX 速度矢量，由到达角 + 速度算每径多普勒 `f_d = (v·k̂)/λ`。
  3. **多快照序列**：多个 MPDB 时刻 → `time.mode=time_varying`，由相位/位置演化估多普勒（A 档时间维前提）。
- 本期 P1 落 (1)/(2)；(3) 随 A 档推进（见《06》《13》）。

---

## 6. 校验与边界（错误处理）

- **schema 校验**：MPDB 必需列存在、类型正确；缺列/空表 → 明确错误。
- **范围裁剪**：时延超 `0..1050` 的径丢弃并计数上报（不静默）。
- **退化保护**：全被过滤 → 报「无有效径」而非产空模型（沿用现有 `ValueError` 风格）。
- **单位/坐标**：Zenith/Elevation、s/ns 只在导入边界换算一次，杜绝重复换算。
- **可复现**：导入配置（power_mode、max_paths、多普勒策略、阵列几何）随 scenario 持久化。

---

## 7. 开放问题
1. MPDB 接入方式（SDK 直读 vs MPQL 导出 CSV）——依赖与部署权衡，见《12》。
2. LINK→信道对（input,output）的映射规则：取决于天线阵列与栅格如何对应（与《06》阵列映射共用定义）。
3. 交叉极化（H 的极化分量 / VV,VH,HV,HH）在 canonical model 的表示与设备可承载度。

## 8. 本篇验收
- MPDB 一条链路可端到端产出 canonical model（taps+角度），单位/坐标正确。
- 现有 tdl.py 的量化—合并—选径逻辑成功迁移并被测试覆盖。
- 与《06》的接口（角度元数据、阵列几何）契合无缝。
