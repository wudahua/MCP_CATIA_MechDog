# catia-v5-lite — 薄壳 fork 使用说明

上游：[daiemon12/catia-v5-mcp-server](https://github.com/daiemon12/catia-v5-mcp-server)（MIT，作者 Adam M'HAMDI）
本 fork 保留上游全部 COM 现场知识，砍掉与实体建模无关的模块，补上"建模—验收"闭环最缺的四件事。

**工具数：98 → 86**（−11 绘图、−5 知识工程，+4 新增）

---

## 1. 为什么 fork 而不是重写

上游 6,660 行里真正值钱的不是结构，是注释里那些用真机试出来的"CATIA 哪里会静默失败"：
`Factory2D.CreateArc` 任何版本都不存在、`AddNewShaft` 属性拿不到、`Measurable` 的
Volume/Area 是 MKS 而 GetCOG 是 mm、out 参数跨 IDispatch 不填值。这些是四个版本现场实测
趟平的，重写等于重交学费。所以本 fork **只做加减法，不重写内核**。

---

## 2. 砍掉了什么

| 模块 | 行数 | 工具 | 砍掉的理由 |
|---|---|---|---|
| `tools/drawing.py` | 997 | 11 | 需要 Drafting 许可证，输出 2D 视图，与实体建模无关 |
| `tools/knowledge.py` | 277 | 5 | 参数/公式/设计表，本项目用不到 |

保留 `export.py`（STEP/STL 交付用得上）与 `http_transport.py`（远程 agent 用得上），
两者都与领域无关。还想继续减，见第 8 节。

---

## 3. 补了什么（本节是重点）

### 补 1 · 保存不再被模态框卡死

上游把这件事写进了 "Runtime limits"：*"A CATIA modal dialog can block the worker and
shutdown ... Close the blocking dialog"* —— 等于让用户自己点。问题在于点不到：阻塞的是
跑工具调用的那个 STA 工作线程，用户在别的窗口点，线程也不会因此恢复。

`catia_mcp/dialogs.py` 做了三件事：

1. **按进程过滤**：只处理属于 CATIA 主窗口（`CATDlgDocument`）那个 pid 的对话框，绝不误点别的程序。
2. **保存前先删目标**：覆盖已存在的文件只会换来一次"要覆盖它吗？"，而这是调用方明确要求的路径。
3. **看门狗线程**（`dialogs.watch()`）：在 COM 调用进行中每 250 ms 扫一次，只回答 `#32770`
   消息框；`CATDlgDialog` 面板不动（那可能是人手动打开的）。这样即使 `SaveAs` 被对话框
   真的阻塞住，另一个线程也能把按钮消息发进去。

`_save_document` / `_close_document(save=True)` 已经改用 `dialogs.save_as()` / `dialogs.save()`。

实测（同一台机、中文 CATIA B30）：

```
[首次存 A]    0.13s  -> Document saved as: ...\A.CATPart
[另存为 B]    1.15s  -> Document saved as: ...\B.CATPart
[覆盖 B]      2.06s  -> Document saved as: ...\B.CATPart   ← 走重试路径
[直接 Save]   0.03s  -> Document 'B.CATPart' saved
```

上游原版在这一步抛 `方法 SaveAs 失败` 并留下一个卡住的模态框。

### 补 2 · 矩阵位姿，并顺手修掉一个会毁掉装配的上游 bug

**先说 bug。** `Position.GetComponents` 是 SAFEARRAY 出参，跨 late-bound IDispatch
不填值，和 v0.2.2 修掉的 `GetCOG` 是同一个根因 —— 但 `assembly.py` 没跟着修：

```python
matrix = [0.0] * 12
position.GetComponents(matrix)   # matrix 仍然是 12 个 0
```

后果有两个，都很严重：

* `catia_move_component` 把读到的**全零**写回 `SetComponents` → **直接把该组件的旋转清零**；
* `catia_list_components` 报的每个组件位置**都是 (0,0,0)**（实测确认）。

本 fork 改用 `SystemService.Evaluate` 在 CATIA 内部填数组，读出来是：

```
P05_CylinderCap position_components =
  [0, 0, -1,  -0.544639, -0.838671, 0,  -0.838671, 0.544639, 0,  0, 0, 402.5]
```

**再说新接口。**

* `catia_get_component_transform(component_name)` —— 同时给两种约定：
  `position_components`（CATIA 原生顺序 = R 的三列 + 原点）和 `rotation_rows`
  （行主序，即 `P_asm = R·P_local + T` 里那个 R），外加 `det`、正交误差、
  以及"局部 X/Y/Z 各自映射到装配的哪个方向"。
* `catia_set_component_transform(component_name, rotation_rows | position_components,
  translation)` —— **可以直接照抄规范里的行主序 R**，服务端负责转置；写完回读并报
  `max_deviation`、`det`、是否右手系。det ≠ +1 会直接给 warning（镜像了）。

实测：用行主序 R 回写 P05，`max_deviation = 8.7e-11`，`det = +1.0`。

> 为什么要专门做这个：转置错误 det 检查**抓不到**（转置后 det 仍是 +1），
> 6/9 件 R 恰好对称，只有 3 件会暴露。把"按列读"这件事固化进工具，比写在文档里靠人记更可靠。

### 补 3 · 计划式建模 + 几何断言

`catia_run_plan(steps, expect, stop_on_error, dry_run)`：一次调用跑完整个建模配方，
然后按期望值验收。

```jsonc
{
  "steps": [
    {"tool": "catia_new_part",              "args": {}},
    {"tool": "catia_create_sketch",         "args": {"plane": "XY"}},
    {"tool": "catia_sketch_rectangle",      "args": {"x1": -20, "y1": -10, "x2": 20, "y2": 10}},
    {"tool": "catia_close_sketch",          "args": {}},
    {"tool": "catia_pad",                   "args": {"height": 10, "direction": "normal"}}
  ],
  "expect": {
    "volume_mm3": 8000, "area_mm2": 2800,
    "faces": 6, "edges": 12, "vertices": 8,
    "cog": [0, 0, 5], "cog_tolerance_mm": 0.01
  }
}
```

实测输出：

```
ok: True | code: PLAN_OK | msg: 5 steps executed, 6 assertions passed
  volume_mm3  exp=8000.0  act=8000.0  pass=True
  faces       exp=6       act=6       pass=True
  edges       exp=12      act=12      pass=True
  vertices    exp=8       act=8       pass=True
  cog         exp=[0,0,5] act=[0,0,5] pass=True
```

**断言为什么必须有**：体积判据有盲区。悬空凸包、切反的腔、钻到空气里的孔，
体积变化都可能在 1% 以内 —— 现场实例是某件体积只差 −0.24%，但面数是 30 而不是 26。
所以这里把 `faces/edges/vertices` 设成**精确比较**（不给容差），体积/面积才给百分比容差。

分步耗时（实测，同一台机）：

| 步骤 | 耗时 |
|---|---|
| `catia_new_part` | **11 640 ms** |
| `catia_create_sketch` | 132 ms |
| `catia_sketch_rectangle` | 70 ms |
| `catia_close_sketch` | 70 ms |
| `catia_pad` | 182 ms |

**95% 的时间花在建新文档上，不是建模**。所以计划里不要为每个零件新建文档后丢弃，
能复用就复用；9 件套的方案里光开文档就要两分钟。

`dry_run: true` 只校验计划合法性（工具名拼错、递归嵌套都会被拦），不碰模型。

### 补 4 · 拓扑计数（`catia_measure_topology`）

看起来是个小工具，但有个坑必须记：`Topology.Edge` 在 body 上搜索会**连被吃掉的草图线一起返回**。
一个 40×20×10 的方块会数成 16 条边 / 12 个顶点。实测拆开看：

```
Face   6  -> {'Selection_RSur': 6}
Edge  16  -> {'Selection_WireREdge': 4, 'Selection_REdge': 12}
Vertex 12 -> {'Selection_WireFVertex': 4, 'Selection_FVertex': 8}
```

所以本工具按 `Selection_Wire*` 前缀剔除线框元素，只数实体拓扑，并把被忽略的数量一并报出来。
不经这层过滤，F-E-V 判据全是错的。

---

## 4. 另外修掉的两个可移植性问题

* **`doc.Part` 取不到**（`AttributeError: <unknown>.Part`）：gen_py 缓存打出来的 `Document`
  只暴露基接口。新增 `connection.part_of()` / `product_of()`，先试原对象再用
  `dynamic.Dispatch(oleobj)` 重包一次。`document.py`、`diagnostics.py`、`part_design.py`
  里所有 `doc.Part` 都改走这里。这一条直接决定"新建零件"能不能用。
* **`catia_new_part` 改名失败**（`方法 Name 失败`）不再吞异常，而是把原因写进返回值，
  文档建出来照样能用。

---

## 5. 安装与接入

```bash
cd "F:/WorkBuddy Space/MCP catia-v5-lite"

# 独立 venv（推荐，别和上游共用，否则两个 catia_mcp 包会互相盖）
python -m venv .venv
.venv/Scripts/pip install -e .
.venv/Scripts/pip install pytest ruff        # 可选，跑测试用
```

跑测试（不需要 CATIA，纯离线）：

```bash
.venv/Scripts/python -m pytest tests/test_planner_assertions.py tests/test_server_runtime.py -q
# 24 passed
```

MCP 客户端配置：

```json
{
  "mcpServers": {
    "catia-v5-lite": {
      "command": "F:\\WorkBuddy Space\\MCP catia-v5-lite\\.venv\\Scripts\\catia-mcp.exe"
    }
  }
}
```

临时复用上游 venv 也行，但要显式把本目录放到 `PYTHONPATH` 首位：

```json
{
  "mcpServers": {
    "catia-v5-lite": {
      "command": "F:\\WorkBuddy Space\\MCP catia-v5-mcp-server\\.venv\\Scripts\\python.exe",
      "args": ["-m", "catia_mcp"],
      "env": { "PYTHONPATH": "F:\\WorkBuddy Space\\MCP catia-v5-lite" }
    }
  }
}
```

---

## 6. 典型用法：照规范建模一件

```jsonc
// 1) 先干跑，确认工具名和参数没拼错
{"steps": [...], "dry_run": true}

// 2) 真跑，带断言
{"steps": [...],
 "expect": {"volume_mm3": 1450450, "area_mm2": 239916, "faces": 6,
            "cog": [0, 0, 178.941], "cog_tolerance_mm": 0.5},
 "stop_on_error": true}

// 3) 单独取拓扑（不算惯性，最快）
{"tool": "catia_measure_topology"}

// 4) 装配：照抄规范里的行主序 R
{"tool": "catia_set_component_transform",
 "args": {"component_name": "P05_CylinderCap",
          "rotation_rows": [[0, -0.544639, -0.838671],
                            [0, -0.838671,  0.544639],
                            [-1, 0, 0]],
          "translation": [0, 0, 402.5]}}
```

> 面数/边数这类数字强依赖"你怎么建的"（GSD 回转 vs 拉伸），所以 `expect` 里的计数请填
> **你自己实测过的值**，不要照抄别人的规范；体积和质心才是跨建法可比的。

---

## 7. 与上游同步

```bash
git fetch origin
git log --oneline origin/main ^HEAD          # 上游新提交
git cherry-pick <sha>                        # 按需挑，别整体 rebase
```

挑的时候优先看这几类：`part_design.py` 的 COM 签名修正、`topology` 相关、
新提交里带 "field" / "verified" 字样的（那是真机实测过的）。上游的 `drawing` /
`knowledge` 提交与 `server.py` 的注册段落会冲突，直接跳过。

上游有个 PR 值得盯：**#12 "Fix Part Design, Sketcher and Measurement on CATIA
V5-6R2020 (B30)"** —— 与本机同版本，合了就该 cherry-pick 进来。

---

## 8. 还想继续做减法？

一条一行，改完立刻跑测试确认工具数：

| 想砍 | 操作 |
|---|---|
| 导出 | 删 `tools/export.py`，从 `tools/__init__.py` 和 `server.py` 的 import / `_tool_modules` 各删一行 |
| 远程 HTTP | 删 `http_transport.py`，再删 `server.py` 的 `run_http()` 与 `--http/--host/--port/--token` 参数 |
| 草图工具 | `sketcher.py` 11 个工具，只用矩形/圆/折线的话可以收到 3 个 |

---

## 9. 已知限制（如实记录，没有伪装成"已解决"）

* **没有 lump 数判据**。上游同样没有。CATIA 的 lump 数要自己走拓扑遍历，
  尚未实现 —— 这是"悬空凸包"类错误唯一可靠的判据，目前只能靠面数间接看。
* **装配总质心没有封装**。要算 `Σ(Vᵢ·(Rᵢ·CGᵢ+Tᵢ))/ΣVᵢ` 需要逐件打开零件读体积与质心，
  组件未加载时会失败。现在得自己在 `run_plan` 外面算。
* **`catia_get_bounding_box` 仍是顶点扫掠**。回转体（曲面轮廓）上会低估甚至直接报
  "no vertices"。CATIA V5 自动化接口本身没有 bbox 方法（已验证 `Part.BoundingBox` 不存在），
  所以这是上游一致的固有限制，不是本 fork 引入的。要用盒子做判据，请改用
  `catia_measure_topology` + 包围盒式的轴向区间反算。
* **看门狗只答 `#32770`**。故意不碰 `CATDlgDialog` 面板，以免误关人正在操作的对话框；
  代价是 CATIA 自己的面板式提示仍需人工处理。
