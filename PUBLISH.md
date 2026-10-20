# 发布到 GitHub 的操作清单

结论先说：**可以发**。上游是 MIT，允许分叉与再发布；仓库里没有密钥、没有本地绝对路径、
没有超过 500 KB 的二进制。但要先处理 5 件事，其中 3 件我已经代改完。

---

## 一、已经替你改好的（不用再动）

| 项 | 原状 | 现状 |
|---|---|---|
| `README.md` | 上游 README：badge、clone 命令、Discord/LinkedIn 全指向上游；工具表里还写着已删除的 Drafting 11 个、Knowledgeware 5 个 | 重写为 fork README：说明差异、修正工具表、保留上游署名 |
| `.github/FUNDING.yml` | `github: daiemon12` —— 等于让访客给上游作者打钱 | 已删除 |
| `traffic/` + `traffic.yml` + `collect_traffic.py` | 上游的克隆/浏览量统计，依赖 `secrets.TRAFFIC_TOKEN`（你没有，workflow 会天天失败并自动提交垃圾 commit） | 已全部删除 |
| `pyproject.toml` | `name = catia-v5-mcp-server`、`version = 0.2.2` | `catia-v5-mcp-lite` / `0.3.0` |
| `CHANGELOG.md` | 只有上游的 `[Unreleased]` | 顶部加了 `[0.3.0] — lite fork` 条目 |
| `LICENSE` | 上游三方版权 | **原样保留**（MIT 强制要求，删了就违规） |

## 二、你必须自己处理的 5 件事

### 1. 换掉 README 里的占位符

```bash
# 把 <your-org> 换成你的 GitHub 用户名或组织名
grep -n "<your-org>" README.md
```

只有 `git clone` 那一行需要改；clone 之后 `cd catia-v5-mcp-lite` 也要跟着改。

### 2. 改写本地提交的作者身份

现在有两个提交署名 `WorkBuddy <workbuddy@local>`，发上去不会挂在你的账号下面。
只改写"上游之后的提交"，上游 113 个提交的历史保持完整（这对将来提 PR 很重要）：

```bash
git filter-branch -f --env-filter '
  if [ "$GIT_AUTHOR_NAME" = "WorkBuddy" ]; then
    export GIT_AUTHOR_NAME="Your Name"
    export GIT_AUTHOR_EMAIL="you@example.com"
    export GIT_COMMITTER_NAME="Your Name"
    export GIT_COMMITTER_EMAIL="you@example.com"
  fi
' cf0c717..HEAD
```

不想装 filter-branch 的话，`git rebase -i` 里对每个提交 `edit` 再
`git commit --amend --author="Your Name <you@example.com>"` 也行，就是慢。

### 3. 加你自己的 remote（**这一步不做，push 一定失败**）

```bash
git remote -v
# origin  https://github.com/daiemon12/catia-v5-mcp-server.git   ← 是上游，你无写权限
```

把上游降级成 `upstream`，给你自己的仓库让出 `origin`：

```bash
git remote rename origin upstream
git remote add origin https://github.com/<your-org>/catia-v5-mcp-lite.git
git push -u origin main
```

### 4. 决定用哪种方式建仓

| 方式 | 做法 | 适合 |
|---|---|---|
| **A. 真 fork**（推荐） | 在 GitHub 网页上点上游的 Fork，再把本地历史强推上去：`git push -u origin main --force` | 想继续跟上游同步、想给上游提 PR。仓库页会显示 "forked from daiemon12/…"，关系透明 |
| **B. 独立仓库** | 新建空仓库（不勾 README），再 push | 打算彻底分道，或要私有 |

> 注意：GitHub 的 "Fork" 按钮建的仓库**不能改名为上游同名以外的任意名字**是误解——
> 可以改名，但 fork 关系会保留。若你希望它看起来像一个独立项目而不是 fork，
> 用方式 B，并在 README 首段保留 "lite fork of …" 这句（现在已经有）。

### 5. 发布前最后一遍自检

```bash
git status --short                 # 干净
git ls-files | wc -l               # 50（删掉 traffic 与流量工作流之后）
ls .venv 2>/dev/null || echo "没有 .venv 被提交"   # .gitignore 已覆盖
python -m pytest tests -q          # 24 passed（要装好 dev 依赖）
```

---

## 三、几个容易踩的坑

**不要发到 PyPI。** 这个包的 import 名仍是 `catia_mcp`，与上游完全同名。
发到 PyPI 会让 `pip install` 的用户在两个包之间随机撞车；你的 README 里那句
"不要和上游装在同一个 venv" 只是提醒，挡不住误装。

**不要把 `.venv` 一起提交。** 现在 `.gitignore` 已经覆盖 `.venv/`、`*.log`、
`__pycache__/`；如果你手工 `git add -A` 前看过 `git status`，就不会带上去。

**CI 会跑 `windows-latest`。** `.github/workflows/ci.yml` 是上游的，逻辑仍然适用
（`pip install -e ".[dev]"` + `pytest`）。但它没有 CATIA，所以只跑离线测试 ——
这正好是我们的 24 个测试覆盖范围，不会红。

**别在 README 里写成"官方版"。** 现在首段写的是 "A trimmed, verification-first
fork of …"，保留了上游链接与作者名，这是 MIT 之外的基本礼貌，也避免搜索引擎混淆。

---

## 四、顺带把修复回馈上游（建议做）

`topo_search.py` 那个中文 CATIA 的 `Selection.Search` 本地化修复，上游**没有**，
而所有中文版 CATIA 用户都会撞上。它跟本 fork 的取舍无关，适合单独提 PR：

```bash
git checkout -b fix/zh-cn-topology-search d35f2b3
git remote add fork https://github.com/<your-org>/catia-v5-lite.git   # 你的仓库
git push -u fork fix/zh-cn-topology-search
```

然后在 GitHub 上对着 `daiemon12/catia-v5-mcp-server` 开 PR。上游对贡献是接受的
（12 个 PR 合了 4 个），这条冲突面又小，通过概率高。合了之后你以后
`git fetch upstream` 就自动带上，不用每次 rebase。

价值最高的另外两条（`SaveAs` 看门狗、`Position.GetComponents` 零矩阵）也可以分拆，
但它们改到了 `document.py` / `assembly.py` 的核心路径，上游若要收，最好一条一个 PR，
每个都带复现步骤 —— 现在 README 的 "Fixed" 表格可以直接当 PR 描述用。
