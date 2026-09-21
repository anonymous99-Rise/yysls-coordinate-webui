# 自动采集物品（纯 Python）

目标：把「手动抄坐标、手动按 F」变成「挂机跑」。

---

## 结论先行：用 Python，别用影刀

这个目录里两条路都有，但**推荐纯 Python**。理由不是偏好，是实测：

| 问题 | 说明 |
|---|---|
| **影刀发键游戏可能不认** | 它走 SendInput + 虚拟键码，正是 DirectX 游戏会静默忽略的组合。我们实测过：**扫描码才行** |
| **UIA 看不见游戏画面** | 影刀的 Win 自动化指令库（85 个指令）建在 pywinauto/UIA 控件树上；DirectX 表面不是控件，它只能退化成截图+图像点击 |
| **Python 片段是二等公民** | 变量靠同名绑定、不能 import 本项目模块、必须点亮那个 Python 图标、报错藏在指令日志里 |
| **不可测、不可版本化** | `.flow` 没法 diff、没法 CI。本仓库的价值恰恰在「可复现 + 可测试」 |

**你机器上就有现成的反例**：`lvjiang`（律匠）是纯 Python 写的视觉 RPA 引擎，397 个源文件、还有 Android 端，它证明这条路走得通。

---

## 四个模块，各管一件事

| 模块 | 干什么 | 依赖 |
|---|---|---|
| `injector.py` | **动手**：往游戏里发键（DirectInput 扫描码）、鼠标点击（前台/后台）、全局热键、拟人化抖动 | 纯标准库 |
| `vision.py` | **看见**：截图（GDI DIB 段）+ 模板匹配（FFT 归一化互相关）+ 帧差（判断画面动没动） | numpy |
| `mapnav.py` | **认路**：世界坐标 → 地图像素（用 117 界碑 + 23 传送点当锚点自动标定） | numpy |
| `gather.py` | **采集**：把上面几个接起来，做「到了之后按 F」 | 同上 |
| `navigate.py` | **移动**：调坐骑「识途」自动寻路（开地图 → 点目的地 → 点【识途】→ 等到达） | 同上 |

```
python rpa/gather.py --check                  # 先自检：发键通不通、屏幕能不能截
python rpa/gather.py --mode spam --key f      # 零准备：连点器
python rpa/gather.py --mode spot --key f \    # 有判断：只在看到提示时按，按完确认
    --template prompt.png --region 700,380,320,220
python rpa/gather.py --mode spot ... --hotkey # 挂机：F10 开始 / F12 暂停
```

---

## 移动这一环：坐骑「识途」

游戏机制（已核实）：**打开地图 → 选一个目的地 → 弹框里点右下角【识途】→ 自动寻路**。
两个必须尊重的约束：

1. **寻路过程中进行任何操作都会打断寻路** → 传送期间一个键都不能发，只能看着
2. **遇到水会停下来** → 不能死等固定时长，必须检测「画面不动了」

**这条路线最大的价值：不需要任何第三方工具**——不读内存、不注入游戏进程，纯 GUI 点击，
零封号风险。而且地图是 UI 界面，不像 3D 场景那样满是动态光效，**模板匹配在这里最可靠**。

### 关键问题：6071 个世界坐标，怎么变成"点地图哪里"？

需要一个二维变换：`world (x, y) → 地图屏幕像素 (px, py)`。

**我们手里有现成的锚点**：数据集里的 **117 个界碑 + 23 个传送点**都有精确世界坐标，
而它们在地图上必定有图标。用其中 2~3 个解出变换，**用剩下 130 多个反过来验证** ——
这是整个方案能被信任的原因。

```bash
# 1) 标定：给 2~3 组「世界坐标 ↔ 地图上该点的像素」（像素用画图/Snipaste 量）
python rpa/mapnav.py --fit pairs.json

# 2) 验证：把全部锚点投影画到地图截图上，红圈应该正好套在界碑图标上
python rpa/mapnav.py --overlay map.png --out check.png

# 3) 批量换算：给任务清单加上 screenX/screenY 两列
python rpa/mapnav.py --tasklist rpa/tasks/farm-龙骨.csv --out nav-龙骨.csv

# 4) 干跑一遍，确认要点哪里都合理，再真跑
python rpa/navigate.py --route nav-龙骨.csv --dry-run
python rpa/navigate.py --route nav-龙骨.csv --map-key m \
    --pathfind-template shitu.png --arrive-region 700,400,200,150 --hotkey
```

### 两个设计上值得一提的点

**① Y 翻转会被自动诊断出来。** 相似变换（4 参数）表达不了"地图 Y 轴和世界 Y 轴相反"这种情况。
如果游戏地图是 Y 向下的，相似变换的残差会飙到几百像素，而仿射变换（6 参数）能到 0。
`fit_best()` 会把两种都算一遍：**残差改善不足 1.5px 就用参数更少的相似变换，改善很大就切仿射并提醒你**。
实测：翻转场景下 similarity RMS=310px vs affine 0.0000px，判断准确。

**② 「到了没有」靠帧差，不靠死等。** `vision.MotionDetector` 连续采样同一小块画面，
算相邻帧的平均绝对差；**连续 N 次都低于阈值**才判定停下来了（单次低可能是恰好卡帧）。
因为「遇到水会停下来」，死等固定时长会让整个流程在卡住时静默跑偏。
超时会明确返回失败并写进日志，而不是假装成功继续下一个点。

---

## 完整的采集闭环

```
任务清单          ← make_tasklist.py（含 repeat 次数）
   ↓
世界坐标 → 地图像素 ← mapnav.py（117 界碑锚点标定，可画图核对）
   ↓
开地图 → 点目的地 → 点【识途】  ← navigate.py（发键/点击由 injector.py 负责）
   ↓
等到达（帧差检测画面静止）
   ↓
截图 → 检测采集提示 → 按 F → 确认提示消失  ← gather.py + vision.py
   ↓
下一个点
```

全都是纯 Python，全都不碰游戏进程。每一段的逻辑都有自动测试覆盖（见下）。

### 三种模式

| 模式 | 行为 | 需要准备 |
|---|---|---|
| `spam` | 固定/随机间隔一直按键 | 无 |
| `detect` | 只在屏幕上**看得见采集提示**时才按 | 一张提示图标模板 PNG |
| `spot` | `detect` + 按完确认提示消失（= 采集成功），失败重试后判定枯竭 | 同上 |

**为什么值得做 detect/spot 而不是只做连点器**：连点器会在没东西的地方空按几百次，你还不知道它到底采到没有。
`spot` 能回答「这个地方还有没有」—— 这正是我们那 **693 个「同坐标多名」可疑点**和 **412 个无名点**
需要的能力：让机器去判断，而不是人跑几百趟。

模板怎么弄：游戏里出现采集提示时按 `Win+Shift+S` 截图，裁下那块提示图标存成 PNG，
再用 `--region` 指定「提示会出现在屏幕哪一块」。

### 为什么发键要用扫描码

普通模拟按键（`pyautogui`、`keyboard.press()`）发的是**虚拟键码**，走 Windows 消息队列。
DirectX / DirectInput 类游戏从设备层直接读状态，**不经过消息队列**，于是按键被静默忽略 ——
脚本看着在跑，游戏毫无反应。`pydirectinput` 作者的原话（[README](https://github.com/learncodebygaming/pydirectinput)）：

> PyAutoGUI uses Virtual Key Codes (VKs) and the deprecated `mouse_event()` and `keybd_event()`
> win32 functions. You may find that PyAutoGUI does not work in some applications, particularly
> in video games and other software that rely on DirectX.

`injector.py` 做的事和 pydirectinput 一致——发 **DirectInput 扫描码（`KEYEVENTF_SCANCODE`）**——
但用 ctypes 直接调 `SendInput`，所以**不装 pydirectinput 也能用**。

> 顺带一个容易混淆的点：`keyboard` 库**适合监听热键、不适合往游戏里发键**。
> 参考项目 `yysls_auto` 正是这么分工的：`keyboard` 装钩子听 F10/F12，`pydirectinput` 负责发。这是两件事。

**UIPI 提醒**：游戏若以管理员运行，脚本也必须是管理员，否则 `SendInput` 会被静默拦掉。
`injector.py --check` 会告诉你当前权限。

### 视觉链路的关键取舍

| 决定 | 为什么 |
|---|---|
| 截图用 **GDI `CreateDIBSection`**，不用 mss/pyautogui | 直接 BitBlt 进 DIB 段内存、拿指针给 numpy 当 view，**零拷贝、不调 `GetDIBits`**。实测「CompatibleBitmap + GetDIBits」那条常见路线无论区域大小恒定 16~18 ms，DIB 段路线没有这个转换开销 |
| 匹配用 **FFT + 积分图的 NCC**，不逐像素比 | 游戏 UI 有抗锯齿、渐变、半透明，逐像素相等会全军覆没；NCC 对整体亮度变化免疫 |
| cv2 只是**可选**快路径 | 没装照样跑（自研实现），装了更快。`vision.cv2_available()` 显式判断——自检里那行假的「cv2 对照」就是没做这件事的后果 |
| **纯色模板直接报错** | 模板方差为 0 时 NCC 分母是 0（0/0）。早期版本静默返回全 0，`argmax` 落在 (0,0)，看起来像「在左上角匹配到 0 分」——排查了半天才发现是模板选在了空白处 |

---

## borrowed：从 `yysls_auto` 和 `lvjiang` 学到的东西

两个参考项目都不是照抄，而是**只取走得通的部分**：

**来自 [`nowyouseetinker/yysls_auto`](https://github.com/nowyouseetinker/yysls_auto)**
- 扫描码发键（上面已展开）—— 这是本项目最值钱的一条知识
- 全局热键 F10/F12 启停 + `threading.Event` + 可中断的等待
- UAC 提权

**来自 [`wanda1416/lvjiang`](https://github.com/wanda1416/lvjiang)**（PolyForm Noncommercial 1.0.0，**非商用**，只借鉴设计不抄代码）
- **输入后端抽象**：前台 SendInput / 后台 PostMessage / ADB 三种可换 → `injector.py` 实现了前两种
- **拟人化参数成组**：前后延迟都是**区间**、点击带随机像素偏移、区域中心抖动 → `injector.Humanize`
- **识别器三件套**：OCR / 模板匹配 / 颜色特征 → 我们目前只实现模板匹配（够用，且不引入 ONNX 那套重依赖）
- **场景 YAML + 分辨率自适应**：他们踩过 DPI 缩放的坑，`docs` 里有专门一篇

**没借的**：SQLite 存方案（我们 JSON + git 更好 diff）、`.wf` DSL（CSV + Python 够用）、PyQt6 UI（我们有 WebUI）。

---

## 剩下的老路：影刀 RPA（保留但不推荐）

下面是影刀方案，**如果你已经装了影刀并且只想用它编排窗口/日志**可以看；否则直接用上面的 Python 三件套。

本目录给这些影刀资产：

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
