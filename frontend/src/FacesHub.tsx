/**
 * FacesHub — Full page grid for managing face clusters.
 *
 * Route: /plugins/ai-faces
 * Provides: grid + filters + PerformerLinkDialog + MergeDialog
 *
 * Registers: window.FacesHub
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[FacesHub] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useCallback, useRef } = React;

  // ---------- Theme ----------

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

  // ---------- Helpers ----------

  function getApiBase(): string {
    const fn = w.AIDefaultBackendBase;
    const base = fn ? fn() : "";
    return base ? `${base}/api/v1/plugins/skier_aitagging` : "";
  }

  function getPerformerImageUrl(imgPath: string): string {
    if (!imgPath) return "";
    if (imgPath.startsWith("http")) return imgPath;
    return imgPath;
  }

  // ---------- PerformerLinkDialog ----------

  function PerformerLinkDialog(props: {
    cluster: any;
    apiBase: string;
    onClose: () => void;
    onLinked: () => void;
  }) {
    const { cluster, apiBase, onClose, onLinked } = props;
    const hasStashdbMatch = Boolean(cluster.stashdb_match_id || cluster.stashdb_match?.ref_id);
    const [suggestions, setSuggestions] = useState([] as any[]);
    const [selectedPerformer, setSelectedPerformer] = useState(null as any);
    const [loading, setLoading] = useState(true);
    const [linking, setLinking] = useState(false);
    const [setPerformerImage, setSetPerformerImage] = useState(false);
    const [hydrateFromStashdb, setHydrateFromStashdb] = useState(true);
    const [thumbIdx, setThumbIdx] = useState(0);
    const [thumbCount, setThumbCount] = useState(1);

    useEffect(() => {
      let cancelled = false;
      (async () => {
        try {
          const res = await fetch(`${apiBase}/faces/clusters/${cluster.id}/suggested-performers`);
          if (res.ok) {
            const data = await res.json();
            if (!cancelled) {
              setSuggestions(data.suggestions || []);
              if (data.suggestions?.length > 0) {
                setSelectedPerformer(data.suggestions[0]);
              }
            }
          }
        } catch (e) {
          console.error("[FacesHub] Failed to load suggestions:", e);
        }
        if (!cancelled) setLoading(false);
      })();
      return () => { cancelled = true; };
    }, [apiBase, cluster.id]);

    useEffect(() => {
      (async () => {
        try {
          const res = await fetch(`${apiBase}/faces/clusters/${cluster.id}/thumbnail-count`);
          if (res.ok) {
            const data = await res.json();
            if (data.count > 1) setThumbCount(data.count);
          }
        } catch (_e) {}
      })();
    }, [apiBase, cluster.id]);

    const isStashdbOnly = selectedPerformer?.confidence === "stashdb_only";

    const handleConfirm = useCallback(async () => {
      if (!selectedPerformer) return;
      setLinking(true);
      try {
        let performerId = selectedPerformer.performer_id || selectedPerformer.id;

        // StashDB-only suggestion: create the performer first, then link
        if (!performerId && selectedPerformer.confidence === "stashdb_only") {
          const createRes = await fetch(`${apiBase}/faces/clusters/${cluster.id}/create-performer`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: selectedPerformer.performer_name || selectedPerformer.stashdb_match?.name || `Unidentified #${cluster.id}`,
              set_performer_image: setPerformerImage,
              thumbnail_index: setPerformerImage ? thumbIdx : undefined,
            }),
          });
          if (!createRes.ok) {
            console.error("[FacesHub] Create performer failed:", await createRes.text());
            setLinking(false);
            return;
          }
          const created = await createRes.json();
          performerId = created.performer_id;
          // The create-performer endpoint already links, so we're done —
          // but we still want to hydrate from StashDB if the checkbox is on.
          if (hydrateFromStashdb) {
            // Re-link to trigger hydration path
            await fetch(`${apiBase}/faces/clusters/${cluster.id}/link`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                performer_id: performerId,
                performer_name: selectedPerformer.performer_name,
                set_performer_image: false,
                hydrate_from_stashdb: true,
              }),
            });
          }
          onLinked();
          setLinking(false);
          return;
        }

        const res = await fetch(`${apiBase}/faces/clusters/${cluster.id}/link`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            performer_id: performerId,
            performer_name: selectedPerformer.performer_name || selectedPerformer.name || null,
            set_performer_image: setPerformerImage,
            thumbnail_index: setPerformerImage ? thumbIdx : undefined,
            hydrate_from_stashdb: hydrateFromStashdb,
          }),
        });
        if (res.ok) onLinked();
      } catch (e) {
        console.error("[FacesHub] Link failed:", e);
      }
      setLinking(false);
    }, [apiBase, cluster.id, selectedPerformer, setPerformerImage, thumbIdx, hydrateFromStashdb, onLinked]);

    // Modal overlay
    return React.createElement(
      "div",
      {
        style: {
          position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
          background: "rgba(0,0,0,0.7)", zIndex: 21000,
          display: "flex", alignItems: "center", justifyContent: "center",
        },
        onClick: (e: any) => { if (e.target === e.currentTarget) onClose(); },
      },
      React.createElement(
        "div",
        {
          style: {
            background: THEME.bg, borderRadius: "8px", maxWidth: "720px", width: "90vw",
            maxHeight: "80vh", display: "flex", flexDirection: "column",
            border: `1px solid ${THEME.borderAccent}`,
            boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
          },
        },
        // Header
        React.createElement(
          "div",
          {
            style: {
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "14px 16px", borderBottom: `1px solid ${THEME.border}`,
            },
          },
          React.createElement("h3", { style: { margin: 0, color: THEME.text, fontSize: "16px" } }, "Link Face to Performer"),
          React.createElement("button", {
            style: { background: "transparent", border: "none", color: THEME.textMuted, fontSize: "20px", cursor: "pointer" },
            onClick: onClose,
          }, "\u00d7")
        ),
        // Body: Side-by-side layout
        React.createElement(
          "div",
          {
            style: { flex: 1, overflowY: "auto", padding: "16px", display: "flex", gap: "16px", alignItems: "flex-start" },
          },
          // LEFT: Face crop
          React.createElement(
            "div",
            { style: { textAlign: "center", flexShrink: 0 } },
            React.createElement("div", { style: { position: "relative", width: 150, height: 150 } },
              React.createElement("img", {
                src: `${apiBase}/faces/clusters/${cluster.id}/thumbnail?size=180&index=${thumbIdx}&pad=0.2`,
                style: { width: 150, height: 150, borderRadius: "8px", objectFit: "cover", border: `2px solid ${THEME.border}` },
              }),
              thumbCount > 1
                ? React.createElement("div", {
                    style: {
                      position: "absolute", bottom: 0, left: 0, right: 0,
                      display: "flex", alignItems: "center", justifyContent: "space-between",
                      padding: "4px 6px",
                      background: "linear-gradient(transparent, rgba(0,0,0,0.6))",
                      borderRadius: "0 0 8px 8px",
                    },
                  },
                    React.createElement("button", {
                      style: {
                        background: "rgba(0,0,0,0.6)", border: "none", color: "#fff",
                        borderRadius: "50%", width: 22, height: 22, cursor: "pointer",
                        fontSize: "10px", display: "flex", alignItems: "center", justifyContent: "center", padding: 0,
                      },
                      onClick: () => setThumbIdx((thumbIdx - 1 + thumbCount) % thumbCount),
                    }, "\u25C0"),
                    React.createElement("span", { style: { color: "#fff", fontSize: "10px" } }, `${thumbIdx + 1}/${thumbCount}`),
                    React.createElement("button", {
                      style: {
                        background: "rgba(0,0,0,0.6)", border: "none", color: "#fff",
                        borderRadius: "50%", width: 22, height: 22, cursor: "pointer",
                        fontSize: "10px", display: "flex", alignItems: "center", justifyContent: "center", padding: 0,
                      },
                      onClick: () => setThumbIdx((thumbIdx + 1) % thumbCount),
                    }, "\u25B6")
                  )
                : null
            ),
            React.createElement("div", { style: { color: THEME.textMuted, fontSize: "11px", marginTop: "4px" } },
              `${cluster.scene_count || 0} scenes, ${cluster.image_count || 0} images`
            )
          ),
          // CENTER: Suggestions + search
          React.createElement(
            "div",
            { style: { flex: 1, minWidth: 0 } },
            React.createElement("div", { style: { color: THEME.textMuted, fontSize: "12px", marginBottom: "8px" } }, "Suggestions:"),
            loading
              ? React.createElement("div", { style: { color: THEME.textMuted } }, "Loading...")
              : suggestions.length > 0
              ? suggestions.map((s: any, i: number) => {
                  const isStashdb = s.confidence === "stashdb" || s.confidence === "stashdb_only" || s.confidence === "stashdb_name" || s.confidence === "stashdb_scene";
                  const sKey = s.performer_id ?? `stashdb-${s.stashdb_match?.stashdb_id ?? i}`;
                  const isSelected = selectedPerformer === s
                    || (s.performer_id && selectedPerformer?.performer_id === s.performer_id);
                  return React.createElement(
                    "div",
                    {
                      key: sKey,
                      style: {
                        padding: "8px",
                        background: isSelected ? THEME.bgHover : "transparent",
                        borderRadius: "4px",
                        cursor: "pointer",
                        marginBottom: "4px",
                        border: isSelected ? `1px solid ${THEME.borderAccent}` : "1px solid transparent",
                      },
                      onClick: () => setSelectedPerformer(s),
                    },
                    React.createElement("div", {
                      style: {
                        color: isStashdb ? "#7ec8ff" : THEME.text,
                        fontWeight: isStashdb ? 600 : 500,
                      },
                    }, isStashdb ? `\u2B50 ${s.performer_name}` : s.performer_name),
                    React.createElement("div", { style: { color: THEME.textMuted, fontSize: "11px" } },
                      isStashdb && s.stashdb_match
                        ? `StashDB match (${Math.round((s.stashdb_match.similarity || 0) * 100)}%)` +
                          (s.confidence === "stashdb_scene"
                            ? ` + in ${[s.scene_count ? `${s.scene_count} scene${s.scene_count > 1 ? "s" : ""}` : null, s.image_count ? `${s.image_count} image${s.image_count > 1 ? "s" : ""}` : null].filter(Boolean).join(", ")}`
                            : "") +
                          (s.confidence === "stashdb_only" ? " \u2014 No local performer" : "") +
                          (s.confidence === "stashdb_name" ? " \u2014 name match" : "")
                        : [
                            s.scene_count ? `${s.scene_count} scenes` : null,
                            s.image_count ? `${s.image_count} images` : null,
                            s.solo_scene_count ? `solo: ${s.solo_scene_count}s` : null,
                          ].filter(Boolean).join(", ") + (s.confidence ? ` [${s.confidence.toUpperCase()}]` : "")
                    ),
                    s.stashdb_match?.disambiguation
                      ? React.createElement("div", { style: { color: THEME.textMuted, fontSize: "10px", fontStyle: "italic" } },
                          s.stashdb_match.disambiguation)
                      : null
                  );
                })
              : React.createElement("div", { style: { color: THEME.textMuted, marginBottom: "8px" } }, "No suggestions found"),
            // Inline PerformerSearch
            React.createElement("div", { style: { marginTop: "8px", borderTop: `1px solid ${THEME.border}`, paddingTop: "8px" } },
              React.createElement("div", { style: { color: THEME.textMuted, fontSize: "12px", marginBottom: "4px" } }, "Or search:"),
              w.PerformerSearchWidget
                ? React.createElement(w.PerformerSearchWidget, {
                    onSelect: (p: any) => setSelectedPerformer({ performer_id: p.id, performer_name: p.name, performer_image: p.image_path }),
                    onHover: () => {},
                    suggestedPerformers: [],
                    placeholder: "Search performers...",
                  })
                : React.createElement("div", { style: { color: THEME.textMuted } }, "Search unavailable")
            )
          ),
          // RIGHT: Selected performer image
          React.createElement(
            "div",
            { style: { textAlign: "center", flexShrink: 0 } },
            selectedPerformer
              ? React.createElement(React.Fragment, null,
                  React.createElement("img", {
                    src: getPerformerImageUrl(
                      selectedPerformer.stashdb_image_url
                      || selectedPerformer.performer_image || selectedPerformer.image_path
                      || (selectedPerformer.stashdb_match?.image_url)
                      || ""
                    ),
                    style: { width: 150, height: 150, borderRadius: "8px", objectFit: "contain", background: THEME.bg, border: `2px solid ${THEME.borderAccent}` },
                    onError: (e: any) => {
                      // Replace broken image with a placeholder div
                      const el = e.target as HTMLImageElement;
                      const parent = el.parentElement;
                      if (parent) {
                        const ph = document.createElement("div");
                        ph.style.cssText = `width:150px;height:150px;border-radius:8px;background:${THEME.border};display:flex;align-items:center;justify-content:center;color:${THEME.textMuted};font-size:11px;border:2px solid ${THEME.borderAccent}`;
                        ph.textContent = "No image available";
                        parent.replaceChild(ph, el);
                      }
                    },
                  }),
                )
              : React.createElement("div", {
                  style: {
                    width: 150, height: 150, borderRadius: "8px", background: THEME.border,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    color: THEME.textMuted, fontSize: "12px",
                  },
                }, "Select a performer"),
            selectedPerformer
              ? React.createElement("div", { style: { color: THEME.text, fontSize: "12px", marginTop: "4px" } }, selectedPerformer.performer_name || selectedPerformer.name)
              : null
          )
        ),
        // Footer
        // Footer
        React.createElement(
          "div",
          {
            style: {
              display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px",
              padding: "12px 16px", borderTop: `1px solid ${THEME.border}`,
            },
          },
          // "Set as performer image" checkbox
          React.createElement("label", {
            style: { display: "flex", alignItems: "center", gap: "6px", cursor: "pointer", fontSize: "12px", color: THEME.textMuted },
          },
            React.createElement("input", {
              type: "checkbox",
              checked: setPerformerImage,
              onChange: (e: any) => setSetPerformerImage(e.target.checked),
              style: { cursor: "pointer" },
            }),
            "Use face as performer image"
          ),
          hasStashdbMatch
            ? React.createElement("label", {
                style: { display: "flex", alignItems: "center", gap: "6px", cursor: "pointer", fontSize: "12px", color: THEME.textMuted },
              },
                React.createElement("input", {
                  type: "checkbox",
                  checked: hydrateFromStashdb,
                  onChange: (e: any) => setHydrateFromStashdb(e.target.checked),
                  style: { cursor: "pointer" },
                }),
                "Update performer from StashDB match"
              )
            : React.createElement("span", null),
          React.createElement("div", { style: { display: "flex", gap: "8px" } },
            React.createElement("button", {
              style: {
                padding: "6px 16px", background: "transparent", color: THEME.text,
                border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer",
              },
              onClick: onClose,
            }, "Cancel"),
            React.createElement("button", {
              style: {
                padding: "6px 16px", background: selectedPerformer ? THEME.accent : THEME.border,
                color: "#fff", border: "none", borderRadius: "4px",
                cursor: selectedPerformer ? "pointer" : "not-allowed", opacity: linking ? 0.6 : 1,
              },
              onClick: handleConfirm,
              disabled: !selectedPerformer || linking,
            }, linking ? "Working..." : isStashdbOnly ? "Create & Link" : "Confirm Link")
          )
        )
      )
    );
  }

  // ---------- MergeDialog ----------

  function MergeDialog(props: {
    cluster: any;
    apiBase: string;
    onClose: () => void;
    onMerged: () => void;
  }) {
    const { cluster, apiBase, onClose, onMerged } = props;
    const [targets, setTargets] = useState([] as any[]);
    const [selectedTarget, setSelectedTarget] = useState(null as any);
    const [loading, setLoading] = useState(true);
    const [merging, setMerging] = useState(false);

    useEffect(() => {
      let cancelled = false;
      (async () => {
        try {
          const res = await fetch(`${apiBase}/faces/clusters?status=unidentified&per_page=100`);
          if (res.ok) {
            const data = await res.json();
            const eligible = (data.clusters || []).filter((c: any) => c.id !== cluster.id);
            if (!cancelled) setTargets(eligible);
          }
        } catch (e) {
          console.error("[FacesHub] Failed to load merge targets:", e);
        }
        if (!cancelled) setLoading(false);
      })();
      return () => { cancelled = true; };
    }, [apiBase, cluster.id]);

    const handleMerge = useCallback(async () => {
      if (!selectedTarget) return;
      setMerging(true);
      try {
        const res = await fetch(`${apiBase}/faces/clusters/merge`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ source_cluster_id: cluster.id, target_cluster_id: selectedTarget.id }),
        });
        if (res.ok) onMerged();
      } catch (e) {
        console.error("[FacesHub] Merge failed:", e);
      }
      setMerging(false);
    }, [apiBase, cluster.id, selectedTarget, onMerged]);

    return React.createElement(
      "div",
      {
        style: {
          position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
          background: "rgba(0,0,0,0.7)", zIndex: 21000,
          display: "flex", alignItems: "center", justifyContent: "center",
        },
        onClick: (e: any) => { if (e.target === e.currentTarget) onClose(); },
      },
      React.createElement(
        "div",
        {
          style: {
            background: THEME.bg, borderRadius: "8px", maxWidth: "560px", width: "90vw",
            maxHeight: "80vh", display: "flex", flexDirection: "column",
            border: `1px solid ${THEME.borderAccent}`,
            boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
          },
        },
        // Header
        React.createElement(
          "div",
          {
            style: {
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "14px 16px", borderBottom: `1px solid ${THEME.border}`,
            },
          },
          React.createElement("h3", { style: { margin: 0, color: THEME.text, fontSize: "16px" } }, "Merge Faces"),
          React.createElement("button", {
            style: { background: "transparent", border: "none", color: THEME.textMuted, fontSize: "20px", cursor: "pointer" },
            onClick: onClose,
          }, "\u00d7")
        ),
        // Body
        React.createElement(
          "div",
          { style: { flex: 1, overflowY: "auto", padding: "16px" } },
          // Side-by-side thumbnails
          React.createElement(
            "div",
            { style: { display: "flex", alignItems: "center", justifyContent: "center", gap: "24px", marginBottom: "16px" } },
            // Source
            React.createElement(
              "div",
              { style: { textAlign: "center" } },
              React.createElement("img", {
                src: `${apiBase}/faces/clusters/${cluster.id}/thumbnail`,
                style: { width: 100, height: 100, borderRadius: "8px", objectFit: "cover", border: `2px solid ${THEME.border}` },
              }),
              React.createElement("div", { style: { color: THEME.textMuted, fontSize: "11px", marginTop: "4px" } },
                cluster.label || `Unknown #${cluster.id}`
              )
            ),
            React.createElement("span", { style: { color: THEME.textMuted, fontSize: "24px" } }, "\u2192"),
            // Target
            selectedTarget
              ? React.createElement(
                  "div",
                  { style: { textAlign: "center" } },
                  React.createElement("img", {
                    src: `${apiBase}/faces/clusters/${selectedTarget.id}/thumbnail`,
                    style: { width: 100, height: 100, borderRadius: "8px", objectFit: "cover", border: `2px solid ${THEME.borderAccent}` },
                  }),
                  React.createElement("div", { style: { color: THEME.textMuted, fontSize: "11px", marginTop: "4px" } },
                    selectedTarget.label || `Unknown #${selectedTarget.id}`
                  )
                )
              : React.createElement("div", {
                  style: {
                    width: 100, height: 100, borderRadius: "8px", background: THEME.border,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    color: THEME.textMuted, fontSize: "11px", textAlign: "center" as any,
                  },
                }, "Select target")
          ),
          // Target selector
          React.createElement("div", { style: { color: THEME.textMuted, fontSize: "12px", marginBottom: "6px" } }, "Select target face:"),
          loading
            ? React.createElement("div", { style: { color: THEME.textMuted } }, "Loading faces...")
            : React.createElement(
                "select",
                {
                  style: {
                    width: "100%", padding: "8px", background: THEME.bgInput, color: THEME.text,
                    border: `1px solid ${THEME.border}`, borderRadius: "4px",
                  },
                  value: selectedTarget?.id || "",
                  onChange: (e: any) => {
                    const tid = parseInt(e.target.value, 10);
                    setSelectedTarget(targets.find((t: any) => t.id === tid) || null);
                  },
                },
                React.createElement("option", { value: "" }, "-- Select a face --"),
                ...targets.map((t: any) =>
                  React.createElement("option", { key: t.id, value: t.id },
                    `${t.label || `Unknown #${t.id}`} (${t.scene_count || 0}s, ${t.image_count || 0}i, quality: ${(t.quality_score || 0).toFixed(2)})`)
                )
              )
        ),
        // Footer
        React.createElement(
          "div",
          {
            style: {
              display: "flex", justifyContent: "flex-end", gap: "8px",
              padding: "12px 16px", borderTop: `1px solid ${THEME.border}`,
            },
          },
          React.createElement("button", {
            style: {
              padding: "6px 16px", background: "transparent", color: THEME.text,
              border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer",
            },
            onClick: onClose,
          }, "Cancel"),
          React.createElement("button", {
            style: {
              padding: "6px 16px", background: selectedTarget ? THEME.warning : THEME.border,
              color: "#fff", border: "none", borderRadius: "4px",
              cursor: selectedTarget ? "pointer" : "not-allowed", opacity: merging ? 0.6 : 1,
            },
            onClick: handleMerge,
            disabled: !selectedTarget || merging,
          }, merging ? "Merging..." : "Confirm Merge")
        )
      )
    );
  }

  // ---------- CreatePerformerDialog ----------

  function CreatePerformerDialog(props: {
    cluster: any;
    apiBase: string;
    onClose: () => void;
    onCreated: () => void;
  }) {
    const { cluster, apiBase, onClose, onCreated } = props;
    const [name, setName] = useState(`Unidentified #${cluster.id}`);
    const [setImage, setSetImage] = useState(true);
    const [creating, setCreating] = useState(false);
    const [thumbIdx, setThumbIdx] = useState(0);
    const [thumbCount, setThumbCount] = useState(1);

    // Fetch thumbnail count on mount
    useEffect(() => {
      (async () => {
        try {
          const res = await fetch(`${apiBase}/faces/clusters/${cluster.id}/thumbnail-count`);
          if (res.ok) {
            const data = await res.json();
            if (data.count > 1) setThumbCount(data.count);
          }
        } catch (_e) {}
      })();
    }, [apiBase, cluster.id]);

    const handleCreate = useCallback(async () => {
      setCreating(true);
      try {
        const res = await fetch(`${apiBase}/faces/clusters/${cluster.id}/create-performer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: name.trim() || `Unidentified #${cluster.id}`,
            set_performer_image: setImage,
            thumbnail_index: setImage ? thumbIdx : undefined,
          }),
        });
        if (res.ok) onCreated();
        else console.error("[FacesHub] Create performer failed:", await res.text());
      } catch (e) {
        console.error("[FacesHub] Create performer failed:", e);
      }
      setCreating(false);
    }, [apiBase, cluster.id, name, setImage, thumbIdx, onCreated]);

    return React.createElement(
      "div",
      {
        style: {
          position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
          background: "rgba(0,0,0,0.7)", zIndex: 21000,
          display: "flex", alignItems: "center", justifyContent: "center",
        },
        onClick: (e: any) => { if (e.target === e.currentTarget) onClose(); },
      },
      React.createElement(
        "div",
        {
          style: {
            background: THEME.bg, borderRadius: "8px", maxWidth: "480px", width: "90vw",
            display: "flex", flexDirection: "column",
            border: `1px solid ${THEME.borderAccent}`,
            boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
          },
        },
        // Header
        React.createElement(
          "div",
          {
            style: {
              display: "flex", justifyContent: "space-between", alignItems: "center",
              padding: "14px 16px", borderBottom: `1px solid ${THEME.border}`,
            },
          },
          React.createElement("h3", { style: { margin: 0, color: THEME.text, fontSize: "16px" } }, "Create New Performer"),
          React.createElement("button", {
            style: { background: "transparent", border: "none", color: THEME.textMuted, fontSize: "20px", cursor: "pointer" },
            onClick: onClose,
          }, "\u00d7")
        ),
        // Body
        React.createElement(
          "div",
          { style: { padding: "16px", display: "flex", gap: "16px", alignItems: "flex-start" } },
          // Face thumbnail with cycling arrows
          React.createElement(
            "div",
            { style: { position: "relative", flexShrink: 0, width: 100, height: 100 } },
            React.createElement("img", {
              src: `${apiBase}/faces/clusters/${cluster.id}/thumbnail?size=120&index=${thumbIdx}&pad=0.2`,
              style: { width: 100, height: 100, borderRadius: "8px", objectFit: "cover", border: `2px solid ${THEME.border}` },
            }),
            thumbCount > 1
              ? React.createElement("div", {
                  style: {
                    position: "absolute", bottom: 0, left: 0, right: 0,
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: "2px 4px",
                    background: "linear-gradient(transparent, rgba(0,0,0,0.6))",
                    borderRadius: "0 0 8px 8px",
                  },
                },
                  React.createElement("button", {
                    style: {
                      background: "rgba(0,0,0,0.6)", border: "none", color: "#fff",
                      borderRadius: "50%", width: 20, height: 20, cursor: "pointer",
                      fontSize: "10px", display: "flex", alignItems: "center", justifyContent: "center", padding: 0,
                    },
                    onClick: () => setThumbIdx((thumbIdx - 1 + thumbCount) % thumbCount),
                  }, "\u25C0"),
                  React.createElement("span", { style: { color: "#fff", fontSize: "9px" } }, `${thumbIdx + 1}/${thumbCount}`),
                  React.createElement("button", {
                    style: {
                      background: "rgba(0,0,0,0.6)", border: "none", color: "#fff",
                      borderRadius: "50%", width: 20, height: 20, cursor: "pointer",
                      fontSize: "10px", display: "flex", alignItems: "center", justifyContent: "center", padding: 0,
                    },
                    onClick: () => setThumbIdx((thumbIdx + 1) % thumbCount),
                  }, "\u25B6")
                )
              : null
          ),
          // Name input + checkbox
          React.createElement(
            "div",
            { style: { flex: 1 } },
            React.createElement("label", { style: { color: THEME.textMuted, fontSize: "12px", display: "block", marginBottom: "4px" } }, "Performer name:"),
            React.createElement("input", {
              type: "text",
              value: name,
              onChange: (e: any) => setName(e.target.value),
              style: {
                width: "100%", padding: "8px", background: THEME.bgInput, color: THEME.text,
                border: `1px solid ${THEME.border}`, borderRadius: "4px", fontSize: "14px",
                boxSizing: "border-box" as any,
              },
              onFocus: (e: any) => e.target.select(),
              autoFocus: true,
              onKeyDown: (e: any) => { if (e.key === "Enter") handleCreate(); },
            }),
            React.createElement("label", {
              style: { display: "flex", alignItems: "center", gap: "6px", marginTop: "10px", cursor: "pointer", fontSize: "12px", color: THEME.textMuted },
            },
              React.createElement("input", {
                type: "checkbox",
                checked: setImage,
                onChange: (e: any) => setSetImage(e.target.checked),
                style: { cursor: "pointer" },
              }),
              "Use face crop as performer image"
            ),
            React.createElement("div", { style: { color: THEME.textMuted, fontSize: "11px", marginTop: "8px" } },
              `Appears in: ${cluster.scene_count || 0} scenes, ${cluster.image_count || 0} images`
            )
          )
        ),
        // Footer
        React.createElement(
          "div",
          {
            style: {
              display: "flex", justifyContent: "flex-end", gap: "8px",
              padding: "12px 16px", borderTop: `1px solid ${THEME.border}`,
            },
          },
          React.createElement("button", {
            style: {
              padding: "6px 16px", background: "transparent", color: THEME.text,
              border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer",
            },
            onClick: onClose,
          }, "Cancel"),
          React.createElement("button", {
            style: {
              padding: "6px 16px", background: THEME.accent,
              color: "#fff", border: "none", borderRadius: "4px",
              cursor: creating ? "not-allowed" : "pointer", opacity: creating ? 0.6 : 1,
            },
            onClick: handleCreate,
            disabled: creating,
          }, creating ? "Creating..." : "Create & Link")
        )
      )
    );
  }

  // ---------- Face Card ----------

  function FaceCard(props: {
    cluster: any;
    apiBase: string;
    onAction: (action: string, cluster: any, payload?: any) => void;
    selected?: boolean;
    onSelect?: (id: number, selected: boolean) => void;
    anySelected?: boolean;
  }) {
    const { cluster, apiBase, onAction, selected, onSelect, anySelected } = props;
    const [hovered, setHovered] = useState(false);
    const [menuOpen, setMenuOpen] = useState(false);
    const [thumbIdx, setThumbIdx] = useState(0);
    const [thumbCount, setThumbCount] = useState(1);
    const menuRef = useRef(null as any);
    const isLinked = cluster.status === "identified" && cluster.performer_id;

    // Fetch how many exemplar thumbnails are available
    useEffect(() => {
      (async () => {
        try {
          const res = await fetch(`${apiBase}/faces/clusters/${cluster.id}/thumbnail-count`);
          if (res.ok) {
            const data = await res.json();
            if (data.count > 1) setThumbCount(data.count);
          }
        } catch (_e) {}
      })();
    }, [apiBase, cluster.id]);

    // Close menu on outside click
    useEffect(() => {
      if (!menuOpen) return;
      const handler = (e: any) => {
        if (menuRef.current && !menuRef.current.contains(e.target)) {
          setMenuOpen(false);
        }
      };
      document.addEventListener("mousedown", handler);
      return () => document.removeEventListener("mousedown", handler);
    }, [menuOpen]);

    const totalAppearances = (cluster.scene_count || 0) + (cluster.image_count || 0);
    const thumbUrl = `${apiBase}/faces/clusters/${cluster.id}/thumbnail?size=200&index=${thumbIdx}&pad=0.2`;

    const showCheckbox = onSelect && (hovered || selected || anySelected);

    return React.createElement(
      "div",
      {
        style: {
          background: THEME.bgCard,
          borderRadius: "8px",
          border: selected ? `2px solid ${THEME.accent}` : `1px solid ${THEME.border}`,
          display: "flex",
          flexDirection: "column",
          position: "relative",
        },
        onMouseEnter: () => setHovered(true),
        onMouseLeave: () => setHovered(false),
      },
      // Selection checkbox (hidden until hover or any selection)
      showCheckbox
        ? React.createElement("div", {
            style: {
              position: "absolute", top: "6px", left: "6px", zIndex: 10,
              display: "flex", alignItems: "center", justifyContent: "center",
            },
            onClick: (e: any) => e.stopPropagation(),
          },
            React.createElement("input", {
              type: "checkbox",
              checked: !!selected,
              onChange: (e: any) => onSelect(cluster.id, e.target.checked),
              className: "face-card-checkbox",
              style: {
                width: "20px", height: "20px", cursor: "pointer",
                accentColor: "#5b9bd5",
                appearance: selected ? undefined : "none" as any,
                WebkitAppearance: selected ? undefined : "none" as any,
                background: selected ? "#5b9bd5" : "rgba(0,0,0,0.4)",
                borderRadius: "3px",
                border: selected ? "none" : "2px solid rgba(255,255,255,0.6)",
              },
            })
          )
        : null,
      // Thumbnail with cycling arrows
      React.createElement("div", {
        style: { overflow: "hidden", borderRadius: "8px 8px 0 0", position: "relative", cursor: "pointer" },
        onClick: () => {
          if (anySelected && onSelect) {
            onSelect(cluster.id, !selected);
          } else {
            onAction("detail", cluster);
          }
        },
      },
      React.createElement("img", {
        src: thumbUrl,
        style: { width: "100%", aspectRatio: "1", objectFit: "cover", display: "block" },
        loading: "lazy",
      }),
      // Left / Right cycling arrows at bottom (only if >1 thumbnail)
      thumbCount > 1
        ? React.createElement(React.Fragment, null,
            React.createElement("div", {
              style: {
                position: "absolute", bottom: "0", left: "0", right: "0",
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "4px 4px",
                background: "linear-gradient(transparent, rgba(0,0,0,0.5))",
              },
            },
              React.createElement("button", {
                style: {
                  background: "rgba(0,0,0,0.55)", border: "none", color: "#fff",
                  borderRadius: "50%", width: "20px", height: "20px", cursor: "pointer",
                  fontSize: "10px", display: "flex", alignItems: "center", justifyContent: "center",
                  padding: 0, lineHeight: 1, flexShrink: 0,
                },
                onClick: (e: any) => { e.stopPropagation(); setThumbIdx((prev: number) => (prev - 1 + thumbCount) % thumbCount); },
              }, "\u25C0"),
              // Dot indicators
              React.createElement("div", {
                style: { display: "flex", gap: "3px", justifyContent: "center", flex: 1 },
              }, ...Array.from({ length: Math.min(thumbCount, 7) }, (_: any, i: number) =>
                React.createElement("div", {
                  key: i,
                  style: {
                    width: "5px", height: "5px", borderRadius: "50%",
                    background: i === thumbIdx % Math.min(thumbCount, 7) ? "#fff" : "rgba(255,255,255,0.4)",
                  },
                })
              )),
              React.createElement("button", {
                style: {
                  background: "rgba(0,0,0,0.55)", border: "none", color: "#fff",
                  borderRadius: "50%", width: "20px", height: "20px", cursor: "pointer",
                  fontSize: "10px", display: "flex", alignItems: "center", justifyContent: "center",
                  padding: 0, lineHeight: 1, flexShrink: 0,
                },
                onClick: (e: any) => { e.stopPropagation(); setThumbIdx((prev: number) => (prev + 1) % thumbCount); },
              }, "\u25B6")
            )
          )
        : null
      ),
      // Info
      React.createElement(
        "div",
        { style: { padding: "8px 10px" } },
        React.createElement("div", {
          style: {
            color: THEME.text, fontWeight: 500, fontSize: "13px",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          },
        }, isLinked ? (cluster.label || `Performer #${cluster.performer_id}`) : "Unknown"),
        // StashDB match badge
        !isLinked && cluster.stashdb_match_id
          ? cluster.stashdb_suggestion_rejected
            // Rejected state: muted badge with undo button
            ? React.createElement("div", {
                style: {
                  display: "inline-flex", alignItems: "center", gap: "4px",
                  background: "rgba(120,120,120,0.1)", border: "1px solid rgba(120,120,120,0.25)",
                  borderRadius: "3px", padding: "1px 4px", marginTop: "3px", fontSize: "10px", color: THEME.textMuted,
                },
                title: `StashDB match rejected \u2014 click \u21a9 to undo`,
              },
                React.createElement("span", {
                  style: { textDecoration: "line-through", opacity: 0.6 },
                }, "\uD83C\uDFAF " + Math.round((cluster.stashdb_match_score || 0) * 100) + "%"),
                React.createElement("button", {
                  style: {
                    background: "transparent", border: "none", color: THEME.textMuted,
                    cursor: "pointer", padding: "0 2px", fontSize: "11px", lineHeight: 1,
                  },
                  onClick: (e: any) => { e.stopPropagation(); onAction("undo-reject-stashdb", cluster); },
                  title: "Undo rejection",
                }, "\u21A9")
              )
            // Normal state: badge with reject button
            : React.createElement("div", {
                style: {
                  display: "inline-flex", alignItems: "center", gap: "4px",
                  background: "rgba(33,150,243,0.15)", border: "1px solid rgba(33,150,243,0.3)",
                  borderRadius: "3px", padding: "1px 4px", marginTop: "3px", fontSize: "10px", color: "#64b5f6",
                },
                title: `StashDB match: ${Math.round((cluster.stashdb_match_score || 0) * 100)}% confidence`,
              },
                "\uD83C\uDFAF StashDB match " + Math.round((cluster.stashdb_match_score || 0) * 100) + "%",
                React.createElement("button", {
                  style: {
                    background: "transparent", border: "none", color: "rgba(100,181,246,0.7)",
                    cursor: "pointer", padding: "0 2px", fontSize: "10px", lineHeight: 1,
                  },
                  onClick: (e: any) => { e.stopPropagation(); onAction("reject-stashdb", cluster); },
                  title: "Mark this StashDB match as wrong",
                }, "\u2715")
              )
          : null,
        // Suggested performer badge
        !isLinked && cluster.top_suggestion && !cluster.stashdb_suggestion_rejected
          ? React.createElement("div", {
              style: {
                display: "inline-flex", alignItems: "center", gap: "4px",
                background: cluster.top_suggestion.confidence === "stashdb"
                  ? "rgba(33,150,243,0.15)"
                  : cluster.top_suggestion.confidence === "high"
                  ? "rgba(76,175,80,0.15)"
                  : cluster.top_suggestion.confidence === "possible"
                  ? "rgba(255,193,7,0.15)"
                  : "rgba(158,158,158,0.15)",
                border: `1px solid ${
                  cluster.top_suggestion.confidence === "stashdb"
                    ? "rgba(33,150,243,0.3)"
                    : cluster.top_suggestion.confidence === "high"
                    ? "rgba(76,175,80,0.3)"
                    : cluster.top_suggestion.confidence === "possible"
                    ? "rgba(255,193,7,0.3)"
                    : "rgba(158,158,158,0.3)"
                }`,
                borderRadius: "3px", padding: "1px 6px", marginTop: "3px", fontSize: "10px",
                color: cluster.top_suggestion.confidence === "stashdb"
                  ? "#64b5f6"
                  : cluster.top_suggestion.confidence === "high"
                  ? "#81c784"
                  : cluster.top_suggestion.confidence === "possible"
                  ? "#ffd54f"
                  : "#bdbdbd",
                maxWidth: "100%", overflow: "hidden",
              },
              title: cluster.top_suggestion.confidence === "stashdb"
                ? `StashDB: ${cluster.top_suggestion.performer_name}`
                : `Suggested: ${cluster.top_suggestion.performer_name} (${Math.round(cluster.top_suggestion.co_occurrence_ratio * 100)}% co-occurrence, ${cluster.top_suggestion.co_occurrence_count}/${cluster.top_suggestion.total_entities} appearances)`,
            },
              cluster.top_suggestion.confidence === "stashdb" ? "\u2B50 "
                : cluster.top_suggestion.confidence === "high" ? "\u2728 " : cluster.top_suggestion.confidence === "possible" ? "\uD83D\uDD0D " : "\uD83D\uDCA1 ",
              React.createElement("span", {
                style: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
              }, cluster.top_suggestion.performer_name),
              // Reject co-occurrence suggestion button (only for non-stashdb suggestions)
              cluster.top_suggestion.confidence !== "stashdb" && cluster.top_suggestion.performer_id
                ? React.createElement("button", {
                    style: {
                      background: "transparent", border: "none", color: "rgba(180,180,180,0.6)",
                      cursor: "pointer", padding: "0 2px", fontSize: "10px", lineHeight: 1, flexShrink: 0,
                    },
                    onClick: (e: any) => {
                      e.stopPropagation();
                      onAction("reject-suggestion", cluster, { performer_id: cluster.top_suggestion.performer_id });
                    },
                    title: `Mark ${cluster.top_suggestion.performer_name} as wrong for this face`,
                  }, "\u2715")
                : null
            )
          : null,
        React.createElement("div", { style: { color: THEME.textMuted, fontSize: "11px", marginTop: "2px" } },
          [
            cluster.scene_count ? `${cluster.scene_count}s` : null,
            cluster.image_count ? `${cluster.image_count}i` : null,
          ].filter(Boolean).join(", ") || "No appearances",
          totalAppearances > 0 ? ` \u00b7 ${totalAppearances} total` : ""
        ),
        // Inline rating widget
        w.RatingWidgetWithAPI
          ? React.createElement("div", { style: { marginTop: "4px" } },
              React.createElement(w.RatingWidgetWithAPI, {
                entityType: "face_cluster",
                entityId: cluster.id,
                initialValue: cluster.rating100 ?? null,
                compact: true,
              })
            )
          : null,
        // Action row
        React.createElement(
          "div",
          { style: { display: "flex", gap: "4px", marginTop: "6px" } },
          !isLinked
            ? React.createElement("button", {
                style: {
                  flex: 1, padding: "4px 0", background: THEME.accent, color: "#fff",
                  border: "none", borderRadius: "4px", cursor: "pointer", fontSize: "11px",
                },
                onClick: () => onAction("link", cluster),
              }, "Link")
            : isLinked
            ? React.createElement("button", {
                style: {
                  flex: 1, padding: "4px 0", background: "transparent", color: THEME.accent,
                  border: `1px solid ${THEME.borderAccent}`, borderRadius: "4px", cursor: "pointer", fontSize: "11px",
                },
                onClick: () => onAction("view", cluster),
              }, "View")
            : null,
          React.createElement("button", {
                style: {
                  padding: "4px 6px", background: "transparent", color: THEME.danger,
                  border: `1px solid ${THEME.danger}33`, borderRadius: "4px", cursor: "pointer", fontSize: "11px",
                },
                onClick: () => onAction("delete", cluster),
                title: "Permanently delete this face cluster",
              }, "\u2715"),
          // Menu button
          React.createElement(
            "div",
            { style: { position: "relative" }, ref: menuRef },
            React.createElement("button", {
              style: {
                padding: "4px 8px", background: "transparent", color: THEME.textMuted,
                border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer", fontSize: "11px",
              },
              onClick: () => setMenuOpen(!menuOpen),
            }, "\u22ef"),
            menuOpen
              ? React.createElement(
                  "div",
                  {
                    style: {
                      position: "absolute", right: 0, bottom: "100%", marginBottom: "2px",
                      background: THEME.bg, border: `1px solid ${THEME.border}`, borderRadius: "4px",
                      minWidth: "140px", zIndex: 100, boxShadow: "0 4px 12px rgba(0,0,0,0.4)",
                    },
                  },
                  isLinked
                    ? React.createElement("div", {
                        style: { padding: "6px 12px", color: THEME.text, cursor: "pointer", fontSize: "12px" },
                        onClick: () => { setMenuOpen(false); onAction("link", cluster); },
                        onMouseEnter: (e: any) => { e.target.style.background = THEME.bgHover; },
                        onMouseLeave: (e: any) => { e.target.style.background = "transparent"; },
                      }, "Change performer")
                    : null,
                  !isLinked
                    ? React.createElement("div", {
                        style: { padding: "6px 12px", color: THEME.text, cursor: "pointer", fontSize: "12px" },
                        onClick: () => { setMenuOpen(false); onAction("link", cluster); },
                        onMouseEnter: (e: any) => { e.target.style.background = THEME.bgHover; },
                        onMouseLeave: (e: any) => { e.target.style.background = "transparent"; },
                      }, "Link to performer")
                    : null,
                  !isLinked
                    ? React.createElement("div", {
                        style: { padding: "6px 12px", color: THEME.text, cursor: "pointer", fontSize: "12px" },
                        onClick: () => { setMenuOpen(false); onAction("create-performer", cluster); },
                        onMouseEnter: (e: any) => { e.target.style.background = THEME.bgHover; },
                        onMouseLeave: (e: any) => { e.target.style.background = "transparent"; },
                      }, "Create performer")
                    : null,
                  isLinked
                    ? React.createElement("div", {
                        style: { padding: "6px 12px", color: THEME.warning, cursor: "pointer", fontSize: "12px" },
                        onClick: () => { setMenuOpen(false); onAction("unlink", cluster); },
                        onMouseEnter: (e: any) => { e.target.style.background = THEME.bgHover; },
                        onMouseLeave: (e: any) => { e.target.style.background = "transparent"; },
                      }, "Unlink performer")
                    : null,
                  React.createElement("div", {
                        style: { padding: "6px 12px", color: THEME.text, cursor: "pointer", fontSize: "12px" },
                        onClick: () => { setMenuOpen(false); onAction("merge", cluster); },
                        onMouseEnter: (e: any) => { e.target.style.background = THEME.bgHover; },
                        onMouseLeave: (e: any) => { e.target.style.background = "transparent"; },
                      }, "Merge with..."),
                  React.createElement("div", {
                        style: { padding: "6px 12px", color: THEME.danger, cursor: "pointer", fontSize: "12px" },
                        onClick: () => { setMenuOpen(false); onAction("delete", cluster); },
                        onMouseEnter: (e: any) => { e.target.style.background = THEME.bgHover; },
                        onMouseLeave: (e: any) => { e.target.style.background = "transparent"; },
                      }, "Delete face")
                )
              : null
          )
        )
      )
    );
  }

  // ---------- Cluster Detail View ----------

  function ClusterDetailView(props: {
    clusterId: number;
    apiBase: string;
    onBack: () => void;
    onAction: (action: string, cluster: any) => void;
  }) {
    const { clusterId, apiBase, onBack, onAction } = props;
    const [detail, setDetail] = useState(null as any);
    const [loading, setLoading] = useState(true);
    const [stashdbBusy, setStashdbBusy] = useState(false);
    const [removingExemplar, setRemovingExemplar] = useState(null as number | null);
    const [thumbToken, setThumbToken] = useState(() => Date.now());
    const [similarFaces, setSimilarFaces] = useState([] as any[]);
    const [similarLoading, setSimilarLoading] = useState(false);
    const [mergingId, setMergingId] = useState(null as number | null);

    const fetchDetail = useCallback(async () => {
      setLoading(true);
      try {
        const res = await fetch(`${apiBase}/faces/clusters/${clusterId}`, { cache: 'no-store' });
        if (res.ok) {
          const data = await res.json();
          console.log("[FacesHub] fetchDetail response: exemplars=", data.exemplars?.length, "ids=", data.exemplars?.map((e: any) => e.id));
          setDetail(data);
        }
      } catch (e) {
        console.error("[FacesHub] Failed to fetch cluster detail:", e);
      }
      setLoading(false);
    }, [apiBase, clusterId]);

    useEffect(() => { fetchDetail(); }, [fetchDetail]);

    // Fetch similar faces
    const fetchSimilar = useCallback(async () => {
      setSimilarLoading(true);
      try {
        const res = await fetch(`${apiBase}/faces/clusters/${clusterId}/similar?limit=12`);
        if (res.ok) {
          const data = await res.json();
          setSimilarFaces(data.matches || []);
        }
      } catch (_e) {}
      setSimilarLoading(false);
    }, [apiBase, clusterId]);

    useEffect(() => { fetchSimilar(); }, [fetchSimilar]);

    const handleMergeSimilar = useCallback(async (absorbedId: number) => {
      if (!confirm(`Merge face #${absorbedId} into this cluster (#${clusterId})? This cannot be undone.`)) return;
      setMergingId(absorbedId);
      try {
        const res = await fetch(`${apiBase}/faces/clusters/merge`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ surviving_id: clusterId, absorbed_id: absorbedId }),
        });
        if (res.ok) {
          // Refresh detail and similar faces
          await fetchDetail();
          await fetchSimilar();
        }
      } catch (e) {
        console.error("[FacesHub] Merge similar failed:", e);
      }
      setMergingId(null);
    }, [apiBase, clusterId, fetchDetail, fetchSimilar]);

    const handleRemoveExemplar = useCallback(async (embeddingId: number) => {
      if (!confirm("Remove this face from the cluster's exemplars?\n\nThe cluster will be recalculated and images that no longer match may be unassigned.")) return;
      setRemovingExemplar(embeddingId);
      try {
        const res = await fetch(`${apiBase}/faces/clusters/${clusterId}/exemplars/${embeddingId}`, { method: "DELETE" });
        if (res.ok) {
          const data = await res.json();
          console.log("[FacesHub] DELETE exemplar response:", JSON.stringify(data));
          const parts: string[] = [];
          if (data.removed_entities > 0) {
            parts.push(`${data.removed_entities} weak embedding(s) unassigned from this cluster`);
          }
          if (data.performers_removed > 0) {
            parts.push(`performer removed from ${data.performers_removed} entity(ies) that no longer match`);
          }
          if (parts.length > 0) {
            alert(`Exemplar removed. ${parts.join("; ")}.`);
          }
          // Use the cluster detail returned directly from the DELETE response
          // to update immediately without a second GET request.
          const newToken = Date.now();
          setThumbToken(newToken);
          if (data.cluster) {
            console.log("[FacesHub] Updating detail from DELETE response, exemplars:", data.cluster.exemplars?.length, data.cluster.exemplars?.map((e: any) => e.id));
            setDetail(data.cluster);
          } else {
            // Fallback: re-fetch if server didn't include cluster detail
            await fetchDetail();
          }
        } else {
          const err = await res.json().catch(() => ({ detail: "Unknown error" }));
          alert(`Failed to remove exemplar: ${err.detail || res.statusText}`);
        }
      } catch (e) {
        console.error("[FacesHub] Failed to remove exemplar:", e);
        alert("Failed to remove exemplar.");
      }
      setRemovingExemplar(null);
    }, [apiBase, clusterId, fetchDetail]);

    if (loading) {
      return React.createElement("div", { style: { padding: "40px", textAlign: "center", color: THEME.textMuted } }, "Loading cluster detail...");
    }
    if (!detail) {
      return React.createElement("div", { style: { padding: "40px", textAlign: "center", color: THEME.textMuted } }, "Cluster not found.");
    }

    const statusColors: any = { identified: THEME.accent, unidentified: THEME.warning };
    const statusColor = statusColors[detail.status] || THEME.textMuted;
    const displayName = detail.label || (detail.performer_id ? `Performer #${detail.performer_id}` : `Face #${detail.id}`);
    const totalAppearances = (detail.scene_count || 0) + (detail.image_count || 0);

    const scenes = (detail.entities || []).filter((e: any) => e.entity_type === "scene");
    const images = (detail.entities || []).filter((e: any) => e.entity_type === "image");

    return React.createElement("div", { style: { padding: "20px 24px", color: THEME.text } },
      // Back + header
      React.createElement("div", { style: { display: "flex", alignItems: "center", gap: "12px", marginBottom: "20px" } },
        React.createElement("button", {
          style: {
            padding: "6px 12px", background: "transparent", color: THEME.textMuted,
            border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer", fontSize: "14px",
          },
          onClick: onBack,
        }, "\u2190 Back"),
        React.createElement("h2", { style: { margin: 0, fontSize: "20px", fontWeight: 600 } }, displayName),
        React.createElement("span", {
          style: {
            padding: "2px 8px", borderRadius: "10px", fontSize: "11px", fontWeight: 600,
            background: `${statusColor}22`, color: statusColor, textTransform: "capitalize",
          },
        }, detail.status)
      ),
      // Metadata row
      React.createElement("div", {
        style: {
          display: "flex", gap: "24px", flexWrap: "wrap", marginBottom: "24px",
          padding: "12px 16px", background: THEME.bgCard, borderRadius: "8px",
          border: `1px solid ${THEME.border}`, fontSize: "13px",
        },
      },
        React.createElement("div", null,
          React.createElement("span", { style: { color: THEME.textMuted } }, "Samples: "),
          React.createElement("strong", null, detail.sample_count || 0)
        ),
        React.createElement("div", null,
          React.createElement("span", { style: { color: THEME.textMuted } }, "Quality: "),
          React.createElement("strong", null, detail.quality_score != null ? detail.quality_score.toFixed(1) : "N/A")
        ),
        React.createElement("div", null,
          React.createElement("span", { style: { color: THEME.textMuted } }, "Appearances: "),
          React.createElement("strong", null, `${totalAppearances} (${detail.scene_count || 0} scenes, ${detail.image_count || 0} images)`)
        ),
        detail.performer_id
          ? React.createElement("div", null,
              React.createElement("span", { style: { color: THEME.textMuted } }, "Performer: "),
              React.createElement("a", {
                href: `/performers/${detail.performer_id}`,
                style: { color: THEME.accent, textDecoration: "none", fontWeight: 600 },
              }, detail.label || `#${detail.performer_id}`)
            )
          : null,
        detail.created_at
          ? React.createElement("div", null,
              React.createElement("span", { style: { color: THEME.textMuted } }, "Created: "),
              React.createElement("span", null, new Date(detail.created_at).toLocaleDateString())
            )
          : null,
        // Rating widget
        w.RatingWidgetWithAPI
          ? React.createElement("div", {
              style: { display: "flex", alignItems: "center", gap: "6px" },
            },
              React.createElement("span", { style: { color: THEME.textMuted } }, "Rating: "),
              React.createElement(w.RatingWidgetWithAPI, {
                entityType: "face_cluster",
                entityId: detail.id,
                initialValue: detail.rating100 ?? null,
              })
            )
          : null
      ),
      // StashDB match suggestion
      detail.stashdb_match && !detail.performer_id
        ? React.createElement("div", {
            style: {
              marginBottom: "24px", padding: "14px 18px", borderRadius: "8px",
              background: "linear-gradient(135deg, #1a237e15, #0d47a118)",
              border: "1px solid #42a5f544",
            },
          },
            React.createElement("div", { style: { display: "flex", alignItems: "center", gap: "10px", marginBottom: "10px" } },
              React.createElement("span", { style: { fontSize: "18px" } }, "\uD83C\uDFAF"),
              React.createElement("strong", { style: { fontSize: "14px" } }, "StashDB Match Found"),
              React.createElement("span", {
                style: {
                  marginLeft: "auto", padding: "2px 10px", borderRadius: "10px", fontSize: "12px", fontWeight: 600,
                  background: (detail.stashdb_match.similarity || 0) >= 0.75 ? "#4caf5033" : "#ff980033",
                  color: (detail.stashdb_match.similarity || 0) >= 0.75 ? "#4caf50" : "#ff9800",
                },
              }, `${Math.round((detail.stashdb_match.similarity || 0) * 100)}% confidence`)
            ),
            React.createElement("div", { style: { display: "flex", gap: "24px", flexWrap: "wrap", fontSize: "13px", marginBottom: "12px" } },
              // StashDB performer image (if available)
              detail.stashdb_match.image_url || detail.stashdb_match.local_performer_id
                ? React.createElement("img", {
                    src: detail.stashdb_match.image_url || `/performer/${detail.stashdb_match.local_performer_id}/image`,
                    style: {
                      width: 80, height: 80, borderRadius: "6px", objectFit: "contain", background: THEME.bg,
                      border: "2px solid #42a5f544", flexShrink: 0,
                    },
                    loading: "lazy",
                    onError: (e: any) => { e.target.style.display = "none"; },
                  })
                : null,
              React.createElement("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } },
                React.createElement("div", null,
                  React.createElement("span", { style: { color: THEME.textMuted } }, "Name: "),
                  React.createElement("strong", null, detail.stashdb_match.name || "Unknown"),
                  detail.stashdb_match.disambiguation
                    ? React.createElement("span", { style: { color: THEME.textMuted, marginLeft: "4px" } }, `(${detail.stashdb_match.disambiguation})`)
                    : null
                ),
                React.createElement("div", null,
                  React.createElement("span", { style: { color: THEME.textMuted } }, "Source: "),
                  React.createElement("span", null, detail.stashdb_match.source_endpoint || "Unknown")
                ),
                detail.stashdb_match.local_performer_id
                  ? React.createElement("div", null,
                      React.createElement("span", { style: { color: THEME.textMuted } }, "Local Performer: "),
                      React.createElement("a", {
                        href: `/performers/${detail.stashdb_match.local_performer_id}`,
                        style: { color: THEME.accent, textDecoration: "none", fontWeight: 600 },
                      }, `#${detail.stashdb_match.local_performer_id}`)
                    )
                  : null
              )
            ),
            React.createElement("div", { style: { display: "flex", gap: "8px" } },
              detail.stashdb_match.local_performer_id
                ? React.createElement("button", {
                    style: {
                      padding: "6px 14px", background: THEME.accent, color: "#fff",
                      border: "none", borderRadius: "4px", cursor: "pointer", fontSize: "12px",
                      opacity: stashdbBusy ? 0.6 : 1,
                    },
                    disabled: stashdbBusy,
                    onClick: async () => {
                      setStashdbBusy(true);
                      try {
                        const res = await fetch(
                          `${apiBase}/faces/stashdb/refs/${detail.stashdb_match.ref_id}/create-performer`,
                          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, use_stashdb_data: true }) }
                        );
                        if (res.ok) {
                          const r = await res.json();
                          setDetail({ ...detail, performer_id: r.performer_id, label: r.performer_name, status: "identified", stashdb_match: null });
                        }
                      } catch (e) { console.error("[FacesHub] StashDB link failed:", e); }
                      setStashdbBusy(false);
                    },
                  }, `\u2714 Link to existing performer #${detail.stashdb_match.local_performer_id}`)
                : React.createElement("button", {
                    style: {
                      padding: "6px 14px", background: THEME.accent, color: "#fff",
                      border: "none", borderRadius: "4px", cursor: "pointer", fontSize: "12px",
                      opacity: stashdbBusy ? 0.6 : 1,
                    },
                    disabled: stashdbBusy,
                    onClick: async () => {
                      setStashdbBusy(true);
                      try {
                        const res = await fetch(
                          `${apiBase}/faces/stashdb/refs/${detail.stashdb_match.ref_id}/create-performer`,
                          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cluster_id: clusterId, use_stashdb_data: true }) }
                        );
                        if (res.ok) {
                          const r = await res.json();
                          setDetail({ ...detail, performer_id: r.performer_id, label: r.performer_name, status: "identified", stashdb_match: null });
                        }
                      } catch (e) { console.error("[FacesHub] StashDB create failed:", e); }
                      setStashdbBusy(false);
                    },
                  }, "\u2714 Create & Link Performer"),
              React.createElement("button", {
                style: {
                  padding: "6px 14px", background: "transparent", color: THEME.textMuted,
                  border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer", fontSize: "12px",
                  opacity: stashdbBusy ? 0.6 : 1,
                },
                disabled: stashdbBusy,
                onClick: async () => {
                  setStashdbBusy(true);
                  try {
                    const res = await fetch(`${apiBase}/faces/stashdb/dismiss/${clusterId}`, { method: "POST" });
                    if (res.ok) {
                      setDetail({ ...detail, stashdb_match: null });
                    }
                  } catch (e) { console.error("[FacesHub] StashDB dismiss failed:", e); }
                  setStashdbBusy(false);
                },
              }, "\u2716 Dismiss")
            )
          )
        : null,
      // Action buttons
      React.createElement("div", { style: { display: "flex", gap: "8px", marginBottom: "24px", flexWrap: "wrap" } },
        !detail.performer_id
          ? React.createElement("button", {
              style: {
                padding: "6px 14px", background: THEME.accent, color: "#fff",
                border: "none", borderRadius: "4px", cursor: "pointer", fontSize: "12px",
              },
              onClick: () => onAction("link", detail),
            }, "Link to performer")
          : React.createElement("button", {
              style: {
                padding: "6px 14px", background: "transparent", color: THEME.accent,
                border: `1px solid ${THEME.accent}55`, borderRadius: "4px", cursor: "pointer", fontSize: "12px",
              },
              onClick: () => onAction("link", detail),
            }, "Change performer"),
        detail.performer_id
          ? React.createElement("button", {
              style: {
                padding: "6px 14px", background: "transparent", color: THEME.warning,
                border: `1px solid ${THEME.warning}55`, borderRadius: "4px", cursor: "pointer", fontSize: "12px",
              },
              onClick: () => onAction("unlink", detail),
            }, "Unlink performer")
          : null,
        !detail.performer_id
          ? React.createElement("button", {
              style: {
                padding: "6px 14px", background: "transparent", color: THEME.accent,
                border: `1px solid ${THEME.accent}55`, borderRadius: "4px", cursor: "pointer", fontSize: "12px",
              },
              onClick: () => onAction("create-performer", detail),
            }, "Create performer")
          : null,
        React.createElement("button", {
              style: {
                padding: "6px 14px", background: "transparent", color: THEME.danger,
                border: `1px solid ${THEME.danger}33`, borderRadius: "4px", cursor: "pointer", fontSize: "12px",
              },
              onClick: () => onAction("delete", detail),
            }, "Delete face")
      ),
      // Exemplar face crops
      React.createElement("h3", { style: { fontSize: "16px", fontWeight: 600, marginBottom: "12px" } },
        `Face Exemplars (${(detail.exemplars || []).length})`
      ),
      React.createElement("div", {
        style: {
          display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
          gap: "8px", marginBottom: "24px",
        },
      },
        ...(detail.exemplars || []).map((ex: any, i: number) => {
          const isRemoving = removingExemplar === ex.id;
          const canRemove = (detail.exemplars || []).length > 1;
          return React.createElement("div", {
            key: ex.id,
            style: {
              position: "relative",
              background: THEME.bgCard, borderRadius: "6px",
              border: `1px solid ${THEME.border}`, overflow: "hidden",
              opacity: isRemoving ? 0.5 : 1,
              transition: "opacity 0.2s",
            },
          },
            React.createElement("img", {
              src: `${apiBase}/faces/clusters/${clusterId}/thumbnail?size=200&index=${i}&pad=0.2&_t=${thumbToken}`,
              style: { width: "100%", aspectRatio: "1", objectFit: "cover", display: "block" },
              loading: "lazy",
            }),
            // Delete button overlay
            canRemove && !isRemoving
              ? React.createElement("button", {
                  style: {
                    position: "absolute", top: "4px", right: "4px",
                    width: "22px", height: "22px",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    background: "rgba(0,0,0,0.7)", color: "#ff6b6b",
                    border: "1px solid rgba(255,107,107,0.4)", borderRadius: "50%",
                    cursor: "pointer", fontSize: "13px", fontWeight: "bold",
                    lineHeight: "1", padding: 0,
                    opacity: 0.7, transition: "opacity 0.15s",
                  },
                  title: "Remove this exemplar",
                  onClick: () => handleRemoveExemplar(ex.id),
                  onMouseEnter: (e: any) => { e.currentTarget.style.opacity = "1"; },
                  onMouseLeave: (e: any) => { e.currentTarget.style.opacity = "0.7"; },
                }, "\u00D7")
              : null,
            isRemoving
              ? React.createElement("div", {
                  style: {
                    position: "absolute", inset: 0,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    background: "rgba(0,0,0,0.5)", color: "#fff", fontSize: "11px",
                  },
                }, "Removing...")
              : null,
            React.createElement("div", {
              style: { padding: "4px 6px", fontSize: "10px", color: THEME.textMuted },
            },
              `Score: ${ex.score?.toFixed(2) || "N/A"}`,
              ex.timestamp_s != null ? ` \u00B7 ${ex.timestamp_s.toFixed(1)}s` : ""
            )
          );
        })
      ),
      // Scenes list
      scenes.length > 0
        ? React.createElement(React.Fragment, null,
            React.createElement("h3", { style: { fontSize: "16px", fontWeight: 600, marginBottom: "12px" } },
              `Scenes (${scenes.length})`
            ),
            React.createElement("div", {
              style: {
                display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
                gap: "8px", marginBottom: "24px",
              },
            },
              ...scenes.map((e: any) =>
                React.createElement("a", {
                  key: `scene-${e.entity_id}`,
                  href: `/scenes/${e.entity_id}`,
                  style: {
                    display: "flex", alignItems: "center", gap: "8px",
                    padding: "8px 12px", background: THEME.bgCard,
                    border: `1px solid ${THEME.border}`, borderRadius: "6px",
                    color: THEME.text, textDecoration: "none", fontSize: "13px",
                  },
                  onMouseEnter: (ev: any) => { ev.currentTarget.style.borderColor = THEME.accent; },
                  onMouseLeave: (ev: any) => { ev.currentTarget.style.borderColor = THEME.border; },
                },
                  React.createElement("img", {
                    src: `/scene/${e.entity_id}/screenshot`,
                    style: { width: 60, height: 34, objectFit: "cover", borderRadius: "3px", flexShrink: 0 },
                    loading: "lazy",
                    onError: (ev: any) => { ev.target.style.display = "none"; },
                  }),
                  React.createElement("span", null, `Scene #${e.entity_id}`)
                )
              )
            )
          )
        : null,
      // Images list
      images.length > 0
        ? React.createElement(React.Fragment, null,
            React.createElement("h3", { style: { fontSize: "16px", fontWeight: 600, marginBottom: "12px" } },
              `Images (${images.length})`
            ),
            React.createElement("div", {
              style: {
                display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
                gap: "8px", marginBottom: "24px",
              },
            },
              ...images.map((e: any) =>
                React.createElement("a", {
                  key: `image-${e.entity_id}`,
                  href: `/images/${e.entity_id}`,
                  style: {
                    display: "block", background: THEME.bgCard,
                    border: `1px solid ${THEME.border}`, borderRadius: "6px",
                    overflow: "hidden", textDecoration: "none",
                  },
                  onMouseEnter: (ev: any) => { ev.currentTarget.style.borderColor = THEME.accent; },
                  onMouseLeave: (ev: any) => { ev.currentTarget.style.borderColor = THEME.border; },
                },
                  React.createElement("img", {
                    src: `/image/${e.entity_id}/thumbnail`,
                    style: { width: "100%", aspectRatio: "1", objectFit: "cover", display: "block" },
                    loading: "lazy",
                    onError: (ev: any) => { ev.target.style.display = "none"; },
                  }),
                  React.createElement("div", { style: { padding: "4px 6px", fontSize: "11px", color: THEME.textMuted, textAlign: "center" } },
                    `Image #${e.entity_id}`
                  )
                )
              )
            )
          )
        : null,
      // Similar Faces section
      React.createElement("h3", { style: { fontSize: "16px", fontWeight: 600, marginBottom: "12px" } },
        "Similar Faces"
      ),
      similarLoading
        ? React.createElement("div", { style: { color: THEME.textMuted, fontSize: "13px", marginBottom: "24px" } }, "Loading similar faces...")
        : similarFaces.length === 0
          ? React.createElement("div", { style: { color: THEME.textMuted, fontSize: "13px", marginBottom: "24px" } }, "No similar faces found.")
          : React.createElement("div", {
              style: {
                display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
                gap: "10px", marginBottom: "24px",
              },
            },
              ...similarFaces.map((m: any) => {
                const isMerging = mergingId === m.id;
                return React.createElement("div", {
                  key: m.id,
                  style: {
                    background: THEME.bgCard, borderRadius: "8px",
                    border: `1px solid ${THEME.border}`, overflow: "hidden",
                    opacity: isMerging ? 0.5 : 1,
                    transition: "opacity 0.2s",
                  },
                },
                  // Clickable thumbnail → navigate to that cluster
                  React.createElement("a", {
                    href: `/plugins/ai-faces?id=${m.id}`,
                    style: { display: "block", textDecoration: "none" },
                  },
                    React.createElement("img", {
                      src: `${apiBase}/faces/clusters/${m.id}/thumbnail?size=200&pad=0.2`,
                      style: { width: "100%", aspectRatio: "1", objectFit: "cover", display: "block" },
                      loading: "lazy",
                      onError: (ev: any) => { ev.target.style.display = "none"; },
                    })
                  ),
                  React.createElement("div", { style: { padding: "6px 8px" } },
                    React.createElement("div", {
                      style: {
                        fontSize: "12px", fontWeight: 500, color: THEME.text,
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                      },
                    }, m.label || `Face #${m.id}`),
                    React.createElement("div", {
                      style: { fontSize: "11px", color: THEME.textMuted, marginTop: "2px" },
                    }, `${Math.round(m.similarity * 100)}% match`),
                    m.performer_id
                      ? React.createElement("a", {
                          href: `/performers/${m.performer_id}`,
                          style: { fontSize: "11px", color: THEME.accent, textDecoration: "none" },
                        }, m.label || `Performer #${m.performer_id}`)
                      : null,
                    // Merge button
                    React.createElement("button", {
                      style: {
                        marginTop: "4px", width: "100%", padding: "4px 0",
                        background: "transparent", color: "#4fc3f7",
                        border: `1px solid #4fc3f733`, borderRadius: "4px",
                        cursor: isMerging ? "wait" : "pointer", fontSize: "11px",
                        opacity: isMerging ? 0.5 : 1,
                      },
                      disabled: isMerging,
                      onClick: () => handleMergeSimilar(m.id),
                    }, isMerging ? "Merging..." : "\u2B07 Merge into this")
                  )
                );
              })
            )
    );
  }

  // ---------- StashDB Management Component ----------

  function StashDBManagement(props: { apiBase: string; onBack: () => void }) {
    const { apiBase, onBack } = props;
    const [stats, setStats] = useState(null as any);
    const [packs, setPacks] = useState([] as any[]);
    const [uploading, setUploading] = useState(false);
    const [uploadProgress, setUploadProgress] = useState("");
    const [uploadPercent, setUploadPercent] = useState(-1);
    const [refs, setRefs] = useState([] as any[]);
    const [refsTotal, setRefsTotal] = useState(0);
    const [refsPage, setRefsPage] = useState(1);
    const [refsSearch, setRefsSearch] = useState("");
    const [refsSearchInput, setRefsSearchInput] = useState("");
    const refsPerPage = 20;
    const fileInputRef = useRef(null as HTMLInputElement | null);

    const fetchStats = useCallback(async () => {
      try {
        const res = await fetch(`${apiBase}/faces/stashdb/stats`);
        if (res.ok) setStats(await res.json());
      } catch (e) { console.error("[StashDB] Failed to fetch stats:", e); }
    }, [apiBase]);

    const fetchPacks = useCallback(async () => {
      try {
        const res = await fetch(`${apiBase}/faces/stashdb/packs`);
        if (res.ok) {
          const data = await res.json();
          setPacks(data.packs || []);
        }
      } catch (e) { console.error("[StashDB] Failed to fetch packs:", e); }
    }, [apiBase]);

    const fetchRefs = useCallback(async () => {
      try {
        const searchParam = refsSearch ? `&search=${encodeURIComponent(refsSearch)}` : "";
        const res = await fetch(`${apiBase}/faces/stashdb/refs?page=${refsPage}&per_page=${refsPerPage}${searchParam}`);
        if (res.ok) {
          const data = await res.json();
          setRefs(data.refs || []);
          setRefsTotal(data.total || 0);
        }
      } catch (e) { console.error("[StashDB] Failed to fetch refs:", e); }
    }, [apiBase, refsPage, refsPerPage, refsSearch]);

    useEffect(() => { fetchStats(); fetchPacks(); }, [fetchStats, fetchPacks]);
    useEffect(() => { fetchRefs(); }, [fetchRefs]);

    const handleUpload = useCallback(async (file: File) => {
      setUploading(true);
      setUploadProgress("Uploading...");
      setUploadPercent(0);
      try {
        const form = new FormData();
        form.append("file", file);
        const res = await fetch(`${apiBase}/faces/stashdb/import`, {
          method: "POST",
          body: form,
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          setUploadProgress(`Error: ${err.detail || res.statusText}`);
          setUploadPercent(-1);
          setUploading(false);
          return;
        }
        // Stream NDJSON progress
        const reader = res.body?.getReader();
        if (!reader) {
          setUploadProgress("Error: no response body");
          setUploadPercent(-1);
          setUploading(false);
          return;
        }
        const decoder = new TextDecoder();
        let buffer = "";
        let lastData: any = null;
        while (true) {
          const { done, value } = await reader.read();
          if (value) buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          for (const line of lines) {
            if (!line.trim()) continue;
            try {
              const msg = JSON.parse(line);
              lastData = msg;
              if (msg.phase === "done") {
                const t = msg.timing;
                setUploadProgress(
                  `Imported ${msg.imported || 0} new, ${msg.updated || 0} updated` +
                  (msg.clusters_matched ? `, ${msg.clusters_matched} clusters matched` : "") +
                  (t ? ` (${t.total_s}s)` : "")
                );
                setUploadPercent(100);
              } else {
                setUploadProgress(msg.message || msg.phase);
                if (msg.total > 0) {
                  setUploadPercent(Math.round((msg.progress / msg.total) * 100));
                }
              }
            } catch { /* ignore malformed lines */ }
          }
          if (done) break;
        }
        // If we never got a "done" phase, show last message
        if (lastData && lastData.phase !== "done") {
          setUploadProgress(lastData.message || "Import finished");
        }
        fetchStats();
        fetchPacks();
        fetchRefs();
      } catch (e: any) {
        setUploadProgress(`Upload failed: ${e.message}`);
        setUploadPercent(-1);
      }
      setUploading(false);
    }, [apiBase, fetchStats, fetchPacks, fetchRefs]);

    const handleDeletePack = useCallback(async (packId: string) => {
      if (!confirm(`Delete pack "${packId}" and all its refs?`)) return;
      try {
        const res = await fetch(`${apiBase}/faces/stashdb/packs/${encodeURIComponent(packId)}`, { method: "DELETE" });
        if (res.ok) { fetchStats(); fetchPacks(); fetchRefs(); }
      } catch (e) { console.error("[StashDB] Delete pack failed:", e); }
    }, [apiBase, fetchStats, fetchPacks, fetchRefs]);

    const refsPages = Math.ceil(refsTotal / refsPerPage);

    return React.createElement("div", { style: { padding: "20px 24px", color: THEME.text } },
      // Header
      React.createElement("div", { style: { display: "flex", alignItems: "center", gap: "12px", marginBottom: "20px" } },
        React.createElement("button", {
          style: {
            padding: "6px 12px", background: "transparent", color: THEME.textMuted,
            border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer", fontSize: "14px",
          },
          onClick: onBack,
        }, "\u2190 Back to Faces"),
        React.createElement("h2", { style: { margin: 0, fontSize: "20px", fontWeight: 600 } }, "StashDB Embeddings")
      ),
      // Stats
      stats
        ? React.createElement("div", {
            style: {
              display: "flex", gap: "16px", flexWrap: "wrap", marginBottom: "20px",
              padding: "12px 16px", background: THEME.bgCard, borderRadius: "8px",
              border: `1px solid ${THEME.border}`, fontSize: "13px",
            },
          },
            React.createElement("div", null,
              React.createElement("span", { style: { color: THEME.textMuted } }, "Total Refs: "),
              React.createElement("strong", null, stats.total_refs || 0)
            ),
            React.createElement("div", null,
              React.createElement("span", { style: { color: THEME.textMuted } }, "With Local Performer: "),
              React.createElement("strong", null, stats.with_local_performer || 0)
            ),
            React.createElement("div", null,
              React.createElement("span", { style: { color: THEME.textMuted } }, "Without Local Performer: "),
              React.createElement("strong", null, stats.without_local_performer || 0)
            ),
            stats.embedders && stats.embedders.length > 0
              ? React.createElement("div", null,
                  React.createElement("span", { style: { color: THEME.textMuted } }, "Embedders: "),
                  React.createElement("strong", null, stats.embedders.join(", "))
                )
              : null
          )
        : null,
      // Upload section
      React.createElement("div", {
        style: {
          marginBottom: "24px", padding: "16px", borderRadius: "8px",
          background: THEME.bgCard, border: `1px solid ${THEME.border}`,
        },
      },
        React.createElement("h3", { style: { margin: "0 0 12px 0", fontSize: "15px", fontWeight: 600 } }, "Import .saie Pack"),
        React.createElement("div", { style: { display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" } },
          React.createElement("input", {
            ref: fileInputRef,
            type: "file",
            accept: ".saie,.zip",
            style: { display: "none" },
            onChange: (e: any) => {
              const file = e.target.files?.[0];
              if (file) handleUpload(file);
              e.target.value = "";
            },
          }),
          React.createElement("button", {
            style: {
              padding: "8px 16px", background: THEME.accent, color: "#fff",
              border: "none", borderRadius: "4px", cursor: uploading ? "wait" : "pointer",
              fontSize: "13px", opacity: uploading ? 0.6 : 1,
            },
            disabled: uploading,
            onClick: () => fileInputRef.current?.click(),
          }, uploading ? "Importing..." : "Choose .saie File"),
          // Progress bar + text
          uploading && uploadPercent >= 0
            ? React.createElement("div", { style: { flex: "1 1 200px", minWidth: "200px" } },
                React.createElement("div", {
                  style: {
                    height: "8px", borderRadius: "4px", background: THEME.bgInput || "#333",
                    overflow: "hidden", marginBottom: "4px",
                  },
                },
                  React.createElement("div", {
                    style: {
                      height: "100%", width: `${uploadPercent}%`,
                      background: THEME.accent, borderRadius: "4px",
                      transition: "width 0.3s ease",
                    },
                  })
                ),
                React.createElement("span", { style: { fontSize: "12px", color: THEME.accent } },
                  `${uploadPercent}% – ${uploadProgress}`
                )
              )
            : uploadProgress
              ? React.createElement("span", {
                  style: {
                    fontSize: "12px",
                    color: uploadProgress.startsWith("Error") || uploadProgress.startsWith("Upload failed")
                      ? THEME.danger : THEME.accent,
                  },
                }, uploadProgress)
              : React.createElement("span", { style: { fontSize: "12px", color: THEME.textMuted } },
                  "Upload an embedding pack (.saie) to enable StashDB matching")
        )
      ),
      // Packs
      packs.length > 0
        ? React.createElement(React.Fragment, null,
            React.createElement("h3", { style: { fontSize: "15px", fontWeight: 600, marginBottom: "10px" } }, "Imported Packs"),
            React.createElement("div", { style: { marginBottom: "24px" } },
              ...packs.map((p: any) =>
                React.createElement("div", {
                  key: p.pack_id,
                  style: {
                    display: "flex", alignItems: "center", gap: "12px",
                    padding: "10px 14px", marginBottom: "6px", borderRadius: "6px",
                    background: THEME.bgCard, border: `1px solid ${THEME.border}`, fontSize: "13px",
                  },
                },
                  React.createElement("strong", { style: { flex: 1 } }, p.pack_id),
                  React.createElement("span", { style: { color: THEME.textMuted } }, `${p.count || 0} performers`),
                  p.source_endpoint
                    ? React.createElement("span", { style: { color: THEME.textMuted, fontSize: "11px" } }, p.source_endpoint)
                    : null,
                  React.createElement("button", {
                    style: {
                      padding: "4px 10px", background: "transparent", color: THEME.danger,
                      border: `1px solid ${THEME.danger}33`, borderRadius: "4px", cursor: "pointer", fontSize: "11px",
                    },
                    onClick: () => handleDeletePack(p.pack_id),
                  }, "Delete")
                )
              )
            )
          )
        : null,
      // Browse refs
      React.createElement("h3", { style: { fontSize: "15px", fontWeight: 600, marginBottom: "10px" } }, "Browse Performers"),
      React.createElement("div", { style: { display: "flex", gap: "8px", marginBottom: "12px", alignItems: "center" } },
        React.createElement("input", {
          type: "text",
          placeholder: "Search by name or StashDB ID...",
          value: refsSearchInput,
          onChange: (e: any) => setRefsSearchInput(e.target.value),
          onKeyDown: (e: any) => {
            if (e.key === "Enter") { setRefsSearch(refsSearchInput); setRefsPage(1); }
          },
          style: {
            flex: 1, minWidth: "180px", padding: "6px 10px",
            background: THEME.bgCard, color: THEME.text,
            border: `1px solid ${THEME.border}`, borderRadius: "4px",
            fontSize: "13px", outline: "none",
          },
        }),
        refsSearchInput
          ? React.createElement("button", {
              style: {
                padding: "6px 10px", background: THEME.accent, color: "#fff",
                border: "none", borderRadius: "4px", cursor: "pointer", fontSize: "12px",
              },
              onClick: () => { setRefsSearch(refsSearchInput); setRefsPage(1); },
            }, "Search")
          : null,
        refsSearch
          ? React.createElement("button", {
              style: {
                padding: "6px 10px", background: "transparent", color: THEME.textMuted,
                border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer", fontSize: "12px",
              },
              onClick: () => { setRefsSearch(""); setRefsSearchInput(""); setRefsPage(1); },
            }, "\u2715 Clear")
          : null,
        React.createElement("span", { style: { fontSize: "12px", color: THEME.textMuted } }, `${refsTotal} total`)
      ),
      refs.length > 0
        ? React.createElement("div", { style: { marginBottom: "16px" } },
            ...refs.map((r: any) =>
              React.createElement("div", {
                key: r.id,
                style: {
                  display: "flex", alignItems: "center", gap: "10px",
                  padding: "8px 12px", marginBottom: "4px", borderRadius: "6px",
                  background: THEME.bgCard, border: `1px solid ${THEME.border}`, fontSize: "13px",
                },
              },
                React.createElement("span", { style: { fontWeight: 600, minWidth: "140px" } }, r.name || "Unknown"),
                r.disambiguation
                  ? React.createElement("span", { style: { color: THEME.textMuted, fontSize: "11px" } }, `(${r.disambiguation})`)
                  : null,
                React.createElement("span", { style: { color: THEME.textMuted, fontSize: "11px", flex: 1 } }, r.stashdb_id?.slice(0, 8) + "..."),
                r.local_performer_id
                  ? React.createElement("a", {
                      href: `/performers/${r.local_performer_id}`,
                      style: { color: THEME.accent, textDecoration: "none", fontSize: "11px" },
                    }, `Linked #${r.local_performer_id}`)
                  : React.createElement("span", { style: { color: THEME.textMuted, fontSize: "11px" } }, "Not linked"),
                r.quality_score != null
                  ? React.createElement("span", { style: { color: THEME.textMuted, fontSize: "11px" } }, `Q: ${r.quality_score.toFixed(1)}`)
                  : null
              )
            )
          )
        : React.createElement("div", { style: { color: THEME.textMuted, fontSize: "13px", padding: "12px 0" } },
            refsSearch ? "No matching performers found." : "No StashDB performers imported yet."
          ),
      // Refs pagination
      refsPages > 1
        ? React.createElement("div", {
            style: { display: "flex", justifyContent: "center", alignItems: "center", gap: "8px", marginTop: "8px" },
          },
            React.createElement("button", {
              style: {
                padding: "4px 10px", background: "transparent", color: refsPage > 1 ? THEME.text : THEME.textMuted,
                border: `1px solid ${THEME.border}`, borderRadius: "4px",
                cursor: refsPage > 1 ? "pointer" : "not-allowed", fontSize: "12px",
              },
              onClick: () => { if (refsPage > 1) setRefsPage(refsPage - 1); },
              disabled: refsPage <= 1,
            }, "\u25c0"),
            React.createElement("span", { style: { color: THEME.textMuted, fontSize: "12px" } },
              `Page ${refsPage} of ${refsPages}`
            ),
            React.createElement("button", {
              style: {
                padding: "4px 10px", background: "transparent", color: refsPage < refsPages ? THEME.text : THEME.textMuted,
                border: `1px solid ${THEME.border}`, borderRadius: "4px",
                cursor: refsPage < refsPages ? "pointer" : "not-allowed", fontSize: "12px",
              },
              onClick: () => { if (refsPage < refsPages) setRefsPage(refsPage + 1); },
              disabled: refsPage >= refsPages,
            }, "\u25b6")
          )
        : null
    );
  }

  // ---------- Main FacesHub Component ----------

  function FacesHub() {
    const apiBase = getApiBase();
    const [clusters, setClusters] = useState([] as any[]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [filter, setFilter] = useState("unidentified");
    const [loading, setLoading] = useState(true);
    const [linkDialog, setLinkDialog] = useState(null as any);
    const [mergeDialog, setMergeDialog] = useState(null as any);
    const [createPerformerDialog, setCreatePerformerDialog] = useState(null as any);
    const [selectedClusterId, setSelectedClusterId] = useState(() => {
      try {
        const p = new URLSearchParams(w.location.search);
        const id = p.get("id");
        return id ? parseInt(id, 10) : null;
      } catch (_) { return null; }
    });
    const [showStashDB, setShowStashDB] = useState(false);
    const [rowsPerPage, setRowsPerPage] = useState(() => {
      try { const v = parseInt(localStorage.getItem('facesHub.rows') || '', 10); if ([3,4,5,6,8,10].includes(v)) return v; } catch(_){}
      return 5;
    });
    const [zoomIndex, setZoomIndex] = useState(() => {
      try { const v = parseInt(localStorage.getItem('facesHub.zoom') || '', 10); if (v >= 0 && v <= 5) return v; } catch(_){}
      return 1;
    });
    const [colCount, setColCount] = useState(0);
    const wrapperRef = useRef(null as any);
    const ZOOM_WIDTHS = [120, 150, 180, 230, 300, 380];
    const perPage = rowsPerPage * Math.max(1, colCount);

    useEffect(() => {
      try { localStorage.setItem('facesHub.rows', String(rowsPerPage)); } catch(_){}
    }, [rowsPerPage]);
    useEffect(() => {
      try { localStorage.setItem('facesHub.zoom', String(zoomIndex)); } catch(_){}
    }, [zoomIndex]);
    useEffect(() => {
      const el = wrapperRef.current;
      if (!el) return;
      const gap = 12, padding = 48, cardMin = ZOOM_WIDTHS[zoomIndex];
      const compute = () => {
        const w = el.offsetWidth || 0;
        if (w > 0) setColCount(Math.max(1, Math.floor((w - padding + gap) / (cardMin + gap))));
      };
      compute();
      // Fallback: if element wasn't laid out yet, retry after browser paint
      const raf = requestAnimationFrame(compute);
      // Interval retry: handles cases where element is hidden on mount (tab not visible)
      const iv = setInterval(() => {
        const w = el.offsetWidth || 0;
        if (w > 0) { compute(); clearInterval(iv); }
      }, 200);
      const ro = new ResizeObserver(compute);
      ro.observe(el);
      return () => { ro.disconnect(); cancelAnimationFrame(raf); clearInterval(iv); };
    }, [zoomIndex]);
    const [sortBy, setSortBy] = useState("suggestion_confidence");
    const [sortDir, setSortDir] = useState("desc");
    const [searchText, setSearchText] = useState("");
    const [searchInput, setSearchInput] = useState("");
    const [selectedIds, setSelectedIds] = useState(new Set<number>());
    const [bulkBusy, setBulkBusy] = useState(false);
    const [bulkResult, setBulkResult] = useState(null as any);

    const abortRef = useRef(null as AbortController | null);
    const fetchClusters = useCallback(async () => {
      // Abort any in-flight fetch to avoid race conditions
      if (abortRef.current) abortRef.current.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setSelectedIds(new Set());  // clear selection on page/filter change
      try {
        const statusParam = filter === "all" ? "" : `&status=${filter}`;
        const sortParam = `&sort=${sortBy}&sort_dir=${sortDir}`;
        const searchParam = searchText ? `&search=${encodeURIComponent(searchText)}` : "";
        const res = await fetch(`${apiBase}/faces/clusters?page=${page}&per_page=${perPage}${statusParam}${sortParam}${searchParam}`, { signal: controller.signal });
        if (res.ok) {
          const data = await res.json();
          setClusters(data.clusters || []);
          setTotal(data.total || 0);
        }
      } catch (e: any) {
        if (e?.name !== 'AbortError') console.error("[FacesHub] Failed to fetch clusters:", e);
      }
      if (!controller.signal.aborted) setLoading(false);
    }, [apiBase, page, filter, perPage, sortBy, sortDir, searchText]);

    useEffect(() => { if (colCount > 0) fetchClusters(); }, [fetchClusters, colCount]);

    // Parse URL params for initial filter
    useEffect(() => {
      try {
        const params = new URLSearchParams(w.location.search);
        const status = params.get("status");
        if (status) setFilter(status);
      } catch (e) {}
    }, []);

    // Sync detail-view selection with URL for deep-linking & browser back
    const navigateToCluster = useCallback((id: number | null) => {
      setSelectedClusterId(id);
      try {
        const url = new URL(w.location.href);
        if (id != null) url.searchParams.set("id", String(id));
        else url.searchParams.delete("id");
        w.history.pushState({}, "", url.pathname + url.search);
      } catch (_) {}
    }, []);

    useEffect(() => {
      const onPop = () => {
        try {
          const p = new URLSearchParams(w.location.search);
          const id = p.get("id");
          setSelectedClusterId(id ? parseInt(id, 10) : null);
        } catch (_) {}
      };
      w.addEventListener("popstate", onPop);
      return () => w.removeEventListener("popstate", onPop);
    }, []);

    const handleAction = useCallback(async (action: string, cluster: any, payload?: any) => {
      if (action === "link") {
        setLinkDialog(cluster);
      } else if (action === "merge") {
        setMergeDialog(cluster);
      } else if (action === "create-performer") {
        setCreatePerformerDialog(cluster);
      } else if (action === "delete") {
        if (!w.confirm("Permanently delete this face and all its data?")) return;
        try {
          await fetch(`${apiBase}/faces/clusters/${cluster.id}`, { method: "DELETE" });
          fetchClusters();
        } catch (e) {
          console.error("[FacesHub] Delete failed:", e);
        }
      } else if (action === "unlink") {
        try {
          await fetch(`${apiBase}/faces/clusters/${cluster.id}/unlink`, { method: "POST" });
          fetchClusters();
        } catch (e) {
          console.error("[FacesHub] Unlink failed:", e);
        }
      } else if (action === "reject-stashdb") {
        try {
          await fetch(`${apiBase}/faces/clusters/${cluster.id}/reject-stashdb`, { method: "POST" });
          fetchClusters();
        } catch (e) {
          console.error("[FacesHub] Reject StashDB failed:", e);
        }
      } else if (action === "undo-reject-stashdb") {
        try {
          await fetch(`${apiBase}/faces/clusters/${cluster.id}/reject-stashdb`, { method: "DELETE" });
          fetchClusters();
        } catch (e) {
          console.error("[FacesHub] Undo reject StashDB failed:", e);
        }
      } else if (action === "reject-suggestion") {
        const performerId = payload?.performer_id;
        if (performerId) {
          try {
            await fetch(`${apiBase}/faces/clusters/${cluster.id}/reject-suggestion`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ performer_id: performerId }),
            });
            fetchClusters();
          } catch (e) {
            console.error("[FacesHub] Reject suggestion failed:", e);
          }
        }
      } else if (action === "view" && cluster.performer_id) {
        w.location.href = `/performers/${cluster.performer_id}`;
      } else if (action === "detail") {
        navigateToCluster(cluster.id);
      }
    }, [apiBase, fetchClusters]);

    const totalPages = Math.ceil(total / perPage);

    const filters = [
      { key: "all", label: "All" },
      { key: "unidentified", label: "Unlinked" },
      { key: "identified", label: "Linked" },
    ];

    // If StashDB management is open, show that
    if (showStashDB) {
      return React.createElement(StashDBManagement, {
        apiBase,
        onBack: () => setShowStashDB(false),
      });
    }

    // If a cluster is selected, show its detail view instead of the grid
    if (selectedClusterId != null) {
      return React.createElement(React.Fragment, null,
        React.createElement(ClusterDetailView, {
          clusterId: selectedClusterId,
          apiBase,
          onBack: () => navigateToCluster(null),
          onAction: (action: string, cluster: any) => {
            // Run the action from detail view context
            handleAction(action, cluster);
            // For destructive actions, go back to the grid
            if (action === "delete" || action === "unlink") {
              navigateToCluster(null);
            }
          },
        }),
        // Dialogs must render alongside the detail view so they appear as overlays
        linkDialog
          ? React.createElement(PerformerLinkDialog, {
              cluster: linkDialog,
              apiBase,
              onClose: () => setLinkDialog(null),
              onLinked: () => { setLinkDialog(null); navigateToCluster(null); fetchClusters(); },
            })
          : null,
        mergeDialog
          ? React.createElement(MergeDialog, {
              cluster: mergeDialog,
              apiBase,
              onClose: () => setMergeDialog(null),
              onMerged: () => { setMergeDialog(null); navigateToCluster(null); fetchClusters(); },
            })
          : null,
        createPerformerDialog
          ? React.createElement(CreatePerformerDialog, {
              cluster: createPerformerDialog,
              apiBase,
              onClose: () => setCreatePerformerDialog(null),
              onCreated: () => { setCreatePerformerDialog(null); navigateToCluster(null); fetchClusters(); },
            })
          : null,
      );
    }

    return React.createElement(
      "div",
      { ref: wrapperRef, style: { padding: "20px 24px", color: THEME.text } },
      // Header row
      React.createElement(
        "div",
        { style: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" } },
        React.createElement("h2", { style: { margin: 0, fontSize: "22px", fontWeight: 600 } }, "Faces"),
        React.createElement("div", { style: { display: "flex", gap: "6px" } },
          React.createElement("button", {
            style: {
              padding: "6px 12px", background: "transparent", color: THEME.textMuted,
              border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer", fontSize: "12px",
            },
            onClick: () => setShowStashDB(true),
            title: "Manage StashDB embeddings",
          }, "\uD83C\uDFAF StashDB"),
          React.createElement("button", {
            style: {
              padding: "6px 12px", background: "transparent", color: THEME.textMuted,
              border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer", fontSize: "14px",
            },
            onClick: fetchClusters,
            title: "Refresh",
          }, "\u21bb")
        )
      ),
      // Filter tabs
      React.createElement(
        "div",
        { style: { display: "flex", gap: "2px", marginBottom: "16px", background: THEME.bgCard, borderRadius: "6px", padding: "2px" } },
        ...filters.map((f: any) =>
          React.createElement("button", {
            key: f.key,
            style: {
              flex: 1, padding: "8px 12px", border: "none", borderRadius: "4px", cursor: "pointer",
              fontSize: "13px", fontWeight: filter === f.key ? 600 : 400,
              background: filter === f.key ? THEME.accent : "transparent",
              color: filter === f.key ? "#fff" : THEME.textMuted,
            },
            onClick: () => { setFilter(f.key); setPage(1); },
          }, `${f.label} ${filter === f.key ? `(${total})` : ""}`)
        )
      ),
      // Search + Sort controls
      React.createElement(
        "div",
        { style: { display: "flex", gap: "8px", marginBottom: "16px", alignItems: "center", flexWrap: "wrap" } },
        // Search input
        React.createElement("input", {
          type: "text",
          placeholder: "Search by name...",
          value: searchInput,
          onChange: (e: any) => setSearchInput(e.target.value),
          onKeyDown: (e: any) => {
            if (e.key === "Enter") { setSearchText(searchInput); setPage(1); }
          },
          style: {
            flex: 1, minWidth: "160px", padding: "6px 10px",
            background: THEME.bgCard, color: THEME.text,
            border: `1px solid ${THEME.border}`, borderRadius: "4px",
            fontSize: "13px", outline: "none",
          },
        }),
        searchInput
          ? React.createElement("button", {
              style: {
                padding: "6px 10px", background: THEME.accent, color: "#fff",
                border: "none", borderRadius: "4px", cursor: "pointer", fontSize: "12px",
              },
              onClick: () => { setSearchText(searchInput); setPage(1); },
            }, "Search")
          : null,
        searchText
          ? React.createElement("button", {
              style: {
                padding: "6px 10px", background: "transparent", color: THEME.textMuted,
                border: `1px solid ${THEME.border}`, borderRadius: "4px", cursor: "pointer", fontSize: "12px",
              },
              onClick: () => { setSearchText(""); setSearchInput(""); setPage(1); },
            }, "\u2715 Clear")
          : null,
        // Sort dropdown + direction arrow
        React.createElement("div", { style: { display: "flex", alignItems: "center", gap: "2px" } },
          React.createElement("select", {
            value: sortBy,
            onChange: (e: any) => { setSortBy(e.target.value); setPage(1); },
            style: {
              padding: "6px 8px", background: THEME.bgCard, color: THEME.text,
              border: `1px solid ${THEME.border}`, borderRadius: "4px 0 0 4px",
              fontSize: "12px", cursor: "pointer", borderRight: "none",
            },
          },
            React.createElement("option", { value: "updated_at" }, "Updated"),
            React.createElement("option", { value: "created_at" }, "Created"),
            React.createElement("option", { value: "scene_count" }, "Scenes"),
            React.createElement("option", { value: "image_count" }, "Images"),
            React.createElement("option", { value: "sample_count" }, "Samples"),
            React.createElement("option", { value: "quality_score" }, "Quality"),
            React.createElement("option", { value: "suggestion_confidence" }, "Suggestion"),
            React.createElement("option", { value: "label" }, "Name"),
            React.createElement("option", { value: "rating" }, "Rating")
          ),
          React.createElement("button", {
            onClick: () => { setSortDir((d: string) => d === "asc" ? "desc" : "asc"); setPage(1); },
            title: sortDir === "asc" ? "Ascending (click to reverse)" : "Descending (click to reverse)",
            style: {
              padding: "6px 8px", background: THEME.bgCard, color: THEME.text,
              border: `1px solid ${THEME.border}`, borderRadius: "0 4px 4px 0",
              fontSize: "14px", cursor: "pointer", lineHeight: 1,
            },
          }, sortDir === "asc" ? "\u25B2" : "\u25BC")
        )
      ),
      // Grid
      loading
        ? React.createElement("div", { style: { textAlign: "center", padding: "40px", color: THEME.textMuted } }, "Loading faces...")
        : clusters.length === 0
        ? React.createElement("div", { style: { textAlign: "center", padding: "40px", color: THEME.textMuted } },
            `No ${filter === "all" ? "" : filter + " "}faces found.`)
        : React.createElement(React.Fragment, null,
            // Bulk action bar (shown when any item is selected)
            selectedIds.size > 0
              ? React.createElement(
                  "div",
                  {
                    style: {
                      display: "flex", gap: "8px", marginBottom: "12px", alignItems: "center", flexWrap: "wrap",
                      padding: "10px 14px", background: "rgba(33,150,243,0.08)",
                      borderRadius: "6px", border: "1px solid rgba(33,150,243,0.25)",
                    },
                  },
                  // Select all on page
                  React.createElement("label", {
                    style: { display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: THEME.textMuted, cursor: "pointer", marginRight: "4px" },
                  },
                    React.createElement("input", {
                      type: "checkbox",
                      checked: clusters.length > 0 && clusters.every((c: any) => selectedIds.has(c.id)),
                      onChange: (e: any) => {
                        if (e.target.checked) {
                          setSelectedIds(new Set(clusters.map((c: any) => c.id)));
                        } else {
                          setSelectedIds(new Set());
                        }
                      },
                      style: { width: "15px", height: "15px", cursor: "pointer", accentColor: THEME.accent },
                    }),
                    "Select all"
                  ),
                  React.createElement("span", {
                    style: { fontSize: "13px", color: THEME.text, fontWeight: 500 },
                  }, `${selectedIds.size} selected`),
                  React.createElement("button", {
                    style: {
                      padding: "4px 8px", background: "transparent", color: THEME.textMuted,
                      border: `1px solid ${THEME.border}`, borderRadius: "4px",
                      cursor: "pointer", fontSize: "11px",
                    },
                    onClick: () => setSelectedIds(new Set()),
                  }, "Clear"),
                  React.createElement("div", { style: { borderLeft: `1px solid ${THEME.border}`, height: "20px", margin: "0 4px" } }),
                  React.createElement("button", {
                    style: {
                      padding: "6px 14px", background: THEME.accent, color: "#fff",
                      border: "none", borderRadius: "4px", cursor: bulkBusy ? "not-allowed" : "pointer",
                      fontSize: "12px", fontWeight: 600, opacity: bulkBusy ? 0.6 : 1,
                    },
                    disabled: bulkBusy,
                    onClick: async () => {
                      if (!confirm(`Link ${selectedIds.size} face(s) to their suggested performers?`)) return;
                      setBulkBusy(true);
                      setBulkResult(null);
                      try {
                        const res = await fetch(`${apiBase}/faces/clusters/bulk-link-suggested`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ cluster_ids: Array.from(selectedIds) }),
                        });
                        if (res.ok) {
                          const data = await res.json();
                          setBulkResult(data);
                          setSelectedIds(new Set());
                          fetchClusters();
                        } else {
                          setBulkResult({ error: await res.text() });
                        }
                      } catch (e: any) {
                        setBulkResult({ error: e.message });
                      }
                      setBulkBusy(false);
                    },
                  }, bulkBusy ? "Linking..." : "\uD83D\uDD17 Link to suggested"),
                  React.createElement("button", {
                    style: {
                      padding: "6px 14px", background: "transparent", color: THEME.danger,
                      border: `1px solid ${THEME.danger}44`, borderRadius: "4px",
                      cursor: bulkBusy ? "not-allowed" : "pointer",
                      fontSize: "12px", opacity: bulkBusy ? 0.6 : 1,
                    },
                    disabled: bulkBusy,
                    onClick: async () => {
                      if (!confirm(`Permanently delete ${selectedIds.size} face cluster(s)? This cannot be undone.`)) return;
                      setBulkBusy(true);
                      const ids = Array.from(selectedIds);
                      try {
                        await fetch(`${apiBase}/faces/clusters/bulk-delete`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ cluster_ids: ids }),
                        });
                      } catch (_e) {}
                      setSelectedIds(new Set());
                      fetchClusters();
                      setBulkBusy(false);
                    },
                  }, "Delete selected")
                )
              : null,
            // Bulk result banner
            bulkResult && !bulkResult.error
              ? React.createElement("div", {
                  style: {
                    padding: "8px 14px", marginBottom: "12px", borderRadius: "6px",
                    background: "rgba(76,175,80,0.12)", border: "1px solid rgba(76,175,80,0.3)",
                    fontSize: "12px", color: "#81c784",
                    display: "flex", alignItems: "center", gap: "8px",
                  },
                },
                  `\u2705 Linked ${bulkResult.linked}, skipped ${bulkResult.skipped}, errors ${bulkResult.errors}`,
                  React.createElement("button", {
                    style: {
                      background: "transparent", border: "none", color: THEME.textMuted,
                      cursor: "pointer", fontSize: "14px", marginLeft: "auto",
                    },
                    onClick: () => setBulkResult(null),
                  }, "\u2715")
                )
              : bulkResult?.error
              ? React.createElement("div", {
                  style: {
                    padding: "8px 14px", marginBottom: "12px", borderRadius: "6px",
                    background: "rgba(244,67,54,0.12)", border: "1px solid rgba(244,67,54,0.3)",
                    fontSize: "12px", color: "#ef5350",
                  },
                }, `\u274C Error: ${bulkResult.error}`)
              : null,
            // The grid
            React.createElement(
              "div",
              {
                style: {
                  display: "grid",
                  gridTemplateColumns: `repeat(auto-fill, minmax(${ZOOM_WIDTHS[zoomIndex]}px, 1fr))`,
                  gap: "12px",
                },
              },
              ...clusters.map((c: any) =>
                React.createElement(FaceCard, {
                  key: c.id,
                  cluster: c,
                  apiBase,
                  onAction: handleAction,
                  selected: selectedIds.has(c.id),
                  anySelected: selectedIds.size > 0,
                  onSelect: (id: number, checked: boolean) => {
                    setSelectedIds((prev: Set<number>) => {
                      const next = new Set(prev);
                      if (checked) next.add(id); else next.delete(id);
                      return next;
                    });
                  },
                })
              )
            )
          ),
      // Pagination
      totalPages > 1
        ? React.createElement(
            "div",
            {
              style: {
                display: "flex", justifyContent: "center", alignItems: "center",
                gap: "8px", marginTop: "20px",
              },
            },
            React.createElement("button", {
              style: {
                padding: "6px 12px", background: "transparent", color: page > 1 ? THEME.text : THEME.textMuted,
                border: `1px solid ${THEME.border}`, borderRadius: "4px",
                cursor: page > 1 ? "pointer" : "not-allowed",
              },
              onClick: () => { if (page > 1) setPage(page - 1); },
              disabled: page <= 1,
            }, "\u25c0"),
            React.createElement("span", { style: { color: THEME.textMuted, fontSize: "13px" } },
              `Page ${page} of ${totalPages}`
            ),
            React.createElement("button", {
              style: {
                padding: "6px 12px", background: "transparent", color: page < totalPages ? THEME.text : THEME.textMuted,
                border: `1px solid ${THEME.border}`, borderRadius: "4px",
                cursor: page < totalPages ? "pointer" : "not-allowed",
              },
              onClick: () => { if (page < totalPages) setPage(page + 1); },
              disabled: page >= totalPages,
            }, "\u25b6"),
            // Rows per page selector
            React.createElement("select", {
              value: rowsPerPage,
              onChange: (e: any) => { setRowsPerPage(Number(e.target.value)); setPage(1); },
              style: {
                padding: "4px 6px", background: THEME.bgCard, color: THEME.text,
                border: `1px solid ${THEME.border}`, borderRadius: "4px",
                fontSize: "12px", cursor: "pointer", marginLeft: "8px",
              },
            },
              ...[3, 4, 5, 6, 8, 10].map(n =>
                React.createElement("option", { key: n, value: n }, `${n} rows`)
              )
            ),
            // Zoom slider
            React.createElement("input", {
              type: "range", min: 0, max: 5, value: zoomIndex,
              onChange: (e: any) => { setZoomIndex(Number(e.target.value)); setPage(1); },
              style: { width: "60px", marginLeft: "12px", cursor: "pointer" },
            })
          )
        : null,
      // PerformerLinkDialog
      linkDialog
        ? React.createElement(PerformerLinkDialog, {
            cluster: linkDialog,
            apiBase,
            onClose: () => setLinkDialog(null),
            onLinked: () => { setLinkDialog(null); fetchClusters(); },
          })
        : null,
      // MergeDialog
      mergeDialog
        ? React.createElement(MergeDialog, {
            cluster: mergeDialog,
            apiBase,
            onClose: () => setMergeDialog(null),
            onMerged: () => { setMergeDialog(null); fetchClusters(); },
          })
        : null,
      // CreatePerformerDialog
      createPerformerDialog
        ? React.createElement(CreatePerformerDialog, {
            cluster: createPerformerDialog,
            apiBase,
            onClose: () => setCreatePerformerDialog(null),
            onCreated: () => { setCreatePerformerDialog(null); fetchClusters(); },
          })
        : null
    );
  }

  // ---------- Register ----------

  w.FacesHub = FacesHub;
  console.log("[FacesHub] Registered window.FacesHub");
})();
