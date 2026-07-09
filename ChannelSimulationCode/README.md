# 信道模拟器代码设计

当前第一版把原始单文件脚本拆成三层：

- `channel_simulator.protocol`：协议 V3.0 的帧头、帧尾、参数 ID、子帧编码、端口编码和单位换算。
- `channel_simulator.tdl`：读取 RT/MAT 数据，把射线按硬件时延分辨率合并成 TDL 表。
- `channel_simulator.commands`：把 TDL 表组装成可下发到设备的控制帧。
- `channel_simulator.cli`：命令行入口，生成 `csv/bin/hex` 三类输出。

示例：

```powershell
python -m channel_simulator.cli frame_7.mat --input-id 0 --output-id 0 --max-paths 24
```

设计注意点：

- 当前设备已明确单信道支持 24 条多径；代码默认选择并配置 24 条，可通过 `--max-paths` 和 `--hardware-paths` 下调。
- 相偏参数表属于简写，当前按设备确认的 24 条多径全部支持相偏；代码默认写 24 条。
- 多径主时延单位为 `1000/120 ns`，范围 `0..1050`。
- 多径幅值衰减是线性幅度，单位 `1/32768`；输出端幅值衰减单位 `1/16384`。
- 多普勒频移码值满足 `Hz = code / 35.791394133`。
