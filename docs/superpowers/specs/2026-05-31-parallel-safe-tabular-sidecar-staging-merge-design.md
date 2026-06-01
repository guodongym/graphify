# Graphify Parallel-Safe Tabular Sidecar Staging and Merge 设计

## 背景

Graphify manifest mode 已经支持把 effective `tabular_policy: "sidecar"` 的表格文件写入共享 SQLite sidecar：

```text
<active_graphify_output>/sidecar/tabular.sqlite
```

domain graph 只保留 file/table/column/anchor/path-ref skeleton 和 `sidecar_ref`，完整行数据通过 `graphify sidecar search`、`resolve` 或受限 `query` 读取。

这个模型在串行 domain build 下是正确的，但不是并行写入模型。JX3 wrapper 如果直接用 `--jobs 4/8` 并发跑多个 Graphify 子进程，多个 domain build 会同时竞争同一个 canonical `tabular.sqlite`。当前 Graphify 有 `SidecarWriteLock`、WAL 和 `busy_timeout`，可以减少部分 SQLite 等待问题，但结构上仍然是多个进程争同一个 writer；而且当前 `update_sidecar()` 在 sidecar 写锁路径中包含 parse、domain membership 替换、rows/refs 写入、index rebuild 和 generation bump，锁窗口过大。

本设计的目标不是让 JX3 wrapper 接管 sidecar，而是在 Graphify 本体补齐 parallel-safe sidecar 写入模型：domain 本地并行 staging，Graphify 单写合并到 canonical shared sidecar。

## 当前状态

当前 manifest sidecar build 关键路径：

- `graphify.code_build_runner.build_code_graph()` 先做 code extraction，然后调用 `update_sidecar()`，再从 sidecar 读取 projection 合并进 graph payload，最后 build graph、cluster、report/export、写 domain state。
- `graphify.tabular_sidecar.update_sidecar()` 对目标 sidecar DB 获取写锁，确保 schema/repo identity，替换当前 domain membership，按文件 hash/config 判断是否重建 rows/refs/indexes，最后 bump `sidecar_generation`。
- `source_files` 是 canonical per source file；`domain_files` 和 `indexed_values` 带 domain 维度。
- 多个 domain 可以共享同一个 source file，并用不同 `primary_key` / `indexed_columns` 配置产生各自的 domain indexes。
- `union_config_hash` 反映同一 source file 当前所有 active domain configs。
- 从一个 domain 移除共享文件时，不能 prune 仍被其他 domain 使用的 canonical rows。

这些语义决定了 sidecar 并行方案不能简单变成“每个 domain 永久一个 DB”，也不能把 staging DB 直接 append 到 canonical DB。staging 必须被视为 domain delta，最终由 Graphify 合并成 canonical shared sidecar。

## 目标

- 支持 sidecar-active domain 的并行 build，避免多个并发 domain 直接写同一个 canonical sidecar。
- 保留现有 shared sidecar 查询模型，`search`、`resolve`、`query` 继续读 canonical `tabular.sqlite`。
- sidecar staging 格式、canonical merge、prune、schema 校验、query consistency 都由 Graphify 拥有。
- JX3 wrapper 和其他调用方只负责 domain 调度与能力探测，不直接管理 sidecar DB。
- `jobs=1` 与 `jobs=N` 产出的 domain graph metadata、sidecar search/resolve 结果保持一致。
- staging 失败或 domain build 失败不能污染 canonical sidecar。
- 补充 machine-readable build trace，让后续能判断慢点在 parse、staging write、merge wait/write、projection、graph build、cluster 还是 report/export。
- MVP 先把并发安全与可观测性做实，再继续优化写入速度。

## 非目标

- 不把“共享 sidecar + jobs>1 fail fast”作为本阶段的独立交付。当前下一步直接做 staging/merge 并行模型。
- 不把每个 domain 的 staging DB 变成永久查询态。
- 不让 JX3 wrapper 或其他调用方拼装/合并 sidecar DB。
- 不把 SQLite `busy_timeout`、WAL 参数调优作为核心方案。
- 不重做 `graphify sidecar query`，它继续是受限 read-only debug/eval surface。
- 不引入 JX3 专用业务语义。

## 总体设计

sidecar 写入拆成三个阶段。

### 执行边界

MVP 不新增 Graphify multi-domain runner。Graphify 仍然以单次 manifest build/update 为边界执行一个 domain；JX3 wrapper 或其他调用方负责把多个 domain 进程并行调度。

Graphify 本体负责提供 per-domain `staging-merge` 写入模式、canonical merge、projection、metadata、build trace 和 capability 输出。wrapper 只能根据 capability 决定是否并行调度 sidecar-active domains，不能直接写 staging DB、合并 canonical DB 或解释 Graphify sidecar 内部 schema。

因此：

- Graphify domain trace 记录当前进程的 sidecar mode、stage timings 和本进程内部 worker 信息。
- wrapper sync report 记录全局 `jobs`、actual jobs、Graphify capability payload 和每个 domain 的 `sidecar_mode`。
- `jobs=1` 与 `jobs=N` 等价验证由 wrapper 或集成测试驱动多个 domain build 进程完成；Graphify 的责任是保证每个 per-domain `staging-merge` 操作可并发、安全且幂等。

### Canonical DB 和 staging 路径解析

canonical sidecar DB 继续遵守既有路径契约：

1. 如果 CLI 显式传入 `--sidecar-db <path>`，canonical DB 使用该路径。
2. 否则 canonical DB 使用 `<active_graphify_output>/sidecar/tabular.sqlite`。

staging DB 默认放在 canonical DB 同级的 staging 目录，避免 `--sidecar-db` override 时 staging 与 canonical 分散到不同产物根：

```text
<canonical_db_parent>/staging/<run_id>/<safe_domain_id>-<attempt_id>.sqlite
```

其中：

- `run_id` 由当前 Graphify 进程生成，默认使用时间戳加随机后缀。
- `attempt_id` 每次 domain build 生成一个 UUID，保证同一 domain 并发重跑不会覆盖彼此 staging DB。
- `safe_domain_id` 是用于文件名展示的 slug；真实 identity 仍以 staging `meta.domain_id` 为准。
- 文件名必须追加 `domain_id` hash，避免不同 domain slug 后碰撞。

staging DB 和 canonical DB 一样都是 Graphify generated artifacts，不能提交到 git。

### 阶段一：domain-local staging

每个 domain build 把 sidecar-active tabular 数据写入独立 staging DB：

```text
<canonical_db_parent>/staging/<run_id>/<safe_domain_id>-<attempt_id>.sqlite
```

staging DB 只属于当前 domain build，可以被覆盖、丢弃或按 debug 策略保留。它不是 query source of truth。

可以并行执行的工作：

- code extraction
- tabular parse
- row / column / ref / indexed-value payload 生成
- staging DB 写入
- 不依赖 canonical sidecar metadata 的 graph build 前置工作

### 阶段二：canonical merge

domain staging 成功后，Graphify 把 staging DB 合并进 canonical sidecar：

```text
<canonical_db>
```

只有 canonical merge 阶段获取 canonical sidecar 写锁。写锁窗口只覆盖 canonical DB 读写，不覆盖 tabular parse 或 payload 生成。MVP 中写锁使用阻塞等待（无超时），后续应给 `SidecarWriteLock` 增加可选 `timeout` 参数，超时后让 domain build 以可重试错误失败并记入 failed trace，避免高并发下某个 domain 被饿死。

merge 是 Graphify 内部能力，不暴露给 wrapper 直接操作。

### 阶段三：domain output finalization

domain build 只有在以下条件都满足后才能写成功 state：

- staging 成功。
- canonical merge 成功。
- domain graph 的 sidecar projection 来自 canonical sidecar，并且 metadata 与 canonical sidecar generation/domain config hash 匹配。
- `graph.json`、`GRAPH_REPORT.md`、`.graphify_analysis.json`、labels、`.graphify_state/update-state.json` 原子写入成功。

如果 staging 成功但 merge 失败，domain build 失败，canonical sidecar 保持旧状态。如果 merge 成功但 graph finalization 失败，canonical sidecar 已提交，但 domain output 不能标记成功；下一次重跑同一 domain 必须能幂等收敛。

## Staging DB 契约

MVP staging DB 优先复用现有 sidecar 逻辑 schema，降低实现复杂度：

- `meta`
- `source_files`
- `domain_files`
- `tabular_tables`
- `tabular_columns`
- `rows`
- `indexed_values`
- `tabular_refs`

staging DB 额外在 `meta` 中记录：

```text
schema_version
staging_schema_version
sidecar_kind = tabular
repo_key
repo_root
domain_id
parser_version
graphify_version
created_at
run_id
attempt_id
```

staging 语义：

- `domain_files` 只包含当前 domain 的 staged membership。
- 如果当前 domain 没有 sidecar-active files，staging DB 也必须能表达 empty membership。canonical merge 仍然要根据“旧 canonical membership + 新空 membership”删除该 domain 的旧 sidecar membership，并按共享引用情况 prune orphan files。
- `indexed_values` 只包含当前 domain 配置要求的 indexes。
- `source_files.union_config_hash` 在 staging 中始终为空字符串，不是 canonical 值；canonical DB 中如果其他 domain 也使用同一 source file，merge 时必须重算。staging DB 的 `union_config_hash` 不应被任何消费方直接使用。
- staging row IDs 不稳定，不能进入 `graph.json` metadata 或对外 API。
- staging DB 是 domain delta，不是可直接 append 的最终 DB。

## Canonical Merge 算法

新增 Graphify 内部函数：

```python
merge_domain_staging(domain_id, staging_db, canonical_db) -> SidecarMergeStats
```

语义步骤：

1. 打开 canonical sidecar，复用现有 schema 和 repo identity 校验。
2. 只读打开 staging DB，校验：
   - `repo_key` 一致
   - `schema_version` 兼容
   - `domain_id` 匹配
   - `parser_version` 支持：staging `parser_version` 必须不低于 canonical 中同一 source file 的已有 `parser_version`。如果 staging 使用的 parser 版本低于 canonical（例如旧版 Graphify 生成的 staging DB），merge 拒绝并提示重新 staging。MVP 只做主版本号兼容检查，不做 minor 版本比较。
3. 对 canonical DB 执行 `BEGIN IMMEDIATE`。
4. 计算 affected files：
   - canonical 中旧的 `domain_files WHERE domain_id = D`
   - staging 中新的 `domain_files WHERE domain_id = D`
5. 删除 canonical 中 domain `D` 的旧 `domain_files`。
6. 删除 canonical 中 domain `D` 的旧 `indexed_values`。
7. 对 staging 中的 source files upsert canonical `source_files` stub。empty membership staging 跳过这一步。
8. 插入 domain `D` 的新 `domain_files`。empty membership staging 插入 0 行。
9. 对每个 affected file：
   - 读取 canonical 当前 active domain config hashes。
   - 如果没有 active domains，删除 canonical `source_files`，依赖 cascade prune tables/rows/indexes/refs。
   - 如果 source file 是新增的（canonical 中此前不存在），用 staging 内容填充 canonical `tabular_tables`、`tabular_columns`、`rows`、`tabular_refs`。
   - 如果 staging content hash 与 canonical hash 不同，用 staging 内容替换 canonical `tabular_tables`、`tabular_columns`、`rows`、`tabular_refs`。
   - 重算 canonical `union_config_hash`。
   - 当 rows 变化或 union config 变化时，重建该 file 所有 active domains 的 `indexed_values`。
10. 只有 canonical DB 发生逻辑变化时才 bump `sidecar_generation`。
11. commit。
12. 当 merge stats 超过阈值或配置要求时，执行受控 WAL checkpoint。

merge 必须幂等：同一个 staging DB 在已成功 merge 后再次 merge，不能产生重复逻辑数据；如果 canonical 内容没有变化，不能 bump generation。幂等判定基于 domain membership fingerprint（`domain_files` 的 config_hash 集合 + 每个文件的 sha256、parser_version、encoding），而不是简单的"有无 membership"检查。

## Graph Projection 和 Metadata

MVP 中，domain graph projection 必须在 canonical merge 成功后从 canonical sidecar 读取，而不是从 staging DB 读取。这样 `graph.json` 中的 sidecar metadata 与 `graphify sidecar search/resolve/query` 实际读取的 DB 一致。

现有 metadata 继续保留：

- `sidecar_db_id`
- `sidecar_instance_id`
- `sidecar_generation`
- `repo_key`
- `repo_root`
- `domain_id`
- `sidecar_domain_config_hash`
- source-level sha/config metadata

新增 metadata：

- `sidecar_mode`: `shared-serial` 或 `staging-merge`
- `staging_run_id`
- `staging_attempt_id`
- `sidecar_merge_generation_before`
- `sidecar_merge_generation_after`

如果 canonical merge 成功但 graph projection/export 失败，canonical sidecar 可以包含已提交数据，但 domain output 仍失败。下一次重跑同一 domain 时，merge 必须识别 no-op 或正确收敛，不能重复写逻辑数据。

## Build Trace

Graphify 应写出机器可读 trace：

```text
<domain-output>/.graphify_state/build-trace.json
```

最小 schema：

```json
{
  "schema_version": 1,
  "domain_id": "skill-core",
  "sidecar_mode": "staging-merge",
  "process_workers": 8,
  "stages": {
    "code_extract_ms": 0,
    "tabular_parse_ms": 0,
    "sidecar_stage_write_ms": 0,
    "sidecar_merge_wait_ms": 0,
    "sidecar_merge_write_ms": 0,
    "sidecar_projection_ms": 0,
    "graph_build_ms": 0,
    "cluster_ms": 0,
    "report_export_ms": 0
  },
  "cache": {
    "ast_hits": 0,
    "ast_misses": 0,
    "tabular_content_unchanged": 0,
    "tabular_content_changed": 0
  },
  "sidecar": {
    "files_staged": 0,
    "files_merged": 0,
    "files_pruned": 0,
    "rows_staged": 0,
    "rows_merged": 0,
    "rows_pruned": 0,
    "indexed_values_merged": 0,
    "refs_merged": 0
  }
}
```

`GRAPH_REPORT.md` 可以摘要展示 trace，但自动化、wrapper 和性能分析应优先读取 JSON。

失败场景也需要尽量保留 trace。Graphify 如果在写正式 domain state 前失败，应优先写：

```text
<domain-output>/.graphify_state/build-trace.failed.json
```

如果失败发生在 output directory 初始化前，至少在 stderr 中输出 compact JSON 摘要，包含 `domain_id`、`sidecar_mode`、已完成 stages 和 error class。

## Capability 契约

Graphify 新增机器可读能力输出，避免 wrapper 依赖版本字符串猜测：

```bash
graphify capabilities --json
```

建议 payload（最终态，staging-merge 集成完成后；初始部署时 `default_write_mode` 应为 `"shared-serial"`）：

```json
{
  "schema_version": 1,
  "tabular_sidecar": {
    "schema_version": 1,
    "write_modes": ["shared-serial", "staging-merge"],
    "default_write_mode": "staging-merge",
    "supports_domain_staging_merge": true,
    "supports_build_trace": true
  }
}
```

wrapper 侧语义：

- 如果 `write_modes` 包含 `staging-merge`，sidecar-active domains 可以并行调度。
- 如果只包含 `shared-serial`，sidecar-active domains 应串行调度。
- sync report 记录 Graphify capability payload、`sidecar_mode` 和实际 jobs。

这个 capability 是兼容契约，不是主要安全机制。主要安全机制仍是 Graphify staging/merge 写入模型。

## 失败处理

### staging 失败

canonical sidecar 不变。domain output 不写成功 state。

### merge commit 前失败

canonical transaction rollback。domain output 不写成功 state。

### merge 成功但 graph finalization 失败

canonical sidecar 已提交，domain output 失败。重跑同一 domain 必须幂等修复。

### merge 期间进程崩溃

依赖 SQLite transaction 语义，canonical DB 处于旧状态或新提交状态之一。下一次运行必须先做 sidecar identity、domain config hash 和 source metadata 校验。

### stale staging DB

staging DB 不是 authoritative state。默认可清理当前 run 之外的旧 staging DB；debug retention flag 可以保留失败 staging DB 供排查。

清理规则必须避免误删其他活跃进程的 staging DB：

- 当前进程只能无条件删除自己的 `attempt_id` staging DB。
- 清理历史 staging 时，只能删除超过 TTL 且没有 lock/marker 的 staging attempt。
- cleanup 失败不能影响 canonical sidecar 的正确性，只能作为 warning 记录。

## 后续性能优化

staging/merge MVP 解决并发安全和调度解锁。之后继续优化写入速度：

- parse 和 row/index/ref payload 生成移出 canonical DB 写锁。
- rows、columns、refs、indexed_values 使用 `executemany`。
- source hash、parser version、domain config 未变时跳过 rows rebuild。
- 只重建 affected file/domain 组合的 indexes。
- 在不破坏 domain-level atomicity 的前提下引入受控分批。
- 大 merge 后执行受控 WAL checkpoint。

## 实施顺序

1. 给现有 shared-serial sidecar path 增加 build trace。
2. 增加 `graphify capabilities --json`，初始 `default_write_mode` 报告 `shared-serial`，待 staging-merge 集成完成后切换为 `staging-merge`。
3. 增加 sidecar path resolver，覆盖 default canonical DB、`--sidecar-db` override、staging run/attempt path。
4. 增加 domain staging sidecar writer，支持非空 membership 和 empty membership。
5. 增加 `merge_domain_staging()`，先覆盖单 domain 新增和更新。
6. 覆盖 shared file、多 domain config、domain removal/prune、idempotent merge。
7. manifest sidecar build 支持 `staging-merge` mode。
8. projection 改为 merge 成功后从 canonical sidecar 读取。
9. graph/report metadata 增加 `sidecar_mode`、staging attempt 和 merge generation。
10. 下游 wrapper 基于 capability 允许并行 sidecar-active domains。
11. 做 `jobs=1` 与 `jobs=4/8` 等价验证后再调高默认并发。

## 测试计划

单元测试：

- staging writer 能写出 domain-local sidecar rows、refs、indexes。
- staging writer 能写出 empty membership staging DB。
- canonical merge 能新增一个 domain。
- canonical merge 能更新已有 domain 且不产生重复数据。
- canonical merge 删除 domain 时只 prune orphan files。
- 两个 domain 共享一个 source file 时保留各自 index config。
- 共享文件内容变化时，canonical rows 和所有 active domain indexes 正确刷新。
- identical staging merge 是 no-op，不 bump generation。
- repo key、schema、domain mismatch 时 merge 明确失败。
- `--sidecar-db` override 时 canonical DB 和 staging DB 使用同一 override 语义下的路径。
- staging attempt path 对特殊字符 domain_id 安全，且同一 domain 并发 attempt 不互相覆盖。

集成测试：

- `jobs=1` 与 `jobs=4` 产出等价 domain graph sidecar metadata。
- serial 与 parallel build 后 `graphify sidecar search` 返回一致。
- parallel build 产出的 `sidecar_ref` 可被 `graphify sidecar resolve` 解析。
- staging 失败不改变 canonical sidecar counts。
- merge 失败不写成功 domain state。
- merge 成功但 graph finalization 失败后，重跑可以修复。
- domain 从 sidecar-active 变成 graph-only 后，canonical DB 删除该 domain membership 并只 prune orphan files。
- 失败 build 尽量写出 `build-trace.failed.json` 或 stderr JSON 摘要。

JX3 验证：

- 选一个包含共享 tabular source file 的小 domain 集合做 serial/parallel 对比。
- 对关键列执行 search/resolve 抽样一致性检查。
- 重跑 sync 后 unchanged domains 保持 unchanged，不重复写逻辑数据。
- 记录 `sidecar_mode=staging-merge`、actual jobs、各阶段耗时。

## 验收

- sidecar-active domain 的并发本地工作不再直接写 canonical `tabular.sqlite`。
- canonical sidecar 的 search/resolve 结果在 serial 和 parallel 构建中一致。
- domain graph metadata 指向 merge 后的 canonical sidecar generation。
- 失败 domain 不写成功 build state。
- staging 失败不会污染 canonical sidecar。
- domain 移除 sidecar files 后，旧 sidecar membership 会被 canonical merge 正确删除。
- `--sidecar-db` override 下 staging/merge/projection/search/resolve 使用同一 canonical DB 语义。
- 重复运行在 source hash 和 config 未变时不重复 rebuild 逻辑数据。
- build trace 能区分 parse、staging write、merge wait/write、projection、graph build、cluster、report/export 的耗时。
- JX3 wrapper 可以通过 Graphify capability 探测决定是否开放 `sync --jobs N`。
