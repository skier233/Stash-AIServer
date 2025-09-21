# AI Overhaul (Minimal Async Core)

This state is a lean, production-oriented core: a context-aware AI action button + real-time task dashboard powered exclusively by a websocket + concise REST endpoints. All heavy orchestration (queues, priority, concurrency, batching, cancellation, parent/child progress) is backend-driven. The frontend is intentionally thin, stateless, and reactive.

## ✅ Included Frontend Pieces

- `pageContext.ts`: Lightweight context detector (page, entityId, selection) exposed via `window.AIPageContext`.
- `AIButton.tsx`: Context-aware action launcher:
  - Fetches available actions lazily on open / context change.
  - Submits actions via `/api/v1/actions/submit`.
  - Tracks multiple concurrent parent tasks; infers progress ring for a single active controller via child task events.
  - No polling. All progress & completion via shared websocket `/api/v1/ws/tasks` (fallback `/ws/tasks`).
- `TaskDashboard.tsx`: Real-time + historical view:
  - Active parent/controller tasks live from global websocket cache.
  - Progress derived from weighted child states (completed|failed=1, running=0.5, queued=0, cancelled excluded).
  - History fetched manually (user refresh or mount) from canonical endpoint: `/api/v1/tasks/history`.
  - Service filter dropdown.
- `AIButtonIntegration.tsx`: Unified integration script:
  - Injects AI button into `MainNavBar.UtilityItems`.
  - Registers dashboard route `/plugins/ai-tasks`.
  - Adds settings tools entry + nav utility link fallback.
- `AIOverhaul.css`: Styling for button, progress ring, dashboard rows.
- Deterministic build `build.js`: Compiles & IIFE-wraps each file to `dist/`.

## 🧹 Intentionally Omitted (Handled Backend-Side Now or Deferred)

- Client-side queue / concurrency accounting (server authoritative).
- Manual polling loops (websocket only for live state; explicit REST for history snapshot).
- Persisting or displaying child tasks (UI focuses on parent/controller clarity).
- Complex modal/result rendering (results surfaced via simple dialogs/notifications for now).
- Legacy heuristic integrations & deprecated code paths.

## 🛠 Build

Compile the minimal assets:

```
npm install
npm run build
```

Outputs to `dist/*.js` (button, dashboard, integration, context). All listed in `AIOverhaul.yml`.

## 🔭 Potential Enhancements

1. Rich result rendering (markdown/modals) based on `result_kind` contract.
2. Inline cancellation buttons for active parent tasks.
3. Service-driven dynamic parameter forms (fetched schema, render ephemeral inputs before submit).
4. Export / download task summaries.
5. Multi-select batch previews before execution.

## 🔒 CSP & Network

Current `AIOverhaul.yml` deliberately keeps things minimal—no broad `connect-src` entries. Re-add only what the new backend actually requires when features return.

## 🧪 Quick Manual Test Flow

1. Open a list page (e.g. scenes) – button tooltip should show page + selection count (if any).
2. Open a detail page – icon style adjusts; entityId appears in tooltip.
3. Open button menu – actions load (network POST /api/v1/actions/available).
4. Execute a batch action – button shows progress ring (% inferred from child updates) or count badge for >1.
5. Open Settings → Tools → AI Tasks – dashboard shows active parent; progress % updates without refresh.
6. Click Refresh – recent history populates from `/api/v1/tasks/history`.

### Multi-Select & Selection Context
When on list (non-detail) pages, selected entity IDs are captured heuristically. Hover the button to see a count; click to view a sample list in the alert.

Enable verbose logging (gates console output across components/integration):
```js
window.AIPageContextDebug = true;
window.AIDebug = true;
```
Manually force a context recompute (rarely needed):
```js
window.AIPageContext.forceRefresh();
```
Toggle debug off:
```js
delete window.AIDebug;
```

## 📁 Structure

```
src/
  pageContext.ts
  AIButton.tsx
  TaskDashboard.tsx
  AIButtonIntegration.tsx
  css/
    AIButton.css
build.js
AIOverhaul.yml
README.md
package.json
```

## 🧩 Design Principles

- Backend-first: queues, prioritization, orchestration live server-side.
- Minimal surface: frontend reacts to declarative task/action contracts.
- Event-driven: no timer loops; websocket + explicit user-triggered fetches only.
- Clear separation: parent tasks represent user intent; children stay implicit.
- Deterministic builds: each TS file → one isolated IIFE output.

## 🤝 Contribution Guidance

Before adding UI complexity, extend backend contracts (actions metadata, parameter schemas, result descriptors). Keep client additions composable and stateless where possible.

---
This focused core is the foundation; extend only as backend capabilities mature.