/**
 * ImageFacesPanel — Injects a "Detected Faces" section into image detail pages.
 *
 * Uses patch.after("ImagePage") to detect image renders, then injects a face
 * card grid below the performer cards via DOM manipulation.
 *
 * Registers: window.ImageFacesPanel
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[ImageFacesPanel] PluginApi or React not available");
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

  const LOG = "[ImageFacesPanel]";
  const PANEL_ID = "ai-image-faces-panel";

  function getApiBase(): string {
    const fn = w.AIDefaultBackendBase;
    const base = fn ? fn() : "";
    return base ? `${base}/api/v1/plugins/skier_aitagging` : "";
  }

  // ---------- ImageFacesGrid Component ----------

  function ImageFacesGrid(props: { imageId: number }) {
    const { imageId } = props;
    const apiBase = getApiBase();
    const [faces, setFaces] = useState([] as any[]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null as string | null);
    const [busyId, setBusyId] = useState(null as number | null);
    const [menuOpenId, setMenuOpenId] = useState(null as number | null);
    const [menuPos, setMenuPos] = useState({ top: 0, left: 0 });

    const fetchFaces = useCallback(async () => {
      if (!apiBase || !imageId) return;
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${apiBase}/faces/images/${imageId}/faces`);
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
    }, [apiBase, imageId]);

    useEffect(() => { fetchFaces(); }, [fetchFaces]);

    // Close menu on outside click
    useEffect(() => {
      if (!menuOpenId) return;
      const close = () => setMenuOpenId(null);
      document.addEventListener("click", close);
      return () => document.removeEventListener("click", close);
    }, [menuOpenId]);

    const handleDelete = useCallback(async (clusterId: number) => {
      if (!confirm("Permanently delete this face cluster and all its data?")) return;
      setBusyId(clusterId);
      try {
        await fetch(`${apiBase}/faces/clusters/${clusterId}`, { method: "DELETE" });
        await fetchFaces();
      } catch (e) { console.error(LOG, "Delete failed:", e); }
      setBusyId(null);
      setMenuOpenId(null);
    }, [apiBase, fetchFaces]);

    const handleDetach = useCallback(async (clusterId: number) => {
      if (!confirm("Detach this face from its cluster for this image? The cluster will be recalculated.")) return;
      setBusyId(clusterId);
      try {
        await fetch(`${apiBase}/faces/images/${imageId}/faces/${clusterId}/detach`, { method: "POST" });
        await fetchFaces();
      } catch (e) { console.error(LOG, "Detach failed:", e); }
      setBusyId(null);
      setMenuOpenId(null);
    }, [apiBase, imageId, fetchFaces]);

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
          const isBusy = busyId === f.id;
          const isMenuOpen = menuOpenId === f.id;
          return React.createElement("div", {
            key: f.id,
            style: {
              display: "flex", flexDirection: "column",
              background: THEME.bgCard, borderRadius: "8px",
              border: `1px solid ${THEME.border}`,
              transition: "border-color 0.15s", position: "relative",
              opacity: isBusy ? 0.5 : 1,
            },
          },
            React.createElement("a", {
              href: `/plugins/ai-faces?id=${f.id}`,
              style: { display: "block", textDecoration: "none", overflow: "hidden", borderRadius: "8px 8px 0 0" },
            },
              React.createElement("img", {
                src: `${apiBase}/faces/clusters/${f.id}/thumbnail?size=160&pad=0.2`,
                alt: f.label || `Face #${f.id}`,
                style: { width: "100%", aspectRatio: "1", objectFit: "cover", display: "block" },
                loading: "lazy",
                onError: (e: any) => { e.currentTarget.style.display = "none"; },
              })
            ),
            React.createElement("div", { style: { padding: "6px 8px" } },
              React.createElement("div", {
                style: {
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                },
              },
                React.createElement("div", {
                  style: {
                    color: THEME.text, fontWeight: 500, fontSize: "12px",
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flex: 1,
                  },
                }, f.label || `Face #${f.id}`),
                // Action menu button
                React.createElement("div", { style: { position: "relative" } },
                  React.createElement("button", {
                    style: {
                      padding: "1px 5px", background: "transparent", color: THEME.textMuted,
                      border: "none", cursor: "pointer", fontSize: "13px", lineHeight: 1,
                    },
                    onClick: (e: any) => {
                      e.preventDefault();
                      e.stopPropagation();
                      if (isMenuOpen) {
                        setMenuOpenId(null);
                      } else {
                        const rect = e.currentTarget.getBoundingClientRect();
                        const menuWidth = 160;
                        setMenuPos({
                          top: rect.top,
                          left: Math.max(4, rect.right - menuWidth),
                        });
                        setMenuOpenId(f.id);
                      }
                    },
                  }, "\u22ef"),
                  isMenuOpen
                    ? React.createElement("div", {
                        style: {
                          position: "fixed",
                          top: menuPos.top,
                          left: menuPos.left,
                          transform: "translateY(-100%)",
                          background: "#1a1a1a", border: `1px solid ${THEME.border}`, borderRadius: "4px",
                          minWidth: "160px", zIndex: 99999, boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
                        },
                      },
                        React.createElement("div", {
                          style: { padding: "6px 10px", color: "#ffa726", cursor: "pointer", fontSize: "11px" },
                          onClick: () => handleDetach(f.id),
                          onMouseEnter: (e: any) => { e.target.style.background = "#333"; },
                          onMouseLeave: (e: any) => { e.target.style.background = "transparent"; },
                        }, "Wrong person"),
                        React.createElement("div", {
                          style: { padding: "6px 10px", color: "#ef5350", cursor: "pointer", fontSize: "11px" },
                          onClick: () => handleDelete(f.id),
                          onMouseEnter: (e: any) => { e.target.style.background = "#333"; },
                          onMouseLeave: (e: any) => { e.target.style.background = "transparent"; },
                        }, "Delete face")
                      )
                    : null
                )
              ),
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

  function renderPanel(container: HTMLElement, imageId: number) {
    const el = React.createElement(ImageFacesGrid, { imageId });
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

  let _currentImageId: number | null = null;

  function isImageDetailTabActive(): boolean {
    // Check if the image-details-panel tab pane is active
    const pane = document.getElementById("image-details-panel");
    if (pane) return pane.classList.contains("active") || pane.classList.contains("show");
    // Fallback: look for image-related content in any active pane
    const activePane = document.querySelector('.tab-pane.active, .tab-pane.show') as HTMLElement | null;
    if (!activePane) return true;
    return !!(
      activePane.querySelector('.image-performers') ||
      activePane.querySelector('.performer-card') ||
      activePane.querySelector('.image-details')
    );
  }

  function injectPanel(imageId: number, attempt = 0) {
    if (_currentImageId === imageId && document.getElementById(PANEL_ID)) return;
    _currentImageId = imageId;

    // Clean up previous panel
    document.getElementById(PANEL_ID)?.remove();

    if (!isImageDetailTabActive()) return;

    let insertionPoint: HTMLElement | null = null;

    // Strategy 1: Append after .image-performers (the performers grid in ImageDetailPanel)
    const imagePerformers = document.querySelector('.image-performers') as HTMLElement | null;
    if (imagePerformers) {
      insertionPoint = imagePerformers;
    }

    // Strategy 2: Find any performer card and use its containing row
    if (!insertionPoint) {
      const perfCard = document.querySelector('.performer-card') as HTMLElement | null;
      if (perfCard) {
        insertionPoint = perfCard.closest('.row') as HTMLElement ||
                         perfCard.parentElement as HTMLElement;
      }
    }

    // Strategy 3: Append into the active image-details-panel tab pane
    if (!insertionPoint) {
      const pane = (
        document.getElementById("image-details-panel") ||
        document.querySelector('.tab-pane.active, .tab-pane.show')
      ) as HTMLElement | null;
      if (pane) insertionPoint = pane;
    }

    if (!insertionPoint) {
      if (attempt < 10) {
        setTimeout(() => injectPanel(imageId, attempt + 1), 200 + attempt * 150);
      }
      return;
    }

    const panel = document.createElement("div");
    panel.id = PANEL_ID;
    // Insert after the performers row / anchor element
    if (insertionPoint.nextSibling) {
      insertionPoint.parentNode?.insertBefore(panel, insertionPoint.nextSibling);
    } else {
      insertionPoint.parentNode?.appendChild(panel);
    }

    renderPanel(panel, imageId);
  }

  // ---------- Re-injection Observer ----------

  let _reinjectObserver: MutationObserver | null = null;

  function setupReinjectObserver() {
    if (_reinjectObserver) return;
    _reinjectObserver = new MutationObserver(() => {
      if (_currentImageId && !document.getElementById(PANEL_ID) && isImageDetailTabActive()) {
        injectPanel(_currentImageId);
      }
    });
    _reinjectObserver.observe(document.body, { childList: true, subtree: true });
  }

  // ---------- Integration ----------
  // ImageDetailPanel is a PatchComponent in Stash — ImagePage is not.

  let integrated = false;

  if (PluginApi.patch?.after) {
    // Primary: patch ImageDetailPanel (confirmed PatchComponent)
    try {
      PluginApi.patch.after("ImageDetailPanel", function (...args: any[]) {
        const result = args[args.length - 1];
        const props = args[0];
        const iid = props?.image?.id;
        if (iid) {
          const imageId = parseInt(String(iid), 10);
          setTimeout(() => injectPanel(imageId), 150);
        }
        return result;
      });
      integrated = true;
    } catch (e) {
      console.warn(LOG, "patch.after('ImageDetailPanel') failed:", e);
    }
  }

  // Fallback: URL detection
  if (!integrated) {
    function checkUrl() {
      const match = window.location.pathname.match(/\/images\/(\d+)/);
      if (match) {
        const iid = parseInt(match[1], 10);
        setTimeout(() => injectPanel(iid), 300);
      } else {
        _currentImageId = null;
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

  w.ImageFacesPanel = ImageFacesGrid;
})();
