# Rhizome export instructions (RXF v1)

Put everything below into the Claude / ChatGPT Project instructions. At the end of each discussion,
just say "export RXF". Also add Rhizome's `rhizome-vocab.yaml` to the Project knowledge.

## Skeleton

```yaml
rxf_version: 1
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
  - {name: GRN inference, relation: about}
  - {name: spatial domain detection, relation: applicable_to}
claims:
  - text: ...
    evidence_type: causal             # causal | correlational | speculative
    evidence: Fig. 3B                 # required
    boundary: ...
    logic_jump: false
assets:
  datasets:
    - {accession: GSE000000, database: GEO, organism: ..., tissue: ..., modality: ..., scale: ..., public: true, role: uses}   # role: uses | produces
  methods:
    - {name: ..., repo: https://github.com/..., role: proposes, io: ..., maintained: unknown}   # role: proposes | uses | evaluates
  ideas:
    - text: ...
      transfer: {type: cross-species, to: ..., barrier: ...}   # cross-species | cross-modality | cross-problem
      origin: model                   # model | user
issues:
  - {severity: major, location: ..., text: ..., test: ...}     # minor | major | critical
user_insights:
  - text: ...
    links_to: [...]
review_cards:
  - q: ...
    a: ...
    about: ...
```

## Rules

When exporting RXF:
- Output exactly one YAML code block and nothing outside it.
- Numbers, accessions and evidence locations must come from the paper; delete any field you do not have, never use placeholders.
- user_insights contains only judgements and associations I voiced in this conversation, paraphrased faithfully; never attribute your views to me.
- In ideas, mark yours origin: model and mine origin: user.
- claims come from the conclusions of the logic chain; set logic_jump: true for those marked as a logical leap. Every claim needs evidence (its location in the paper).
- For topics prefer canonical names from rhizome-vocab.yaml in the Project knowledge; otherwise create a short English noun phrase. relation is only about or applicable_to.
- review_cards: 3–5 questions covering the most memorable assets and claims, answerable without the paper.
- Set depth to the actual depth of this discussion.
- Field names are fixed English; do not add fields outside the skeleton. Values may be in any language.
- Write text containing question marks, colons or quotes (especially review_cards questions) in block style (q: / a: on their own lines), never inside {...} flow mappings.
