# Rhizome 导出指令（RXF v1）

把下面全部内容放进 Claude / ChatGPT 的 Project 指令。每次讨论结束说一句"导出 RXF"即可。
同时把 Rhizome 导出的 `rhizome-vocab.yaml` 放进 Project 知识。

## 骨架

```yaml
rxf_version: 1
id: rxf-2025-0001                     # optional id of this export
exported_at: 2025-06-01T10:30:00+08:00
language: zh-CN                       # main language of the text values
prompt_version: deep-review-v2        # version of the review prompt used in this chat
depth: deep                           # light | deep
paper:
  doi: 10.xxxx/xxxx
  title: ...
  year: 2025
  type: [research, tool]              # research | tool | resource | benchmark | review | protocol
  organisms: [Arabidopsis thaliana]
  modalities: [snRNA-seq, scATAC-seq]
tldr: [what was done, most important finding, biggest reservation]
topics:
  - {id: t1, name: GRN inference, relation: about}
  - {id: t2, name: spatial domain detection, relation: applicable_to}
claims:
  - id: c1
    text: ...
    evidence_type: causal             # causal | correlational | speculative
    evidence: Fig. 3B                 # required
    boundary: ...
    logic_jump: false
assets:
  datasets:
    - {id: d1, accession: GSE000000, database: GEO, organism: ..., tissue: ..., modality: ..., scale: ..., public: true, role: uses}   # role: uses | produces
  methods:
    - {id: m1, name: ..., repo: https://github.com/..., role: proposes, io: ..., maintained: unknown}   # role: proposes | uses | evaluates
  ideas:
    - id: i1
      text: ...
      transfer: {type: cross-species, to: t2, barrier: ...}   # cross-species | cross-modality | cross-problem
      origin: model                   # model | user
issues:
  - {severity: major, location: ..., text: ..., test: ...}     # minor | major | critical
user_insights:
  - id: u1
    text: ...
    links_to: [c1, m1]                # ids from this file
review_cards:
  - q: ...
    a: ...
    about: m1                         # an id from this file
```

## 规则

导出 RXF 时：
- 只输出一个 YAML 代码块，代码块外不写任何内容。
- 数字、accession、证据位置必须来自原文；没有的字段整个删除，不填占位符。
- user_insights 只收录我在本次对话中说出的判断和联想，按我的原意转述，不要把你的观点归到我名下。
- ideas 中由你提出的标 origin: model，由我提出的标 origin: user。
- claims 取自逻辑链的结论；带【逻辑跳跃】的设 logic_jump: true。每条 claim 必须有 evidence（原文位置）。
- topics 优先使用 Project 知识里 rhizome-vocab.yaml 的规范名；没有合适的再新建，用简短的英文名词短语；relation 只能是 about 或 applicable_to。
- review_cards 出 3–5 道题，覆盖最值得记住的资产和论断，题目要能脱离原文作答。
- depth 按本次讨论的实际深度填写。
- topics、claims、datasets、methods、ideas、user_insights 各条目给一个文件内唯一的短 id（t1、c1、d1、m1、i1、u1）。links_to 和 about 只能写这些 id，不写名称；transfer 必须是嵌套的 {type, to, barrier} 对象；topics 不标 new（新不新由 Rhizome 判断）。
- 字段名一律用英文，不要新增骨架以外的字段；字段值可以用任何语言。
- 含有问号、冒号或引号的文本（尤其是 review_cards 的题目）用多行块写法（q: / a: 各占一行），不要写进 {...} 花括号里。
