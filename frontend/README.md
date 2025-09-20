# AI Overhaul (Minimal Reset)

This branch / state is a stripped-down reset of the original AI Overhaul plugin. It intentionally removes nearly all prior frontend logic, services, handlers, WebSocket code, and action orchestration. The only remaining functionality is a small, context-aware UI button that detects what page of Stash the user is currently on and displays that context.

## ✅ What Remains

- `AIButton.tsx`: A minimal React component (no build-time props needed) that:
  - Detects page type (`scenes`, `images`, `galleries`, `performers`, `home`, etc.)
  - Determines whether the user is on a detail view (entity page) vs a list
  - Extracts an entity ID if present in the URL
  - Displays a compact button with contextual tooltip
  - Emits simple console logs for future debugging
- `AIButtonIntegration.tsx`: Injects the button into the Stash UI nav bar.
- Minimal CSS: Only the light styles needed for the button hover/active effect.
- Simple build process compiling just the two remaining TypeScript files.

## 🧹 What Was Removed

- All action handlers (single, batch, multi-select)
- Service discovery & health checks
- WebSocket manager & cancellation logic
- GraphQL utilities & mutations
- Settings page and persistence logic
- Results overlays & job suites
- SVG icon set (replaced with emoji for now)
- Extended CSP allowances (no outbound AI service calls in this state)

## 🛠 Build

Compile the minimal assets:

```
npm install
npm run build
```

Outputs to `dist/AIButton.js` and `dist/AIButtonIntegration.js` which are referenced in `AIOverhaul.yml`.

## 🔭 Next Steps (Backend-Centric Rewrite Roadmap)

These are suggested future directions now that the frontend is reduced:

1. Define backend APIs for: task creation, queue state, batch operations.
2. Reintroduce service discovery by fetching a descriptor from the backend instead of embedding logic.
3. Add a lightweight event or polling channel (later possibly WebSocket) only after backend contracts stabilize.
4. Introduce a typed DTO layer for results & tasks (fetched on-demand instead of pushed aggressively).
5. Progressive enhancement: button → dropdown → modal → overlay, driven entirely by backend responses.

## 🔒 CSP & Network

Current `AIOverhaul.yml` deliberately keeps things minimal—no broad `connect-src` entries. Re-add only what the new backend actually requires when features return.

## 🧪 Testing the Button

Navigate around Stash and observe:
- Tooltip updates when moving between list and detail pages
- Button label changes color (planned future enhancement) can be added if desired
- Console logs show detected context

### Multi-Select & Selection Context
When on list (non-detail) pages, selected entity IDs are captured heuristically. Hover the button to see a count; click to view a sample list in the alert.

Enable verbose logging:
```js
window.AIPageContextDebug = true;
```
Manually force a context recompute (rarely needed):
```js
window.AIPageContext.forceRefresh();
```

## 📁 Current Structure

```
src/
  AIButton.tsx
  AIButtonIntegration.tsx
  css/
    AIButton.css
build.js
AIOverhaul.yml
README.md
package.json
```

## 🧩 Design Principles for the Rewrite

- Move ALL actionable intelligence server-side.
- Keep the frontend a stateless renderer + minimal context detector.
- Treat every future interaction as an idempotent backend call returning declarative UI model.
- Avoid long-lived client state until strictly necessary.

## � Contribution Guidance (During Rewrite Phase)

Please DO NOT reintroduce large client modules yet. Instead, propose backend contracts first (e.g. `GET /ai/services`, `POST /ai/tasks`, etc.). Once accepted, the frontend can bind those responses to tiny, composable UI pieces.

---
This minimal state is the foundation for a cleaner, backend-driven architecture. Build back only what you truly need.