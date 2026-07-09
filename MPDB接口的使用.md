# MPDB (Multipath Database) 技术与使用手册

**版本**: 1.0  
**适用环境**: RT-Release-MacOS / HyperRT SDK

---

## 1. 简介

**MPDB** 是 HyperRT 引擎专用的高性能数据库格式，用于存储无线电光线追踪（Ray Tracing）产生的海量多径数据。它基于 PyTorch 张量构建，支持高效的压缩存储和 GPU 加速查询。

**核心应用场景**：
*   **信道分析**：提取功率时延谱 (PDP)、角度谱 (PAS)。
*   **数字孪生**：将仿真产生的确定性信道数据导入硬件仿真器（如 Keysight PropSim）。
*   **AI 训练**：直接作为 PyTorch Dataset 供神经网络使用。

---

## 2. 数据库结构 (Schema)

MPDB 包含两张核心表：**`LINK`** (拓扑表) 和 **`CHANNEL`** (物理表)。

### 2.1 LINK 表 (链路信息)
存储发射机 (TX) 与接收机 (RX) 的拓扑关系及位置信息。

| 列名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `TX` | Int | 发射机索引 |
| `RX` | Int | 接收机索引 |
| `TX_ANT_POSITION` | Tensor (x,y,z) | 发射天线在世界坐标系中的位置 (米) |
| `RX_ANT_POSITION` | Tensor (x,y,z) | 接收天线在世界坐标系中的位置 (米) |

### 2.2 CHANNEL 表 (多径信息)
这是最核心的数据表，每一行代表一条传播路径（Ray/Path）。

| 列名 | 单位/类型 | 说明 | 物理含义 |
| :--- | :--- | :--- | :--- |
| **`LINK_ID`** | Int | 外键 | 对应 LINK 表的行索引 |
| **`DELAY`** | 秒 (s) | 传播时延 | 路径长度 / 光速 |
| **`H`** | Complex | 信道系数 | 复数增益 (包含幅度与相位)，对应极化分量 |
| **`AOA`** | 度 (deg) | 到达方位角 | 接收端水平方向角度 (Azimuth of Arrival) |
| **`ZOA`** | 度 (deg) | 到达天顶角 | 接收端垂直方向角度 (Zenith of Arrival, 0°为正头顶) |
| **`AOD`** | 度 (deg) | 离去方位角 | 发射端水平方向角度 (Azimuth of Departure) |
| **`ZOD`** | 度 (deg) | 离去天顶角 | 发射端垂直方向角度 (Zenith of Departure) |
| `CHANNEL_TYPE` | Int | 传播类型 | 0=直射, >0=反射/绕射等 |

> **注意**：HyperRT 使用**天顶角 (Zenith)** 坐标系（0° 指向 Z 轴正方向/天空，90° 指向水平面）。这与通信设备常用的**仰角 (Elevation)** 坐标系（0° 指向水平面，90° 指向天空）互余。

---

## 3. MPQL 查询指令指南

MPQL (Multipath Query Language) 是一种专为 MPDB 设计的轻量级 SQL 风格查询语言。它支持通过命令行快速筛选、聚合和导出数据，语法与标准 SQL 高度相似，但在物理层进行了针对 PyTorch Tensor 的优化。

**基本调用方式** (在终端中)：
```bash
./HyperRT -m HyperRT.MiRT.MPDB.MPQL --db <文件名>.mpdb -e "<SQL语句>"
```

### 3.1 常用指令速查

| 功能 | 语法示例 | 说明 |
| :--- | :--- | :--- |
| **查看表** | `SHOW TABLES;` | 显示库中所有表名 |
| **查看列** | `SHOW COLUMNS FROM CHANNEL;` | 显示表结构 |
| **简单查询** | `SELECT DELAY, H FROM CHANNEL;` | 选取特定列 |
| **条件过滤** | `SELECT * FROM CHANNEL WHERE LINK_ID==0;` | 筛选特定链路的数据 |
| **限制行数** | `SELECT HEAD(5, DELAY, AOA) FROM CHANNEL;` | 查看前 5 行 (类似 LIMIT) |
| **排序** | `SELECT * FROM CHANNEL ORDER BY DELAY ASC;` | 按时延升序排列 |
| **导出 CSV** | `SELECT WRITE_CSV("out.csv", DELAY, H) FROM ...` | 将查询结果导出为 CSV |

### 3.2 语法详解 (Syntax Reference)

MPQL 支持标准 SQL 的核心子集，适用于单表查询与聚合分析。

#### 核心结构
```sql
SELECT [HEAD(n, ...)|AGG(...)|COL1, COL2]
FROM table_name
[WHERE condition]
[GROUP BY col_name]
[ORDER BY col_name [ASC|DESC]];
```

*   **SELECT**: 支持列名、数学运算表达式及 UDF (User Defined Functions)。
*   **FROM**: 指定数据表 (`CHANNEL` 或 `LINK`)。
*   **WHERE**: 支持布尔逻辑过滤。注意相等判断推荐使用 `==` (Python 风格) 或 `=` (SQL 风格)。
*   **GROUP BY**: 用于聚合操作 (如 `COUNT`, `SUM`, `AVG`)。
*   **ORDER BY**: 对结果进行排序，支持 `ASC` (升序) 和 `DESC` (降序)。

#### 运算符 (Operators)
*   **比较**: `==`, `!=`, `>`, `<`, `>=`, `<=`
*   **逻辑**: `AND`, `OR`, `NOT`
*   **算术**: `+`, `-`, `*`, `/`

### 3.3 内置函数 (Built-in UDFs)

MPDB 提供了丰富的内置函数来处理物理量和复数运算。

| 函数名 | 类型 | 说明 | 示例 |
| :--- | :--- | :--- | :--- |
| **`H2PL(H)`** | Map | 将复数 H 转换为路径损耗 (Path Loss, dB) | `SELECT H2PL(H) FROM CHANNEL` |
| **`H2ANG(H)`** | Map | 获取 H 的相位角 (弧度) | `SELECT H2ANG(H) FROM CHANNEL` |
| **`HEAD(N, ...)`** | Map | 获取前 N 行 (替代 LIMIT) | `SELECT HEAD(10, DELAY, H) ...` |
| **`COUNT(col)`** | Reduce | 统计行数 (需配合 GROUP BY 使用) | `SELECT COUNT(DELAY) FROM CHANNEL` |
| **`SUM(col)`** | Reduce | 求和 | `SELECT SUM(H) FROM CHANNEL` |
| **`AVG(col)`** | Reduce | 求平均值 | `SELECT AVG(DELAY) FROM CHANNEL` |
| **`WRITE_CSV(...)`** | Action | 将选中列写入 CSV 文件 | `SELECT WRITE_CSV("data.csv", DELAY, H) ...` |

### 3.4 详细参考文档

MPQL 的完整语法定义、所有支持的 UDF 列表及更复杂的查询示例（如 Common Table Expressions CTE），请参考 SDK 内置的详细文档：

**文档路径**: `PyHyperRT/DocumentServer/tutorials/MPDB.md`

您可以直接阅读该 Markdown 文件获取最权威的语法说明。

---

## 4. 实战案例：适配 Keysight PropSim GCM

本节演示如何编写 Python 脚本，将 MPDB 数据转换为 **Keysight PropSim GCM (Geometric Channel Modeling)** 能够识别的 CSV 格式。

### 4.1 需求分析 (Gap Analysis)

HyperRT 的原生数据与 PropSim 的导入要求存在以下差异，必须在导出时进行转换：

1.  **时间单位**：HyperRT 为 **秒 (s)** $\rightarrow$ PropSim 要求 **纳秒 (ns)**。
2.  **角度坐标**：HyperRT 为 **天顶角 (Zenith)** $\rightarrow$ PropSim 要求 **仰角 (Elevation)**。
    *   转换公式：$Elevation = 90^° - Zenith$
3.  **复数格式**：HyperRT 为 Python Complex 对象 $\rightarrow$ PropSim 要求字符串格式 `Re+Imi` (例如 `1.2e-4-3.5e-4i`)，且不带括号。
4.  **数据结构**：PropSim 要求将同一时刻、同一链路的**所有多径**平铺在**同一行**中。

### 4.2 转换脚本实例

请在项目根目录创建并运行 `export_mpdb_to_propsim.py`：

```python
import os
import torch
from HyperRT.MiRT.MPDB import MPDB

def format_complex_propsim(c):
    """将复数格式化为 PropSim 要求的 'a+bi' 字符串格式"""
    real = c.real
    imag = c.imag
    sign = "+" if imag >= 0 else "-"
    return f"{real:.4e}{sign}{abs(imag):.4e}i"

def main():
    input_db = "etoile_radio.mpdb"
    output_csv = "propsim_import.csv"
    sim_frequency_mhz = 2500  # 仿真频率
    
    db = MPDB.load(input_db)
    link_tbl = db.link
    chan_tbl = db.channel
    
    def get_col(tbl, name):
        val = tbl.get(name) if hasattr(tbl, "get") else tbl[name]
        return val.data if hasattr(val, "data") else val

    tx_pos = get_col(link_tbl, "TX_ANT_POSITION")
    rx_pos = get_col(link_tbl, "RX_ANT_POSITION")
    
    link_ids = get_col(chan_tbl, "LINK_ID")
    delays   = get_col(chan_tbl, "DELAY")
    aoas     = get_col(chan_tbl, "AOA")
    aods     = get_col(chan_tbl, "AOD")
    zoas     = get_col(chan_tbl, "ZOA")
    zods     = get_col(chan_tbl, "ZOD")
    hs       = get_col(chan_tbl, "H")
    
    _, counts = torch.unique(link_ids, return_counts=True)
    max_paths = counts.max().item()

    header_base = "CMIMPORTMODE,UEID,UEXCOORD,UEYCOORD,UEZCOORD,BSID,SID,BSXCOORD,BSYCOORD,BSZCOORD,DLFREQUENCY,ULFREQUENCY"
    path_template = "PATHID,DELAY,AOA,AOD,EOA,EOD,VV,VH,HV,HH"
    full_header = header_base + ("," + path_template) * max_paths

    with open(output_csv, "w") as f:
        f.write(full_header + "\n")
        num_links = tx_pos.shape[0]
        for link_id in range(num_links):
            tx = tx_pos[link_id].tolist()
            rx = rx_pos[link_id].tolist()
            row = ["PATH", str(link_id + 1), f"{rx[0]:.4f}", f"{rx[1]:.4f}", f"{rx[2]:.4f}", "1", "1", f"{tx[0]:.4f}", f"{tx[1]:.4f}", f"{tx[2]:.4f}", str(sim_frequency_mhz), str(sim_frequency_mhz)]
            
            mask = (link_ids == link_id)
            path_idxs = torch.nonzero(mask).flatten()
            if len(path_idxs) > 0:
                # 排序并填充
                sorted_indices = torch.argsort(delays[path_idxs])
                path_idxs = path_idxs[sorted_indices]
                for i in range(len(path_idxs)):
                    idx = path_idxs[i]
                    row.extend([str(i + 1), f"{delays[idx].item()*1e9:.6f}", f"{aoas[idx].item():.4f}", f"{aods[idx].item():.4f}", f"{90-zoas[idx].item():.4f}", f"{90-zods[idx].item():.4f}", format_complex_propsim(hs[idx].item()), format_complex_propsim(0j), format_complex_propsim(0j), format_complex_propsim(0j)])
                row.extend(["" * (10 * (max_paths - len(path_idxs)))])
            f.write(",".join(row) + "\n")

if __name__ == "__main__":
    main()
```

---

## 5. 运行与验证

在终端中执行以下命令（必须使用 SDK 自带的 `HyperRT` 运行时）：

```bash
export DYLD_LIBRARY_PATH=$(pwd)
./HyperRT export_mpdb_to_propsim.py
```

执行后，生成的 `propsim_import.csv` 即可在 Keysight PropSim 的 **Channel Studio** 中通过 "Import GCM Path Data" 功能进行导入。

