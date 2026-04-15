/**
 * TrainingPage — Staging ground for explicit recommender training.
 *
 * Shows batches of scenes using the native Stash SceneCard component (same
 * layout as Recommended Scenes).  Users click through to the scene detail
 * page to watch and rate scenes using Stash's built-in rating system.
 * When they return (window re-focuses), the page re-checks which scenes
 * have been rated.
 *
 * Registers: window.TrainingPage
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[TrainingPage] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useRef, useMemo } = React;

  const LOG = "[TrainingPage]";

  // ---- backend helpers ----

  function getApiBase(): string {
    const fn = w.AIDefaultBackendBase;
    const base = typeof fn === "function" ? fn() : "";
    return base ? `${base}/api/v1` : "";
  }

  function withHeaders(init?: any): any {
    const helper = w.AISharedApiKeyHelper;
    if (helper && typeof helper.withHeaders === "function") {
      return helper.withHeaders(init || {});
    }
    const key = (w.AI_SHARED_API_KEY || "").trim();
    if (!key) return init || {};
    const headers = { ...(init?.headers || {}) };
    headers["x-ai-api-key"] = key;
    return { ...(init || {}), headers };
  }

  // ---- layout helpers (matches RecommendedScenes grid) ----

  function useDebounce(fn: any, delay: number) {
    const timeoutRef = useRef(null as any);
    return useMemo(
      () =>
        (...args: any[]) => {
          clearTimeout(timeoutRef.current);
          timeoutRef.current = setTimeout(() => fn(...args), delay);
        },
      [fn, delay],
    );
  }

  function useResizeObserver(target: any, callback: any) {
    useEffect(() => {
      if (!target.current || typeof ResizeObserver === "undefined") return;
      const ro = new ResizeObserver((entries: any) => {
        if (entries && entries.length > 0) callback(entries[0]);
      });
      ro.observe(target.current);
      return () => ro.disconnect();
    }, [target, callback]);
  }

  function calculateCardWidth(containerWidth: number, preferredWidth: number) {
    const root =
      typeof window !== "undefined"
        ? window.getComputedStyle(document.documentElement)
        : null;
    const containerPadding = root
      ? parseFloat(root.getPropertyValue("--ai-rec-container-padding"))
      : 30;
    const cardMargin = root
      ? parseFloat(root.getPropertyValue("--ai-rec-card-margin"))
      : 10;
    const maxUsable = containerWidth - containerPadding;
    const cols = Math.ceil(maxUsable / preferredWidth);
    return maxUsable / cols - cardMargin;
  }

  function useContainerDimensions(threshold = 20) {
    const target = useRef(null as any);
    const [dim, setDim] = useState({ width: 0, height: 0 });
    const debouncedSet = useDebounce((entry: any) => {
      if (!entry.contentBoxSize || !entry.contentBoxSize.length) return;
      const { inlineSize: width, blockSize: height } = entry.contentBoxSize[0];
      if (Math.abs(dim.width - width) > threshold) setDim({ width, height });
    }, 50);
    useResizeObserver(target, debouncedSet);
    useEffect(() => {
      if (target.current && dim.width === 0) {
        const rect = target.current.getBoundingClientRect();
        if (rect.width > 0) setDim({ width: rect.width, height: rect.height });
      }
    }, []);
    return [target, dim] as const;
  }

  function useCardWidth(
    containerWidth: number,
    zoomIndex: number,
    zoomWidths: number[],
  ) {
    return useMemo(() => {
      if (window.innerWidth <= 768) return undefined;
      const effective = containerWidth || 1200;
      if (zoomIndex < 0 || zoomIndex >= zoomWidths.length) return undefined;
      return calculateCardWidth(effective, zoomWidths[zoomIndex]);
    }, [containerWidth, zoomIndex, zoomWidths]);
  }

  // ---- normalize scene for SceneCard ----

  function normalizeScene(s: any) {
    if (!s || typeof s !== "object") return undefined;
    const arrayFields = [
      "performers", "tags", "markers", "scene_markers",
      "galleries", "images", "files", "groups",
    ];
    arrayFields.forEach((f) => {
      if (s[f] == null) s[f] = [];
      else if (!Array.isArray(s[f])) s[f] = [s[f]].filter(Boolean);
    });
    if (!s.studio) s.studio = null;
    if (s.rating100 == null && typeof s.rating === "number")
      s.rating100 = s.rating * 20;
    if (s.rating == null && typeof s.rating100 === "number")
      s.rating = Math.round(s.rating100 / 20);
    return s;
  }

  // ---- styles ----

  const S: Record<string, any> = {
    page: {
      padding: "24px 16px",
      fontFamily:
        "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
      color: "#e0e0e0",
    },
    header: {
      display: "flex",
      justifyContent: "space-between",
      alignItems: "flex-start",
      flexWrap: "wrap" as any,
      gap: 12,
      marginBottom: 16,
    },
    h1: { fontSize: 22, fontWeight: 700, margin: "0 0 4px", color: "#fff" },
    subtitle: { fontSize: 13, color: "#888", margin: 0 },
    statsBar: {
      display: "flex",
      gap: 10,
      flexWrap: "wrap" as any,
      marginBottom: 16,
    },
    statChip: {
      background: "#1e1e24",
      borderRadius: 6,
      padding: "8px 14px",
      fontSize: 12,
      color: "#ccc",
    },
    statNum: { fontWeight: 700, color: "#fff", marginRight: 4 },
    controls: {
      display: "flex",
      gap: 10,
      flexWrap: "wrap" as any,
      marginBottom: 16,
      alignItems: "center",
    },
    selectInput: {
      background: "#1a1a20",
      border: "1px solid #444",
      borderRadius: 4,
      color: "#ccc",
      padding: "6px 10px",
      fontSize: 12,
    },
    btnPrimary: {
      background: "#1976d2",
      border: "1px solid #1565c0",
      borderRadius: 4,
      color: "#fff",
      padding: "8px 16px",
      cursor: "pointer",
      fontSize: 12,
      fontWeight: 600,
    },
    btnFinalize: {
      background: "#388e3c",
      border: "1px solid #2e7d32",
      borderRadius: 4,
      color: "#fff",
      padding: "8px 16px",
      cursor: "pointer",
      fontSize: 12,
      fontWeight: 600,
    },
    progress: {
      height: 4,
      background: "#333",
      borderRadius: 2,
      overflow: "hidden",
    },
    progressFill: {
      height: "100%",
      borderRadius: 2,
      transition: "width 0.3s ease",
      background: "linear-gradient(90deg, #1976d2, #42a5f5)",
    },
    empty: { color: "#666", textAlign: "center" as any, padding: 40 },
    clusterSelect: {
      background: "#1a1a20",
      border: "1px solid #444",
      borderRadius: 4,
      color: "#ccc",
      padding: "6px 10px",
      fontSize: 12,
      maxWidth: 260,
    },
    batchInfo: {
      fontSize: 12,
      color: "#888",
      marginBottom: 16,
      display: "flex",
      gap: 16,
      alignItems: "center",
    },
  };

  // ---- main component ----

  function TrainingPage() {
    const [status, setStatus] = useState(null as any);
    const [scenes, setScenes] = useState([] as any[]);
    const [ratedIds, setRatedIds] = useState(new Set() as Set<number>);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null as string | null);
    const [strategy, setStrategy] = useState("mixed");
    const [clusterId, setClusterId] = useState(null as number | null);
    const [sessionCount, setSessionCount] = useState(0);
    const [finalizing, setFinalizing] = useState(false);
    const [batchSize, setBatchSize] = useState(24);
    const [excludeTags, setExcludeTags] = useState(
      () => {
        try { return JSON.parse(localStorage.getItem("aiTraining.excludeTags") || "[]"); } catch { return []; }
      }
    );
    const [tagInput, setTagInput] = useState("");
    const [allTags, setAllTags] = useState([] as string[]);
    const [showHelp, setShowHelp] = useState(false);
    // Use a ref to persist rated IDs across focus events that might
    // transiently return fewer results (e.g. timing issues).
    const ratedIdsRef = useRef(new Set() as Set<number>);

    // Layout (matches RecommendedScenes)
    const zoomWidths = [280, 340, 480, 640];
    const [zoomIndex, setZoomIndex] = useState(1);
    const [componentRef, { width: containerWidth }] = useContainerDimensions();
    const cardWidth = useCardWidth(containerWidth, zoomIndex, zoomWidths);

    // Load the native SceneCard component
    const componentsToLoad = [
      PluginApi.loadableComponents?.SceneCard,
    ].filter(Boolean);
    const componentsLoading = PluginApi.hooks?.useLoadComponents
      ? PluginApi.hooks.useLoadComponents(componentsToLoad)
      : false;
    const { SceneCard } = PluginApi.components || ({} as any);

    // Fetch training status on mount and after finalize
    useEffect(() => {
      const apiBase = getApiBase();
      if (!apiBase) return;
      fetch(`${apiBase}/training/status`, withHeaders())
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data) setStatus(data);
        })
        .catch((e) => console.warn(LOG, e));
    }, [sessionCount]);

    // Fetch all tag names once for the exclusion filter
    useEffect(() => {
      const apiBase = getApiBase();
      if (!apiBase) return;
      fetch(`${apiBase}/tags/names`, withHeaders())
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (data && Array.isArray(data.tags)) setAllTags(data.tags);
        })
        .catch(() => {});
    }, []);

    // When the window re-gains focus (user comes back from rating a scene),
    // re-check which scenes in the batch are now rated.
    // We merge new results with existing tracked IDs so we never lose
    // previously detected ratings (prevents the finalize button from vanishing).
    useEffect(() => {
      function onFocus() {
        if (scenes.length === 0) return;
        const apiBase = getApiBase();
        if (!apiBase) return;
        const ids = scenes.map((s: any) => s.id);
        fetch(
          `${apiBase}/training/check-rated`,
          withHeaders({
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ scene_ids: ids }),
          }),
        )
          .then((r) => (r.ok ? r.json() : null))
          .then((data) => {
            if (data && Array.isArray(data.rated_ids)) {
              const merged = new Set(ratedIdsRef.current);
              (data.rated_ids as number[]).forEach((id: number) => merged.add(id));
              ratedIdsRef.current = merged;
              setRatedIds(new Set(merged));
              // Also refresh status
              setSessionCount((c: number) => c + 0.001);
            }
          })
          .catch(() => {});
      }
      window.addEventListener("focus", onFocus);
      return () => window.removeEventListener("focus", onFocus);
    }, [scenes]);

    function fetchBatch() {
      const apiBase = getApiBase();
      if (!apiBase) {
        setError("Backend not configured");
        return;
      }
      setLoading(true);
      setError(null);
      const body: any = {
        strategy,
        batch_size: batchSize,
        exclude_rated: true,
        exclude_tag_names: excludeTags.length > 0 ? excludeTags : undefined,
      };
      if (strategy === "cluster" && clusterId !== null)
        body.cluster_id = clusterId;

      fetch(
        `${apiBase}/training/batch`,
        withHeaders({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
      )
        .then((r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          return r.json();
        })
        .then((data) => {
          const normalized = (data.scenes || [])
            .map(normalizeScene)
            .filter(Boolean);
          setScenes(normalized);
          const newRated = new Set((data.rated_ids || []) as number[]);
          ratedIdsRef.current = newRated;
          setRatedIds(newRated);
          setLoading(false);
        })
        .catch((e) => {
          setError(e.message);
          setLoading(false);
        });
    }

    function handleFinalize() {
      const apiBase = getApiBase();
      if (!apiBase) return;
      setFinalizing(true);
      fetch(`${apiBase}/training/finalize`, withHeaders({ method: "POST" }))
        .then((r) => r.json())
        .then(() => {
          setFinalizing(false);
          setSessionCount((c: number) => c + 1);
          setScenes([]);
          ratedIdsRef.current = new Set();
          setRatedIds(new Set());
        })
        .catch((e) => {
          console.warn(LOG, "finalize failed:", e);
          setFinalizing(false);
        });
    }

    const ratedCount = ratedIds.size;
    const totalInBatch = scenes.length;
    const progressPct =
      totalInBatch > 0 ? (ratedCount / totalInBatch) * 100 : 0;

    // Scene grid using native SceneCard (same as RecommendedScenes)
    // Intercept clicks on scene links to open in new tab so the user
    // doesn't lose the training page.
    const gridClickHandler = useMemo(() => {
      return (e: any) => {
        // Find the closest <a> element from the click target
        let target = e.target as HTMLElement;
        while (target && target.tagName !== "A" && target !== e.currentTarget) {
          target = target.parentElement as HTMLElement;
        }
        if (target && target.tagName === "A") {
          const href = target.getAttribute("href");
          if (href && href.startsWith("/scenes/")) {
            e.preventDefault();
            e.stopPropagation();
            window.open(href, "_blank");
          }
        }
      };
    }, []);

    const grid = useMemo(() => {
      if (loading || componentsLoading) {
        return React.createElement(
          "div",
          { style: { color: "#888", padding: 20, textAlign: "center" } },
          "Loading scenes...",
        );
      }
      if (!scenes.length) return null;
      if (cardWidth === undefined) {
        return React.createElement(
          "div",
          { style: { color: "#888", padding: 20, textAlign: "center" } },
          "Calculating layout...",
        );
      }

      const children = scenes.map((s: any, i: number) =>
        React.createElement(
          "div",
          { key: s.id + "_" + i, style: { display: "contents" } },
          SceneCard
            ? React.createElement(SceneCard, {
                scene: s,
                zoomIndex,
                queue: undefined,
                index: i,
              })
            : React.createElement(
                "a",
                {
                  href: `/scenes/${s.id}`,
                  style: {
                    color: "#ccc",
                    padding: 8,
                    display: "block",
                    textDecoration: "none",
                  },
                },
                s.title || `Scene ${s.id}`,
              ),
        ),
      );

      return React.createElement(
        "div",
        {
          className: "row ai-rec-grid d-flex flex-wrap justify-content-center",
          ref: componentRef,
          style: { ["--ai-card-width" as any]: cardWidth + "px" },
          onClick: gridClickHandler,
          onAuxClick: gridClickHandler,
        },
        children,
      );
    }, [
      loading,
      componentsLoading,
      scenes,
      SceneCard,
      cardWidth,
      zoomIndex,
    ]);

    return React.createElement(
      "div",
      { style: S.page },

      // Header
      React.createElement(
        "div",
        { style: S.header },
        React.createElement(
          "div",
          null,
          React.createElement("h1", { style: S.h1 }, "Recommender Training"),
          React.createElement(
            "p",
            { style: S.subtitle },
            "Scenes open in a new tab. Watch, rate, then come back — your progress auto-updates.",
          ),
        ),
        // Zoom slider + help toggle
        React.createElement(
          "div",
          { style: { display: "flex", alignItems: "center", gap: 12 } },
          React.createElement(
            "button",
            {
              style: {
                background: "none",
                border: "1px solid #555",
                borderRadius: 4,
                color: "#aaa",
                padding: "4px 10px",
                cursor: "pointer",
                fontSize: 11,
              },
              onClick: () => setShowHelp((h: boolean) => !h),
            },
            showHelp ? "Hide Help" : "How It Works",
          ),
          React.createElement(
            "span",
            { style: { fontSize: 11, color: "#888" } },
            "Zoom:",
          ),
          React.createElement("input", {
            type: "range",
            min: 0,
            max: zoomWidths.length - 1,
            value: zoomIndex,
            onChange: (e: any) => setZoomIndex(parseInt(e.target.value)),
            style: { width: 80 },
          }),
        ),
      ),

      // Help panel
      showHelp &&
        React.createElement(
          "div",
          {
            style: {
              background: "#1a1a24",
              border: "1px solid #333",
              borderRadius: 6,
              padding: "12px 16px",
              marginBottom: 16,
              fontSize: 12,
              color: "#bbb",
              lineHeight: 1.6,
            },
          },
          React.createElement("strong", { style: { color: "#fff" } }, "Workflow: "),
          "Load a batch → click scenes to open in new tabs → rate them using Stash's built-in star rating → come back here. ",
          "You don't need to rate every scene — even a few ratings help. ",
          React.createElement("br", null),
          React.createElement("strong", { style: { color: "#fff" } }, "Next Batch: "),
          "Just loads more scenes. Your ratings are saved automatically in Stash.",
          React.createElement("br", null),
          React.createElement("strong", { style: { color: "#fff" } }, "Finalize & Recompute: "),
          "Recalculates your taste profile and embedding centroids using all your ratings. ",
          "Click this when you've rated a good batch — it makes future recommendations better.",
        ),

      // Stats bar
      status &&
        React.createElement(
          "div",
          { style: S.statsBar },
          React.createElement(
            "div",
            { style: S.statChip },
            React.createElement(
              "span",
              { style: S.statNum },
              status.rated_count || 0,
            ),
            " rated",
          ),
          React.createElement(
            "div",
            { style: S.statChip },
            React.createElement(
              "span",
              { style: S.statNum },
              status.watched_count || 0,
            ),
            " watched",
          ),
          React.createElement(
            "div",
            { style: S.statChip },
            React.createElement(
              "span",
              { style: S.statNum },
              status.embedded_count || 0,
            ),
            " embedded",
          ),
          React.createElement(
            "div",
            { style: S.statChip },
            React.createElement(
              "span",
              { style: S.statNum },
              (status.clusters || []).length,
            ),
            " clusters",
          ),
        ),

      // Controls
      React.createElement(
        "div",
        { style: S.controls },
        React.createElement(
          "select",
          {
            style: S.selectInput,
            value: strategy,
            onChange: (e: any) => setStrategy(e.target.value),
          },
          React.createElement(
            "option",
            { value: "mixed" },
            "Mixed (balanced)",
          ),
          React.createElement("option", { value: "random" }, "Random"),
          React.createElement(
            "option",
            { value: "cluster" },
            "From cluster",
          ),
          React.createElement(
            "option",
            { value: "uncertain" },
            "Uncertain (system unsure)",
          ),
        ),

        strategy === "cluster" &&
          status?.clusters?.length > 0 &&
          React.createElement(
            "select",
            {
              style: S.clusterSelect,
              value: clusterId || "",
              onChange: (e: any) =>
                setClusterId(
                  e.target.value ? parseInt(e.target.value) : null,
                ),
            },
            React.createElement(
              "option",
              { value: "" },
              "Select cluster...",
            ),
            (status.clusters || []).map((c: any) =>
              React.createElement(
                "option",
                { key: c.id, value: c.id },
                `${c.label} (${c.scene_count} scenes)`,
              ),
            ),
          ),

        React.createElement(
          "select",
          {
            style: S.selectInput,
            value: batchSize,
            onChange: (e: any) => setBatchSize(parseInt(e.target.value)),
          },
          [12, 24, 36, 48].map((n) =>
            React.createElement(
              "option",
              { key: n, value: n },
              `${n} scenes`,
            ),
          ),
        ),

        React.createElement(
          "button",
          {
            style: S.btnPrimary,
            onClick: fetchBatch,
            disabled: loading,
          },
          loading
            ? "Loading..."
            : scenes.length > 0
              ? "Next Batch"
              : "Load Training Batch",
        ),

        scenes.length > 0 &&
          React.createElement(
            "button",
            {
              style: {
                ...S.btnFinalize,
                opacity: ratedCount > 0 ? 1 : 0.5,
              },
              onClick: handleFinalize,
              disabled: finalizing || ratedCount === 0,
            },
            finalizing
              ? "Recomputing..."
              : ratedCount > 0
                ? `Finalize & Recompute (${ratedCount} rated)`
                : "Finalize (rate some scenes first)",
          ),
      ),

      // Tag exclusion filter
      React.createElement(
        "div",
        {
          style: {
            display: "flex",
            gap: 8,
            flexWrap: "wrap" as any,
            alignItems: "center",
            marginBottom: 12,
          },
        },
        React.createElement(
          "span",
          { style: { fontSize: 11, color: "#888" } },
          "Exclude tags:",
        ),
        excludeTags.map((tag: string) =>
          React.createElement(
            "span",
            {
              key: tag,
              style: {
                background: "#3a1a1a",
                border: "1px solid #633",
                borderRadius: 12,
                padding: "2px 8px 2px 10px",
                fontSize: 11,
                color: "#e88",
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              },
            },
            tag,
            React.createElement(
              "span",
              {
                style: { cursor: "pointer", fontWeight: 700 },
                onClick: () => {
                  const next = excludeTags.filter((t: string) => t !== tag);
                  setExcludeTags(next);
                  localStorage.setItem("aiTraining.excludeTags", JSON.stringify(next));
                },
              },
              "\u00d7",
            ),
          ),
        ),
        React.createElement("input", {
          type: "text",
          placeholder: "Type tag name...",
          value: tagInput,
          onChange: (e: any) => setTagInput(e.target.value),
          onKeyDown: (e: any) => {
            if (e.key === "Enter" && tagInput.trim()) {
              const tag = tagInput.trim();
              if (!excludeTags.includes(tag)) {
                const next = [...excludeTags, tag];
                setExcludeTags(next);
                localStorage.setItem("aiTraining.excludeTags", JSON.stringify(next));
              }
              setTagInput("");
            }
          },
          list: "training-tag-suggestions",
          style: {
            ...S.selectInput,
            minWidth: 140,
            flex: "0 1 200px",
          },
        }),
        allTags.length > 0 &&
          React.createElement(
            "datalist",
            { id: "training-tag-suggestions" },
            allTags
              .filter(
                (t: string) =>
                  !excludeTags.includes(t) &&
                  t.toLowerCase().includes(tagInput.toLowerCase()),
              )
              .slice(0, 30)
              .map((t: string) =>
                React.createElement("option", { key: t, value: t }),
              ),
          ),
      ),

      // Progress bar and batch info
      scenes.length > 0 &&
        React.createElement(
          "div",
          { style: S.batchInfo },
          React.createElement(
            "span",
            null,
            `${ratedCount} / ${totalInBatch} rated in this batch`,
          ),
          React.createElement(
            "div",
            { style: { ...S.progress, flex: 1 } },
            React.createElement("div", {
              style: { ...S.progressFill, width: `${progressPct}%` },
            }),
          ),
        ),

      // Error
      error &&
        React.createElement(
          "div",
          { style: { color: "#ef5350", marginBottom: 12 } },
          `Error: ${error}`,
        ),

      // Scene grid (native SceneCard)
      grid
        ? grid
        : !loading &&
          React.createElement(
            "div",
            { style: S.empty },
            'Click "Load Training Batch" to get scenes. Each scene opens in a new tab — watch it and rate it using Stash\'s star rating. Come back here to see your progress and load more.',
          ),
    );
  }

  w.TrainingPage = TrainingPage;
})();
