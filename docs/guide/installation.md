---
description: Install RAGmen on Windows, macOS, or Linux. Requires Python 3.11+, Docker Desktop, and an OpenAI API key. Step-by-step setup guide.
---

# Installation

Get RAGmen running from scratch in about 10 minutes.

---

## Requirements

| Requirement | Why you need it | Minimum version |
|---|---|---|
| **Python** | RAGmen is written in Python | 3.11+ |
| **Git** | To download the code | Any recent |
| **Docker Desktop** | Runs the Neo4j knowledge graph database | Any recent |
| **OpenAI API key** | Extracts knowledge from your documents | — |

!!! info "Can I test without an API key?"

    Yes. Use `--dry-run` to inspect what would be extracted, or `--provider mock` to run a full pipeline with fake extraction data. You only need a real API key when you want actual knowledge extracted.

---

## Step 1 — Install Python

Go to [python.org/downloads](https://www.python.org/downloads/) and download Python **3.11 or newer**.

=== "Windows"

    During installation, check **"Add Python to PATH"** — this is easy to miss and causes most installation problems.

    Verify it worked by opening **Command Prompt** or **PowerShell** and running:

    ```
    python --version
    ```

=== "macOS"

    Install via Homebrew (recommended):

    ```bash
    brew install python@3.11
    ```

    Or download the installer from python.org.

=== "Linux"

    ```bash
    sudo apt install python3.11 python3-pip   # Debian/Ubuntu
    sudo dnf install python3.11               # Fedora
    ```

You should see something like `Python 3.11.9`. If you see `3.9` or lower, the wrong version was installed — check again.

---

## Step 2 — Install Docker Desktop

Go to [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop) and download Docker Desktop. Install and start it.

Verify it worked:

```
docker --version
```

You should see `Docker version 27.x.x` or similar.

!!! warning "Docker must be running"

    Docker Desktop must be open and running whenever you use RAGmen. It does not need to be in the foreground, but it must be started.

---

## Step 3 — Download RAGmen

Open a terminal and run:

```bash
git clone https://github.com/Kamaal404/RAGmen.git
cd RAGmen
```

---

## Step 4 — Install the components

RAGmen has three independently installable components. Install all three:

```bash
cd forge && pip install -e ".[dev]" && cd ..
cd serve && pip install -e ".[dev]" && cd ..
cd agent && pip install -e ".[dev]" && cd ..
```

Verify the main component is installed:

```
forge --help
```

You should see a list of `forge` commands. If you see `command not found`, the installation did not complete — re-run the pip commands above.

---

## Step 5 — Start the database

RAGmen stores the knowledge graph in Neo4j. Start it with:

```bash
docker compose up -d
```

The first run downloads the Neo4j image (~600 MB). After that it starts in a few seconds.

Open [http://localhost:7474](http://localhost:7474) in your browser. You should see the Neo4j browser interface. This confirms the database is running.

Default login: `neo4j` / `password`

---

## Step 6 — Set your credentials

Create a file called `.env` in the RAGmen folder:

```bash title=".env"
OPENAI_API_KEY=sk-...your key here...
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
```

RAGmen reads this file automatically.

!!! tip "Where do I get an OpenAI API key?"

    Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys), sign in, and create a new key. Keep it secret — treat it like a password.

---

## Verify everything works

Run the full test suite — it works completely offline with no API key and no database:

```
make test
```

You should see all tests passing with no errors. If any test fails, check the error output and refer to [Troubleshooting](../troubleshooting.md).

---

## What got installed

```
RAGmen/
├── forge/     ← build-time tool (forge add, forge cook, ...)
├── serve/     ← MCP retrieval server (lattice-serve)
├── agent/     ← answer policy layer (agent ask)
├── spec/      ← blueprint schema and examples
├── blueprint/ ← design-time scripts
└── docs/      ← this documentation
```

---

[Your First Graph :octicons-arrow-right-24:](first-graph.md){ .md-button .md-button--primary }
