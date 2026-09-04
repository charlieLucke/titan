# SYSTEM_LINK — this repo is part of a system

> Dropped into each child repo as `docs/ai/SYSTEM_LINK.md` by `./workspace.sh new` and
> kept in sync by `./workspace.sh sync-shared`. It tells an agent working *inside* this
> repo that it belongs to a larger system, and where the system brain lives.

## This repo
- **Service name:** titan
- **Role:** RAG engine — ingest (Docling → Late Chunking → BGE-M3) + hybrid search over Qdrant
- **Consumes:** —
- **Exposes:** contracts/titan.openapi.yaml
- **Port (local):** 8765

## Where the system brain lives
The coordinating workspace repo holds the cross-repo picture:
- System map & dependency graph → workspace `docs/ai/SYSTEM.md`
- Which repo owns what → workspace `docs/ai/ROUTING.md`
- The contracts this repo must honor → workspace `docs/ai/CONTRACTS.md` + `contracts/`
- Cross-repo decisions → workspace `docs/ai/DECISIONS.md`

## Rules that override nothing, but add one thing
Follow this repo's own `CLAUDE.md` for all local work. The one addition from being part of
a system: **a change at this repo's boundary (its exposed contract) is a workspace-level
decision** — stop and surface it rather than changing the interface here.

<!-- SHARED-AGENT-RULES:START (synced from workspace shared/agent-rules.md — do not edit here) -->

# Shared Agent Rules

> Canonical convention fragment. The single-repo template's `CLAUDE.md` already carries the
> per-repo rules; this file is the **system-wide** delta that every child repo must also
> honor. `./workspace.sh sync-shared` appends/refreshes this block inside each child's
> `docs/ai/SYSTEM_LINK.md`. Edit it **here only** — never per repo.

## You are part of a larger system

This repo does not stand alone. It is one service in `stack-workspace`. Before changing
anything that another repo can observe:

- Check whether the change crosses a **contract**. If it does, stop — that is a workspace
  (Opus-level) decision, not a local one. Surface it.
- Your inputs and outputs at the boundary are defined in the workspace `contracts/`.
  Treat them as fixed unless a workspace plan says otherwise.
- Keep this repo **standalone-runnable**: don't import another service's code; talk to it
  only through its contract.

## What stays local vs. goes up

- A decision about *this* repo's internals → local `docs/ai/DECISIONS.md`.
- A decision affecting how this repo talks to others → workspace `docs/ai/DECISIONS.md`.
- An out-of-scope idea touching only this repo → local `IDEAS.md`; touching others →
  workspace `IDEAS.md`.

## Boundary discipline

- Don't widen this repo's public surface casually — every new endpoint/field is a contract.
- Don't read another service's database, files, or internals directly.
- When you change behavior at the boundary, the contract change lands *with* the code, and
  every consumer is updated in the same workspace feature.

<!-- SHARED-AGENT-RULES:END -->
