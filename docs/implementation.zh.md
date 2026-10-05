# 实现对照与进度

对照 [设计文档](design.zh.md) 逐章说明当前代码做到了哪里、和设计有哪些偏差、哪些还没做。
状态：✅ 已实现并有测试　◐ 已实现但未在真实环境验证　○ 未实现

## 总览

| 设计章节 | 状态 | 代码位置 |
| --- | --- | --- |
| 五层数据模型（L0–L3 + human_decision） | ✅ | `backend/rhizome/db/models.py`，迁移 `migrations/versions/0001_*` |
| RXF 格式、JSON Schema 校验、错误报告 | ✅ | `rhizome/rxf/`，`rxf-spec/` |
| 收件箱监听（done/ error/） | ✅ | `rhizome/inbox.py` |
| T0 OpenAlex、引用边 | ◐ | `external/openalex.py`，`pipeline/materialize.py:link_citations`（测试离线运行，未连真实 API） |
| T1 RXF 解析成实体和边 | ✅ | `pipeline/materialize.py` |
| T2 附 PDF 重新导出深度版 | ✅ | 同名 PDF 自动附上，存入 L0，档位记为 T2 |
| 外部 ID 校验（正则 + NCBI / BioStudies / GitHub） | ◐ | `external/ids.py`，`external/verify.py`（API 调用未在 CI 中联网验证） |
| 实体规范化（别名 → 向量 top-5 → reranker → 阈值） | ✅ | `pipeline/canonicalize.py` |
| 论断 NLI（双向蕴含合并 / 单向支持 / 矛盾进队列） | ◐ | 逻辑已实现；mDeBERTa 未在本仓库 CI 中下载运行 |
| 候选主题 → 正式主题（≥3 篇或确认） | ✅ | `materialize.py:promote_topics` |
| 上下位 `is_a` 候选（一律审核） | ✅ | `materialize.py:_topic_relation_candidates` |
| 重算（L1 → L2/L3，最后应用人工决定） | ✅ | `pipeline/rebuild.py` |
| 回溯重标注（top-k → rerank → 小模型 / 队列） | ✅ | `pipeline/retro.py` |
| 合并 / 拆分 / 确认 / 拒绝及撤销 | ✅ | `pipeline/decisions.py`；撤销后自动排一次重算 |
| 混合检索 + 筛选 + 前 50 重排 | ✅ | `services/search.py` |
| 主题全景 / 主题页 / 局部图 / 论文卡片 / 资产卡片 / 审核队列 | ✅ | `services/views.py`，`frontend/src/pages/` |
| REST API（文档列出的全部端点） | ✅ | `api/app.py` |
| MCP（rhz_ingest / recall / search / get / related / queue / decide，另加 rhz_digest） | ✅ | `mcp_server.py` |
| CLI（search / get / data / recall --from-file / sync …） | ✅ | `cli.py` |
| 远程只读快照、`rhz sync`、ControlMaster 复用 | ◐ | `services/snapshot.py`；dry-run 有测试，真实 scp 未测 |
| 主动召回（相关度 × 遗忘度，摄入时自动召回） | ✅ | `services/recall.py` |
| 每周综合候选、争议清单、反馈调阈值 | ✅ | `services/synthesis.py` |
| 社区划分（graspologic Leiden） | ◐ | `services/communities.py`；未安装 graspologic 时用确定性的标签传播兜底 |
| FSRS 复习（用户 Idea 优先、每日上限、深度版才进复习） | ✅ | `services/cards.py` |
| 词表导出 rhizome-vocab.yaml | ✅ | `services/vocab.py` |
| 中英双语（前端 react-i18next，后端 gettext/Babel，CI 检查 key 对齐） | ✅ | `frontend/src/locales/`，`rhizome/i18n/`，`scripts/check_i18n.py` |
| 设置（全部环境相关项） | ✅ | `config.py`，`<数据目录>/settings.json` |
| 推理后端接口（队列 / 本地小模型 / 插件） | ◐ | `inference/`；Qwen3.5-2B 通过 llama-cpp-python 接入，未在 CI 运行 |
| 夜间批任务、任务表 + 后台 worker | ✅ | `jobs.py` |
| 迁移前自动备份、`rhz backup`、诊断包（不含论文内容） | ✅ | `db/session.py`，`cli.py diag` |
| 检索回归集 / `rhz bench`（G2 门槛） | ✅ | `services/bench.py`，`tests/fixtures/bench-synthetic.yaml` |
| Windows 桌面应用（Tauri 外壳 + PyInstaller 目录版后台 + NSIS 安装包，按用户安装、无需管理员） | ✅ | `desktop/`、`.github/workflows/release.yml`；CI 在干净的 Windows 上全程使用**中文 + 空格路径**：安装到 `C:\软件 工具\Rhizome 程序`，资料库在 `C:\研究 资料\Rhizome 资料库`，收件箱里中文文件名的论文自动摄入，一键写入 Claude Desktop 配置并用该命令跑通 MCP，杀掉外壳后后台自动退出，卸载后资料库保留 |
| CI：Windows + Linux 测试、许可证检查、gitleaks、SPDX | ◐ | `.github/workflows/ci.yml`（第一次推送后才会真正运行） |
| PostgreSQL 后端 | ◐ | 模型和迁移可移植；未在 Postgres 上跑测试；PG 下关键词检索退化为子串匹配 |
| API key 存入系统凭据管理器 | ○ | 目前没有任何 API 插件，所以还没接 keyring；插件出现时一起做 |
| 每月主题变化摘要 | ◐ | `GET /topic/{id}/changes` 提供统计；文字总结按设计交给 Claude（MCP 侧还没有专门的工具） |
| Tauri 自动更新 | ○ | 未启用：需要签名密钥和发布地址，首次公开发布前再接 |
| 代码签名 | ○ | 安装包未签名，首次运行 SmartScreen 会提示"未知发布者" |

## 与设计的偏差（均为有意为之）

1. **向量存储**：向量以 float32 BLOB 存在 `embedding` 表，查询用进程内 numpy 暴力检索，按数据库签名自动失效。
   10⁵ 个 1024 维向量约 400 MB，单次查询毫秒级。接口集中在 `pipeline/graph.py:knn`，以后换 sqlite-vec / pgvector 不影响上层。
   原因：sqlite 扩展加载在部分 Python 发行版里被禁用，先保证零依赖可装。
2. **持久身份用 `entity.key`**（如 `dataset:GSE12345`、`topic:grn inference`），不用自增 id。
   人工决定、复习卡、访问记录都引用 key，这样重算之后它们仍然对得上。人工合并通过 key 重定向立即生效。
3. **`of_modality` 也允许 Work → Modality**：RXF 的 `paper.modalities` 在论文层给出，需要落边才能按模态筛选论文。
4. **user_insights 落成 `proposes` 边，并带 `origin: user`**：13 类边里没有更合适的；节点本身标 `origin: user`、权重 2。
5. **没有 NLI 时的论断矛盾**：用“文字几乎相同但否定词奇偶不同”的启发式，把这类论断对送进矛盾审核，而不是合并审核
   （否则 lexical reranker 会建议把“先于”和“并不先于”合并）。
6. **内置轻量模型**：默认的 `hashing` 向量和 `lexical` 重排不依赖任何下载，确定性、可在远程快照上直接用；
   效果明显弱于 bge-m3 / bge-reranker。**阈值（0.9 / 0.6 等）目前没有校准**，换模型后要在 G3 标注集上重新标定。
7. **回溯标注的边**：本地小模型高置信度的结果写成一条 `retro_tag` 类型的 L1 抽取记录，重算时重放；
   人工在队列里接受的写成 `human_decision`。重算分两段：先重放论文级抽取并应用人工决定（用户新建的主题此时才存在），再重放回溯标注。
8. **RXF v1 在骨架上加了几个可选字段**（`claims[].stance`、`datasets[].role` 等），见 `rxf-spec/README.md`。

## 第一轮代码审查后的修正（2026-10-03）

独立审查发现 12 处正确性问题，均已修复并加了回归测试（修复前这些测试全部失败）：

| 问题 | 修正 |
| --- | --- |
| 论文节点在 tldr 写入前就被索引，摘要内容搜不到 | `materialize_rxf` 结束时重新索引论文；已存在的锚定资产更新属性后也重新索引 |
| 导出文档的 `work_key` 在解析前计算，预印本/正式版合并后卡片看不到新导出 | 物化后把 L0/L1 行改到真正的节点 key；人工合并两篇论文时同样迁移 |
| 应用失败的人工决定仍被持久化；合并到不存在的目标会污染重定向表 | 先应用再入库，失败抛 `DecisionError`（API 返回 422）；合并要求同类型、来源存在、不成环 |
| 重算把全部 `created_at` 重置为当前时间，摘要和遗忘度失真 | 重放时用抽取记录（或决定）的时间戳创建实体和边 |
| 进程崩溃后 `running` 任务永远卡住，夜间批任务不再调度 | worker 启动时重新排队；6 小时以上的 running 不再阻塞调度 |
| 进程内向量索引对原地替换的向量不感知；跨进程重算后索引错位 | 索引随写入增量更新；签名加入 `kv.embedding_version` |
| 收件箱一个文件抛异常会杀死监听线程；文件先移动后提交 | 每个文件独立事务，提交成功后才移动；异常写入 error/ 报告 |
| `POST /ingest` 在事件循环上同步跑网络和模型 | 放到线程池 |
| 拒绝过的边再 `add_edge` 无效 | 显式置为 confirmed |
| 审核队列里过期的合并项（一方已被合并走） | 跟随重定向；两边已是同一实体则标记 obsolete |
| 前端把无时区的 UTC 时间当本地时间 | 前端按 UTC 解析 |
| `rhz ingest` 不带 `--move` 时忽略同名 PDF | 两种客户端都支持附 PDF |

## Windows 打包时在真机上发现并修复的问题（2026-10-03）

这些问题在 Linux 开发环境里都不会出现，是 Windows CI 抓到的：

| 问题 | 影响 | 修正 |
| --- | --- | --- |
| 数据库路径里的 `D:` 被 Alembic 配置转义成 `D%3A`，`configparser` 又把 `%` 当插值 | 每个 Windows 用户首次启动就崩溃 | 转义 `%`；只读快照改用标准 `file:///D:/…` URI |
| `with sqlite3.connect()` 不关闭连接，Windows 不允许移动仍被打开的文件 | 快照、迁移前备份、迁移数据目录全部失败 | 显式关闭连接后再替换文件 |
| 干净安装会解析到刚发布的 mcp 2.x（`FastMCP` 改名） | MCP 服务器无法启动 | 直接依赖加大版本上限；安装包按 `desktop/constraints.txt` 锁定版本构建 |
| MCP 工具里首次懒加载 numpy 的 C 扩展时，stdin 读取线程正阻塞在管道上 → 死锁 | 在 Claude Desktop 里一调用就卡死 | 启动 stdio 之前预加载模块并打开资料库 |
| `pyproject` 引用了上级目录的 README，且 `web/` 没在 package-data 里 | 非可编辑安装失败或没有界面 | 修正打包配置；CI 检查界面是否已打包 |

### 中文路径专项（2026-10-03）

- 安装器原为"所有用户/当前用户"两可模式，NSIS 的多用户初始化会把安装目录重置为 Program Files，命令行 `/D=` 指定的目录（包括中文目录）被忽略；改为按当前用户安装后，安装目录与图形界面选择的目录走同一路径，已在 Windows 上验证中文目录。
- 用户手动编辑的 JSON（设置、资料库位置指向文件、Claude Desktop 配置、token）改为兼容 BOM 读取：旧版记事本保存的 UTF-8 文件带 BOM，原来会被静默忽略。
- 本地测试覆盖中文+空格的资料库、收件箱文件名、备份、快照、只读模式、迁移资料库和 Claude 配置路径。

## RXF 导入：一次真实失败后的修正（2026-10-04）

一份导出报了 36 行错，实际只有 5 个原因。导出端的问题（`transfer` 被摊平、`topics` 多了 `new`）已由新的导出 prompt 约束；软件这边做了以下修改：

| 项 | 修改 |
| --- | --- |
| schema 单一来源 | 删除包内的 JSON Schema 副本，校验器在运行时由 Pydantic 模型生成；`rxf-spec/schema/` 下的文件只是发布用的导出物，契约测试保证它与模型一致 |
| 新字段 | 顶层 `id`、`exported_at`、`language`；topics / claims / datasets / methods / ideas / issues / user_insights / review_cards 可带文件内 `id`。都可选，旧文件继续有效 |
| 引用校验 | 文件里只要声明了 id，`links_to` 和 `about` 就必须是本文件里的 id，指向 issue 或复习卡也会报错（它们不是图上的节点）；重复 id 报错。与 schema 错误一起报告 |
| 引用变成边 | 入库时建立“文件内 id → 库内实体”映射：用户观点 → claim / method / dataset / idea 为 `relates_to`（新增的第 14 种边），→ topic 为 `applicable_to`；`review_cards.about` 和 `transfer.to` 也按 id 解析。rebuild 后不变 |
| 报告按原因归并 | 按“路径模式 + 字段”合并，例如 `claims.*: 不允许的字段 score（10 处）`，每个原因附一句修正提示；摊平的 transfer 产生的三条错误合并为一个原因 |
| 不静默修复 | 已知漂移只在用户要求时修正：`rhz ingest --repair`，或首页“收件箱中未通过的文件”里的“修正后导入”。L0 保留原始字节，L1 的 `meta.repairs` 记录修了什么 |
| 收件箱 | 按内容识别 RXF（顶层有 `rxf_version:`），不看文件名和扩展名；`.pdf`、下载中的临时文件、隐藏文件不处理；文件名含空格和中文已测试 |
| 测试 | 正例 `rxf-spec/examples/deep-with-ids.yaml`，负例 `rxf-spec/examples/invalid/export-drift.yaml`（摊平的 transfer、`new`、指向不存在 id 的引用），可修复样例 `backend/tests/fixtures/drift-repairable.yaml`；`backend/tests/test_rxf_ids.py` |

## 第一周修复（工程审查后，2026-10-05，v0.1.3）

按 `docs/engineering-review.zh.md` 第 5 节的顺序完成：

| 编号 | 修改 |
| --- | --- |
| T14 | 设置优先级改为 显式参数 > 环境变量 > settings.json；测试清空所有 `RHIZOME_*` 和系统目录变量，不会再写进真实资料库 |
| T1 | `ci.yml` 在 `claude/**` 推送时运行；release.yml 发布前用锁定依赖跑单元测试和 ruff；ruff 固定版本 |
| D5(1)、D4 | 每日备份（启动时和每 30 分钟检查一次，超过 20 小时补做），按类型保留；备份目录可配置；重算前的备份 10 分钟内只做一次，失败只告警 |
| D1 | 重放时过期的决策跳过并在重算结果里列出，不再中止重算 |
| G7、G6 | settings.json 只保存改过的键，保留未知键，原子写入并留 .bak；坏字段跳过并报告 |
| A3、G1 | MCP 在应用重启（新端口/新 token）后自动重连；Claude 配置不再固定资料库路径，迁移时清理旧路径 |
| A7、P2 | 空上下文、无 import 脚本、notebook、作业调度指令；FTS 关键词去重、去停用词、最多 12 个 |
| M2 | 无法加载的模型给出明确报错（API 503、页面横幅、一键改回内置），收件箱文件原地保留 |
| D3 | 重算保持实体和待审项的编号；`rhz_related` 接受 key |
| X1 | `GET /rxf/instructions`、设置页“聊天 Project 设置”、MCP 工具 `rhz_rxf_guide` |
| D11 | 带 PDF 的深度导出记为 T2 |

下一批（第一个月）从迁移基础设施开始：U1 → H2 → T4，必须在任何 0002 迁移之前完成。

## 第一个月修复（2026-10-05，v0.1.4）

按 `docs/engineering-review.zh.md` 第 5 节顺序完成：

| 组 | 编号 | 修改 |
| --- | --- | --- |
| 迁移 | U1 H2 T4 U2 U3、D8 P8 | `BEGIN IMMEDIATE` 迁移引擎，迁移前备份、失败回滚；`SchemaTooNew`（退出码 3）；从备份恢复；0002 重建 FTS（rowid = 实体 id），0003 `job.owner_pid` |
| 并发 | C1 C2 O5 G5 | 任务合并、按进程认领与恢复；重算期间写入返回 503；夜间任务分段并退避 |
| 模型 | M1 M10 M11 M3 | KV `embedding_model` + `check_index_model`，`IndexStale` 返回 503；无 NLI 时不盲目合并论断 |
| 纠错 | D28 D26 D2 A15 | 决策 `retract` / `reject_entity`，导入 `replace`，决策历史与撤销，复习卡稳定身份 |
| 导入 | N1 N3 N4 N5 N7 N6 N9 D13 D21 D9 | GSA 编号、DOI/仓库规范化、物种名查询、OpenAlex 状态与 `enrich` 补全、系统证书、按主机记录网络状态 |
| 规模 | P3 P5 P9 | 卡片每类边 50 条 + 分页接口；主题页 SQL 聚合每列 150；中文 3 字窗口；重排使用别名 |
| 可见性 | O1 O2 F3 F4 L1 L2 C4 G3 G2 | 按角色分日志、请求编号；前端错误边界与提示；桌面壳监护后端（重启码 75、找不到资料库码 4）；迁移用临时目录；找不到资料库不新建；收件箱状态 |
| 安全 | S11 | 快照同步 umask 077 / chmod 600 |

## 远程 / HPC 组（2026-10-05）

| 编号 | 修改 |
| --- | --- |
| A1 | FTS 查询失败（旧 libsqlite3 没有 trigram）时记录一次并退回别名匹配，不再报错；快照与程序的数据库版本不同会提示；`rhz diag` 记录并打印 SQLite 版本 |
| A2 | 快照改为回滚日志（头字节 18/19 = 1,1），只读打开时加 `immutable=1`，不留 -wal/-shm 文件，可在只读目录和 NFS/Lustre 上使用；在线库仍按 WAL 打开 |
| A5 | `--snapshot` 下写命令（ingest、rebuild、nightly、backup、sync、settings set、watch…）退出码 2；vocab、bench、diag 直接读快照 |
| A6 | 读命令遇到没有资料库时报错（退出码 2）并提示 `--snapshot` / `RHIZOME_SNAPSHOT`，不再悄悄新建空库；`rhz sync` 成功后打印 export 行 |
| A17 | `requires-python >= 3.10`，CI 增加 3.10 任务；README 写明 pipx / uv / conda / 离线 wheelhouse 安装 |
| A18 | 快照模式沿用当前设置（语言、阈值、模型目录），并按快照的 `embedding_model` 选用嵌入模型；本机加载不了时提示 |
| G10 | `rhz sync` 只开一个 SSH 会话（快照经 stdin 流入远端 `cat >`），带超时（远程配置 `timeout`，默认 1800 秒），失败给出本地化说明；Windows 上设置了 control_path 会提示 |
| A19 | 下载提示区分单个 run（prefetch）与研究/项目（ENA filereport 列 run），GEO 系列给出 suppl FTP 路径，Zenodo 不再落到"检索" |
| A20 | 快照内写入 `kv.snapshot_meta`（生成时间、版本、schema），超过 7 天提示 |

## 数据质量组（2026-10-05）

| 编号 | 修改 |
| --- | --- |
| D10 | 用户想法与模型想法不再自动合并（进待审）；接受合并后保留用户的措辞和 origin |
| D12 D20 | 论文立场为 contradicts 时不再对其蕴含的论断写 supports 边；矛盾待审项带立场、证据类型、强度和 extraction id，接受后写入边的列（立场相反时写 supports） |
| D15 | 另一份导出重报的边整体替换证据/置信度/属性，旧记录存入 `attrs.evidence_records`；同一份导出内仍取最大值 |
| D17 | 多篇论文报告的数据集/方法属性：空位填入、产出方优先、冲突记入 `attrs.reported` |
| D18 | 文件内 id（d1、t2）不再进入属性和全文；idea 的 transfer 存主题名和 key；迁移 0004 清理旧库 |
| M6 | 人工合并的任一侧尚未物化时按自己的 key 创建且不自动合并；来源已并到别处的合并作为跳过项报告 |
| D6 | 读写引擎 `synchronous=FULL`；原始文件 mkstemp + fsync + rename；截断文件会重写；`rhz diag` 检查原始文件 |
| D7 | 重算和 `rhz gc` 清理其他模型和无主文本的向量缓存（gc 并 VACUUM）；快照去掉向量缓存、他模型向量、访问记录、任务行并压缩 |
| P1 P7 | 向量索引预分配缓冲、逻辑长度、O(1) 追加、交换删除；按行数流式加载、单飞；应用和 MCP 启动时后台预热；`token_path` 移到 config |
| P10 | 合成候选按块矩阵乘，最优在前、每个源至多 2 条，只对够好的对走图检查，从上次水位线扫描 |
| P4 | `neighbors` 两跳都是子查询，不穿过 organism/modality 枢纽，SQL 分页计数，只读页内节点间的边 |
| M12 | `/entity` 支持 `touch=false`；Claude 的 `rhz_get` 不再算作"看过" |
| M13 | `POST /cards/{id}/suspend`（可整资产 `no_cards`）；界面 `s` 键与按钮，CLI s / S / k；被否决资产的卡片查询时过滤 |
| M15 | 模板想法卡以 transfer 目标或关联资产为线索；无线索的模型想法不出卡；生成卡揭晓前隐藏资产名 |
| M7 | 无本地判官时每次 retro 只排 `retro_queue_max` 条并报告剩余，重跑翻页；`POST /review/dismiss`；重定义主题使旧候选失效 |
| D19 | `ex.meta.insight_keys` 记录每条 insight 对应的实体；论文卡可就地改写（edit_text），`rhz edit KEY TEXT` |
| X6 | 从正文任意位置的围栏块（``` 或 ~~~）或 `rxf_version:` 行开始取文档；收件箱识别同样放宽 |
| X2 | 报错提示改为"移到 assets 下 / 改名为 text、q、a、venue、links_to"，缺枚举字段列出可选值，列表项类型错时列出所需键，原因 ≥4 条提示重读骨架 |
| X4 | 词表默认包含有论文的候选主题（单独 `candidate_topics` 段，标状态和论文数）；CLI `--no-candidates`，设置页勾选 |
| M4 | RXF topics 可带 `aliases`（导出指令已更新）；缩写/全称首字母匹配进待审；设置页提示内置模型只做字面匹配 |
| M5 | 阈值按嵌入模型分档（`THRESHOLD_PROFILES`），用户显式设置的值跨模型保留；0.35/0.2/0.3 字面量进 `Thresholds` |
| U4 | 重算前按存储版本预检所有导出，失败则中止不删（`--force` 跳过并列出，退出码 1）；`parse_stored` 按版本解析并 upcast |
| U5 | `NORM_VERSION` 与黄金测试锁定 norm/free_key 输出，注释写明改动所需的数据迁移 |
| D30 | OpenAlex 响应存为 L0，`<sha>.meta.json` 记录文件名、时间、PDF/OpenAlex 哈希、修复；`rhz recover --from-raw` 离线重放并保留原时间 |

## 测试组与前端组（2026-10-05）

| 编号 | 修改 |
| --- | --- |
| T2 | release.yml：同一 ref 的发布串行；生成 `SHA256SUMS.txt` 并随安装包发布；推 tag 时校验 tag 与版本一致；同一版本从另一提交再发布会失败而不是静默跳过；发布说明里的链接指向该版本的 tag |
| T3 | `Worker.step()` 从循环里抽出并同步测试（任务、每日备份、按墙钟的夜间检查）；TestClient 生命周期测试确认工作线程和收件箱线程启停；`_Handler` 在中文路径上的事件测试。顺带修了 `Worker._stop` 遮住 `Thread._stop()` 的旧 bug |
| T5 | 真实 uvicorn 端口上的 `HttpClient` 与 `LocalClient` 参数化对比（含错误用例）：404/422 统一为 `{ok: false, error}`，分页上限 `MAX_LIMIT` 两边共用，MCP `_call` 把所有失败归一化 |
| T11 | spawn 两个进程各自循环导入的并发测试；`bench` 标记（`RHIZOME_BENCH=1`）在约 2k 篇的合成库上打印检索/卡片/两跳图耗时 |
| F1 | 启动时读 `/health.language`（最多等 1.5 s）再渲染，localStorage 只作首屏猜测 |
| F2 | 桌面壳用 `location.replace` 进入应用，token 捕获用 `replaceState`：后退不再回到加载页 |
| F5 F6 | 搜索分页：浏览模式的过滤全部下推到 SQL 并返回精确总数，文本搜索返回 `has_more`；界面"加载更多"、诚实的计数文案；搜索页按去掉 offset 的查询串作 key，顶栏搜索后表单同步 |
| F9 | `decode_rxf`：UTF-8/UTF-16（带 BOM）/明显的 GB18030，其余给出"另存为 UTF-8"的本地化提示（API 422、收件箱报告、CLI 均如此）；拖放区有忙碌状态，导入中忽略再次拖放，嗅探放进 try |

## low 组（一）：后端安全与 API 卫生（2026-10-05）

| 编号 | 修改 |
| --- | --- |
| S1 | `RemoteTarget` 字段校验（不以 `-` 开头、无空白/控制字符、ssh_options 单 token 且禁止 ProxyCommand/LocalCommand）；ssh 命令里主机前加 `--`；`PATCH /settings` 拒绝修改 remotes（只能改文件或用 CLI） |
| S2 | `GET /settings` 与 `rhz diag` 的 `database_url` 隐去密码（`redact_url`） |
| S3 | 桌面模式（设 RHIZOME_API_TOKEN）下 `rhz serve` 的启动行不再打印 token |
| S5 | `server.json` 的 pid 已死则忽略标记；`/health` 带 `app: rhizome`，客户端要求它且捕获非 JSON；默认端口只在显式配置或无标记时探测 |
| S6 | 每次 `rhz serve` 生成新 token（CLI/MCP 连接时重读文件） |
| S7 | `rhz serve` 监听回环时拒绝非回环 Host（421，防 DNS rebinding）；`/health` 未认证只返回 ok/app/read_only，版本和语言需要 token |
| S8 | MCP 工具带 ToolAnnotations（读工具 readOnlyHint，decide/correct/undo destructiveHint）；`rhz_decide` 返回里附 undo 提示 |
| S9 | 非 ASCII 的 bearer 值返回 401 而不是 500 |
| A8 | 重复提交同一决策返回已有记录，不再新增审计行 |
| A9 | `rhz_ingest` 增加 `repair` 参数，可修复时返回 "repair=true 重新调用" 的提示 |
| A10 | `rhz_search` 增加 year_max/edge_type/offset，CLI `search --offset`，`/decisions` 上限 200 并返回 total |
| A11 | HTTP 客户端发现服务端版本不同会在 stderr 警告一次 |
| A12 | 决策的文本字段不能为空（去首尾空白）；`POST /topic` 把 DecisionError 映射为 422 |
| A13 | 同一导出再次带 PDF 提交时附上 PDF（`pdf_attached`），不再静默丢弃 |
| A14 | 附件必须以 `%PDF` 开头；RXF 5 MB、PDF 200 MB 上限 |
| X3 | 缺失必填字段按 jsonschema 的每条错误只报一次，计数正确 |
| U6 | 带引号的 `"1"` 或 `1.0` 的 rxf_version 自动按数字读取并记为修复；错误信息用 repr |
| N11 | OpenAlex 响应结构异常只记日志并按 transient 处理，不再中止导入 |
| M9 | 复习"一天"按本地时间、凌晨 4 点翻日；新卡按优先级和创建时间（迁移 0005 `review_card.created_at`）排序 |
| M14 | 本地模型生成的卡也保留用户想法的优先级 |
| M16 | 首页"待复习"数按每日上限计算 |
| D16 | 后来的 light 导出不覆盖 deep 导出的 tldr/作者/期刊（只补空） |
| D22 | about 指向被判定虚构而未入库的条目的复习卡不再挂到论文上 |
| D23 | evidence_type/boundary 只记在边上，不再写进论断实体 |
| D14 | 由 D15 的按导出替换边记录覆盖 |
| F15 | `/` 与 `/ui/*` 发 `Cache-Control: no-cache`，`/assets/*` immutable |

## low 组（二）：前端细节（2026-10-05）

| 编号 | 修改 |
| --- | --- |
| F7 | 换页时滚动回顶部（只改筛选不滚）；复习队列的种类、主题页的角色筛选放进 URL |
| F8 | 快捷键忽略长按重复、输入法合成和 Ctrl/Alt/Meta 组合；评分与审阅动作有进行中保护，不会重复触发 |
| F10 | 实体页、主题页按 id 作 key，切换时不再短暂显示上一个的内容 |
| F11 | `useRefreshOnFocus`：窗口回到前台或本应用写入后（`rhz:changed` 事件）自动刷新首页统计、待审数、队列 |
| F12 | 确认/否决边后重新加载卡片与局部图，行的删除线、标签、图同步；请求中禁用按钮，错误以提示显示 |
| F13 | 召回有进行中与错误状态，显示已读取字数 / 4000 上限 |
| F14 | 弱化色加深（亮/暗两套），类型徽标文字加对比，队列行和拖放区可用键盘操作 |
| D25 | 论文卡显示摘要（可折叠）、作者、DOI / 开放获取 / 原文链接、类型标签；问题条目显示位置；边上显示证据类型、逻辑跳跃、边界 |
| F16 | 实体标题下显示界面语言对应的别名（中英互补） |
| M19 | 评为"重来"的卡在学习步骤到期后回到本次复习，页面显示等待张数与时间 |

## low 组（三）：桌面与运维（2026-10-05）

| 编号 | 修改 |
| --- | --- |
| O3 | 桌面壳每次启动把 `backend-console.log` 轮转为 `.prev.log`；通过 `RHIZOME_CONSOLE_LOG` 告知后端，`rhz diag` 打包这两份；README 与 CI 的日志路径改为 `%LOCALAPPDATA%` |
| O4 | CLI 回调与 MCP 入口启用 `faulthandler`（全线程），本机崩溃在控制台日志里留下栈 |
| L3 | Windows 下父进程监视改为持有 SYNCHRONIZE 句柄并 `WaitForSingleObject`，pid 复用不再留下孤儿后端 |
| L4 | 安装器钩子：检测到 Rhizome 在运行时先弹双语 确定/取消，取消则中止安装；确认后再结束 Rhizome.exe 与 rhz.exe |
| L5 | 默认窗口 1200×800、最小 800×480、`preventOverflow`，小屏笔记本不再溢出 |
| L6 | 工作线程每天向 GitHub 查询最新 release（`check_updates` 设置，离线不查），结果存 KV；首页统计附带 `update`，界面显示可按版本关闭的横幅 |
| S4 | Windows 上新建的资料库目录（含迁移目标）用 icacls 设为仅本账户 + SYSTEM；用户目录之外的位置给出提示；不改动已有非空目录和盘符根 |
| S10 M8 | 模型从解析后的快照目录加载并命名为 `<key>@<commit7>`（不同权重的向量不混用）；`REVISIONS` / `RHIZOME_MODEL_REVISION_*` 可固定 commit；只下载 safetensors/tokenizer；离线只用本地文件，缺失时提示 `rhz models download`；网络失败提示 HTTPS_PROXY / HF_ENDPOINT；去掉 ONNX 路径与 onnxruntime 依赖 |
| S1 | tauri.conf.json 设置 CSP |
| T13 | 发布流程重新定位离线安装包并断言它比标准版大 100 MB 以上且哈希不同（此前复制的是标准版） |
| T7 | CI 增加按 `desktop/constraints.txt` 安装的 Windows 单元；constraints 固定 PyInstaller |
| T12 | `check_licenses.py` 把许可证列表视为可选项、空输入报错、忽略构建工具；发布时在受约束环境运行 pip-licenses |
| H3 | README / 设计文档：NSIS 而非 MSI、14 类边；`scripts/check_version.py` 进 CI；安装后校验 `/health.version` 与安装包版本一致 |
| H5 | `scripts/gen_third_party_notices.py` 汇总 Python / npm / Rust 依赖与字体许可，生成 `THIRD-PARTY-NOTICES.txt` 随安装包发布 |

## 还需要你来做的（M0）

- 定稿导出指令：`rxf-spec/instructions/` 里是按设计文档整理的版本，加了一条“含问号的文本不要写进花括号”——
  写示例时真的踩到了这个 YAML 坑。
- 预注册 benchmark 问题和门槛：按 `rhz bench` 的格式写 `questions.yaml`，放在仓库之外（含个人数据）。
- 准备 G3 标注集（约 100 对实体、50 对论断），用来校准规范化阈值和 NLI。
- 选定真实模型后：`pip install "rhizome[models]"`，`rhz settings set embedder bge-m3`，然后 `rhz rebuild`。
  **安装版桌面应用不含 torch / sentence-transformers，只能用内置模型**；用真实模型时改用 pip 安装的
  `rhz serve`，并把 Claude Desktop 的 MCP 指向这个 `rhz`。两者共用同一个资料库时，安装版会在每个页面顶部提示
  “模型无法加载”，可一键改回内置模型并重算。
