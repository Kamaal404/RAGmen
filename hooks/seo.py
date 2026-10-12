"""MkDocs hook — inject Open Graph, Twitter Card, JSON-LD, and canonical tags.

GEO (Generative Engine Optimization) strategy:
  - JSON-LD SoftwareApplication schema so LLMs can cite the tool accurately
  - Canonical URLs prevent duplicate-content dilution
  - Open Graph / Twitter Card for social previews
  - Per-page description from meta block or site default
  - BreadcrumbList JSON-LD for navigation context
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

SITE_URL = "https://kamaal404.github.io/RAGmen"
SITE_NAME = "RAGmen"
SITE_DESCRIPTION = (
    "Turn any document corpus into a schema-grounded knowledge graph "
    "and a read-only retrieval layer. Ask questions, get cited answers. "
    "Open source GraphRAG framework built on Neo4j."
)
OG_IMAGE = f"{SITE_URL}/assets/ragmen-logo.png"

# JSON-LD for the software itself — injected on every page so LLMs can find it
SOFTWARE_LD = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    "name": "RAGmen",
    "url": SITE_URL,
    "applicationCategory": "DeveloperApplication",
    "operatingSystem": "Windows, macOS, Linux",
    "description": SITE_DESCRIPTION,
    "softwareVersion": "0.1.0",
    "license": "https://opensource.org/licenses/MIT",
    "codeRepository": "https://github.com/Kamaal404/RAGmen",
    "author": {
        "@type": "Person",
        "name": "Kamaal404",
        "url": "https://github.com/Kamaal404",
    },
    "keywords": [
        "GraphRAG",
        "knowledge graph",
        "RAG",
        "Neo4j",
        "document retrieval",
        "knowledge extraction",
        "MCP",
        "Python",
        "NLP",
        "entity extraction",
        "citation",
        "open source",
    ],
    "featureList": [
        "Schema-grounded knowledge graph extraction",
        "Hybrid retrieval: vector + keyword + graph traversal",
        "Content-addressed, resumable extraction pipeline",
        "Multi-source conflict detection and surfacing",
        "MCP-compatible retrieval server",
        "Blueprint design system with human approval gate",
        "Enforced citations on every answer",
        "Neo4j backend with in-memory fallback",
    ],
}


def on_page_content(html: str, page, config, files) -> str:  # noqa: ANN001
    """Inject SEO meta tags and JSON-LD into every page."""

    page_url = urljoin(SITE_URL + "/", page.url or "")
    page_title = page.title or SITE_NAME
    page_desc = (
        page.meta.get("description")
        if page.meta
        else None
    ) or SITE_DESCRIPTION

    # ---- canonical ----
    canonical = f'<link rel="canonical" href="{page_url}"/>'

    # ---- Open Graph ----
    og = "\n".join([
        f'<meta property="og:type" content="website"/>',
        f'<meta property="og:site_name" content="{SITE_NAME}"/>',
        f'<meta property="og:title" content="{page_title} — {SITE_NAME}"/>',
        f'<meta property="og:description" content="{page_desc}"/>',
        f'<meta property="og:url" content="{page_url}"/>',
        f'<meta property="og:image" content="{OG_IMAGE}"/>',
        f'<meta property="og:image:width" content="512"/>',
        f'<meta property="og:image:height" content="512"/>',
        f'<meta property="og:image:alt" content="RAGmen — knowledge graph RAG framework"/>',
    ])

    # ---- Twitter Card ----
    twitter = "\n".join([
        f'<meta name="twitter:card" content="summary"/>',
        f'<meta name="twitter:title" content="{page_title} — {SITE_NAME}"/>',
        f'<meta name="twitter:description" content="{page_desc}"/>',
        f'<meta name="twitter:image" content="{OG_IMAGE}"/>',
    ])

    # ---- JSON-LD: SoftwareApplication (on every page) ----
    sw_ld = (
        '<script type="application/ld+json">\n'
        + json.dumps(SOFTWARE_LD, indent=2)
        + "\n</script>"
    )

    # ---- JSON-LD: BreadcrumbList ----
    parts = [p for p in page_url.replace(SITE_URL, "").split("/") if p]
    crumbs = [{"@type": "ListItem", "position": 1, "name": "Home", "item": SITE_URL}]
    for i, part in enumerate(parts, 2):
        crumb_url = SITE_URL + "/" + "/".join(parts[: i - 1]) + "/"
        crumbs.append({
            "@type": "ListItem",
            "position": i,
            "name": part.replace("-", " ").title(),
            "item": crumb_url,
        })
    breadcrumb_ld = (
        '<script type="application/ld+json">\n'
        + json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": crumbs}, indent=2)
        + "\n</script>"
    )

    # ---- inject before </head> ----
    injection = "\n".join([canonical, og, twitter, sw_ld, breadcrumb_ld])
    return html.replace("</head>", f"{injection}\n</head>", 1)
