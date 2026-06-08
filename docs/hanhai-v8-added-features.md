# Hanhai-v8 Added Features

本文档总结 `hanhai-v8` 分支相对官方原生 `upstream/v8` 新增的能力、为什么要做这些能力，以及使用方式。

当前分支的核心目标是把 Graphify 从“通用代码仓库知识图谱”扩展为“能处理大型游戏配置、表格语料和多业务域图谱的构建工具”。重点不是替换官方目录扫描模式，而是在保留原生能力的基础上，为大规模结构化数据增加更可控的抽取、构建和查询路径。

## 兼容性处理原则

这些能力拓展目前按以下原则处理：

- 原生目录模式必须保持可用：`graphify update .`、`graphify extract <path> --out DIR`、`GRAPHIFY_OUT` 和原生 `graphify-out/manifest.json` 的语义不能被 manifest/sidecar 模式覆盖。
- 新增的 manifest、sidecar、staging-merge、build-trace 和大图查询上限属于显式 opt-in 或 wrapper-facing 能力，优先通过 CLI 帮助、文档和测试锁定边界。
- 文件类型扩展会改变默认扫描结果，不能伪装成“完全无行为变化”。`.tab`、`.tsv`、`.ini`、`.lh` 和 TSV-like `.txt` 会进入结构化 code path；不想纳入图谱的仓库应使用 `.graphifyignore` 排除。
- 暂不为 TSV-like `.txt` 增加全局关闭开关。当前启发式足够保守，且这是 Hanhai/JX3 语料的核心需求；如果未来上游化或遇到真实误伤，再考虑加 manifest-level 或 env-level opt-out。

## 1. 通用表格文件抽取

### 为什么做

官方原生 Graphify 主要面向代码和文档。对于游戏工程里的 `.tab`、`.tsv` 以及 TSV-like `.txt` 配置表，如果把它们当普通文本处理，容易出现两个问题：

- 大量表格内容会进入语义/LLM 路径，成本高、速度慢，而且结果不可控。
- 表格内部的“列、行、配置值、路径引用”是确定性结构，用 LLM 推断反而不如本地解析稳定。

所以 `hanhai-v8` 增加了确定性的表格抽取器，让这些文件直接生成图节点和边。

### 新增能力

- 支持 `.tab`、`.tsv`。
- 支持强 TSV 结构的 `.txt`，普通文本不会误入表格路径。
- 生成文件节点、列节点、行节点、低基数值节点。
- 识别单元格中的路径引用，并生成 `references` 边。
- 对行数、列数、路径引用数和值节点数有上限保护，避免图无限膨胀。

实现入口：

- `graphify/extract.py::extract_tab`
- `graphify/tabular.py::looks_like_tabular_text`
- `graphify/detect.py::classify_file`

### 使用方式

目录模式不需要额外参数：

```bash
graphify update .
graphify extract /path/to/repo --out /tmp/out
```

查询时按普通图谱查询即可：

```bash
graphify query "哪些配置表引用了 StandardAI.lua"
```

## 2. INI 结构化抽取和 Lua/INI 引用补边

### 为什么做

游戏 UI、资源和行为配置经常分散在 `.ini` 与 Lua 代码里。官方原生 Graphify 没有理解这类配置文件的 section、父子关系和资源引用，导致图里看不到“Lua 代码使用了哪个 UI 配置”“某个 INI section 挂在哪个父节点下”等关系。

因此新增 `.ini` 抽取和 Lua 到 INI 的引用补边，让 UI 配置和脚本代码能出现在同一张知识图谱里。

### 新增能力

- `.ini` 文件作为 code-like 结构文件进入 AST-only 抽取路径。
- 识别 section 节点。
- 识别 UI 父子关系，例如 `Parent`、`ParentWnd`、`ParentWindow`。
- 识别窗口/控件类型，例如 `WndType`、`ControlType`、`Class`、`Type`。
- 保留几何属性，例如 `Left`、`Top`、`Width`、`Height` 等。
- 识别 `.lua`、`.ini`、`.xml`、`.json`、图片、音频等资产路径引用。
- `.lh` 被纳入 Lua family，和 `.lua`、`.luau`、`.toc` 一起参与 Include 和 INI 引用补边。

实现入口：

- `graphify/extract.py::extract_ini`
- `graphify/extract.py::_add_lua_include_edges`
- `graphify/extract.py::_add_lua_ini_reference_edges`

### 使用方式

目录模式直接扫描：

```bash
graphify update .
```

查询示例：

```bash
graphify query "哪些 Lua 文件引用了这个 INI section"
graphify query "这个 UI 控件的父级和资源引用是什么"
```

## 3. Manifest Domain Build

### 为什么做

官方原生 Graphify 默认扫描整个目录并写入统一的 `graphify-out/`。在大型仓库或游戏工程里，全量图经常太大，且业务方通常更关心某个“域”：例如技能、Buff、UI、资源索引等。

Manifest Domain Build 的目标是让上层调用方显式声明“这个 domain 由哪些文件组成”，Graphify 只构建这个文件集合，并把输出直接写到该 domain 的目录下。这样可以减少图规模、降低更新成本，也方便多个 domain 分开构建和消费。

### 新增能力

- `graphify extract --manifest FILE --output-dir DIR`
- `graphify update --manifest FILE --output-dir DIR`
- manifest 是 caller-owned 文件，不允许误用原生 `graphify-out/manifest.json`。
- `--manifest` 与位置参数互斥。
- `--output-dir` 在 manifest 模式必填，输出 `DIR/graph.json`、`DIR/GRAPH_REPORT.md` 和 `DIR/.graphify_state/update-state.json`。
- 非 strict 模式下会跳过不支持或被策略过滤的文件，并在报告里记录。
- `--strict-manifest` 可以切换为遇到第一个无效文件即失败。

实现入口：

- `graphify/__main__.py::_maybe_run_manifest_code_build`
- `graphify/manifest.py::load_domain_manifest`
- `graphify/code_build_runner.py::build_code_graph`

### Manifest 示例

```json
{
  "repo_root": ".",
  "domain_id": "graphify-out/domains/skill",
  "files": [
    { "path": "src/skill.lua" },
    { "path": "settings/ui.ini" },
    { "path": "settings/skills.tab" }
  ]
}
```

### 使用方式

```bash
graphify extract --manifest graphify-out/domains/skill/domain-files.json \
  --output-dir graphify-out/domains/skill

graphify update --manifest graphify-out/domains/skill/domain-files.json \
  --output-dir graphify-out/domains/skill
```

可选参数：

```bash
graphify update --manifest graphify-out/domains/skill/domain-files.json \
  --output-dir graphify-out/domains/skill \
  --max-workers 8 \
  --strict-manifest
```

## 4. Tabular Sidecar SQLite

### 为什么做

普通图谱不适合承载大量表格行和单元格。对于几千行、几十列甚至更大的配置表，如果把完整内容全部写进 `graph.json`，会导致：

- `graph.json` 体积急剧膨胀。
- 图查询、渲染、聚类和报告生成变慢。
- 重复构建和增量更新成本变高。
- 业务查询真正需要的是“按键查完整行”，而不是把每个 cell 都变成图节点。

Tabular Sidecar 的设计是：图里只保留可导航骨架，完整行数据放到 SQLite，需要时再按 `sidecar_ref` 或索引列回查。

### 新增能力

Manifest 中的表格文件可以声明：

- `tabular_policy: "graph"`：沿用普通表格图抽取。
- `tabular_policy: "sidecar"`：完整行写入 SQLite，图里只保留 skeleton。
- `tabular_policy: "auto"`：默认策略；当表格超过阈值时自动进入 sidecar。

`auto` 进入 sidecar 的条件：

- 行数大于 2000。
- 列数大于 80。
- 文件大小大于 1 MB。

Sidecar 文件默认写入：

```text
GRAPHIFY_OUT/sidecar/tabular.sqlite
```

也可以通过 `--sidecar-db PATH` 指定。

实现入口：

- `graphify/tabular_manifest.py::load_tabular_domain_manifest`
- `graphify/tabular_sidecar.py::update_sidecar`
- `graphify/tabular_graph.py::merge_sidecar_projection`
- `graphify/tabular_sidecar_paths.py::plan_sidecar_paths`

### Manifest 示例

```json
{
  "repo_root": ".",
  "domain_id": "graphify-out/domains/skill",
  "files": [
    {
      "path": "settings/skills.tab",
      "tabular_policy": "sidecar",
      "primary_key": "SkillID",
      "indexed_columns": ["SkillID", "Name"],
      "anchor_columns": ["SkillID"]
    }
  ]
}
```

如果列名重复，可以用 1-based `column_index` 消除歧义：

```json
{
  "path": "settings/skills.tab",
  "tabular_policy": "sidecar",
  "primary_key": { "name": "ID", "column_index": 1 },
  "indexed_columns": [
    { "name": "ID", "column_index": 1 },
    { "name": "Name", "column_index": 2 }
  ]
}
```

### 使用方式

```bash
graphify update --manifest graphify-out/domains/skill/domain-files.json \
  --output-dir graphify-out/domains/skill \
  --sidecar-db graphify-out/sidecar/tabular.sqlite
```

构建完成后，domain graph 里会有 `tabular_sidecar` 元数据和 `sidecar_ref` 指针。完整行内容通过 sidecar CLI 读取。

## 5. Sidecar 查询 CLI

### 为什么做

Sidecar 把完整表格行从 `graph.json` 移到了 SQLite。这样图会更小，但也需要一个稳定方式把完整行查回来，尤其是 Agent 或业务 wrapper 看到 `sidecar_ref` 后，需要能解析它对应的原始行。

所以新增了 `graphify sidecar` CLI，作为 sidecar 的稳定读取入口。

### 新增能力

- `search`：按索引列和值查行。
- `resolve`：按 `sidecar://tabular/...` 引用解析完整行。
- `query`：受限只读 SQL，用于调试、评估和分析。
- 自动从 `--graph` 的 `tabular_sidecar` 元数据推断 DB 路径、repo identity 和 domain scope。
- 校验 graph 与 sidecar DB 的 repo、schema、domain config hash、source sha 是否匹配。
- `query` 只允许 `SELECT` 或 `WITH`，只能访问受控 view，不能直接读物理表。

实现入口：

- `graphify/sidecar_cli.py`
- `graphify/tabular_sidecar.py::search_rows`
- `graphify/tabular_sidecar.py::resolve_ref`
- `graphify/tabular_sidecar.py::execute_readonly_query`

### 使用方式

按索引列查行：

```bash
graphify sidecar search \
  --graph graphify-out/domains/skill/graph.json \
  --column SkillID \
  --value 100
```

解析图里的 `sidecar_ref`：

```bash
graphify sidecar resolve \
  --graph graphify-out/domains/skill/graph.json \
  sidecar://tabular/<repo>/<file>/<row>/<line_hash>
```

运行受限 SQL：

```bash
graphify sidecar query \
  --graph graphify-out/domains/skill/graph.json \
  --sql "SELECT source_file, row_no, sidecar_ref FROM current_domain_rows LIMIT 5"
```

可用的查询 view 包括：

- `current_domain_sources`
- `current_domain_rows`
- `current_domain_indexed_values`
- `current_domain_refs`

## 6. 并行安全的 Sidecar Staging Merge

### 为什么做

当上层 wrapper 同时构建多个 domain 时，多个 Graphify 子进程会同时写共享 sidecar SQLite。直接并发写同一个 DB 容易变成性能瓶颈，也会增加锁冲突和失败风险。

Staging Merge 的目标是把长耗时写入从共享 DB 中移走：每个进程先写自己的 staging DB，然后只在最后合并时短暂持有 canonical sidecar 的写锁。

### 新增能力

- 默认在支持 `fcntl` 文件锁的平台使用 `staging-merge`。
- 每个 domain build 有独立 `run_id` 和 `attempt_id`。
- 每次构建先写 staging DB。
- 合并阶段短暂锁定 canonical sidecar。
- 合并后清理 staging attempt。
- 不支持文件锁的平台退化为 `shared-serial`。
- `graphify capabilities --json` 暴露当前平台是否支持并行安全 sidecar。

实现入口：

- `graphify/code_build_runner.py::build_code_graph`
- `graphify/tabular_sidecar_staging.py`
- `graphify/tabular_sidecar.py::SidecarWriteLock`
- `graphify/capabilities.py::capabilities_payload`

### 使用方式

Graphify 内部自动选择写模式。上层 wrapper 在启用多 domain 并发前应先探测能力：

```bash
graphify capabilities --json
```

重点字段：

```json
{
  "tabular_sidecar": {
    "write_modes": ["shared-serial", "staging-merge"],
    "default_write_mode": "staging-merge",
    "supports_domain_staging_merge": true,
    "supports_build_trace": true,
    "parallel_safe": true
  }
}
```

如果 `parallel_safe` 为 `true`，上层 wrapper 可以并发调度多个 manifest domain build，例如：

```bash
graphify update --manifest graphify-out/domains/skill/domain-files.json \
  --output-dir graphify-out/domains/skill

graphify update --manifest graphify-out/domains/buff/domain-files.json \
  --output-dir graphify-out/domains/buff
```

多进程调度由 wrapper 负责，Graphify 负责 sidecar 写入的安全性。

## 7. Build Trace

### 为什么做

Sidecar 和 manifest domain build 引入了更多阶段：代码抽取、sidecar staging 写入、canonical merge、sidecar projection、图构建、聚类、报告生成。只看最终耗时很难判断瓶颈在哪里。

Build Trace 用来给 wrapper、性能调优和回归排查提供机器可读证据。

### 新增能力

成功构建写入：

```text
<output-dir>/.graphify_state/build-trace.json
```

失败构建写入：

```text
<output-dir>/.graphify_state/build-trace.failed.json
```

记录内容包括：

- `domain_id`
- `sidecar_mode`
- `process_workers`
- `code_extract_ms`
- `sidecar_stage_write_ms`
- `sidecar_merge_write_ms`
- `sidecar_projection_ms`
- `graph_build_ms`
- `cluster_ms`
- `report_export_ms`
- staged/merged/pruned files and rows
- merged indexed values and refs

实现入口：

- `graphify/build_trace.py::BuildTrace`
- `graphify/code_build_runner.py::build_code_graph`

### 使用方式

正常运行 manifest build 后直接查看：

```bash
cat graphify-out/domains/skill/.graphify_state/build-trace.json
```

如果构建失败：

```bash
cat graphify-out/domains/skill/.graphify_state/build-trace.failed.json
```

## 8. 大图查询上限显式配置

### 为什么做

Graphify 对 `graph.json` 有大小上限保护，避免误读超大文件导致内存风险。但 sidecar/domain 场景下，有些图确实会比默认上限更大。此时应该由调用方显式声明“我知道这个图很大，但仍要查询”，而不是关闭安全检查。

### 新增能力

- `graphify query` 新增 `--max-graph-bytes`。
- 支持 `k`、`m`、`g` 后缀，例如 `512m`、`2g`。
- 支持环境变量 `GRAPHIFY_MAX_GRAPH_FILE_BYTES`。

实现入口：

- `graphify/__main__.py` 的 `query` CLI 参数解析。
- `graphify/security.py::parse_graph_file_size_cap`
- `graphify/security.py::check_graph_file_size_cap`

### 使用方式

单次查询放宽：

```bash
graphify query "这个 domain 的核心节点是什么" \
  --graph graphify-out/graph.json \
  --max-graph-bytes 2g
```

环境变量方式：

```bash
GRAPHIFY_MAX_GRAPH_FILE_BYTES=2g graphify query "这个 domain 的核心节点是什么"
```

## 9. 辅助维护能力

### 为什么做

这条分支长期跟随官方 `v8`，并且需要支持本地 JX3/大语料工作流。除了核心功能外，也补了一些维护性能力，保证新增构建模式不会破坏官方原生体验。

### 新增能力

- 当前中文 README 快照：`docs/translations/README.zh-CN-current.md`。
- 本地 IDE、macOS 文件和本地 worktree 忽略规则。
- `collect_files()` 修复：避免 nested worktrees 泄漏进扫描。
- manifest 非 strict 模式下跳过策略过滤文件并报告。
- 缺失文件不会被 `.graphifyignore` 或过滤策略掩盖。
- sidecar merge file lookup 增加索引，减少大 DB 合并热点。

## 兼容性与迁移说明

### 原生目录模式

原生目录模式继续使用原来的入口：

```bash
graphify update .
graphify extract <path> --out <dir>
```

兼容性约束：

- `graphify update .` 仍写入当前目录下的 `graphify-out/graph.json` 和 `graphify-out/manifest.json`。
- 设置 `GRAPHIFY_OUT=custom-graphify-out` 时，原生 graph 和 manifest 仍写入该目录。
- `graphify extract <path> --out DIR` 仍写入 `DIR/graphify-out/`，不会直接写 `DIR/graph.json`。
- 原生 `graphify-out/manifest.json` 是增量状态文件，不会被当成 caller-owned domain manifest 使用。

需要注意的默认行为变化：

- `.tab`、`.tsv`、`.ini`、`.lh` 会被当作 code-like 结构文件扫描。
- 强 TSV 结构的 `.txt` 会从 document/semantic path 切到结构化 tabular path。
- 这会改变图节点数、边数量、watch 重建行为和部分文件的语义抽取路径。

如果某个仓库不希望这些配置文件进入图谱，推荐使用 `.graphifyignore`：

```gitignore
*.tab
*.tsv
*.ini
generated-config/
```

### TSV-like `.txt`

TSV-like `.txt` 的路由是内容启发式，不是单纯按扩展名判断。普通 `.txt` 仍走 document path；只有满足稳定 tabular 结构的 `.txt` 才进入 tabular path。

当前处理：

- 不增加全局关闭开关。
- 用测试覆盖 plain `.txt`、paper-like `.txt` 和 TSV-like `.txt` 的分类边界。
- 对真正需要结构化抽取的大量 `.txt` 表格，默认进入 tabular path。

迁移建议：

- 如果 `.txt` 是文档或论文素材，保持自然文本结构即可。
- 如果某些 `.txt` 长得像 TSV 但不想入图，用 `.graphifyignore` 排除。
- 如果未来需要更细控制，再增加 manifest-level 字段，例如 `tabular_policy: "ignore"` 或 `text_policy: "document"`。

### Manifest mode

Manifest mode 是新的 domain build 模式，不是原生 full extraction 的完整替代。

支持：

```bash
graphify extract --manifest FILE --output-dir DIR
graphify update --manifest FILE --output-dir DIR
```

不支持与 manifest 混用的原生命令参数包括：

- `--backend`
- `--model`
- `--token-budget`
- `--max-concurrency`
- `--api-timeout`
- `--out`
- `--no-cluster`
- `--cache-root`

当前处理：

- CLI 会直接拒绝这些组合，避免静默生成错误输出。
- `--manifest` 与位置参数互斥。
- `--output-dir` 只属于 manifest mode；目录模式继续使用 `--out`。
- manifest 默认是 code/domain build，非 strict 模式会跳过 document、paper、image、unsupported 文件，并在报告里记录。

迁移建议：

- 想跑官方原生全量提取，继续用目录模式。
- 想构建业务域子图，使用 manifest mode。
- 想让大表走 sidecar，在 manifest 的文件条目里声明 `tabular_policy`。

### Tabular sidecar

Sidecar 只在 manifest 中存在 effective `tabular_policy: "sidecar"` 的表格文件时启用。普通目录模式不会创建或更新 `GRAPHIFY_OUT/sidecar/tabular.sqlite`。

当前处理：

- `GRAPHIFY_OUT/sidecar/tabular.sqlite` 是可重建的生成物。
- graph 只保留 skeleton 节点和 `sidecar_ref`。
- 稳定读取接口是 `graphify sidecar search` 和 `graphify sidecar resolve`。
- `graphify sidecar query` 是受限只读 SQL，定位为 debug/eval surface。

迁移建议：

- 小表可以继续用 `tabular_policy: "graph"` 或 `auto`。
- 大表应显式使用 `tabular_policy: "sidecar"`，并声明 `primary_key`、`indexed_columns`、`anchor_columns`。
- 上层 wrapper 不应直接依赖 SQLite 物理表结构；应优先调用 sidecar CLI 或稳定 API。

### Watch mode

Watch mode 会跟随新的文件分类逻辑：

- TSV-like `.txt` 改动会触发 code rebuild。
- plain `.txt` 改动会触发 semantic update notify。
- 删除 `.txt` 时会保守触发 code rebuild 和 semantic notify，确保旧 tabular 节点能被清理。

迁移建议：

- 如果 watch 过程中发现配置表导致重建过重，用 `.graphifyignore` 排除不需要入图的目录。
- 对大量 domain 并发同步，不建议依赖 watch；应由 wrapper 显式调度 manifest build。

### 大图查询上限

`--max-graph-bytes` 和 `GRAPHIFY_MAX_GRAPH_FILE_BYTES` 只影响显式查询时的 graph file size cap。

当前处理：

- 默认 512 MiB 上限不变。
- 放宽上限必须由调用方显式传参或设置环境变量。

迁移建议：

- 普通用户不要全局设置过大的 `GRAPHIFY_MAX_GRAPH_FILE_BYTES`。
- 只在明确知道图文件可信且确实较大时使用：

```bash
graphify query "问题" --graph graphify-out/graph.json --max-graph-bytes 2g
```

### 并行 sidecar 写入

`staging-merge` 只改变 sidecar 写入实现，不改变原生目录模式。

当前处理：

- 上层 wrapper 先用 `graphify capabilities --json` 探测能力。
- `parallel_safe: true` 时可以并发调度多个 manifest domain build。
- 不支持文件锁的平台会退化为 `shared-serial`。

迁移建议：

- wrapper 不应默认假设所有平台都支持并发 sidecar 写入。
- 并发前必须检查 `tabular_sidecar.parallel_safe` 或 `supports_domain_staging_merge`。

## 推荐使用组合

### 小型普通代码仓库

继续使用官方原生方式：

```bash
graphify update .
graphify query "这个仓库的核心模块是什么"
```

### 包含少量 `.tab`、`.ini`、Lua 配置的仓库

直接目录模式即可：

```bash
graphify update .
graphify query "哪些 Lua 文件引用了 UI 配置"
```

### 大型游戏配置仓库

建议使用 manifest domain build + sidecar：

```bash
graphify update --manifest graphify-out/domains/skill/domain-files.json \
  --output-dir graphify-out/domains/skill \
  --sidecar-db graphify-out/sidecar/tabular.sqlite
```

然后按需查完整表格行：

```bash
graphify sidecar search \
  --graph graphify-out/domains/skill/graph.json \
  --column SkillID \
  --value 100
```

### 多 domain 并发构建

先探测能力：

```bash
graphify capabilities --json
```

如果 `tabular_sidecar.parallel_safe` 为 `true`，wrapper 可以并发调度多个 domain build。Graphify 会通过 staging-merge 保护共享 sidecar 写入。
