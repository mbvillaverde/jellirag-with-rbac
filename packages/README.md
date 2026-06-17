# packages

Workspace for code shared across `apps/*`. Intentionally minimal for MVP — the
broker (TypeScript) and backend (Python) do not share a typed contract module
because they live in different language ecosystems. Shared TypeScript types
(frontend <-> broker) can live here if a need arises.
