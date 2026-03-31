/**
 * RatingWidget — Reusable rating component that mirrors Stash's rating UI.
 *
 * Reads Stash's ratingSystemOptions from the GraphQL config to render either
 * a star-based or decimal-based rating control, using the same look/feel as
 * the host Stash application.
 *
 * Props:
 *   entityType  - e.g. "face_cluster", "scene"
 *   entityId    - the entity's ID (string or number)
 *   ratingKey   - defaults to "default"; supports multiple rating dimensions
 *   value       - current rating100 value (0-100 or null)
 *   onChange    - callback when the user sets/clears a rating: (value: number | null) => void
 *   compact     - if true, renders a smaller inline version
 *   readOnly    - if true, disables interaction
 *
 * Registers: window.RatingWidget
 * Also exports: window.RatingUtils (helpers for other components)
 */
(function () {
  const w = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) {
    console.warn("[RatingWidget] PluginApi or React not available");
    return;
  }
  const React = PluginApi.React;
  const { useState, useEffect, useCallback, useRef, useMemo } = React;

  // ---------- Types ----------

  interface RatingSystemOptions {
    type: "stars" | "decimal";
    starPrecision?: "full" | "half" | "quarter" | "tenth";
  }

  interface RatingWidgetProps {
    entityType: string;
    entityId: string | number;
    ratingKey?: string;
    value: number | null;         // rating100 (0-100) or null
    onChange?: (value: number | null) => void;
    compact?: boolean;
    readOnly?: boolean;
  }

  // ---------- Config Cache ----------

  let _cachedConfig: RatingSystemOptions | null = null;
  let _configPromise: Promise<RatingSystemOptions> | null = null;

  async function fetchRatingConfig(): Promise<RatingSystemOptions> {
    if (_cachedConfig) return _cachedConfig;
    if (_configPromise) return _configPromise;
    _configPromise = (async () => {
      try {
        const res = await fetch("/graphql", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: `query { configuration { ui } }`,
          }),
        });
        if (res.ok) {
          const json = await res.json();
          let ui = json?.data?.configuration?.ui;
          // Stash may return ui as a JSON string or as an object
          if (typeof ui === "string") {
            try { ui = JSON.parse(ui); } catch (_e) { /* ignore parse errors */ }
          }
          if (ui?.ratingSystemOptions) {
            _cachedConfig = ui.ratingSystemOptions as RatingSystemOptions;
            return _cachedConfig;
          }
        }
      } catch (e) {
        console.warn("[RatingWidget] Failed to fetch rating config:", e);
      }
      // Default: 5-star full precision
      _cachedConfig = { type: "stars", starPrecision: "full" };
      return _cachedConfig;
    })();
    return _configPromise;
  }

  // ---------- Conversion Helpers ----------

  function rating100ToStars(rating100: number): number {
    return rating100 / 20; // 0-100 → 0-5
  }

  function starsToRating100(stars: number): number {
    return Math.round(stars * 20); // 0-5 → 0-100
  }

  function rating100ToDecimal(rating100: number): number {
    return rating100 / 10; // 0-100 → 0.0-10.0
  }

  function decimalToRating100(decimal: number): number {
    return Math.round(decimal * 10); // 0.0-10.0 → 0-100
  }

  function getStarPrecisionStep(precision: string): number {
    switch (precision) {
      case "half": return 0.5;
      case "quarter": return 0.25;
      case "tenth": return 0.1;
      default: return 1; // "full"
    }
  }

  function snapToStep(value: number, step: number): number {
    return Math.round(value / step) * step;
  }

  // ---------- Star Rating Component ----------

  const STAR_COLOR_FILLED = "#f5c518";
  const STAR_COLOR_EMPTY = "#666";
  const STAR_COLOR_HOVER = "#ffd740";

  function StarRating(props: {
    value: number;          // 0-5
    step: number;           // star precision step
    onChange?: (v: number) => void;
    onClear?: () => void;
    compact?: boolean;
    readOnly?: boolean;
  }) {
    const { value, step, onChange, onClear, compact, readOnly } = props;
    const [hoverValue, setHoverValue] = useState(null as number | null);
    const containerRef = useRef(null as any);
    const starCount = 5;
    const starSize = compact ? 16 : 22;

    const getStarValue = useCallback((e: any, starIndex: number) => {
      if (!containerRef.current) return starIndex + 1;
      const starEls = containerRef.current.querySelectorAll("[data-star]");
      if (!starEls[starIndex]) return starIndex + 1;
      const rect = starEls[starIndex].getBoundingClientRect();
      const x = e.clientX - rect.left;
      const fraction = Math.max(0, Math.min(1, x / rect.width));
      const raw = starIndex + fraction;
      return Math.max(step, snapToStep(raw, step));
    }, [step]);

    const handleMouseMove = useCallback((e: any, starIndex: number) => {
      if (readOnly) return;
      setHoverValue(getStarValue(e, starIndex));
    }, [readOnly, getStarValue]);

    const handleClick = useCallback((e: any, starIndex: number) => {
      if (readOnly || !onChange) return;
      const newValue = getStarValue(e, starIndex);
      // Click on same value = clear
      if (Math.abs(newValue - value) < step / 2) {
        if (onClear) onClear();
      } else {
        onChange(newValue);
      }
    }, [readOnly, onChange, onClear, value, step, getStarValue]);

    const displayValue = hoverValue ?? value;

    return React.createElement(
      "span",
      {
        ref: containerRef,
        style: {
          display: "inline-flex",
          alignItems: "center",
          gap: compact ? "1px" : "2px",
          cursor: readOnly ? "default" : "pointer",
          lineHeight: 1,
        },
        onMouseLeave: () => setHoverValue(null),
      },
      ...Array.from({ length: starCount }, (_: any, i: number) => {
        const fill = Math.max(0, Math.min(1, displayValue - i));
        return React.createElement(
          "span",
          {
            key: i,
            "data-star": i,
            style: {
              position: "relative",
              display: "inline-block",
              width: `${starSize}px`,
              height: `${starSize}px`,
              fontSize: `${starSize}px`,
              lineHeight: 1,
              userSelect: "none",
            },
            onMouseMove: (e: any) => handleMouseMove(e, i),
            onClick: (e: any) => handleClick(e, i),
          },
          // Empty star (background)
          React.createElement("span", {
            style: {
              position: "absolute",
              top: 0,
              left: 0,
              color: STAR_COLOR_EMPTY,
            },
          }, "\u2605"),
          // Filled star (clipped)
          fill > 0
            ? React.createElement("span", {
                style: {
                  position: "absolute",
                  top: 0,
                  left: 0,
                  color: hoverValue != null ? STAR_COLOR_HOVER : STAR_COLOR_FILLED,
                  clipPath: `inset(0 ${(1 - fill) * 100}% 0 0)`,
                  transition: hoverValue != null ? "none" : "clip-path 0.15s",
                },
              }, "\u2605")
            : null
        );
      }),
      // Numeric display for non-compact mode
      !compact && value > 0
        ? React.createElement("span", {
            style: {
              marginLeft: "6px",
              fontSize: "12px",
              color: "#999",
              minWidth: "24px",
            },
          }, (hoverValue ?? value).toFixed(step < 1 ? 1 : 0))
        : null
    );
  }

  // ---------- Decimal Rating Component ----------

  function DecimalRating(props: {
    value: number;          // 0.0-10.0
    onChange?: (v: number) => void;
    onClear?: () => void;
    compact?: boolean;
    readOnly?: boolean;
  }) {
    const { value, onChange, onClear, compact, readOnly } = props;
    const [editing, setEditing] = useState(false);
    const [inputVal, setInputVal] = useState("");

    const displayText = value > 0 ? value.toFixed(1) : "\u2014";

    if (editing && !readOnly) {
      return React.createElement("input", {
        type: "number",
        min: 0,
        max: 10,
        step: 0.1,
        value: inputVal,
        autoFocus: true,
        style: {
          width: compact ? "48px" : "60px",
          padding: compact ? "2px 4px" : "4px 6px",
          background: "#111",
          color: "#eee",
          border: "1px solid #555",
          borderRadius: "4px",
          fontSize: compact ? "12px" : "14px",
          textAlign: "center" as any,
          outline: "none",
        },
        onFocus: (e: any) => e.target.select(),
        onChange: (e: any) => setInputVal(e.target.value),
        onBlur: () => {
          const parsed = parseFloat(inputVal);
          if (!isNaN(parsed) && parsed >= 0 && parsed <= 10) {
            if (onChange) onChange(parsed);
          } else if (inputVal === "" || inputVal === "0") {
            if (onClear) onClear();
          }
          setEditing(false);
        },
        onKeyDown: (e: any) => {
          if (e.key === "Enter") e.target.blur();
          if (e.key === "Escape") setEditing(false);
        },
      });
    }

    return React.createElement(
      "span",
      {
        style: {
          display: "inline-flex",
          alignItems: "center",
          gap: "4px",
          cursor: readOnly ? "default" : "pointer",
          padding: compact ? "2px 6px" : "3px 8px",
          borderRadius: "4px",
          background: value > 0 ? "#f5c51822" : "transparent",
          border: value > 0 ? "1px solid #f5c51844" : "1px solid #333",
          fontSize: compact ? "12px" : "14px",
          color: value > 0 ? "#f5c518" : "#666",
          fontWeight: value > 0 ? 600 : 400,
          transition: "all 0.15s",
          minWidth: compact ? "36px" : "44px",
          textAlign: "center" as any,
          justifyContent: "center",
        },
        onClick: () => {
          if (!readOnly) {
            setInputVal(value > 0 ? value.toFixed(1) : "");
            setEditing(true);
          }
        },
        title: readOnly ? `${displayText} / 10` : "Click to set rating",
      },
      displayText
    );
  }

  // ---------- Main Rating Widget ----------

  function RatingWidget(props: RatingWidgetProps) {
    const { entityType, entityId, ratingKey, value, onChange, compact, readOnly } = props;
    const [config, setConfig] = useState(_cachedConfig as RatingSystemOptions | null);

    useEffect(() => {
      if (!config) {
        fetchRatingConfig().then((c) => setConfig(c));
      }
    }, [config]);

    const handleStarChange = useCallback((starVal: number) => {
      if (onChange) onChange(starsToRating100(starVal));
    }, [onChange]);

    const handleDecimalChange = useCallback((decVal: number) => {
      if (onChange) onChange(decimalToRating100(decVal));
    }, [onChange]);

    const handleClear = useCallback(() => {
      if (onChange) onChange(null);
    }, [onChange]);

    if (!config) return null; // Loading config

    if (config.type === "decimal") {
      const decValue = value != null ? rating100ToDecimal(value) : 0;
      return React.createElement(DecimalRating, {
        value: decValue,
        onChange: handleDecimalChange,
        onClear: handleClear,
        compact,
        readOnly,
      });
    }

    // Stars mode
    const step = getStarPrecisionStep(config.starPrecision || "full");
    const starValue = value != null ? rating100ToStars(value) : 0;
    return React.createElement(StarRating, {
      value: starValue,
      step,
      onChange: handleStarChange,
      onClear: handleClear,
      compact,
      readOnly,
    });
  }

  // ---------- API-backed wrapper (auto-persists to backend) ----------

  function RatingWidgetWithAPI(props: {
    entityType: string;
    entityId: string | number;
    ratingKey?: string;
    initialValue: number | null;
    compact?: boolean;
    readOnly?: boolean;
  }) {
    const { entityType, entityId, ratingKey = "default", initialValue, compact, readOnly } = props;
    const [value, setValue] = useState(initialValue as number | null);
    const [saving, setSaving] = useState(false);

    // Sync if initialValue changes from parent
    useEffect(() => { setValue(initialValue); }, [initialValue]);

    function getApiBase(): string {
      const fn = w.AIDefaultBackendBase;
      const base = fn ? fn() : "";
      return base ? `${base}/api/v1` : "";
    }

    const handleChange = useCallback(async (newValue: number | null) => {
      const prev = value;
      setValue(newValue); // Optimistic update
      setSaving(true);
      const apiBase = getApiBase();
      if (!apiBase) { setSaving(false); return; }
      try {
        if (newValue != null) {
          const res = await fetch(
            `${apiBase}/ratings/${encodeURIComponent(entityType)}/${encodeURIComponent(String(entityId))}/${encodeURIComponent(ratingKey)}`,
            {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ value: newValue }),
            }
          );
          if (!res.ok) {
            console.error("[RatingWidget] PUT failed:", res.status);
            setValue(prev); // Revert
          }
        } else {
          const res = await fetch(
            `${apiBase}/ratings/${encodeURIComponent(entityType)}/${encodeURIComponent(String(entityId))}/${encodeURIComponent(ratingKey)}`,
            { method: "DELETE" }
          );
          if (!res.ok && res.status !== 404) {
            console.error("[RatingWidget] DELETE failed:", res.status);
            setValue(prev);
          }
        }
      } catch (e) {
        console.error("[RatingWidget] API error:", e);
        setValue(prev);
      }
      setSaving(false);
    }, [entityType, entityId, ratingKey, value]);

    return React.createElement(
      "span",
      { style: { opacity: saving ? 0.6 : 1, transition: "opacity 0.15s" } },
      React.createElement(RatingWidget, {
        entityType,
        entityId: String(entityId),
        ratingKey,
        value,
        onChange: readOnly ? undefined : handleChange,
        compact,
        readOnly,
      })
    );
  }

  // ---------- Exports ----------

  w.RatingWidget = RatingWidget;
  w.RatingWidgetWithAPI = RatingWidgetWithAPI;
  w.RatingUtils = {
    fetchRatingConfig,
    rating100ToStars,
    starsToRating100,
    rating100ToDecimal,
    decimalToRating100,
    getStarPrecisionStep,
  };
  console.log("[RatingWidget] Registered window.RatingWidget, window.RatingWidgetWithAPI, window.RatingUtils");
})();
