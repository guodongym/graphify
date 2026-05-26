# Graphify Manifest Domain Build 设计

## 背景

JX3 需要稳定、较小的 domain graph，而不是一个巨大的全仓图。JX3 wrapper 可以生成确定性的 domain file-list manifest，但不能拼装 Graphify 内部模块，也不能自己写 `graph.json`。因此 Graphify 需要以扩展模式支持 manifest-driven build/update：调用方显式提供文件列表和精确输出目录，Graphify 继续复用原生 cache、抽取、构图、聚类和查询能力。

这份设计是以下 JX3 方案的 Graphify 侧 Phase 0 前置任务：

- `/Users/zhaoguodong/Codes/jx3/docs/superpowers/specs/2026-05-25-jx3-graphify-change-impact-design.md`
- `/Users/zhaoguodong/Codes/jx3/docs/superpowers/plans/2026-05-25-jx3-graphify-change-impact.md`

JX3 wrapper 继续实现前，必须先在 Graphify 仓库完成并稳定 Phase 0。

## 当前状态

2026-05-26 本地探测结果：

- `graphify version` 返回 `0.8.18+hanhai.0.1`。
- `graphify query/path/explain/affected` 已支持 `--graph <path>`。
- `graphify extract <path> --out DIR` 支持输入路径和输出根目录，但输出会写到 `<DIR>/graphify-out/`。
- `graphify update <path>` 支持扫描目录，也可以通过 `GRAPHIFY_OUT` 重定向输出，但它仍然是目录扫描模式。
- 当前没有已暴露的 CLI/API 同时支持 `--manifest` 和精确 `--output-dir` 来完成 domain build/update。
- 结构化 `.tab/.ini`、TSV-like `.txt`、Lua header `.lh`、Lua/LH `Include(...)` 静态依赖抽取已经存在，应该复用。
- source decoding 还不完整：部分结构化文本路径支持 UTF-8/GB18030，但 generic AST 文本抽取仍有 UTF-8 replacement 路径，结构化解码也不覆盖 UTF-16LE。

## 目标

- 为 `extract` 和 `update` 增加 manifest/file-list input。
- 让调用方把输出直接写到精确 domain 目录。
- manifest mode 复用 Graphify 原生 cache 机制，不新增调用方 cache 管理面。
- manifest mode 必须生成 `graph.json` 和 `GRAPH_REPORT.md`，两者都直接写入精确 domain 输出目录。
- 保持生成的 domain `graph.json` 兼容 `query/path/explain --graph`。
- 同一个 manifest 支持 full build 和 update，语义一致。
- 为 JX3 相关的结构化文件和 Lua/LH 抽取路径集中 source decoding。
- manifest path、output path、repo root 或 source file path 无效时给出明确错误。

## 非目标

- 不向 Graphify 添加 JX3 专用路由、门派、技能或 eval-set 逻辑。
- 不让 Graphify 把 JX3 控制面文件，例如 `jx3-anchor-graph.json`，当作语料输入。
- 不改变现有目录模式 `graphify extract <path>` 和 `graphify update <path>` 的外部行为，除非是共享内部实现所需且保持兼容。
- 不暴露 `--cache-root` 或其他 cache 管理参数；调用方不拥有、不解释 Graphify cache 内部结构。
- Phase 0 manifest mode 不引入 LLM semantic document/paper/image 更新路径；原生目录模式的 semantic extraction 保持不变。manifest mode 只接受 Graphify 当前分类为 `code` 且可通过 AST/code extractor 直接抽取的文件。
- Phase 0 不产品化复杂配置格式，只提供最小 manifest 契约。

## Manifest 契约

Graphify 接收一个调用方生成的 JSON file-list manifest。为避免和 Graphify 原生 `graphify-out/manifest.json` 内部增量状态文件混淆，JX3 wrapper 应使用 `domain-files.json` 这类输入文件名，不使用 `manifest.json` 作为 domain file-list 文件名。

```json
{
  "schema_version": 1,
  "repo_root": ".",
  "files": [
    {"path": "client/settings/skill/skills.tab"}
  ]
}
```

规则：

- `files[].path` 是 manifest-driven extraction 的 source of truth。
- `repo_root` 是绝对路径时直接使用；是相对路径时按 CLI 当前工作目录解析。JX3 wrapper 应从仓库根目录调用 Graphify，或在 manifest 中写入绝对 `repo_root`。
- `files[].path` 按解析后的 `repo_root` 解析。
- `repo_root` 是 manifest mode 的扫描边界；manifest mode 不再调用目录发现来扩展文件集合。
- Graphify 按解析后的 `repo_root` 相对路径对 manifest 文件列表去重；重复写法不会重复进入抽取、输出 state 或报告统计。
- 空 `files` 列表，或去重后没有任何可接受 AST/code 文件，必须在 build 前明确报错。
- manifest 可以包含调用方自有的额外字段；Graphify 忽略未知字段。
- Graphify 必须校验每个文件路径都位于 `repo_root` 下。
- 文件缺失、已删除或不支持时必须明确报错，不能静默回退到扫描父目录。
- Graphify 必须用原生文件分类和 AST/code extractor 能力校验 manifest 文件。Phase 0 只接受分类为 `code` 且已有 AST/code extractor 的文件；`document`、`paper`、`image`、`video`、unknown 或当前无 extractor 的文件必须在写输出前报错，并提示改用原生 directory `extract` 或后续 Phase。
- Graphify 内部传递给 extractor、graph payload 和 update state 的 source path 必须归一化为 `repo_root` 相对路径；允许 manifest 输入绝对路径或相对路径，但不能让两种写法在 `source_file`、cache key 或 prune 逻辑里形成不同身份。
- manifest 文件由调用方拥有；Graphify 只能读取，不能修改或覆盖该文件。
- manifest 文件本身不能出现在 `files[]` 中；caller-owned 输入清单不是 domain source 文件。
- Graphify 原生 `graphify-out/manifest.json` 继续只表示目录模式内部增量状态；manifest mode 不能改变它的语义或写入路径。
- 如果 `--manifest` 解析后指向 `<repo_root>/graphify-out/manifest.json`，Graphify 必须拒绝执行并给出明确错误，不能把原生增量状态文件当成 domain file-list input。

## CLI 契约

Phase 0 暴露：

```bash
graphify extract \
  --manifest graphify-out/domains/skill-core/domain-files.json \
  --output-dir graphify-out/domains/skill-core

graphify update \
  --manifest graphify-out/domains/skill-core/domain-files.json \
  --output-dir graphify-out/domains/skill-core
```

行为：

- `--manifest` 将 Graphify 从目录发现切换到 manifest file-list input。
- `--output-dir` 是写入 `graph.json`、`GRAPH_REPORT.md` 和 Graphify sidecars 的精确目录。
- `--output-dir` 是绝对路径时直接使用；是相对路径时按 CLI 当前工作目录解析。
- `--output-dir` 不受 `GRAPHIFY_OUT` 影响；`GRAPHIFY_OUT` 只继续影响 Graphify 原生输出基目录相关能力，例如原生 cache 位置。
- `--manifest` 存在时 `--output-dir` 必填；缺失必须明确报错，不能默认写入 `GRAPHIFY_OUT` 或当前目录。
- `--output-dir` 只用于 manifest mode；未传 `--manifest` 时传入 `--output-dir` 必须明确报错并提示目录模式继续使用 `--out`，避免改变 `graphify extract <path> --out DIR` 写入 `<DIR>/graphify-out/` 的原生语义。
- manifest mode 不暴露 `--cache-root`；cache 继续按 Graphify 原生 active `GRAPHIFY_OUT` 规则使用共享 cache。默认是 `repo_root/graphify-out/cache`；如果环境中设置了 `GRAPHIFY_OUT`，则按现有原生 cache 解析规则使用对应 cache。
- manifest mode 下未识别的 `--xxx` 或 `-x` 选项必须明确拒绝，不能静默忽略。
- `--manifest` 和 positional `<path>` 互斥；`graphify extract <path> --manifest ...` 或 `graphify update <path> --manifest ...` 必须明确报错，不能同时存在两个 source of truth。
- manifest-driven `extract` 对 manifest 文件列表执行 full AST/code build，并在成功后初始化 `<output-dir>/.graphify_state/update-state.json`。manifest 文件全为 code-like 文件时，不需要 LLM backend API key。
- manifest-driven `update` 只更新 manifest 文件列表，不扫描 `repo_root` 或仓库根目录；如果 `<output-dir>/.graphify_state/update-state.json` 不存在，必须退化为 manifest full rebuild 并初始化 state，不能读取原生 `graphify-out/manifest.json`。
- manifest mode 若支持 `--no-cluster`，也必须写入 `GRAPH_REPORT.md` 并在报告中明确 raw/no-cluster 状态；如果暂不支持该组合，必须明确拒绝，不能静默少写报告。
- 现有 query 命令继续使用 `--graph <path>`。

未传 `--manifest` 时，`extract <path>`、`extract <path> --out DIR`、`update <path>`、`GRAPHIFY_OUT` 和现有 cache 布局保持原生行为。

## 输出和 Cache 归属

Graphify 拥有：

- `graph.json`
- `GRAPH_REPORT.md`
- graph visualization/report sidecars，如果启用
- active Graphify 原生输出基目录下的 cache entries，默认是 repo root 下 `graphify-out/cache`

调用方拥有：

- manifest 生成
- 选择传入哪个 manifest
- 选择输出目录
- 删除或归档旧 domain 目录

manifest mode 下，Graphify 不能写 `<output-dir>/graphify-out/`。它必须直接写入 `<output-dir>`。

manifest-driven update 必须维护 Graphify-owned domain state。该状态不能写回或覆盖调用方 manifest，只能使用 `<output-dir>/.graphify_state/update-state.json` 这类 Graphify-owned sidecar，不能读取或写入原生 `graphify-out/manifest.json`。该状态必须至少能记录上一轮 manifest 文件集合，且文件身份必须使用 `repo_root` 相对路径，使 `update --manifest` 能删除已从 manifest 移除但仍存在于 repo 的旧 source_file 节点和边。state schema 中 `files` 与 `current_files` 表达相同当前文件集合，`files_by_type` 与 `current_files_by_type` 表达相同当前类型分组；保留两组字段是为了兼容旧读法和更明确的当前状态读法。

cache 继续复用原生共享 cache，并尊重 active `GRAPHIFY_OUT`。多个 domain graph 引用同一个源文件时，应复用同一份按文件内容和 repo-relative path 命中的 cache，而不是按 domain output directory 拆分 cache。

Phase 0 manifest mode 不提供 output-dir 写锁；同一个 `<output-dir>` 不能并发执行多个 `extract --manifest` 或 `update --manifest`。调用方如果需要并发生成多个 domain graph，应使用不同输出目录，或在调用层串行化同一输出目录。

## Source Decoding

Graphify source extraction 必须为结构化 `.tab/.ini/.txt` 和 Lua/LH 路径使用共享 bytes-to-text decoder。这些路径可能产生 label、string reference 或 Include edge。

要求的解码顺序：

1. 使用 BOM 或强 NUL-byte 奇偶位特征做小样本 UTF-16LE sniff。
2. 只有 sniff 命中时才尝试 UTF-16LE，并且解码后做文本 sanity check。
3. UTF-8-SIG。
4. UTF-8。
5. GB18030。
6. replacement fallback，并记录 warning metadata。

约束：

- UTF-16LE detection 是 small-sample sniff，不是 full-file pre-scan。
- 解码结果包含内嵌 NUL 字符时，不能当作正常文本接受。
- replacement fallback 必须能在 extraction warnings 或 report data 中观察到。
- 现有非 JX3 文件应保持当前可读行为。

## 验收

Graphify Phase 0 完成条件：

- `extract --manifest --output-dir` 直接把 `graph.json` 写入指定输出目录。
- `extract --manifest --output-dir` 和 `update --manifest --output-dir` 都必须直接把 `GRAPH_REPORT.md` 写入指定输出目录。
- code-like manifest build/update 不需要 LLM backend API key。
- `update --manifest --output-dir` 可对同一 manifest 工作，并且不扫描无关仓库路径；从 manifest 移除的文件对应旧节点和边必须被清理。
- `--output-dir` 相对路径按 CLI 当前工作目录解析，绝对路径直接使用。
- `--output-dir` 不受 `GRAPHIFY_OUT` 影响；manifest mode cache 尊重 active `GRAPHIFY_OUT` 原生规则。
- manifest mode 缺失 `--output-dir` 必须明确失败；directory mode 传入 `--output-dir` 也必须明确失败。
- 生成的 domain graph 可通过 `query/path/explain --graph` 查询。
- `--manifest <repo_root>/graphify-out/manifest.json` 被明确拒绝，原生增量状态文件不作为 domain file-list input。
- manifest 中 unsupported 文件必须明确失败，不能静默产生空图或部分图。
- `--manifest` 与 positional `<path>` 同时出现时必须明确失败。
- 首次 `extract --manifest` 必须初始化 manifest-mode update state；`update --manifest` 在 state 缺失时必须 full rebuild 并重新初始化 state。
- GB18030 和 UTF-16LE fixture 能产生可读中文 labels/strings/references。
- replacement decoding 以 warning 形式报告，不能静默接受。
- 既有 directory-based extract/update 测试仍通过。
- manifest mode 复用 active `GRAPHIFY_OUT` 指向的原生共享 cache；同一源文件在多个 domain 中不应因为输出目录不同而重复生成独立 cache。
- JX3 smoke commands 可以 build/update/query 一个 `skill-core` domain graph，且 JX3 wrapper 不写 `graph.json`。

## 暂停门禁

Phase 0 落地后必须暂停，并记录：

- Graphify branch 或 commit SHA。
- 精确 Graphify test command 和结果。
- 精确 JX3 smoke commands 和结果。
- Graphify 确实把输出直接写入指定 domain output directory。
- JX3 wrapper 没有生成或修改 `graph.json`。

只有用户确认 Graphify 版本稳定后，JX3 wrapper 工作才能继续。
