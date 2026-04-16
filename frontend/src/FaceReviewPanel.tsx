/**
 * FaceReviewPanel — Task-completion inline face review panel.
 *
 * Entry point: window.showFaceReviewPanel(options)
 * Creates a modal overlay showing newly discovered faces with
 * side-by-side face/performer comparison and link/search/defer actions.
 *
 * Registers: window.showFaceReviewPanel
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[FaceReviewPanel] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useCallback } = React;

  // ---------- Types ----------

  interface FaceReviewOptions {
    apiBase: string;
    newClusterIds: number[];
    matchedClusterIds: number[];
    facesNew: number;
    facesMatched: number;
    onClose?: () => void;
    onNavigateToHub?: () => void;
  }

  interface ClusterInfo {
    id: number;
    thumbnail_url: string;
    status: string;
    performer_id: number | null;
    stashdb_match: any | null;
    suggestions: any[];
    clusterSceneCount: number;
    clusterImageCount: number;
  }

  // ---------- Styles ----------

  const THEME = {
    bg: "#1a1a1a",
    bgCard: "#222",
    bgHover: "#2a2a2a",
    bgInput: "#111",
    border: "#333",
    borderAccent: "rgba(72, 180, 97, 0.3)",
    text: "#eee",
    textMuted: "#888",
    textSuccess: "#d4edda",
    accent: "#4caf50",
    accentHover: "#66bb6a",
    warning: "#ff9800",
    danger: "#f44336",
  };

  // ---------- Helper ----------

  function getPerformerImageUrl(imgPath: string): string {
    if (!imgPath) return "";
    if (imgPath.startsWith("http")) return imgPath;
    return imgPath;
  }

  // ---------- Panel Component ----------

  function FaceReviewPanelInner(props: FaceReviewOptions & { cleanup: () => void }) {
    const { apiBase, newClusterIds, matchedClusterIds, facesNew, facesMatched, cleanup } = props;
    const [clusters, setClusters] = useState([] as ClusterInfo[]);
    const [loading, setLoading] = useState(true);
    const [handled, setHandled] = useState(new Set() as Set<number>);
    const [linked, setLinked] = useState(0);
    const [deferred, setDeferred] = useState(0);
    const [deleted, setDeleted] = useState(0);
    const [searchOpenFor, setSearchOpenFor] = useState(null as number | null);
    const [hoveredPerformer, setHoveredPerformer] = useState(null as any);
    const [hoveredCluster, setHoveredCluster] = useState(null as number | null);
    const [selectedPerformers, setSelectedPerformers] = useState({} as Record<number, any>);
    const [thumbIndices, setThumbIndices] = useState({} as Record<number, number>);
    const [thumbCounts, setThumbCounts] = useState({} as Record<number, number>);

    // Load cluster details and suggestions on mount
    useEffect(() => {
      let cancelled = false;
      (async () => {
        const results: ClusterInfo[] = [];
        const ids = [...newClusterIds];
        // Fetch in parallel
        const fetches = ids.map(async (cid: number) => {
          try {
            const [detailRes, suggestRes] = await Promise.all([
              fetch(`${apiBase}/faces/clusters/${cid}`),
              fetch(`${apiBase}/faces/clusters/${cid}/suggested-performers`),
            ]);
            const detail = detailRes.ok ? await detailRes.json() : null;
            const suggestions = suggestRes.ok ? await suggestRes.json() : null;
            return {
              id: cid,
              thumbnail_url: `${apiBase}/faces/clusters/${cid}/thumbnail`,
              status: detail?.status || "unidentified",
              performer_id: detail?.performer_id || null,
              stashdb_match: detail?.stashdb_match || null,
              suggestions: suggestions?.suggestions || [],
              clusterSceneCount: suggestions?.cluster_scene_count || 0,
              clusterImageCount: suggestions?.cluster_image_count || 0,
            };
          } catch (e) {
            console.error("[FaceReviewPanel] Failed to load cluster", cid, e);
            return {
              id: cid,
              thumbnail_url: `${apiBase}/faces/clusters/${cid}/thumbnail`,
              status: "unidentified",
              performer_id: null,
              stashdb_match: null,
              suggestions: [],
              clusterSceneCount: 0,
              clusterImageCount: 0,
            };
          }
        });
        const all = await Promise.all(fetches);
        if (!cancelled) {
          setClusters(all);
          setLoading(false);
          // Fetch thumbnail counts in background
          for (const cl of all) {
            fetch(`${apiBase}/faces/clusters/${cl.id}/thumbnail-count`)
              .then((r: any) => r.ok ? r.json() : null)
              .then((d: any) => { if (d && d.count > 1 && !cancelled) setThumbCounts((prev: any) => ({ ...prev, [cl.id]: d.count })); })
              .catch(() => {});
          }
        }
      })();
      return () => { cancelled = true; };
    }, [apiBase, newClusterIds]);

    const handleLink = useCallback(async (clusterId: number, performerId: number, performerName?: string, thumbnailIndex?: number, hydrateFromStashdb?: boolean) => {
      try {
        const res = await fetch(`${apiBase}/faces/clusters/${clusterId}/link`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            performer_id: performerId,
            performer_name: performerName || null,
            thumbnail_index: thumbnailIndex,
            hydrate_from_stashdb: hydrateFromStashdb !== false,
          }),
        });
        if (res.ok) {
          setHandled((prev: Set<number>) => new Set([...prev, clusterId]));
          setLinked((prev: number) => prev + 1);
          setSearchOpenFor(null);
        }
      } catch (e) {
        console.error("[FaceReviewPanel] Link failed:", e);
      }
    }, [apiBase]);

    const handleDoLater = useCallback((clusterId: number) => {
      setHandled((prev: Set<number>) => new Set([...prev, clusterId]));
      setDeferred((prev: number) => prev + 1);
      setSearchOpenFor(null);
    }, []);

    const handleDelete = useCallback(async (clusterId: number) => {
      try {
        const res = await fetch(`${apiBase}/faces/clusters/${clusterId}`, {
          method: "DELETE",
        });
        if (res.ok) {
          setHandled((prev: Set<number>) => new Set([...prev, clusterId]));
          setDeleted((prev: number) => prev + 1);
          setSearchOpenFor(null);
        }
      } catch (e) {
        console.error("[FaceReviewPanel] Delete failed:", e);
      }
    }, [apiBase]);

    const handleCreatePerformer = useCallback(async (clusterId: number) => {
      const name = w.prompt("Performer name:", `Unidentified #${clusterId}`);
      if (!name) return; // cancelled
      try {
        const res = await fetch(`${apiBase}/faces/clusters/${clusterId}/create-performer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, set_performer_image: true }),
        });
        if (res.ok) {
          setHandled((prev: Set<number>) => new Set([...prev, clusterId]));
          setLinked((prev: number) => prev + 1);
          setSearchOpenFor(null);
        }
      } catch (e) {
        console.error("[FaceReviewPanel] Create performer failed:", e);
      }
    }, [apiBase]);

    const navigateToHub = useCallback(() => {
      cleanup();
      if (w.location) w.location.href = "/plugins/ai-faces?status=unidentified";
    }, [cleanup]);

    const visibleClusters = clusters.filter((c: ClusterInfo) => !handled.has(c.id));
    const allHandled = visibleClusters.length === 0 && !loading;

    // Compute truly-existing matches (matched to clusters from *prior* runs,
    // not clusters created during the same batch).
    const newSet = new Set(newClusterIds);
    const priorMatched = matchedClusterIds.filter((id: number) => !newSet.has(id)).length;

    // Summary bar
    const totalUnique = facesNew + priorMatched;
    const summaryText = allHandled
      ? `${totalUnique} face${totalUnique !== 1 ? "s" : ""}: ${linked} linked, ${deferred} deferred${deleted ? `, ${deleted} deleted` : ""}`
      : priorMatched > 0
        ? `Detected ${totalUnique} unique face${totalUnique !== 1 ? "s" : ""} (${facesNew} new, ${priorMatched} seen before).`
        : `Detected ${facesNew} unique face${facesNew !== 1 ? "s" : ""}.`;

    const renderRow = (c: ClusterInfo) => {
      const topSuggestion = c.suggestions[0] || null;
      const isSearchOpen = searchOpenFor === c.id;
      const hoveringPerformerForThis = hoveredCluster === c.id ? hoveredPerformer : null;
      const selectedPerformer = selectedPerformers[c.id] || null;

      return React.createElement(
        "div",
        {
          key: c.id,
          style: {
            display: "flex",
            alignItems: "flex-start",
            gap: "12px",
            padding: "10px",
            borderBottom: `1px solid ${THEME.border}`,
          },
        },
        // LEFT: Unknown face thumbnail with cycling
        (() => {
          const idx = thumbIndices[c.id] || 0;
          const count = thumbCounts[c.id] || 1;
          const thumbSrc = `${c.thumbnail_url}?size=150&index=${idx}&pad=0.2`;
          return React.createElement(
            "div",
            { style: { position: "relative", flexShrink: 0, width: 80, height: 80 } },
            React.createElement("img", {
              src: thumbSrc,
              style: {
                width: 80,
                height: 80,
                borderRadius: "6px",
                objectFit: "cover",
                border: `2px solid ${THEME.border}`,
              },
              loading: "lazy",
            }),
            count > 1
              ? React.createElement("div", {
                  style: {
                    position: "absolute", bottom: "0", left: "0", right: "0",
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: "2px 2px",
                    background: "linear-gradient(transparent, rgba(0,0,0,0.55))",
                    borderRadius: "0 0 6px 6px",
                  },
                },
                  React.createElement("button", {
                    style: {
                      background: "rgba(0,0,0,0.6)", border: "none", color: "#fff",
                      borderRadius: "50%", width: "16px", height: "16px", cursor: "pointer",
                      fontSize: "8px", display: "flex", alignItems: "center", justifyContent: "center",
                      padding: 0,
                    },
                    onClick: () => setThumbIndices((prev: any) => ({ ...prev, [c.id]: ((prev[c.id] || 0) - 1 + count) % count })),
                  }, "\u25C0"),
                  React.createElement("button", {
                    style: {
                      background: "rgba(0,0,0,0.6)", border: "none", color: "#fff",
                      borderRadius: "50%", width: "16px", height: "16px", cursor: "pointer",
                      fontSize: "8px", display: "flex", alignItems: "center", justifyContent: "center",
                      padding: 0,
                    },
                    onClick: () => setThumbIndices((prev: any) => ({ ...prev, [c.id]: ((prev[c.id] || 0) + 1) % count })),
                  }, "\u25B6")
                )
              : null
          );
        })(),
        // CENTER: info + actions
        React.createElement(
          "div",
          { style: { flex: 1, minWidth: 0 } },
          React.createElement(
            "div",
            { style: { color: THEME.text, fontWeight: 500, marginBottom: "4px" } },
            "Unknown face"
          ),
          c.stashdb_match && !isSearchOpen && !selectedPerformer
            ? React.createElement(
                "div",
                { style: { marginBottom: "6px" } },
                React.createElement(
                  "span",
                  { style: { color: "#7ec8ff", fontSize: "12px", fontWeight: 600 } },
                  `StashDB: ${c.stashdb_match.name || "Unknown"} (${Math.round((c.stashdb_match.similarity || 0) * 100)}%)`
                )
              )
            : null,
          topSuggestion && !isSearchOpen && !selectedPerformer
            ? React.createElement(
                "div",
                { style: { marginBottom: "6px" } },
                React.createElement(
                  "span",
                  { style: { color: topSuggestion.confidence === "stashdb" ? "#7ec8ff" : THEME.textMuted, fontSize: "12px" } },
                  topSuggestion.confidence === "stashdb" ? "StashDB match: " : `Likely: `,
                  React.createElement("strong", { style: { color: THEME.text } }, topSuggestion.performer_name),
                  topSuggestion.confidence === "stashdb" && topSuggestion.stashdb_match
                    ? ` (${Math.round((topSuggestion.stashdb_match.similarity || 0) * 100)}%)`
                    : ` (${[
                        topSuggestion.scene_count ? `${topSuggestion.scene_count} scenes` : null,
                        topSuggestion.image_count ? `${topSuggestion.image_count} images` : null,
                      ]
                        .filter(Boolean)
                        .join(", ")})`
                )
              )
            : !isSearchOpen && !selectedPerformer && !c.stashdb_match
            ? React.createElement(
                "div",
                { style: { color: THEME.textMuted, fontSize: "12px", marginBottom: "6px" } },
                "No strong match"
              )
            : null,
          // Selected performer confirmation bar (collapsed from search)
          selectedPerformer && !isSearchOpen
            ? React.createElement(
                "div",
                { style: { marginBottom: "6px" } },
                React.createElement(
                  "span",
                  { style: { color: THEME.accent, fontSize: "12px", fontWeight: 600 } },
                  `Selected: ${selectedPerformer.name || selectedPerformer.performer_name || "Unknown"}`
                )
              )
            : null,
          // Search mode: inline PerformerSearch (only when actively searching)
          isSearchOpen && w.PerformerSearchWidget
            ? React.createElement(
                "div",
                { style: { marginBottom: "6px" } },
                React.createElement(w.PerformerSearchWidget, {
                  onSelect: (p: any) => {
                    // Select and collapse the search widget
                    setSelectedPerformers((prev: any) => ({ ...prev, [c.id]: p }));
                    setSearchOpenFor(null);
                  },
                  onHover: (p: any) => {
                    setHoveredPerformer(p);
                    setHoveredCluster(p ? c.id : null);
                  },
                  selectedPerformerId: selectedPerformer?.id || null,
                  suggestedPerformers: c.suggestions,
                  placeholder: "Search performers...",
                })
              )
            : null,
          // Action buttons — three states:
          // 1) Default (no search, no selection): Link/Search/DoLater
          // 2) Search open: Cancel only
          // 3) Selection made (search collapsed): Confirm/Change/Cancel
          !isSearchOpen && !selectedPerformer
            ? React.createElement(
                "div",
                { style: { display: "flex", gap: "6px", flexWrap: "wrap" } },
                topSuggestion
                  ? React.createElement(
                      "button",
                      {
                        style: {
                          padding: "4px 10px",
                          background: topSuggestion.confidence === "stashdb" ? "#1565c0" : THEME.accent,
                          color: "#fff",
                          border: "none",
                          borderRadius: "4px",
                          cursor: "pointer",
                          fontSize: "12px",
                        },
                        onClick: () => handleLink(c.id, topSuggestion.performer_id, topSuggestion.performer_name, thumbIndices[c.id] || 0, Boolean(c.stashdb_match?.ref_id || topSuggestion.stashdb_match)),
                      },
                      `Link to ${topSuggestion.performer_name}`
                    )
                  : null,
                React.createElement(
                  "button",
                  {
                    style: {
                      padding: "4px 10px",
                      background: "transparent",
                      color: THEME.text,
                      border: `1px solid ${THEME.border}`,
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "12px",
                    },
                    onClick: () => setSearchOpenFor(c.id),
                  },
                  "Search..."
                ),
                React.createElement(
                  "button",
                  {
                    style: {
                      padding: "4px 10px",
                      background: "transparent",
                      color: THEME.accent,
                      border: `1px solid ${THEME.accent}55`,
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "12px",
                    },
                    onClick: () => handleCreatePerformer(c.id),
                    title: "Create a new performer and link this face to them",
                  },
                  "Create"
                ),
                React.createElement(
                  "button",
                  {
                    style: {
                      padding: "4px 10px",
                      background: "transparent",
                      color: THEME.textMuted,
                      border: `1px solid ${THEME.border}`,
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "12px",
                    },
                    onClick: () => handleDoLater(c.id),
                  },
                  "Do Later"
                ),
                React.createElement(
                  "button",
                  {
                    style: {
                      padding: "4px 10px",
                      background: "transparent",
                      color: THEME.danger,
                      border: `1px solid ${THEME.danger}33`,
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "12px",
                    },
                    onClick: () => handleDelete(c.id),
                    title: "Permanently delete this face",
                  },
                  "Bad Image"
                )
              )
            : selectedPerformer && !isSearchOpen
            ? // Selection made — confirm / change / cancel
              React.createElement(
                "div",
                { style: { display: "flex", gap: "6px", flexWrap: "wrap" } },
                React.createElement(
                  "button",
                  {
                    style: {
                      padding: "4px 10px",
                      background: THEME.accent,
                      color: "#fff",
                      border: "none",
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "12px",
                      fontWeight: 600,
                    },
                    onClick: () => {
                      handleLink(
                        c.id,
                        selectedPerformer.id || selectedPerformer.performer_id,
                        selectedPerformer.name || selectedPerformer.performer_name,
                        thumbIndices[c.id] || 0,
                        Boolean(c.stashdb_match?.ref_id),
                      );
                      setSelectedPerformers((prev: any) => {
                        const next = { ...prev };
                        delete next[c.id];
                        return next;
                      });
                    },
                  },
                  "Confirm"
                ),
                React.createElement(
                  "button",
                  {
                    style: {
                      padding: "4px 10px",
                      background: "transparent",
                      color: THEME.text,
                      border: `1px solid ${THEME.border}`,
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "12px",
                    },
                    onClick: () => setSearchOpenFor(c.id),
                  },
                  "Change"
                ),
                React.createElement(
                  "button",
                  {
                    style: {
                      padding: "4px 10px",
                      background: "transparent",
                      color: THEME.textMuted,
                      border: `1px solid ${THEME.border}`,
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "12px",
                    },
                    onClick: () => {
                      setSelectedPerformers((prev: any) => {
                        const next = { ...prev };
                        delete next[c.id];
                        return next;
                      });
                    },
                  },
                  "Cancel"
                )
              )
            : // Search open — just a cancel button
              React.createElement(
                "button",
                {
                  style: {
                    padding: "4px 10px",
                    background: "transparent",
                    color: THEME.textMuted,
                    border: `1px solid ${THEME.border}`,
                    borderRadius: "4px",
                    cursor: "pointer",
                    fontSize: "12px",
                    marginTop: "4px",
                  },
                  onClick: () => {
                    setSearchOpenFor(null);
                    setSelectedPerformers((prev: any) => {
                      const next = { ...prev };
                      delete next[c.id];
                      return next;
                    });
                  },
                },
                "Cancel"
              )
        ),
        // RIGHT: Suggested performer image (side-by-side)
        // Priority: hovered > selected > StashDB match image > top suggestion (when not in search/selection mode)
        (() => {
          const showPerformer = hoveringPerformerForThis || selectedPerformer ||
            (topSuggestion && !isSearchOpen && !selectedPerformer ? topSuggestion : null);
          let imgSrc = showPerformer
            ? getPerformerImageUrl(showPerformer.performer_image || showPerformer.image_path || "")
            : "";
          // For StashDB-sourced suggestions, try the StashDB image URL
          if (!imgSrc && showPerformer?.stashdb_match?.image_url) {
            imgSrc = showPerformer.stashdb_match.image_url;
          }
          // If no performer is shown but we have a StashDB match with an image, use it
          if (!imgSrc && c.stashdb_match && !isSearchOpen && !selectedPerformer) {
            imgSrc = c.stashdb_match.image_url || "";
            // Fallback: if the match has a local_performer_id, use Stash's performer image endpoint
            if (!imgSrc && c.stashdb_match.local_performer_id) {
              imgSrc = `/performer/${c.stashdb_match.local_performer_id}/image`;
            }
          }
          return imgSrc
            ? React.createElement("img", {
                src: imgSrc,
                style: {
                  width: 80,
                  height: 80,
                  borderRadius: "6px",
                  objectFit: "cover",
                  flexShrink: 0,
                  border: `2px solid ${selectedPerformer ? THEME.accent : THEME.borderAccent}`,
                },
                loading: "lazy",
                onError: (e: any) => { e.target.style.display = "none"; },
              })
            : React.createElement("div", {
                style: {
                  width: 80,
                  height: 80,
                  borderRadius: "6px",
                  background: THEME.border,
                  flexShrink: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: THEME.textMuted,
                  fontSize: "10px",
                  textAlign: "center" as any,
                },
              }, "?");
        })()
      );
    };

    // Modal content
    return React.createElement(
      "div",
      {
        style: {
          background: THEME.bg,
          border: `1px solid ${THEME.borderAccent}`,
          borderRadius: "8px",
          maxWidth: "680px",
          width: "90vw",
          maxHeight: "80vh",
          display: "flex",
          flexDirection: "column",
          boxShadow: "0 8px 32px rgba(0, 0, 0, 0.5)",
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
            padding: "14px 16px",
            borderBottom: `1px solid ${THEME.border}`,
          },
        },
        React.createElement(
          "div",
          null,
          React.createElement(
            "h3",
            { style: { margin: 0, color: THEME.textSuccess, fontSize: "16px", fontWeight: 600 } },
            "Faces Found"
          ),
          React.createElement(
            "div",
            { style: { color: THEME.textMuted, fontSize: "12px", marginTop: "2px" } },
            summaryText
          )
        ),
        React.createElement(
          "button",
          {
            style: {
              background: "transparent",
              border: "none",
              color: THEME.textMuted,
              fontSize: "20px",
              cursor: "pointer",
              padding: "4px 8px",
            },
            onClick: cleanup,
          },
          "\u00d7"
        )
      ),
      // Body
      React.createElement(
        "div",
        {
          style: {
            flex: 1,
            overflowY: "auto",
            padding: allHandled ? "16px" : 0,
          },
        },
        loading
          ? React.createElement(
              "div",
              { style: { padding: "20px", textAlign: "center", color: THEME.textMuted } },
              "Loading face data..."
            )
          : allHandled
          ? React.createElement(
              "div",
              { style: { textAlign: "center", color: THEME.textMuted } },
              summaryText
            )
          : visibleClusters.map(renderRow)
      ),
      // Footer
      React.createElement(
        "div",
        {
          style: {
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "10px 16px",
            borderTop: `1px solid ${THEME.border}`,
          },
        },
        React.createElement(
          "a",
          {
            href: "#",
            style: { color: THEME.accent, fontSize: "12px", textDecoration: "none" },
            onClick: (e: any) => {
              e.preventDefault();
              navigateToHub();
            },
          },
          "Review all in Faces hub"
        ),
        React.createElement(
          "span",
          { style: { color: THEME.textMuted, fontSize: "12px" } },
          `${visibleClusters.length} of ${clusters.length} remaining`
        )
      )
    );
  }

  // ---------- Imperative entry point ----------

  function showFaceReviewPanel(options: FaceReviewOptions) {
    const modalId = `face-review-panel-${Date.now()}`;
    const overlay = document.createElement("div");
    overlay.id = modalId;
    overlay.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.7);
      z-index: 20000;
      display: flex;
      align-items: center;
      justify-content: center;
      animation: fadeIn 0.2s ease-out;
    `;
    document.body.appendChild(overlay);

    const cleanup = () => {
      try {
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
      } catch (e) {}
      options.onClose?.();
    };

    // Close on overlay click (not panel click).
    // Track mousedown target to avoid closing when user drag-selects text
    // and the mouseup lands on the overlay.
    let mouseDownTarget: EventTarget | null = null;
    overlay.addEventListener("mousedown", (e: any) => {
      mouseDownTarget = e.target;
    });
    overlay.addEventListener("mouseup", (e: any) => {
      if (e.target === overlay && mouseDownTarget === overlay) cleanup();
      mouseDownTarget = null;
    });

    // Close on Escape
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        cleanup();
        document.removeEventListener("keydown", onKeyDown);
      }
    };
    document.addEventListener("keydown", onKeyDown);

    // Render React component into overlay using PluginApi pattern
    const container = document.createElement("div");
    overlay.appendChild(container);

    // Use PluginApi.ReactDOM if available, otherwise manual rendering
    const panelElement = React.createElement(FaceReviewPanelInner, {
      ...options,
      cleanup,
    });

    // Try ReactDOM.render or manual approach
    try {
      const ReactDOM = (w as any).ReactDOM || PluginApi.ReactDOM;
      if (ReactDOM && ReactDOM.render) {
        ReactDOM.render(panelElement, container);
      } else if (ReactDOM && ReactDOM.createRoot) {
        const root = ReactDOM.createRoot(container);
        root.render(panelElement);
      } else {
        // Fallback: use a simple approach
        // Mount via createElement cycle if ReactDOM is unavailable
        console.warn("[FaceReviewPanel] ReactDOM not accessible; using innerHTML fallback");
        container.innerHTML = `<div style="background:#1a1a1a;border-radius:8px;padding:24px;color:#eee;max-width:680px;width:90vw;">
          <p>Face review requires ReactDOM. Open the <a href="/plugins/ai-faces" style="color:#4caf50;">Faces hub</a> instead.</p>
        </div>`;
      }
    } catch (err) {
      console.error("[FaceReviewPanel] Render error:", err);
    }
  }

  // ---------- Register ----------

  w.showFaceReviewPanel = showFaceReviewPanel;
  console.log("[FaceReviewPanel] Registered window.showFaceReviewPanel");
})();
