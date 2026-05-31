# Graphify Tabular Sidecar DB 设计

## 背景

JX3 代码仓库包含大量 `.tab` 和 TSV-like `.txt` 结构化表格。当前 Graphify 的 `.tab` extractor 会把文件、列、行、路径引用和部分低基数字段写入 graph，但为了控制 graph 规模，它对大表存在行数、列数、文件大小和值节点数量上限。

这个策略对一般中小型结构化文件是合理的，但对 JX3 这类大型配置仓库会出现两个问题：

- 高行号业务实体可能被 extractor cap 截断，导致 graph 中找不到关键 `SkillID`、`BuffID` 或其他表格实体。
- 如果取消 cap 并把所有行列甚至单元格都入图，`graph.json` 会变成事实数据库，节点和边规模会快速膨胀，反而削弱 graph 的导航价值。

Phase 0 已经为 manifest-driven domain build/update 提供基础。Phase 0.5 在这个基础上增加 Graphify 通用的 tabular sidecar DB：graph 继续表达结构、锚点和关系骨架，完整表格事实进入可重建的 SQLite sidecar。

## 目标

- 为 `.tab`、TSV-like `.txt` 和 `.tsv` 提供通用 sidecar DB 能力。
- sidecar DB 是 Graphify 生成产物，可重建、不提交 git，默认写入 active Graphify output base 下的 `sidecar/tabular.sqlite`。
- 支持共享 sidecar DB，让多个 domain graph 通过 `sidecar_ref` 引用同一份表格事实。
- manifest 可以按文件声明 tabular policy、primary key、indexed columns 和 anchor columns。
- 大表可以完整入 sidecar，但 graph 只保留文件、表、列、必要锚点、路径引用、关系骨架和 `sidecar_ref`。
- 提供显式 CLI/API 让调用方按 `sidecar_ref` resolve 原始行，或按索引字段搜索行。
- manifest update 时同时检查文件 hash 和 domain tabular config；文件内容变化时整表重建，配置变化时重建 derived indexes 和 anchors，manifest 移除文件时先删除当前 domain membership，再按共享引用情况决定是否 prune 全局 sidecar 记录。
- 保持未启用 sidecar 时现有 `.tab` extractor 行为兼容。
- 用通用 fixture 证明这是 Graphify 能力，再用 JX3 mini/smoke 验证真实收益。

## 非目标

- 不在 Phase 0.5 做 `.ini` sidecar；`.ini` 继续使用现有 section-level graph extractor。
- 不在 Phase 0.5 做 `.csv` sidecar；CSV 的 quoting、escape、delimiter sniff 和跨行字段语义后续单独设计。
- 不做全量 cell 表，不把每个单元格都落成一条 DB 记录。
- 不把 JX3 专用业务语义写入 Graphify，例如 `SkillID -> skills.tab`、`BuffID -> Buff.tab`、`RecipeID -> recipeSkill.tab`。
- 不让 JX3 wrapper 或其他调用方直接拼装 `graph.json` 或管理 Graphify 内部 cache。
- 不改变 `graphify query` 的默认行为，不自动展开 sidecar row。
- 不在第一版做 row-level diff update。
- 不引入 LLM semantic extraction 或 embedding 检索路径。

## 产物归属

sidecar DB 是 Graphify-owned generated artifact，不是调用方私有状态，也不是源数据。

默认路径：

```text
<active_graphify_output>/sidecar/tabular.sqlite
```

规则：

- active Graphify output 默认是 `<repo_root>/graphify-out`。
- 如果环境中设置了 `GRAPHIFY_OUT`，sidecar 默认跟随 active Graphify output，写入 `$GRAPHIFY_OUT/sidecar/tabular.sqlite`。
- Phase 0.5 提供 `--sidecar-db <path>` 作为显式 override；build/update/search/resolve/query 必须使用同一个解析规则，避免 graph、cache、sidecar 分散到不可预期位置。
- Phase 0.5 sidecar DB 是 single-repo DB。Graphify 必须在 `meta` 中记录当前 `repo_root` 和 `repo_key`；打开既有 sidecar DB 时，如果其中记录的 repo identity 与当前 repo 不匹配，必须 fail，不能混用两个仓库的相对路径事实。
- Graph 输出必须记录结构化 `sidecar_db_hint`，用于定位候选 DB。hint 至少包含 `kind` 和 `path`：`repo_relative` 表示相对 repo root，`graphify_output_relative` 表示相对 active Graphify output，`absolute` 表示绝对路径。当 active Graphify output 或 `--sidecar-db` 不在 repo root 下时，必须记录 `absolute` hint。`sidecar_db_hint` 只用于定位候选 DB；`sidecar_db_id`、repo identity、source hash 和 sidecar domain config hash 仍是 domain-scoped 最终校验依据。
- sidecar DB 可通过重新运行 Graphify build/update 重建。
- sidecar DB 不应提交到 git。
- domain graph 可以引用 shared sidecar DB 中的 row。
- Graphify 负责 sidecar schema、写入、prune、resolve 和 search。
- 调用方负责生成 manifest，并决定哪些文件启用 sidecar policy。

共享 DB 优先于 domain-local DB。JX3 等大型仓库中，一个表格事实可能被多个 domain 使用；如果每个 domain 各自维护 sidecar DB，会造成重复存储、跨域关系难以解析和 prune 语义复杂化。

## 安全和过滤边界

sidecar DB 会保存 `raw_line`、`row_json` 和 `row_values_json`，比 graph 摘要包含更完整的源数据。因此 sidecar ingest 必须继承 Graphify 现有的输入过滤语义，不能因为 manifest 显式列出文件就绕过安全边界。

规则：

- sidecar manifest 文件必须经过 repo root 归一化，不能指向 repo root 外的路径，除非未来单独设计 trusted external source 支持。
- sidecar ingest 必须继承 `.graphifyignore`、内置 skip dirs 和 sensitive file policy。
- 如果 manifest 显式列出的文件被 `.graphifyignore`、skip dirs 或 sensitive file policy 拒绝，默认 fail closed，并在 report 中列出 rejected file 和 reason。
- Phase 0.5 不提供绕过 sensitive file policy 的通用开关；如果后续确实需要，应作为单独安全设计，明确审计、提示和生成产物保护策略。
- `graphify-out/sidecar/tabular.sqlite` 必须继续被视为 generated artifact；Graphify 应在报告中提示它不应提交到 git。

## Manifest 契约

Phase 0 已经要求 manifest 忽略未知字段。Phase 0.5 开始识别以下文件级字段：

实现对齐约束：

- 当前 Phase 0 的 manifest build 入口是 `graphify/__main__.py` 调用 `graphify/code_build_runner.py::build_code_graph(...)`。Phase 0.5 应复用这条路径，不能新增一条会绕过当前 manifest build/update、state、direct output 语义的 parallel runner。
- 当前 `graphify.manifest.load_domain_manifest()` 只返回代码文件集合和 repo-relative identity，并会忽略未知字段。Phase 0.5 的 tabular manifest 解析必须读取 caller manifest 原始 JSON，或显式扩展 Phase 0 loader 暴露 raw entries；不能从现有 `DomainManifest` 推断 `domain_id`、`tabular_policy`、列选择器或 sidecar config hash。
- `--output-dir` 仍然只是 domain graph 输出目录。sidecar 默认位置必须来自 active Graphify output 或显式 `--sidecar-db`，不能从 domain `--output-dir` 派生。
- effective `sidecar` 文件必须从普通 `extract()` 输入集合中分流出去，不能同时走现有 `.tab` extractor 和 sidecar graph projection。对同一个 sidecar-active 文件，graph 中的 file/table/column/anchor/path-ref skeleton 只能由 sidecar projection 生成；否则会出现重复 file/column/row 节点，以及 extractor cap 与 sidecar 完整覆盖互相矛盾的问题。effective `graph` 文件继续使用现有 extractor。
- 纯表格 domain 是合法输入：当 manifest 中所有文件都是 effective `sidecar` 时，Graphify 仍必须通过 sidecar projection 产出非空 graph，不能在普通 extractor 返回空结果后提前触发 empty graph failure。

```json
{
  "schema_version": 1,
  "repo_root": ".",
  "domain_id": "skill-core",
  "files": [
    {
      "path": "client/settings/skill/skills.tab",
      "tabular_policy": "sidecar",
      "primary_key": "SkillID",
      "indexed_columns": ["SkillID", "SkillName", "ScriptFile"],
      "anchor_columns": ["SkillID", "SkillName"]
    }
  ]
}
```

字段语义：

- `domain_id`: 可选。共享 sidecar DB 的 domain membership identity；缺失时使用 manifest output directory 的 repo-relative path。如果 manifest output directory 不在 repo root 下，必须显式提供 `domain_id`，不能用绝对路径派生 fallback identity。
- `tabular_policy`: `graph`、`sidecar` 或 `auto`。缺省值为 `auto`。
- `primary_key`: 可选。指定 row 的主键列，用于快速定位和稳定展示。
- `indexed_columns`: 可选。Graphify 需要写入 `indexed_values` 的列。
- `anchor_columns`: 可选。用于决定哪些字段可以参与 graph anchor label。

列选择器可以是简单字符串，也可以是带列序号的对象：

```json
{
  "primary_key": "SkillID",
  "indexed_columns": [
    "SkillID",
    {"name": "Name", "column_index": 3}
  ],
  "anchor_columns": [
    {"name": "Name", "column_index": 2}
  ]
}
```

`column_index` 使用 1-based 原始列序号。header 唯一时可以只写字符串；header 为空、重复或调用方需要精确定位时必须使用对象形式。Graphify 解析 manifest 时必须把列选择器归一化为 `normalized_name + optional column_index`，并在列不存在或重复列缺少 `column_index` 时 fail。

策略：

- `graph`: 使用现有 graph extractor 行为，不写 sidecar。
- `sidecar`: 完整表格写入 sidecar，同时在 graph 中写入文件、表、列、必要 metadata、anchor、path ref、关系骨架和 `sidecar_ref`。该语义等价旧方案中的 `hybrid`，不再保留“只写 DB、不写可导航骨架”的轻量 sidecar policy。
- `auto`: 小表保持 `graph`；大表切到 `sidecar`。

`auto` 大表阈值：

- 文件大于 1MB。
- 行数大于 2000。
- 列数大于 80。

满足任一条件即视为大表，effective policy 为 `sidecar`；否则为 `graph`。JX3 wrapper 建议显式声明 `sidecar`，不依赖 `auto` 猜测。

`auto` 必须在 manifest build/update 时解析成确定的 effective policy。`domain_files` 需要记录声明策略和生效策略，或至少在 `config_hash`、report 和 graph metadata 中使用 effective policy；否则同一份 manifest 在不同文件规模下会出现不可解释的 hash 和 update 行为。report 必须说明 `auto` 的命中原因，例如 file size、row count 或 column count。

显式 `graph` 不受 `auto` 阈值影响；即使文件超过大表阈值，也继续走现有 graph extractor，并在 report 中输出建议改为 `sidecar` 的 warning。显式 `sidecar` 始终写入 sidecar，不需要阈值判断。

`domain_files` 是 sidecar DB 内的 membership 表，只记录 effective `sidecar` 文件。effective `graph` 文件继续走普通 graph extraction，不写入 sidecar DB，也不进入 `sidecar_domain_config_hash`。

## Source Decoding

Phase 0.5 必须复用并扩展 Graphify 的 shared structured text decoder，避免 `.tab` extractor 和 sidecar ingest 对同一文件产生不同视图。

要求：

- `.tab`、TSV-like `.txt` 和 `.tsv` 统一通过 shared decoder 解码。
- Phase 0.5 decoder 至少支持 UTF-8、UTF-8-SIG、GB18030 和 UTF-16LE。
- decoder 输出用于 `raw_line`、`line_hash` 和 parser metadata；`source_sha256` 仍基于原始 bytes。
- `line_hash` 基于 decoder 后的 canonical raw line 计算，必须统一行尾为 `\n`，并保留字段原始文本和分隔符语义。
- 解码失败必须明确失败并报告 encoding candidate，不允许用 replacement 字符静默写入 sidecar。

## Sidecar Schema

MVP schema 避免全量 cell 表，采用行级存储加选择性索引。

这次 schema 收敛只简化物理存储，不降低 Phase 0.5 对外效果。MVP 仍必须满足：

- `sidecar resolve` 能从 `sidecar_ref` 回到准确 `source_file`、`row_no`、`raw_line`、`row_json` 和 `row_values_json`。
- `sidecar search --domain` 能查询当前 domain 声明的 `primary_key` 和 `indexed_columns`，并支持 `--file`、重复 header 下的 `--column-index` 和 normalized value lookup。
- `sidecar query --domain` 提供只读 Agent SQL/debug 能力，用于评测分析和关系反推；它不替代 `search/resolve` 的稳定业务接口。
- graph 中仍写入 source file、table、column、anchor、path-ref skeleton 和 `sidecar_ref`。
- graph-aware search/resolve 仍校验 `repo_key`、`sidecar_db_id`、`sidecar_domain_config_hash`、`source_sha256`、`domain_file_config_hash` 和 row `line_hash`。
- shared DB 的 domain 隔离、union index 重建和 prune 语义不变。

被延后的字段只能影响诊断或未来增强，不能成为 Phase 0.5 核心能力的前置条件。`size`、`mtime_ns`、`created_at`、`updated_at`、`ref_kind`、`target_kind`、`confidence`、materialized `sidecar_ref_key` 等字段可以作为后续 schema migration 增加；MVP 先通过 `sha256`、`config_json/config_hash`、`union_config_hash`、`target_ref/value` 和按需计算的 `sidecar_ref_key` 保证正确性。

```sql
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE source_files (
  file_id INTEGER PRIMARY KEY,
  source_file TEXT NOT NULL UNIQUE,
  file_key TEXT NOT NULL UNIQUE,
  sha256 TEXT NOT NULL,
  parser_version INTEGER NOT NULL,
  union_config_hash TEXT NOT NULL,
  encoding TEXT NOT NULL
);

CREATE TABLE domain_files (
  domain_id TEXT NOT NULL,
  file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
  declared_policy TEXT NOT NULL,
  effective_policy TEXT NOT NULL,
  config_json TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  PRIMARY KEY (domain_id, file_id)
);

CREATE TABLE tabular_tables (
  table_id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  delimiter TEXT NOT NULL,
  header_row INTEGER NOT NULL,
  row_count INTEGER NOT NULL,
  column_count INTEGER NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE tabular_columns (
  column_id INTEGER PRIMARY KEY,
  table_id INTEGER NOT NULL REFERENCES tabular_tables(table_id) ON DELETE CASCADE,
  column_index INTEGER NOT NULL,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL
);

CREATE TABLE rows (
  row_id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
  table_id INTEGER REFERENCES tabular_tables(table_id) ON DELETE CASCADE,
  row_no INTEGER NOT NULL,
  line_hash TEXT NOT NULL,
  raw_line TEXT NOT NULL,
  row_json TEXT NOT NULL,
  row_values_json TEXT NOT NULL,
  UNIQUE(file_id, row_no)
);

CREATE TABLE indexed_values (
  file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
  row_id INTEGER NOT NULL REFERENCES rows(row_id) ON DELETE CASCADE,
  domain_id TEXT NOT NULL,
  column_index INTEGER NOT NULL,
  column_name TEXT NOT NULL,
  normalized_column_name TEXT NOT NULL,
  value TEXT NOT NULL,
  value_norm TEXT NOT NULL
);

CREATE TABLE tabular_refs (
  file_id INTEGER NOT NULL REFERENCES source_files(file_id) ON DELETE CASCADE,
  row_id INTEGER NOT NULL REFERENCES rows(row_id) ON DELETE CASCADE,
  target_ref TEXT NOT NULL,
  value TEXT NOT NULL
);
```

Required indexes:

```sql
CREATE INDEX idx_domain_files_file ON domain_files(file_id);
CREATE INDEX idx_tables_file ON tabular_tables(file_id);
CREATE INDEX idx_columns_table ON tabular_columns(table_id);
CREATE INDEX idx_rows_file ON rows(file_id);
CREATE INDEX idx_indexed_lookup ON indexed_values(domain_id, normalized_column_name, value_norm);
CREATE INDEX idx_indexed_column_lookup ON indexed_values(domain_id, normalized_column_name, column_index, value_norm);
CREATE INDEX idx_indexed_row ON indexed_values(row_id);
CREATE INDEX idx_refs_row ON tabular_refs(row_id);
```

每个打开 sidecar DB 的 SQLite connection 必须启用 `PRAGMA foreign_keys=ON`，并在 startup self-check 中校验该设置。schema 的 prune 和整表重建路径依赖 `ON DELETE CASCADE`；如果 foreign key enforcement 未开启，Graphify 必须 fail fast，不能继续写入可能留下 orphan rows 的 DB。

`meta` 必须至少记录：

- `schema_version`
- `created_by`
- `repo_root`
- `repo_key`
- `sidecar_db_id`
- `sidecar_instance_id`
- `sidecar_generation`

`repo_key` 是 canonical repo root 的稳定 hash。`sidecar_db_id` 是可重建的确定性 ID，由 `repo_key`、sidecar kind 和 `schema_version` 派生；删除并重建 sidecar DB 后，只要 repo identity、sidecar kind 和 schema version 不变，`sidecar_db_id` 必须保持不变。`sidecar_instance_id` 是 sidecar DB 创建时生成并持久化的随机实例 ID，只用于诊断本地 DB 实例，不能作为 graph resolve 的硬性匹配条件。`sidecar_generation` 每次成功发布 sidecar update 后递增，用于报告和诊断当前 sidecar DB 快照；它不能作为所有 graph resolve 的硬性全局匹配条件，否则一个 domain 的更新会使其他 domain 的旧 graph 全部误判失效。

`domain_id` 用于共享 sidecar DB 的 prune 语义。Graphify 可以从 manifest metadata 读取显式 `domain_id`；如果没有显式值，则使用 manifest output directory 的 repo-relative path 作为稳定 domain identity。若 output directory 不能表示为 repo-relative path，Graphify 必须要求 manifest 显式声明 `domain_id` 并 fail fast。

`source_file` 是 repo-relative path。`file_key` 是 `repo_key` 和 `source_file` 的稳定 hash，不随文件内容变化。`line_hash` 基于共享 source decoder 输出后的 canonical raw line 计算；canonical raw line 保留字段原始文本和分隔符语义，但统一行尾为 `\n`，避免不同平台换行导致 ref 漂移。`sidecar_ref_key` 由 `repo_key`、`file_key`、`row_no` 和 `line_hash` 派生，用于生成稳定 `sidecar_ref`；MVP 不需要把它冗余存入 `rows` 表，可以在 resolve/search/projection 时按需计算。即使 SQLite 自增 `row_id` 在 rebuild 后变化，旧引用也不会误指向另一行；如果行内容变化，旧 `sidecar_ref` 应解析失败，而不是返回错误事实。

`row_json` 是按 normalized column name 生成的便捷对象；`row_values_json` 是按原始列顺序保存的数组，元素至少包含 `column_index`、`name`、`normalized_name` 和 `value`。当 header 为空或重复时，Graphify 必须通过 `row_values_json` 保留原始结构，并在 `row_json` 中使用确定性 disambiguation，不能静默覆盖字段。

`config_hash` 和 `union_config_hash` 必须使用 canonical JSON 计算，避免同一语义配置因字段顺序或大小写差异导致重复重建，也避免真实配置变化漏触发。列选择器必须先基于解析后的 header 归一化为 `{name, normalized_name, column_index}`；`indexed_columns` 和 `anchor_columns` 再按 `(normalized_name, column_index, name)` 排序后进入 hash。canonical 输入至少包含：

- `schema_version` 和 tabular sidecar schema version。
- parser name 和 parser version。
- declared policy 和 effective policy。
- normalized `primary_key`。
- normalized `indexed_columns`，按 normalized column name 和可选 column index 排序。
- normalized `anchor_columns`，按 normalized column name 和可选 column index 排序。

未知 manifest 字段不进入 hash。`primary_key`、`indexed_columns` 和 `anchor_columns` 中指向不存在的列必须 fail；重复 header 缺少 `column_index` 也必须 fail；不允许把缺失列 silently dropped 后继续计算 hash。

## 为什么不做全量 cell 表

全量 cell 表会把一个二维表拆成每个单元格一条记录，规模从行数级变成行数乘列数级。

例如一个 60000 行、250 列的大表会产生 15000000 条 cell 记录。JX3 有大量 `.tab` 和 TSV-like `.txt` 文件，如果 MVP 就全量 cell 化，SQLite 会快速膨胀到几千万甚至上亿行。

本地 JX3 仓库快照的粗略容量基线（2026-05-27，仅作数量级判断，不作为 Graphify 通用阈值）：

- 扫描 `.tab`、`.tsv` 和 `.txt` 文件约 84431 个，总大小约 491MB，总行数约 435 万行。
- 命中当前 `auto` 大表条件的文件约 1103 个，其中大于 1MB 的文件 36 个，超过 2000 行的文件 105 个，超过 80 列的文件 1009 个。
- 单文件最大行数约 24.3 万行，单文件最大列数约 757 列；典型大型技能/配置表包括 `client/settings/skill/Buff.tab`、`client/settings/NpcTemplate.tab` 等。

这说明 sidecar 的行级存储在 JX3 规模下仍是百万行级别，SQLite single-file DB 可以作为 Phase 0.5 的合理起点；如果转成全量 cell 表，按宽表估算会进入数千万到上亿记录级别，写入、索引和 rebuild 风险明显更高。

这会带来几个问题：

- 写入慢，每次 ingest 都要插入大量单元格。
- 更新慢，文件变化时删除和重建成本高。
- 索引膨胀，按列和值查找需要额外大索引。
- 大量空值、默认值、数值配置项没有直接检索价值。
- Phase 0.5 的核心查询主要是按主键、索引字段、路径引用和 graph anchor 回源，不需要任意 cell 级扫描。

因此 MVP 采用：

- `rows.row_json` 保存 normalized column name 视角下的便捷行对象。
- `rows.row_values_json` 保存原始列顺序和重复列名信息。
- `rows.raw_line` 保存原始证据。
- `indexed_values` 只保存需要搜索和 join 的列，并同时保存原始 column/value 与 normalized column/value。
- `tabular_refs` 只保存可解释的引用关系。

全量 cell 表可以作为后续可选增强，用于任意列全局搜索、schema profiling 或列分布统计，但不进入 Phase 0.5。

## Graph 输出策略

启用 `sidecar` 后，graph 不再把大表完整展开成事实数据库。

Graphify 应写入：

- source file node。
- table node。
- column nodes。
- selected anchor row/entity nodes。
- path-like refs。
- `sidecar_ref` metadata（用于 row-derived anchor/path/ref skeleton）。

Graphify 不应写入：

- 全量 row nodes。
- 全量 cell nodes。
- JX3 专用业务关系。

`sidecar_ref` 格式：

```text
sidecar://tabular/<repo_key>/<file_key>/<row_no>/<line_hash>
```

sidecar-active source file/table/column metadata 至少包含：

- `source_file`
- `file_key`
- `sidecar_db_id`
- `source_sha256`
- `domain_file_config_hash`
- `union_config_hash`

`sidecar` 生成的 row-derived anchor/path/ref skeleton metadata 额外包含：

- `row_no`
- `sidecar_ref`
- `sidecar_ref_key`

graph-level metadata 至少包含：

- `domain_id`
- `sidecar_domain_config_hash`
- `sidecar_db_hint`
- `sidecar_db_id`
- `sidecar_schema_version`
- `repo_key`
- `repo_root`
- `sidecar_instance_id`
- `sidecar_generation`

`sidecar_domain_config_hash` 是当前 domain 所有 effective `sidecar` 文件的 `domain_files.config_hash` canonical aggregate hash，用于判断 graph 与 sidecar 中的 sidecar-active domain config 是否一致；effective `graph` 文件不参与该 hash。`domain_file_config_hash` 是该 source file 在当前 domain 下的 file-level config hash；`union_config_hash` 是所有 active domains 对该文件的 union config hash，主要用于 sidecar derived index 维护、report 诊断和 global debug。`sidecar resolve/search` 接收 graph metadata 或 `sidecar_ref` 附带 metadata 时，domain-scoped 模式必须校验 `sidecar_domain_config_hash`、`domain_file_config_hash`、`source_sha256` 和 row line hash；global debug 或请求 union-derived facts 时才把 `union_config_hash` 作为硬校验。不匹配时明确提示 rebuild sidecar/graph，不能只依赖 `sidecar_db_id`、`source_sha256` 或 `sidecar_generation`。

Hash/identity validation matrix：

| 场景 | `repo_key` | `sidecar_db_id` | `sidecar_domain_config_hash` | `source_sha256` | `domain_file_config_hash` | `union_config_hash` | row `line_hash` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| build/update | hard | hard | 生成并写入 graph | 写入 source/file metadata | 写入 source/file metadata | 生成/更新 derived data | 写入 sidecar rows |
| `search --domain --graph` | hard | hard | hard | hard for returned rows | hard for returned rows | diagnostic | skip |
| `search --global-debug --graph` | hard | hard | skip | hard for returned rows | diagnostic | hard for returned rows | skip |
| `resolve --domain --graph` | hard | hard | hard | hard | hard | diagnostic | hard |
| `resolve --global-debug --graph` | hard | hard | skip | hard | diagnostic | hard | hard |
| `query --domain --graph` | hard | hard | hard | hard for query scope | hard for query scope | diagnostic | skip unless returned as ref evidence |
| `query --global-debug --graph` | hard | hard | skip | hard for query scope | diagnostic | hard for query scope | skip unless returned as ref evidence |
| raw sidecar mode | hard against DB meta only | hard against DB meta only | current DB domain hash only | current DB data only | current DB data only | current DB data only | hard for `resolve` |

`hard` 表示不匹配必须失败；`diagnostic` 表示返回或 report 中可暴露用于解释，但 domain-scoped 读取不能因为其他 domain 导致的 union 变化而误失效；`skip` 表示该场景不应依赖该字段做判断。这个矩阵是 MVP 的最小一致性边界，不建议把 `domain_file_config_hash` 降级为诊断字段，否则同一 source 内容、不同 domain selector 的旧 graph 可能回到错误字段集合。

`sidecar_db_hint.kind` 必须是 `repo_relative`、`graphify_output_relative` 或 `absolute`。如果 active Graphify output 在 repo root 内，优先写 `repo_relative`；如果 sidecar 位于 active Graphify output 下但不在 repo root 内，写 `absolute`，并可额外写 `graphify_output_relative` 作为调试信息。resolve/search/query 可以使用 hint 定位 DB，但必须继续校验 `repo_key`、确定性的 `sidecar_db_id`、source hash 和 sidecar domain config hash。`sidecar_instance_id` 和 `sidecar_generation` 只需要记录在 graph-level metadata 或 report-level metadata 中，用于诊断当前 sidecar 快照；node/edge 不需要记录它们。

`sidecar` 策略下，graph 必须写 file/table/column metadata，并写入 anchor/path/ref skeleton 和 `sidecar_ref`。这保留旧 `hybrid` 的效果，但删除独立 `hybrid` policy name。

graph skeleton 的最小契约：

- source file node: 表示 tabular source file，包含 `source_file`、`file_key`、`sidecar_db_id`、`source_sha256`、`domain_file_config_hash` 和 `union_config_hash`。
- table node: 每个 sidecar-active 文件至少一个 table node；source file node 通过 `contains` 指向 table node。
- column nodes: table node 通过 `contains` 指向 column node；column node 记录 `column_index`、`name`、`normalized_name`。
- anchor node: 仅当 manifest 为文件声明了 `anchor_columns` 时生成。每个命中 anchor 的 row 生成一个稳定 anchor node；label 由 anchor column value 按 canonical selector order 拼接，空值全部为空时跳过该 row anchor。
- anchor metadata: anchor node 必须包含 `row_no`、`sidecar_ref`、`sidecar_ref_key`、`file_key`、`source_file`、`domain_file_config_hash`。`sidecar_ref_key` 使用 `repo_key/file_key/row_no/line_hash` 的 canonical hash，不使用 SQLite 自增 ID。
- anchor edges: table node 通过 `contains` 指向 anchor node；anchor node 可以通过 `references` 指向从该 row 抽取出的通用 path-like target。
- path-like refs: Graphify 只投影通用 path-like refs，不做 JX3 专用业务 join。path ref node/edge 必须复用既有 `.tab` extractor 的路径归一化语义；无法解析到真实文件时保留 tab-owned stub，不静默丢弃。
- 禁止项: 不生成全量 row nodes、全量 cell nodes，也不基于列名猜测 `SkillID -> BuffID` 等业务关系。

## 通用引用抽取

Graphify core 可以做通用、低风险的引用抽取：

- path-like 字段，例如以脚本、资源、配置路径形式出现的值。

manifest 指定的 `primary_key` 和 `indexed_columns` 只进入 `indexed_values`，用于 search 和上层 wrapper 后续解释，不自动形成 refs 或 graph edge。

第一版不把格式明显但无 manifest 指定语义的 ID-like 值自动写成 graph edge。它们最多进入 `indexed_values`。这样可以避免把大量数字配置误连成噪声关系。

Graphify core 不解释：

- `SkillID` 应指向哪个表。
- `BuffID` 应指向哪个表。
- `RecipeID` 应如何和技能、配方、文本表组合。
- cooldown、mobile、school 等 JX3 专用业务关系。

这些业务语义由 JX3 wrapper 或上层 domain adapter 基于 sidecar search/resolve 实现。

## Update 和 Prune 语义

MVP 使用文件级更新，不做 row-level diff。文件内容变化和 domain tabular config 变化都可能触发 sidecar 维护。

Phase 0.5 的 sidecar 写入只发生在 manifest build/update 路径。普通 directory `graphify watch` 不掌握 domain manifest 和 `domain_id` 时，不能直接维护 shared sidecar DB；它可以识别 tabular 文件变化并标记需要 manifest rebuild，或在未来接入 manifest watch mode 后复用同一套 single-writer sidecar update 协议。

流程：

1. 按 repo-relative path 定位当前 manifest source files，并解析每个 tabular 文件的 effective policy。
2. 读取当前 `domain_id` 的旧 sidecar-active membership，计算 `old_set`、`new_set` 和 `affected_set = old_set ∪ new_set`；`new_set` 只包含 effective `sidecar` 文件。
3. 将当前 `domain_id` 的 `domain_files` 视为 sidecar-active 整集合替换：删除 `old_set - new_set`，upsert `new_set`。effective `graph` 文件不进入 `domain_files`。
4. 对 `affected_set` 中的每个文件重新计算 active domain configs 的 union config hash，包含 declared policy、effective policy、primary key、indexed columns 和 anchor columns。
5. 如果某文件已经没有任何 active `domain_files` membership，prune 该文件对应的全局 sidecar records。
6. 如果某文件仍有 active membership，计算文件 sha256。
7. 如果 sha256 和本轮计算出的 union config hash 均与 `source_files` 中的上一轮记录一致，跳过 sidecar ingest。
8. 如果 sha256 变化，在事务内保留该文件的 `source_files` identity 和所有 `domain_files` membership，删除并重建该文件下的 table、row、index 和 ref 记录。
9. 如果 sha256 未变化但 union config hash 变化，只重建该文件的 derived indexes 和 anchor metadata；通用 path-like refs 可按现有 row data 重建或保持一致，不需要重新读取原始文件。这个场景包括某个 domain 从 manifest 移除文件，但其他 domain 仍引用该文件，导致 union config 收缩。
10. 重新解析或重建 derived data 后，更新 `source_files.sha256`、`source_files.union_config_hash`、`parser_version`、`encoding`、受影响 active `domain_files.config_hash` 和 sidecar generation。
11. sidecar update 在 shared DB 内事务提交并完成必要 fsync 后，Graphify 才能原子发布引用该 `sidecar_db_id`、`source_sha256` 和 stable `sidecar_ref` 的 graph output。

要求：

- sidecar update 必须事务化；失败不能留下半更新状态。
- shared sidecar DB 必须使用 repo-level single-writer lock；同一 repo 内两个 build/update 不能并发写入同一个 sidecar DB。
- SQLite connection 必须启用 `WAL` 和合理 `busy_timeout`；read-only resolve/search/query 使用独立只读 connection，并接受 SQLite snapshot 语义。
- Graphify 不允许通过无锁替换整个 shared SQLite 文件来发布更新；sidecar update 必须在同一个 DB 内事务提交，避免破坏其他 domain membership。
- graph output 必须在 sidecar transaction 成功提交并 fsync 后发布；graph 发布失败时，sidecar 可以保留较新的 generated state，但下次 build/update 必须能通过 manifest 和 `source_sha256` 幂等恢复。
- prune 后，`sidecar search --domain <domain_id>` 不能查到已从该 domain manifest 移除的文件事实。
- 如果同一文件仍被其他 domain 引用，global `sidecar search` 仍可查到该文件事实。
- 同一文件被多个 domain 引用时，`indexed_values` 必须按所有 active `domain_files` config 的 union 生成；后加入的 domain 不能因为文件 hash 未变而缺失自己的索引列。
- `tabular_refs` 第一版只保存通用 path-like refs，不包含 domain 私有业务 refs；它们可以随文件重建，也可以在文件内容不变时保持一致。
- 某个 domain 移除文件但其他 domain 仍引用该文件时，Graphify 必须按剩余 active configs 重建 derived indexes，不能保留已移除 domain 私有的索引语义。
- graph 必须记录本次 build 使用的 `sidecar_db_id`、`source_sha256`、`sidecar_domain_config_hash`、`domain_file_config_hash` 和 `union_config_hash`；`sidecar` row-derived skeleton 还必须记录 `sidecar_ref`。`sidecar resolve/search` 在接收 graph 中的 ref 或 metadata 时必须校验 repo key、DB ID、row line hash、source sha 和 sidecar domain config hash；不匹配时明确提示重新 build/update graph，不能静默返回旧事实。全局 `sidecar_generation` 只用于诊断，不作为跨 domain graph 的硬性失效条件。
- 文件内容没变但当前 domain config 变化时，旧 graph 不能因为 `source_sha256` 和确定性 `sidecar_db_id` 仍匹配而继续被当成完整可用。其他 domain 导致的 `union_config_hash` 变化不应让当前 domain graph 在只访问自身声明字段时硬失效；它只要求 sidecar 重建 union derived indexes，并在 global debug 或 union-derived facts 请求中参与校验。
- graph update 和 sidecar update 必须对同一轮 manifest 中的 effective policy 决策达成一致；sidecar update 只维护 effective `sidecar` 文件集合。
- schema version 不兼容时必须明确报错，并提示 rebuild sidecar。
- Phase 0.5 不实现增量 schema migration。sidecar DB 是可重建生成产物，schema mismatch 的恢复路径是删除或 rebuild sidecar 后重新运行 manifest build/update。验收目标是 JX3 smoke 的 full rebuild 能在分钟级完成；如果真实仓库 full rebuild 超过约 5 分钟，再把 forward-only migration、per-file shard 或 staged rebuild 作为 Phase 1 设计。

## CLI 和 API

Phase 0.5 增加显式 sidecar 命令，不改变 `graphify query` 默认展开行为。

```bash
graphify sidecar resolve sidecar://tabular/<repo_key>/<file_key>/<row_no>/<line_hash> \
  --domain skill-core \
  --graph graphify-out/graph.json
```

输出应包含：

- source file path。
- row number。
- primary key。
- raw line。
- row JSON。
- ordered row values JSON。
- indexed values。
- refs。

`resolve/search` 有两种校验模式：

- raw sidecar mode：只提供 `sidecar_ref`、`--domain`、`--file`、`--column` 等 sidecar 参数，不提供 graph metadata。此模式只能校验 repo identity、`sidecar_db_id`、`sidecar_ref` 格式、row line hash、domain membership 和当前 sidecar DB 中的 sidecar domain config；它不能证明调用方手里的旧 graph 与当前 sidecar 完全一致。
- graph-aware mode：提供 `--graph <graph-output>`，或等价 programmatic API metadata envelope。Graphify 必须从 graph-level metadata 读取 `sidecar_db_hint`、`sidecar_db_id`、`domain_id`、`sidecar_domain_config_hash`，并从匹配的 source-file/table/column 或 row-derived source metadata 读取 `source_sha256`、`domain_file_config_hash` 和 `union_config_hash`。JX3 wrapper 和其他 domain adapter 从 graph 命中回源时默认使用 graph-aware mode。

`--domain` 对 `resolve` 可选；存在时，返回的 indexed values 和 refs 必须限制到该 domain 当前 config 声明的 `primary_key`、`indexed_columns` 和 path-like refs。JX3 wrapper 和其他 domain adapter 默认必须传 `--domain`。不传 `--domain` 时是 global debug resolve，可以返回 shared DB 中该 row 的 union indexed values 和 refs，并要求 `union_config_hash` 匹配。CLI 可以用显式 `--global-debug` 避免 graph-aware mode 自动从 graph metadata 填充 `domain_id`。

```bash
graphify sidecar search \
  --domain skill-core \
  --file client/settings/skill/skills.tab \
  --column SkillID \
  --value 40177
```

搜索规则：

- `--file` 可选；存在时限制到单文件。
- `--domain` 可选；存在时限制到该 domain 当前 manifest membership。
- `--domain` 存在时，搜索列还必须属于该 domain 当前 config 声明的 `primary_key` 或 `indexed_columns`。物理 DB 中按 union 生成的索引不能泄漏成 domain 的查询能力；如果列存在于 union index 但不属于该 domain config，必须返回 `not indexed for domain`。
- 不传 `--domain` 时是 global debug search。CLI graph-aware mode 下需要显式 `--global-debug` 才允许不传 `--domain`；JX3 wrapper 和其他 domain adapter 默认必须传 `--domain`，避免跨 domain 命中不属于当前分析上下文的事实。
- `--column-index` 可选；当 header 重复或调用方需要精确列定位时使用。
- `--column` 和 `--value` 必填。
- 当只传 `--column` 时，搜索命中所有 `normalized_column_name` 相同的列；当同时传 `--column-index` 时，搜索必须进一步收窄到指定列序号。
- 搜索使用 normalized value。
- 输出返回 row summary 和 `sidecar_ref`。

graph-aware search 的校验顺序必须是：先用 graph-level metadata 定位 sidecar DB、校验 repo identity、`sidecar_db_id`、`domain_id` 和 `sidecar_domain_config_hash`；再执行 sidecar search；对每个命中 row，按 row 的 `file_key` 优先、`source_file` 兜底回到 graph 中的 source-file metadata index，校验该文件的 `source_sha256` 和 `domain_file_config_hash`。source-file metadata index 必须至少支持 `file_key` 和 `source_file` 两种 lookup。如果 graph 中找不到对应 source-file metadata，必须明确失败，除非调用方显式选择 raw sidecar mode。

如果提供 `--sidecar-db <path>`，`resolve`、`search` 和 `query` 必须使用同一套 path 解析规则。graph-aware mode 未提供 `--sidecar-db` 时，必须使用 graph metadata 中的 `sidecar_db_hint` 定位候选 DB，不能从当前进程的 active Graphify output 重新推导；raw sidecar mode 未提供 `--sidecar-db` 时，才使用 active Graphify output 下的默认 sidecar DB。

`--graph` 和 `--sidecar-db` 同时提供时，`--sidecar-db` 只能作为候选 DB override；Graphify 仍必须校验 graph metadata 中的 `sidecar_db_id`、repo identity、source hash 和 sidecar domain config hash。后续可以增加 programmatic API，但 Phase 0.5 CLI 先作为可验证边界。

### 只读 Agent SQL

`sidecar query` 是面向 Agent、评测和 debug 的高级接口，不是普通 wrapper 的主业务接口。JX3 wrapper 的日常回源仍默认使用 `sidecar search/resolve`；只有需要探索式分析、评测集反推或关系候选统计时才使用 SQL。

```bash
graphify sidecar query \
  --domain skill-core \
  --graph graphify-out/graph.json \
  --sql-file query.sql \
  --limit 1000 \
  --timeout-ms 5000
```

执行规则：

- `query` 只能使用 read-only SQLite connection，并启用 `PRAGMA query_only=ON`。
- 只允许单条 `SELECT` 或 `WITH` 语句；禁止 `INSERT`、`UPDATE`、`DELETE`、`DROP`、`ALTER`、`CREATE`、`ATTACH`、`DETACH`、`VACUUM`、`REINDEX`、`PRAGMA` 等会修改 DB 或改变连接状态的语句。
- SQL 安全检查不能用简单 token 扫描整条 SQL，因为字符串字面量、列别名或字段名可能包含 `insert`、`update`、`delete` 等词。MVP 的 syntactic precheck 只判断顶层首关键字为 `SELECT` 或 `WITH`；真正的只读边界由 read-only connection、`PRAGMA query_only=ON`、SQLite authorizer、物理表读取拒绝和函数 allowlist 共同保证。
- 单语句约束必须显式检测字符串字面量和 SQL 注释外的 statement separator `;`；字符串值中的 `;` 不能误杀。执行层仍用 `sqlite3.Cursor.execute()` 和 SQLite authorizer 兜底，但不能把 `ProgrammingError` 当成唯一多语句防线，因为用户 SQL 会被包进 `SELECT * FROM (...) LIMIT ?` 子查询，部分多语句会变成 SQLite syntax error。
- 默认必须有 `--domain`，或由 graph-aware mode 从 graph metadata 填充 `domain_id`。不传 `--domain` 时必须显式 `--global-debug`，并按 union/global debug 规则校验。
- `--limit` 默认 1000，允许调用方调低；实现必须设置最大上限，建议不超过 10000。输出超过 limit 时标记 `truncated=true`。
- `--timeout-ms` 默认 5000，必须通过 SQLite progress handler 或等价机制中断长时间查询，避免 Agent SQL 因聚合或 join 卡住评测流程。
- 输出为 JSON envelope，包含 sidecar DB path、schema version、repo key、sidecar DB ID、sidecar instance ID、sidecar generation、domain ID、SQL scope、columns、rows、row_count 和 truncated。
- graph-aware mode 与 `search/resolve` 使用同一套 DB path 解析、repo identity、`sidecar_db_id`、`domain_id` 和 `sidecar_domain_config_hash` 校验。`--global-debug` 模式还必须校验 union-derived facts 所需的 `union_config_hash`；如果 graph metadata 不能覆盖 global debug scope 中的所有 source files，必须明确失败，调用方应改用 raw sidecar debug 或提供覆盖该 scope 的 graph metadata。
- SQL 第一版只承诺稳定的 domain-scoped views，不承诺物理表 schema 是 Agent 可依赖的长期接口。实现必须使用 SQLite authorizer 或等价机制拒绝用户 SQL 直接读取物理表；用户 SQL 只能读取 `current_domain_*` views。允许使用的 SQL 函数必须走 allowlist，避免未来注册扩展函数后扩大只读 SQL 能力边界。

稳定 view 名称：

- `current_domain_sources`：当前 domain 可见的 sidecar source files，包含 `domain_id`、`source_file`、`file_key`、`sha256`、`encoding`、`domain_file_config_hash` 和 `union_config_hash`。
- `current_domain_rows`：当前 domain 可见 rows，包含 `source_file`、`file_key`、`row_no`、`line_hash`、`raw_line`、`row_json`、`row_values_json` 和可按需计算的 `sidecar_ref`。
- `current_domain_indexed_values`：当前 domain 可见 indexed values，包含 `domain_id`、`source_file`、`file_key`、`row_no`、`column_name`、`normalized_column_name`、`column_index`、`value` 和 `value_norm`。
- `current_domain_refs`：当前 domain 可见 path-like refs，包含 `source_file`、`file_key`、`row_no`、`target_ref` 和 `value`。

这些 view 的目标是让 Agent 做探索式分析时天然落在当前 domain scope 内，避免手写 SQL 漏掉 domain membership filter。raw physical table 查询只能作为内部调试，不进入 `sidecar query` 的默认能力。

## 错误处理

必须明确失败的情况：

- sidecar DB 不存在但用户调用 `sidecar resolve/search/query`。
- sidecar DB 的 `repo_key` 或 `repo_root` 与当前 repo identity 不匹配。
- manifest 文件被 `.graphifyignore`、skip dirs 或 sensitive file policy 拒绝。
- manifest output directory 不在 repo root 下且未显式提供 `domain_id`。
- `sidecar_ref` 格式非法。
- `sidecar_ref` 的 `repo_key` 与当前 sidecar DB 不匹配。
- `sidecar_ref` 指向的 row 已被 prune。
- `sidecar_ref` 的 `line_hash` 与当前 DB 中同一 `file_key`、`row_no` 的行不匹配。
- graph-aware mode 下，graph metadata 中的 `sidecar_db_id`、`source_sha256`、`sidecar_domain_config_hash`、`domain_file_config_hash` 或 `sidecar_ref` 与当前 sidecar DB 不匹配。
- global debug 或 union-derived facts 请求中，graph metadata 中的 `union_config_hash` 与当前 sidecar DB 不匹配。
- manifest 声明了 `tabular_policy`，但文件不是可解析的 tabular 文件。
- `primary_key`、`indexed_columns` 或 `anchor_columns` 指向不存在的列。
- `sidecar search --domain` 请求的列不属于该 domain 当前 config 的 `primary_key` 或 `indexed_columns`。
- `sidecar resolve --domain` 请求的 row 不属于该 domain 当前 manifest membership。
- `sidecar query` 收到非 `SELECT/WITH`、多语句、超过最大 limit、缺少 domain 且未显式 `--global-debug`、直接读取物理表、调用非 allowlist 函数、尝试写入/PRAGMA/ATTACH 等 unsafe SQL 操作，或试图访问未暴露的 unsafe raw SQL mode。
- schema version 不兼容。

可以 warning 并继续的情况：

- 单行列数不足或超过 header 列数。
- 个别字段解码后为空或无法推断类型。
- path-like ref 无法解析到仓库内目标文件。

warning 必须可通过 table metadata 或 report data 观察，不能静默吞掉。

## Report 和可观测性

Phase 0.5 必须让 sidecar 状态可检查，避免出现 graph 命中但无法判断 sidecar 是否完整的问题。

`GRAPH_REPORT.md` 或等价 report data 中至少包含：

- sidecar DB path。
- sidecar schema version。
- repo key。
- sidecar DB ID。
- sidecar instance ID。
- current sidecar generation。
- tabular files count。
- tables count。
- rows count。
- indexed values count。
- refs count。
- warnings count。
- pruned files/rows count。
- domain membership count。
- rejected files count 和 rejected reasons。
- sidecar lock wait/fail count。
- SQLite busy timeout count。

`GRAPH_REPORT.md` 中的统计必须区分 current snapshot 与 last run delta。current snapshot 表示当前 DB 全量状态；last run delta 表示本轮 build/update 新增、重建、prune 和 warning 数量。

`graphify sidecar search/resolve/query` 输出必须包含当前 sidecar DB path、schema version、repo key、sidecar DB ID、sidecar instance ID 和 sidecar generation。遇到 search miss 或 resolve miss 时，应区分未命中、未索引、not indexed for domain、文件已 prune、domain scope 不包含该文件、schema version 不兼容、repo/DB identity 不匹配、source sha 不匹配、sidecar domain config hash 不匹配、domain file config hash 不匹配、union config hash 不匹配、manifest 文件被过滤策略拒绝、sidecar lock busy 等情况。其中 union config hash mismatch 只应在 global debug 或 union-derived facts 请求中作为硬失败原因；domain-scoped 查询访问自身声明字段时应作为诊断信息而不是硬失败。`sidecar query` 还必须区分 SQL safety rejection、domain scope missing、limit exceeded/truncated 和 readonly connection failure。

## 测试和评测

Phase 0.5 的评测分为通用功能正确性、通用价值评测和 JX3 smoke。

功能正确性：

- `auto` 判断为大表时 sidecar 全量入库，graph 不依赖全量 row nodes。
- `sidecar` graph 中的 `sidecar_ref` 可以 resolve 到准确 `source_file`、`row_no`、`raw_line`、`row_json` 和 `row_values_json`。
- `sidecar resolve --domain` 只返回该 domain config 可见的 indexed values 和通用 path-like refs。
- `sidecar search` 可按 `primary_key` 和 `indexed_columns` 命中目标行。
- `sidecar search --domain` 只能查询该 domain config 声明的 `primary_key` 和 `indexed_columns`；其他 domain 贡献的 union index 不会泄漏到当前 domain。
- `sidecar query --domain` 只能通过 read-only SQL 查询当前 domain-scoped views；非 `SELECT/WITH`、多语句、DDL/DML、`ATTACH`、`PRAGMA` 和超过最大 limit 的请求明确失败。
- 文件内容变化后旧 rows 被替换，新 rows 可查。
- manifest 移除文件后，当前 domain membership 被删除；如果没有其他 domain 引用该文件，对应 rows/indexes/refs 被 prune。
- 文件内容不变但 domain `indexed_columns` 变化时，derived indexes 会重建并可查询。
- 新 domain 首次引用已有文件且文件内容不变时，union config hash 会变化，并触发 derived indexes 和 anchors 重建。
- 同一文件被多个 domain 引用时，索引列按 active domain configs 取 union。
- domain 移除共享文件时，如果其他 domain 仍引用该文件，derived indexes 会按剩余 active configs 收缩重建，通用 path-like refs 不引入 domain 私有语义。
- 旧 `sidecar_ref` 在 row 内容变化后解析失败，不会误指向另一行。
- raw sidecar mode 和 graph-aware mode 有独立 fixture：raw mode 只校验 sidecar/ref/domain membership；graph-aware mode 额外校验 graph metadata 中的 `sidecar_db_id`、`source_sha256`、`sidecar_domain_config_hash` 和 `domain_file_config_hash`。
- graph-aware search fixture 覆盖不传 `--file` 的跨文件 search：每条命中 row 都能按 `file_key` 优先、`source_file` 兜底找回 graph source-file metadata 并完成 per-file hash 校验；缺失 source-file metadata 时明确失败。
- global debug 或 union-derived facts 请求中，`union_config_hash` 与 sidecar DB 不匹配时 resolve/search 明确失败并提示 rebuild；domain-scoped 查询访问自身声明字段时，其他 domain 导致的 union hash 变化不会让旧 graph 硬失效。
- `auto` policy fixture 覆盖小表 effective `graph`、大表 effective `sidecar`，并验证 `config_hash`、report 和 graph metadata 使用 effective policy；effective `graph` 文件不进入 `domain_files` 或 `sidecar_domain_config_hash`。
- 重复 header fixture 可通过 `row_values_json` 保留原始列，并可用 `--column-index` 精确搜索。
- manifest 中的列选择器支持字符串和 `{name, column_index}` 两种形式；重复 header 缺少 `column_index` 时明确失败。
- UTF-8、UTF-8-SIG、GB18030、UTF-16LE fixture 能稳定解析。
- 解码失败不会用 replacement 字符静默写入 sidecar。
- manifest 显式列出被 `.graphifyignore`、skip dirs 或 sensitive file policy 拒绝的文件时 fail closed。
- 未启用 sidecar 时，既有 `.tab` extractor 行为和测试保持兼容。

价值评测：

- graph-only 模式因 cap 找不到的高行号数据，sidecar 模式可以找到。
- sidecar 模式 graph node/edge 数量显著低于全量 row/cell 入图。
- query 命中 `sidecar` graph anchor 后，可以通过 `sidecar_ref` 回到完整原始行。
- update/prune 后没有旧事实残留。
- generic TSV fixtures 不依赖 JX3 字段名也能工作。
- path-like 字段能形成通用 refs，不依赖 JX3 业务规则。
- report stats 能说明 sidecar rows、indexes、refs 和 warnings 的规模。

JX3 smoke：

- 使用精简 `skills.tab`、`Buff.tab`、`recipeSkill.tab`、`CoolDownList.tab`、`skill.txt`、`buff.txt` fixture 验证 skill-core 关键形态。
- 全量 JX3 仓库只做手工或 smoke 验证，不进入常规测试套件。
- 验证高行号 `SkillID`、`BuffID` 等不再因为 graph cap 丢失。

建议 fixture 布局：

```text
tests/fixtures/sidecar/generic/
tests/fixtures/sidecar/relationships/
tests/fixtures/sidecar/jx3-mini/
```

## Implementation Touchpoints

Phase 0.5 至少涉及以下代码边界，拆任务时不应把 sidecar 只当成 extractor 内部改动：

- manifest parser：读取 `tabular_policy`、列选择器、`domain_id`，忽略未知字段，并处理 `auto` 到 effective policy 的决策。
- file detection：把 `.tab`、TSV-like `.txt` 和 `.tsv` 标记为 tabular structured extractor 输入。
- shared decoder：统一 `.tab`、TSV-like `.txt`、`.tsv` 的 encoding、canonical raw line 和 `line_hash`。
- sidecar store：SQLite schema、schema migration/version check、WAL、busy timeout、repo-level single-writer lock、foreign key self-check、transactional update/prune。
- graph writer：写入 graph-level sidecar metadata、source-file/table/column metadata、`sidecar` row-derived metadata、stable `sidecar_ref` 和 graph-aware validation 所需 hash。
- CLI/API：实现 `sidecar resolve/search/query`，区分 raw sidecar mode 和 graph-aware mode，并支持 `--graph` 定位 metadata；`query` 只作为只读 Agent SQL/debug 接口。
- report writer：输出 current snapshot、last run delta、auto decision、rejected files、lock/busy 和 mismatch diagnostics。
- watch/update：普通 directory watch 只发出 manifest rebuild signal；manifest build/update 才维护 shared sidecar。
- tests/evals：覆盖 large table、encoding、duplicate header、auto policy、raw vs graph-aware validation、update/prune、domain-scoped union isolation 和 JX3 smoke。

## 与 Phase 0 的关系

Phase 0 负责 manifest-driven domain build/update 和精确 output directory。Phase 0.5 建议在 Phase 0 主路径稳定后落地。

Phase 0.5 依赖：

- manifest mode 能读取调用方提供的 file list。
- manifest mode 能忽略未知字段。
- domain graph 能写入精确 output directory。
- shared source decoding 能覆盖 tabular 文件。

Phase 0.5 会扩展 Phase 0 的 manifest file admissible gate。Phase 0 只要求 code-like AST/code extractor 文件可进入 manifest build；Phase 0.5 需要允许 `code-like extractor` 和 `tabular structured extractor` 两类文件进入 manifest mode。对应的 detect、watch、extract 和 manifest validation 必须把 `.tab`、TSV-like `.txt` 和 `.tsv` 识别为 tabular structured extractor 输入，而不是在进入 sidecar 前按 unsupported 文件拒绝。

这里的 watch 只负责识别 tabular structured extractor 输入和触发 rebuild 信号；第一版不要求普通 directory watch 在缺少 manifest context 时写 sidecar。

Phase 0.5 不应反向扩大 Phase 0 范围。Phase 0 只需要保留 manifest metadata 扩展空间，不需要提前实现 sidecar DB 或 tabular file gate 扩展。

## 验收

Phase 0.5 完成条件：

- manifest 可按文件启用 `tabular_policy`。
- `sidecar` policy 能把 tabular 文件完整写入 shared SQLite sidecar，并写入旧 `hybrid` 等价的 graph anchor/path/ref skeleton。
- sidecar DB 默认位于 active Graphify output 下的 `sidecar/tabular.sqlite`，并支持明确一致的 `--sidecar-db` override。
- graph output 记录结构化 `sidecar_db_hint`，使 graph-aware `sidecar resolve/search/query` 能从 graph metadata 定位候选 sidecar DB，再用确定性的 `sidecar_db_id`、repo identity、source hash 和 sidecar domain config hash 校验。
- graph output 记录 `domain_id`，使 wrapper 或后续工具能默认按 domain scope 调用 sidecar resolve/search/query。
- graph output 记录 `sidecar_domain_config_hash`，sidecar-active source-file metadata 记录 `domain_file_config_hash` 和 `union_config_hash`，`sidecar` row-derived metadata 额外记录 `sidecar_ref`；domain-scoped resolve/search 硬校验 sidecar domain config hashes，global debug 或 union-derived facts 请求才硬校验 `union_config_hash`。
- 删除并重建 sidecar DB 后，确定性的 `sidecar_db_id` 保持不变；`sidecar_instance_id` 可以变化，但不能导致旧 graph resolve 失败。
- sidecar DB 记录并校验 repo identity；不同 repo 不能误用同一个 sidecar DB。
- 大表不再因为 graph extractor cap 丢失 sidecar rows。
- sidecar graph 包含可 resolve 的稳定 `sidecar_ref`，格式不依赖 SQLite 自增 row id。
- `graphify sidecar resolve` 能返回准确原始行、JSON 行数据和 ordered row values。
- `graphify sidecar resolve --domain` 只返回该 domain config 可见的 indexed values 和通用 path-like refs；不传 `--domain` 时才是 global debug resolve。
- `graphify sidecar search` 能按索引字段命中目标行。
- `graphify sidecar search --domain` 只允许查询该 domain config 声明的可索引列；请求 union index 中由其他 domain 贡献的列时明确失败。
- `graphify sidecar query --domain` 能在只读 connection 上执行单条 `SELECT/WITH`，默认查询 current-domain views，输出 JSON envelope，并对 unsafe SQL、缺少 domain scope 和超限结果明确失败或标记 truncated。
- 文件变更时 sidecar 整表重建。
- 文件未变但 domain tabular config 变化时，索引按 active domain configs 的 union 重建。
- domain membership 删除导致 union config 收缩时，索引按剩余 active configs 重建，通用 path-like refs 不引入 domain 私有语义。
- `config_hash` 和 `union_config_hash` 使用 canonical JSON，包含 schema/parser version、declared policy、effective policy、primary key、indexed columns 和 anchor columns。
- `sidecar_domain_config_hash` 使用当前 domain effective `sidecar` file config hashes 的 canonical aggregate hash；effective `graph` 文件不参与该 hash；output directory 在 repo 外时 manifest 必须显式提供 `domain_id`。
- manifest column selector 使用 1-based `column_index`，重复 header 必须显式指定列序号。
- graph output 记录 `sidecar_db_id` 和 `source_sha256`，并且 DB ID、source sha、sidecar domain config hash 或 row line hash 不匹配时 graph-aware resolve/search 明确失败。
- 当前 domain 的 `domain_files` membership 按 manifest 中 effective `sidecar` 文件集合整集合替换，移除或变成 effective `graph` 的文件不会残留在该 domain sidecar scope 中。
- manifest 移除文件或文件变成 effective `graph` 时删除当前 domain 的 sidecar membership，并在没有其他 domain 引用时 prune 对应记录。
- 普通 directory watch 在缺少 manifest context 时不会直接写 sidecar；tabular 变化只触发需要 manifest rebuild 的信号。
- manifest 文件过滤继承 `.graphifyignore`、skip dirs 和 sensitive file policy；被拒绝的显式 manifest 文件 fail closed 并进入 report。
- `row_values_json` 能保留原始列顺序、空 header 和重复 header 信息。
- sidecar report stats 能展示 sidecar DB path、schema version、files、tables、rows、indexes、refs、warnings、rejected files、lock/busy 和 prune count。
- sidecar SQLite connection 启用并校验 `PRAGMA foreign_keys=ON`，cascade 约束未生效时 fail fast。
- shared sidecar DB 使用 repo-level single-writer lock，SQLite `WAL` 和 `busy_timeout`；resolve/search/query 走只读 connection。
- 大表整表重建的删除路径有 child FK 索引覆盖，避免 SQLite 级联删除退化成全表扫描。
- sidecar report stats 区分 current snapshot 和 last run delta。
- schema version 不兼容时明确报错并提示 rebuild。
- 未启用 sidecar 的 directory 和 manifest graph extraction 保持兼容。
- 通用 fixtures 覆盖 large table、encoding、search、resolve、update 和 prune。
- JX3 mini/smoke 证明高行号业务表实体可以被查到，且 graph 规模不因全量表格事实入图而膨胀。

## 暂停门禁

Phase 0.5 设计落地前，需要先确认：

- Phase 0 manifest/output-dir 主路径已经稳定。
- sidecar DB 作为 Graphify generated artifact，而不是 JX3 wrapper 私有 DB。
- 第一版只覆盖 tabular sidecar，不覆盖 `.ini` sidecar。
- 第一版只覆盖 `.tab`、TSV-like `.txt` 和 `.tsv`，不覆盖 `.csv` sidecar。
- 第一版不做全量 cell 表。
- 第一版不修改 `graphify query` 的默认展开行为。
- JX3 专用关系规则留在 JX3 wrapper 或 domain adapter。
