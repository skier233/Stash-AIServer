/**
 * TasteProfile — Full-page visualisation of the system's understanding of
 * user preferences.  Shows tag affinities (horizontal bars anchored at 50
 * neutral), performer affinities, negative signals, embedding coverage,
 * and engagement distribution stats.
 *
 * Registers: window.TasteProfile
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[TasteProfile] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useRef } = React;

  const LOG = "[TasteProfile]";

  // ---- backend helpers (same pattern as other pages) ----

  function getApiBase(): string {
    const fn = w.AIDefaultBackendBase;
    const base = typeof fn === "function" ? fn() : "";
    return base ? `${base}/api/v1` : "";
  }

  function getSharedApiKey(): string {
    try {
      const helper = w.AISharedApiKeyHelper;
      if (helper && typeof helper.get === "function") {
        const v = helper.get();
        if (typeof v === "string") return v.trim();
      }
    } catch {}
    const raw = w.AI_SHARED_API_KEY;
    return typeof raw === "string" ? raw.trim() : "";
  }

  function withHeaders(init?: any): any {
    const helper = w.AISharedApiKeyHelper;
    if (helper && typeof helper.withHeaders === "function") {
      return helper.withHeaders(init || {});
    }
    const key = getSharedApiKey();
    if (!key) return init || {};
    const headers = { ...(init?.headers || {}) };
    headers["x-ai-api-key"] = key;
    return { ...(init || {}), headers };
  }

  // ---- styles ----

  const S: Record<string, React.CSSProperties> = {
    page: {
      maxWidth: 1100,
      margin: "0 auto",
      padding: "24px 20px",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      color: "#e0e0e0",
    },
    h1: { fontSize: 22, fontWeight: 700, margin: "0 0 6px", color: "#fff" },
    subtitle: { fontSize: 13, color: "#888", marginBottom: 20 },
    statsRow: {
      display: "flex",
      gap: 16,
      flexWrap: "wrap" as any,
      marginBottom: 24,
    },
    statBox: {
      background: "#1e1e24",
      borderRadius: 8,
      padding: "14px 18px",
      minWidth: 130,
      flex: "1 1 130px",
    },
    statValue: { fontSize: 22, fontWeight: 700, color: "#fff", lineHeight: 1 },
    statLabel: {
      fontSize: 11,
      color: "#888",
      textTransform: "uppercase" as any,
      letterSpacing: 0.5,
      marginTop: 4,
    },
    section: { marginBottom: 28 },
    sectionTitle: {
      fontSize: 15,
      fontWeight: 600,
      color: "#ccc",
      marginBottom: 10,
      borderBottom: "1px solid #333",
      paddingBottom: 6,
    },
    barRow: {
      display: "flex",
      alignItems: "center",
      marginBottom: 4,
      height: 22,
    },
    barLabel: {
      width: 160,
      fontSize: 12,
      color: "#bbb",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap" as any,
      flexShrink: 0,
    },
    barTrack: {
      flex: 1,
      height: 14,
      background: "#2a2a30",
      borderRadius: 3,
      position: "relative" as any,
      overflow: "hidden",
    },
    barFillPos: {
      position: "absolute" as any,
      top: 0,
      height: "100%",
      borderRadius: 3,
    },
    barValue: {
      width: 48,
      textAlign: "right" as any,
      fontSize: 11,
      color: "#888",
      flexShrink: 0,
      marginLeft: 6,
    },
    error: { color: "#f44336", padding: 20, textAlign: "center" as any },
    loading: { color: "#888", padding: 40, textAlign: "center" as any },
    centerLine: {
      position: "absolute" as any,
      left: "50%",
      top: 0,
      bottom: 0,
      width: 1,
      background: "#555",
    },
    perfRow: {
      display: "flex",
      alignItems: "center",
      marginBottom: 5,
      gap: 10,
    },
    perfName: {
      width: 180,
      fontSize: 12,
      color: "#bbb",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap" as any,
      flexShrink: 0,
    },
    perfBar: {
      flex: 1,
      height: 16,
      background: "#2a2a30",
      borderRadius: 3,
      position: "relative" as any,
      overflow: "hidden",
    },
    engTable: {
      width: "100%",
      borderCollapse: "collapse" as any,
      fontSize: 12,
      marginTop: 8,
    },
    engTh: {
      textAlign: "left" as any,
      color: "#888",
      fontSize: 10,
      textTransform: "uppercase" as any,
      padding: "4px 8px",
      borderBottom: "1px solid #333",
    },
    engTd: { padding: "4px 8px", color: "#ccc" },
    coverageBar: {
      height: 20,
      borderRadius: 4,
      overflow: "hidden",
      background: "#2a2a30",
      position: "relative" as any,
    },
    coverageFill: {
      height: "100%",
      borderRadius: 4,
      transition: "width 0.3s ease",
    },
    coverageLabel: {
      position: "absolute" as any,
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontSize: 11,
      color: "#fff",
      fontWeight: 600,
    },
    refreshBtn: {
      background: "#333",
      border: "1px solid #555",
      borderRadius: 4,
      color: "#ccc",
      padding: "6px 14px",
      cursor: "pointer",
      fontSize: 12,
      marginLeft: 12,
    },
  };

  // ---- sub-components ----

  function StatBox({ value, label, color }: { value: string | number; label: string; color?: string }) {
    return React.createElement("div", { style: S.statBox },
      React.createElement("div", { style: { ...S.statValue, color: color || "#fff" } }, value),
      React.createElement("div", { style: S.statLabel }, label),
    );
  }

  /** Horizontal bar anchored at 50 (neutral).  >50 fills right (green), <50 fills left (red). */
  function AffinityBar({ name, affinity, negWeight }: { name: string; affinity: number; negWeight?: number }) {
    const pct = Math.max(0, Math.min(100, affinity));
    const isPositive = pct >= 50;
    const barWidth = Math.abs(pct - 50) ; // 0-50 %
    const color = isPositive ? "#66bb6a" : "#ef5350";
    const fillStyle: any = {
      ...S.barFillPos,
      background: color,
      width: `${barWidth}%`,
    };
    if (isPositive) {
      fillStyle.left = "50%";
    } else {
      fillStyle.right = "50%";
    }
    return React.createElement("div", { style: S.barRow },
      React.createElement("div", { style: S.barLabel, title: name }, name),
      React.createElement("div", { style: S.barTrack },
        React.createElement("div", { style: S.centerLine }),
        React.createElement("div", { style: fillStyle }),
      ),
      React.createElement("div", { style: S.barValue },
        pct.toFixed(0),
        negWeight ? ` (-${(negWeight * 100).toFixed(0)})` : "",
      ),
    );
  }

  function PerformerBar({ name, affinity, negWeight }: { name: string; affinity: number; negWeight?: number }) {
    const pct = Math.max(0, Math.min(100, affinity));
    const isPositive = pct >= 50;
    const barWidth = Math.abs(pct - 50);
    const color = isPositive ? "#7e57c2" : "#ef5350";
    const fillStyle: any = {
      ...S.barFillPos,
      background: color,
      width: `${barWidth}%`,
    };
    if (isPositive) fillStyle.left = "50%";
    else fillStyle.right = "50%";
    return React.createElement("div", { style: S.perfRow },
      React.createElement("div", { style: S.perfName, title: name }, name),
      React.createElement("div", { style: S.perfBar },
        React.createElement("div", { style: S.centerLine }),
        React.createElement("div", { style: fillStyle }),
      ),
      React.createElement("div", { style: S.barValue },
        pct.toFixed(0),
        negWeight ? ` (-${(negWeight * 100).toFixed(0)})` : "",
      ),
    );
  }

  /** Grid of scene thumbnails (screenshots) from scene IDs. */
  function SceneThumbnailGrid({ sceneIds, loading: isLoading }: { sceneIds: number[]; loading?: boolean }) {
    if (isLoading) {
      return React.createElement("div", { style: { color: "#888", fontSize: 11, padding: "8px 0" } }, "Loading scenes...");
    }
    if (!sceneIds || sceneIds.length === 0) {
      return React.createElement("div", { style: { color: "#666", fontSize: 11, padding: "8px 0" } }, "No scenes found.");
    }
    return React.createElement("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
        gap: 6,
        marginTop: 8,
        maxHeight: 340,
        overflowY: "auto" as any,
      },
    },
      sceneIds.map((sid: number) =>
        React.createElement("a", {
          key: sid,
          href: `/scenes/${sid}`,
          target: "_blank",
          rel: "noopener",
          style: { display: "block", borderRadius: 4, overflow: "hidden", background: "#111", position: "relative" as any },
          title: `Scene ${sid}`,
        },
          React.createElement("img", {
            src: `/scene/${sid}/screenshot`,
            style: { width: "100%", height: 80, objectFit: "cover" as any, display: "block" },
            loading: "lazy",
          }),
          React.createElement("div", {
            style: { fontSize: 9, color: "#888", textAlign: "center" as any, padding: "2px 0", background: "#1a1a1e" },
          }, `#${sid}`),
        ),
      ),
    );
  }

  // ---- main component ----

  function TasteProfile() {
    const [data, setData] = useState(null as any);
    const [centroids, setCentroids] = useState([] as any[]);
    const [clusters, setClusters] = useState([] as any[]);
    const [loading, setLoading] = useState(false);
    const [recomputing, setRecomputing] = useState(false);
    const [error, setError] = useState(null as string | null);
    const [fetchCount, setFetchCount] = useState(0);  // bump to re-fetch

    // Expandable cluster / centroid state
    const [expandedCentroids, setExpandedCentroids] = useState({} as any);
    const [centroidScenes, setCentroidScenes] = useState({} as any);
    const [centroidLoading, setCentroidLoading] = useState({} as any);
    const [expandedClusters, setExpandedClusters] = useState({} as any);
    const [clusterScenes, setClusterScenes] = useState({} as any);
    const [clusterLoading, setClusterLoading] = useState({} as any);

    useEffect(() => {
      let cancelled = false;
      setLoading(true);
      setError(null);

      const apiBase = getApiBase();
      if (!apiBase) {
        setError("Backend base URL not configured");
        setLoading(false);
        return;
      }

      // Fetch profile, centroids, and clusters in parallel
      Promise.all([
        fetch(`${apiBase}/taste-profile/summary`, withHeaders())
          .then((res) => res.ok ? res.json() : null),
        fetch(`${apiBase}/taste-profile/centroids`, withHeaders())
          .then((res) => res.ok ? res.json() : [])
          .catch(() => []),
        fetch(`${apiBase}/taste-profile/clusters`, withHeaders())
          .then((res) => res.ok ? res.json() : [])
          .catch(() => []),
      ])
        .then(([profileJson, centroidsJson, clustersJson]) => {
          if (cancelled) return;
          if (profileJson?.error) throw new Error(profileJson.error);
          setData(profileJson);
          setCentroids(Array.isArray(centroidsJson) ? centroidsJson : centroidsJson?.centroids || []);
          setClusters(Array.isArray(clustersJson) ? clustersJson : clustersJson?.clusters || []);
          setLoading(false);
        })
        .catch((e) => {
          if (cancelled) return;
          console.error(LOG, "fetch failed:", e);
          setError(e.message || "Unknown error");
          setLoading(false);
        });

      return () => { cancelled = true; };
    }, [fetchCount]);

    function doRefresh() { setFetchCount((c: number) => c + 1); }

    function doRecompute() {
      const apiBase = getApiBase();
      if (!apiBase) return;
      setRecomputing(true);
      fetch(`${apiBase}/taste-profile/recompute`, withHeaders({ method: "POST" }))
        .then((res) => res.json())
        .then(() => {
          setRecomputing(false);
          setFetchCount((c: number) => c + 1);
        })
        .catch((e) => {
          console.warn(LOG, "recompute failed:", e);
          setRecomputing(false);
        });
    }

    function doRecomputeClusters() {
      const apiBase = getApiBase();
      if (!apiBase) return;
      setRecomputing(true);
      fetch(`${apiBase}/taste-profile/clusters/recompute`, withHeaders({ method: "POST" }))
        .then((res) => res.json())
        .then(() => {
          setRecomputing(false);
          setFetchCount((c: number) => c + 1);
        })
        .catch((e) => {
          console.warn(LOG, "cluster recompute failed:", e);
          setRecomputing(false);
        });
    }

    function toggleCentroid(centroidKey: string, centroidType: string, embeddingType: string) {
      const isOpen = !!expandedCentroids[centroidKey];
      setExpandedCentroids((prev: any) => ({ ...prev, [centroidKey]: !isOpen }));
      if (!isOpen && !centroidScenes[centroidKey]) {
        const apiBase = getApiBase();
        if (!apiBase) return;
        setCentroidLoading((prev: any) => ({ ...prev, [centroidKey]: true }));
        fetch(`${apiBase}/taste-profile/centroids/scenes?centroid_type=${encodeURIComponent(centroidType)}&embedding_type=${encodeURIComponent(embeddingType)}&limit=30`, withHeaders())
          .then((r) => r.ok ? r.json() : { scene_ids: [] })
          .then((j: any) => {
            setCentroidScenes((prev: any) => ({ ...prev, [centroidKey]: j.scene_ids || [] }));
            setCentroidLoading((prev: any) => ({ ...prev, [centroidKey]: false }));
          })
          .catch(() => setCentroidLoading((prev: any) => ({ ...prev, [centroidKey]: false })));
      }
    }

    function toggleCluster(clusterId: number) {
      const isOpen = !!expandedClusters[clusterId];
      setExpandedClusters((prev: any) => ({ ...prev, [clusterId]: !isOpen }));
      if (!isOpen && !clusterScenes[clusterId]) {
        const apiBase = getApiBase();
        if (!apiBase) return;
        setClusterLoading((prev: any) => ({ ...prev, [clusterId]: true }));
        fetch(`${apiBase}/taste-profile/clusters/${clusterId}/scenes?limit=50`, withHeaders())
          .then((r) => r.ok ? r.json() : { scene_ids: [] })
          .then((j: any) => {
            setClusterScenes((prev: any) => ({ ...prev, [clusterId]: j.scene_ids || [] }));
            setClusterLoading((prev: any) => ({ ...prev, [clusterId]: false }));
          })
          .catch(() => setClusterLoading((prev: any) => ({ ...prev, [clusterId]: false })));
      }
    }

    if (loading && !data) {
      return React.createElement("div", { style: S.loading },
        "Loading taste profile...",
      );
    }
    if (error && !data) {
      return React.createElement("div", { style: S.error },
        `Error: ${error}`,
        React.createElement("button", { style: { ...S.refreshBtn, marginLeft: 12 }, onClick: doRefresh }, "Retry"),
      );
    }
    if (!data) return null;

    const tags: any[] = data.tags || [];
    const negTags: any[] = data.negative_tags || [];
    const performers: any[] = data.performers || [];
    const negPerfs: any[] = data.negative_performers || [];
    const engStats = data.engagement_stats || {};
    const embedStats = data.embedding_stats || {};

    return React.createElement("div", { style: S.page },
      // Header
      React.createElement("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between" } },
        React.createElement("div", null,
          React.createElement("h1", { style: S.h1 }, "Taste Profile"),
          React.createElement("div", { style: S.subtitle },
            `Based on ${data.watched_scenes || 0} watched scenes`,
          ),
        ),
        React.createElement("div", { style: { display: "flex", gap: 8 } },
          React.createElement("button", {
            style: S.refreshBtn,
            onClick: doRefresh,
            disabled: loading,
          }, loading ? "Refreshing..." : "Refresh"),
          React.createElement("button", {
            style: { ...S.refreshBtn, background: "#1976d2", borderColor: "#1565c0", color: "#fff" },
            onClick: doRecompute,
            disabled: recomputing,
          }, recomputing ? "Recomputing..." : "Recompute"),
        ),
      ),

      // Stats row
      React.createElement("div", { style: S.statsRow },
        StatBox({ value: data.watched_scenes || 0, label: "Watched" }),
        StatBox({ value: data.liked_scenes || 0, label: "Liked", color: "#66bb6a" }),
        StatBox({ value: data.disliked_scenes || 0, label: "Disliked", color: "#ef5350" }),
        StatBox({ value: data.corpus_size || 0, label: "Corpus" }),
        StatBox({
          value: embedStats.coverage_pct != null ? `${embedStats.coverage_pct}%` : "—",
          label: "Embed Coverage",
          color: "#42a5f5",
        }),
      ),

      // Engagement distribution
      engStats.mean != null && React.createElement("div", { style: S.section },
        React.createElement("div", { style: S.sectionTitle }, "Engagement Distribution"),
        React.createElement("table", { style: S.engTable },
          React.createElement("thead", null,
            React.createElement("tr", null,
              ["Min", "P25", "Median", "Mean", "P75", "Max"].map((h) =>
                React.createElement("th", { style: S.engTh, key: h }, h),
              ),
            ),
          ),
          React.createElement("tbody", null,
            React.createElement("tr", null,
              [engStats.min, engStats.p25, engStats.median, engStats.mean, engStats.p75, engStats.max].map(
                (v: number, i: number) =>
                  React.createElement("td", { style: S.engTd, key: i }, v != null ? v.toFixed(4) : "—"),
              ),
            ),
          ),
        ),
      ),

      // Embedding coverage bar
      React.createElement("div", { style: { ...S.section, maxWidth: 500 } },
        React.createElement("div", { style: S.sectionTitle }, "Embedding Coverage"),
        React.createElement("div", { style: S.coverageBar },
          React.createElement("div", {
            style: {
              ...S.coverageFill,
              width: `${embedStats.coverage_pct || 0}%`,
              background: "linear-gradient(90deg, #1e88e5, #42a5f5)",
            },
          }),
          React.createElement("div", { style: S.coverageLabel },
            `${embedStats.scenes_with_visual || 0} / ${embedStats.total_watched || 0} scenes`,
          ),
        ),
      ),

      // Tag affinities
      tags.length > 0 && React.createElement("div", { style: S.section },
        React.createElement("div", { style: S.sectionTitle }, `Tag Affinities (${tags.length})`),
        React.createElement("div", { style: { fontSize: 11, color: "#666", marginBottom: 8 } },
          "Center line = neutral. Right = positive affinity, Left = negative.",
        ),
        tags.map((t: any) =>
          React.createElement(AffinityBar, {
            key: t.tag_id,
            name: t.tag_name,
            affinity: t.affinity,
            negWeight: t.negative_weight,
          }),
        ),
      ),

      // Negative tags (not already in positive list)
      negTags.length > 0 && React.createElement("div", { style: S.section },
        React.createElement("div", { style: S.sectionTitle }, `Negative Tags (${negTags.length})`),
        negTags.slice(0, 30).map((t: any) =>
          React.createElement(AffinityBar, {
            key: t.tag_id,
            name: t.tag_name,
            affinity: t.affinity,
            negWeight: t.negative_weight,
          }),
        ),
      ),

      // Performer affinities
      performers.length > 0 && React.createElement("div", { style: S.section },
        React.createElement("div", { style: S.sectionTitle }, `Performer Affinities (${performers.length})`),
        performers.map((p: any) =>
          React.createElement(PerformerBar, {
            key: p.performer_id,
            name: p.performer_name,
            affinity: p.affinity,
            negWeight: p.negative_weight,
          }),
        ),
      ),

      // Negative performers
      negPerfs.length > 0 && React.createElement("div", { style: S.section },
        React.createElement("div", { style: S.sectionTitle }, `Negative Performers (${negPerfs.length})`),
        negPerfs.slice(0, 20).map((p: any) =>
          React.createElement(PerformerBar, {
            key: p.performer_id,
            name: p.performer_name,
            affinity: p.affinity,
            negWeight: p.negative_weight,
          }),
        ),
      ),

      // Centroids summary — click to expand scene thumbnails
      centroids.length > 0 && React.createElement("div", { style: S.section },
        React.createElement("div", { style: S.sectionTitle }, `Embedding Centroids (${centroids.length})`),
        React.createElement("div", { style: { fontSize: 11, color: "#666", marginBottom: 8 } },
          "Click a centroid to view scenes closest to it in embedding space.",
        ),
        React.createElement("div", { style: { display: "flex", gap: 12, flexWrap: "wrap" as any, flexDirection: "column" as any } },
          centroids.map((c: any, i: number) => {
            const cKey = `${c.centroid_type}__${c.embedding_type}`;
            const isOpen = !!expandedCentroids[cKey];
            return React.createElement("div", {
              key: i,
              style: {
                background: "#1e1e24",
                borderRadius: 6,
                padding: "10px 14px",
                fontSize: 12,
                color: "#ccc",
                cursor: "pointer",
                border: isOpen ? "1px solid #555" : "1px solid transparent",
              },
            },
              React.createElement("div", {
                onClick: () => toggleCentroid(cKey, c.centroid_type, c.embedding_type),
                style: { display: "flex", alignItems: "center", justifyContent: "space-between" },
              },
                React.createElement("div", null,
                  React.createElement("div", {
                    style: { fontWeight: 700, color: c.centroid_type?.startsWith("liked") ? "#66bb6a" : "#ef5350", marginBottom: 4 },
                  }, `${c.centroid_type} — ${c.embedding_type}`),
                  React.createElement("div", null, `${c.dim}d, ${c.scene_count} scenes`),
                  c.computed_at && React.createElement("div", { style: { fontSize: 10, color: "#666", marginTop: 2 } },
                    new Date(c.computed_at).toLocaleString(),
                  ),
                ),
                React.createElement("span", { style: { fontSize: 16, color: "#888" } }, isOpen ? "▼" : "▶"),
              ),
              isOpen && React.createElement(SceneThumbnailGrid, {
                sceneIds: centroidScenes[cKey] || [],
                loading: !!centroidLoading[cKey],
              }),
            );
          }),
        ),
      ),

      // Content clusters — click to expand scene thumbnails
      clusters.length > 0 && React.createElement("div", { style: S.section },
        React.createElement("div", { style: { display: "flex", alignItems: "center", justifyContent: "space-between" } },
          React.createElement("div", { style: S.sectionTitle }, `Content Clusters (${clusters.length})`),
          React.createElement("button", {
            style: { ...S.refreshBtn, fontSize: 11 },
            onClick: doRecomputeClusters,
            disabled: recomputing,
          }, "Recompute Clusters"),
        ),
        React.createElement("div", { style: { fontSize: 11, color: "#666", marginBottom: 8 } },
          "Click a cluster to view its member scenes.",
        ),
        React.createElement("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 } },
          clusters.map((cl: any) => {
            const isOpen = !!expandedClusters[cl.id];
            return React.createElement("div", {
              key: cl.id,
              style: {
                background: "#1e1e24",
                borderRadius: 6,
                padding: "12px 14px",
                fontSize: 12,
                color: "#ccc",
                cursor: "pointer",
                border: isOpen ? "1px solid #555" : "1px solid transparent",
                gridColumn: isOpen ? "1 / -1" : undefined,
              },
            },
              React.createElement("div", {
                onClick: () => toggleCluster(cl.id),
                style: { display: "flex", alignItems: "center", justifyContent: "space-between" },
              },
                React.createElement("div", null,
                  React.createElement("div", { style: { fontWeight: 700, color: "#fff", marginBottom: 4, fontSize: 13 } }, cl.label),
                  React.createElement("div", null, `${cl.scene_count} scenes`),
                  cl.avg_engagement != null && React.createElement("div", null,
                    `Avg engagement: ${cl.avg_engagement.toFixed(3)}`,
                  ),
                ),
                React.createElement("span", { style: { fontSize: 16, color: "#888" } }, isOpen ? "▼" : "▶"),
              ),
              cl.top_tags && React.createElement("div", { style: { marginTop: 6, display: "flex", gap: 4, flexWrap: "wrap" as any } },
                (cl.top_tags || []).slice(0, 8).map((t: any, j: number) =>
                  React.createElement("span", {
                    key: j,
                    style: {
                      background: "#333",
                      borderRadius: 3,
                      padding: "2px 6px",
                      fontSize: 10,
                      color: "#aaa",
                    },
                  }, t.tag_name),
                ),
              ),
              isOpen && React.createElement(SceneThumbnailGrid, {
                sceneIds: clusterScenes[cl.id] || [],
                loading: !!clusterLoading[cl.id],
              }),
            );
          }),
        ),
      ),

      // Computation time
      data.computation_ms && React.createElement("div", { style: { fontSize: 11, color: "#555", marginTop: 16 } },
        `Profile computed in ${data.computation_ms}ms`,
        data.computed_at ? ` at ${new Date(data.computed_at).toLocaleString()}` : "",
      ),
    );
  }

  // Expose to window for route registration
  w.TasteProfile = TasteProfile;
})();
