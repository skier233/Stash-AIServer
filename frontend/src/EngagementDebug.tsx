/**
 * EngagementDebug — Debug panel that shows engagement scores, confidence,
 * signal breakdowns, and segment heatmaps for a scene.
 *
 * Injects into scene detail pages alongside the rating panel.  Intended as a
 * development/tuning aid (see engagement API endpoints in the backend).
 *
 * Registers: window.EngagementDebug
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[EngagementDebug] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useCallback } = React;

  const LOG = "[EngagementDebug]";
  const PANEL_ID = "ai-engagement-debug-panel";

  function getApiBase(): string {
    const fn = w.AIDefaultBackendBase;
    const base = fn ? fn() : "";
    return base ? `${base}/api/v1` : "";
  }

  // ---------- Types ----------

  interface SignalDetail {
    value: number | null;
    raw: any;
    available: boolean;
    reliability: number;
    source: string;
    weight: number;
    effective_contribution: number;
  }

  interface EngagementData {
    entity_id: number;
    entity_type: string;
    score: number;
    confidence: number;
    signal_count: number;
    total_possible: number;
    signals: Record<string, SignalDetail>;
  }

  interface SegmentData {
    start_s: number;
    end_s: number;
    score: number;
    watch_count: number;
    total_watch_s: number;
  }

  // ---------- Styles ----------

  const S = {
    container: {
      background: "#1a1a1e",
      border: "1px solid #333",
      borderRadius: "6px",
      padding: "12px 16px",
      marginTop: "8px",
      marginBottom: "8px",
      fontFamily: "monospace",
      fontSize: "12px",
    },
    header: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      marginBottom: "8px",
    },
    title: {
      fontSize: "13px",
      fontWeight: 600,
      color: "#aaa",
      textTransform: "uppercase" as any,
      letterSpacing: "0.5px",
    },
    toggleBtn: {
      background: "none",
      border: "none",
      color: "#888",
      cursor: "pointer",
      fontSize: "12px",
      padding: "2px 6px",
    },
    scoreRow: {
      display: "flex",
      gap: "24px",
      alignItems: "baseline",
      marginBottom: "10px",
      flexWrap: "wrap" as any,
    },
    bigNumber: {
      fontSize: "28px",
      fontWeight: 700,
      lineHeight: 1,
    },
    label: {
      fontSize: "10px",
      color: "#777",
      textTransform: "uppercase" as any,
      letterSpacing: "0.5px",
      marginBottom: "2px",
    },
    signalTable: {
      width: "100%",
      borderCollapse: "collapse" as any,
      marginTop: "8px",
    },
    th: {
      textAlign: "left" as any,
      color: "#888",
      fontSize: "10px",
      textTransform: "uppercase" as any,
      borderBottom: "1px solid #333",
      padding: "4px 8px",
    },
    td: {
      padding: "4px 8px",
      borderBottom: "1px solid #222",
      color: "#ccc",
    },
    bar: {
      height: "4px",
      borderRadius: "2px",
      background: "#333",
      position: "relative" as any,
      overflow: "hidden" as any,
      minWidth: "60px",
    },
    barFill: (pct: number, color: string) => ({
      position: "absolute" as any,
      top: 0,
      left: 0,
      height: "100%",
      width: `${Math.min(100, Math.max(0, pct))}%`,
      background: color,
      borderRadius: "2px",
      transition: "width 0.3s",
    }),
    heatmapRow: {
      display: "flex",
      gap: "1px",
      marginTop: "8px",
      height: "28px",
      alignItems: "flex-end" as any,
    },
    muted: {
      color: "#666",
      fontStyle: "italic" as any,
    },
  };

  // ---------- Helpers ----------

  function scoreColor(score: number): string {
    if (score >= 0.8) return "#4caf50";
    if (score >= 0.6) return "#8bc34a";
    if (score >= 0.4) return "#ffc107";
    if (score >= 0.2) return "#ff9800";
    return "#f44336";
  }

  function confidenceColor(conf: number): string {
    if (conf >= 0.7) return "#4caf50";
    if (conf >= 0.4) return "#ffc107";
    return "#f44336";
  }

  function formatDuration(seconds: number): string {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  // ---------- Signal Table ----------

  function SignalTable(props: { signals: Record<string, SignalDetail> }) {
    const { signals } = props;
    const entries = Object.entries(signals).sort((a: any, b: any) => {
      // Sort: available first (by effective_contribution desc), then unavailable
      if (a[1].available !== b[1].available) return a[1].available ? -1 : 1;
      return (b[1].effective_contribution || 0) - (a[1].effective_contribution || 0);
    });

    return React.createElement(
      "table",
      { style: S.signalTable },
      React.createElement(
        "thead",
        null,
        React.createElement(
          "tr",
          null,
          React.createElement("th", { style: S.th }, "Signal"),
          React.createElement("th", { style: S.th }, "Value"),
          React.createElement("th", { style: S.th }, "Raw"),
          React.createElement("th", { style: S.th }, "Reliability"),
          React.createElement("th", { style: S.th }, "Contribution"),
          React.createElement("th", { style: { ...S.th, minWidth: "80px" } }, "")
        )
      ),
      React.createElement(
        "tbody",
        null,
        entries.map(([name, sig]: [string, any]) => {
          const available = sig.available;
          const rowStyle = available ? {} : { opacity: 0.4 };
          return React.createElement(
            "tr",
            { key: name, style: rowStyle },
            React.createElement("td", { style: S.td }, name),
            React.createElement(
              "td",
              { style: S.td },
              available ? sig.value.toFixed(3) : "—"
            ),
            React.createElement(
              "td",
              { style: { ...S.td, fontSize: "11px", color: "#999" } },
              sig.raw != null ? String(sig.raw) : "—"
            ),
            React.createElement(
              "td",
              { style: S.td },
              sig.reliability.toFixed(2)
            ),
            React.createElement(
              "td",
              { style: { ...S.td, fontWeight: available ? 600 : 400 } },
              available ? sig.effective_contribution.toFixed(4) : "—"
            ),
            React.createElement(
              "td",
              { style: S.td },
              React.createElement(
                "div",
                { style: S.bar },
                available
                  ? React.createElement("div", {
                      style: S.barFill(sig.value * 100, scoreColor(sig.value)),
                    })
                  : null
              )
            )
          );
        })
      )
    );
  }

  // ---------- Segment Heatmap ----------

  function SegmentHeatmap(props: { segments: SegmentData[] }) {
    const { segments } = props;
    if (segments.length === 0) {
      return React.createElement("div", { style: S.muted }, "No segment data");
    }
    const maxScore = Math.max(...segments.map((s: SegmentData) => s.score), 0.01);

    return React.createElement(
      "div",
      null,
      React.createElement("div", { style: S.label }, "Segment Rewatch Heatmap"),
      React.createElement(
        "div",
        { style: S.heatmapRow },
        segments.map((seg: SegmentData, i: number) => {
          const height = Math.max(4, (seg.score / maxScore) * 28);
          const color = scoreColor(seg.score / maxScore);
          return React.createElement("div", {
            key: i,
            title: `${formatDuration(seg.start_s)}–${formatDuration(seg.end_s)}: score ${seg.score.toFixed(3)}, watched ${seg.watch_count}×`,
            style: {
              flex: 1,
              height: `${height}px`,
              background: color,
              borderRadius: "1px",
              opacity: 0.8,
              cursor: "default",
              transition: "height 0.3s",
            },
          });
        })
      ),
      // Time labels
      React.createElement(
        "div",
        {
          style: {
            display: "flex",
            justifyContent: "space-between",
            fontSize: "10px",
            color: "#555",
            marginTop: "2px",
          },
        },
        React.createElement("span", null, "0:00"),
        segments.length > 0
          ? React.createElement(
              "span",
              null,
              formatDuration(segments[segments.length - 1].end_s)
            )
          : null
      )
    );
  }

  // ---------- Main Component ----------

  function EngagementDebug(props: { sceneId: number }) {
    const { sceneId } = props;
    const [engagement, setEngagement] = useState(null as EngagementData | null);
    const [segments, setSegments] = useState([] as SegmentData[]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null as string | null);
    const [collapsed, setCollapsed] = useState(true); // collapsed by default
    const [showSignals, setShowSignals] = useState(false);

    useEffect(() => {
      let cancelled = false;
      setLoading(true);
      setError(null);

      const apiBase = getApiBase();
      if (!apiBase) {
        setLoading(false);
        return;
      }

      fetch(`${apiBase}/engagement/scenes/${sceneId}/detail?bin_size=10`)
        .then((res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return res.json();
        })
        .then((data) => {
          if (cancelled) return;
          setEngagement(data.engagement || null);
          setSegments(data.segments || []);
          setLoading(false);
        })
        .catch((e) => {
          if (cancelled) return;
          console.warn(LOG, "Failed to fetch engagement:", e);
          setError(e.message || "Unknown error");
          setLoading(false);
        });

      return () => {
        cancelled = true;
      };
    }, [sceneId]);

    if (loading) return null;
    if (error) {
      return React.createElement(
        "div",
        { style: { ...S.container, color: "#f44" } },
        "Engagement debug: " + error
      );
    }
    if (!engagement) return null;

    return React.createElement(
      "div",
      { style: S.container },
      // Header
      React.createElement(
        "div",
        { style: S.header },
        React.createElement("span", { style: S.title }, "⚡ Engagement Debug"),
        React.createElement(
          "button",
          {
            style: S.toggleBtn,
            onClick: () => setCollapsed((c: boolean) => !c),
          },
          collapsed ? "▶ Show" : "▼ Hide"
        )
      ),
      // Score summary (always visible)
      React.createElement(
        "div",
        { style: S.scoreRow },
        // Score
        React.createElement(
          "div",
          null,
          React.createElement("div", { style: S.label }, "Score"),
          React.createElement(
            "div",
            { style: { ...S.bigNumber, color: scoreColor(engagement.score) } },
            (engagement.score * 100).toFixed(1) + "%"
          )
        ),
        // Confidence
        React.createElement(
          "div",
          null,
          React.createElement("div", { style: S.label }, "Confidence"),
          React.createElement(
            "div",
            {
              style: {
                ...S.bigNumber,
                fontSize: "20px",
                color: confidenceColor(engagement.confidence),
              },
            },
            (engagement.confidence * 100).toFixed(0) + "%"
          )
        ),
        // Signal count
        React.createElement(
          "div",
          null,
          React.createElement("div", { style: S.label }, "Signals"),
          React.createElement(
            "div",
            { style: { fontSize: "16px", color: "#ccc" } },
            `${engagement.signal_count} / ${engagement.total_possible}`
          )
        )
      ),
      // Expanded details
      !collapsed &&
        React.createElement(
          "div",
          null,
          // Signal breakdown toggle
          React.createElement(
            "div",
            {
              style: {
                display: "flex",
                alignItems: "center",
                gap: "8px",
                marginBottom: "6px",
              },
            },
            React.createElement(
              "button",
              {
                style: {
                  ...S.toggleBtn,
                  border: "1px solid #444",
                  borderRadius: "4px",
                  padding: "3px 10px",
                },
                onClick: () => setShowSignals((s: boolean) => !s),
              },
              showSignals ? "Hide Signal Breakdown" : "Show Signal Breakdown"
            )
          ),
          // Signal table
          showSignals &&
            React.createElement(SignalTable, { signals: engagement.signals }),
          // Segment heatmap
          segments.length > 0 &&
            React.createElement(
              "div",
              { style: { marginTop: "12px" } },
              React.createElement(SegmentHeatmap, { segments })
            )
        )
    );
  }

  // ---------- Injection ----------

  let _currentSceneId: number | null = null;

  function isDetailTabActive(): boolean {
    // The scene detail tab pane has eventKey "scene-details-panel"
    const detailPane = document.querySelector(
      '.tab-pane[data-rb-event-key="scene-details-panel"]'
    ) as HTMLElement | null;
    if (detailPane) {
      return detailPane.classList.contains("active");
    }
    // Fallback: look for any active tab pane with scene-detail content
    const tabContainer = document.querySelector(".scene-tabs");
    if (!tabContainer) return true;
    const activePane = tabContainer.querySelector(".tab-pane.active") as HTMLElement | null;
    if (!activePane) return true;
    return !!(
      activePane.querySelector(".scene-details") ||
      activePane.querySelector(".performer-card") ||
      activePane.querySelector('[class*="detail"]')
    );
  }

  function getDetailPane(): HTMLElement | null {
    // Try the exact Stash eventKey first
    const exact = document.querySelector(
      '.tab-pane.active[data-rb-event-key="scene-details-panel"]'
    ) as HTMLElement | null;
    if (exact) return exact;
    // Fallback
    const tabContainer = document.querySelector(".scene-tabs");
    if (tabContainer) {
      const active = tabContainer.querySelector(".tab-pane.active") as HTMLElement | null;
      if (active) return active;
    }
    return null;
  }

  function injectPanel(sceneId: number, attempt = 0) {
    if (!sceneId) return;
    if (_currentSceneId === sceneId && document.getElementById(PANEL_ID)) return;
    _currentSceneId = sceneId;

    document.getElementById(PANEL_ID)?.remove();

    if (!isDetailTabActive()) return;

    // Insert at the bottom of the detail tab pane content
    const detailPane = getDetailPane();

    if (!detailPane) {
      if (attempt < 12) {
        setTimeout(() => injectPanel(sceneId, attempt + 1), 200 + attempt * 150);
      } else {
        console.warn(LOG, "No detail pane found after retries");
      }
      return;
    }

    const container = document.createElement("div");
    container.id = PANEL_ID;
    detailPane.appendChild(container);

    const ReactDOM = PluginApi.ReactDOM || (w as any).ReactDOM;
    if (ReactDOM?.createRoot) {
      ReactDOM.createRoot(container).render(React.createElement(EngagementDebug, { sceneId }));
    } else if (ReactDOM?.render) {
      ReactDOM.render(React.createElement(EngagementDebug, { sceneId }), container);
    }
  }

  let _observer: MutationObserver | null = null;
  function setupObserver() {
    if (_observer) return;
    _observer = new MutationObserver(() => {
      if (_currentSceneId && !document.getElementById(PANEL_ID) && isDetailTabActive()) {
        injectPanel(_currentSceneId);
      }
    });
    _observer.observe(document.body, { childList: true, subtree: true });
  }

  function setupPatch() {
    if (!PluginApi.patch?.after) {
      console.warn(LOG, "PluginApi.patch.after not available");
      return;
    }
    try {
      PluginApi.patch.after("ScenePage", function (...args: any[]) {
        const result = args[args.length - 1];
        const props = args[0];
        const sid = props?.scene?.id;
        if (sid) {
          const sceneId = parseInt(String(sid), 10);
          // Slightly longer delay to let SceneRatingPanel mount first
          setTimeout(() => injectPanel(sceneId, 0), 400);
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

  w.EngagementDebug = EngagementDebug;
  console.log(LOG, "Registered window.EngagementDebug");
})();
