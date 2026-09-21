# raw/rpa/ —— RPA 采集落地区

`tools/00_import_raw.py --force` 重建 `raw/` 时会**特意保留**这个子目录，
因为它不属于「原始坐标库」，而是影刀 RPA / `collector.py` 的采集输出。

每一次采集 = 一个会话目录：

```
raw/rpa/20260921_174040_清河补漏/
├─ collected.ini    ← 采到的坐标（x,y,z,名称）—— 唯一会被 etl.py 读进管线的文件
├─ session.json     ← 会话元信息与统计（不会被当坐标读）
├─ result.csv       ← 方案 C 的逐点校验记录（不会被当坐标读）
└─ log.txt          ← 人类可读日志
```

采集完执行一次即可并入主数据集：

```bash
python tools/etl.py         # 自动扫 raw/rpa/**，来源会标 isRpa: true
python tools/verify.py      # 对账
```

怎么搭影刀流程见 [`../../rpa/README.md`](../../rpa/README.md)；
不想装影刀的话，`python rpa/collector.py` 这条命令本身就能采。
