# Rhizome 开发文档

Oct 5, 2026 · @YIQING WANG

## 概述

Rhizome 是一个个人文献资产图谱。它把论文拆成数据、方法、思路等可复用资产，用类型化的边连成一张可以回溯重组的知识图，并在工作现场主动召回。它按通用桌面软件来开发：单用户、可以安装分发，测试完成后开源，界面支持中英双语。内核不调用任何 LLM API：需要生成和推理的工作在聊天里完成，本机只运行几个小模型。

**命名**：Rhizome（地下茎）没有主根，任意一节都能长出新根，并与其他节相连。这对应系统的三个特性：不按树状结构分类；任意维度之间可以横向连接；新建的概念能够长回旧节点（回溯重标注）。CLI 简称 `rhz`。

**目标**

- 存入时不需要做任何结构决策：原始材料全量保存，结构事后生成，并且可以重算。
- 一篇论文里的每个可复用资产，都能被独立检索、独立连接。
- 解决三种找回失败：记得做过但不知道是哪篇；知道是哪篇但找不到具体资产；完全忘了读过。
- 支撑知识内化：主题全景图、定期综合跨论文的联系、间隔复习。
- 只收录读过并讨论过的论文；架构按 10⁵ 篇的上限设计，明确接受前期使用收益较低。

**非目标**

- 不做 PDF 阅读器，也不替代引用管理工具。
- 不做多人协作，也不批量导入没读过的文献。
- 不替代 AI 讨论本身：讨论仍然在 Claude 或 ChatGPT 中进行，Rhizome 只消费讨论的结果。

## 需求

需求共 12 条，来自前期访谈和后续确认。每一条都对应到具体的设计响应，后面各章不再重复说明理由。

| 编号 | 需求 | 设计响应 |
| --- | --- | --- |
| R1 | 输入是在 Claude 或 ChatGPT 讨论结束时导出的规范化文档（可附 PDF），从现在开始积累，不导入历史记录 | RXF 导出格式；收件箱目录自动摄入 |
| R2 | 存入时不知道哪些东西将来有价值 | 原始层不可变；结构延迟生成，可以重算 |
| R3 | 投入分档：默认零成本，重要论文愿意深挖 | 导出文档分轻量版和深度版；全文增强为可选项 |
| R4 | 资产是多维的：算法、数据、思路、物种、模态、问题 | 异构实体 + 类型化边；主题与角色正交 |
| R5 | 三种找回失败：不知道哪篇、找不到资产、完全忘了 | 资产级语义检索、资产卡片、主动召回 |
| R6 | 四个调用场景：和 AI 讨论方案、在 HPC（如 Palmetto）上写代码、写论文/grant/review、读新论文 | API 优先；UI、MCP、CLI 三个客户端；远程服务器上读只读快照 |
| R7 | 内化：主题全景图、系统综合跨论文的联系、Anki 式复习 | 主题聚合视图、定期综合任务、FSRS 复习 |
| R8 | 本地部署，Windows 优先 | Windows 原生运行，不依赖 Docker 或 WSL2 |
| R9 | 要能处理大量文献，但只限读过并讨论过的；前期收益低可以接受 | 架构按 10⁵ 篇设计；不做未读文献的批量导入 |
| R10 | 记录论断，以及论文之间的支持与反驳关系 | Claim 实体 + supports / contradicts 边 |
| R11 | 按通用软件开发，不绑定个人环境 | 单用户桌面应用 + 安装包；服务器、LLM 提供方、路径等全部放进设置 |
| R12 | 界面支持中英双语 | 全量 i18n；界面语言与内容语言分开处理 |

## 设计原则

九条原则，有冲突时按编号顺序取舍。

1. **原始层不可变，派生层可重算。** 每个派生结果都记录模型、提示词版本、schema 版本和生成时间。换模型或改 schema 时，整体重跑即可。
2. **延迟结构化。** 存入时不做分类；结构由抽取和规范化生成。新建一个概念后，要回溯应用到全库。
3. **资产是一等公民。** 数据集、方法、思路都是独立的节点，各有独立的向量。论文只是它们的来源。
4. **主题与角色正交。** 主题层级里只放主题；“数据 / 算法 / benchmark”是边上的角色，不是子主题。边的类型要区分“关于”和“可用于”。
5. **优先锚定外部 ID。** 能用 accession、NCBI Taxonomy、repo URL 的，就不用自由文本。
6. **API 优先。** 所有能力先做成 API，UI、MCP、CLI 都只是它的客户端。
7. **成本有上界。** 任何全库操作都先用向量预筛，模型只处理筛出来的候选。
8. **证伪驱动。** 每个里程碑都有预注册的验收门槛；一个功能层只有在被某种失败模式证明必要之后，才会加进来。
9. **LLM 在外，软件在内。** 内核不调用任何 LLM API。生成和推理在聊天里完成，通过 RXF 进入、通过 MCP 回调；本机只运行 embedding、reranker、NLI 和一个可选的小模型。

## 系统架构

&#91;embedded content: Rhizome 五层架构 · 数据自上而下流动\]

数据从 L0 往下流到 L4。层与层之间的每一步都是幂等的批处理，可以单独重跑。人工决定不属于任何一层，它在重算的最后一步覆盖到 L2 和 L3 上。流水线 worker、规范化服务和夜间批任务都是同一个 FastAPI 应用里的模块，彼此通过 数据库里的任务表解耦。

## 数据模型

数据模型是一张异构图：8 类实体，14 类边。“GRN 下的算法”不需要存成子主题，它是一次查询：取指向 GRN 的边，再按起点的实体类型或边的类型筛选。

**实体**

| 类型 | 锚定 ID | 说明 |
| --- | --- | --- |
| Work 论文 | OpenAlex work ID；DOI 作为别名 | 预印本和正式发表版合并为同一个节点 |
| Dataset 数据集 | GEO / SRA / ENA / GSA / CNGB / ArrayExpress / Zenodo accession；没有 accession 时用 name，按自由概念规范化 | 被多篇论文使用的数据只存一个节点 |
| Method 方法/工具 | repo URL 或 bio.tools ID；没有代码时用自由概念 | 包括算法、软件、分析流程 |
| Idea 思路 | 无（自由文本 + 向量） | 脱离原课题表述的可迁移思路 |
| Claim 论断 | 无（自由文本 + 向量） | 一条可以被支持或反驳的命题，附成立条件；多篇论文可以指向同一个论断 |
| Topic 主题 | 自建词表，从零开始，随新数据扩展 | DAG 结构，允许多个父节点 |
| Organism 物种 | NCBI Taxonomy ID |  |
| Modality 模态 | EDAM ontology | 例如 snRNA-seq、Stereo-seq |

**边**

| 类型 | 起点 → 终点 | 含义 |
| --- | --- | --- |
| about | Work → Topic | 论文研究的就是这个主题 |
| applicable\_to | Work / Dataset / Method / Idea → Topic | 可以用于这个主题，但不是原文的主题 |
| proposes | Work → Method / Idea | 原文提出 |
| uses | Work → Method / Dataset | 原文使用 |
| produces | Work → Dataset | 原文产出 |
| evaluates | Work → Method | 原文做了 benchmark 评测 |
| supports | Work → Claim | 原文证据支持这个论断 |
| contradicts | Work → Claim | 原文证据与这个论断相反 |
| extends | Method → Method | 改进或继承关系 |
| cites | Work → Work | 引用关系，来自 OpenAlex |
| is\_a | Topic → Topic | 主题层级 |
| of\_organism | Dataset / Work → Organism | 物种 |
| of\_modality | Dataset / Method → Modality | 模态 |
| relates\_to | 你的观点（origin 为 user 的 Idea）→ Claim / Method / Dataset / Idea / Topic | 这条观点联系到的节点，来自 RXF 的 links\_to |

每条边都带以下属性：置信度、来源抽取记录、原文证据位置、状态（自动 / 已确认 / 已拒绝）。`applicable_to` 边额外带两个字段：迁移类型（跨物种 / 跨模态 / 跨问题）和迁移障碍。

`supports` 和 `contradicts` 边额外带三个字段：证据类型（因果 / 相关 / 推测）、证据位置、适用边界。导出文档里标了【逻辑跳跃】的论证，对应边的证据强度记为弱。

**表结构**（以 Postgres 写法示意；SQLite 版本由同一套 SQLAlchemy 模型和 Alembic 迁移生成）

```sql
-- L0 原始层：按内容哈希寻址，只追加，不修改
create table raw_object (sha256 text primary key, kind text, uri text, work_id bigint, created_at timestamptz);
-- L1 抽取层：带版本，重算时新增一行，旧行标记为非当前
create table extraction (id bigserial primary key, work_id bigint, tier smallint, model text, prompt_version text, schema_version text, input_hashes text[], output jsonb, is_current bool, created_at timestamptz);
-- L2 实体层
create table entity (id bigserial primary key, type text, canonical_name text, external_id text, attrs jsonb, unique (type, external_id));
create table entity_alias (entity_id bigint, alias text, lang text, source text);
create table work (entity_id bigint primary key, openalex_id text unique, dois text[], year int, tier smallint);
-- L3 图层
create table edge (id bigserial primary key, src bigint, dst bigint, type text, attrs jsonb, confidence real, extraction_id bigint, evidence text, status text);
create table embedding (entity_id bigint, model text, vec vector(1024), primary key (entity_id, model));
-- 人工决定：合并、拆分、确认、拒绝；重算时最后应用，永不被覆盖
create table human_decision (id bigserial primary key, op text, payload jsonb, created_at timestamptz);
```

`human_decision` 是让“可重算”和“人工审核”能够共存的关键。重算会重建派生层，但你拒绝过的边不会被重新提出，你合并过的节点也不会被重新拆开。

## 导出格式（RXF）

RXF（Rhizome Exchange Format）是 Rhizome 唯一的输入格式。讨论结束时，由 Claude 或 ChatGPT 按这个结构输出一份 YAML，你把它保存进收件箱目录即可。论文的结构化抽取在聊天里就完成了，摄入时基本不需要再调用 LLM。

选 YAML 而不选 JSON，是因为逻辑链、思路这类多行文本在 YAML 里不需要转义，LLM 输出时出错更少。入库时先转成 JSON，再用 JSON Schema 校验。

骨架里 # 后为说明：“均必填”表示 light 和 deep 都必填；“必填”表示 deep 必填、light 选填；其余为选填。

```yaml
rxf_version: 1                        # 均必填
exported_at: 2026-10-04               # 均必填；YYYY-MM-DD
prompt_version: deep-review-v2        # 均必填；生成时所用审稿提示词的版本，没有时写 unversioned
instructions_version: "2026.10"       # 均必填；照抄，不要自己编
depth: deep                           # 均必填：light | deep
language: zh                          # 均必填：自由文本的主要语言，zh | en
paper:                                # 均必填
  doi: "10.1101/xxxx"                 # 有则必填；arXiv 写 10.48550/arXiv.<编号>，不带版本号
  title: "..."                        # 均必填，原文标题
  year: 2025                          # 均必填
  venue: "..."
  type: [research, tool]              # 均必填，可多选：research | tool | resource | benchmark | review | protocol
  organisms: [Arabidopsis thaliana]   # 拉丁学名
  modalities: [snRNA-seq, scATAC-seq]
tldr: ["做了什么", "最重要的发现", "最大的保留意见"]   # 均必填，恰好 3 条
topics:                               # 均必填，至少 1 条；优先用词表里的规范名，不标 new
  - {id: t1, name: GRN inference, relation: about, aliases: [gene regulatory network inference, 基因调控网络推断]}   # relation: about | applicable_to
  - {id: t2, name: spatial domain detection, relation: applicable_to}
claims:                               # 必填；取自逻辑链的结论 D
  - id: c1                            # 文件内 id：t / c / d / m / i / u + 序号
    text: "..."
    evidence_type: causal             # causal | correlational | speculative
    evidence: "Fig. 3B"               # 原文位置，均必填
    boundary: "..."                   # 适用边界
    logic_jump: false
    stance: supports                  # supports | contradicts；原文证据与该命题相反时写 contradicts，否则可省略
assets:                               # 有则必填
  datasets:
    - id: d1
      name: "..."                     # 通用短名，不超过 5 个词；没有 accession 时必填
      accession: GSE000000            # 原文给出才写
      database: GEO                   # 必填：GEO | SRA | ENA | GSA | CNGB | ArrayExpress | Zenodo | other
      role: produces                  # produces | uses
      organism: "..."                 # 非生物数据删除
      tissue: "..."
      modality: "..."
      scale: "..."                    # 按原文转述
      public: true                    # true | false | unknown
  methods:
    - id: m1
      name: "SCENIC+"                 # 通用短名，不超过 5 个词
      role: proposes                  # proposes | uses | evaluates
      extends: ["SCENIC"]             # 被它改进的方法的短名
      repo: "https://github.com/..."
      io: "输入 → 输出"
      maintained: unknown             # true | false | unknown
  ideas:
    - id: i1
      text: "..."                     # 脱离原课题的一句话
      transfer: {type: cross-species, to: t2, barrier: "..."}   # 必须嵌套；type: cross-species | cross-modality | cross-problem；to 填主题 id；barrier 选填
      origin: model                   # model | user
issues:                               # 必填
  - {severity: major, location: "...", text: "...", test: "..."}   # severity: minor | major | critical
user_insights:                        # 只收录我在讨论中说出的判断和联想
  - {id: u1, text: "...", links_to: [c1, m1]}   # 只能引用本文件内的 id
review_cards:                         # 必填，3–5 张
  - q: "..."                          # 只能引用本文件内的 id
    a: "..."
    about: d1
```

**文件内 id**：`id` 只在单份 RXF 内有效，作用是让 `links_to` 和 `about` 有确定的引用对象。入库时它们会被解析成库里的实体 ID，本身不进入 L2。`id` 是可选字段；但只要 `links_to` 或 `about` 引用了某个 id，这个 id 就必须在本文件里存在，否则校验失败。

**数据集标识**：数据集靠 accession 或 name 识别，两者至少有一个，否则校验失败。有 accession 时，database 填它所属的库（GEO / SRA / ENA / GSA / CNGB / ArrayExpress / Zenodo），不在枚举里的库填 other；没有 accession 时（非生物数据、需要订阅的数据、自建数据），name 必填，database 填 other。name 只写原文或领域里通用的短名，例如 GKX、nuScenes、Tabula Sapiens；没有通用名时用“第一作者 年份 + 数据类型”。name 要进别名表做精确匹配，写成长描述就不可能和其他论文里的同一份数据对上；描述性内容写进 tissue、modality、scale。数据集名和托管平台名（Hugging Face、GitHub、Kaggle）不写进 database。

**布尔值**：`maintained` 和 `public` 的取值是 true / false / unknown，schema 里的类型为布尔值或字面量 "unknown"（历史导出里的 "yes" / "no" 也接受）。导出端不能写 yes / no：PyYAML 按 YAML 1.1 解析，会把 yes、no、on、off 转成布尔值。

**导出指令**：放进 Claude 和 ChatGPT 的 Project 指令时，先放下面这段规则，再放上面的骨架；两段一起单独贴进对话也能用。之后每次讨论结束，只需要说一句“导出 RXF”。中英两个版本由 `rhz rxf instructions`（或设置页、MCP 的 `rhz_rxf_guide`）给出，下面是中文版。

```
【Rhizome 导出规则 · RXF v1】
当我说"导出 RXF"或"export RXF"时，按以下规则和其后的骨架输出本次讨论的论文。骨架里 # 后为说明："均必填"表示 light 和 deep 都必填；"必填"表示 deep 必填、light 选填；其余为选填。

输出
1. 能创建文件时，生成一个 .yaml 文件，文件名为 {year}_{第一作者姓}_{标题前3个词}.yaml：全部小写，词之间用 - 连接，只保留 a–z、0–9、- 和 _（例：2026_wang_birds-eye-view-informed-reasoning.yaml）。不能创建文件时，只输出一个 yaml 代码块，前后不写任何文字。
2. 只用骨架里的字段名和枚举值，不增加字段。特别禁止：topics 里的 new；ideas 里摊平的 transfer_type / transfer_to / barrier；database 里写枚举以外的值。
3. 输出必须能被标准 YAML 解析器解析：包含冒号、#、引号、问号或以特殊符号开头的字符串，用双引号包起来，或者用多行块写法（review_cards 的 q: / a: 各占一行，不写进 {...} 花括号里）；布尔值只写 true / false，不写 yes / no。
4. 选填字段没有内容或不适用时，整个字段删除。不写 null、空字符串、"未报告""不适用"或任何占位符。

内容
5. 数字、accession、证据位置、repo 只能来自原文，不得推算或补全。
6. DOI：有 DOI 就写；arXiv 论文写 10.48550/arXiv.<编号>，不带版本号。
7. 自由文本用本次对话的主要语言；字段名、枚举值、topics.name、物种拉丁学名用英文；论文标题保持原文。
8. depth：本次做过完整的深度审稿（逐节审读、理清逻辑链，于是有 claims、issues、review_cards）写 deep，否则写 light。
9. claims 取自逻辑链的结论 D，一条写一个可以独立成立的命题。evidence_type 按证据的实际类型判断，不按作者的说法；每条都要有 evidence（原文位置）；带【逻辑跳跃】的设 logic_jump: true；原文证据与该命题相反时设 stance: contradicts，否则不写 stance。
10. user_insights 只收录我在本次对话中明确说出的判断、联想和计划，按原意转述；你的观点不得放进这里。我没有说过，就删除整段。
11. ideas：我提出的 origin 写 user，你提出的写 model；迁移信息只能写成嵌套的 transfer: {type, to, barrier}，to 填本文件里目标主题的 id。
12. datasets：
   - 有 accession：照原文写；database 填它所属的库（GEO、SRA、ENA、GSA、CNGB、ArrayExpress、Zenodo），不属于这些库的填 other。
   - 没有 accession：name 必填，database 填 other。
   - name 用原文或领域里通用的短名，不超过 5 个词（如 GKX、nuScenes、Tabula Sapiens）；没有通用名时，用"第一作者 年份 + 数据类型"（如 Lin 2026 duckweed snRNA-seq）。描述性内容写进 tissue / modality / scale，不写进 name。
   - 数据集名和托管平台名（Hugging Face、GitHub、Kaggle、项目官网）一律不写进 database。
   - public：全部可以公开下载写 true；部分公开或需要订阅写 false；原文没有说明写 unknown。
13. methods：name 只写文献里通用的短名（如 MFCF、SCENIC+），不超过 5 个词；方法做什么写进 io。extends 只填它在其基础上改进的方法，不包括只是调用的组件；被改进的方法也列在本文件 methods 里时，名称与它的 name 完全一致。
14. topics：如果对话或 Project 知识里有 rhizome-vocab.yaml，优先使用其中的规范名（candidate_topics 里合适的也可以沿用）；没有合适的才新建，用简短的英文名词短语，并在 aliases 里给出它的其他叫法（缩写的全称、中文名），这样会并入已有主题而不是另起一个。about 指论文本身研究的主题；applicable_to 指论文的资产可以用于、但论文本身并不研究的主题。
15. id：topics 用 t1、t2…，claims 用 c1…，datasets 用 d1…，methods 用 m1…，ideas 用 i1…，user_insights 用 u1…；user_insights.links_to、review_cards.about 和 transfer.to 只能引用这些 id，不写名称。
16. review_cards：3–5 张，题目必须能脱离原文作答，答案一到两句话。

自查（写入或输出前完成，不要输出自查过程；能运行代码时，先用 YAML 解析器解析一遍，再逐项检查）
17. 必填字段齐全；没有骨架之外的字段；枚举值合法；id 唯一；links_to、about 和 transfer.to 都指向本文件里存在的 id；每个 dataset 都有 accession 或 name，database 在枚举内；dataset 和 method 的 name 都是短名；没有占位符。
```

**词表同步**：词表每次变化后，Rhizome 导出一份 rhizome-vocab.yaml（主题规范名及其中英别名），放进 Claude 和 ChatGPT 的 Project 知识里。这样导出时就直接使用规范名，入库时大部分主题在别名精确匹配这一步就能命中，审核队列也随之变短。

格式漂移由三层机制兜住。第一，字段和枚举只在代码里的 Pydantic 模型维护一份，JSON Schema 由它生成（`rhz rxf schema`）；骨架和导出指令手工维护，CI 把骨架当作正例跑契约测试（骨架里的每个字段和枚举值都必须通过 schema），并检查 rxf-spec 与后端内置的副本一致。schema 必须接受骨架允许的所有字段和枚举值，并拒绝骨架之外的字段；骨架里的必填标注约束的是导出端，schema 可以更宽。第二，每份文档都带 `rxf_version`，入库时按对应版本的 schema 校验，并检查文件内 id 引用是否完整。第三，校验失败的文件会被移到 error/ 目录并附一份错误报告；把报告贴回聊天，让 AI 修正后重新导出即可。

## 处理流水线

每篇论文都从一份 RXF 导出文档开始，按三个档位处理：T0 自动完成，T1 是主路径，T2 可选。所有档位进入同一张图，节点上标注已完成的档位；升级只追加内容，不覆盖已有结果。

| 档位 | 输入 | 处理 | 成本 |
| --- | --- | --- | --- |
| T0 | DOI | 从 OpenAlex 拉取元数据、摘要、引用关系 | 免费 API |
| T1 | RXF 导出文档（轻量版或深度版） | 校验后直接解析为实体和边，不再调用 LLM 抽取 | 讨论时已付出 |
| T2 | 全文（开放获取，或你放进收件箱的 PDF） | 把 PDF 放进聊天，重新导出一份深度版 RXF，合并补充的资产 | 手动，可选 |

RXF 里的 user\_insights，也就是你在讨论中说出的判断和联想，会作为“用户提出”的 Idea 节点单独存放，权重高于模型抽取的内容。这是整个库里最难重新得到的部分。

**单篇摄入**

1. 捕获：把 RXF 文件（可附同名 PDF）放进收件箱目录，由监听进程自动处理。在 Claude Desktop 里也可以用 `rhz_ingest` 一步提交。
2. 校验：按 `rxf_version` 对应的 schema 校验。成功的文件移到 done/，失败的移到 error/ 并附错误报告。
3. 解析规范 ID：通过 OpenAlex 把 DOI 解析为 work ID，合并预印本和正式版。
4. T0：拉取元数据和引用关系。
5. T1：解析导出文档并写入 L1，记录 RXF 版本和审稿提示词版本。
6. 实体规范化：写入 L2（见“实体规范化”一章）。
7. 写边：写入 L3，然后应用 `human_decision`。
8. 向量化：以资产为单位，每个 Dataset、Method、Idea、Claim 各生成一个向量。
9. 召回：返回这篇论文与已有资产之间的关联（见“主动召回与内化”一章）。

**批任务（夜间运行）**：社区划分（Leiden）、主题聚合计数、综合候选的生成（见“主动召回与内化”一章）。

**重算**

- 触发条件：本地模型、映射规则或 schema 版本发生变化。
- 执行方式：用新的映射规则重新解析 L1 里已存的导出文档；L2 和 L3 从 L1 的当前行重建，`human_decision` 在最后应用。导出文档本身不会重新生成，因为当时的聊天无法重跑。

**回溯重标注（新建主题时）**

1. 为主题写定义：名称、一句话描述，以及 2–3 个正例和反例，然后生成向量。
2. 在全库资产向量上做近邻检索，取 top-k 作为候选（k 默认 500，可调）。
3. reranker 用主题定义给候选重新打分，低于阈值的直接丢弃。
4. 剩下的候选由 Qwen3.5-2B 以受约束输出判定为 about、applicable\_to 或无关。没有启用本地 LLM 时，候选进入审核队列，或者在 Claude Desktop 里通过 MCP 交给 Claude 批量判定。
5. 低置信度的判定进入审核队列。

单次成本的上界由 k 决定，与库的总规模无关。

## 实体规范化

规范化分两条路径处理：锚定类实体靠外部 ID 自动对齐，自由概念靠“召回 + 判定 + 阈值”处理。人工只处理中间置信度的那一段。

**锚定类实体（有 accession 的 Dataset、Work、Organism、Modality、有 repo 的 Method）**

- 抽取结果必须带上外部 ID。ID 先做格式校验（正则），再通过 API 确认它确实存在：GEO/SRA 用 NCBI E-utilities，Work 用 OpenAlex，repo 用 GitHub API。
- Dataset 的 accession 格式按 database 校验，对不上就报错：GEO 为 GSE/GSM/GDS/GPL，SRA 为 SRP/SRR/SRX/SRS/PRJNA 及 ENA、DDBJ 的镜像前缀，ENA 为 PRJEB/ERP/ERR/ERX/ERS/SAMEA，GSA 为 PRJCA/CRA/CRR/CRX/HRA/OMIX，CNGB 为 CNP/CNX/CNR/CNS/CNA，ArrayExpress 为 E-XXXX-<编号>，Zenodo 为 10.5281/zenodo.<编号> 或纯编号。
- 校验不通过的 ID 不入库，并标记该次抽取疑似幻觉。LLM 编造的 accession 是这一层最主要的风险。

**自由概念（Topic、Idea、Claim、没有 repo 的 Method、没有 accession 的 Dataset）**

1. 别名精确匹配：先做归一化（大小写、标点），再查中英文别名表。词表同步到聊天之后，大部分主题在这一步就能命中。
2. 向量召回：在同类实体中取 top-5 作为候选。
3. reranker 打分：bge-reranker-v2-m3 对“新概念—候选”逐对打分，得到是否为同一概念的置信度。
4. 按阈值处理：置信度 ≥ 0.9 自动合并；0.6–0.9 进入审核队列；< 0.6 新建实体。这两个阈值只是初始值，要在人工标注的样本上校准。
5. 上下位关系（更宽 / 更窄）：由 Qwen3.5-2B 判定后生成 `is_a` 边的候选，一律进入审核队列，不自动生效。

**论断用 NLI 判定**：mDeBERTa NLI 对“新论断—候选论断”双向打分。双向都是蕴含，视为同一命题，直接合并；只有单向蕴含，记为新论文支持已有论断；判为矛盾时，新论文对已有论断连一条 `contradicts` 边，并进入每周综合的争议清单。NLI 模型是在通用语料上训练的，科学论断属于领域迁移，所以矛盾判定一律先进审核队列，等它在你的标注集上达到门槛之后，才放开自动写入。

**防止主题爆炸**：LLM 提出的新主题先以“候选”状态存在。满足以下任一条件才转为正式主题：挂上至少 3 篇论文，或者经过你确认。

**合并和拆分都可以撤销**：所有操作都记录在 `human_decision` 里，别名历史完整保留。拆分就是把边重新分配到新节点上。

**审核队列界面**：批量处理，全键盘操作，每一项都显示双方的别名、定义和 3 条代表性连接。

## 检索与可视化

检索以资产为单位返回结果；可视化永远不把全量论文图发到前端，服务端每次最多返回约 300 个元素。

**检索**

- 混合检索：资产向量的近邻检索，加上关键词检索。bge-m3 同时提供稠密向量和稀疏向量，中英文混合的内容不需要额外分词。
- 筛选条件：实体类型、边类型、物种、模态、年份、档位，以及主题子树（用递归 CTE 展开 DAG）。
- 前 50 条结果用 bge-reranker-v2-m3 重排。每条结果都附带来源论文和原文证据位置。

**视图**

| 视图 | 内容 | 渲染方式 |
| --- | --- | --- |
| 主题全景 | 主题 DAG、每个主题按角色分类的资产计数、社区划分 | sigma.js（WebGL）；只画主题层，由服务端聚合 |
| 主题页 | 某个主题子树下的全部资产，按角色分栏（数据 / 方法 / 思路 / 论断 / 论文），有争议的论断置顶，可以按物种、模态、年份筛选 | 列表 + 局部小图 |
| 局部图 | 任一节点 1–2 跳以内的邻域，边按类型着色，可以分页展开 | Cytoscape.js |
| 论文卡片 | 三句话速览、资产清单、你的观点、逻辑链要点，以及导出文档原文 | 页面 |
| 资产卡片 | 数据集或方法的属性，所有关联论文按边类型分组 | 页面 |
| 审核队列 | 规范化和回溯标注的待审项 | 键盘操作界面 |

“点 GRN → 看算法 → 点论文 → 看关键贡献”这条交互路径，对应的是“主题页 → 方法栏 → 论文卡片”。原文本身不在主路径上，只在卡片里提供链接。

## 接口

所有能力都先实现成 REST API，MCP 和 CLI 是它上面的薄封装。鉴权方式是单用户 token。

**REST API**

| 端点 | 作用 |
| --- | --- |
| `POST /ingest` | 接收 RXF 文件（可附 PDF），校验后入库 |
| `GET /search` | 混合检索，支持上一章列出的所有筛选条件 |
| `GET /entity/{id}` | 获取论文卡片或资产卡片 |
| `GET /entity/{id}/neighbors` | 获取局部图，参数为跳数、边类型、分页 |
| `GET /topic/{id}/assets` | 获取主题子树下的资产，可按角色筛选 |
| `GET /topic-map` | 获取主题全景的聚合数据 |
| `POST /topic` | 新建主题，同时触发回溯重标注任务 |
| `GET /review`、`POST /decision` | 审核队列，以及写入人工决定 |
| `POST /recall` | 输入一段上下文，返回相关的已有资产 |
| `GET /jobs/{id}` | 查询异步任务的状态 |

**MCP（供 Claude Desktop 使用）**

- `rhz_ingest`：在 Claude Desktop 里讨论完一篇论文后，说一句“存入 Rhizome”，Claude 就会生成 RXF 并直接提交，省掉保存文件这一步。
- `rhz_recall`：讨论新的分析方案时，Claude 用当前上下文去查库，返回你读过但可能已经忘了的相关资产。
- `rhz_search`、`rhz_get`、`rhz_related`：主动查询。
- `rhz_digest`、`rhz_topic_changes`：每周综合的候选对，以及某个主题子树这个月新增的论文、资产和矛盾；文字说明由 Claude 据此写出。
- `rhz_queue`、`rhz_decide`：维护类工具。你说一句“处理这周的审核队列”，Claude 分页读出待审项并给出判断，你确认后写回库里。回溯标注、每周综合的说明这类需要推理的批量工作，都走这条路，用的是你的订阅额度。

本地部署时，只有 Claude Desktop 能连接本地 MCP。ChatGPT 和网页版 Claude 输出 RXF 后，保存进收件箱目录即可。

**CLI（本机使用，或在远程服务器上读只读快照）**

- `rhz search "..."`、`rhz get <id>`：在终端里直接查询。
- `rhz data <accession>`：返回数据集的下载方式，以及关联的论文。
- `rhz recall --from-file analysis.py`：读取脚本里的导入语句和注释，召回相关的方法和数据。

远程服务器（例如 Palmetto 这样的 HPC 集群）通常访问不到你的本机，所以远程端的 CLI 不调用 API，而是读一份只读快照。默认存储本身就是 SQLite，快照就是数据库文件的一致性副本，再加上向量索引。快照由本机用 `rhz sync <remote>` 推送，同步目标在设置里配置，可以配多个。远程端装了 embedding 模型时，查询向量在远程现场计算；没装时退化为关键词加筛选检索。对于要求双因素认证的服务器，支持手动触发同步，也支持复用一条已经认证过的 SSH 连接（ControlMaster）。

## 主动召回与内化

这一层由三个机制组成：召回负责“你忘了，但现在用得上”；综合负责“你没意识到的联系”；复习负责“真正记住”。三者共用同一个排序信号：相关度 × 遗忘度。遗忘度是指距离你上一次查看或复习这个资产过了多久。

**主动召回（四个场景）**

- 和 AI 讨论方案时：在 Claude Desktop 的 Project 指令里写明“讨论分析方案前先调用 `rhz_recall`”。
- 在 HPC 或远程服务器上写代码时：使用 `rhz recall --from-file`。
- 写论文、grant、review 时：把段落交给 `rhz_recall`，返回可以引用的证据和数据。
- 读新论文时：导出文档摄入完成后，返回结果里直接附上“和你读过的哪几篇相关、关联在哪个维度上”。摄入这个动作本身就会触发一次召回。

**定期综合（每周）**

1. 生成候选：找出向量相似度高，但图上 2 跳以内没有路径、并且分属不同社区的资产对。这正是“跨领域但你没有连起来”的那一类联系。新出现的 contradicts 边也会进入每周摘要，作为争议清单。
2. 说明不在本地生成：摘要里先把两张资产卡片并排展示；需要文字说明时，在 Claude Desktop 里通过 MCP 让 Claude 批量写，内容包括联系是什么、可能对哪个问题有用。
3. 结果汇总成每周摘要，你逐条标记有用或无用。标为有用的写成边或 Idea 节点，并记入 `human_decision`；你的标记会反过来调整候选阈值。
4. 对活跃主题，每月额外整理一份“这个月新增了什么、改变了什么”的摘要。新增和变化部分由软件统计，文字总结同样通过 MCP 交给 Claude。

**复习（FSRS 调度）**

- 复习卡片以资产为单位；你自己提出的 Idea 优先进入。
- 题目主要来自 RXF 里的 review\_cards；缺题的资产由本地小模型补题，或者用结构化字段套模板生成，例如“哪个方法用什么手段解决了某个问题”“某个 accession 是什么物种、什么模态”。
- 默认只有深度版导出文档里的资产进入复习，每天设上限，避免规模变大以后复习量失控。

## 技术栈

按桌面软件分发：Windows 原生运行，不依赖 Docker 或 WSL2，安装后就能在桌面窗口或浏览器里打开。默认存储改为 SQLite：只收录读过的论文时，它完全够用，而且单文件、零依赖，便于安装和备份。规模真的变大时可以切到 Postgres，两种后端共用同一套模型和迁移。整个系统不需要图数据库，也不需要 Redis。

| 层 | 选型 | 理由 |
| --- | --- | --- |
| 存储 | 默认 SQLite（FTS5 + sqlite-vec）；可选 PostgreSQL + pgvector | 两种后端共用 SQLAlchemy 模型和 Alembic 迁移；主题 DAG 两边都用递归 CTE |
| 原始文件 | 数据目录下的内容寻址存储 | 按 sha256 寻址，只追加 |
| 后端 | FastAPI + Pydantic | 用 Pydantic 定义 RXF 和抽取 schema，同时导出 JSON Schema |
| 任务队列 | 数据库内的任务表 + 后台 worker | 不额外引入 Redis；两种存储后端都适用 |
| LLM | 内核不调用任何 LLM API；需要推理的工作通过 RXF 和 MCP 交给聊天里的 Claude / ChatGPT | 没有 API key，也没有运行成本；API 模式作为可选插件保留，默认关闭 |
| Embedding | bge-m3（1024 维） | 中英混合；同时提供稠密和稀疏向量；本机 CPU 就能跑 |
| 本地判定模型 | bge-reranker-v2-m3（规范化、检索重排）；mDeBERTa-v3-base-xnli（论断关系） | 在 CPU 上用 PyTorch 运行（固定到某个模型版本，向量索引按版本命名）；比生成式 LLM 更小、更快、输出更稳定 |
| 本地小 LLM（可选） | Qwen3.5-2B，Q4\_K\_M 量化的 GGUF，约 1.3 GB | 用 llama.cpp 运行，开启 JSON schema 约束解码；只负责上下位判定、about / applicable\_to 分类和补题 |
| 图算法 | graspologic（Leiden 算法） | 社区划分，在夜间批任务里运行；MIT 许可。不用 igraph 和 leidenalg，因为它们是 GPL 许可 |
| 外部数据源 | OpenAlex、Unpaywall、Europe PMC、NCBI E-utilities、GitHub API | 元数据、引用、开放获取全文、accession 校验 |
| 前端 | React + sigma.js + Cytoscape.js + react-i18next | WebGL 全景、局部图、中英双语 |
| 桌面外壳 | Tauri + Python sidecar（PyInstaller 打包） | 生成 Windows NSIS 安装包 |
| MCP | Python MCP SDK | 对 REST API 的薄封装，以本地 stdio 方式接入 Claude Desktop |
| 复习调度 | FSRS（py-fsrs） |  |

另外还有三个组件：收件箱监听用 watchdog；远程快照就是 SQLite 数据库文件的一致性副本（使用 Postgres 后端时，导出为 SQLite）；备份是每天复制一次数据库文件，并把 L0 目录同步到云盘。只要 L0 还在，其余各层都可以重算。

如果以后需要复杂的多跳路径查询，可以把 Neo4j 作为只读的派生层加进来。因为 L3 本身是可重算的，加这一层不需要做数据迁移。

## 工程规范

Rhizome 按通用软件来开发：单用户，Windows 优先，macOS 和 Linux 其次。所有和个人环境有关的东西都放进设置，包括远程服务器、LLM 提供方、存储路径，一律不写进代码。

**国际化（中英双语）**

- 界面字符串全部走 i18n key：前端用 react-i18next；后端的错误信息、校验报告和 CLI 输出用 Babel（gettext）。zh-CN 和 en 各一套资源文件，CI 检查两套的 key 是否对齐，并禁止硬编码字符串。
- 默认语言跟随系统，可以在设置里切换，切换后立即生效。
- 日期和数字按语言格式化（Intl）。
- 界面语言和内容语言分开处理：论文内容和你的观点保持原文，不做翻译；主题名通过别名表的 lang 字段，优先显示当前界面语言的那个名称。
- 系统生成的文本（每周综合、复习题、校验报告）按当前界面语言生成。
- RXF 的字段名固定用英文，字段值可以是任何语言；导出指令提供中英两个版本。
- 中英文混排的字体回退和断行要单独测试。

**配置**

- 设置项包括：界面语言、数据目录、存储后端、本地模型开关、可选 API 插件及其 API key、embedding 模型、收件箱路径、远程同步目标。
- API key 存进系统的凭据管理器（Windows Credential Manager），不以明文落盘。
- 推理后端抽象为一个接口：本地小模型、通过 MCP 交给 Claude、可选的 API 插件，三者实现同一个接口，可以随时切换。默认只启用前两种。

**代码与测试**

- 单仓库，目录分为 backend、frontend、desktop、rxf-spec、docs。
- RXF 规范独立做版本管理，附带 JSON Schema 和示例文件，同时作为契约测试的输入。JSON Schema 由 Pydantic 模型生成；骨架和导出指令手工维护，CI 以骨架为正例做契约测试。
- 测试分三层：单元测试；RXF 契约测试；检索回归集（就是 M0 预注册的 benchmark 问题）。CI 用 GitHub Actions，在 Windows 和 Linux 上都要跑。
- 版本号遵循语义化版本；数据库变更一律走 Alembic 迁移，每次升级前自动备份。
- 日志写在本地文件里；提供一键导出诊断包，诊断包里不含论文内容。

**打包与分发**

- 后端用 PyInstaller 打成单个可执行文件，作为 Tauri 桌面外壳的 sidecar；Windows 安装包为 NSIS（exe）。
- 所有模型（bge-m3、reranker、NLI、Qwen3.5-2B）都不打进安装包，用到对应功能时才下载，每个都可以单独关闭，以控制安装包体积。
- 更新：后台每天查一次 GitHub Release，有新版时界面顶部提示并链接到安装包；安装前自动备份数据目录。

**开源准备（开发测试阶段闭源）**

- 开发和测试阶段使用私有仓库，正式发布时再公开。
- 许可证定为 Apache-2.0，所有依赖都必须和它兼容。仓库根目录放 LICENSE 和 NOTICE 文件，每个源文件加上 SPDX 头（SPDX-License-Identifier: Apache-2.0）。CI 加入依赖许可证检查（Python 用 pip-licenses，前端用 license-checker），一旦出现 GPL / AGPL 依赖，构建直接失败。
- 模型的许可证已经核实过，都是宽松许可：bge-m3（MIT）、bge-reranker-v2-m3（Apache-2.0）、mDeBERTa-v3-base-xnli（MIT）、Qwen3.5-2B（Apache-2.0）。模型在运行时下载，不随安装包分发。
- 仓库从第一天起不放任何个人数据：测试夹具只用合成数据或开放获取论文，你自己的 RXF 和数据库都放在仓库之外。pre-commit 加入密钥扫描（gitleaks），防止 API key 进入提交历史。
- 发布时，如果历史提交里有不适合公开的内容，就用压缩后的历史新建一个公开仓库，而不是把私有仓库直接转为公开。
- 闭源阶段如果要把测试版发给别人，安装包就附上最终要用的那份开源许可证，避免出现两套许可条款。

## 里程碑与验收门槛

&#91;embedded content: 里程碑 M0–M6 · 每步一个验收门槛\]

G2 是整个计划的证伪点：如果检索基线达不到 M0 预注册的阈值，就先回头改抽取 schema，不往上叠加图层和界面。各门槛的具体数值（命中率、重复节点率、本地模型准确率、采纳率）都在 M0 写定，之后不再修改。G3 用到的标注集（约 100 对实体、50 对论断）也在 M0 准备好。M1 完成后，库本身已经可以用 SQL 查询；M2 完成后，就能在远程服务器上用。

## 风险与对策

最大的风险不在技术上，而在两件事：概念碎片化，以及这个项目挤占科研时间。前者靠规范化机制解决，后者靠“每个里程碑都能独立使用”来兜底。

| 风险 | 后果 | 对策 |
| --- | --- | --- |
| 概念碎片化、重复节点 | 图断裂，检索漏召 | 别名表 + 词表同步 + reranker 判定；候选主题机制 |
| 项目挤占科研时间 | 半途而废 | 每个里程碑结束时都能独立使用，任何时候停下都不白做 |
| LLM 编造 accession 或数字 | 错误资产入库 | 用外部 API 校验 ID；审稿提示词和导出指令都禁止推算；证据位置为必填 |
| 本地模型准确率不足 | 错误合并、错误的矛盾边 | 启用前先在你的标注集上验证（G3）；矛盾边先进审核队列；每个模型都可以单独关闭 |
| 导出文档格式漂移 | 摄入失败或字段错位 | 每份文档带 `rxf_version`；schema 校验；骨架作为契约测试的正例；把错误报告贴回聊天修正 |
| 你的观点被模型混写 | user\_insights 失真 | 导出指令要求按原意转述并区分 origin；入库后可以在论文卡片里修改 |
| 主题爆炸 | 全景图不可读 | 主题从“候选”转“正式”需要满足门槛；每季度做一次合并审查 |
| 重算覆盖人工决定 | 人工审核成果丢失 | `human_decision` 在重算的最后一步应用 |
| 可视化变成毛线团 | 图失去作用 | 全景只画主题层；局部图设元素上限 |
| 早期 schema 频繁变动 | 历史数据前后不一致 | L1 记录 schema 版本；L2 和 L3 随时可以重建 |
| 许可证冲突 | 开源时被迫替换依赖或改许可证 | 开发初期就定许可证；CI 做依赖许可证检查 |
| 个人数据或密钥进入仓库历史 | 无法直接公开仓库 | 测试夹具只用合成数据；gitleaks 密钥扫描；必要时用压缩历史新建公开仓库 |
| 双语文案不同步 | 界面缺字或中英混杂 | CI 检查两套资源文件的 key 是否对齐；禁止硬编码字符串 |
| 安装包过大或依赖冲突 | 安装失败 | 不依赖 Docker；所有模型按需下载；CI 在干净的 Windows 环境上验证安装 |
| 维护工作积压 | 审核队列越积越长 | 需要推理的批量工作通过 MCP 一次处理一批；队列长度显示在首页 |
| 本地单机故障 | 原始材料丢失 | 每天备份数据库文件，L0 目录同步到云盘；L0 还在，其余都能重算 |
| 召回噪声 | 打扰工作，最终被弃用 | 设相关度阈值，每次最多返回 5 条；你的反馈用来调整阈值 |

## 决定记录

以下决定在 2026-10-03 至 10-05 确认，相应的改动已经同步到上面各章。目前没有待定问题。

| 问题 | 决定 | 对设计的影响 |
| --- | --- | --- |
| 是否批量导入未读文献 | 不导入，只收录读过并讨论过的 | 去掉批量导入里程碑；档位简化为 T0–T2 |
| 部署位置 | 先本地，Windows | Windows 原生运行；远程服务器读只读快照；MCP 只接 Claude Desktop |
| LLM 处理方式 | 主要在 Claude / ChatGPT 聊天中完成，导出规范化文档 | 新增 RXF 格式；摄入时基本不调用 API |
| 主题词表 | 从零开始，随新数据扩展 | 依靠“候选 → 正式”机制控制增长；词表同步进聊天的 Project |
| 是否加入 Claim | 加入 | 新增 Claim 实体和 supports / contradicts 边 |
| ChatGPT 低成本捕获 | 暂不考虑 | 两边统一用 RXF + 收件箱 |
| 开发定位 | 按通用软件开发 | 单用户桌面应用 + 安装包；个人环境全部放进设置；新增“工程规范”一章 |
| 界面语言 | 中英双语 | 全量 i18n；界面语言与内容语言分开处理 |
| Palmetto 双因素认证 | 不针对 Palmetto 定制 | 远程快照同步做成通用功能，支持手动同步和复用 SSH 连接 |
| 软件内是否调用 LLM | 内核不调用 LLM API | 本地运行 reranker、NLI 和可选的 Qwen3.5-2B；需要推理的工作走聊天和 MCP；API 插件可选，默认关闭 |
| 默认存储 | SQLite | Postgres 保留为可选后端；远程快照直接复用数据库文件 |
| 是否开源 | 开源；开发测试阶段闭源 | 私有仓库开发；依赖必须与开源许可证兼容，igraph / leidenalg 换成 graspologic；新增“开源准备”一节 |
| 开源许可证 | Apache-2.0 | CI 拒绝 GPL / AGPL 依赖；仓库带 LICENSE、NOTICE 和 SPDX 头 |
| RXF v1 字段补充（首次导出校验失败后） | 新增可选字段 `exported_at`、`language`，claims 与各类 assets 新增文件内 `id`；`topics.new` 不收；`ideas.transfer` 保持嵌套 | 仍为 `rxf_version: 1`（向后兼容）；`links_to` / `about` 只能引用文件内 id，校验检查引用完整性；导出指令新增三条 |
| RXF v1 第二次补充（两份导出分别因数据集缺少标识和 database 取值越界而校验失败，2026-10-04） | 数据集用 accession 或 name 标识；database 枚举定为 GEO / SRA / ENA / CNGB / ArrayExpress / Zenodo / other；骨架补齐代码已接受的字段（`paper.venue`、`datasets.name`、`datasets.role`、`methods.extends`）；`maintained`、`public` 用 true / false / unknown | 仍为 `rxf_version: 1`；导出指令重写为 17 条；没有 accession 的 Dataset 走自由概念规范化；骨架作为契约测试的正例 |
| RXF v1 第三次补充（对照代码核查文档，2026-10-05） | `public` 与 `maintained` 一样接受 unknown；database 枚举加入 GSA；骨架补上代码已有的 `claims.stance`（contradicts 边的来源）、`topics.aliases`、`instructions_version`；导出指令改为上面的 17 条，中英两版 | 仍为 `rxf_version: 1`；骨架进入 CI 契约测试；实现状态见 docs/implementation.zh.md |

需求和架构已经定稿；实现进度和与设计的偏差见 docs/implementation.zh.md。还没完成的 M0 事项：预注册 benchmark 问题和门槛，准备 G3 标注集。
