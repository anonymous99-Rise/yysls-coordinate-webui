# RPA 待办清单（此目录的内容是生成的）

这里的 `.csv` 和 `index.json` 由脚本生成，**没有提交进仓库**，请按需重新生成：

```bash
# 全部（needs-name + verify-dup）
python rpa/make_tasklist.py

# 只要某几类
python rpa/make_tasklist.py --only needs-name
python rpa/make_tasklist.py --only verify-dup

# 顺带生成按材料的挂机采集路线
python rpa/make_tasklist.py --farm 龙骨 --farm 佛泪参

# 先小批量试跑
python rpa/make_tasklist.py --limit 50
```

## 三类清单的含义

| 清单 | 条数 | 用来干什么 |
|---|---|---|
| `needs-name.csv` | 412 | 原始数据没给名字的点（占位名「示例」「未备注地点」+ 1 条完全缺名称）。跑过去看一眼就知道那是什么，回填之后数据集才算完整。 |
| `verify-dup.csv` | 693 | 同一坐标挂着多个名字。原始作者写过「坐标软件没刷新会导致导出一堆一样的坐标」——哪些是真重复、哪些是同一个点上的多个东西，只能实地确认。 |
| `farm-<材料>.csv` | 按材料 | 某一材料的全部点位，按区域/坐标排序，可直接喂给坐标软件做挂机路线。 |

## 列定义

```
pointId, x, y, z, name, category, region, place, note, repeat, flags
```

- `pointId` 对应 `data/points.json` 里的 `id`，可用 `python tools/query.py` 反查
- `repeat` 是该坐标在原始文件里的重复次数；做挂机路线时，这个值就是「要循环打几次」
- `flags` 用 `|` 分隔，含义见根目录 README 的「数据标记」表

## 跑完的结果记在哪

影刀用 `rpa/scripts/03_record_task.py` 把每条的结论写进
`raw/rpa/<会话>/result.csv`（列：`pointId,status,x,y,z,name,actualX/Y/Z,note,recordedAt,clipRaw`）。

`status` 取值：`ok` 到场确认 / `gone` 已失效 / `moved` 位置偏了（把实际坐标填 `actualX/Y/Z`）/
`bad` 坐标本身有问题（穿模、落点在水里）/ `skip` 本次跳过。

收尾时 `04_run_finish.py` 会把计数合并进 `session.json`，并检查采集文件是否符合
`etl.py` 的入库格式——不合规就点名，不让脏数据混进主数据集。
