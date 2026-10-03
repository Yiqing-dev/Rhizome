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
| Tauri 桌面外壳 + PyInstaller sidecar + MSI | ◐ | `desktop/`；本环境没有编译，`release.yml` 尚未在 Windows runner 上跑过 |
| CI：Windows + Linux 测试、许可证检查、gitleaks、SPDX | ◐ | `.github/workflows/ci.yml`（第一次推送后才会真正运行） |
| PostgreSQL 后端 | ◐ | 模型和迁移可移植；未在 Postgres 上跑测试；PG 下关键词检索退化为子串匹配 |
| API key 存入系统凭据管理器 | ○ | 目前没有任何 API 插件，所以还没接 keyring；插件出现时一起做 |
| 每月主题变化摘要 | ◐ | `GET /topic/{id}/changes` 提供统计；文字总结按设计交给 Claude（MCP 侧还没有专门的工具） |
| Tauri 自动更新 | ○ | 已接 updater 插件，但 endpoints / 公钥要在首次公开发布前配置 |

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

## 还需要你来做的（M0）

- 定稿导出指令：`rxf-spec/instructions/` 里是按设计文档整理的版本，加了一条“含问号的文本不要写进花括号”——
  写示例时真的踩到了这个 YAML 坑。
- 预注册 benchmark 问题和门槛：按 `rhz bench` 的格式写 `questions.yaml`，放在仓库之外（含个人数据）。
- 准备 G3 标注集（约 100 对实体、50 对论断），用来校准规范化阈值和 NLI。
- 选定真实模型后：`pip install "rhizome[models]"`，`rhz settings set embedder bge-m3`，然后 `rhz rebuild`。
