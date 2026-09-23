# Knowledge and RAG

## Goal
Give the planner grounded knowledge about the LU4 server and Lineage 2 mechanics.

## Initial sources
- https://lu4.org/
- https://mw2.wiki/lu4-gamma/main
- official patch notes / announcements

A Telegram news channel may be added later after the exact source is supplied.

## Ingestion
```text
source
  -> fetch
  -> clean
  -> normalized Markdown/text
  -> chunk
  -> metadata
  -> SQLite
  -> embeddings
  -> FAISS
```

## Metadata
Each chunk should retain:
- source_url
- source_type
- title
- retrieved_at
- published_at when available
- server/version scope when known
- tags/entities when known

## Retrieval
The agent should retrieve only when knowledge may materially affect a decision.

Examples:
- quest objective
- NPC location
- item requirements
- class mechanic
- patch-specific behavior

## Freshness
Server-specific information can change.
The system must preserve timestamps and avoid silently treating stale data as current.

## Storage
Start with SQLite.

Possible tables:
- sources
- documents
- chunks
- entities
- quests
- items
- locations
- ingestion_runs

FAISS is optional until semantic retrieval is required.

## Important
Do not turn retrieved text directly into an action.
Knowledge informs the planner; observed WorldState remains authoritative for what is currently on screen.
