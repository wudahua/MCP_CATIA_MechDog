# 发布到 GitHub 的操作清单

结论先说：**可以发**。上游是 MIT，允许分叉与再发布；仓库里没有密钥、没有本地绝对路径、
没有超过 500 KB 的二进制。

原计划的 5 件事，**第 1、2、3、5 件已经代做完毕**，只剩"在网页上建仓库 + push"
需要你本人动手（push 要 GitHub 凭据，我没有，也不应该替你持有）。

---

## 一、已经改好的（不用再动）

| 项 | 原状 | 现状 |
|---|---|---|
| `README.md` | 上游 README：badge、clone 命令、Discord/LinkedIn 全指向上游；工具表里还写着已删除的 Drafting 11 个、Knowledgeware 5 个 | 重写为 fork README；clone 地址已是 `github.com/wudahua/catia-v5-mcp-lite` |
| `.github/FUNDING.yml` | `github: daiemon12` —— 等于让访客给上游作者打钱 | 已删除 |
| `traffic/` + `traffic.yml` + `collect_traffic.py` | 上游的克隆/浏览量统计，依赖 `secrets.TRAFFIC_TOKEN`（你没有，workflow 会天天失败并自动提交垃圾 commit） | 已全部删除 |
| `pyproject.toml` | `name = catia-v5-mcp-server`、`version = 0.2.2` | `catia-v5-mcp-lite` / `0.3.0` |
| `CHANGELOG.md` | 只有上游的 `[Unreleased]` | 顶部加了 `[0.3.0] — lite fork` 条目 |
| `LICENSE` | 上游三方版权 | **原样保留**（MIT 强制要求，删了就违规） |
| **提交署名** | 7 个提交混着 `WorkBuddy <workbuddy@local>` 和一个用不上的 `users.noreply.github.com` 邮箱 | 全部改写为 `wudahua <wudahua@users.noreply.github.com>`；上游 113 个提交的历史**一个字节没动** |
| **远端** | `origin` 指向上游 `daiemon12/catia-v5-mcp-server`（你无写权限） | `origin` → `wudahua/catia-v5-mcp-lite`；上游保留为 `upstream` |
| **工具数断言** | `tests/` 里两处还写着上游的 98 | 已同步为 86 |
| **HTTP 测试** | `test_missing_or_wrong_token_is_rejected` 在设了 `HTTP_PROXY` 的机器上必红 | 见下方"顺手修的一个测试缺陷" |

### 顺手修的一个测试缺陷

`tests/test_http_transport.py` 里的 `httpx.AsyncClient()` 默认 `trust_env=True`，
会读系统 `HTTP_PROXY`。一旦有代理，httpx 对明文 HTTP 就发 absolute-form 请求行
（`POST http://127.0.0.1:PORT/mcp`），uvicorn 把整条 URI 塞进 `scope["path"]`，
服务端于是对一个路径完全正确的请求回了 404 —— 表现为"第一条 401 正常，第二条莫名 404"。

这不是新引入的 bug：**在没改动的上游仓库里跑同一个测试，红得一模一样**，只是上游 CI
没有代理变量所以看不出来。修法是给测试的 HTTP client 全部加 `trust_env=False`，
让测试对运行环境免疫。服务端代码一行未改。

## 二、只剩这一步要你做

### 建仓库并推送

选一种：

**A. 真 fork**（推荐）—— 想继续跟上游同步、想给上游提 PR

1. 打开 https://github.com/daiemon12/catia-v5-mcp-server
2. 右上角 **Fork** → owner 选 `wudahua` → Create fork
3. 建好后进你 fork 的仓库 → Settings → 把仓库名改成 `catia-v5-mcp-lite`（fork 关系会保留）
4. 回到本地：

```bash
cd "F:/WorkBuddy Space/MCP catia-v5-lite"
git push -u origin main --force
```

`--force` 是必须的：上游那 113 个提交的 SHA 你这边是原样的，但 Fork 出来的仓库
默认在同样的提交上，你本地多了 5 个改写过的提交，历史不一致，普通 push 会被拒。

**B. 独立仓库** —— 打算彻底分道，或要私有

1. https://github.com/new → Repository name 填 `catia-v5-mcp-lite`
2. **不要**勾 "Add a README file"、不要加 .gitignore、不要选 license（否则会有初始提交，push 冲突）
3. 本地：

```bash
cd "F:/WorkBuddy Space/MCP catia-v5-lite"
git push -u origin main
```

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

## 三、发布前最后一遍自检（已全部通过）

```bash
git status --short                 # 干净
git ls-files | wc -l               # 50
git ls-files | grep -c '^\.venv/'  # 0，没有 .venv 被提交
python -m pytest tests -q          # 34 passed
```

工具数：GSD 24 + PartDesign 18 + Sketcher 11 + Assembly 11 + Document 9 +
Measurement 6 + Export 4 + Planner 2 + Diagnostics 1 = **86**。

## 四、几个容易踩的坑

**不要发到 PyPI。** 这个包的 import 名仍是 `catia_mcp`，与上游完全同名。
发到 PyPI 会让 `pip install` 的用户在两个包之间随机撞车；你的 README 里那句
"不要和上游装在同一个 venv" 只是提醒，挡不住误装。

**不要把 `.venv` 一起提交。** 现在 `.gitignore` 已经覆盖 `.venv/`、`*.log`、
`__pycache__/`；如果你手工 `git add -A` 前看过 `git status`，就不会带上去。

**CI 会跑 `windows-latest`。** `.github/workflows/ci.yml` 是上游的，逻辑仍然适用
（`pip install -e ".[dev]"` + `pytest`）。它没有 CATIA，所以只跑离线测试 ——
34 个测试全部是离线的，不会红。

**别在 README 里写成"官方版"。** 现在首段写的是 "A trimmed, verification-first
fork of …"，保留了上游链接与作者名，这是 MIT 之外的基本礼貌，也避免搜索引擎混淆。

## 五、顺带把修复回馈上游（建议做）

`topo_search.py` 那个中文 CATIA 的 `Selection.Search` 本地化修复，上游**没有**，
而所有中文版 CATIA 用户都会撞上。它跟本 fork 的取舍无关，适合单独提 PR：

```bash
git checkout -b fix/zh-cn-topology-search 11a9d6f   # 中文兼容修复那个提交
git push -u origin fix/zh-cn-topology-search
```

然后在 GitHub 上对着 `daiemon12/catia-v5-mcp-server` 开 PR。上游对贡献是接受的
（12 个 PR 合了 4 个），这条冲突面又小，通过概率高。合了之后你以后
`git fetch upstream` 就自动带上，不用每次 rebase。

价值最高的另外两条（`SaveAs` 看门狗、`Position.GetComponents` 零矩阵）也可以分拆，
但它们改到了 `document.py` / `assembly.py` 的核心路径，上游若要收，最好一条一个 PR，
每个都带复现步骤 —— 现在 README 的 "Fixed" 表格可以直接当 PR 描述用。

另外上面那个 `trust_env` 测试缺陷也适合单独提，三行改动、有明确复现条件
（`set HTTP_PROXY=... && pytest tests/test_http_transport.py`），属于上游会喜欢的类型。
