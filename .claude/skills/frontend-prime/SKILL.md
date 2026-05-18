---
name: frontend-prime
description: Use when the user wants to prime context for frontend-only work in app/frontend (React + Vite + TypeScript + Bun + Tailwind). Triggers on phrases like "prime the frontend", "load frontend context", "I'm working on the UI", "/frontend-prime", or any request that scopes work to app/frontend/ without backend changes. Loads frontend conventions, the API/SSE client layer, hooks, components, and current state — and intentionally skips backend Python, Alembic, and ingest pipelines.
---

# Frontend Prime: Load `app/frontend` Context

## Objective

Build a working understanding of the React + Vite + TypeScript frontend in `app/frontend/` — its routing, API client, streaming hook, components, styling, and tooling — so that any subsequent change lands in the right place and matches existing conventions.

Scope is **strictly the frontend**. Do not read backend Python, Alembic migrations, or ingest pipelines unless the user explicitly asks.

## Process

### 1. Frontend conventions (the contract)

Read the Frontend block of the project guide and the Dos/Don'ts that apply to the UI:

- Read `CLAUDE.md` — focus on the **Tech Stack › Frontend**, **TypeScript (frontend)**, and **Don't** sections (no Redux/Zustand, all `fetch()` goes through `src/lib/api.ts`, SSE parsing only through the streaming hook, Tailwind utilities only).

### 2. Tooling + config

Read in parallel:

- `app/frontend/package.json` — deps, scripts (`dev`, `build`, `type-check`, `lint`, `test`)
- `app/frontend/vite.config.ts` — dev-server proxy for `/api/*` → backend
- `app/frontend/tsconfig.json` — strictness, path aliases
- `app/frontend/biome.json` — lint + format rules
- `app/frontend/tailwind.config.js` — design tokens / theme extensions
- `app/frontend/vitest.config.ts` — test environment (jsdom, setup files)
- `app/frontend/index.html` — root HTML / fonts / branding

### 3. Application shell

Read in parallel:

- `app/frontend/src/main.tsx` — React entry, providers
- `app/frontend/src/App.tsx` — `BrowserRouter` routes, auth guard, layout
- `app/frontend/src/__tests__/setup.ts` — global test setup

### 4. API + streaming layer (single-source-of-truth modules)

Read in full — these are referenced by every component:

- `app/frontend/src/lib/api.ts` — **all** typed `fetch()` wrappers and TS interfaces live here
- `app/frontend/src/lib/authApi.ts` — auth-specific calls
- `app/frontend/src/hooks/useStreamingResponse.ts` — **all** SSE parsing happens here (`data: <json>` tokens, `event: sources` frame, `[DONE]` terminator)

### 5. Hooks

List `app/frontend/src/hooks/` then read:

- `useAuth.tsx`, `useConversations.ts`, `useMessages.ts`, `useToast.ts`

### 6. Components + pages

List `app/frontend/src/components/` and `app/frontend/src/pages/`. Read at minimum:

- `components/ChatArea.tsx` — main chat surface, wires hooks + streaming
- `components/Sidebar.tsx` — conversation list
- `components/Message.tsx` + `components/MarkdownRenderer.tsx` — assistant rendering + citations
- `components/ChatInput.tsx` — user input
- `components/BrandingHeader.tsx` — header/branding
- `components/DocumentExplorer.tsx` + `components/DocumentPreviewModal.tsx` — admin docs UI
- `pages/Login.tsx`, `pages/Signup.tsx`, `pages/AdminDocuments.tsx`, `pages/NotFound.tsx`

### 7. Styling

- `app/frontend/src/styles/globals.css` (if present) — CSS variables consumed in Tailwind classes (e.g. `bg-[var(--bg)]`)

### 8. Current state

Run:

- `git status -- app/frontend` — uncommitted frontend changes
- `git log -10 --oneline -- app/frontend` — recent frontend activity

## Output report

Provide a concise summary, scannable with bullets + headers:

### Stack snapshot
- React / TS / Vite / Bun / Tailwind versions
- Test runner + lint/format tooling
- Notable deps (markdown rendering, syntax highlighting, router)

### Architecture
- Route map from `App.tsx`
- Provider tree from `main.tsx`
- How a user message flows: input → hook → `api.ts` → backend SSE → `useStreamingResponse` → `Message`
- Where citations get rendered (`[c:<chunk_id>]` marker handling, sources chip collapse)

### Conventions in force
- All `fetch()` calls funnel through `src/lib/api.ts`
- SSE parsing centralised in `useStreamingResponse`
- Tailwind utilities only; no CSS-in-JS; no component library
- Named exports, one component per file; filename matches component name
- React built-in state only — no Redux/Zustand/Jotai
- Custom hooks live in `src/hooks/`, prefixed `use`

### Test + tooling commands
- `bun install`
- `bun run dev` (Vite HMR on :5173, proxies `/api/*` to :8000)
- `bun run build`
- `bun run test`
- `bun x biome check src`
- `bun run tsc --noEmit`

### Current state
- Modified files under `app/frontend/`
- Last few commits touching the frontend
- Anything in progress worth flagging
