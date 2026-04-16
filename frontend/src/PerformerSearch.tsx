/**
 * PerformerSearch — Generic reusable performer search widget.
 *
 * Supports pre-populated suggestions (from co-occurrence data) and a
 * debounced GraphQL typeahead search against Stash's findPerformers.
 *
 * Registers: window.PerformerSearchWidget
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[PerformerSearch] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useRef, useCallback } = React;

  // ---------- Types ----------

  interface PerformerSuggestion {
    performer_id: number;
    performer_name: string;
    performer_image: string;
    scene_count?: number;
    image_count?: number;
    co_occurrence_count?: number;
    confidence?: string;
    solo_scene_count?: number;
    solo_image_count?: number;
    already_linked?: boolean;
  }

  interface PerformerResult {
    id: number;
    name: string;
    image_path?: string;
  }

  interface PerformerSearchProps {
    onSelect: (performer: PerformerResult) => void;
    onHover?: (performer: PerformerResult | null) => void;
    placeholder?: string;
    suggestedPerformers?: PerformerSuggestion[];
    selectedPerformerId?: number;
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

  // ---------- Component ----------

  function PerformerSearch(props: PerformerSearchProps) {
    const { onSelect, onHover, placeholder, suggestedPerformers, selectedPerformerId } = props;
    const [searchTerm, setSearchTerm] = useState("");
    const [searchResults, setSearchResults] = useState([] as PerformerResult[]);
    const [loading, setLoading] = useState(false);
    const debounceRef = useRef(null as any);

    const doSearch = useCallback(async (term: string) => {
      if (!term.trim()) {
        setSearchResults([]);
        return;
      }
      setLoading(true);
      try {
        const gql = `query FindPerformers($term: String!) {
          findPerformers(
            filter: { per_page: 10 }
            performer_filter: { name: { value: $term, modifier: INCLUDES } }
          ) {
            performers { id name image_path }
          }
        }`;
        const res = await fetch("/graphql", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: gql, variables: { term } }),
        });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const json = await res.json();
        const performers = (json?.data?.findPerformers?.performers || []).map(
          (p: any) => ({ id: parseInt(p.id, 10), name: p.name, image_path: p.image_path || "" })
        );
        setSearchResults(performers);
      } catch (e) {
        console.error("[PerformerSearch] Search failed:", e);
        setSearchResults([]);
      } finally {
        setLoading(false);
      }
    }, []);

    const handleInputChange = useCallback(
      (e: any) => {
        const value = e.target.value;
        setSearchTerm(value);
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => doSearch(value), 200);
      },
      [doSearch]
    );

    // Cleanup debounce on unmount
    useEffect(() => {
      return () => {
        if (debounceRef.current) clearTimeout(debounceRef.current);
      };
    }, []);

    const confidenceBadge = (conf: string | undefined) => {
      if (!conf) return null;
      const colors: any = { high: THEME.accent, possible: THEME.warning, suggested: THEME.textMuted };
      const labels: any = { high: "HIGH", possible: "POSSIBLE", suggested: "SUGGESTED" };
      return React.createElement(
        "span",
        {
          style: {
            fontSize: "10px",
            fontWeight: 600,
            color: colors[conf] || THEME.textMuted,
            border: `1px solid ${colors[conf] || THEME.textMuted}`,
            borderRadius: "3px",
            padding: "1px 5px",
            marginLeft: "6px",
            textTransform: "uppercase" as any,
          },
        },
        labels[conf] || conf
      );
    };

    const getPerformerImageUrl = (imgPath: string) => {
      if (!imgPath) return "";
      if (imgPath.startsWith("http")) return imgPath;
      // Local Stash path like "/performer/42/image"
      return imgPath;
    };

    const suggestionItem = (s: PerformerSuggestion) => {
      const imgUrl = getPerformerImageUrl(s.performer_image);
      return React.createElement(
        "div",
        {
          key: `suggestion-${s.performer_id}`,
          style: {
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "6px 8px",
            borderRadius: "4px",
            cursor: "pointer",
            background: s.performer_id === selectedPerformerId ? THEME.bgHover : THEME.bgCard,
            border: s.performer_id === selectedPerformerId
              ? `2px solid ${THEME.accent}`
              : `1px solid ${THEME.border}`,
            marginBottom: "4px",
            opacity: s.already_linked ? 0.5 : 1,
          },
          onClick: () =>
            onSelect({
              id: s.performer_id,
              name: s.performer_name,
              image_path: s.performer_image,
            }),
          onMouseEnter: () =>
            onHover?.({
              id: s.performer_id,
              name: s.performer_name,
              image_path: s.performer_image,
            }),
          onMouseLeave: () => onHover?.(null),
        },
        imgUrl
          ? React.createElement("img", {
              src: imgUrl,
              style: {
                width: 32,
                height: 32,
                borderRadius: "50%",
                objectFit: "cover",
                flexShrink: 0,
              },
              loading: "lazy",
            })
          : React.createElement("div", {
              style: {
                width: 32,
                height: 32,
                borderRadius: "50%",
                background: THEME.border,
                flexShrink: 0,
              },
            }),
        React.createElement(
          "div",
          { style: { flex: 1, minWidth: 0 } },
          React.createElement(
            "div",
            {
              style: {
                color: THEME.text,
                fontWeight: 500,
                fontSize: "13px",
                display: "flex",
                alignItems: "center",
              },
            },
            s.performer_name,
            confidenceBadge(s.confidence),
            s.already_linked
              ? React.createElement(
                  "span",
                  {
                    style: {
                      fontSize: "10px",
                      color: THEME.warning,
                      marginLeft: "6px",
                    },
                  },
                  "(linked elsewhere)"
                )
              : null
          ),
          React.createElement(
            "div",
            { style: { color: THEME.textMuted, fontSize: "11px" } },
            [
              s.scene_count ? `${s.scene_count} scene${s.scene_count !== 1 ? "s" : ""}` : null,
              s.image_count ? `${s.image_count} image${s.image_count !== 1 ? "s" : ""}` : null,
            ]
              .filter(Boolean)
              .join(", "),
            s.solo_scene_count || s.solo_image_count
              ? ` (solo: ${[
                  s.solo_scene_count ? `${s.solo_scene_count}s` : null,
                  s.solo_image_count ? `${s.solo_image_count}i` : null,
                ]
                  .filter(Boolean)
                  .join(", ")})`
              : ""
          )
        )
      );
    };

    const searchResultItem = (p: PerformerResult) => {
      const imgUrl = getPerformerImageUrl(p.image_path || "");
      return React.createElement(
        "div",
        {
          key: `search-${p.id}`,
          style: {
            display: "flex",
            alignItems: "center",
            gap: "8px",
            padding: "6px 8px",
            borderRadius: "4px",
            cursor: "pointer",
            background: p.id === selectedPerformerId ? THEME.bgHover : THEME.bgCard,
            border: p.id === selectedPerformerId
              ? `2px solid ${THEME.accent}`
              : `1px solid transparent`,
            marginBottom: "2px",
          },
          onClick: () => onSelect(p),
          onMouseEnter: () => onHover?.(p),
          onMouseLeave: () => onHover?.(null),
        },
        imgUrl
          ? React.createElement("img", {
              src: imgUrl,
              style: {
                width: 28,
                height: 28,
                borderRadius: "50%",
                objectFit: "cover",
                flexShrink: 0,
              },
              loading: "lazy",
            })
          : React.createElement("div", {
              style: {
                width: 28,
                height: 28,
                borderRadius: "50%",
                background: THEME.border,
                flexShrink: 0,
              },
            }),
        React.createElement(
          "span",
          { style: { color: THEME.text, fontSize: "13px" } },
          p.name
        )
      );
    };

    return React.createElement(
      "div",
      { style: { width: "100%" } },
      // Suggestions section
      suggestedPerformers && suggestedPerformers.length > 0
        ? React.createElement(
            "div",
            { style: { marginBottom: "8px" } },
            React.createElement(
              "div",
              {
                style: {
                  fontSize: "11px",
                  color: THEME.textMuted,
                  marginBottom: "4px",
                  textTransform: "uppercase" as any,
                  letterSpacing: "0.5px",
                },
              },
              "Suggestions"
            ),
            suggestedPerformers.map(suggestionItem)
          )
        : null,
      // Search input
      React.createElement(
        "div",
        {
          style: {
            display: "flex",
            alignItems: "center",
            gap: "6px",
            marginBottom: searchResults.length ? "4px" : 0,
          },
        },
        React.createElement("input", {
          type: "text",
          value: searchTerm,
          onChange: handleInputChange,
          placeholder: placeholder || "Search performers...",
          style: {
            flex: 1,
            padding: "6px 10px",
            background: THEME.bgInput,
            border: `1px solid ${THEME.border}`,
            borderRadius: "4px",
            color: THEME.text,
            fontSize: "13px",
            outline: "none",
          },
        }),
        loading
          ? React.createElement(
              "span",
              { style: { color: THEME.textMuted, fontSize: "12px" } },
              "..."
            )
          : null
      ),
      // Search results dropdown
      searchResults.length > 0
        ? React.createElement(
            "div",
            {
              style: {
                maxHeight: "200px",
                overflowY: "auto",
                border: `1px solid ${THEME.border}`,
                borderRadius: "4px",
                padding: "4px",
                background: THEME.bg,
              },
            },
            searchResults.map(searchResultItem)
          )
        : searchTerm.trim() && !loading
        ? React.createElement(
            "div",
            { style: { color: THEME.textMuted, fontSize: "12px", padding: "4px" } },
            "No results"
          )
        : null
    );
  }

  // ---------- Register ----------

  w.PerformerSearchWidget = PerformerSearch;
  console.log("[PerformerSearch] Registered window.PerformerSearchWidget");
})();
