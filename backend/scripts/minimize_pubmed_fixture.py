"""Turn local PubMed recordings into fixtures that are safe to commit.

    uv run python -m scripts.minimize_pubmed_fixture

Reads tests/fixtures/pubmed/recorded/efetch_*.xml (kept out of git) and writes
tests/fixtures/pubmed/minimized/. Structure, identifiers, dates, MeSH terms, publication types,
titles and abstract section labels are kept; abstract prose, author lists, affiliations,
references and grants are replaced or removed.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "pubmed"
SOURCE = FIXTURES / "recorded"
TARGET = FIXTURES / "minimized"
REMOVED = (
    "AuthorList",
    "InvestigatorList",
    "GrantList",
    "ReferenceList",
    "OtherAbstract",
    "CopyrightInformation",
    "KeywordList",
)
PARSER = etree.XMLParser(resolve_entities=False, no_network=True, remove_blank_text=True)


def minimize(path: Path) -> bytes:
    tree = etree.parse(str(path), PARSER)
    for name in REMOVED:
        for node in list(tree.iter(name)):
            node.getparent().remove(node)
    for article in tree.iter("PubmedArticle"):
        pmid = article.findtext("MedlineCitation/PMID", default="?")
        for index, node in enumerate(article.iterfind(".//Abstract/AbstractText"), start=1):
            label = node.get("Label") or "abstract"
            for child in list(node):
                node.remove(child)
            node.text = f"Placeholder {label.lower()} text {index} for PMID {pmid}."
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def main() -> None:
    sources = sorted(SOURCE.glob("efetch_*.xml"))
    if not sources:
        raise SystemExit(f"no recordings in {SOURCE}; run make pubmed-record first")
    TARGET.mkdir(parents=True, exist_ok=True)
    for path in sources:
        (TARGET / path.name).write_bytes(minimize(path))
        print(f"wrote {TARGET / path.name}")


if __name__ == "__main__":
    main()
