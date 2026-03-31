/**
 * PerformerFacesPanel — Shows face clusters associated with a performer.
 *
 * Injected into the performer detail page via PluginApi.patch.
 * Falls back to URL-based detection if the patch point is unavailable.
 *
 * Registers: window.PerformerFacesPanel
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[PerformerFacesPanel] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useCallback } = React;

  const THEME = {
    bg: "#1a1a1a",
    bgCard: "#222",
    bgHover: "#2a2a2a",
    border: "#333",
    borderAccent: "rgba(72, 180, 97, 0.3)",
    text: "#eee",
    textMuted: "#888",
    accent: "#4caf50",
    accentHover: "#66bb6a",
  };

  function getApiBase(): string {
    const fn = w.AIDefaultBackendBase;
    const base = fn ? fn() : "";
    return base ? `${base}/api/v1/plugins/skier_aitagging` : "";
  }

  // ---------- Panel Component ----------

  function PerformerFacesPanel(props: { performerId: number }) {
    const { performerId } = props;
    const apiBase = getApiBase();
    const [clusters, setClusters] = useState([] as any[]);
    const [loading, setLoading] = useState(true);
    const [collapsed, setCollapsed] = useState(false);

    const fetchClusters = useCallback(async () => {
      if (!apiBase || !performerId) return;
      setLoading(true);
      try {
        const res = await fetch(
          `${apiBase}/faces/clusters?performer_id=${performerId}&per_page=50`
        );
        if (res.ok) {
          const data = await res.json();
          setClusters(data.clusters || []);
        }
      } catch (e) {
        console.error("[PerformerFacesPanel] fetch failed:", e);
      }
      setLoading(false);
    }, [apiBase, performerId]);

    useEffect(() => { fetchClusters(); }, [fetchClusters]);

    if (!apiBase) return null;
    if (!loading && clusters.length === 0) return null; // No faces: hide panel entirely

    return React.createElement(
      "div",
      {
        style: {
          marginTop: "16px",
          background: THEME.bgCard,
          borderRadius: "8px",
          border: `1px solid ${THEME.border}`,
          overflow: "hidden",
        },
      },
      // Header
      React.createElement(
        "div",
        {
          style: {
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "10px 14px",
            cursor: "pointer",
            userSelect: "none",
          },
          onClick: () => setCollapsed((c: boolean) => !c),
        },
        React.createElement(
          "h3",
          {
            style: {
              margin: 0,
              fontSize: "14px",
              fontWeight: 600,
              color: THEME.text,
              display: "flex",
              alignItems: "center",
              gap: "8px",
            },
          },
          "\uD83D\uDC64 Faces",
          !loading
            ? React.createElement(
                "span",
                {
                  style: {
                    fontSize: "11px",
                    fontWeight: 400,
                    color: THEME.textMuted,
                    background: `${THEME.accent}22`,
                    padding: "1px 6px",
                    borderRadius: "8px",
                  },
                },
                String(clusters.length)
              )
            : null
        ),
        React.createElement(
          "span",
          { style: { color: THEME.textMuted, fontSize: "12px" } },
          collapsed ? "\u25B6" : "\u25BC"
        )
      ),
      // Content
      !collapsed
        ? React.createElement(
            "div",
            {
              style: {
                padding: "0 14px 14px",
              },
            },
            loading
              ? React.createElement(
                  "div",
                  { style: { color: THEME.textMuted, fontSize: "12px", padding: "8px 0" } },
                  "Loading faces..."
                )
              : React.createElement(
                  "div",
                  {
                    style: {
                      display: "flex",
                      flexWrap: "wrap",
                      gap: "10px",
                    },
                  },
                  ...clusters.map((c: any) =>
                    React.createElement(
                      "a",
                      {
                        key: c.id,
                        href: `/plugins/ai-faces?id=${c.id}`,
                        style: {
                          display: "flex",
                          flexDirection: "column",
                          alignItems: "center",
                          textDecoration: "none",
                          width: "90px",
                        },
                        title: `Face #${c.id}${c.label ? ` — ${c.label}` : ""}`,
                      },
                      React.createElement("img", {
                        src: c.thumbnail_url || `${apiBase}/faces/clusters/${c.id}/thumbnail`,
                        style: {
                          width: "80px",
                          height: "80px",
                          borderRadius: "50%",
                          objectFit: "cover",
                          border: `2px solid ${THEME.border}`,
                          background: THEME.bg,
                          transition: "border-color 0.15s",
                        },
                        loading: "lazy",
                        onMouseEnter: (e: any) => {
                          e.currentTarget.style.borderColor = THEME.accent;
                        },
                        onMouseLeave: (e: any) => {
                          e.currentTarget.style.borderColor = THEME.border;
                        },
                        onError: (e: any) => {
                          e.currentTarget.style.display = "none";
                        },
                      }),
                      React.createElement(
                        "span",
                        {
                          style: {
                            fontSize: "11px",
                            color: THEME.textMuted,
                            marginTop: "4px",
                            textAlign: "center",
                            maxWidth: "80px",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          },
                        },
                        c.label || `Face #${c.id}`
                      ),
                      // Metadata line: quality + appearances
                      React.createElement(
                        "span",
                        {
                          style: {
                            fontSize: "10px",
                            color: THEME.textMuted,
                            marginTop: "2px",
                          },
                        },
                        [
                          c.quality_score != null
                            ? `Q: ${c.quality_score.toFixed(1)}`
                            : null,
                          (c.scene_count || 0) + (c.image_count || 0) > 0
                            ? `${(c.scene_count || 0) + (c.image_count || 0)} appearances`
                            : null,
                        ]
                          .filter(Boolean)
                          .join(" · ") || "\u00A0"
                      )
                    )
                  )
                )
          )
        : null
    );
  }

  // ---------- Integration ----------

  // Patch points to try — every working patch in the codebase uses patch.before
  const patchTargets = [
    "PerformerDetailsPanel",
    "PerformerPage",
  ];

  function extractPerformerId(props: any): number | null {
    // Different Stash versions pass performer data differently
    if (props?.performer?.id) return parseInt(props.performer.id, 10);
    if (props?.id) return parseInt(props.id, 10);
    return null;
  }

  let patched = false;
  if (PluginApi.patch && PluginApi.patch.before) {
    for (const target of patchTargets) {
      try {
        PluginApi.patch.before(target, function (props: any) {
          const performerId = extractPerformerId(props);
          if (!performerId) return [props];
          const panel = React.createElement(PerformerFacesPanel, {
            key: "ai-performer-faces",
            performerId,
          });
          const existing = props.children
            ? (Array.isArray(props.children) ? [...props.children] : [props.children])
            : [];
          existing.push(panel);
          return [{
            ...props,
            children: React.createElement(React.Fragment, null, ...existing.filter(Boolean)),
          }];
        });
        patched = true;
        console.log(`[PerformerFacesPanel] Patched ${target} via patch.before`);
        break;
      } catch (e) {
        // Patch point not available, try next
      }
    }
  }

  // Fallback: URL-based injection for performer pages via navigation listener
  if (!patched) {
    console.log("[PerformerFacesPanel] patch.before unavailable for performer pages, using URL fallback");

    let _lastPerformerId: number | null = null;
    let _container: HTMLElement | null = null;

    function injectPanel() {
      const match = window.location.pathname.match(/\/performers\/(\d+)/);
      const pid = match ? parseInt(match[1], 10) : null;

      // Clean up if we left the performer page
      if (!pid || pid !== _lastPerformerId) {
        if (_container) {
          _container.remove();
          _container = null;
        }
        _lastPerformerId = null;
      }
      if (!pid) return;
      if (_lastPerformerId === pid) return; // Already injected
      _lastPerformerId = pid;

      // Brief delay so the Stash page renders first
      setTimeout(() => {
        if (document.getElementById("ai-performer-faces-root")) return;
        // Find a suitable insertion point in the performer page
        const anchor =
          document.querySelector(".performer-details") ||
          document.querySelector(".detail-group") ||
          document.querySelector(".detail-container") ||
          document.querySelector("[class*='detail']");
        const parent = anchor?.parentElement;
        if (!parent) {
          console.warn("[PerformerFacesPanel] No suitable performer page container found");
          return;
        }
        _container = document.createElement("div");
        _container.id = "ai-performer-faces-root";
        parent.insertBefore(_container, anchor.nextSibling);

        // Render using PluginApi.ReactDOM (Stash provides this)
        const ReactDOM = w.ReactDOM || (PluginApi.libraries && PluginApi.libraries.ReactDOM);
        if (ReactDOM) {
          const el = React.createElement(PerformerFacesPanel, { performerId: pid });
          if (ReactDOM.createRoot) {
            ReactDOM.createRoot(_container).render(el);
          } else if (ReactDOM.render) {
            ReactDOM.render(el, _container);
          }
        }
      }, 300);
    }

    // Listen for SPA navigation
    const _origPush = history.pushState;
    history.pushState = function (...args: any[]) {
      _origPush.apply(this, args);
      setTimeout(injectPanel, 100);
    };
    window.addEventListener("popstate", () => setTimeout(injectPanel, 100));
    injectPanel(); // Initial check
  }

  w.PerformerFacesPanel = PerformerFacesPanel;
  console.log("[PerformerFacesPanel] Registered window.PerformerFacesPanel");
})();
