# graphify

🇺🇸 [English](../../README.md) | 🇨🇳 [简体中文](README.zh-CN-current.md) | 🇯🇵 [日本語](README.ja-JP.md) | 🇰🇷 [한국어](README.ko-KR.md) | 🇩🇪 [Deutsch](README.de-DE.md) | 🇫🇷 [Français](README.fr-FR.md) | 🇪🇸 [Español](README.es-ES.md) | 🇮🇳 [हिन्दी](README.hi-IN.md) | 🇧🇷 [Português](README.pt-BR.md) | 🇷🇺 [Русский](README.ru-RU.md) | 🇸🇦 [العربية](README.ar-SA.md) | 🇮🇹 [Italiano](README.it-IT.md) | 🇵🇱 [Polski](README.pl-PL.md) | 🇳🇱 [Nederlands](README.nl-NL.md) | 🇹🇷 [Türkçe](README.tr-TR.md) | 🇺🇦 [Українська](README.uk-UA.md) | 🇻🇳 [Tiếng Việt](README.vi-VN.md) | 🇮🇩 [Bahasa Indonesia](README.id-ID.md) | 🇸🇪 [Svenska](README.sv-SE.md) | 🇬🇷 [Ελληνικά](README.el-GR.md) | 🇷🇴 [Română](README.ro-RO.md) | 🇨🇿 [Čeština](README.cs-CZ.md) | 🇫🇮 [Suomi](README.fi-FI.md) | 🇩🇰 [Dansk](README.da-DK.md) | 🇳🇴 [Norsk](README.no-NO.md) | 🇭🇺 [Magyar](README.hu-HU.md) | 🇹🇭 [ภาษาไทย](README.th-TH.md) | 🇺🇿 [Oʻzbekcha](README.uz-UZ.md) | 🇹🇼 [繁體中文](README.zh-TW.md)

[![YC S26](https://img.shields.io/badge/Y%20Combinator-S26-F0652F?style=flat&logo=ycombinator&logoColor=white)](https://www.ycombinator.com/companies/graphify)
[![The Memory Layer](https://img.shields.io/badge/Book-The%20Memory%20Layer-2ea44f?style=flat&logo=gitbook&logoColor=white)](https://safishamsi.gumroad.com/l/qetvlo)
[![CI](https://github.com/safishamsi/graphify/actions/workflows/ci.yml/badge.svg?branch=v8)](https://github.com/safishamsi/graphify/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/graphifyy)](https://pypi.org/project/graphifyy/)
[![Downloads](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fsql-clickhouse.clickhouse.com%2F%3Fquery%3DSELECT%2520concat%2528toString%2528round%2528sum%2528count%2529%2F1000%2529%2529%2C%2520%2527k%2527%2529%2520AS%2520c%2520FROM%2520pypi.pypi_downloads%2520WHERE%2520project%253D%2527graphifyy%2527%2520FORMAT%2520JSON%26user%3Ddemo&query=%24.data%5B0%5D.c&label=downloads&color=blue)](https://clickpy.clickhouse.com/dashboard/graphifyy)
[![Sponsor](https://img.shields.io/badge/sponsor-safishamsi-ea4aaa?logo=github-sponsors)](https://github.com/sponsors/safishamsi)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Safi%20Shamsi-0077B5?logo=linkedin)](https://www.linkedin.com/in/safi-shamsi)
[![X](https://img.shields.io/badge/X-graphifyy-000000?logo=x&logoColor=white)](https://x.com/graphifyy)

在你的 AI 编码助手里输入 `/graphify`，它会把整个项目，包括代码、文档、PDF、图片、视频，映射成一张可以查询的知识图谱。这样你不用在文件里到处 grep，也能直接围绕项目结构提问。

支持 Claude Code、Codex、OpenCode、Cursor、Gemini CLI、GitHub Copilot CLI、VS Code Copilot Chat、Aider、OpenClaw、Factory Droid、Trae、Hermes、Kimi Code、Kiro、Pi 和 Google Antigravity。

```bash
/graphify .
```

就这样。你会得到三个文件：

```text
graphify-out/
├── graph.html       可在任意浏览器打开：点击节点、过滤、搜索
├── GRAPH_REPORT.md  重点摘要：关键概念、意外连接、建议问题
└── graph.json       完整图谱：之后可随时查询，无需重新读取文件
```

如果想生成带 Mermaid 调用流图的可读架构页面，运行：

```bash
graphify export callflow-html
```

---

## 前置要求

| 要求 | 最低版本 | 检查方式 | 安装方式 |
|---|---|---|---|
| Python | 3.10+ | `python --version` | [python.org](https://www.python.org/downloads/) |
| uv（推荐） | 任意 | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| pipx（替代方案） | 任意 | `pipx --version` | `pip install pipx` |

**macOS 快速安装（Homebrew）：**

```bash
brew install python@3.12 uv
```

**Windows 快速安装：**

```powershell
winget install astral-sh.uv
```

**Ubuntu/Debian：**

```bash
sudo apt install python3.12 python3-pip pipx
# 或安装 uv:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 安装

> **官方包：** PyPI 包名是 `graphifyy`（两个 y）。PyPI 上其他 `graphify*` 包都不是官方关联包。CLI 命令仍然是 `graphify`。

**第 1 步：安装包：**

```bash
# 推荐方式（uv 会自动把 graphify 放到 PATH）:
uv tool install graphifyy

# 替代方式:
pipx install graphifyy
pip install graphifyy
```

**第 2 步：把 skill 注册到你的 AI 助手：**

```bash
graphify install
```

完成。打开你的 AI 助手，输入 `/graphify .`

如果想把 assistant skill 安装到当前仓库，而不是用户 profile，添加 `--project`：

```bash
graphify install --project
graphify install --project --platform codex
```

Project-scoped install 会写入当前目录下的位置，例如 `.claude/skills/graphify/SKILL.md` 或 `.agents/skills/graphify/SKILL.md`，并打印可提交文件的 `git add` 提示。支持 project-scoped install 的平台命令也接受同一个 flag，例如 `graphify claude install --project` 或 `graphify codex install --project`。

> **PowerShell 提示：** 使用 `graphify .`，不要用 `/graphify .`，因为 PowerShell 会把开头的斜杠当作路径分隔符。

> **遇到 `graphify: command not found`？** 使用 `uv tool install graphifyy` 或 `pipx install graphifyy`，两者都会自动把 CLI 放到 PATH。若使用普通 `pip`，需要把 `~/.local/bin`（Linux）或 `~/Library/Python/3.x/bin`（Mac）加入 PATH，或者运行 `python -m graphify`。

### 选择你的平台

| 平台 | 安装命令 |
|----------|----------------|
| Claude Code (Linux/Mac) | `graphify install` |
| Claude Code (Windows) | `graphify install --platform windows` |
| Codex | `graphify install --platform codex` |
| OpenCode | `graphify install --platform opencode` |
| GitHub Copilot CLI | `graphify install --platform copilot` |
| VS Code Copilot Chat | `graphify vscode install` |
| Aider | `graphify install --platform aider` |
| OpenClaw | `graphify install --platform claw` |
| Factory Droid | `graphify install --platform droid` |
| Trae | `graphify install --platform trae` |
| Trae CN | `graphify install --platform trae-cn` |
| Gemini CLI | `graphify install --platform gemini` |
| Hermes | `graphify install --platform hermes` |
| Kimi Code | `graphify install --platform kimi` |
| Kiro IDE/CLI | `graphify kiro install` |
| Pi coding agent | `graphify install --platform pi` |
| Cursor | `graphify cursor install` |
| Google Antigravity | `graphify antigravity install` |

> Codex 用户还需要在 `~/.codex/config.toml` 的 `[features]` 下添加 `multi_agent = true`。
> Codex 使用 `$graphify`，而不是 `/graphify`。

### 可选扩展

按需安装：

| Extra | 增加的能力 | 安装 |
|---|---|---|
| `pdf` | PDF 提取 | `pip install "graphifyy[pdf]"` |
| `office` | `.docx` 和 `.xlsx` 支持 | `pip install "graphifyy[office]"` |
| `google` | Google Sheets 渲染 | `pip install "graphifyy[google]"` |
| `video` | 视频/音频转写（faster-whisper + yt-dlp） | `pip install "graphifyy[video]"` |
| `mcp` | MCP stdio server | `pip install "graphifyy[mcp]"` |
| `neo4j` | Neo4j 推送支持 | `pip install "graphifyy[neo4j]"` |
| `svg` | SVG 图谱导出 | `pip install "graphifyy[svg]"` |
| `leiden` | Leiden 社区发现（仅 Python < 3.13） | `pip install "graphifyy[leiden]"` |
| `ollama` | Ollama 本地推理 | `pip install "graphifyy[ollama]"` |
| `openai` | OpenAI / OpenAI-compatible APIs | `pip install "graphifyy[openai]"` |
| `gemini` | Google Gemini API | `pip install "graphifyy[gemini]"` |
| `bedrock` | AWS Bedrock（使用 IAM，无需 API key） | `pip install "graphifyy[bedrock]"` |
| `sql` | SQL schema 提取 | `pip install "graphifyy[sql]"` |
| `all` | 上面所有能力 | `pip install "graphifyy[all]"` |

---

## 让助手始终使用图谱

在项目里构建图谱后运行一次：

| 平台 | 命令 |
|----------|---------|
| Claude Code | `graphify claude install` |
| Codex | `graphify codex install` |
| OpenCode | `graphify opencode install` |
| GitHub Copilot CLI | `graphify copilot install` |
| VS Code Copilot Chat | `graphify vscode install` |
| Aider | `graphify aider install` |
| OpenClaw | `graphify claw install` |
| Factory Droid | `graphify droid install` |
| Trae | `graphify trae install` |
| Trae CN | `graphify trae-cn install` |
| Cursor | `graphify cursor install` |
| Gemini CLI | `graphify gemini install` |
| Hermes | `graphify hermes install` |
| Kimi Code | `graphify install --platform kimi` |
| Kiro IDE/CLI | `graphify kiro install` |
| Pi coding agent | `graphify pi install` |
| Google Antigravity | `graphify antigravity install` |

这会写入一个很小的配置文件，告诉你的助手在回答代码库问题时先查知识图谱，优先使用类似 `graphify query "<question>"` 的范围化查询，而不是直接读完整报告或 grep 原始文件。在支持携带 payload 的 hook 平台上（Claude Code、Gemini CLI），hook 会在搜索类工具调用前自动触发，提醒助手走图谱路径。其他平台（Codex、OpenCode、Cursor 等）则通过持久化指令文件（`AGENTS.md`、`.cursor/rules/` 等）提供同样的 query-first 指引。`GRAPH_REPORT.md` 仍然适合做宽泛架构审阅。

要一次性从所有平台移除 graphify：运行 `graphify uninstall`；加 `--purge` 还会删除 `graphify-out/`。也可以使用各平台自己的命令，例如 `graphify claude uninstall`。

---

## 报告里有什么

- **God nodes**：项目里连接最多的概念。很多路径都会经过它们。
- **意外连接**：跨文件或跨模块的关系，按意外程度排序。
- **“为什么”**：行内注释（`# NOTE:`、`# WHY:`、`# HACK:`）、docstring 和文档里的设计 rationale 会被抽取为独立节点，并连接到它们解释的代码。
- **建议问题**：图谱最适合回答的 4 到 5 个问题。
- **置信度标签**：每条推断关系都会标记为 `EXTRACTED`、`INFERRED` 或 `AMBIGUOUS`。你始终能知道哪些是直接发现的，哪些是推测。

---

## 支持哪些文件

| 类型 | 扩展名 |
|------|-----------|
| Code (32 languages) | `.py .ts .tsx .js .jsx .mjs .ets .go .rs .java .c .cpp .h .hpp .rb .cs .kt .scala .php .swift .lua .luau .lh .zig .ps1 .ex .exs .m .mm .jl .vue .svelte .astro .groovy .gradle .dart .v .sv .sql .f .f90 .f95 .f03 .f08 .pas .pp .dpr .dpk .lpr .inc .dfm .lfm .lpk .sh .bash .json .tab .ini` |
| Docs | `.md .mdx .qmd .html .txt .rst .yaml .yml` |
| Office | `.docx .xlsx`（需要 `pip install graphifyy[office]`） |
| Google Workspace | `.gdoc .gsheet .gslides`（显式开启；需要 `gws` 认证和 `--google-workspace`；Sheets 还需要 `pip install graphifyy[google]`） |
| PDFs | `.pdf` |
| Images | `.png .jpg .webp .gif` |
| Video / Audio | `.mp4 .mov .mp3 .wav` 等（需要 `pip install graphifyy[video]`） |
| YouTube / URLs | 任意视频 URL（需要 `pip install graphifyy[video]`） |

代码通过 tree-sitter 在本地 AST 提取，不会产生 API 调用。其他内容会经过你的 AI 助手模型 API。

Google Drive for desktop 的 `.gdoc`、`.gsheet` 和 `.gslides` 文件只是快捷指针，不是文档内容。若要在 headless 提取中包含原生 Google Docs、Sheets 和 Slides，需要安装并认证 [`gws` CLI](https://github.com/googleworkspace/cli)，然后运行：

```bash
pip install "graphifyy[google]"  # Google Sheets 表格渲染所需
gws auth login -s drive
graphify extract ./docs --google-workspace
```

也可以设置 `GRAPHIFY_GOOGLE_WORKSPACE=1`。Graphify 会把快捷方式导出到 `graphify-out/converted/` 作为 Markdown sidecar，然后提取这些文件。

---

## 常用命令

```bash
/graphify .                        # 为当前文件夹构建图谱
/graphify ./docs --update          # 只重新提取已变更文件
/graphify . --cluster-only         # 不重新提取，只重新聚类
/graphify . --cluster-only --resolution 1.5      # 更细粒度的社区
/graphify . --cluster-only --exclude-hubs 99     # 从 god-node 排名中压制工具型超级 hub
/graphify . --no-viz               # 跳过 HTML，只生成报告 + JSON
/graphify . --wiki                 # 从图谱构建 markdown wiki
graphify export callflow-html      # Mermaid 架构/调用流 HTML（若已安装 hook，每次 git commit 后自动再生成）

/graphify query "what connects auth to the database?"
/graphify path "UserService" "DatabasePool"
/graphify explain "RateLimiter"

/graphify add https://arxiv.org/abs/1706.03762   # 拉取一篇论文并加入图谱
/graphify add <youtube-url>                       # 转写并加入一个视频

graphify hook install              # git commit 后自动重建
graphify merge-graphs a.json b.json              # 合并两张图

graphify prs                       # PR dashboard：CI 状态、review 状态、worktree 映射
graphify prs 42                    # 深入查看 PR #42 及其图谱影响
graphify prs --triage              # AI 给你的 review 队列排序（使用已配置的任意 backend）
graphify prs --conflicts           # 共享图谱 community 的 PR，也就是合并顺序风险
```

见下方[完整命令参考](#完整命令参考)。

---

## 忽略文件

在项目根目录创建 `.graphifyignore`。语法与 `.gitignore` 相同，包括 `!` 取反：

```gitignore
# .graphifyignore
node_modules/
dist/
*.generated.py

# 只索引 src/，忽略其他所有内容
*
!src/
!src/**
```

---

## 团队设置

`graphify-out/` 设计上适合提交到 git，这样团队里的每个人一开始就有一张地图。

**建议加入 `.gitignore`：**

```gitignore
graphify-out/manifest.json    # 基于 mtime，git clone 后会失效
graphify-out/cost.json        # 仅本地使用
# graphify-out/cache/         # 可选：提交可提速；不提交可保持仓库较小
```

**工作流：**

1. 一个人运行 `/graphify .` 并提交 `graphify-out/`。
2. 其他人 pull 后，他们的助手可以立刻读取图谱。
3. 运行 `graphify hook install`，每次 commit 后自动重建（仅 AST，无 API 成本）。它还会设置一个 git merge driver，确保 `graph.json` 不会留下冲突标记；两个开发者并行提交时，图谱会自动取并集合并。
4. 文档或论文变更时，运行 `/graphify --update` 刷新这些节点。

---

## 直接使用图谱

```bash
# 从终端查询图谱
graphify query "show the auth flow"
graphify query "what connects DigestAuth to Response?" --graph graphify-out/graph.json

# 把图谱暴露成 MCP server，方便重复 tool-call 访问
python -m graphify.serve graphify-out/graph.json

# 注册到 Kimi Code:
kimi mcp add --transport stdio graphify -- python -m graphify.serve graphify-out/graph.json
```

MCP server 会给你的助手提供结构化访问能力：`query_graph`、`get_node`、`get_neighbors`、`shortest_path`、`list_prs`、`get_pr_impact`、`triage_prs`。

> **WSL / Linux 提示：** Ubuntu 默认提供 `python3`，不是 `python`。使用 venv 避免冲突：
>
> ```bash
> python3 -m venv .venv && .venv/bin/pip install "graphifyy[mcp]"
> ```

---

## 环境变量

这些变量只在 **headless / CI 提取**（`graphify extract`）时需要。通过 IDE 内的 `/graphify` skill 运行时，模型 API 由你的 IDE 会话提供，不需要额外 key。

| 变量 | 用途 | 何时需要 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude (Anthropic) backend | `--backend claude` |
| `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` | Google Gemini backend | `--backend gemini` |
| `OPENAI_API_KEY` | OpenAI 或 OpenAI-compatible APIs | `--backend openai` |
| `DEEPSEEK_API_KEY` | DeepSeek backend | `--backend deepseek` |
| `MOONSHOT_API_KEY` | Kimi Code backend | `--backend kimi` |
| `OLLAMA_BASE_URL` | Ollama 本地推理 URL | `--backend ollama`（默认：`http://localhost:11434`） |
| `OLLAMA_MODEL` | Ollama 模型名 | `--backend ollama`（默认：自动检测） |
| `GRAPHIFY_OLLAMA_NUM_CTX` | 覆盖 Ollama KV-cache 窗口大小 | 可选，默认自动调整 |
| `GRAPHIFY_OLLAMA_KEEP_ALIVE` | 保持 Ollama 模型加载的分钟数 | 可选；设为 `0` 可在每个 chunk 后卸载 |
| `AWS_*` / `~/.aws/credentials` | AWS Bedrock，标准 credential chain | `--backend bedrock`（无需 API key，使用 IAM） |
| `GRAPHIFY_MAX_WORKERS` | AST 并行线程数 | 可选，也可用 `--max-workers` flag |
| `GRAPHIFY_MAX_OUTPUT_TOKENS` | 为密集语料提高输出上限 | 可选，例如大型文件用 `32768` |
| `GRAPHIFY_API_TIMEOUT` | HTTP 超时时间，单位秒（默认 600） | 可选，也可用 `--api-timeout` flag |
| `GRAPHIFY_FORCE` | 即使节点更少也强制重建图谱 | 可选，也可用 `--force` flag |
| `GRAPHIFY_GOOGLE_WORKSPACE` | 自动启用 Google Workspace 导出 | 可选，设为 `1` |
| `GRAPHIFY_TRIAGE_BACKEND` | `graphify prs --triage` 使用的 backend | 可选，会从可用 key 自动检测 |
| `GRAPHIFY_TRIAGE_MODEL` | triage 的模型覆盖 | 可选，例如 `claude-opus-4-7` |

---

## 隐私

- **代码文件**：通过 tree-sitter 在本地处理，不会离开你的机器。
- **视频 / 音频**：使用 faster-whisper 在本地转写，不会离开你的机器。
- **文档、PDF、图片**：会发送给你的 AI 助手做语义提取（通过 `/graphify` skill，使用你的 IDE 会话当前运行的模型）。Headless `graphify extract` 需要 `GEMINI_API_KEY` / `GOOGLE_API_KEY`（Gemini）、`MOONSHOT_API_KEY`（Kimi）、`ANTHROPIC_API_KEY`（Claude）、`OPENAI_API_KEY`（OpenAI）、`DEEPSEEK_API_KEY`（DeepSeek）、运行中的 Ollama 实例（`OLLAMA_BASE_URL`）、通过标准 provider chain 提供的 AWS credentials（Bedrock，无需 API key，使用 IAM），或 `claude` CLI 二进制（Claude Code，无需 API key，使用你的 Claude 订阅）。`--dedup-llm` flag 使用同一套 key。
- 没有 telemetry，没有使用跟踪，也没有 analytics。

---

## 故障排查

**`pip install graphifyy` 后提示 `graphify: command not found`**

`pip` 会把脚本安装到用户 bin 目录，而这个目录可能不在 PATH 中。修复方式：

- macOS：把 `~/Library/Python/3.x/bin` 加到 `~/.zshrc` 的 PATH
- Linux：把 `~/.local/bin` 加到 `~/.bashrc` 的 PATH
- 或使用 `uv tool install graphifyy` / `pipx install graphifyy`，两者都会自动管理 PATH

**`python -m graphify` 能运行，但 `graphify` 命令不能运行**

你的 shell PATH 没包含 Python scripts 目录。用 `uv` 或 `pipx` 替代普通 `pip`。

**PowerShell 中 `/graphify .` 报 “path not recognized”**

PowerShell 会把开头的 `/` 当作路径分隔符。Windows 上使用 `graphify .`，不要加斜杠。

**`--update` 或重建后图谱节点变少**

如果重构删除了文件，旧节点可能仍然残留。传入 `--force`（或设置 `GRAPHIFY_FORCE=1`），即使重建后的节点更少也覆盖旧图谱。

**图谱里同一实体出现重复节点（ghost duplicates）**

这是语义提取和 AST 提取对 node ID 格式判断不一致导致的。运行一次完整重新提取来清理：

```bash
graphify extract . --force
```

**Ollama VRAM 不够 / 超过上下文窗口**

KV-cache 窗口会自动调整，但对你的 GPU 来说可能仍然太大。可以调小：

```bash
GRAPHIFY_OLLAMA_NUM_CTX=8192 graphify extract ./docs --backend ollama --token-budget 4000
```

**Graph HTML 太大，浏览器打不开（>5000 nodes）**

跳过 HTML 生成，直接使用 JSON：

```bash
graphify cluster-only ./my-project --no-viz
graphify query "..."
```

**两个开发者同时提交后 `graph.json` 出现 conflict markers**

运行 `graphify hook install`。它会设置 git merge driver，自动对 `graph.json` 做 union merge，避免冲突。

**文档或 PDF 提取返回空 nodes/edges**

文档和 PDF 需要 LLM 调用。检查 API key 是否已设置、backend 是否正确：

```bash
ANTHROPIC_API_KEY=sk-... graphify extract ./docs --backend claude
```

**IDE 中出现 skill version mismatch warning**

已安装的 graphify 版本和 skill 文件版本不一致。更新：

```bash
uv tool upgrade graphifyy
graphify install  # 覆盖 skill 文件
```

---

## 完整命令参考

```bash
/graphify                          # 在当前目录运行
/graphify ./raw                    # 在指定文件夹运行
/graphify ./raw --mode deep        # 更激进地提取关系
/graphify ./raw --update           # 只重新提取已变更文件
/graphify ./raw --directed         # 保留边方向
/graphify ./raw --cluster-only     # 在已有图谱上重新聚类
/graphify ./raw --no-viz           # 跳过 HTML 可视化
/graphify ./raw --obsidian         # 生成 Obsidian vault
/graphify ./raw --wiki             # 构建 agent 可抓取的 markdown wiki
/graphify ./raw --svg              # 导出 graph.svg
/graphify ./raw --graphml          # 导出给 Gephi / yEd
/graphify ./raw --neo4j            # 为 Neo4j 生成 cypher.txt
/graphify ./raw --neo4j-push bolt://localhost:7687
/graphify ./raw --watch            # 文件变更时自动同步
/graphify ./raw --mcp              # 启动 MCP stdio server

/graphify add https://arxiv.org/abs/1706.03762
/graphify add <video-url>
/graphify add https://... --author "Name" --contributor "Name"

/graphify query "what connects attention to the optimizer?"
/graphify query "..." --dfs --budget 1500
/graphify path "DigestAuth" "Response"
/graphify explain "SwinTransformer"

graphify uninstall                 # 一次性从所有平台移除
graphify uninstall --purge         # 同时删除 graphify-out/
graphify uninstall --project --platform codex  # 只移除 project-scoped install 文件

graphify hook install              # post-commit + post-checkout hooks
graphify hook uninstall
graphify hook status

graphify claude install / uninstall
graphify codex install / uninstall
graphify opencode install
graphify cursor install / uninstall
graphify gemini install / uninstall
graphify copilot install / uninstall
graphify aider install / uninstall
graphify claw install / uninstall
graphify droid install / uninstall
graphify trae install / uninstall
graphify trae-cn install / uninstall
graphify hermes install / uninstall
graphify kiro install / uninstall
graphify antigravity install / uninstall

graphify extract ./docs                        # CI 用 headless LLM 提取（不需要 IDE）
graphify extract ./docs --backend gemini       # 显式 backend：gemini、kimi、claude、openai、deepseek、ollama、bedrock 或 claude-cli
graphify extract ./docs --backend gemini --model gemini-3.1-pro-preview
graphify extract ./docs --backend ollama       # 本地 Ollama（设置 OLLAMA_BASE_URL / OLLAMA_MODEL），loopback 无需 API key
GRAPHIFY_OLLAMA_NUM_CTX=32768 graphify extract ./docs --backend ollama   # 覆盖 KV-cache 窗口（默认自动调整）
GRAPHIFY_OLLAMA_KEEP_ALIVE=0 graphify extract ./docs --backend ollama    # 每个 chunk 后卸载模型（小显存 GPU 省 VRAM）
graphify extract ./docs --backend bedrock      # AWS Bedrock via IAM，无需 API key，使用 AWS credential chain
graphify extract ./docs --backend claude-cli   # 通过 Claude Code CLI 路由，无需 API key，使用你的 Claude 订阅
graphify extract ./docs --max-workers 16       # AST 并行度（也可用 GRAPHIFY_MAX_WORKERS）
graphify extract ./docs --token-budget 30000   # 为本地/小模型使用更小语义 chunk
graphify extract ./docs --max-concurrency 2    # 更少并行 LLM 调用（适合本地推理）
graphify extract ./docs --api-timeout 900      # 为较慢本地模型设置更长 HTTP timeout（默认 600s）
graphify extract ./docs --google-workspace     # 提取前通过 gws 导出 .gdoc/.gsheet/.gslides
graphify extract ./docs --no-cluster           # 只做原始提取，跳过聚类
graphify extract ./docs --force                # 即使新图节点更少也覆盖 graph.json（重构后或清理 ghost duplicates 时使用）
graphify extract ./docs --dedup-llm            # 用 LLM 处理有歧义的实体对（使用同一 API key）
graphify extract ./docs --global --as myrepo   # 提取并注册到跨项目 global graph
GRAPHIFY_MAX_OUTPUT_TOKENS=32768 graphify extract ./docs --backend claude  # 为密集语料提高输出上限

graphify export callflow-html                       # graphify-out/<project>-callflow.html
graphify export callflow-html --max-sections 8      # 限制生成的架构章节数
graphify export callflow-html --output docs/arch.html
graphify export callflow-html ./some-repo/graphify-out

graphify global add graphify-out/graph.json myrepo   # 将项目图谱注册到 ~/.graphify/global.json
graphify global remove myrepo                         # 从 global graph 移除一个项目
graphify global list                                  # 显示所有已注册 repo 及 node/edge 数量
graphify global path                                  # 打印 global graph 文件路径

graphify prs                              # PR dashboard：CI、review、worktree、graph impact
graphify prs 42                           # 深入查看 PR #42
graphify prs --triage                     # AI triage 排序（从 env 自动检测 backend）
graphify prs --worktrees                  # worktree → branch → PR 映射
graphify prs --conflicts                  # 共享 graph communities 的 PR（合并顺序风险）
graphify prs --base main                  # 过滤目标 base branch
graphify prs --repo owner/repo            # 针对另一个 GitHub repo 运行
GRAPHIFY_TRIAGE_BACKEND=kimi graphify prs --triage   # 为 triage 指定 backend

graphify clone https://github.com/karpathy/nanoGPT
graphify merge-graphs a.json b.json --out merged.json
graphify --version                                    # 打印已安装版本
graphify watch ./src
graphify check-update ./src
graphify update ./src
graphify update ./src --no-cluster  # 跳过重新聚类，只写入原始 AST 图
graphify update ./src --force       # 即使新图节点更少也覆盖
graphify cluster-only ./my-project
graphify cluster-only ./my-project --graph path/to/graph.json  # 自定义图谱位置
graphify cluster-only ./my-project --resolution 1.5            # 更多、更小的 community
graphify cluster-only ./my-project --exclude-hubs 99           # 从 partitioning 中排除 p99 degree 节点
```

---

## 了解更多

- [How it works](../../docs/how-it-works.md)：提取流水线、社区发现、置信度评分、基准测试
- [ARCHITECTURE.md](../../ARCHITECTURE.md)：模块拆分，以及如何添加一种语言
- [Optional integrations](../../docs/docker-mcp-sqlite.md)：Docker MCP Toolkit + SQLite

---

## 基于 graphify 构建：Penpax

[**Penpax**](https://graphifylabs.ai) 是构建在 graphify 之上的 always-on 层。它把同样的图谱方法应用到你的整个工作生活：会议、浏览器历史、邮件、文件和代码，并在后台持续更新。

它面向那些工作分散在数百段对话和文档里、事后很难完整复原的人。无云端，完全在设备本地运行。

**免费试用即将上线。** [加入 waitlist →](https://graphifylabs.ai)

---

<details>
<summary>贡献</summary>

### 开发环境设置

克隆 repo 并以 editable mode 安装：

```bash
git clone https://github.com/safishamsi/graphify.git
cd graphify
git checkout v8                        # active development branch

# 创建虚拟环境（需要 Python 3.10+）:
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate

# 以 editable mode 安装，并包含所有 optional extras:
pip install -e ".[all]"
```

验证 editable install：

```bash
graphify --version
python -c "import graphify; print(graphify.__file__)"
```

### 运行测试

```bash
pip install pytest
pytest tests/ -q                       # 跑完整测试套件
pytest tests/test_extract.py -q        # 跑单个模块
pytest tests/ -q -k "python"           # 按名称过滤
```

> macOS 提示：测试套件同时包含 `sample.f90` 和 `sample.F90` fixture。它们在大小写不敏感的 HFS+ / APFS 文件系统上会冲突。如果需要同时测试两个 Fortran 变体，请在 Linux 或 Docker 容器中运行。

### Git 工作流

- active development 在 `v8` branch 上进行。
- Commit 风格：`fix: <description>` / `feat: <description>` / `docs: <description>`。
- 打开 PR 前运行 `pytest tests/ -q` 并确认通过。
- 对任何新的语言 extractor，请在 `tests/fixtures/` 添加 fixture 文件，并在 `tests/test_languages.py` 添加测试。

### 可以贡献什么

**Worked examples** 是最有用的贡献。对真实语料运行 `/graphify`，把输出保存到 `worked/{slug}/`，写一份诚实的 `review.md` 说明图谱哪些地方做对了、哪些地方做错了，然后打开 PR。

**Extraction bugs**：提交 issue 时请附上输入文件、对应 cache entry（`graphify-out/cache/`）以及漏掉了什么或错在哪里。

模块职责和如何添加语言见 [ARCHITECTURE.md](../../ARCHITECTURE.md)。

</details>
