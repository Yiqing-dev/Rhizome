# Rhizome export instructions (RXF v1)

Put everything below into the Claude / ChatGPT Project instructions. At the end of each discussion,
just say "export RXF". Also add Rhizome's `rhizome-vocab.yaml` to the Project knowledge.

## Skeleton

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
  - {id: t1, name: GRN inference, relation: about, aliases: [gene regulatory network inference, 基因调控网络推断]}
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

## Rules

When exporting RXF:
- Output exactly one YAML code block and nothing outside it.
- Numbers, accessions and evidence locations must come from the paper; delete any field you do not have, never use placeholders.
- user_insights contains only judgements and associations I voiced in this conversation, paraphrased faithfully; never attribute your views to me.
- In ideas, mark yours origin: model and mine origin: user.
- claims come from the conclusions of the logic chain; set logic_jump: true for those marked as a logical leap. Every claim needs evidence (its location in the paper).
- For topics prefer canonical names from rhizome-vocab.yaml in the Project knowledge (candidate_topics may be reused when they fit); otherwise create a short English noun phrase and give its other names in aliases (the spelled-out form of an acronym, the Chinese name), so it merges with an existing topic instead of becoming a second one. relation is only about or applicable_to.
- review_cards: 3–5 questions covering the most memorable assets and claims, answerable without the paper.
- Set depth to the actual depth of this discussion.
- Give topics, claims, datasets, methods, ideas and user_insights short ids unique within the file (t1, c1, d1, m1, i1, u1). links_to and about may only contain those ids, never names; transfer is always a nested {type, to, barrier} object; topics have no `new` flag (Rhizome decides that).
- Field names are fixed English; do not add fields outside the skeleton. Values may be in any language.
- Write text containing question marks, colons or quotes (especially review_cards questions) in block style (q: / a: on their own lines), never inside {...} flow mappings.
