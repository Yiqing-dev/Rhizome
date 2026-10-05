# Rhizome 导出指令（RXF v1）

放进 Claude 和 ChatGPT 的 Project 指令时，先放下面这段规则，再放骨架；两段一起单独贴进对话也能用。
同时把 Rhizome 导出的 `rhizome-vocab.yaml` 放进 Project 知识。之后每次讨论结束，只需要说一句"导出 RXF"。

## 规则

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

## 骨架

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
