# 发布到 GitHub：就绪状态与操作清单

**结论：代码层面已经可以发布。差的是"建仓库 + push"这一步，而那一步需要你的 GitHub 凭据。**

刚做过一遍发布前审计（2026-10-22），逐项结果见第二节。审计里修掉了一个会让 CI
第一次就红的阻塞项，和一批泄露本机路径的文档。

实测三个地址：

```
https://api.github.com/repos/daiemon12/catia-v5-mcp-server  → 200   （上游在）
https://api.github.com/repos/wudahua/MCP_CATIA_MechDog      → 404   （目标仓库还没建）
https://api.github.com/users/wudahua                        → 200   （账号在，2013-10-30 建号）
```

所以现在直接 `git push` 会 404，不是权限问题，是仓库根本不存在。

---

## 一、发布前审计（2026-10-22，全部通过）

| 检查项 | 结果 |
|---|---|
| 工作区 | `git status --short` 干净 |
| 被跟踪文件 | 51 个，合计 **468 KB**，最大单文件 66 KB（`part_design.py`） |
| 二进制 / 大文件 | 无。`*.log`、`.venv/`、`__pycache__/`、`.pytest_cache/` 都被 `.gitignore` 挡住 |
| 密钥 / token | 无（`api_key`/`secret`/`token`/`ghp_`/`sk-` 全仓扫描为空） |
| 本机绝对路径 | 无（`F:\WorkBuddy Space\...`、`C:\Users\...` 全仓扫描为空） |
| 提交署名 | 只剩上游作者 + `wudahua <wudahua@users.noreply.github.com>`，没有 `WorkBuddy <workbuddy@local>` |
| 远端 | `origin` → `wudahua/MCP_CATIA_MechDog`，`upstream` → `daiemon12/catia-v5-mcp-server` |
| LICENSE | 原样保留（MIT 强制，删了就违规） |
| 测试 | `pytest tests test_server.py -q` → **72 passed**，无警告 |
| CI 命令 | 与 `.github/workflows/ci.yml` 完全一致，同上 |
| 工具数 | **87**，与 README / CHANGELOG / 测试断言三处一致 |

### 这次审计修掉的三个问题

**1. `test_server.py` 会让 CI 第一次就红（阻塞项）**

根目录的 `test_server.py` 还在 `import catia_mcp.tools.drawing` 和 `tools.knowledge` ——
这两个模块在 lite fork 里已经删了。而 CI 跑的正是一模一样的命令：

```bash
python -m pytest tests test_server.py -q
```

之前几轮我只跑 `pytest tests`，所以一直没发现。现在 import 修好了，也补上了漏掉的
planner 模块（否则工具数对不上），顺带把两个 `return int` 改成 `assert` —— pytest 现在
只是警告，将来会拒绝。

**2. `FORK.md` / `PUBLISH.md` 里有本机绝对路径**

MCP 客户端配置示例和安装步骤里写着 `F:\WorkBuddy Space\MCP catia-v5-lite\...`。
不是密钥，但对读者毫无意义，也暴露了本机目录结构。已全部换成 `<path to this repo>`。

**3. 这是一个 shallow clone（阻塞项，决定"fork 还是新建"时才查出来）**

`.git/shallow` 里有两个边界提交 —— 历史是被截断的，本地只有 125 个提交。
后果：`git merge-base` 和上游算不出共同祖先，推送时 Git 无法证明历史连通，
推上去也可能是残缺历史。

已用 `git fetch --unshallow upstream` 补全，现在是完整的 **188 个提交**，
`git fsck --connectivity-only` 干净（只有一个 filter-branch 留下的 dangling blob，无害）。

这一条之前几轮审计都没发现，因为只看 `git status` 和文件清单看不出浅克隆。

---

## 二、只剩这一步要你做

### 建仓库并推送

两条路的实测差异（2026-10-22 查证，历史已从 shallow 补全）：

```
上游 main              176 个提交，HEAD = 8d84073
本地 main              188 个提交，HEAD = e388e15
共同分叉点             feb9c38
本地领先上游           14 个提交
本地落后上游            2 个提交（都是 chore(traffic) 自动提交，正是本 fork 删掉的功能）
本地是否包含 upstream/main   否   →  推到 fork 必须 --force
```

**推荐 B：新建空仓库。**

1. 本地不含 `upstream/main`（落后那 2 个 traffic 提交），fork 之后必须 `--force` 去覆盖
   fork 的默认分支。那 2 个提交本身是垃圾，但为了推上去还得强推一次。
2. 项目已经叫 `MCP_CATIA_MechDog`，定位是独立产品线。挂在
   "forked from daiemon12/catia-v5-mcp-server" 下面、头上还长期顶着 "2 commits behind"，
   和这个定位不符。
3. 免费账号的 fork 不能设为 private。
4. 空仓库 push 没有任何历史约束，一次成功。

fork 更适合这些场景：打算长期高频跟随上游（比如每周 merge）、需要 GitHub 那个
"Sync fork" 按钮、或者就是想把"这是上游衍生"写在脸上。它真正的优势是同步顺手，
而当前的取舍（删 16 个工具、改 5 处核心行为）已经离上游挺远了。

两条路都不影响给上游提 PR —— 历史共享分叉点 `feb9c38`，GitHub 认得出共同祖先。

**B. 新建空仓库（推荐）**

1. https://github.com/new → Repository name 填 `MCP_CATIA_MechDog`
2. **不要**勾 "Add a README file"、不要加 .gitignore、不要选 license（否则会有初始提交，push 冲突）
3. 本地：

```bash
cd /path/to/MCP_CATIA_MechDog
git push -u origin main
```

**A. Fork 上游** —— 想长期跟随上游更新

1. 打开 https://github.com/daiemon12/catia-v5-mcp-server → 右上角 **Fork** → owner 选 `wudahua`
2. 建好后进仓库 → Settings → 把仓库名改成 `MCP_CATIA_MechDog`（fork 关系会保留）
3. 本地：

```bash
cd /path/to/MCP_CATIA_MechDog
git push -u origin main --force
```

`--force` 是必须的：fork 的 main 停在上游 `8d84073`，本地 main 从分叉点 `feb9c38`
起领先 14 个提交、缺那 2 个 traffic 提交，普通 push 会被判 non-fast-forward。

> push 会弹凭据窗口。账号 `wudahua`，密码位置填 **Personal Access Token**（GitHub 早已
> 不接受账号密码）。没有 token 就去 Settings → Developer settings → Personal access
> tokens → Tokens (classic) → Generate new token，勾 `repo` 即可。

> **不要用 `git push --all` / `--mirror` / `--tags`。** 本地还留着改写前的安全网
> （`backup/pre-linear` 分支、`safety-aa935dd` 标签），它们指向旧署名的提交。
> 确认这次 push 成功、GitHub 上作者显示为 `wudahua` 之后，把它们删掉：
>
> ```bash
> git branch -D backup/pre-linear
> git tag -d safety-aa935dd
> git update-ref -d refs/original/refs/heads/main   # filter-branch 的残留
> ```

---

## 三、已经改好的（不用再动）

| 项 | 原状 | 现状 |
|---|---|---|
| `README.md` | 上游版：badge、clone 命令、Discord/LinkedIn 全指向上游；工具表还列着已删的 Drafting 11 个、Knowledgeware 5 个 | 重写为 fork README；clone 地址是 `github.com/wudahua/MCP_CATIA_MechDog` |
| `.github/FUNDING.yml` | `github: daiemon12` —— 等于让访客给上游作者打钱 | 已删除 |
| `traffic/` + `traffic.yml` + `collect_traffic.py` | 上游的克隆量统计，依赖 `secrets.TRAFFIC_TOKEN`（你没有，workflow 会天天失败并自动提交垃圾 commit） | 已全部删除 |
| `pyproject.toml` | `catia-v5-mcp-server` / `0.2.2` | `MCP_CATIA_MechDog` / `0.3.0` |
| `CHANGELOG.md` | 只有上游的 `[Unreleased]` | 顶部加了 `[0.3.0] — lite fork`，并记录本轮全部修复 |
| **提交署名** | 混着 `WorkBuddy <workbuddy@local>` | 全部改写为 `wudahua`；上游 113 个提交的历史一个字节没动 |
| **远端** | `origin` 指向上游（你无写权限） | `origin` → `wudahua/MCP_CATIA_MechDog`，上游保留为 `upstream` |
| **工具数断言** | 两处还写着上游的 98 | 已同步为 87 |
| **建模能力** | `catia_shaft` 建不出实体；草图只能落在 3 张主基准面；画不出构造线；贯通切除只能猜深度 | 四项全补齐并真机验证；另修了「pad/pocket 拿错草图」这个会静默切错轮廓的 bug |
| **装配可读性** | 9 个组件全叫 `Part1.1 / Part2.1 / …` | 新增 `catia_rename_component`，树里显示 `Part1 (P01_CylinderBlock)` |
| **CI 阻塞项** | `test_server.py` import 已删除的模块 | 见上一节 |
| **本机路径泄露** | `FORK.md` / `PUBLISH.md` 里 6 处 | 已换成占位符 |

### 顺手修的一个测试缺陷（上游同样存在）

`tests/test_http_transport.py` 里的 `httpx.AsyncClient()` 默认 `trust_env=True`，
会读系统 `HTTP_PROXY`。一旦有代理，httpx 对明文 HTTP 就发 absolute-form 请求行
（`POST http://127.0.0.1:PORT/mcp`），uvicorn 把整条 URI 塞进 `scope["path"]`，
服务端于是对一个路径完全正确的请求回了 404 —— 表现为"第一条 401 正常，第二条莫名 404"。

这不是 fork 引入的：**在没改动的上游仓库里跑同一个测试，红得一模一样**，只是上游 CI
没有代理变量所以看不出来。修法是给测试的 HTTP client 全部加 `trust_env=False`。
服务端代码一行未改。

---

## 四、工具数怎么来的

```
GSD 24 + PartDesign 18 + Assembly 12 + Sketcher 11 + Document 9
+ Measurement 6 + Export 4 + Planner 2 + Diagnostics 1 = 87
```

相对上游 98 的变化：−11（`drawing.py`，需要 Drafting 许可）、−5（`knowledge.py`）、
+5（`run_plan`、`measure_topology`、`get/set_component_transform`、`rename_component`）。

---

## 五、几个容易踩的坑

**不要发到 PyPI。** 这个包的 import 名仍是 `catia_mcp`，与上游完全同名。
发到 PyPI 会让 `pip install` 的用户在两个包之间随机撞车；README 里那句
"不要和上游装在同一个 venv" 只是提醒，挡不住误装。

**CI 会跑 `windows-latest`。** `.github/workflows/ci.yml` 是上游的，逻辑仍然适用
（`pip install -e ".[dev]"` + `pytest tests test_server.py`）。它没有 CATIA，所以只跑
离线测试 —— 72 个测试全部离线，不会红。

**别在 README 里写成"官方版"。** 现在首段是 "A trimmed, verification-first fork of …"，
保留上游链接与作者名 —— MIT 之外的基本礼貌，也避免搜索引擎混淆。

---

## 六、顺带把修复回馈上游（建议做）

`topo_search.py` 那个中文 CATIA 的 `Selection.Search` 本地化修复，上游**没有**，
而所有中文版 CATIA 用户都会撞上。它跟本 fork 的取舍无关，适合单独提 PR：

```bash
git checkout -b fix/zh-cn-topology-search 11a9d6f   # 中文兼容修复那个提交
git push -u origin fix/zh-cn-topology-search
```

然后在 GitHub 上对着 `daiemon12/catia-v5-mcp-server` 开 PR。上游对贡献是接受的
（12 个 PR 合了 4 个），这条冲突面又小，通过概率高。合了之后你以后
`git fetch upstream` 就自动带上，不用每次 rebase。

价值最高的另外几条也可以分拆，但都改到了核心路径（`document.py` / `assembly.py` /
`part_design.py`），上游若要收，最好一条一个 PR，每个都带复现步骤 —— 现在 README 的
"Fixed" 表格和 `CHANGELOG.md` 可以直接当 PR 描述用：

- `SaveAs` 看门狗（模态框不卡死 STA worker）
- `Position.GetComponents` 读到全零（erased rotation）
- `pad`/`pocket` 按树顺序取"最后一张草图"导致切错轮廓
- `trust_env` 测试缺陷（三行改动、有明确复现条件）
