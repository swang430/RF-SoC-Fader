# T2-04 · M4 RT-MPDB 导入与阵列映射（功能设计）

> 第二册《功能设计》· 第 4 篇（L1 `mpdb_reader` + L3 导入管线）
> 状态：草稿 v0.1 · 待评审
> 依据：《T1-05 导入管线》《T1-03c schema（§2 MPDB 映射为规范）》《T1-12 #4/#6》（冻结基线）；《MPDB接口的使用.md》
> 消费方：**M5**（消费 RT 模型执行导向/塌缩/选径编排）、M6（导入任务生命周期）、M10（配置持久化）
> 承载：《T1-12》#6 **阵列↔栅格映射（port_map，重要设计）**

---

## 1. 概述与定位

M4 把 **MPDB（HyperRT 直连）读成 canonical model 的 RT 层**，并提供两块可复用机制：

1. **`mpdb_reader`（L1）**：MPDB → 与来源无关的原始径表（纯数据，无业务）。
2. **导入管线（L3）**：径表 → `ChannelModel{level=RT, environment.links[].rays[]}`（物理量、天顶角、自包含阵列几何、provenance）。
3. **阵列↔栅格映射 `PortMap`**：LINK/阵元 ↔ 设备端口的映射规则与校验（M5/M2 共用）。
4. **量化合并纯函数集**（自 `tdl.py` 迁移）：供 M5 在**导向之后**调用（顺序纪律见 §5）。

**非职责**：导向相位/相关塌缩/R 计算/选径编排（→M5，B 方案 Phase 2–5）；协议码值（→M2 渲染时经 M1）；`.mat` 一律拒绝（《T1-01》）。

---

## 2. mpdb_reader（L1，HyperRT 直连）

```python
# 依赖边界：HyperRT SDK + PyTorch 仅在本模块内 import（惰性），出口一律 numpy/内置类型——
# 主进程其余部分不感知 torch（《T1-09》依赖隔离；部署形态见《T1-12》#4）
def load(path: str) -> RawRtTable:
    db = HyperRT.MiRT.MPDB.MPDB.load(path)                    # 直连，无中间件（已定）
    link  = require_columns(db.link,  ["TX","RX","TX_ANT_POSITION","RX_ANT_POSITION"])
    chan  = require_columns(db.channel, ["LINK_ID","DELAY","H","AOA","ZOA","AOD","ZOD","CHANNEL_TYPE"])
    return RawRtTable(
        links = to_numpy(link),          # tx/rx 索引 + 收发天线世界坐标(m)
        rays  = to_numpy(chan),          # 每径: delay[s], H[complex], 角度[deg, 天顶], type
    )
```

- **schema 校验**：缺列/空表/维度不符 → `ValueError`（列名与期望型别入错误信息）。
- **不做任何单位换算**：DELAY 保持秒、角度保持度·天顶角——与 canonical 约定一致（《T1-03c》§2），**导入是"结构搬运"**；仰角换算只发生在需要它的消费者（.asc 导出）。

---

## 3. 阵列↔栅格映射 PortMap（重要设计，《T1-12》#6）

### 3.1 数据结构与两种链路模式

```python
ElementKey = int | tuple[int, PolBranch]       # 传导：阵元号；OTA 双极化：(阵元号, 极化支路 "+45"/"-45"...)
                                               # ——键型必须能承载 (element,pol)，否则 V4 的双极化独立端口无法表示

@dataclass(frozen=True)
class PortMap:
    tx_element_to_input:  Mapping[ElementKey, int]    # 发射阵元(或阵元×极化) → 输入端口(0..7)
    rx_element_to_output: Mapping[ElementKey, int]    # 接收阵元(或阵元×极化) → 输出端口(0..7)
    link_mode: Literal["per_element_pair", "single_reference"]
```

**link_mode 是本模块的核心设计决策**（MPDB 的 LINK 表有两种可能形态）：

| 模式 | MPDB 形态 | 平台处理 |
| :-- | :-- | :-- |
| **per_element_pair** | LINK 集合覆盖 M_tx×M_rx **每个阵元对**（RT 已逐对算径，几何真值） | 每 LINK 按 PortMap 直接映射到 (input,output) 信道对；**不需要**导向合成 |
| **single_reference** | 单 LINK（单参考天线对）+ 用户提供阵列几何 | M5 用导向矢量**合成**各阵元对信道（《T1-06》B 方案 Phase 2），再映射 |

- 模式由导入配置显式声明 + 对 LINK 表做**分模式核验**（不猜）：
  - `per_element_pair`：校验**端点集合** `{(TX,RX)} == tx_elements × rx_elements`（笛卡尔积集合相等，**非仅数量**——数量相等仍可能重复+缺失并存，静默污染 MIMO 矩阵）；报错列出**重复项与缺失项**明细；
  - `single_reference`：`len(links)==1`；
  - 均不符→明确报错并提示两种合法形态。
- 两种模式产出同一 RT 层 schema（single_reference 时 `environment` 只有参考 LINK，合成结果由 M5 产出为 TDL 层）。

### 3.2 校验规则（构造 PortMap 时全部执行）

```
V1 端口范围: 映射值 ∈ 0..7
V2 无冲突: tx 映射单射、rx 映射单射（一个端口不承载两个阵元）
V3 拓扑相容: 使用的 (input,output) 组合 ⊆ 当前 Grid 拓扑(8×8/4×8/2×8/1×8)的有效信道对
V4 OTA 极化: test_mode=OTA 且阵元双极化(slant ±45) → 每极化占独立端口（《T1-03c》§5.3），
             PortMap 键为 (element, pol)；传导模式则单端口 + xpr_db 归约，不展开
V5 路径扩展: 若启用 ID16，被扩展消耗的配对信道不得出现在映射目标中（《T1-A1》§4 拓扑/扩展）
V6 能力上限: 端口数/信道数 ≤ backend.capabilities.grid（M2）
```
校验器为纯函数，签名显式接收全部判定上下文（PortMap 本体不承载设备态）：
`validate_portmap(portmap, ctx: ValidationContext)`，其中
`ValidationContext = {grid_topology(V3), path_expansion_enabled: bool(V5), capabilities(V6), test_mode+arrays(V4)}`
——ID16 扩展状态经 `ImportConfig.path_expansion` 传入，不隐式读取。任一违反 → `ValueError` 带具体冲突项；M2/M5/GUI(M11) 复用。

---

## 4. 导入管线（L3）

```python
def import_mpdb(source, arrays: {tx: AntennaArray, rx: AntennaArray},
                portmap: PortMap, cfg: ImportConfig) -> tuple[ChannelModel, ImportReport]:
    raw = mpdb_reader.load(source)                       # §2
    validate_portmap_against(raw.links, portmap, cfg)    # §3（含 link_mode 核验）
    rays, report = clean(raw.rays)                       # 去 NaN/Inf/零增益；report=逐类丢弃计数（★随返回值带出，不丢）
    model = ChannelModel(
        schema_version=..., id=new_id(), level="RT", realization="none",
        meta=Meta(center_frequency_hz=cfg.fc, units=CANONICAL_UNITS,
                  angle_convention=ZENITH, arrays=arrays),          # 自包含（《T1-03c》§4）
        time=TimeAxis(mode="static"),                               # 本期单快照
        grid=Grid(topology=cfg.topology, channel_pairs=portmap.used_pairs()),
        environment=Environment(links=[
            Link(link_id=l.id, tx_index=l.tx, rx_index=l.rx,
                 tx_pos_m=l.tx_pos, rx_pos_m=l.rx_pos,
                 rays=[Ray(delay_s=r.delay, gain=r.H,               # ★物理量原样；复数=单极化标量
                           aoa_az_deg=r.AOA, aoa_zen_deg=r.ZOA,     # ★天顶角保留
                           aod_az_deg=r.AOD, aod_zen_deg=r.ZOD,
                           type=r.TYPE) for r in rays_of(l)])
            for l in raw.links]),
        provenance=Provenance(source_type="MPDB", source_ref=str(source),
                              import_config={**asdict(cfg),
                                             "portmap": serialize(portmap)}),  # ★完整 PortMap 入模型——
        # link_mode 与 ElementKey(含极化)→端口 的全量映射必须随模型持久化，否则 single_reference/
        # OTA 双极化场景下 M5 无法从持久化模型恢复消费语义；Meta.arrays.port_map 仅承载
        # 单极化投影（schema 现字段为 int[]），完整映射以 provenance.import_config["portmap"] 为规范来源
    )
    if total_rays(model) == 0: raise ValueError("no valid ray after cleaning")
    return model, report                                  # level=RT；退化到 TDL 由 M5 编排；report 供 GUI/审计（§6）
```

`ImportConfig`（dataclass，沿现有风格）：`fc, topology, path_expansion: bool, link_mode, max_paths≤24, power_mode(coherent|noncoherent), default_doppler_hz, velocity?(几何多普勒,《T1-05》§5), test_mode(conducted|ota)`。

---

## 5. 量化合并纯函数集（自 tdl.py 迁移，供 M5 调用）

```python
# ★顺序纪律（《T1-06》正确性要点 1）：这些函数必须在 M5 施加导向相位【之后】调用——
#   M4 只提供机制，不自行对 RT 径调用（防"先合并后导向"破坏相关性）
def quantize_delays(delay_s: ndarray) -> tuple[ndarray, QuantReport]
    # round(delay/(1000/120ns))；超出 0..1050 的径 **丢弃**（T1-05 §6 冻结契约：丢弃并计数上报，
    # ★不得夹到端点 bin——夹取会把不存在的能量堆到 0/1050）；
    # QuantReport = {dropped_low/high 计数 + 丢弃掩码}，并入 ImportReport（§6），不静默
def merge_bins(delay_code, gain) -> list[Bin]               # 同 bin 复增益相干叠加（保相位）；noncoherent 仅用于功率统计
def select_strongest(bins, k≤24) -> list[Bin]               # 按功率截断，按时延排序
def normalize(bins, ref: float) -> list[Bin]                # ★ref 由 M5 传入（全系统共享基准，《T1-06》要点 2）——
                                                            #   本函数不得自行取局部 max（旧 tdl.py 行为，B 方案已废）
```
- 与旧 `tdl.py` 的行为差异（迁移清单）：数据源列名换 MPDB、**归一化基准外置**、角度列全程保留、输出为 schema Tap 而非 DataFrame。旧 DataFrame 接口保留一层薄适配供 CLI 过渡。

---

## 6. 错误处理

- **输入不可信**（《T1-11》§3）：schema 校验、范围裁剪计数、清洗统计全部进 `ImportReport`（随结果返回，GUI/审计可见；丢弃不静默）。
- HyperRT import 失败（SDK 缺失/版本不符）→ 明确的环境错误（区别于数据错误）。
- link_mode 与 LINK 表不符、PortMap V1–V6 违例 → `ValueError` 带冲突明细。

---

## 7. 测试设计（本模块）

| 类别 | 内容 | 判据 |
| :-- | :-- | :-- |
| **reader 契约** | 合成小 MPDB fixture（数径/数链路）→ RawRtTable | 列/型别/数值逐一断言；缺列/空表负例必 ValueError |
| **结构搬运** | 导入后 Ray 字段 vs fixture 原值 | **秒/度/天顶角原样**（零换算）；gain 复数无损 |
| **PortMap 校验** | V1–V6 各构造违例 + OTA 双极化展开例 | 全部拒绝且冲突项定位准确 |
| **link_mode** | per_element_pair(M×N 链路)与 single_reference(单链路)各一例；数量不符例 | 模式核验正确；不猜 |
| **量化合并黄金** | 与旧 tdl.py 同输入对照（含相干叠加保相位、0..1050 超范围丢弃计数） | 数值一致（归一化除外——基准外置后按传入 ref 断言） |
| **顺序纪律** | normalize 无 ref 调用 / M4 内部禁止对 RT 径调用合并 | 静态断言/接口不可达 |
| **可复现** | 同输入同配置导入两次 | model 除 id/时间戳外逐字段相等；provenance 完整 |
| **确定性依赖隔离** | 主进程未 import torch（模块级断言） | 依赖不泄漏 |

---

## 8. 开放问题
1. **MPDB 多快照/时变**：本期 static；多快照序列（A 档时间维、几何多普勒的时间差分）待 RT 侧提供形态后扩展（《T1-05》§5-3）。
2. **极化**：MPDB `H` 为单复数（单极化标量）；若 HyperRT 后续输出极化分量（VV/VH/HV/HH），Ray.gain 升级为 PolMatrix（schema 已备，《T1-03c》§5.3）。
3. **CHANNEL_TYPE 的利用**：0=LoS 可用于 K 因子/聚簇（reduce_RT_to_CDL，归 M5）；本期仅透传保留。
4. HyperRT SDK 随产品部署的打包细节（容器内 DYLD 路径等）→ 部署文档/T3。
5. **schema 对齐**：《T1-03c》AntennaArray.port_map 现为 int[]，不含极化键与 link_mode——完整 PortMap 暂以 provenance 为规范来源；建议 T1-03c 升版时将字段升级为本篇 PortMap 结构（T1 已冻结，需走 PR 升版）。

## 9. 本篇验收
- 一份真实 MPDB 可端到端读成 `level=RT` 的合法 schema 实例（零单位换算、天顶角、自包含、可复现）。
- PortMap V1–V6 与两种 link_mode 全覆盖测试通过；M5 可仅凭 §3/§5 接口完成 B 方案编排。
- 量化合并与旧 tdl.py 黄金对照一致（基准外置差异除外）。
