# SPDX-License-Identifier: Apache-2.0
"""Built-in NCBI Taxonomy IDs for common organisms (works offline). Others resolve online."""

from __future__ import annotations

from ..text import norm

_TABLE = {
    "9606": ["Homo sapiens", "human", "人", "人类"],
    "10090": ["Mus musculus", "mouse", "小鼠"],
    "10116": ["Rattus norvegicus", "rat", "大鼠"],
    "7955": ["Danio rerio", "zebrafish", "斑马鱼"],
    "7227": ["Drosophila melanogaster", "fruit fly", "果蝇"],
    "6239": ["Caenorhabditis elegans", "C. elegans", "线虫"],
    "4932": ["Saccharomyces cerevisiae", "budding yeast", "酿酒酵母"],
    "3702": ["Arabidopsis thaliana", "arabidopsis", "拟南芥"],
    "39947": ["Oryza sativa Japonica Group", "Oryza sativa japonica", "japonica rice"],
    "4530": ["Oryza sativa", "rice", "水稻"],
    "4577": ["Zea mays", "maize", "corn", "玉米"],
    "4565": ["Triticum aestivum", "wheat", "bread wheat", "小麦"],
    "4081": ["Solanum lycopersicum", "tomato", "番茄"],
    "3847": ["Glycine max", "soybean", "大豆"],
    "9544": ["Macaca mulatta", "rhesus macaque", "猕猴"],
    "8355": ["Xenopus laevis", "African clawed frog", "非洲爪蟾"],
    "9031": ["Gallus gallus", "chicken", "鸡"],
    "9913": ["Bos taurus", "cattle", "牛"],
    "9823": ["Sus scrofa", "pig", "猪"],
    "562": ["Escherichia coli", "E. coli", "大肠杆菌"],
}

_BY_NORM = {norm(n): tid for tid, names in _TABLE.items() for n in names}
SCIENTIFIC = {tid: names[0] for tid, names in _TABLE.items()}


def lookup_builtin(name: str) -> str | None:
    return _BY_NORM.get(norm(name))
