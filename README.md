# MCP_CATIA_MechDog 0.3.152

![version](https://img.shields.io/badge/version-0.3.152-blue) ![license](https://img.shields.io/badge/license-MIT-green) ![platform](https://img.shields.io/badge/platform-Windows%20x64-lightgrey) ![python](https://img.shields.io/badge/python-3.10%2B-blue) ![CATIA](https://img.shields.io/badge/CATIA-V5%20R30%20(B30)-orange) ![MCP](https://img.shields.io/badge/MCP-stdio%20%7C%20http-purple)

当前正式版 **0.3.152**——按 0.3.151 的验收报告处理，P0 是「装配约束全路径不可用」，这一版把它从「试了不行」变成「量出来是**哪一层**不行」。`Application.GetWorkbenchId()` 在装配文档激活时返回 **'Assembly'**——工作台一直是开的，`StartWorkbench` 在这版上是**空操作**（连不存在的名字都返回 None，要 Part Design 也不换 id），所以前两轮把「工作台没激活」当嫌疑是错的；工作台开着，`AddMonoEltCst` 仍然对**全部 14 个约束类型码**报 E_FAIL——根集合与子件集合、就地新建的零件与从磁盘装的零件、元素给组件/`ReferenceProduct`/根产品/以及**前几轮没试过的**`Selection.Item(1).Reference` 都一样，`Product.CreateReferenceFromObject` 不存在。让这些失败有意义的是一格对照：**同一会话、同一 CATIA**，同一个 `AddMonoEltCst(0, Reference)` 在**草图**上建出 `固定的.1`；而 UI 命令 `'Fix'`/`'CreateFix'` 都答「未知命令」、`'Update'` 正常——即约束命令根本没注册进来。于是四个 mate 工具的描述直接写 **UNAVAILABLE IN THIS BUILD**，`catia_diagnose` 发一条 `capability_ceilings`（含同样证据），**没有**加「假装是约束的变换层」。P1：`transform_13` + `translation_unit` 直接吃列主序 13 元组（CATIA 原生九个数**就是**列，所以只换单位、校验 scale=1，**不做转置**——第一版转了，被验收第 A 格抓住：90° 转向装成 180°），读回还报 `if_read_transposed.differs_by_deg`。P2/P3：截断的顶点扫描**不是下界而是样本的盒子**——582 顶点只读 200，max y 报 3.1941 而零件到 46.1033（短 42.9 mm），还花了 41 054 ms；同一零件 `mesh` 用 181 ms 给到46.1033。P4：`save_as_copy(part_number=...)` 让副本自带新编号（实例名从 `P05_legrod.6` 变成 `P06_mir_legrod.1`）。P5：`strict_arc` 拒绝那次 0.0028 mm 的圆心移动。 · [变更日志](CHANGELOG.md) · [Releases](https://github.com/wudahua/MCP_CATIA_MechDog/releases)

> 让 AI 客户端通过 MCP，在 CATIA V5 里建**原生可编辑的实体特征**——并且每一步都能量回来。

MCP_CATIA_MechDog 是运行在你自己 Windows 电脑上的本地 MCP 服务（stdio，可选 HTTP）。它把 CATIA V5 的 **COM Automation** 封装成 **154 个工具**（14 组），供 WorkBuddy、Claude Desktop、Cursor 等支持 MCP 的客户端调用：从一句需求出发，生成带草图、拉伸、切除、圆角、曲面与装配关系的 `.CATPart` / `.CATProduct`，保存到磁盘，再把体积、面积、面/边/顶点数读回来核对。

本仓库是 [daiemon12/catia-v5-mcp-server](https://github.com/daiemon12/catia-v5-mcp-server)（MIT，Adam M'HAMDI）的 **lite fork**：只保留「按书面规格把实体建出来、并且自己验证」这一条主线，只做加法与修补，不重写 COM 层。上游注释里那些实机验证过的坑，是本项目最大的资产。

**适合**：重复性参数化建模、按图纸/规格批量出零件、AI 驱动的草图→实体→装配流程、接管已有模型继续改。
**不适合**：任意复杂自由曲面的造型、需要 CATIA 专业模块完整封装的工作、没有真实 CATIA 与许可的「云端建模」。

## 特性

- **建模即校验**：`pad` / `pocket` / `hole` / `shaft` / `groove` / `circ_pattern` 每次都在回执里报移动前后的体积，静默失效的特征当场可见；装配的 `catia_get_inertia` 递归聚合子件体积、面积与世界重心（9 件装配实测 0.009 mm），并展开子装配、给出 `complete` / `incomplete_because`。
- **边有指纹，不是索引**：`catia_list_edges` 返回 `L17.6112|Pad.6;2|Sketch.7;2`，`fillet` / `chamfer` 在调用时解析它，所以多圆角脚本扛得住自己后续的编辑。
- **草图可回改**：`catia_sketch_edit` 重开已关闭的草图，`catia_sketch_delete_element` 删掉画错的一条，零件随之重建——错一条线不必整件重做。
- **量不到就说量不到**：超范围、超能力或读数失败时，回执以 `UNVERIFIED:` 开头并说明哪一个测量失败，不会把失败写成成功。
- **能力可发现**：诊断文字旁边给一行能直接粘贴的调用；「有工具但没人知道怎么调」和「没有这个工具」一样是缺陷。
- **离线可测**：**2448** 个测试不需要 CATIA，`pytest` 在本机就能跑全绿；真机验收脚本在 `scripts/b30_*`。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows x64（COM Automation，`pywin32` 是硬依赖） |
| Python | 3.10+ x64 |
| CAD | CATIA V5，已安装并可用许可；实测基线 **V5-6R2020（B30）简体中文版** |
| 传输 | stdio（默认）；`catia-mcp --http` 提供 Streamable HTTP |

CATIA 软件与许需自行取得；MIT 只覆盖本项目原创代码与文档。

**两个已知的版本差异**（都在 B32 上复现，B30 正常）：`catia_gsd_multi_section_surface` 在 **V5-6R2022（B32）** 上任何配置都失败，而同一组曲线上的 `catia_gsd_blend` 正常；`catia_hole(threaded=true)` 在 B32 上无法切出螺纹孔（B30 正常），B32 返回 `FEATURE_INCOMPLETE` 并回滚。

## 安装

```bash
git clone https://github.com/wudahua/MCP_CATIA_MechDog.git
cd MCP_CATIA_MechDog
python -m venv .venv
.venv/Scripts/pip install -e .

# 离线测试：不需要 CATIA
.venv/Scripts/python -m pytest tests -q
```

> **不要和上游装进同一个虚拟环境。** 两边都提供 `catia_mcp` 导入包，会互相遮蔽。

升级已有工作副本用 `git pull`。若改用下载的压缩包更新，**不要** `cp -r src/* dst/`——shell 通配符和资源管理器都会跳过点开头的条目（`.gitattributes`、`.github/workflows/ci.yml`、`.claude/skills/**`、`.gitignore`），丢掉它们不会有任何提示。用 `cp -r src/. dst/`（那个 `/.` 才是保留点文件的关键），或直接用发布包（含点文件与 `.sha256`）。

## 连接 MCP 客户端

推荐入口是 `python -m catia_mcp`，它不经过启动器 `.exe`：

```json
{
  "mcpServers": {
    "MCP_CATIA_MechDog": {
      "command": "C:\\path\\to\\MCP_CATIA_MechDog\\.venv\\Scripts\\python.exe",
      "args": ["-m", "catia_mcp"]
    }
  }
}
```

已安装的 `catia-mcp.exe` 等价可用，但 Windows 会锁住运行中的可执行文件：**客户端开着服务时升级包会失败**（`pip` 先删旧 `.exe`，删除被拒绝，整个事务中止）。要么让客户端走上面的 `python -m` 形式（`.py` 在导入时读进内存，替换掉没问题），要么升级前先关客户端：

```json
{
  "mcpServers": {
    "MCP_CATIA_MechDog": {
      "command": "C:\\path\\to\\MCP_CATIA_MechDog\\.venv\\Scripts\\catia-mcp.exe"
    }
  }
}
```

远端 Agent 用 `catia-mcp --http`，在 `/mcp` 提供 Streamable HTTP，见 [docs/remote-access.md](docs/remote-access.md)。

## 快速开始

计划是数据，不是对话。下面这份计划建一个 40×20×10 的方块，并把它量回来：

```jsonc
{
  "steps": [
    {"tool": "catia_new_part",         "args": {}},
    {"tool": "catia_create_sketch",    "args": {"plane": "xy"}},
    {"tool": "catia_sketch_rectangle", "args": {"x1": -20, "y1": -10, "x2": 20, "y2": 10}},
    {"tool": "catia_close_sketch",     "args": {}},
    {"tool": "catia_pad",              "args": {"height": 10, "direction": "normal"}}
  ],
  "expect": {
    "volume_mm3": 8000, "area_mm2": 2800,
    "faces": 6, "edges": 12, "vertices": 8,
    "cog": [0, 0, 5], "cog_tolerance_mm": 0.01
  }
}
```

交给 `catia_run_plan` 执行：预检 → 逐步执行 → 一次终态判定。断言不过时 `summary=true` 仍保留每一步的记录，不必为了定位偏差把计划跑两遍；`rollback: true` 会按逆序删掉本次建的特征，并把树读回来告诉你是谁没了。

长度单位毫米、角度单位度，`plane` 的 H/V/法向不是直觉那一组（`xy` = H:X / V:Y / 法向:Z），回执每次都写明。坐标与引用格式见[英文完整文档](README.en.md)。

## 功能一览

| Group | Tools | 能力 |
| --- | ---: | --- |
| Document | 12 | 新建 / 打开 / 保存 / 关闭 / 列举，`catia_save_as_copy`；保存遇模态框会重试 |
| Sketcher | 19 | 线、矩形、圆、椭圆、圆弧、轮廓、样条（含控制点 B 样条）、约束与尺寸；重开已关闭的草图、删单个元素；`catia_sketch_copy` 把轮廓复述到另一个平面；`catia_sketch_strokes` 一次画完整个字形轮廓 |
| Part Design | 26 | 凸台、凹槽、旋转槽、旋转体、孔、圆角、倒角、阵列、镜像、抽壳、多截面实体、多体；圆角/倒角漏掉具名边时返回 `PARTIAL_EDGES_FAILED` 并列出成功与拒绝两组 |
| Generative Shape Design | 41 | 线框与曲面、提取、边界、缝合、曲面圆角、脊线、旋转/平移/环形阵列、法向平面、角度平面、旋转曲面与封闭、`catia_surface_engrave`、`catia_solid_tube`、`catia_gsd_splines`、`catia_solid_sweep_variable`（半径表扫掠，站点可为椭圆）、`catia_measure_deviation`、`catia_surface_probe`（沿给定方向求曲面高度，先自校准再出数） |
| Assembly | 14 | 新建、加件、列举、约束、组件位姿读写（两种矩阵约定互报偏差与正交性）、重命名、复制、替换 |
| Appearance | 3 | 装配按实例、零件按节点上色；写颜色会 flush 零件（不 `Update()` 不上屏） |
| Measurement | 8 | 惯性、距离、包围盒、参数、曲面检查、形状审计 |
| Export | 4 | STEP / STL / IGES 交付 |
| Knowledge | 6 | 参数、公式、关系、设计表；长度列不带单位会按**米**读，回执会说明 |
| Drawing | 13 | 新建图纸、生成视图（平面/面/实体）、投影、剖视、局部放大、文字、表格、自动与手动标注、图幅、图框与标题栏 |
| Planner | 2 | `catia_run_plan`（含断言与回滚）、`catia_measure_topology` |
| Diagnostics | 2 | `catia_diagnose`（工厂、类型库、能力边界全探针）、`catia_server_info`（版本、工具数、CATIA 版本，不连接） |
| Blueprint | 2 | `catia_read_drawing`（DXF/SVG/矢量 PDF → 轮廓，离线）、`catia_import_drawing`（1:1 画进草图，支持多视图与样条模式） |
| Sheet Metal | 2 | 钣金参数读写（厚度、折弯半径、K 因子、止裂槽）、新建带参数的零件；**几何不在其中**：没有壁、翻边、折弯、展开，因为 `SheetMetalFactory` 不提供 `AddNew*` |

工具数由 `tests/test_readme_tool_counts.py` 逐个模块核对，改代码不改表就会红。

## 文档

| 我想… | 看这里 |
| --- | --- |
| 看每个工具的参数、实测依据与坑 | [README.en.md](README.en.md)（英文完整版，含全部工具逐条说明） |
| 查版本变更 | [CHANGELOG.md](CHANGELOG.md)、[Releases](https://github.com/wudahua/MCP_CATIA_MechDog/releases) |
| 了解与上游的差异 | [FORK.md](FORK.md)、[docs/upstream-diff.md](docs/upstream-diff.md) |
| 远程 Agent 接入 | [docs/remote-access.md](docs/remote-access.md) |
| 服务端运行时与并发 | [docs/server-runtime.md](docs/server-runtime.md) |
| 参与开发 | [CONTRIBUTING.md](CONTRIBUTING.md) |

## 已知限制

- **平面不等于 datum，就像「南边」不等于一个门牌号。** `frame` 平面是无限的，两个草图落在同一平面上不代表它们踩在同一个基准上：实测 z=0 上相距 50 mm 的两个 datum 报出相同 H/V、不同原点，删其一的基准对面另一个毫无影响。判「谁还站在这个 datum 上」要比完原点与两个方向；退化成比平面会把 P06 这类多轮位零件全部误拒。另记一条同源的坑：**`getattr` 成功不代表成员存在**——`Sketch.Reference` / `.Support` / `.Plane` 都会返回一个占位对象，真去用就报 `'NoneType' object has no attribute 'Name'`，所以「能不能读」必须靠用一次来判断。
- **由输入算出来的量，不是观测。** 上一版用「过两端点和弧中点的圆」去闭合轮廓，而那个中点是**由半径算出来的**——把它当第三个约束喂回去，等于把全部残差挤进半径，浅弧上放大 `1/(1−cos(θ/2))`（28° 弧 34 倍、90° 弧 3.4 倍）。判据就是**放大倍数**本身：端点偏 0.0027 mm 却挪了 0.09 mm，这个倍数在说「哪个自由度被冻结了」。正解是保住那个有物理含义的量（半径/同轴），只移动派生不出来的那个（圆心）——两点 + 半径只剩一个自由度，选离调用方最近的那一支。
- **贵的那一次调用往往不是你以为的那次。** 「选边 10 s」查下来是 `GetMeasurable` 每条边 91 ms（288 条 26.2 s），而读 `Length` 只要 312 ms——全表建 Measurable 只是为了让指纹有个长度。于是「按名字选边」根本不需要它：34.1 s → 4.5 s。**缓存整张边表被拒绝**：省下的只是枚举（5.6 s），代价是可能把重建前的引用交给调用方——过期引用不会响亮地失败，而是圆角落在现在占了那个下标的边上。
- **「做不到」要写清是在什么状态下测的。** 0.3.103 说「窗口大小改不了截图分辨率」，那是在**最大化窗口**上测的——最大化时 setter 本来就是空操作。普通窗口下请求 600/1100/1600 就是 600/1100/1600。于是「做不到」变成「做得到，附一句：最大化时无效」。
- **改完窗口，视图还没改。** 同一个自动化调用里新建的文档，设完 1200×800、等 250 ms 再拍，写出的是 808×744：视图布局在 CATIA 自己的消息循环里，而那个循环正在跑你的调用。`Reframe()`/`Activate()` 都没用，**等 1 秒**才有用——所以拍完要**回读文件尺寸校验并重拍**。
- **「慢」多半是自己的记账，不是 CATIA。** 单边倒角 25–56 s，实测拆开是「选边 17 501 ms / 建特征 16 ms / 重建 16 ms」：真正贵的是把整张边表**又枚举了一遍**（以为引用会随选择集失效，实测不会），以及给每个锚点都做一次**全文档** `part.Update()`。把耗时打进回执，比猜十次都有用。
- **报告提的判据也要测。** 报告说「两个 R 之和大于棱间距就一定失败」，而实测 0.9 mm 间距 + R3 两条都建成了。于是把这条从「预言」降级为「flag」，两种实测结论一起写进同一句话——预报错了会和算错一样伤信任。
- **设了参数还要 update 才算测过。** `Pad.SecondLimit.Dimension.Value` 写进去、不 update 就量体积，得到的永远是旧值：第一版探针就这样「证明」了双限不可用。
- **方向向量的长度没有含义，但曾经有后果。** `frame` 平面按 `origin + u + v` 定义，`u=[1000,0,0]` 与 `u=[1,0,0]` 是同一个平面；0.3.148 之前本地把 `u` 当位移用，于是「草图坐标 → 世界坐标」被放大 |u| 倍，样条的实测偏差变成 999 × 半径。已修，并且读数一旦大于探针自身跨度就不再作为偏差发布。
- **草图样条不能用控制点定义，只能采样逼近。** `Spline2D` 不暴露次数、节点矢量与控制点，`Factory2D.CreateSpline` 只吃「通过点」，所以 `catia_sketch_spline(control_points=…)` 在本地求值后采样绘制，回执同时给出采样偏差与**实测**的曲线偏差；采样点会作为点元素留在草图里（`GeometricElements` 没有 Remove），所以默认预算收到 0.03 mm、每段最多 32 点（0.3.147：二次 17 点、三次 33 点），需要更密就传 `tolerance` 或 `samples`，回执会说明代价。草图样条也无法做成周期曲线（`IsPeriodic` 只读），需要真正闭合请用 `catia_gsd_spline(closed=true)`。
- **草图的坐标读不回来。** 草图对测量接口什么都不答，所以「多视图是否对齐」只能由 pad 后读实体包围盒来证明，不能直接读草图位置。
- **投影到曲面的曲线不能分割或填充该曲面，但可以刻槽。** 实测：`gsd_split` / `gsd_fill` / `thick_surface` 在曲面上都失败，而同一闭合曲线在平面上分割正常；可行路线是 `catia_surface_engrave`（等深刻槽）。
- **在混合曲面上取的 measurable 量的是包住它的实体。** 面的面积、重心要用拓扑引用（`Face.N`），用 hybrid 引用会得到整体。
- **钣金只有参数，没有几何。** 没有 wall / flange / bend / unfold / flat-pattern：Automation 层的 `SheetMetalFactory` 不提供 `AddNew*`，只有 `CreateSheetMetalParameters` 与 `GetThickness()`。
- **V5 草图里没有文字。** 不是本项目缺功能：草图没有文字对象，字形要转成轮廓（内置单线字体 `catia_text_strokes`，或导入 DXF/SVG 轮廓）。
- **量不到时回执以 `UNVERIFIED:` 开头。** 遇到它请按回执指出的失败项处理，不要当成成功。

完整清单（三十余条，每条都带实测日期与格数）见 [README.en.md 的 Known limits](README.en.md#known-limits)。

## 开发

```bash
# 全部离线单测（不需要 CATIA）
.venv/Scripts/python -m pytest tests -q --basetemp=<全新临时目录>

# 工具定义数与路由数是否相等
.venv/Scripts/python tests/test_server.py
```

两条命令从 0.3.67 起跑同一个相等断言：曾经 smoke 脚本自带一份九个模块的清单，报出「108 个工具定义 vs 127 条路由」而 pytest 全绿——最强的检查恰好是 CI 没跑的那个。

真机验收脚本在 `scripts/b30_*_acceptance*.py`，每版发布前在装有 CATIA 的机器上跑一遍，结果写进 CHANGELOG。发版流程（打包、`.sha256`、GitHub release、元数据与字节双回校验）见 [PUBLISH.md](PUBLISH.md)。

## 许可证与来源

[MIT](LICENSE)。上游版权保留在 `LICENSE` 中（Adam M'HAMDI、Ondřej Schiller、Chong Zhu）；本 fork 的改动是在其之上的增补与修补，具体范围见 [FORK.md](FORK.md) 与 [CHANGELOG.md](CHANGELOG.md)。

不针对本 fork 的改进，欢迎回上游——它接受贡献。
