/**
 * SceneFacesPanel — Injects a "Detected Faces" section into scene detail pages.
 *
 * Uses patch.after("ScenePage") to detect scene renders, then injects a face
 * card grid below the performer cards via DOM manipulation.
 *
 * Registers: window.SceneFacesPanel
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[SceneFacesPanel] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useCallback } = React;

  const THEME = {
    bg: "#1a1a1a",
    bgCard: "#222",
    border: "#333",
    text: "#eee",
    textMuted: "#888",
    accent: "#4caf50",
  };

  const LOG = "[SceneFacesPanel]";
  const PANEL_ID = "ai-scene-faces-panel";

  function getApiBase(): string {
    const fn = w.AIDefaultBackendBase;
    const base = fn ? fn() : "";
    return base ? `${base}/api/v1/plugins/skier_aitagging` : "";
  }

  // ---------- SceneFacesGrid Component ----------

  function SceneFacesGrid(props: { sceneId: number }) {
    const { sceneId } = props;
    const apiBase = getApiBase();
    const [faces, setFaces] = useState([] as any[]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null as string | null);

    const fetchFaces = useCallback(async () => {
      if (!apiBase || !sceneId) return;
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${apiBase}/faces/scenes/${sceneId}/faces`);
        if (res.ok) {
          const data = await res.json();
          setFaces(data.faces || []);
        } else {
          setError(`Failed to load faces (${res.status})`);
        }
      } catch (e) {
        setError("Could not connect to AI backend");
      }
      setLoading(false);
    }, [apiBase, sceneId]);

    useEffect(() => { fetchFaces(); }, [fetchFaces]);

    if (!apiBase) return null;
    if (loading) {
      return React.createElement("div", {
        style: { color: THEME.textMuted, fontSize: "13px", padding: "8px 0" },
      }, "Loading detected faces...");
    }
    if (error) {
      return React.createElement("div", {
        style: { color: "#c62828", fontSize: "12px", padding: "8px 0" },
      }, error);
    }
    if (faces.length === 0) return null; // No faces detected — hide section

    return React.createElement("div", { style: { marginTop: "16px" } },
      React.createElement("h3", {
        style: {
          fontSize: "14px", fontWeight: 600, color: THEME.text,
          marginBottom: "10px", textTransform: "uppercase", letterSpacing: "0.5px",
        },
      }, `Detected Faces (${faces.length})`),
      React.createElement("div", {
        style: {
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
          gap: "10px",
        },
      },
        ...faces.map((f: any) => {
          const totalAppearances = (f.scene_count || 0) + (f.image_count || 0);
          return React.createElement("a", {
            key: f.id,
            href: `/plugins/ai-faces?id=${f.id}`,
            style: {
              display: "flex", flexDirection: "column", textDecoration: "none",
              background: THEME.bgCard, borderRadius: "8px",
              border: `1px solid ${THEME.border}`, overflow: "hidden",
              transition: "border-color 0.15s",
            },
            onMouseEnter: (e: any) => { e.currentTarget.style.borderColor = THEME.accent; },
            onMouseLeave: (e: any) => { e.currentTarget.style.borderColor = THEME.border; },
          },
            React.createElement("img", {
              src: `${apiBase}/faces/clusters/${f.id}/thumbnail?size=160&pad=0.2`,
              alt: f.label || `Face #${f.id}`,
              style: { width: "100%", aspectRatio: "1", objectFit: "cover", display: "block" },
              loading: "lazy",
              onError: (e: any) => { e.currentTarget.style.display = "none"; },
            }),
            React.createElement("div", { style: { padding: "6px 8px" } },
              React.createElement("div", {
                style: {
                  color: THEME.text, fontWeight: 500, fontSize: "12px",
                  whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                },
              }, f.label || `Face #${f.id}`),
              React.createElement("div", {
                style: { color: THEME.textMuted, fontSize: "10px", marginTop: "2px" },
              },
                f.performer_id
                  ? `Performer #${f.performer_id}`
                  : f.status === "unidentified" ? "Unidentified" : f.status
              ),
              totalAppearances > 0
                ? React.createElement("div", {
                    style: { color: THEME.textMuted, fontSize: "10px" },
                  }, `${totalAppearances} appearance${totalAppearances !== 1 ? "s" : ""}`)
                : null,
              // Rating
              w.RatingWidgetWithAPI
                ? React.createElement("div", { style: { marginTop: "2px" } },
                    React.createElement(w.RatingWidgetWithAPI, {
                      entityType: "face_cluster",
                      entityId: f.id,
                      initialValue: null,
                      compact: true,
                    })
                  )
                : null
            )
          );
        })
      )
    );
  }

  // ---------- DOM Injection ----------

  function renderPanel(container: HTMLElement, sceneId: number) {
    const el = React.createElement(SceneFacesGrid, { sceneId });
    const ReactDOM = w.ReactDOM || PluginApi?.ReactDOM;
    if (!ReactDOM) {
      console.warn(LOG, "ReactDOM not available");
      return;
    }
    if (ReactDOM.createRoot) {
      ReactDOM.createRoot(container).render(el);
    } else if (ReactDOM.render) {
      ReactDOM.render(el, container);
    }
  }

  let _currentSceneId: number | null = null;

  function injectPanel(sceneId: number, attempt = 0) {
    if (_currentSceneId === sceneId && document.getElementById(PANEL_ID)) return;
    _currentSceneId = sceneId;

    // Clean up previous
    document.getElementById(PANEL_ID)?.remove();

    // Find the performer cards section on the scene detail page.
    // Stash renders "Performers" as a div within the scene detail.
    // We look for the detail tab content area.
    const detailTab: HTMLElement | null =
      (document.querySelector('.scene-tabs .tab-pane.active') as HTMLElement) ||
      (document.querySelector('.scene-details') as HTMLElement) ||
      null;

    // Try to find the performers section specifically
    let insertionPoint: HTMLElement | null = null;

    // Look for the "Performers" header or performer cards container
    const allHeaders = document.querySelectorAll('h6, .scene-details h6');
    for (const h of Array.from(allHeaders)) {
      if (h.textContent?.trim().toLowerCase() === 'performers') {
        // Find the card container that follows it
        let sibling = h.nextElementSibling as HTMLElement | null;
        while (sibling) {
          if (sibling.querySelector('.performer-card') || sibling.classList.contains('row')) {
            insertionPoint = sibling;
            break;
          }
          sibling = sibling.nextElementSibling as HTMLElement | null;
        }
        if (!insertionPoint) {
          // Insert after the header itself
          insertionPoint = h as HTMLElement;
        }
        break;
      }
    }

    // Fallback: look for performer cards directly
    if (!insertionPoint) {
      const perfCard = document.querySelector('.performer-card') as HTMLElement | null;
      if (perfCard) {
        // Walk up to find the row/container
        insertionPoint = perfCard.closest('.row') as HTMLElement ||
                         perfCard.parentElement as HTMLElement;
      }
    }

    // Fallback: just use the detail tab or scene detail area
    if (!insertionPoint) {
      insertionPoint = detailTab ||
        document.querySelector('.scene-details') as HTMLElement ||
        null;
    }

    if (!insertionPoint) {
      if (attempt < 10) {
        setTimeout(() => injectPanel(sceneId, attempt + 1), 200 + attempt * 150);
      }
      return;
    }

    const panel = document.createElement("div");
    panel.id = PANEL_ID;
    // Insert after the performer section
    if (insertionPoint.nextSibling) {
      insertionPoint.parentNode?.insertBefore(panel, insertionPoint.nextSibling);
    } else {
      insertionPoint.parentNode?.appendChild(panel);
    }

    renderPanel(panel, sceneId);
  }

  // ---------- Re-injection Observer ----------

  let _reinjectObserver: MutationObserver | null = null;

  function setupReinjectObserver() {
    if (_reinjectObserver) return; // already watching
    _reinjectObserver = new MutationObserver(() => {
      if (_currentSceneId && !document.getElementById(PANEL_ID)) {
        injectPanel(_currentSceneId);
      }
    });
    _reinjectObserver.observe(document.body, { childList: true, subtree: true });
  }

  // ---------- Integration ----------

  let integrated = false;

  if (PluginApi.patch?.after) {
    try {
      PluginApi.patch.after("ScenePage", function (...args: any[]) {
        const result = args[args.length - 1];
        const props = args[0];
        const sid = props?.scene?.id;
        if (sid) {
          const sceneId = parseInt(String(sid), 10);
          setTimeout(() => injectPanel(sceneId), 150);
        }
        return result;
      });
      integrated = true;
    } catch (e) {
      console.warn(LOG, "patch.after('ScenePage') failed:", e);
    }
  }

  // Fallback: URL detection
  if (!integrated) {
    function checkUrl() {
      const match = window.location.pathname.match(/\/scenes\/(\d+)/);
      if (match) {
        const sid = parseInt(match[1], 10);
        setTimeout(() => injectPanel(sid), 300);
      } else {
        _currentSceneId = null;
      }
    }
    const _origPush = history.pushState;
    history.pushState = function (...args: any[]) {
      _origPush.apply(this, args);
      setTimeout(checkUrl, 100);
    };
    window.addEventListener("popstate", () => setTimeout(checkUrl, 100));
    checkUrl();
  }

  setupReinjectObserver();

  w.SceneFacesPanel = SceneFacesGrid;
})();
