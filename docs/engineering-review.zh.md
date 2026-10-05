# Rhizome 工程审查报告（v0.1.2）

> 工作量：S ≈ 半天以内，M ≈ 1–3 天，L ≈ 一周以上（均为审查者的估计）。编号对应各审查者的 finding id。

## 1. 总评

现在的 Rhizome 适合"在一个小库上试用"，还不能放心地日常使用。离日常使用还差三件事。

1. **数据没有可靠的退路。** 所有备份都和库放在同一块盘上，而且不会自动生成（D5）。`raw/` 并不能恢复你的人工决策和复习历史（D5、D30）。rebuild 遇到一条陈旧决策就会一直失败（D1），每次 rebuild 还会多存一份永不清理的整库副本，直到磁盘写满（D4）。
2. **多个进程会互相打架。** rebuild 会持有写锁几分钟到几小时，期间 UI、MCP 和 inbox 全都报 `database is locked`（C1）。app 重启后，Claude 的 MCP 会一直连不上（A3）。迁移过资料库或切换过模型后，各个进程看到的库和模型不一致（G1、M1、G5）。
3. **错误数据进来后改不掉。** 没有撤回、拒绝、编辑或撤销的入口（D2、A15）。没有 NLI 时，立场相反的 claim 会被自动合并（M3）。中国数据库的 accession 会被当成幻觉直接丢掉（N4）。

此外，第一个 schema 迁移（0002）发布之前，必须先修好迁移的原子性（U1、H2、T4）。

---

## 2. 必须先修（critical / high）

### A. 数据可恢复性与资料库完整性

#### 1. D5：没有定时备份，也没有异盘备份；"仅凭 L0 可重建一切"的说法不成立
- **问题**：`backup_database` 只在三种情况下被调用：手动 `rhz backup`、迁移前、rebuild 前。它只把 `rhizome.db` 复制到同盘的 `data_dir/backups`，nightly 从不备份。rebuild 只读 L1，从不读 `raw/`。HumanDecision、用户主题、ReviewLog 和 KV 调优值都不在 L0 里。
- **证据**：`backend/rhizome/db/session.py:133-139`、`backend/rhizome/jobs.py:152-169`、`backend/rhizome/config.py:165`、`backend/rhizome/pipeline/rebuild.py:43`。文档的承诺与实现不符：`docs/design.zh.md:356,430`、`README.md:21`，`docs/implementation.zh.md:39` 把备份标为 ✅。已用临时目录复现：把 `raw/` 重新导入新目录后，所有决策和用户主题都丢了，FSRS 状态被重置。
- **场景**：你按文档只把 `raw/` 同步到网盘。SSD 坏了，或者崩溃后 `rhizome.db` 损坏，几个月的合并、拒绝、主题整理和复习记录全部丢失。在新库上跑 `rhz rebuild` 只会报 `extractions: 0`。
- **修法**：
  1. 每天做一次 `backup_database(tag='daily')` 并轮转；启动时如果距上次备份超过 20 小时就补做一次。`backup_dir` 改成可配置，可以放到 `data_dir` 之外（关闭状态的副本可以放网盘，带 WAL 的活库不行）。在界面上显示上次备份时间。
  2. 每次提交决策后，把决策（可选再加 ReviewLog 评分）追加到 `raw/decisions/*.jsonl`，并提供一个幂等的 restore-decisions 重放。
  3. 修正 README 和 design.zh.md，写清楚 L0 到底覆盖哪些内容。
  4. 以后再补 `rhz reingest --from-raw`（见 D30）。
- **工作量**：M

#### 2. D1：任何一条陈旧决策都会让 rebuild 中止，而且之后每次都失败
- **问题**：在 `apply()` 中，`except DecisionError: raise` 写在通用的 `except Exception` 前面，而后者的注释恰恰是"陈旧决策绝不能打断 rebuild"。`_need()`、`_op_merge` 和 `_op_add_edge`（cannot link an entity to itself）都会抛 DecisionError，`rebuild()` 调用 `apply_all` 时没有任何保护。
- **证据**：`backend/rhizome/pipeline/decisions.py:88,112-116,175-176`、`backend/rhizome/pipeline/rebuild.py:54-56`、`backend/rhizome/pipeline/graph.py:213-218`。已用两种方式复现：(a) 先 add_edge，再 merge 同一对实体；(b) 在 claim key 上有决策，同时调低合并阈值（切换到 bge-m3 正是这种情况），结果报 `entity claim:... not found`。
- **场景**：在整理过的库上按文档执行 `rhz settings set embedder bge-m3` 和 `rhz rebuild`，rebuild 中止，traceback 里只出现实体名，看不出是哪条决策。Settings 里的重算按钮什么也不做；`DELETE /decision` 排队的 rebuild 会失败，所以撤销永远不生效。库本身靠回滚保持完好，但再也无法重新计算。
- **修法**：非 strict 模式下捕获 DecisionError，带上 `d.id` 写日志并返回 False，把跳过的决策 id 写入 rebuild 摘要或 KV `rebuild_warnings`，让 UI 能显示。`_op_add_edge` 和 `_op_edge_status` 在解析重定向后如果发现 src==dst，按 no-op 处理。加一条回归测试：先 add_edge、再 merge 同一对后，rebuild 仍然成功。
- **工作量**：S

#### 3. T14：环境变量优先于显式参数，pytest 会写进你的真实资料库
- **问题**：`settings_customise_sources` 返回 `(env, init)`，所以 `RHIZOME_*` 环境变量会盖过 `--data-dir`、`--snapshot` 和测试 fixture 显式传入的值。
- **证据**：`backend/rhizome/config.py:145`、`backend/tests/conftest.py:21`、`backend/rhizome/client.py:274`、`backend/rhizome/cli.py:32-37`、`backend/tests/test_cli_mcp.py:32`。已复现：设置 `RHIZOME_DATA_DIR` 后，pytest 把 rhizome.db 和 backups 写进了"真实"目录，还把 settings.json 改成 `offline=true`、`zh_CN`。完整跑一遍测试会在那里执行 `rebuild --no-backup`。同样的优先级也会让残留的 `RHIZOME_DATABASE_URL` 把 snapshot 重定向到别处。
- **场景**：你既是维护者又是唯一用户，shell profile 里设了 `RHIZOME_DATA_DIR`，然后跑了一次 pytest。
- **修法**：加一个 autouse fixture，清掉所有 `RHIZOME_*` 并设置 OFFLINE；在 settings fixture 里断言 data_dir。config.py 的优先级改为 init > env > file，并给 settings.json 一个独立的 source；或者在构造之后用 `model_copy` 应用 snapshot 和 `--data-dir`。加回归测试。
- **工作量**：S

#### 4. D4：每次 rebuild、撤销和迁移都写一份整库副本，从不清理；备份失败会让 rebuild 失败
- **问题**：`backup_database` 写出 `rhizome-{tag}-{ts}.db`，没有任何代码列出或删除旧备份。rebuild 默认 `backup=True`，撤销（`app.py:380`）和 Settings 按钮发的都是 `{}`。enqueue 不去重，所以撤销 5 次就有 5 份整库副本。Embedding 和 VectorCache 各存一份向量，5k 篇论文时单份副本有几百 MB。磁盘写满时残留的 `.tmp` 不会被清理。`move_data_dir` 还会把 backups/ 一起复制过去。备份失败会把整个 job 标记为失败，而 UI 从不轮询 job 状态。
- **证据**：`backend/rhizome/db/session.py:128-139,148-150,163-166`、`backend/rhizome/pipeline/rebuild.py:30-32`、`backend/rhizome/jobs.py:142`、`backend/rhizome/api/app.py:380`、`backend/rhizome/system.py:174`、`frontend/src/pages/Settings.tsx:157`。
- **场景**：第 30 天，`%APPDATA%\Rhizome\backups`（漫游配置文件所在的系统盘）里堆着几十份几乎相同、每份几百 MB 的副本。磁盘写满后，每次 rebuild 都在备份这一步失败，撤销和模型切换都不再生效，界面上什么也看不到。
- **修法**：每次写备份后调用 `prune_backups`：pre-rebuild 保留最新 3 份，pre-<rev> 保留最新约 5 份，手动备份全部保留，同时删除残留的 `*.tmp`。几分钟内已有 pre-rebuild 备份时就跳过；至少要改成尽力而为、失败只警告。合并排队中的 rebuild。在 Settings 和 `rhz diag` 里显示备份数量、总大小和位置。
- **工作量**：S

#### 5. U1：迁移既不原子，也没有跨进程串行；迁移中途崩溃后再也启动不了
- **问题**：`init_db` 不加锁就读 `current_revision`，然后备份，再在 `engine.begin()` 里跑 `command.upgrade`。pysqlite 在 DDL 前不发 BEGIN，所以 CREATE、ALTER 和 batch 表复制会自动提交，而 `alembic_version` 却被回滚。连接监听器设置了 `foreign_keys=ON`，batch 方式删除 entity 表时会级联删除关联行。
- **证据**：`backend/rhizome/db/session.py:156-171`、`backend/rhizome/migrations/env.py:15`、`backend/rhizome/client.py:116-126`、`backend/rhizome/api/app.py:130`、`backend/rhizome/mcp_server.py:102-113`。已验证：`engine.begin()` 里的 CREATE TABLE 在 rollback 后依然存在。在空目录上同时跑 3 个 `init_db`，5 次中有 3 次报 `table access_log already exists`。
- **场景**：0.2 带来迁移 0002。大库第一次启动时开始迁移，90 秒超时后用户关掉窗口、taskkill，或者笔记本断电。DDL 只执行了一半而 revision 还是旧的，之后每次启动都报 `already exists`，每次重试还会再写一份整库备份。另一种情况是 Claude Desktop 的 MCP 和 app 同时启动，互相竞争后崩溃。
- **修法**：用专门的迁移 engine（NullPool、`isolation_level=None`、`PRAGMA foreign_keys=OFF`），执行 `BEGIN IMMEDIATE`；拿到锁后重新读 revision，已经是 head 就直接返回；备份也在锁内做。通过 `cfg.attributes` 把连接传给 Alembic，COMMIT 前跑 `PRAGMA foreign_key_check`，并设置较长的 busy_timeout。`make_engine` 不用改。加测试：一个必然失败的假 0002 不改变 schema；两个进程同时启动时只产生一份备份，并且都成功。
- **工作量**：S（**必须在任何 0002 之前完成**）

#### 6. G7：保存设置会写入整个有效模型，默认值在第一次保存时被冻结
- **问题**：`model_dump` 会写出全部 17 个字段，包括所有阈值、sidecar 的随机端口和来自环境变量的值。`extra='ignore'` 又会在加载时丢掉新版本才有的 key。
- **证据**：`backend/rhizome/config.py:199-202,110`、`backend/rhizome/api/app.py:457-466`、`backend/rhizome/cli.py:38-40,79-81,494-509`、`frontend/src/App.tsx:41`。已验证：切换一次语言就把 `port=54321` 和 `offline=true` 写进了文件。文档自己说明这些阈值尚未校准、以后会改。
- **场景**：第一天切换一次语言，所有阈值就被冻结，0.2 校准后的默认值永远到不了你这里。临时设过一次 `RHIZOME_OFFLINE`，OpenAlex 就被永久关闭。`rhz serve` 会绑定一个早已过期的随机端口。
- **修法**：用 `update_settings(patch)` 取代 `save_settings`：读原始 JSON，只深度合并改动的 key，丢掉等于类默认值的项，保留未知 key，加 `settings_version`，校验后原子写入。PATCH 和 CLI 只发送增量，`null` 表示恢复默认。对 v0.1 的文件做一次迁移，删掉等于 0.1 默认值的 key。
- **工作量**：M（越晚修，被冻结默认值的文件越多）

### B. 多进程、连接与模型一致性

#### 7. C1：rebuild 等任务持有 SQLite 写事务几分钟到几小时；"只读"接口也会写库
- **问题**：`rebuild()` 先删除 Edge、Embedding、Alias、Work 和 Entity，再重新物化，直到 `run_next` 的 `session_scope` 退出时才提交；retro_tag 和 nightly 也都是单个事务。GET 路径也会写库：entity 和 topic 页面会调用 `touch()`，新的搜索查询会在 `embed_texts` 里 merge VectorCache。这些写操作等满 30 秒 busy timeout 后返回 500。`ingest_file` 遇到 `database is locked` 时会把文件移到 `error/`，而且有一个测试把这个行为固定了下来。每次撤销都排一次全量 rebuild，enqueue 不去重，`recover_stale_jobs` 会在启动时重新排队被中断的 rebuild。切换模型后在 CPU 上要跑几个小时，进程被杀时缓存写入一起回滚，下次从零开始。
- **证据**：`backend/rhizome/pipeline/rebuild.py:33-34`、`backend/rhizome/jobs.py:60-61,33-39`、`backend/rhizome/db/session.py:49`、`backend/rhizome/pipeline/ingest.py:198-204`、`backend/rhizome/services/recall.py:29-37`、`backend/rhizome/services/views.py:64-65`、`backend/rhizome/pipeline/graph.py:197`、`backend/rhizome/api/app.py:380`、`backend/tests/test_ingest.py:139-151`。
- **场景**：库里约 2,000 篇论文时撤销一次合并，或者切到 bge-m3 后点"重算"。接下来几分钟到几小时里，每个实体卡片、每次新搜索、每个 MCP 调用都卡 30 秒后报错；拖进 inbox 的文件进了 `error/`，报告还误导性地让你"修正字段"。合上笔记本会杀掉任务，下次启动从零开始。
- **修法**：
  1. 读路径不写库：查询向量用 `embed_texts(persist=False)` 加进程内 LRU；`touch()` 先缓存在内存里，由 worker 用短事务、短 busy_timeout 刷入。这样 WAL 下的读者永远不会被阻塞。
  2. rebuild 保持原子，但在破坏性删除之前分批预计算嵌入和 reranker 分数并提交，让持锁阶段只做 DB 操作，中断后重启的代价很小。
  3. 合并 rebuild：已有排队中的就复用，不再新排。
  4. 提供 `GET /jobs/active` 和横幅；长任务期间写接口返回 503 加 Retry-After。
  5. inbox 文件遇到锁就留在原地，由周期性重扫来重试。
  6. app 在运行时，`rhz rebuild` 通过 HTTP 排队。
- **工作量**：M

#### 8. A3：MCP 启动时一次性决定 client、端口和 token；app 重启后所有 Claude 工具都失效
- **问题**：`client()` 在进程生命周期内缓存 `connect()` 的结果，`warm_up` 在 `mcp.run()` 之前就调用了它。HttpClient 在构造时固定 `base_url` 和 bearer token，不会重试。桌面壳每次启动都换新端口和新 token。如果 Claude 先于 Rhizome 启动，MCP 整个会话都以 LocalClient 身份在进程内写库。
- **证据**：`backend/rhizome/mcp_server.py:28-36,100-117`、`backend/rhizome/client.py:39-55,269-291`、`desktop/src-tauri/src/main.rs:120-121`、`backend/rhizome/api/app.py:39-48`、`desktop/README.md:28`。已在实机复现：缓存的 client 抛 ConnectError，而重新 `connect()` 能成功。工具失败不会记到 Rhizome 自己的日志里，pip 安装的 `rhizome-mcp` 入口也不调用 `setup_logging`。
- **场景**：午休时关掉再打开 Rhizome，当天剩下的时间里每个 `rhz_*` 调用都返回 `[WinError 10061]`，没有任何地方提示要重启 Claude。或者 Claude 开机自启早于 Rhizome，MCP 整天在进程内写库，自己加载一份模型和向量缓存。
- **修法**：用按 (server.json 的 url, token) 内容为键的解析器取代单例：内容变化时重新解析；缓存的是 LocalClient 而 server 已经起来时也重新解析。工具体包一层 `_call`：遇到 `httpx.ConnectError` 或 401 时丢弃 client、重新 `connect()` 并重试一次；超时和 5xx 不重试，因为 ingest 和 decide 不是幂等的。返回可读的双语错误，每次重连都记录模式。`warm_up` 里的 native import 保留。`rhizome-mcp` 改用和 `rhz mcp` 相同的 bootstrap。加测试：两次调用之间切换 server 和 token。
- **工作量**：S

#### 9. G1：写进 `claude_desktop_config.json` 的 `RHIZOME_DATA_DIR` 在迁移库后过期，MCP 静默写进旧副本
- **问题**：data_dir 不是平台默认值时，`claude_desktop_snippet` 会写入 `env.RHIZOME_DATA_DIR`，而 env 优先于 `location.json` 指针。`move_data_dir` 只改指针，不碰也不标记 Claude 的配置；它也不删除任何东西，所以旧目录依然能用。`connect()` 读到旧目录的 token 和 server.json，探测失败后回退到旧副本上的 LocalClient。这个值也没有被 resolve，相对路径会原样复制。portable 模式下 snippet 里的 command 也是 exe 的绝对路径。
- **证据**：`backend/rhizome/system.py:141-147,177-211,71-76`、`backend/rhizome/config.py:55-58`、`backend/rhizome/client.py:282-291`。release.yml 和 test_system.py 测的正是这条路径。
- **场景**：把库迁到 `D:\研究\Rhizome` 后连上 Claude Desktop，之后又迁走或迁回默认位置。从此每次"存入 Rhizome"都进了旧库，`rhz_recall` 用的是冻结的旧数据；如果删掉旧目录，MCP 进程会在原处重新建一个空库。
- **修法**：通过指针或 portable 解析出的路径不再写入 env，因为 `rhz.exe mcp` 自己能解析这两种情况。只有进程拿到了显式的 `RHIZOME_DATA_DIR`（解析成绝对路径）且不是 portable 时才写。在 `move_data_dir` 和 app 启动时，查找 `mcpServers.rhizome` 中带 `env.RHIZOME_DATA_DIR` 的条目，删除或改写（保留 .bak），并返回 `claude_config_updated`，让 UI 提示重启 Claude。portable 模式加一条说明：换盘符后需要重新设置 Claude。加测试：装在自定义目录、迁移后，配置里不再出现旧路径。
- **工作量**：S

#### 10. M1：嵌入模型是进程级设置，不是资料库的属性
- **问题**：`get_settings()` 在进程生命周期内缓存。`rhz settings set embedder` 只写文件，`rhz rebuild` 在 CLI 进程里运行。knn 按 `Embedding.model == get_embedder().name` 过滤，没有匹配的行或维度对不上时静默返回 `[]`。`PATCH /settings` 立即切换模型，没有任何 rebuild 门控，只有一句提示文字。reindex 只写当前模型的向量，所以切换后的 HPC 快照里只有旧实体有 hashing 向量。
- **证据**：`backend/rhizome/config.py:206-212`、`backend/rhizome/cli.py:320-328,494-510`、`backend/rhizome/pipeline/graph.py:79-99,159-164,296-301`、`backend/rhizome/api/app.py:456-472`、`frontend/src/pages/Settings.tsx:139-158`、`docs/implementation.zh.md:124`。
- **场景**：Rhizome 开着时按文档切换模型，或者在 Settings 里选了 bge-m3 却忘了 rebuild。搜索退化成纯关键词，recall 什么也找不到，`resolve_free` 找不到合并候选，每天下午的 ingest 都在静默制造重复的数据集、方法和主题。
- **修法**：rebuild 重写 Embedding 时（以及第一次 ingest 时）在同一事务里记录 `kv['embedding_model']`。knn 和 reindex 把它和本进程的模型比较：不一致时先重新加载设置、重置模型、重试一次，仍不一致就抛出明确的错误，绝不静默返回 `[]`，也绝不写入混合向量。PATCH 返回 `rebuild_required` 和受影响的资产数，UI 确认后执行 rebuild，并显示常驻的 index_stale 横幅，MCP 也给出警告。app 运行时，`rhz settings set` 和 `rhz rebuild` 走 HTTP。`make_snapshot` 发现 hashing 覆盖不全时要警告或补齐。
- **工作量**：M

#### 11. M2：按文档切换到 bge-m3 会让安装版 app 坏掉
- **问题**：registry 导入 hf 模型时没有保护，而 `rhz.spec` 排除了 torch、transformers 和 sentence_transformers。pip 安装的 CLI 和冻结版 app 读的是同一份 `%APPDATA%` 设置。
- **证据**：`backend/rhizome/ml/registry.py:15-50`、`desktop/rhz.spec:29`、`docs/implementation.zh.md:124`、`frontend/src/pages/Settings.tsx:140`。
- **场景**：为了用 bge-m3，你 pip 安装了后端并执行了文档里的命令。从此 app 里的每次搜索、recall 和 ingest 都返回 500，inbox 把所有 RXF 移进 `error/`，MCP 也失败，但浏览页面看起来一切正常。在 Settings 里还能选回内置模型，可是没有任何解释。
- **修法**：registry 检测到模型无法导入时，记录 `model_unavailable` 状态，在 /system 和 MCP 中暴露，UI 显示横幅和"改用内置模型并重算"按钮。不要静默回退成混合向量。模型错误时 inbox 文件留在原地。修正文档：安装版 app 跑不了 bge-m3，需要用 pip 安装的 `rhz serve`，并让 MCP 指向那个 rhz。
- **工作量**：S

### C. 纠错能力与图谱正确性

#### 12. D2：修正后重新导出不会取代旧导出；无法撤回论文，也无法拒绝幻觉实体
- **问题**：没有任何代码把 `Extraction.is_current` 置为 False，rebuild 会重放所有 current 的提取。除了 `/decision` 之外没有 DELETE 路由，CLI 没有 retract，也没有实体级的 reject 操作。读路径只按 edge 状态或 `!= candidate` 过滤。
- **证据**：`backend/rhizome/pipeline/ingest.py:122`、`backend/rhizome/pipeline/rebuild.py:43`、`backend/rhizome/services/views.py:51-53`、`backend/rhizome/services/search.py:187`、`backend/rhizome/services/recall.py:133`、`backend/rhizome/services/datasets.py:31`、`backend/rhizome/pipeline/decisions.py:34`、`backend/rhizome/api/app.py:371`。已复现：拒绝 `dataset:GSE999002` 的所有边之后，search、data 和 recall 仍然把它排在第一位，它的复习卡也没有被挂起。
- **场景**：Claude 编造了一个 accession 或 claim，你在对话里纠正后重新导出。新旧两份导出都是 current，错误的数据集继续出现在 recall 和复习卡里，每次 rebuild 后都会回来，只能手写 SQL 清理。
- **修法**：新增 `retract` HumanDecision（复用现有的 revoke 来撤销），把某篇论文的提取置为非 current 并排一次 rebuild；入口有 `rhz retract`、`POST /work/{key}/retract` 和论文卡片上的按钮，不开放给 MCP。`/ingest`、CLI 和 `rhz_ingest` 增加 `replace: bool`，论文已有 current 的 rxf 行时返回 `existing_exports`。新增 `reject_entity` 操作，并用同一个可见性谓词在 search、recall、datasets、views、vocab、FTS/VECTORS 填充和 due_cards 中过滤被拒实体。前提是 D1。
- **工作量**：M

#### 13. A15：决策历史、撤销和直接纠正操作在 UI、CLI 和 MCP 中都没有入口
- **问题**：`GET /decisions` 和 `DELETE /decision` 存在，但没有任何调用方。UI 只能触达 edge_status 和 confirm_topic。≥0.9 的静默自动合并从不产生队列项。文档却把撤销标为 ✅。
- **证据**：`frontend/src/api.ts:113-146`、`backend/rhizome/client.py:20-36`、`backend/rhizome/cli.py:275`、`backend/rhizome/mcp_server.py:89`、`backend/rhizome/api/app.py:363-381`、`backend/rhizome/pipeline/decisions.py:34`、`docs/implementation.zh.md:23`。
- **场景**：想撤销上周的合并，或者拆开一次错误的自动合并，唯一的办法是带着 token 用 curl。
- **修法**：Client 增加 `decisions()` 和 `revoke()`；CLI 增加 `rhz decisions`、`rhz undo ID` 以及带类型的 `rhz rename/alias/merge/split/link/idea`；MCP 增加 `rhz_edit` 和 `rhz_undo`，需经确认才执行。UI 增加决策历史页，Undo 后轮询 rebuild job；实体页加编辑菜单（重命名、别名、合并到、拆分、连边），并显示别名及其来源。依赖 D1、D3、D26、D28 和 O5。
- **工作量**：L

#### 14. D3：rebuild 会回收并打乱数字实体 id，而 UI、CLI 和 MCP 都把 id 当句柄
- **问题**：`Entity.id` 是普通的整数主键。rebuild 按提取顺序删除再重建行，由 SQLite 分配 rowid。ReviewItem 的 id 同样会被回收。
- **证据**：`backend/rhizome/db/models.py:93,178`、`backend/rhizome/pipeline/rebuild.py:33-48`、`backend/rhizome/pipeline/graph.py:257-268`、`backend/rhizome/mcp_server.py:76-79`、`frontend/src/components/common.tsx:15`。已复现：在有用户主题和一次合并的库上，29 个 id 全变了，其中 28 个指向了别的实体。`test_rebuild_is_deterministic` 只比较 key 集合。
- **场景**：Claude 记着早先 `rhz_search` 得到的 id 812，你点了"重算"或撤销了一条决策，Claude 接下来的 `rhz_related(812)` 就在静默地讨论另一篇论文。一个过期的审核页面也可能写入错误的合并。
- **修法**：删除前先保存 `{key: id}` 快照（以及 pending ReviewItem 的 `dedupe_key: id`），`Graph.create` 和 `Graph.queue` 优先使用旧 id，否则从 max+1 显式分配，不需要迁移。确定性测试改为比较完整的 key→id 映射。MCP 改为 key 优先：`rhz_related(entity: str)`，并在 docstring 里写明 key 才是稳定句柄。
- **工作量**：S

#### 15. D26：撤销两篇论文的合并后，被合并论文的导出、卡片和原始文件仍挂在另一篇上
- **问题**：merge 会改写 `Extraction.work_key` 和 `RawObject.work_key`，而 rebuild 从不重新设置它们。
- **证据**：`backend/rhizome/pipeline/graph.py:373-387`、`backend/rhizome/pipeline/decisions.py:58-64`、`backend/rhizome/pipeline/rebuild.py:43-56`、`backend/rhizome/services/views.py:51`。已验证：撤销并 rebuild 后，W2 有 0 份导出，W1 有 2 份。
- **场景**：误合并后点了撤销，B 的 TL;DR 和洞见从此显示在 A 名下。
- **修法**：在 rebuild 的第一遍里，根据每次物化的结果设置 `ex.work_key` 和 `RawObject.work_key`，然后再由 `apply_all` 重放仍然有效的合并。加测试。ReviewCard 的来源会丢失，要么接受，要么写进文档。
- **工作量**：M

#### 16. M3：没有 NLI 时，立场相反的 claim 会被静默自动合并成一个节点
- **问题**：默认 `nli='none'` 时，`resolve_claim` 只把否定奇偶性不同的候选分开。`_negated` 把 CJK 文本按单字切分，所以"没有""并非"不会被识别为否定，而"无监督""不同"反倒被误判为否定。反义词完全不考虑。`resolve_free` 在相似度 ≥0.9 时自动合并，只写一行 alias，不产生审核项。
- **证据**：`backend/rhizome/pipeline/canonicalize.py:97-129,74-75`、`backend/rhizome/config.py:125-126`、`backend/rhizome/pipeline/materialize.py:151-158`。已复现：一对较长的"没有"句子相似度 0.948，被合并。
- **场景**：反驳论文的 claim 被合并进它所反驳的 claim，两篇论文都"支持"同一个节点，矛盾列表是空的，而且无法拆开。
- **修法**：没有 NLI 时，claim 一律不自动合并，最多到 merge_review 进入队列。加入中英文反义词对，以及最左最长匹配的中文否定正则（附排除表），命中的对送去矛盾审核。加测试。
- **工作量**：S

#### 17. N1：NCBI taxonomy 查询在物化阶段、写锁内执行，rebuild 因此既不确定也不能离线重放
- **问题**：`resolve_organism` 对不在内置表里的名字调用 `taxonomy_id`。rebuild 先清空 EntityAlias，所以每个这样的物种都会在 rebuild 事务里重新联网查询。查询失败（离线、429 被解析成 KeyError、超时）时，key 退化成 `organism:<name>` 而不是 `organism:taxon:N`。`Extraction.meta` 不记录 taxa，违背了 `models.py` 写明的不变量。使用 local 后端时，judge 和 make_card 也在锁内运行。
- **证据**：`backend/rhizome/pipeline/canonicalize.py:175-189`、`backend/rhizome/external/verify.py:66-78`、`backend/rhizome/pipeline/rebuild.py:33-48`、`backend/rhizome/pipeline/ingest.py:96-99`、`backend/rhizome/db/models.py:81`、`backend/rhizome/api/app.py:380`。
- **场景**：离线或网络不稳时撤销一条决策，rebuild 后物种 key 发生翻转，基于 `organism:taxon` key 的合并决策悄悄失效；卡住的连接每个名字占锁 15 秒。
- **修法**：在 `ingest_text` 写库之前（和 `_check_ids` 放在一起）解析 taxa，存为 `meta['taxa'] = {name: taxid|'not_found'|'unverified'}`。`resolve_organism` 依次查 meta、内置表、free key，绝不联网。rebuild 删除前先保存当前的 organism alias→taxid 解析结果，用它回填旧提取的 meta。`unverified` 只在带外重试。local LLM 的判断改为排队任务，逐项提交。
- **工作量**：M

#### 18. D13：后续导出中自由概念的属性被静默丢弃
- **问题**：`resolve_free` 在命中别名、key 或自动合并时提前返回，不应用 attrs。受影响的包括方法的 io 和 biotools、idea 的 transfer，以及没有 accession 的数据集的各字段。
- **证据**：`backend/rhizome/pipeline/canonicalize.py:53-61,74-80`、`backend/rhizome/pipeline/materialize.py:254,277`、`backend/rhizome/pipeline/graph.py:50`。已复现：ArchR 只剩 `{name}`，io 卡片从未生成，io 也没有进入 FTS 和嵌入文本。
- **场景**：首次出现在 light 导出里的热门工具永远只有一个名字，在 5k 篇规模的库里拖累搜索和卡片质量。
- **修法**：新增 `Graph.fill_attrs`，只填缺失的 key；在 `resolve_free` 所有 `created=False` 的路径上按类型调用可合并的 key，有变化就 reindex。每篇论文各自的 transfer 留在边上。加回归测试，并说明需要重新物化。
- **工作量**：S

#### 19. D21：被标为"有用"的合成对和手动 create_idea 产生孤立的 Idea
- **问题**：只对 topic 建边，而合成对两端总是资产，所以一条边也不会建，也不生成卡片。链接只存在隐藏的 attrs 里，文本是 "A ↔ B"。这个孤儿和两端资产都很接近，下周的合成会再次推荐它。
- **证据**：`backend/rhizome/pipeline/decisions.py:229-237`、`backend/rhizome/services/review.py:107-109`、`backend/rhizome/services/synthesis.py:20-35`、`backend/rhizome/pipeline/materialize.py:224`、`frontend/src/pages/Digest.tsx:13`。已复现。
- **场景**：你认可的每一对都从图谱里消失，还会作为噪声再被推回来。
- **修法**：参照 `materialize.py:224`，对非 topic 的链接建 confirmed 的 relates_to 边（origin=user），无法解析的链接单独存为 links_unresolved。key 只在实时记录时校验。生成优先级 10 的用户 idea 卡。Digest 加备注字段。通过 rebuild 重放来回填。
- **工作量**：S

### D. 外部数据采集

#### 20. N4：中国国家数据库（GSA/NGDC 的 PRJCA、CRA、HRA、OMIX、SAMC）被拒绝或被报 NCBI not_found
- **问题**：没有数据库标签时，CRA 和 HRA 通不过格式检查（bad_format），节点被丢弃。所有 PRJ* 都被送到 NCBI bioproject，PRJCA 因此返回 not_found 后被丢弃。CNGB 和 Zenodo 从不核实；Zenodo 的 DOI 形式和裸 id 会产生两个 key。
- **证据**：`backend/rhizome/external/ids.py:12-44`、`backend/rhizome/external/verify.py:36-38`、`backend/rhizome/pipeline/materialize.py:238-240`、`backend/rhizome/rxf/schema.py:74`、`backend/rhizome/services/datasets.py:20`。
- **场景**：一篇用了 GSA-Human 数据的论文，偏偏丢掉了你最需要的那个数据集，还被标成疑似幻觉。
- **修法**：加入 GSA 的格式和数据库标签；标签错了但格式匹配时也接受；NCBI 路由收窄到 PRJNA、PRJEB 和 PRJDB；没有索引的数据库返回 unverified。可选：Zenodo 记录核实和规范化 key。补充下载提示和测试。
- **工作量**：M

#### 21. N6：OpenAlex 补全只做一次，失败也不出声；遇到瞬时错误就永久丢失摘要、期刊和引用边
- **问题**：非 200 响应直接返回 None，不写日志，之后再也不会重新获取。重复拖入的文件被短路。唯一的调用点在 ingest 时。
- **证据**：`backend/rhizome/external/openalex.py:38-42`、`backend/rhizome/pipeline/ingest.py:94,85-89`、`backend/rhizome/jobs.py:138-172`、`backend/rhizome/pipeline/materialize.py:384-399`。
- **场景**：OpenAlex 503 期间拖入的 15 份导出没有摘要、没有引用、`openalex_id` 为 NULL，而且永远链接不上。
- **修法**：`fetch_work` 返回状态（ok/not_found/transient），短暂重试并遵守 Retry-After。新增回填任务（`rhz enrich`，联网时作为 nightly 的一步）：选出有 DOI 但没有 current openalex 提取的论文，复用 `attach_openalex` 和 `link_citations`。显示"缺 T0"的数量。与 N9 配套。
- **工作量**：M

### E. 核心功能的可用性与规模

#### 22. A7：空白上下文、notebook 或没有 import 的脚本会让 recall 抛 KeyError 'relevance'
- **问题**：空白查询会走到 `browse()`，其结果来自 `summarize()`，没有 relevance 字段。`context_from_code` 不解析 .ipynb 的 JSON，还会丢掉只有标准库 import 的行。
- **证据**：`backend/rhizome/services/recall.py:57-64,82-91`、`backend/rhizome/services/search.py:152-155,195-201`、`backend/rhizome/api/app.py:384-395`。结果是 CLI 打出 traceback，API 返回 500，MCP 返回 isError。
- **场景**：在 HPC 上通过 Jupyter 跑 `rhz recall --from-file analysis.ipynb`，直接 traceback。"在 HPC 上边写代码边回忆"这个主打功能，在最常见的文件类型上失效。
- **修法**：空白上下文提前返回 `[]`；`summarize` 总是输出 `relevance: None`；解析 notebook 的 code 和 markdown 单元，去掉 `#SBATCH`、`#PBS` 行；上下文为空时 CLI 打印 no_results。加测试。
- **工作量**：S

#### 23. P2：recall 和搜索的关键词步骤把每个词都 OR 进 FTS5，既不去重也不设上限
- **问题**：`_fts_query` 把所有 ≥3 字符的归一化词 OR 在一起，包括重复词和停用词，而 recall 传入的是 `context[:4000]`。
- **证据**：`backend/rhizome/services/search.py:39-43,154`、`backend/rhizome/services/recall.py:57`、`backend/rhizome/client.py:40`。实测保留重复词时，230 个词耗时 11.6 s，600 个词耗时 69.5 s；去重后同样的词集分别是 1.0 s 和 2.9 s。
- **场景**：Claude 对一段贴进来的草稿调用 `rhz_recall`，库里有几千篇论文时超过 60 秒的超时，主打的 MCP 功能失败。
- **修法**：按出现顺序去重、去掉停用词，最多保留约 12 个（优先长词）。recall 改为发送精简后的查询，或者长上下文时跳过 FTS，只靠 knn 和 rerank。加测试：4,000 字符的上下文最多产生 12 个短语。
- **工作量**：S

#### 24. P3：`entity_card` 加载 hub 节点的全部边（1.1 万条边、3.4 MB）
- **问题**：查询没有 LIMIT，与模块 docstring 里写的"约 300 个元素上限"不符。UI 全部渲染，`rhz_get` 把它们全部返回给 Claude（只去掉了 `exports.raw`），审核队列还为每个审核项各构建一次完整卡片，最后只保留 3 条连接。
- **证据**：`backend/rhizome/services/views.py:23-27,36-45`、`backend/rhizome/services/review.py:40-60`、`backend/rhizome/mcp_server.py:65-73`、`frontend/src/pages/Entity.tsx:32-59`。
- **场景**：`rhz_get organism:taxon:9606` 撑爆 Claude 的上下文；点开 Homo sapiens 时 UI 卡死。
- **修法**：返回每种边类型的计数，加上每类前 N 条（约 50）边，另提供分页的 `/entity/{id}/edges`。`_context` 改用自己的轻量查询。`rhz_get` 默认返回摘要形式。
- **工作量**：M

#### 25. P5：`topic_assets` 没有元素上限，根主题返回 4.5 万项，Windows 上直接 500
- **问题**：Python set 被作为展开的 IN 列表传回 SQL（`all_ids`），也没有上限；`topic_changes` 同样如此。Topic 页把结果全部渲染，每点一个角色筛选就重新请求一次，而且有数据后就不再显示 Loading。
- **证据**：`backend/rhizome/services/views.py:104-163,182-197`、`frontend/src/pages/Topic.tsx:15-62`。根主题返回 45k 项、13.8 MB，在 Windows 上报 `too many SQL variables`。
- **场景**：在 5k 篇的库里打开最宽泛的主题，Windows 上返回 500，或者 webview 卡死。
- **修法**：在 SQL 里聚合，每列 LIMIT 加计数和各自的 offset，绝不绑定 id 列表。前端只请求一次，用 useMemo 过滤角色，每列显示 150 项并提供"显示更多"，重新加载时把视图调暗。
- **工作量**：M

#### 26. M10：nightly（社区、主题提升、每周合成）只在连续空闲 30 分钟后才调度
- **问题**：`maybe_schedule_nightly` 只在空闲 tick 满足 `ticks%900==0` 时触发，而 tick 每次启动都会清零。手动按钮发的是 `{}`，被 7 天的保护跳过了合成，页面 1.5 秒后就刷新。没有运行 nightly 的那些周里新增的资产永远不会参与合成。
- **证据**：`backend/rhizome/jobs.py:115-130,161-167`、`backend/rhizome/services/synthesis.py:43-48`、`frontend/src/pages/Digest.tsx:23`、`frontend/src/api.ts:145`。
- **场景**：你每次只用 10–20 分钟，笔记本经常休眠。社区从不计算，主题一直停在 candidate，Digest 永远是空的。
- **修法**：worker 启动时检查一次 nightly，之后每约 5 分钟按墙钟时间检查。合成改用追赶窗口（`since=last_synthesis`）。加 `force_synthesis` payload；Digest 页轮询 job 直到完成，并显示上一批的时间。
- **工作量**：S

#### 27. X1：安装版的 UI 和 MCP 都拿不到 RXF 导出说明、骨架和词表，而捕获流程第一步就依赖它们
- **问题**：Home 页让你把说明放进 Project，但没有任何页面或接口提供这份说明，`rhz` 也不在 PATH 里。MCP 的 INSTRUCTIONS 默认已有 Project，也没有提供骨架或词表的 tool、resource 或 prompt。
- **证据**：`frontend/src/pages/Home.tsx:96`、`backend/rhizome/cli.py:475-484`、`backend/rhizome/api/app.py:327`、`backend/rhizome/mcp_server.py:17-25,43-46`、`README.md:37-41`。
- **场景**：新用户，或者任何一个普通的 Claude Desktop 对话，都会产出自己编的 YAML。所有文件进了 `error/`，`rhz_ingest` 反复重试，主题也不参考词表。
- **修法**：提供 `GET /rxf/instructions?lang`，在 Settings 加"Chat Project 设置"面板（复制说明、下载 md 和词表），从 home.step1 链接过去。MCP 增加 `rhz_rxf_guide()` 工具，返回说明和当前词表；INSTRUCTIONS 写明除非 Project 里已有说明，否则先调用它。同时提供 resource 和 prompt。README 增加首次使用一节。
- **工作量**：S

### F. 工程保障

#### 28. T1：ci.yml 从未运行过；单元测试、ruff、license、i18n、SPDX 和 gitleaks 检查一次都没跑，期间却发布了三个版本
- **问题**：ci.yml 只在 push 到 main 或 PR 时触发，而仓库没有 main 分支，也没有 PR，所有运行都来自 release.yml。Windows 上的 pytest 一次都没跑过。gitleaks 缺少 `fetch-depth: 0`，没有任何 job 设置超时。
- **证据**：`.github/workflows/ci.yml:3-6`、`.github/workflows/release.yml:7-9`、`docs/implementation.zh.md:42`。
- **场景**：一个只在 Windows 上出现的 ingest 或 decisions 回归被发布出去，因为 release 的冒烟测试碰不到这些地方。
- **修法**：在 release.yml 发布前跑 pytest 和 ruff（或者通过 workflow_call 加 needs 调用 ci.yml）。ci.yml 的触发条件加上 `claude/**` 和 workflow_dispatch。gitleaks 设置 `fetch-depth: 0`，每个 job 设置 `timeout-minutes`。
- **工作量**：S

---

## 3. 应该修（medium）

| 编号 | 问题 | 位置 | 修法 | 工作量 |
|---|---|---|---|---|
| D6 | `synchronous=NORMAL`，raw 文件写入不 fsync；掉电后论文可能从 DB 消失，而导出已经进了 done/，raw 副本可能是 0 字节 | `db/session.py:56-57`、`rawstore.py:20-27`、`pipeline/ingest.py:199-205` | 读写 engine 用 `synchronous=FULL`；rawstore 用 mkstemp + fsync + os.replace；`rhz diag` 检查 0 字节文件和残留 .tmp | S |
| D7 | vector_cache 无限增长（每次查询、每段实体文本、每个用过的模型），并随每次备份、快照和同步一起复制 | `pipeline/graph.py:180-198`、`services/snapshot.py:22-26,63-75` | 查询路径用 `persist=False` 加 LRU；rebuild 或 `rhz gc` 清理旧模型行和孤儿行，再显式 VACUUM；快照剔除 vector_cache、access_log 和 job 后转 DELETE 日志模式；加测试确认快照里没有 vector_cache | M |
| D8 | add_alias、自动合并和 NLI 合并都不刷新 FTS，rebuild 照样复现漂移；中文别名的实体完全搜不到 | `pipeline/decisions.py:218`、`canonicalize.py:48-50,60,76-78,142`、`graph.py:291-295` | add_alias 返回是否插入，插入时调用 `_refresh_fts`；Alembic 0002 重建全部 FTS 行；加不变量测试 | S |
| D9 | 复习卡 id = sha(entity_key+q)，合并、切换语言或换模型后卡片重复或变成孤儿；陈旧的 retro_tag 项返回 422 | `pipeline/materialize.py:306-318`、`graph.py:382-387`、`services/review.py:75-111` | merge 时重新计算卡片 id，冲突时保留有历史的卡并迁移 ReviewLog；生成卡用槽位 id；rebuild 结束时对账孤儿卡；陈旧项标为 obsolete；附一次性清理迁移 | M |
| D10 | 用户洞见自动合并进模型的 idea 后，模型 idea 变成"你的"，用户原话只剩一个别名 | `materialize.py:193-195,207-209`、`canonicalize.py:70-75` | `resolve_free` 传入 origin，跨来源的自动合并降级为排队的合并项 | S |
| D11 | T2 层级从未写入 Work（`w.tier = max(w.tier, 1)`），tier=2 搜索永远没有结果 | `materialize.py:113` | 改为 `max(w.tier, ex.tier or 1)`，加 ingest 和 rebuild 测试 | S |
| D12 | NLI 的 also_supports 不看 stance，反驳论文被记为支持；矛盾项不带 stance | `materialize.py:156-162`、`review.py:97-100` | 仅 stance=supports 时传播；payload 带 stance，stance 为 contradicts 时反转接受后的边 | S |
| D15 | upsert_edge 混合不同导出的出处（light→deep 升级、论文合并后） | `pipeline/graph.py:320-341,353-357` | extraction_id 变化时整体替换 evidence、confidence 和 attrs，旧值归档到 `attrs.evidence_records` | M |
| D17 | 多篇论文引用同一数据集或方法时，属性（tissue、organism 等）后写者胜 | `materialize.py:248,272,351` | 新增 merge_reported_attrs：为空才填，产出方（produces/proposes）为准，冲突写入 `attrs.reported` | S |
| D18 | 文件内局部 id（t2、d1、m1）泄漏进实体属性，显示在卡片上并进入 FTS | `materialize.py:192,235,258`、`Entity.tsx:112-120` | transfer.to 解析成规范名和 to_key；数据集和方法属性排除 id；一次性回填并 reindex | S |
| D19 | edit_text 改不了论文卡片和 rhz_get 显示的内容，也没有任何入口能发出这个操作 | `services/views.py:59`、`decisions.py:222-227` | 物化时在 ex.meta 记录 insight→entity key；卡片输出 canonical_name 加原文；加编辑按钮和 CLI/MCP 入口 | M |
| D20 | 接受矛盾审核时证据写进 attrs 而不是 edge 列，缺少 evidence_type、strength 和 extraction_id | `services/review.py:99-100`、`decisions.py:172-178` | payload 携带这些字段，add_edge 接受并传给 upsert_edge | S |
| D28 | 撤销后，被撤销的合并或 distinct 对不会回到审核队列 | `pipeline/graph.py:392-395`、`rebuild.py:36-37` | revoke 时删除该对的非 pending merge 和 topic_relation 项，让 rebuild 重新排队 | S |
| D30 | 从 raw/ 重新导入无法复现资料库：PDF 配对、OpenAlex 响应、文件名和时间戳只存在 DB 里 | `pipeline/ingest.py:94-133`、`rawstore.py:15-27` | OpenAlex JSON 存为 L0；写一次性的 `<sha>.meta.json`；提供 `rhz recover --from-raw`；与 D5 配套 | M |
| G2 | move_data_dir 后运行中的 app 继续写旧目录；复制是同步且非原子的；有 env 覆盖时迁移等于没做 | `system.py:177-211`、`api/app.py:141-144,493-497` | 迁移成功后进入只读并返回 409，提供"立即重启"；先复制到 `.rhizome-moving` 再 rename；data_dir 来自 env 或 `--data-dir` 时直接拒绝 | S |
| G3 | location.json 指向不可用的目标时，要么启动崩溃，要么静默新建空库 | `config.py:60-70,176-179`、`main.rs:150-156` | 指针目标不存在时抛 LibraryNotFound，绝不 mkdir；main.rs 提供"重试/使用默认"；增加 `rhz data-dir --reset` | S |
| G4 | Settings 提示建议把 WAL 库放进"同步文件夹" | `locales/zh-CN.json:302`、`system.py:177-208` | 修改两种语言的文案；对 UNC、网络盘、可移动盘和 OneDrive、坚果云等返回警告并要求确认；切换前跑 quick_check；备份目录可以放到库外 | S |
| G5 | CLI 绕过运行中的 app：`settings set` 被 PATCH 覆盖，rebuild、nightly 和 watch 成了第二个写者 | `config.py:205-212`、`cli.py:109-124,494-509`、`app.py:457-466` | server 在线时 CLI 走 HTTP；PATCH 基于重新加载的设置合并；拒绝 `rhz watch`；nightly 只跑自己排的任务 | M |
| G6 | settings.json 非原子写入；任何加载错误（坏 JSON 或一个非法枚举值）都让 app、CLI 和 MCP 启动即崩 | `config.py:188-202,82` | tmp + fsync + replace，并保留 .bak；校验失败只丢弃出错字段并提示用户 | S |
| G8 | 非法设置值返回 500，Settings 页吞掉失败；CLI 拼错 key 也显示成功 | `api/app.py:464`、`Settings.tsx:21-52`、`cli.py:507` | 类型化 SettingsPatch，ValidationError 转为 422；CLI 校验 key；前端 await 并显示错误 | S |
| G9 | inbox_dir 的父目录无法创建时 app、CLI 和 MCP 都崩溃；相对路径按 CWD 解析 | `config.py:116,176-179`、`inbox.py:96,114` | 核心目录与 inbox 目录分开处理；inbox 创建失败不致命，显示横幅；写入设置时校验 | S |
| G10 | `rhz sync` 开三次 SSH 会话（三次 2FA）、没有超时；Windows OpenSSH 不支持 ControlMaster；'~' 被引成字面量 | `services/snapshot.py:47-56,68-75` | 单次 ssh 通过 stdin 流式传输并设超时；'~' 映射为 `"$HOME"`；本地化的错误信息 | S |
| A1 | HPC 快照：旧版 libsqlite3 没有 trigram 时搜索崩溃；不检查 schema revision | `services/search.py:46-56`、`client.py:271-275` | 捕获 OperationalError 后回退到 LIKE；快照模式比对 revision；`rhz diag` 打印 SQLite 版本 | S |
| A2 | 快照继承 WAL 模式，只读目录下 `mode=ro` 打不开 | `services/snapshot.py:22-26`、`db/session.py:39,44-47` | make_snapshot 转为 `journal_mode=DELETE`；文件头是回滚日志模式时才加 `immutable=1` | S |
| A4 | CLI 错误要么假装成功，要么打栈：`rhz decide` 遇到 422 仍打印 ok 并退出 0 | `cli.py:278,316`、`client.py:50-55,133-137` | `_check()` 助手配合 `typer.Exit(1)`；入口包装成一行错误；snapshot 路径先 expanduser | S |
| A5 | 全局 `--snapshot` 被 rebuild、nightly、vocab、diag、backup 等命令静默忽略 | `cli.py:41-52,319-328,423-442` | 写命令加 `_require_writable`；只读命令用 `_local_session` | S |
| A6 | 远端没设 `RHIZOME_SNAPSHOT` 时静默新建空库并返回"无结果" | `cli.py:33`、`client.py:124-126` | 读命令用 `create=False`，找不到库时报错并给出提示；写进 README | S |
| A17 | `requires-python>=3.11` 挡住集群上常见的 3.10，而 106 个测试在 3.10 上全部通过；没有离线安装路径 | `backend/pyproject.toml:11` | 改为 >=3.10 并加 3.10 CI；文档化 pipx/uv、conda 和离线 wheelhouse | M |
| A18 | 快照模式丢弃用户设置（--lang、远端 settings.json、阈值、embedder） | `client.py:270-275,120-122` | 用 `get_settings().model_copy(update=...)`；快照缺当前 embedder 的向量时警告 | S |
| S11 | 同步到集群的快照默认全局可读，里面包含整个库和你的个人 idea | `services/snapshot.py:68-72` | 上传前 `umask 077`，mv 前 `chmod 600` | S |
| U2 | 更新版或降级后的 DB 不被识别：先写一份误导性的备份，再死在原始 Alembic 错误上；安装器允许降级 | `db/session.py:162-170`、`main.rs:153-155` | 遇到未知 revision 先抛 SchemaTooNew，不写备份；写 schema.json 记录版本；可设 `allowDowngrades=false` | S |
| U3 | 没有恢复备份的路径；在残留 -wal 旁边手动覆盖文件会损坏 DB | `cli.py:415-420` | `rhz restore`：独占锁检查、完整性检查、恢复前再备份一次，然后用 backup API 写回 | S |
| U4 | L1 重放用当前模型而不是存储时的版本校验，失败的行被静默丢弃 | `materialize.py:107`、`rebuild.py:46-61` | 删除前预检，有失败就列出并以退出码 1 结束；按 schema_version 做 upcast | M |
| U5 | 持久化的实体 key 依赖 `text.norm()`，改归一化规则就会孤立决策和卡片 | `text.py:10-19`、`graph.py:248-255` | golden 测试锁定 norm 的输出；规则变更时附带数据迁移 | M |
| L1 | 启动看门狗 90 秒后永久放弃，首次连接成功后不再监视后端 | `main.rs:134-171` | 超时但子进程还活着时显示"正在升级，请勿关闭"并继续轮询；之后每约 2 秒 try_wait | M |
| L2 | 启动失败消息可能丢失；setup 出错时在无窗口进程里静默 panic | `main.rs:117-129,164` | 错误存入 managed state，在页面加载完成后显示；setup 不返回 Err；加载页 120 秒超时，提示可能被杀软隔离 | S |
| C2 | worker 启动时把所有 running 任务重新排队，包括另一个进程正在运行的 | `jobs.py:84-88,118-122` | 增加 owner_pid 列，只在 pid 已死或超过 STALE_RUNNING 时重新排队 | S |
| C3 | inbox 不检查伴随的 PDF 是否已写完；提交后的移动可能孤立 PDF | `inbox.py:61`、`ingest.py:157-182` | 把 YAML 和 PDF 移进 `.processing/`，以能否加锁判断就绪；迟到的 PDF 补挂到已入库的论文 | S |
| C4 | Observer 在启动扫描结束后才开始监听，没有存活检查，也没有周期重扫 | `inbox.py:76,96-125` | 先启动 Observer；每 30–60 秒重扫；在 /stats 和 Home 上显示等待中的文件数 | S |
| N2 | ingest 串行发起外部调用且没有总预算，可能超过 60 秒客户端超时，重试时只拿到 duplicate | `external/http.py:10-13`、`ingest.py:53-95` | 共享 client、短超时、按主机熔断；duplicate 返回已存的 suspect 和相关论文；`rhz_ingest` 捕获超时 | M |
| N3 | NCBI 返回 200 且带 ERROR 时被当作 not_found，真实的 accession 被永久判为幻觉 | `external/verify.py:22-39` | 只有显式的 0 才算 not_found；增加 `rhz recheck` | M |
| N5 | 标签写错但真实的 accession、非规范的 repo URL 被当作伪造 | `external/ids.py:22,34-55` | 用 urllib 解析 repo URL；按 guess_database 重新标注后核实 | S |
| N7 | DOI 归一化漏掉常见的残留字符，'#' 或 '?' 会截断 OpenAlex 的查询 URL | `external/ids.py:47`、`openalex.py:33-37` | 统一的 normalize_doi，用 doi_ok 把关，URL 百分号编码 | S |
| N9 | 网络故障完全不可见：没有诊断、不用 Windows 证书库、没有代理指引，UI 里也设不了 contact_email | `external/http.py:10-13` | 用 truststore；按主机统计失败次数并在 Settings 显示横幅；增加 `rhz doctor`；加 contact_email 输入框 | M |
| P1 | 向量索引每加一个新实体就 vstack 整个矩阵，rebuild 变成 O(N²) | `pipeline/graph.py:115-130` | 预分配缓冲区并按倍数扩容；rebuild 期间暂停增量维护，结束后一次性加载 | S |
| P4 | 2 跳邻居先把整个邻域载入内存再分页（12 s、233 MB），大 hub 超过 Windows 的绑定变量上限 | `services/views.py:75-102` | 用子查询加 SQL 分页，不展开 hub 节点，UI 显示"共 N 个，显示 150 个" | M |
| P7 | 向量缓存每个进程多占 465 MB RSS、冷加载 2.4 s、峰值翻倍，rebuild 与读者互相驱逐 | `pipeline/graph.py:77-99` | 预分配并流式加载、single-flight、rebuild 用私有缓存、后台预热 | M |
| P8 | `entity_fts.entity_id` 是 UNINDEXED，却按它删除，每次 reindex 都全表扫描 | `pipeline/graph.py:294-295,306` | 用 rowid = entity id 做插入和删除；在 0002 里重建（可以和 D8 合并） | S |
| P9 | 中文关键词搜索基本失效：多字 CJK 查询匹配不到 FTS，LIKE 回退按单字匹配并污染 RRF | `services/search.py:39-66`、`text.py:12-27` | ≥3 字的 CJK 串用 3 字滑窗 OR；2 字用 bigram LIKE 并排在 FTS 结果之后；reranker 也用 bigram | M |
| P10 | 合成对每个近期资产跑一次 knn，没有上限，窗口固定 7 天，按任意顺序取满 20 对就停 | `services/synthesis.py:43-75` | 只调用一次 VECTORS.get 并分块做矩阵乘，按相似度排序、限额；`since=last_synthesis`；在写事务外计算 | S |
| M4 | 默认模型只能识别词形变体，同义词、缩写和中英文对既不合并也不入队 | `ml/fallback.py:50-67`、`canonicalize.py:47-86` | RXF topic 支持中英文别名；缩写检查命中时入队；Settings 标注"仅词法匹配" | M |
| M5 | 一套阈值用在两种分数尺度上，部分门限是硬编码，调优值在换模型后仍然保留 | `config.py:96-106`、`materialize.py:292` | 每个模型一套阈值；字面量移入 Thresholds；学习值按 embedder 分开存 | M |
| M6 | 在新模型下 rebuild 时，如果源实体先被自动合并到别处，人工合并会被静默丢弃 | `decisions.py:123-127`、`canonicalize.py:57-75` | key 被重定向而目标还不存在时直接建实体；`_op_merge` 核对合并结果，不符则排一个冲突项 | S |
| M7 | 没有本地判别器时，retro-tag 每个主题最多排 500 个审核项，不能批量忽略 | `pipeline/retro.py:59` | 增加 retro_queue_max（约 30）；提供 `POST /review/dismiss` | S |
| M11 | 合成反馈阈值只升不降，最高到 0.95，并永久覆盖配置值 | `services/review.py:116-127`、`synthesis.py:39-41` | 每个 embedder 存一个限幅的 delta，用目标率步长，提供重置入口 | S |
| M12 | Claude 的 rhz_get 调用被算作你"看过"，压低 recall 刚找出的结果 | `services/views.py:64-65`、`mcp_server.py:66-73` | 增加 touch 参数，rhz_get 传 False | S |
| M13 | 坏卡无法挂起或删除，被拒实体的卡仍然到期 | `services/cards.py:77,39-46` | `POST /cards/{id}/suspend` 加 's' 键；due_cards 过滤掉没有有效来源边的卡 | S |
| M15 | 模板 idea 卡对一篇论文的每个 idea 都出同一道题，答案在揭示前就可见 | `materialize.py:355-358` | 用 transfer.to 出题；揭示前隐藏 entity_name | S |
| O1 | app、MCP 和 CLI 共用一个 RotatingFileHandler，Windows 上轮转失败后丢日志并毁掉旧备份 | `logging_setup.py:15` | 按角色分文件；轮转时捕获 OSError；第三方 logger 调到 WARNING | S |
| O2 | 未处理的 500 进不了 rhizome.log；没有启停标记；冻结版无法调日志级别 | `cli.py:96`、`logging_setup.py:19` | 全局 exception_handler 加 request id；记录启动、关闭和 parent gone；读取 `RHIZOME_LOG_LEVEL` | S |
| O5 | 后台任务只写不读：没有任务列表、没有轮询、不去重；nightly 失败后每 30 分钟静默重跑 | `api/app.py:433-445`、`jobs.py:33-39,91-104` | 幂等任务合并；连续失败后退避；提供 `GET /jobs`；前端 useJob 钩子 | M |
| O6 | `rhz diag` 需要健康且可迁移的 DB，缺少 revision、失败任务和控制台日志，而且 app 里没有入口 | `cli.py:423-443` | 只读打开 DB，每节单独 try；补齐缺失的内容 | M |
| T2 | 任何 `claude/**` 分支的推送都会公开发布，没有 tag 校验、没有校验和、没有并发保护 | `release.yml:5-17,221-234` | 只在 v* tag 或 dispatch 时发布，tag 必须等于 v$ver；发布前跑 pytest 和 ruff；生成 SHA256SUMS | S |
| T3 | worker、nightly 调度和 inbox 线程从未被任何测试跑过 | `jobs.py:116-131`、`inbox.py:113-125` | 抽出 `Worker.step()` 同步测试；加一个 lifespan 冒烟测试 | M |
| T4 | 升级路径没有测试 | `db/session.py:156-171` | 新增 `test_migrations.py` 并用一个假 0002；提交一份旧版本的 DB fixture | M |
| T5 | app 运行时实际使用的 HttpClient 没有测试，错误行为也和 LocalClient 不一致 | `client.py:39-113` | MCP `_call` 统一错误格式；对两种客户端做参数化测试 | S |
| T11 | 没有规模测试，也没有双进程测试 | `tests/conftest.py:45` | 多进程同时 ingest 的测试；写会话用 BEGIN IMMEDIATE；可选的 bench 标记 | M |
| H2 | 没有生成 0002 的办法，而且 autogenerate 会删掉 FTS 表 | `db/session.py:103-112`、`migrations/env.py` | include_object 排除 `entity_fts*`；加漂移测试；提供开发用的 revision 脚本 | S |
| F1 | 界面语言每次启动都丢失（每次都是随机端口的新 origin） | `frontend/src/i18n.ts:9-17` | 启动时读取 `/health.language` | S |
| F2 | 鼠标后退键会落到已经失效的"正在启动"页 | `main.rs:139-141`、`api.ts:17` | 用 location.replace；token 捕获改用 history.replaceState | S |
| F3 | 没有 ErrorBoundary，任何渲染或 effect 异常（比如没有 WebGL）都让整个窗口白屏 | `main.tsx:14-17`、`TopicGraph.tsx:32` | 顶层 ErrorBoundary；Sigma 构造包 try/catch 并提供列表回退 | S |
| F4 | 约 15 个写操作没有 catch，Loading 丢掉服务端的 detail，也不感知只读模式 | `common.tsx:73-81`、`api.ts:31-35` | errorText 助手、toast 和 run() 包装；启动时读 read_only 并禁用写控件 | M |
| F5 | 搜索和浏览静默截断到 20 条，计数不对，带过滤的浏览只看最新 500 个 id | `Search.tsx:19-55`、`app.py:243` | 过滤推到 SQL 并返回精确 total；提供"加载更多" | M |
| F6 | 在顶栏搜索后，Search 页的表单和 URL 不同步 | `Search.tsx:15,27` | `key={query}` | S |
| F9 | GBK 或 UTF-16 导出、拖入文件夹都会不透明地失败 | `app.py:187,193`、`ingest.py:160`、`Home.tsx:32-60` | decode_rxf 助手：认 BOM、试 UTF-8、否则提示另存为 UTF-8 并返回 422；拖放区加 busy 状态 | S |
| X2 | 错误报告提示 LLM 删除放错位置的资产列表，也不给出允许的枚举值 | `rxf/loader.py:82-85,166-176` | `_KNOWN` 增加移动和改名提示；附上 schema 中的枚举值 | S |
| X4 | 词表导出不含候选主题，Project 里的词表好几周都几乎是空的 | `services/vocab.py:16-19` | 默认导出 ≥1 篇论文的候选主题，单独成一节 | S |
| X6 | YAML 代码块前后只要有文字，整个文件就失败 | `rxf/loader.py:19` | 提取第一个包含 rxf_version 的 fenced 块 | S |

---

## 4. 可以晚点（low）

- **安全**：S1（PATCH 可写入恶意 ssh_options，校验 RemoteTarget 并加 CSP）、S2（diag 和 GET /settings 泄露 database_url 密码）、S3（token 写进 backend-console.log）、S4（Windows 上没有 ACL）、S5（server.json 过期后会信任任意 /health 200）、S6（`rhz serve` 的 token 不轮换，捕获 token 时留下历史记录）、S7（没有 Host 校验，/health 暴露版本信息）、S8（MCP 写工具没有 annotations）、S9（非 ASCII bearer 返回 500 而不是 401）、S10（HF 模型没有固定 revision）。
- **日志与桌面生命周期**：O3（console log 路径与文档不符，每次启动被截断，diag 里没有）、O4（faulthandler 默认不开）、L3（父进程看门狗依赖可复用的 PID）、L4（安装器在询问之前就杀掉 rhz.exe，降级没有阻止）、L5（窗口在 1366×768 加缩放的屏幕上溢出）、L6（没有更新提醒）。
- **性能与 ML**：P6（每次 ingest 的开销与库大小成正比）、M8（模型下载整个仓库，没有镜像提示）、M9（FSRS 按 UTC 零点切天）、M14（local LLM 生成的卡丢失优先级）、M16（Home 的到期数不计每日上限）、M17（争议列表只看 7 天窗口）、M18（ingest 时的相关论文排序有缺陷）、M19（Cards 页丢掉学习步）。
- **外部 API**：N8（GitHub 未认证、没有缓存、verified 状态不升级）、N10（没有遵守 NCBI 的礼仪要求）、N11（OpenAlex 返回意外载荷时整次 ingest 中止）。
- **测试与 CI**：T6（MCP 的 JSON-RPC 层只有冒烟测试）、T7（CI 的依赖浮动，与 constraints 不一致）、T8（没有 mypy）、T9（15 个 CLI 命令没有测试）、T10（前端和 Tauri 壳没有测试）、T12（check_licenses 有缺陷）、T13（离线安装包从未被测试）、T15（检索门槛只是冒烟测试）、T16（verify 和 openalex 的响应处理没有测试）。
- **升级**：U6（加引号的 rxf_version 被拒，prompt_version 没有登记）、U7（FSRS 状态没有版本号）。
- **前端**：F7（导航不重置滚动，过滤条件不进 URL）、F8（快捷键没有防重复）、F10（useLoad 短暂显示旧数据）、F11（没有实时刷新）、F12（边确认后行样式不更新）、F13（recall 没有 busy 状态，静默截断到 4,000 字）、F14（暗色模式对比度低、部分控件无法用键盘操作）、F15（`rhz serve` 下 index.html 被缓存）、F16（别名语言对显示没有影响）。
- **API/CLI/MCP**：A8（decision、revoke 和 topic 接口不幂等）、A9（rhz_ingest 的修复提示指向不存在的入口）、A10（各端过滤和分页参数不一致）、A11（客户端与服务端不检查版本兼容）、A12（允许空主题名）、A13（重复导入时附带的 PDF 被丢弃）、A14（/ingest 不检查 PDF 类型和大小）、A16（决策 key 对大小写敏感，报错没有提示）、A19（下载提示命令有误）、A20（远端不知道快照有多旧）、A21（终端截断不考虑 CJK 宽度，get 只接受 id 或 key）。
- **数据**：D14（共享 idea 的 transfer attrs 后写者胜）、D16（light 导出覆盖 deep 导出的 tldr）、D22（被拒 accession 的卡片挂到论文上）、D23（claim 显示第一篇论文的 evidence_type）、D24（bio.tools id 没有用作锚点）、D25（论文卡片不显示 OpenAlex 信息）、D27（split 不移动别名，总是报告成功）、D29（无法丢弃伪候选主题）、D31（没有开放格式的批量导出）、D32（切换到 Postgres 后没有备份）、D33（raw/ 不能单独放置，也不校验完整性）。
- **可维护性与 RXF**：H1（create_app 是一个大闭包）、H3（文档与发布元数据不一致，版本号散落在七个文件里）、H4（词汇表手工复制在 10 多处）、H5（缺少第三方许可声明）、H6（死代码，两个 MCP 入口不一致）、X3（缺失字段被重复报 N×N 次）、X5（词表过期没有提示）、X7（骨架里固定的 prompt_version 被原样照抄）。

---

## 5. 建议的落地顺序

### 第一周：止血（防止丢数据，处理每天都会撞上的问题）
**T14 → T1 → D5(1) + D4 → D1 → G7 + G6 → A3 + G1 → A7 + P2 → M2 → D3 → X1 → D11**

- **T14、T1 放最前**：之后的每一项修复都要跑测试。先保证跑测试不会碰到你的真实库，再让 CI 真正跑起来，后面的改动才有保护。
- **D5 第一步（每日备份、备份目录可外置）和 D4（轮转）一起改**：都在改 `backup_database`。这是唯一能挽回所有其他 bug 后果的保险。
- **D1**：D2、A15 和 M1 的切换流程都依赖 rebuild 能容错。不修它，撤销和换模型都等于把库锁死。
- **G7、G6**：每多保存一次设置，就多一份被冻结默认值的文件，越晚修迁移越难。
- **A3、G1**：只要每天开关 app、用 Claude 存论文，就会撞上。改动都是 S。
- **A7、P2**：recall 是主打功能，两者都是 S。
- **M2（先修文档）、D3、X1、D11**：都是 S，能消除"按文档操作反而出问题"和"第一步就卡住"的情况。

### 第一个月：结构性修复
1. **迁移基础设施：U1 → H2 → T4 → U2 → U3，然后发布 0002（D8 + P8 合在一起）。** 任何 0002 都必须等 U1 和 H2 完成；D8 和 P8 都要重建 FTS，正好共用这一次迁移。
2. **并发与任务：C1 → C2 → O5 → G5。** C1、D4、O5 和 A8 都需要 `enqueue_once` 和 job 轮询，先做公共部分。
3. **模型一致性：M1 → M10 → M11 → M3。** M1 修好后才能安全地引导用户换模型。M3 是静默污染，越早修，需要清理的合并越少。
4. **纠错通道：D28 → D26 → D2 → A15。** A15 依赖 D1、D3、D26、D28 和 O5；先修底层，再开放 UI 和 CLI 入口。
5. **采集正确性：N1 → N3、N4、N5、N7 → N6 + N9 → D13 → D21 → D9。** 先让新进来的数据正确，再回填旧数据。
6. **规模：P3 → P5 → P9。** 库到几千篇时一定会撞上；P9 直接影响中文用户的日常搜索。
7. **可见性：O1、O2 → F3、F4 → L1、L2 → C4 → G3、G2。** 前面的修复上线后，失败必须能被看见。
8. **S11**：S，涉及个人笔记的隐私，顺手修掉。

### 之后
- **HPC 组**：A1、A2、A5、A6、A17、A18、G10，以及 low 中的 A19、A20。如果近期就要在集群上用，这一组整体提前。
- **数据质量组**：D6、D7、D10、D12、D15、D17–D20、D30（与 D5 第二步配套）、M4–M7、M12、M13、M15、U4、U5、X2、X4、X6、P1、P4、P7、P10。
- **测试组**：T2、T3、T5、T11（每修完一个对应领域就补上）。
- **前端组**：F1、F2、F5、F6、F9。
- **全部 low 项**，按顺手程度处理。

---

## 6. 已检查且没有问题的方面

- **SQLite 基础**：每个连接都设置了 WAL、30 秒 busy timeout 和 `foreign_keys=ON`。rebuild 是原子的（已验证失败时完整回滚）。ingest 先提交再移动文件。copy_sqlite 使用 backup API 并以 tmp + replace 写入。任务领取的 CAS 在多进程下正确。两个并发 ingest 会干净地串行执行，没有 UNIQUE 冲突（12/12 轮）。向量缓存靠 (count, id-sum, embedding_version) 签名能察觉其他进程的写入。
- **Windows 与中文路径**：CJK、空格、% 和 # 路径端到端可用，CI 会安装到 `C:\软件 工具\…`。UTF-8 模式和 BOM 容错读取都正确。portable 检测在 Rust 和 Python 两侧一致。卸载和升级不会碰资料库。单实例、高 DPI 和 CREATE_NO_WINDOW 处理正确。
- **安全基线**：除 /health 和静态资源外所有接口都需要 Bearer 认证，并用 compare_digest 比较，没有 CSRF 风险，CORS 关闭。token 通过 env 传递，每次启动重新生成，只绑定 127.0.0.1。webview 只有 `core:default` 权限。YAML 用 SafeLoader，FTS 查询参数化，远程路径用 shlex.quote，没有 pickle、eval 或 `shell=True`。SettingsPatch 会丢弃 host、port 和 database_url。
- **离线与外部 API**：`RHIZOME_OFFLINE` 在四个外部调用点都生效。完全重复的文件在联网前就被短路。GitHub 403、NCBI 429 和 HTML 响应被正确映射为 unverified。accession 和 repo 的检查在第一次 flush 之前完成，不占写锁。
- **性能**：在 1 万篇论文、5.5 万实体的合成库上，暴力 knn（11–16 ms）、浏览、首页统计、topic_map（上限 250 节点）和社区检测都可以接受。实际执行的 WHERE 子句都有可用索引。前端图组件只接收服务端已限量的数据。
- **工程**：106 个测试通过，ruff 和 `tsc` strict 都干净，i18n 两种语言的 key 一致，license gate 有效，模型与 0001 迁移之间没有漂移，各处版本号目前都是 0.1.2，RXF 的契约测试覆盖了已发布的 schema。