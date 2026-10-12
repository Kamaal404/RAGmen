# What is RAGmen?

<div align="center">
<img src="../assets/originals/ragmen-logo.png" width="140"/>

**Noodles are nodes. Instant GraphRAG without the long cooking.**
</div>

---

## The problem RAGmen solves

You have documents — a legal manual, a technical handbook, a recipe collection, a policy archive. You want to be able to ask questions about them and get accurate, cited answers.

The simplest approach is to dump all the text into an AI and ask. That works for simple questions, but it breaks down when:

- The answer requires connecting information from two different sections
- You need to know *which document* a fact came from
- Two documents contradict each other and you need to know both sides
- You want the AI to stay strictly within your documents and not invent answers

RAGmen solves all of these by building a **knowledge graph** from your documents — a structured map of facts and relationships — and then answering questions by navigating that map rather than guessing from raw text.

---

## The cooking metaphor

RAGmen uses a cooking metaphor because building a knowledge graph is a lot like cooking:

| Step | Kitchen equivalent | RAGmen equivalent |
|---|---|---|
| Plan the dish | Write a recipe | Design a **blueprint** |
| Prep the ingredients | Chop and measure | **Forge**: chunk and extract |
| Cook and serve | Plate the food | **Serve**: answer questions |

### Blueprint — the recipe

Before you touch your documents, you design a blueprint. A blueprint tells RAGmen:
- What *kinds of things* live in your documents (e.g. Procedures, Materials, Hazards)
- What *relationships* exist between them (e.g. a Procedure REQUIRES a Material)
- What questions you want the system to answer

You approve the blueprint before any processing begins. This is the design step — it is the most important decision you make, and it happens before any AI cost is spent.

### Forge — the kitchen

Forge is the build step. It reads your documents, breaks them into pieces (chunks), sends those pieces to an AI to extract entities and relationships, and writes everything into a knowledge graph.

Forge runs once per document (or whenever a document changes). It is unattended — you start it and it runs to completion. If it gets interrupted, it resumes where it left off.

### Serve — the dining room

Serve is what runs when users ask questions. It is a read-only window into the graph — it never changes anything. It uses a combination of keyword search, vector similarity, and graph traversal to find the most relevant information, then returns cited answers.

---

## What makes it different from plain AI chat

| Feature | Plain AI chat | RAGmen |
|---|---|---|
| Answers from your documents only | No — mixes in general knowledge | Yes — refuses to answer outside the corpus |
| Citations | Sometimes, often hallucinated | Always — every claim has a source + page |
| Multi-hop reasoning | Weak | Strong — follows graph relationships |
| Conflicting sources | Picks one and sounds confident | Surfaces both sides with attribution |
| Reprocessing when a document changes | Full re-run | Only reprocesses changed sections |

---

## Who this is for

RAGmen is designed for people who have a body of documents they need to query reliably. Examples:

- A law firm querying a client's contract archive
- A fire safety instructor querying a survival manual
- A policy team querying government regulations
- A technical writer querying a product manual collection

You do not need to write code to use RAGmen. You need to be comfortable running commands in a terminal.

---

## Next step

[Installation](installation.md) — get RAGmen set up on your machine.
