# AI Overhaul (Trimmed Core)

Lean core: a context-aware AI action button + standalone recommendations page. Task dashboard & legacy experimental recommender harness removed to simplify maintenance and reduce bundle size.

## ✅ Included Frontend Pieces

- `pageContext.ts`: Lightweight context detector (page, entityId, selection) exposed via `window.AIPageContext`.
- `AIButton.tsx`: Context-aware action launcher (actions list + execute + websocket task progress inference).
- `AIButtonIntegration.tsx`: Injects button into nav + settings tools link.
- `RecommendedScenes.tsx`: Independent recommendations page (backend ID fetch + GraphQL scene hydration + layout persistence).
- `AIOverhaul.css`: Styling for button, progress ring, recommendations grid.
- `build.js`: Deterministic single-file builds (IIFE wrapped) for each source.

## 🧹 Removed / Deferred

- Task dashboard & historical task view (will return after backend contracts stabilize).
- Legacy recommendation harness (`src/recommendations/*`).
- Experimental embedding components (`RecommendationPanel`, `Recommender*`).
- Test harnesses (`testreact.tsx`, simplified dashboard integrations).
- Complex result rendering (stays simple for now).

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

## 🎯 Recommendations Page

`RecommendedScenes.tsx` provides a focused, page-level experience:
* Calls backend stub `/api/v1/recommendations/scenes` for ordered scene ID lists (algorithm + min_score + limit).
* Falls back to deterministic mock set if backend unreachable (badge indicates fallback / ok state).
* Hydrates scene details via GraphQL one-by-one with adaptive schema pruning.
* Persists layout state (page size, zoom, page) via `localStorage` keys (`aiRec.*`) and URL params.

Future enhancements: overlay scores, server-provided reasons, batched detail fetch, filters, similarity seed injection.

Route: `/plugins/recommended-scenes` → `dist/RecommendedScenes.js`.

## �🔒 CSP & Network

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
  AIButtonIntegration.tsx
  RecommendedScenes.tsx
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