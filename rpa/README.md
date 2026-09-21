# 用影刀 RPA 自动采集燕云十六声坐标

目标：把「手动抄坐标」变成「按个键 / 挂着跑」，采到的数据自动并回主数据集。

本目录给三样东西：

| 文件 | 作用 |
|---|---|
| `collector.py` | **不依赖影刀**也能跑的剪贴板采集器（实测通过） |
| `scripts/01~04_*.py` | 影刀「执行Python代码」指令里直接粘贴的片段 |
| `make_tasklist.py` | 从规范化数据生成待办清单，喂给 RPA 去跑 |
| `tasks/*.csv` | 生成出来的清单 |

---

## 三种采集方案，先选一个

| | 方案 A：剪贴板桥接 ⭐推荐 | 方案 B：屏幕 OCR | 方案 C：清单跑图 |
|---|---|---|---|
| **原理** | 按坐标记录热键 → 坐标进剪贴板 → 程序落盘 | 定时截屏 → OCR 识别 HUD 上的坐标 → 落盘 | 读清单 → 逐个传送 → 记录这个点还在不在 |
| **前提** | 你的坐标插件有「记录当前坐标」热键 | 游戏界面/插件上**显示**实时坐标 | 坐标软件支持输入坐标传送 |
| **人力** | 人要在游戏里走，不用碰键盘 | 无人值守，挂着跑图 | 半自动，人得处理卡点 |
| **稳定性** | 高（剪贴板是最可靠的进程间通道） | 中（OCR 受分辨率/UI 缩放影响） | 中（传送落点可能偏） |
| **适合** | 补漏、随手记 | 大规模扫图 | 校验存量 693 个可疑点 |

下面主要写方案 A 和 C，方案 B 附在最后。

---

## 方案 A：剪贴板桥接

### A-0 先确认一件事：你的坐标插件有没有「记录坐标」热键

这类插件基本都有，通常在设置里叫「记录当前坐标」「复制坐标」之类，按下后会把
`x,y,z` 或 `x,y,z,名称` 写进 Windows 剪贴板。

**验证方法**：在游戏里按一下那个键，然后开记事本 Ctrl+V。粘贴出来像
`-2400.5,700,-60` 或 `-2400.5 ,700 ,-60 ,名字` 就对了。

### A-1 最快的用法：不用影刀，直接跑采集器

```bash
python rpa/collector.py --out raw/rpa/我的手采/collected.ini --interval 250
```

它会一直盯着剪贴板。你在游戏里按记录键，它就记一条；Ctrl+C 收工。
按重复内容自动去重，输出 `x,y,z,名称` 格式，附一份 `session.json` 元信息。

```bash
# 只看一次剪贴板（排查用）
python rpa/collector.py --once

# 直接解析一段文本
python rpa/collector.py --text "-2400.5,700,-60,我家门口"
```

### A-2 用影刀编排（想要窗口管理、日志、定时那套）

影刀流程按下面的顺序搭。指令名以影刀「Win窗口自动化」指令库为准（指令面板直接搜名字）。

```
[1] 按进程名获取窗口对象
      进程名 = yysls
      窗口标题 = 燕云十六声
      等待时间 = 10
      → 输出「窗口对象」

      ▸ 燕云十六声的客户端进程是 yysls.exe（实测路径
        E:\Game\yysls\yysls_fast\Engine\Binaries\Win64r\yysls.exe），
        启动器是 launcher.exe，别抓错。

[2] 激活窗口（窗口对象）

[3] 执行Python代码  ← 粘贴 scripts/01_run_init.py
      输入变量：{"base_dir": "D:\\blog\\yysls-coordinate-webui\\raw\\rpa",
                "tag": "清河补漏", "mode": "clipboard"}
      输出变量：${会话目录}  ${输出文件}  ${启动信息}
      ▸ ⚠️ 必须点亮指令右上角的 Python 图标，否则代码根本不执行

[4] 循环（次数设大一点，比如 5000 次）
    ├─ 设置剪贴板文本  文本 = "____WAITING____"     ← 先塞哨兵值
    ├─ 发送快捷键（窗口对象 = 窗口对象，快捷键 = 你的记录坐标热键）
    ├─ 等待（1 秒）
    ├─ 获取剪贴板文本 → ${剪贴板文本}
    ├─ 如果 ${剪贴板文本} 不等于 "____WAITING____" 且 不为空：
    │   └─ 执行Python代码  ← 粘贴 scripts/02_parse_clipboard.py
    │         输入变量：{"clip_text": ${剪贴板文本}, "out_file": ${输出文件}}
    │         输出变量：${结果}  ${是否新增}
    │         └─ 如果 ${是否新增} 为真 → 写日志（${结果}）
    └─ 等待（你在游戏里挪到下一个点的时间，比如 3 秒）

[5] 执行Python代码  ← 粘贴 scripts/04_run_finish.py
      输入变量：{"run_dir": ${会话目录}, "out_file": ${输出文件}}
      输出变量：${汇总}  ${汇总文本}  ${是否可入库}
      → 弹窗/写日志显示 ${汇总文本}
```

**为什么第 4 步要「先塞哨兵值」**：影刀的「获取剪贴板文本」在运行时偶尔拿不到内容
（社区有反馈），如果直接读，分不清是「游戏还没写进去」还是「读失败了」。
先写入 `____WAITING____`，只要读到的不是它，就说明游戏确实写了新内容。

**想解放双手**的话，把「等待 3 秒」换成「判断剪贴板是否变化」的死循环，
也就是把整个 `[4]` 换成直接跑 `collector.py`：

```
[4'] 执行命令行：python rpa/collector.py --out ${输出文件} --interval 200
     ▸ 影刀里用「运行程序」或「执行CMD命令」指令；
       注意它是阻塞的，要配合「并发调用流程」或后台运行
```

### A-3 采集完并入主数据

```bash
python tools/etl.py      # 会自动扫 raw/rpa/ 下的 .ini/.txt/.cfg
python tools/verify.py   # 对账
```

采集产物落在 `raw/rpa/**`，`etl.py` 的 `iter_raw_files()` 会把它算进管线，
并在 `facets.json` 的 `sourceFiles` 里标 `isRpa: true`。

---

## 方案 C：按清单跑图，校验存量数据

存量数据里有 **693 个「同坐标挂多个名字」** 的点——原始作者自己说过
「坐标软件没刷新会导致导出一堆一样的坐标」，到底是真重复还是同一个点上的多个东西，
只能实地看一眼。这正是 RPA 的活。

```bash
# 生成待办清单
python rpa/make_tasklist.py
#   needs-name   412 条  原始数据没给名字的点 → 走过去看一眼就知道是什么
#   verify-dup   693 条  同坐标多名 → 确认是不是真重复

python rpa/make_tasklist.py --farm 龙骨 --farm 佛泪参    # 顺带生成挂机路线
```

清单是 CSV，列固定：`pointId,x,y,z,name,category,region,place,note,repeat,flags`

影刀流程：

```
[1] 按进程名获取窗口对象（yysls）

[2] 执行Python代码  ← scripts/01_run_init.py
      输入变量：{"base_dir": "…\\raw\\rpa", "tag": "verify-dup", "mode": "tasklist"}

[3] 读取表格（文件 = rpa/tasks/verify-dup.csv）→ 得到表格数据

[4] 循环（表格数据 每一行）
    ├─ 把坐标写进坐标软件的传送输入框（输入文本 或 设置剪贴板文本 + 发送快捷键）
    ├─ 发送快捷键（传送键）
    ├─ 等待（3 秒，等加载）
    ├─ 窗口截图到文件（存证，路径用 pointId 命名，便于事后核对）
    ├─ 判断：目标还在不在（人工看一眼 → 用一个影刀「输入对话框」收集结论）
    │      结论写进变量 ${status}，取值 ok / gone / moved / bad / skip
    └─ 执行Python代码  ← scripts/03_record_task.py
          输入变量：{"out_file": "…\\raw\\rpa\\本次会话\\result.csv",
                    "point_id": ${当前行.pointId}, "coord": ${当前行.x}+","+${当前行.y}+","+${当前行.z},
                    "name": ${当前行.name}, "status": ${status}, "note": ${备注}}
          输出变量：${结果}  ${是否有效}

[5] 执行Python代码  ← scripts/04_run_finish.py
      → ${汇总文本} 会告诉你：采到多少条、格式检查过没过、能不能入库
```

半自动版（推荐先这么用）：第 4 步的「人工判断」换成
**你人在游戏里逐条跑，发现不对就按一个快捷键**，影刀听快捷键往 `result.csv` 里记一行。

---

## 方案 B：屏幕 OCR（无人值守）

前提：游戏界面或插件 HUD 上**显示**着实时坐标。

```
[1] 按进程名获取窗口对象（yysls）
[2] 执行Python代码 ← 01_run_init.py
[3] 循环
    ├─ 窗口截图到文件（区域裁到坐标文字那一小块，能大幅提高识别率）
    ├─ OCR 识别（影刀自带的通用文字识别指令）→ ${ocr文本}
    ├─ 执行Python代码 ← 02_parse_clipboard.py
    │     输入变量：{"clip_text": ${ocr文本}, "out_file": ${输出文件}}
    │     ▸ 解析器自带兜底：能从「坐标：X=-2400.5 Y=700 Z=-60」
    │       甚至「(-2400.5, 700, -60)」这种里把三个数字捞出来
    ├─ 停止条件：按了某个键 / 循环次数到了
    └─ 等待（0.5 秒）
[4] 04_run_finish.py 收尾
```

提高识别率的三个实操点：
1. **裁剪截图区域**到只包含坐标文字，别整屏 OCR
2. 游戏用**窗口化/无边框**，避免全屏独占导致截图抓到黑屏
3. UI 缩放设 100%，HUD 字体别用太细的

---

## 影刀变量绑定规则（这段是踩过坑总结的）

影刀的「执行Python代码」指令（官方文档也叫「运行代码」组件）：

| 项 | 怎么填 | 在 Python 里 |
|---|---|---|
| **输入变量** | `{"clip_text": ${剪贴板文本}, "out_file": ${输出文件}}` | 直接就是名为 `clip_text`、`out_file` 的变量 |
| **输出变量** | `${结果}` `${是否新增}` | Python 里的同名变量会回传，支持 字符串/数字/列表/字典 |

### 四个必须知道的坑

1. **⚠️ 必须点亮指令右上角的 Python 图标。** 官方教程明确点过这个坑：不点亮，指令是灰的，
   代码写了也不执行，很多新手卡在这儿。
2. **影刀的 Python 环境不保证有第三方库。** 本目录所有脚本**只用标准库**（`json`/`re`/`csv`/`os`/`datetime`），
   不会有 `ModuleNotFoundError`。
3. **报错信息藏在指令的「日志」里。** 流程跑不对先看那一步的日志，别猜。
4. **不行就降级成命令行。** 影刀的「运行程序 / 执行CMD命令」指令直接调
   `python rpa/scripts/02_parse_clipboard.py --text "…" --out "…"`，
   这条路径不依赖影刀的 Python 环境，最稳。

> **坐标以 `-` 开头会让命令行参数解析炸掉**（`--coord -2400,700,-60` 里的值被当成新选项）。
> 本目录的脚本都做了自动改写，`--coord -2400,700,-60` 和 `--coord=-2400,700,-60` 两种写法都能用。
> 你自己写新脚本时记得这个坑。

---

## 自己测一遍（不需要影刀、不需要游戏）

```bash
python tools/test_rpa.py
```

34 项断言，覆盖：
- `collector.py` 的解析器与 `tools/lib_parse.py` **对同一批样本给出一致判定**
  （两处正则是有意重复的——`rpa/` 要能单独分发出去，不能 import 项目代码；
  既然重复了就得有测试盯着它们别跑偏。测试里也显式声明了两处**有意的差异**：
  `collector` 多认括号三元组，`lib_parse` 多做「3-」前缀修复）
- 四个影刀片段都能当命令行程序独立跑通
- 采集产物真的能被 `etl.py` 吃进去（端到端）

单跑某个片段：

```bash
python rpa/scripts/01_run_init.py --base-dir raw/rpa --tag 冒烟测试
python rpa/scripts/02_parse_clipboard.py --text "-2400.5,700,-60,测试点" --out /tmp/x.ini
python rpa/scripts/03_record_task.py --out raw/rpa/x/result.csv --point-id p1 --coord -2400,700,-60 --status ok
python rpa/scripts/04_run_finish.py --run-dir raw/rpa/x --out-file raw/rpa/x/collected.ini
```

---

## 目录约定

```
raw/rpa/
└─ 20260921_174040_清河补漏/       ← 一次采集 = 一个目录
   ├─ collected.ini                ← 采到的坐标（x,y,z,名称，etl.py 直接吃）
   ├─ session.json                 ← 本次会话元信息与统计
   ├─ result.csv                   ← 方案 C 的逐点校验记录
   └─ log.txt                      ← 人类可读日志
```

- `raw/rpa/` 是**影刀的落地区**：`tools/00_import_raw.py --force` 重建 `raw/` 时会**特意保留**它。
- 只有 `collected.ini` 会进 ETL 管线；`session.json` / `result.csv` / `log.txt` 是旁证，不会被误当坐标读进去。
- `04_run_finish.py` 会先做一次格式体检：`collected.ini` 里只要有一行不是 `x,y,z,名称`，
  就会被点名记进 `session.json` 的 `reportedProblems`，并且 `readyForEtl` 置 false —— 脏数据进不了主数据集。
