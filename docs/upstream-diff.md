# 与上游的完整比对 —— 0.3.62 ← 0.2.2

**怎么测的**：`git archive upstream/main` 导出上游整棵树，两个包（都叫 `catia_mcp`）分别在
独立进程里调用各自的 `get_tool_definitions()` 导出 JSON，再逐字段比对。不是凭 README 计数，
也不是凭记忆。上游 = `daiemon12/catia-v5-mcp-server` 的 `main`（`74f3a55`），本地 = `88c03e6`。

> **0.3.63 之后的更新**：本文的 knowledge 一节记录的是 0.3.62 的状态——那时 `knowledge` 整模块
> 缺失。0.3.63 已把上游的 5 个工具取回、在 B30 上逐个实测并修正了 4 处，另加
> `catia_apply_design_table`（上游只能建表、不能切配置）。所以现在只有 `drawing` 是整模块缺失，
> 工具数是 114 而不是 108。本文其余部分仍按 0.3.62 记录，未回改。

---

## 1 总量

| 项 | 上游 0.2.2 | 本地 0.3.62 | 差 |
|---|---|---|---|
| 工具数 | 98 | **108** | +26 新增 / −16 移除 |
| 两者都有 | — | 82 | 其中 48 个参数完全一致，34 个有差异 |
| 模块（工具类） | 10 | 9 | 少 `drawing`、`knowledge`，多 `planner` |
| `catia_mcp/` 代码行 | 6 755 | **24 468** | 3.6× |
| 测试用例 | 17 个 `def test_` | **951 个 `def test_`**（pytest 收集 1 020，含参数化展开） | 56× |
| README | 543 行 | 1 352 行 | 2.5× |
| CHANGELOG | 252 行 | 5 497 行 | 21× |

---

## 2 上游有、本地没有（16 个工具）

| 模块 | 工具 | 说明 |
|---|---|---|
| `drawing`（995 行） | `catia_new_drawing`、`catia_drawing_add_view`、`catia_drawing_projection_view`、`catia_drawing_section_view`、`catia_drawing_detail_view`、`catia_drawing_add_dimension`、`catia_drawing_generate_dimensions`、`catia_drawing_list_dimensions`、`catia_drawing_list_view_geometry`、`catia_drawing_add_text`、`catia_drawing_add_table` | 2D 工程图，需要 Drafting 许可证 |
| `knowledge`（277 行） | `catia_create_parameter`、`catia_set_formula`、`catia_create_design_table`、`catia_list_relations`、`catia_delete_relation` | 参数与设计表（知识工程） |

这两块是**整模块缺失**，不是工具级删减：本地没有对应的 `.py` 文件。
fork 的取舍是"只保留能建出并验收实体所需的部分"，2D 出图与参数化设计表不在其中。

---

## 3 本地有、上游没有（26 个工具）

| 模块 | 工具 | 解决的问题 |
|---|---|---|
| planner（新增模块，1 170 行） | `catia_run_plan`、`catia_measure_topology` | 一次调用跑完 `{tool,args}` 配方并断言体积/面积/面边点数；拓扑计数 |
| assembly | `catia_set_component_transform`、`catia_get_component_transform`、`catia_rename_component`、`catia_copy_component` | 3×3 矩阵位姿（两种约定）、可读的实例名、一次复制 N 个实例 |
| part_design | `catia_new_body`、`catia_set_active_body`、`catia_list_bodies`、`catia_delete_body`、`catia_boolean`、`catia_set_visibility`、`catia_solid_loft` | 多实体零件的分体管理、布尔、可见性、薄壁实体放样 |
| gsd | `catia_gsd_extract`、`catia_gsd_boundary`、`catia_gsd_heal`、`catia_gsd_fillet_surface`、`catia_gsd_spine`、`catia_gsd_rotate`、`catia_gsd_translate` | 曲面取边界、缝合修复、面圆角、脊柱、变换（旋转/平移） |
| document | `catia_activate_document`、`catia_save_as_copy`、`catia_set_part_number` | 多文档切换、快照、件号 |
| sketcher | `catia_sketch_edit`、`catia_sketch_delete_element` | 闭合草图重新打开、删单个元素 |
| measurement | `catia_check_surface` | 曲面体检（折返边、穿壁边、离散曲率、可加厚判定） |

每个工具的引入理由在 README 的 `### Added (26 tools over upstream)` 表里逐条记录（含实测日期）。

---

## 4 共有的 82 个：参数差异 34 个

### 4.1 别名：同一个东西多种拼写（7 个工具）

| 工具 | 别名 → 主名 |
|---|---|
| `catia_gsd_circle` | `plane` / `support` → `support_plane` |
| `catia_gsd_project` | `plane` / `support_plane` → `support` |
| `catia_gsd_plane_offset` | `plane` / `support` → `base_plane` |
| `catia_gsd_intersection` | `cutter` → `element2` |
| `catia_gsd_split` | `element2` → `cutter` |
| `catia_gsd_join` | `surfaces` → `elements` |
| `catia_measure_distance` | `elem1`/`elem2`/`element1_name`/`element2_name` → `element1`/`element2` |

这些同时**放宽了 required**（主名移出必填、别名进 properties），因为客户端在服务端归一化之前
就做 schema 校验，只声明别名而不改 required 等于没做（0.3.60 踩过）。

### 4.2 新能力（26 个工具新增参数）

| 工具 | 新增参数 | 作用 |
|---|---|---|
| `catia_hole` / `catia_pocket` | `through_all` | 通孔/通切，不用猜壁厚 |
| `catia_delete_feature` | `names`、`index`、`last`、`cascade` | 批量删、按位置删、删最后一个、连带删构造点 |
| `catia_fillet` / `catia_chamfer` | `edge_anchors`、`edge_names`、`anchor_tol`、`on_anchor_miss`、`propagation` | 用坐标锚定边（不靠 `Edge.N` 猜），批量，失败策略，切线传播 |
| `catia_list_edges` | `coords`、`faces`、`include_nls`、`limit`、`min_length`、`max_length`、`offset` | 每条边的点、按相邻面筛、长度筛、分页 |
| `catia_get_bounding_box` | `method`、`component`、`document`、`include_curved`、`warm_up` | 顶点扫/网格两种口径、按实例、跨文档、曲面件回退、冷态成本 |
| `catia_get_inertia` | `document`、`document_name` | 测非活动文档 |
| `catia_rect_pattern` | `dir1_axis`、`dir2_axis`、`reverse_dir1`、`reverse_dir2` | 用全局轴而不是边（边的方向读不回来） |
| `catia_circ_pattern` | `radius_aligned`、`reverse` | 副本随图案旋转、反向 |
| `catia_shaft` / `catia_groove` | `axis` | 绕草图的水平/竖直绝对轴 |
| `catia_shell` | `external_thickness` | 外偏移厚度 |
| `catia_draft` | `neutral_face` | 指定拔模中性面 |
| `catia_gsd_multi_section_surface` | `start_tangent`、`end_tangent`、`ruled` | 首末截面保持相切、直纹 |
| `catia_gsd_spline` | `create_points`、`element` | 自动建点、用草图轮廓 |
| `catia_sketch_arc` | `p1`/`p2`、`direction`、`major` | 两端点建弧、方向、优弧 |
| `catia_sketch_line` / `-point` | `construction` | 构造几何 |
| `catia_sketch_get_geometry` | `sketch_name`、`hide_construction` | 按名查、隐藏构造 |
| `catia_create_sketch` | `offset` | 偏移基准面 |
| `catia_add_component` | `count` | 一次加 N 个实例 |
| `catia_new_part` / `-product` | `file_path` | 建到指定路径 |
| `catia_save_document` | `overwrite` | 覆盖保存 |
| `catia_list_components` | `part_number`、`names_only` | 报件号、只列名 |

### 4.3 放宽 required（12 个）

- **别名类 7 个**：`catia_gsd_circle`、`_project`、`_plane_offset`、`_intersection`、`_split`、`_join`、`catia_measure_distance`
- **换一种输入即可省掉必填项 5 个**：
  - `catia_delete_feature`：`feature_name` → 可用 `names` / `index` / `last`
  - `catia_hole`、`catia_pocket`：`depth` → 可用 `through_all`
  - `catia_sketch_arc`：`start_angle`/`end_angle` → 可用 `p1`/`p2`（两端点建弧）
  - `catia_gsd_spline`：`points` → 可用 `element`（草图轮廓）

### 4.4 收紧 required（1 个）

`catia_shell`：`faces_to_remove` 变成必填。上游 schema 里它不 required，但实现里
`if not faces: raise ValueError` —— 等于必填。本地只是把隐含要求写进 schema，行为不变。

### 4.5 删除参数（1 个工具，2 个参数）

`catia_coincidence_constraint` 删掉了 `element1` / `element2`。上游声明了它们，但实现从不读
（`AddBiEltCst(0, comp1, comp2)`），调用方传了也只会得到一个"整件对整件"的约束，而回话说是成功。
删掉是把这个静默空操作变成"这个工具不接这个参数"。

---

## 5 共有工具的实现差异（同一件工作，两种做法）

| 主题 | 上游做法 | 本地做法 | 依据 |
|---|---|---|---|
| **孔的深度** | `AddNewHoleFromSketch(sketch, depth)`，回话直接报 `depth` | 走 `BottomLimit.LimitMode=0` + `BottomLimit.Dimension.Value` | 上游那第二个参数在 B30 上**不是深度**：D4 要 4 深，在 10 mm 块上直接打穿；改 BottomLimit 后 D4×4 移除 50.3 mm³、D4×6 移除 75.4 mm³，都等于 πr²d |
| **孔的位置** | 不检查，回话不提 | 用体积与 COG 变化校验，不在材料上就回滚并报错 | `AddNewHoleFromSketch` 的轴取自草图里的**点**，只含一个圆的草图会被钻在草图原点：(0,0) 与 (10,0) 的圆生成完全相同的零件，而移除体积仍是 πr²h，体积检查看不出来 |
| **螺纹** | 上游 `f9751a1` 起写 `ThreadingMode = 0`（`_set_prop` 回退），先 update 再设 | 同值，但**两个方向都写**（`threaded:false` 也写 1），设值失败时报错 | 类型库 `CatHoleThreadingMode`：0 螺纹 / 1 光孔；且孔**继承上一只孔的模式**（三只连建读 0/0/0） |
| **孔的螺纹规格** | 只给模式，不设螺距/标准 | 同（没有更细的控制），但回话明说未设 | 两侧一致 |
| **pocket 的对称切** | `IsSymmetric = True` | 两次单侧切，各 `depth/2` | `Pocket + IsSymmetric` 在 B30 上**被接受但忽略**：38.1 的中间面切按 19.05 单侧算，移除 5 609.6 mm³，正好是单侧 19.05 的数，不是双侧的 11 150.9 |
| **shaft/groove** | `AddNewShaft(sketch)` + `RevoluteAxis = AbsoluteAxis.VDirection` | `AddNewShaftFromRef` + `SetProfileElement` + `FirstAngle.Value` + 水平/竖直参考轴 | `AddNewShaft(sketch)` 在本机的实测里就是 `UpdateObject` 失败（0.3.x，见 FORK.md 补 2）；`FirstAngle` 是带 `.Value` 的对象，不是数字 |
| **草图的绝对轴** | `GeometricElements.Item("AbsoluteAxis")` | `Item(1)`（按位置） | 中文版 `Item("AbsoluteAxis")` 报 E_FAIL，元素名是"绝对轴" |
| **边/面的定位** | 直接 `Edge.N` / `Face.N`（`_topo_index` 解析） | 名称 + 坐标锚点（`edge_anchors`）、面键（`Pad.6;2`） | 拓扑编号在重建后会变；坐标是调用方唯一能稳定表达"这条边"的东西 |
| **导出** | `doc.ExportData(path, fmt)` 直接调；有格式白名单，无其它检查 | 同一调用，外加：空几何拒绝、全隐藏时删空文件并报错、`DisplayFileAlerts=False`、`os.path.normpath` | 空文档/全隐藏会写出 37 字节 0 三角形 STL 并报成功；目标已存在会弹覆盖框把脚本挂死；`ExportData` 拒绝正斜杠路径（E_FAIL），而 Python 里 `C:/x/y` 比反斜杠更常见 |
| **惯性** | 体积/面积/COG/质量/张量 | 同，外加 `uniform_thickness_mm`（2V/A）、成本账（`elapsed_ms`/冷热）、多文档路由、无 Product 时跳过装配扫描（3 103→42 ms） | — |
| **包围盒口径** | 只扫**最后一个特征**的顶点；全曲面件直接抛 `UNSUPPORTED_CAPABILITY` | 默认 STL 网格（平面精确、曲面下界），可切顶点扫，可按实例、可跨文档 | 曲面会凸出到所有顶点之外，所以"算不出来"和"算小了"是两件事；本地把前者变成后者的一种可选口径，并在回话里标明是哪一种 |
| **曲面体检** | 无 | `catia_check_surface`：折返边、穿壁边、离散曲率、可加厚判定 | 上游没有能回答"这个曲面能不能加厚 0.3"的工具 |

---

## 6 非工具层

| 层 | 上游 | 本地 | 差在哪 |
|---|---|---|---|
| `connection.py` | 189 行 | 731 行 | 文档身份解析（重名不静默取错）、草图别名、元素归属记录、gen_py 缓存兼容（`_dynamic`）、装配引用统计 |
| `results.py` | 42 行 | 385 行 | `CodedError` 及其子类（`ArgumentRejected`/`ArgumentOutOfRange`/`ComponentNotFound`/`PathNotSupported`）与 `FeatureRejected`、`failure_code` 归一、`is_read_only` |
| `dialogs.py` | 不存在 | 246 行 | 模态框看门狗：`#32770` 在 COM 调用被阻塞时由另一线程应答 |
| `topo_search.py` | 不存在 | 358 行 | 拓扑查找（按坐标/面键找边与面） |
| `planner.py` | 不存在 | 1 170 行 | 一次调用跑完整配方 + 几何断言 |
| `server.py` | 243 行 | 431 行 | 未知参数检查（`_declared_arguments`）、会话状态、错误包装 |
| STA 单线程 worker | **有** | 有 | 两边一致，不是本地独有 |

---

## 7 文档与过程

| 项 | 上游 | 本地 |
|---|---|---|
| README | 543 行，工具表 | 1 352 行，含 Known limits、逐条实测日期 |
| CHANGELOG | 252 行 | 5 497 行，每个版本记录"什么被推翻" |
| FORK.md | 无 | 302 行（**数字已过时**：开头写 "98 → 96"、安装段写 "72 passed"；当前是 108 个工具、951 个测试函数／1020 收集） |
| `docs/` | `remote-access.md`、`server-runtime.md` | 同两个文件 |

**FORK.md 需要更新**："98 → 96" 与 "72 passed" 是 0.3.5 时代的数字，之后没有同步过。

---

## 8 结论

**上游强于本地的地方**：2D 工程图（11 个工具，995 行）与知识工程（5 个工具，277 行）这两个模块本地没有。
如果任务需要出图或做参数化设计表，本地做不到，必须回到上游。

**本地强于上游的地方**：三件事——
1. **不谎报**：上游有三处"参数被接受、几何没跟上"——孔的深度（第二个参数不是深度，实测会打穿）、
   孔的位置（从不校验，草图原点即钻孔点）、pocket 的 `IsSymmetric`（本机被接受但忽略，
   38.1 的中间面切只切掉 19.05 的量）。螺纹与 Measurable 两条上游已经修对（`ThreadingMode` 0 =
   螺纹、`_bound_measurable`），本机也复现过真值，不算在缺陷里；
2. **可验收**：拓扑计数、曲面体检、几何断言、体积/COG 复核，上游没有能判定"建对了吗"的工具；
3. **多文档与拓扑稳定**：重名解析、草图别名、坐标锚定边，这些是重建后还能继续工作的前提。

**两边一致的判断**：STA 单线程 worker、`ExportData` 保存路径、`Measurable` 的 MKS 单位约定 ——
这些上游是对的，本地照抄并保留注释，没有重写。

**未解决的分歧**（本地记录了但没改）：`ReferenceType` 在 fix 约束上的含义查不到枚举，
`OpenEdition` 返回 None 与 thick 的偶发失败在本机未复现。
