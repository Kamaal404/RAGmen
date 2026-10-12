# Installation

This guide gets RAGmen running on your machine from scratch.

---

## What you need

| Requirement | Why | Minimum version |
|---|---|---|
| Python | RAGmen is written in Python | 3.11 or newer |
| Git | To download the code | Any recent version |
| Docker Desktop | To run the Neo4j graph database | Any recent version |
| An OpenAI API key | To extract knowledge from your documents | — |

> **Note:** You can run RAGmen without an API key using `--dry-run` mode to inspect what it would extract, or using the built-in mock provider for testing. You only need an API key for real extraction.

---

## Step 1 — Install Python

Go to [python.org/downloads](https://python.org/downloads) and download Python 3.11 or newer.

During installation on Windows, check the box that says **"Add Python to PATH"**.

To verify it worked, open a terminal and run:

```
python --version
```

You should see something like `Python 3.11.9`.

---

## Step 2 — Install Docker Desktop

Go to [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop) and download Docker Desktop for your operating system. Install it and start it.

To verify it worked:

```
docker --version
```

You should see something like `Docker version 27.x.x`.

---

## Step 3 — Download RAGmen

Open a terminal, navigate to where you want to put the project, and run:

```
git clone https://github.com/Kamaal404/RAGmen.git
cd RAGmen
```

---

## Step 4 — Install the components

RAGmen has three components that each need to be installed:

```
cd forge && pip install -e ".[dev]" && cd ..
cd serve && pip install -e ".[dev]" && cd ..
cd agent && pip install -e ".[dev]" && cd ..
```

Or all at once using Make (on Mac/Linux):

```
make setup
```

To verify the forge component installed:

```
forge --help
```

You should see a list of commands.

---

## Step 5 — Start the database

RAGmen stores the knowledge graph in Neo4j. Start it with Docker:

```
docker compose up -d
```

The first time this runs it will download the Neo4j image (about 600 MB). After that it starts in a few seconds.

To check it is running, open [http://localhost:7474](http://localhost:7474) in your browser. You should see the Neo4j browser.

Default credentials: `neo4j` / `password` (you can change these in `docker-compose.yml` and a `.env` file).

---

## Step 6 — Set your API key

Create a file called `.env` in the RAGmen folder:

```
OPENAI_API_KEY=sk-...your key here...
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
```

RAGmen reads this file automatically before running.

---

## Verify everything works

Run the test suite — it works completely offline with no API key and no database:

```
make test
```

You should see all tests passing with no errors.

---

## Next step

[Your first graph](first-graph.md) — add a document and ask your first question.
