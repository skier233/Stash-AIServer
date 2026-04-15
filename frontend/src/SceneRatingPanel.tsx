/**
 * SceneRatingPanel — Multi-dimension rating panel for scenes.
 *
 * Fetches the available rating dimensions from the backend and renders
 * a RatingWidgetWithAPI for each one (Overall, Performers, Content, etc.).
 * Designed to be injected into scene detail pages via PluginApi patches.
 *
 * Registers: window.SceneRatingPanel
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[SceneRatingPanel] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useCallback, useRef } = React;

  const LOG = "[SceneRatingPanel]";
  const PANEL_ID = "ai-scene-rating-panel";

  // ---------- Helpers ----------

  function getApiBase(): string {
    const fn = w.AIDefaultBackendBase;
    const base = fn ? fn() : "";
    return base ? `${base}/api/v1` : "";
  }

  interface Dimension {
    key: string;
    label: string;
    description: string;
    icon: string;
  }

  interface RatingEntry {
    rating_key: string;
    value: number;
  }

  // Cache dimensions per entity type so we don't re-fetch every render
  const _dimCache: Record<string, Dimension[]> = {};

  async function fetchDimensions(entityType: string): Promise<Dimension[]> {
    if (_dimCache[entityType]) return _dimCache[entityType];
    const apiBase = getApiBase();
    if (!apiBase) return [];
    try {
      const res = await fetch(`${apiBase}/ratings/dimensions/${encodeURIComponent(entityType)}`);
      if (res.ok) {
        const data = await res.json();
        _dimCache[entityType] = data.dimensions || [];
        return _dimCache[entityType];
      }
    } catch (e) {
      console.warn(LOG, "Failed to fetch dimensions:", e);
    }
    return [];
  }

  async function fetchRatings(entityType: string, entityId: string): Promise<Record<string, number | null>> {
    const apiBase = getApiBase();
    if (!apiBase) return {};
    try {
      const res = await fetch(`${apiBase}/ratings/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}`);
      if (res.ok) {
        const data = await res.json();
        const map: Record<string, number | null> = {};
        for (const r of data.ratings || []) {
          map[r.rating_key] = r.value;
        }
        return map;
      }
    } catch (e) {
      console.warn(LOG, "Failed to fetch ratings:", e);
    }
    return {};
  }

  // ---------- Styles ----------

  // Compact inline row that sits right after .scene-toolbar (the existing
  // rating/ocount/organized bar) and before the tab nav.
  const STYLES = {
    container: {
      display: "flex",
      alignItems: "center",
      flexWrap: "wrap" as any,
      gap: "2px 10px",
      padding: "2px 0 4px 0",
    },
    dimRow: {
      display: "flex",
      alignItems: "center",
      gap: "4px",
    },
    label: {
      fontSize: "11px",
      color: "#999",
      whiteSpace: "nowrap" as any,
    },
    separator: {
      width: "1px",
      height: "12px",
      background: "#444",
      alignSelf: "center" as any,
    },
  };

  // ---------- Main Component ----------

  function SceneRatingPanel(props: { sceneId: number }) {
    const { sceneId } = props;
    const [dimensions, setDimensions] = useState([] as Dimension[]);
    const [ratings, setRatings] = useState({} as Record<string, number | null>);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
      let cancelled = false;
      setLoading(true);
      Promise.all([
        fetchDimensions("scene"),
        fetchRatings("scene", String(sceneId)),
      ]).then(([dims, rats]) => {
        if (cancelled) return;
        setDimensions(dims);
        setRatings(rats);
        setLoading(false);
      });
      return () => { cancelled = true; };
    }, [sceneId]);

    if (loading || dimensions.length === 0) return null;

    // Only show the extra (non-default) dimensions here — overall is native Stash
    const extraDims = dimensions.filter((d: Dimension) => d.key !== "default");
    if (extraDims.length === 0) return null;

    const RatingWidgetWithAPI = w.RatingWidgetWithAPI;
    if (!RatingWidgetWithAPI) return null;

    const children: any[] = [];
    extraDims.forEach((dim: Dimension, i: number) => {
      if (i > 0) {
        children.push(React.createElement("div", { key: `sep-${i}`, style: STYLES.separator }));
      }
      children.push(
        React.createElement(
          "div",
          { key: dim.key, style: STYLES.dimRow, title: dim.description },
          React.createElement("span", { style: STYLES.label }, dim.label),
          React.createElement(RatingWidgetWithAPI, {
            entityType: "scene",
            entityId: String(sceneId),
            ratingKey: dim.key,
            initialValue: ratings[dim.key] ?? null,
            compact: true,
          })
        )
      );
    });

    return React.createElement("div", { style: STYLES.container }, ...children);
  }

  // ---------- Injection via PluginApi patches ----------

  let _currentSceneId: number | null = null;

  function injectPanel(sceneId: number, attempt = 0) {
    if (!sceneId) return;
    if (_currentSceneId === sceneId && document.getElementById(PANEL_ID)) return;
    _currentSceneId = sceneId;

    document.getElementById(PANEL_ID)?.remove();

    // Insert right after .scene-toolbar (the bar with the overall rating,
    // view count, o-counter, organized button, and operations menu).
    // This places detailed ratings between the toolbar and the tab nav,
    // matching Stash's native layout without pushing the video player down.
    const toolbar = document.querySelector(".scene-toolbar") as HTMLElement | null;

    if (!toolbar || !toolbar.parentNode) {
      if (attempt < 10) {
        setTimeout(() => injectPanel(sceneId, attempt + 1), 200 + attempt * 150);
      } else {
        console.warn(LOG, "Could not find .scene-toolbar after retries");
      }
      return;
    }

    const container = document.createElement("div");
    container.id = PANEL_ID;
    // insertAfter: place right after the toolbar div
    toolbar.parentNode.insertBefore(container, toolbar.nextSibling);

    const ReactDOM = PluginApi.ReactDOM || (w as any).ReactDOM;
    if (ReactDOM?.createRoot) {
      ReactDOM.createRoot(container).render(React.createElement(SceneRatingPanel, { sceneId }));
    } else if (ReactDOM?.render) {
      ReactDOM.render(React.createElement(SceneRatingPanel, { sceneId }), container);
    }
  }

  // Re-inject when DOM changes (e.g. SPA navigation)
  let _observer: MutationObserver | null = null;
  function setupObserver() {
    if (_observer) return;
    _observer = new MutationObserver(() => {
      if (_currentSceneId && !document.getElementById(PANEL_ID)) {
        injectPanel(_currentSceneId);
      }
    });
    _observer.observe(document.body, { childList: true, subtree: true });
  }

  function setupPatch() {
    if (!PluginApi.patch?.after) {
      console.warn(LOG, "PluginApi.patch.after not available, skipping auto-injection");
      return;
    }
    try {
      PluginApi.patch.after("ScenePage", function (...args: any[]) {
        const result = args[args.length - 1];
        const props = args[0];
        const sid = props?.scene?.id;
        if (sid) {
          const sceneId = parseInt(String(sid), 10);
          setTimeout(() => injectPanel(sceneId), 200);
        }
        return result;
      });
      setupObserver();
      console.log(LOG, "ScenePage patch registered");
    } catch (e) {
      console.warn(LOG, "patch.after failed:", e);
    }
  }

  setupPatch();

  // ---------- Exports ----------

  w.SceneRatingPanel = SceneRatingPanel;
  console.log(LOG, "Registered window.SceneRatingPanel");
})();
