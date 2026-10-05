# Rhizome export instructions (RXF v1)

In the Claude / ChatGPT Project instructions put the rules below first, then the skeleton; pasted
together into a single conversation they work as well. Also add Rhizome's `rhizome-vocab.yaml` to
the Project knowledge. At the end of each discussion, just say "export RXF".

## Rules

[Rhizome export rules · RXF v1]
When I say "export RXF" (or "导出 RXF"), write up the paper of this discussion following these rules
and the skeleton after them. In the skeleton, the text after # is guidance: "required" means required
for both light and deep; "deep" means required for deep and optional for light; everything else is optional.

Output
1. When you can create files, write one .yaml file named {year}_{first author surname}_{first 3 title words}.yaml: all lower case, words joined by -, only a–z, 0–9, - and _ kept (e.g. 2026_wang_birds-eye-view-informed-reasoning.yaml). When you cannot, output exactly one yaml code block with no text before or after it.
2. Use only the field names and enum values of the skeleton; add no fields. Explicitly forbidden: new on topics; a flattened transfer_type / transfer_to / barrier on ideas; any database value outside the enum.
3. The output must parse with a standard YAML parser: wrap strings containing colons, #, quotes or question marks, or starting with a special character, in double quotes, or write them in block style (review_cards with q: / a: on their own lines, never inside {...} flow mappings); booleans are only true / false, never yes / no.
4. Delete an optional field entirely when it has no content or does not apply. Never write null, an empty string, "not reported", "n/a" or any other placeholder.

Content
5. Numbers, accessions, evidence locations and repos come from the paper only; never infer or complete them.
6. DOI: write it whenever the paper has one; for arXiv papers write 10.48550/arXiv.<id> without the version suffix.
7. Free text is in the main language of this conversation; field names, enum values, topics.name and Latin species names are English; the paper title stays as in the paper.
8. depth: deep when a full in-depth review was done in this conversation (section by section, logic chain worked through, hence claims, issues and review_cards); otherwise light.
9. claims come from the conclusions D of the logic chain, one independently valid proposition each. Judge evidence_type by the actual kind of evidence, not by the authors' wording; every claim has evidence (its location in the paper); set logic_jump: true for those marked as a logical leap; set stance: contradicts when the paper's evidence goes against the proposition, otherwise leave stance out.
10. user_insights holds only judgements, associations and plans I voiced explicitly in this conversation, paraphrased faithfully; your views never go here. If I said nothing of the kind, delete the whole section.
11. ideas: origin is user for mine and model for yours; transfer information is only ever the nested transfer: {type, to, barrier}, with to naming the id of the target topic in this file.
12. datasets:
   - With an accession: copy it from the paper; database is the repository it belongs to (GEO, SRA, ENA, GSA, CNGB, ArrayExpress, Zenodo), other for anything else.
   - Without an accession: name is required and database is other.
   - name is the short name used in the paper or the field, at most 5 words (e.g. GKX, nuScenes, Tabula Sapiens); without a common name use "first author year + data type" (e.g. Lin 2026 duckweed snRNA-seq). Descriptive detail goes into tissue / modality / scale, never into name.
   - Dataset names and hosting platforms (Hugging Face, GitHub, Kaggle, project websites) never go into database.
   - public: true when everything can be downloaded openly; false when only partly public or subscription-bound; unknown when the paper does not say.
13. methods: name is the short name used in the literature (e.g. MFCF, SCENIC+), at most 5 words; what the method does goes into io. extends lists only the methods it builds on and improves, not components it merely calls; when the improved method is also listed in this file, use exactly its name.
14. topics: when the conversation or the Project knowledge has rhizome-vocab.yaml, prefer its canonical names (candidate_topics may be reused when they fit); only otherwise create a new one as a short English noun phrase and give its other names in aliases (the spelled-out form of an acronym, the Chinese name), so it merges with an existing topic instead of becoming a second one. about is what the paper itself studies; applicable_to is a topic the paper's assets can serve although the paper does not study it.
15. id: topics t1, t2…, claims c1…, datasets d1…, methods m1…, ideas i1…, user_insights u1…; user_insights.links_to, review_cards.about and transfer.to may only refer to these ids, never to names.
16. review_cards: 3–5 cards; the question must be answerable without the paper, the answer is one or two sentences.

Self-check (before writing or outputting, without showing the check; when you can run code, parse the YAML first, then go through the items)
17. All required fields present; no fields outside the skeleton; enum values valid; ids unique; links_to, about and transfer.to all point at ids that exist in this file; every dataset has an accession or a name and database is in the enum; dataset and method names are short names; no placeholders.

## Skeleton

```yaml
rxf_version: 1                        # required
exported_at: 2026-10-04               # required; YYYY-MM-DD
prompt_version: deep-review-v2        # required; version of the review prompt used, unversioned when there is none
instructions_version: "2026.10"       # required; copy as is, never invent one
depth: deep                           # required: light | deep
language: en                          # required: main language of the free text, zh | en
paper:                                # required
  doi: "10.1101/xxxx"                 # required when the paper has one; arXiv: 10.48550/arXiv.<id> without version
  title: "..."                        # required, as in the paper
  year: 2025                          # required
  venue: "..."
  type: [research, tool]              # required, several allowed: research | tool | resource | benchmark | review | protocol
  organisms: [Arabidopsis thaliana]   # Latin names
  modalities: [snRNA-seq, scATAC-seq]
tldr: ["what was done", "most important finding", "biggest reservation"]   # required, exactly 3
topics:                               # required, at least 1; prefer canonical names from the vocabulary, no new flag
  - {id: t1, name: GRN inference, relation: about, aliases: [gene regulatory network inference, 基因调控网络推断]}   # relation: about | applicable_to
  - {id: t2, name: spatial domain detection, relation: applicable_to}
claims:                               # deep; the conclusions D of the logic chain
  - id: c1                            # file-local id: t / c / d / m / i / u + number
    text: "..."
    evidence_type: causal             # causal | correlational | speculative
    evidence: "Fig. 3B"               # location in the paper, required
    boundary: "..."                   # where the claim holds
    logic_jump: false
    stance: supports                  # supports | contradicts; contradicts when the paper's evidence goes against the proposition, else omit
assets:                               # required when the paper has any
  datasets:
    - id: d1
      name: "..."                     # common short name, at most 5 words; required without an accession
      accession: GSE000000            # only when the paper gives one
      database: GEO                   # required: GEO | SRA | ENA | GSA | CNGB | ArrayExpress | Zenodo | other
      role: produces                  # produces | uses
      organism: "..."                 # delete for non-biological data
      tissue: "..."
      modality: "..."
      scale: "..."                    # as stated in the paper
      public: true                    # true | false | unknown
  methods:
    - id: m1
      name: "SCENIC+"                 # common short name, at most 5 words
      role: proposes                  # proposes | uses | evaluates
      extends: ["SCENIC"]             # short names of the methods it improves on
      repo: "https://github.com/..."
      io: "input → output"
      maintained: unknown             # true | false | unknown
  ideas:
    - id: i1
      text: "..."                     # one sentence, detached from the original problem
      transfer: {type: cross-species, to: t2, barrier: "..."}   # nested; type: cross-species | cross-modality | cross-problem; to is a topic id; barrier optional
      origin: model                   # model | user
issues:                               # deep
  - {severity: major, location: "...", text: "...", test: "..."}   # severity: minor | major | critical
user_insights:                        # only judgements and associations I voiced in the discussion
  - {id: u1, text: "...", links_to: [c1, m1]}   # ids from this file only
review_cards:                         # deep, 3–5 cards
  - q: "..."                          # about: an id from this file
    a: "..."
    about: d1
```
