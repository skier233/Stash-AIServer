/**
 * PerformerFacesPanel — Adds a "Faces" tab to the performer detail page.
 *
 * Stash's PerformerTabs is NOT a PatchComponent, so there are no tab-level
 * patch points.  Instead we use:
 *   - patch.after("PerformerPage") to detect performer renders and get the
 *     performer ID from props
 *   - DOM injection to add a "Faces" nav-link into the existing #performer-tabs
 *     navigation bar and a companion content pane
 *
 * When the injected "Faces" tab is clicked the React-managed .tab-content is
 * hidden and our own pane is shown.  When any native Stash tab is clicked the
 * reverse happens via a MutationObserver.
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
    text: "#eee",
    textMuted: "#888",
    accent: "#4caf50",
  };

  const LOG = "[PerformerFacesPanel]";
  const NAV_ID = "ai-faces-tab-nav";
  const PANE_ID = "ai-faces-tab-pane";
  const ZOOM_WIDTHS = [120, 150, 180, 230, 300, 380];

  function getApiBase(): string {
    const fn = w.AIDefaultBackendBase;
    const base = fn ? fn() : "";
    return base ? `${base}/api/v1/plugins/skier_aitagging` : "";
  }

  // ---------- FaceCard (matches FacesHub style) ----------

  function FaceCard(props: { cluster: any; apiBase: string; cardWidth: number }) {
    const { cluster, apiBase, cardWidth } = props;
    const [hovered, setHovered] = useState(false);
    const [thumbIdx, setThumbIdx] = useState(0);
    const [thumbCount, setThumbCount] = useState(1);

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

    const thumbUrl = `${apiBase}/faces/clusters/${cluster.id}/thumbnail?size=200&index=${thumbIdx}&pad=0.2`;
    const totalAppearances = (cluster.scene_count || 0) + (cluster.image_count || 0);

    return React.createElement(
      "a",
      {
        href: `/plugins/ai-faces?id=${cluster.id}`,
        style: {
          display: "flex",
          flexDirection: "column",
          textDecoration: "none",
          width: `${cardWidth}px`,
          background: THEME.bgCard,
          borderRadius: "8px",
          border: `1px solid ${hovered ? THEME.accent : THEME.border}`,
          overflow: "hidden",
          transition: "border-color 0.15s",
        },
        onMouseEnter: () => setHovered(true),
        onMouseLeave: () => setHovered(false),
      },
      // Thumbnail with cycling
      React.createElement("div", {
        style: { position: "relative", overflow: "hidden" },
      },
        React.createElement("img", {
          src: thumbUrl,
          style: { width: "100%", aspectRatio: "1", objectFit: "cover", display: "block" },
          loading: "lazy",
          onError: (e: any) => { e.currentTarget.style.display = "none"; },
        }),
        // Left/Right arrows (only if >1 thumbnail)
        thumbCount > 1
          ? React.createElement("div", {
              style: {
                position: "absolute", bottom: "0", left: "0", right: "0",
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "4px", background: "linear-gradient(transparent, rgba(0,0,0,0.5))",
              },
            },
              React.createElement("button", {
                style: {
                  background: "rgba(0,0,0,0.55)", border: "none", color: "#fff",
                  borderRadius: "50%", width: "20px", height: "20px", cursor: "pointer",
                  fontSize: "10px", display: "flex", alignItems: "center", justifyContent: "center",
                  padding: 0, lineHeight: 1,
                },
                onClick: (e: any) => { e.preventDefault(); e.stopPropagation(); setThumbIdx((p: number) => (p - 1 + thumbCount) % thumbCount); },
              }, "\u25C0"),
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
                  padding: 0, lineHeight: 1,
                },
                onClick: (e: any) => { e.preventDefault(); e.stopPropagation(); setThumbIdx((p: number) => (p + 1) % thumbCount); },
              }, "\u25B6")
            )
          : null
      ),
      // Info area
      React.createElement("div", { style: { padding: "8px 10px" } },
        // Label
        React.createElement("div", {
          style: {
            color: THEME.text, fontWeight: 500, fontSize: "13px",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          },
        }, cluster.label || `Face #${cluster.id}`),
        // Appearances
        React.createElement("div", {
          style: { color: THEME.textMuted, fontSize: "11px", marginTop: "2px" },
        },
          [
            cluster.scene_count ? `${cluster.scene_count}s` : null,
            cluster.image_count ? `${cluster.image_count}i` : null,
          ].filter(Boolean).join(", ") || "No appearances",
          totalAppearances > 0 ? ` \u00b7 ${totalAppearances} total` : "",
          cluster.quality_score != null ? ` \u00b7 Q: ${cluster.quality_score.toFixed(1)}` : ""
        ),
        // Rating widget
        w.RatingWidgetWithAPI
          ? React.createElement("div", { style: { marginTop: "4px" } },
              React.createElement(w.RatingWidgetWithAPI, {
                entityType: "face_cluster",
                entityId: cluster.id,
                initialValue: cluster.rating100 ?? null,
                compact: true,
              })
            )
          : null
      )
    );
  }

  // ---------- Face Grid Component ----------

  function PerformerFacesPanel(props: { performerId: number }) {
    const { performerId } = props;
    const apiBase = getApiBase();
    const [clusters, setClusters] = useState([] as any[]);
    const [loading, setLoading] = useState(true);
    const [similarPerformers, setSimilarPerformers] = useState([] as any[]);
    const [similarLoading, setSimilarLoading] = useState(false);

    const fetchClusters = useCallback(async () => {
      if (!apiBase || !performerId) return;
      setLoading(true);
      try {
        const res = await fetch(
          `${apiBase}/faces/clusters?performer_id=${performerId}&per_page=100`
        );
        if (res.ok) {
          const data = await res.json();
          setClusters(data.clusters || []);
        }
      } catch (e) {
        console.error(LOG, "fetch failed:", e);
      }
      setLoading(false);
    }, [apiBase, performerId]);

    const fetchSimilarPerformers = useCallback(async () => {
      if (!apiBase || !performerId) return;
      setSimilarLoading(true);
      try {
        const res = await fetch(
          `${apiBase}/faces/performers/${performerId}/similar-by-face?limit=12`
        );
        if (res.ok) {
          const data = await res.json();
          setSimilarPerformers(data.matches || []);
        }
      } catch (e) {
        console.error(LOG, "similar fetch failed:", e);
      }
      setSimilarLoading(false);
    }, [apiBase, performerId]);

    useEffect(() => { fetchClusters(); fetchSimilarPerformers(); }, [fetchClusters, fetchSimilarPerformers]);

    if (!apiBase) {
      return React.createElement("div", {
        style: { color: THEME.textMuted, padding: "20px" },
      }, "AI backend not configured.");
    }

    if (loading) {
      return React.createElement("div", {
        style: { color: THEME.textMuted, padding: "20px" },
      }, "Loading faces...");
    }

    if (clusters.length === 0) {
      return React.createElement("div", {
        style: { color: THEME.textMuted, padding: "20px" },
      }, "No face clusters found for this performer.");
    }

    return React.createElement(
      "div",
      { style: { padding: "20px 0" } },
      // Face clusters grid
      React.createElement(
        "div",
        {
          style: {
            display: "grid",
            gridTemplateColumns: `repeat(auto-fill, minmax(${ZOOM_WIDTHS[1]}px, 1fr))`,
            gap: "12px",
          },
        },
        ...clusters.map((c: any) =>
          React.createElement(FaceCard, {
            key: c.id,
            cluster: c,
            apiBase,
            cardWidth: ZOOM_WIDTHS[1],
          })
        )
      ),
      // Similar Performers by Face section
      React.createElement("h3", {
        style: { fontSize: "16px", fontWeight: 600, margin: "28px 0 12px 0", color: THEME.text },
      }, "Similar Performers by Face"),
      similarLoading
        ? React.createElement("div", { style: { color: THEME.textMuted, fontSize: "13px" } }, "Loading similar performers...")
        : similarPerformers.length === 0
          ? React.createElement("div", { style: { color: THEME.textMuted, fontSize: "13px" } }, "No similar performers found.")
          : React.createElement("div", {
              style: {
                display: "grid",
                gridTemplateColumns: `repeat(auto-fill, minmax(${ZOOM_WIDTHS[1]}px, 1fr))`,
                gap: "12px",
              },
            },
              ...similarPerformers.map((m: any) =>
                React.createElement("a", {
                  key: m.performer_id,
                  href: `/performers/${m.performer_id}`,
                  style: {
                    display: "flex", flexDirection: "column", textDecoration: "none",
                    width: "100%", background: THEME.bgCard, borderRadius: "8px",
                    border: `1px solid ${THEME.border}`, overflow: "hidden",
                    transition: "border-color 0.15s",
                  },
                  onMouseEnter: (e: any) => { e.currentTarget.style.borderColor = THEME.accent; },
                  onMouseLeave: (e: any) => { e.currentTarget.style.borderColor = THEME.border; },
                },
                  // Performer profile image — portrait ratio, contain so nothing is cropped
                  React.createElement("div", {
                    style: {
                      width: "100%", aspectRatio: "2/3", background: "#111",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      overflow: "hidden",
                    },
                  },
                    React.createElement("img", {
                      src: `/performer/${m.performer_id}/image`,
                      style: { width: "100%", height: "100%", objectFit: "contain", display: "block" },
                      loading: "lazy",
                      onError: (e: any) => {
                        // Fall back to face cluster thumbnail (square crop is fine for face images)
                        if (m.thumbnail_url && e.currentTarget.src !== `${apiBase}${m.thumbnail_url}?size=200&pad=0.2`) {
                          e.currentTarget.src = `${apiBase}${m.thumbnail_url}?size=200&pad=0.2`;
                          e.currentTarget.style.objectFit = "cover";
                        } else {
                          (e.currentTarget.parentElement as HTMLElement).style.display = "none";
                        }
                      },
                    })
                  ),
                  React.createElement("div", { style: { padding: "8px 10px" } },
                    React.createElement("div", {
                      style: {
                        color: THEME.text, fontWeight: 500, fontSize: "13px",
                        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                      },
                    }, m.performer_name || `Performer #${m.performer_id}`),
                    React.createElement("div", {
                      style: { color: THEME.textMuted, fontSize: "11px", marginTop: "2px" },
                    }, `${Math.round(m.similarity * 100)}% face similarity`)
                  )
                )
              )
            )
    );
  }

  // ---------- DOM Tab Injection ----------

  // Renders the React face grid into a DOM node via ReactDOM
  function renderPanel(container: HTMLElement, performerId: number) {
    const el = React.createElement(PerformerFacesPanel, { performerId });
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

  let _currentPid: number | null = null;
  let _observer: MutationObserver | null = null;

  function injectFacesTab(performerId: number, attempt = 0) {
    // Same performer, already injected?
    if (
      _currentPid === performerId &&
      document.getElementById(NAV_ID) &&
      document.getElementById(PANE_ID)
    ) {
      return;
    }
    _currentPid = performerId;

    // Clean up any previous injection
    document.getElementById(NAV_ID)?.remove();
    document.getElementById(PANE_ID)?.remove();
    if (_observer) { _observer.disconnect(); _observer = null; }

    // React-Bootstrap renders the <Tabs id="performer-tabs"> as a <ul role="tablist">
    // so the element with that ID IS the nav bar, not a wrapper.
    // Fall back to querying by role/class if the ID isn't found yet.
    const tabsEl: HTMLElement | null = document.getElementById("performer-tabs");
    const navBar: HTMLElement | null =
      (tabsEl?.getAttribute("role") === "tablist" ? tabsEl : null) ??
      (tabsEl?.tagName === "UL" ? tabsEl : null) ??
      (tabsEl?.querySelector('[role="tablist"]') as HTMLElement | null) ??
      (document.querySelector('[role="tablist"].nav-tabs') as HTMLElement | null);

    if (!navBar) {
      // DOM not ready yet — retry with backoff (up to ~2 s total)
      if (attempt < 10) {
        if (attempt === 0) console.log(LOG, "Tab bar not ready, will retry...");
        setTimeout(() => injectFacesTab(performerId, attempt + 1), 150 + attempt * 100);
      } else {
        console.warn(LOG, "Could not find performer tab nav after retries");
      }
      return;
    }

    // .tab-content is a sibling of the nav bar (both children of the Tabs wrapper)
    const tabContent: HTMLElement | null =
      (navBar.parentElement?.querySelector(".tab-content") as HTMLElement | null) ??
      (document.querySelector(".performer-tabs .tab-content") as HTMLElement | null) ??
      null;

    // --- Create the nav item ---
    // Detect whether existing items use <li> or direct <a>/<button>
    const usesLi = !!navBar.querySelector("li.nav-item");

    const link = document.createElement("a");
    link.className = "nav-link";
    link.href = "#";
    link.textContent = "Faces";
    link.setAttribute("role", "tab");
    link.id = usesLi ? "" : NAV_ID;

    let navNode: HTMLElement;
    if (usesLi) {
      const li = document.createElement("li");
      li.className = "nav-item";
      li.id = NAV_ID;
      li.setAttribute("role", "presentation");
      li.appendChild(link);
      navNode = li;
    } else {
      navNode = link;
    }
    navBar.appendChild(navNode);

    // --- Create the faces pane (sibling of .tab-content, not inside it) ---
    const pane = document.createElement("div");
    pane.id = PANE_ID;
    pane.style.display = "none";
    // Insert after .tab-content so it's in the same visual position.
    // navBar.parentElement is the Tabs wrapper that also contains .tab-content.
    if (tabContent && tabContent.parentElement) {
      tabContent.parentElement.insertBefore(pane, tabContent.nextSibling);
    } else if (navBar.parentElement) {
      navBar.parentElement.appendChild(pane);
    } else {
      navBar.insertAdjacentElement("afterend", pane);
    }

    // Render the React face grid
    renderPanel(pane, performerId);

    // --- Show/hide logic ---
    function activateFacesTab() {
      // Deactivate all React-managed nav links
      navBar.querySelectorAll(".nav-link").forEach((el) => {
        if (el !== link) el.classList.remove("active");
      });
      link.classList.add("active");

      // Hide the React tab-content, show our pane
      if (tabContent) tabContent.style.display = "none";
      pane.style.display = "";
    }

    function deactivateFacesTab() {
      link.classList.remove("active");
      pane.style.display = "none";
      if (tabContent) tabContent.style.display = "";
    }

    // Click on our Faces tab
    link.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      activateFacesTab();
    });

    // Deactivate immediately when any native tab is clicked.
    // This handles the case where React's activeKey doesn't change (e.g. the
    // previously-active tab is Scenes and the user clicks Scenes again after
    // visiting Faces — React sees no state change so never re-renders, meaning
    // the MutationObserver never fires and tabContent would stay hidden).
    navBar.addEventListener("click", (e) => {
      const target = e.target as HTMLElement;
      const clickedLink = target.closest(".nav-link");
      if (clickedLink && clickedLink !== link && link.classList.contains("active")) {
        deactivateFacesTab();
      }
    });

    // Also watch for React class mutations so we catch programmatic tab switches.
    _observer = new MutationObserver(() => {
      if (!document.contains(link)) {
        // Our injected element was removed by a React re-render
        deactivateFacesTab();
      }
    });
    _observer.observe(navBar, {
      subtree: true,
      childList: true,
    });

    console.log(LOG, "Injected Faces tab for performer", performerId);
  }

  // ---------- Re-injection on React re-render ----------
  // React-Bootstrap Tabs re-renders the <ul>/<nav> when active tab changes,
  // which may remove our injected nav item.  We observe the container and
  // re-inject when our element disappears.

  function setupReinjectObserver() {
    const bodyObs = new MutationObserver(() => {
      if (_currentPid && !document.getElementById(NAV_ID)) {
        // Our nav was removed by a React re-render — re-inject
        injectFacesTab(_currentPid);
      }
    });
    bodyObs.observe(document.body, { childList: true, subtree: true });
  }

  // ---------- Integration: use patch.after("PerformerPage") ----------
  // PerformerPage IS a PatchComponent.  patch.after receives (props, result)
  // and must return result.  We use it purely as a trigger for DOM injection.

  let integrated = false;

  if (PluginApi.patch?.after) {
    try {
      // patch.after calls fn(...componentCallArgs, result) where result is the JSX.
      // React calls components as Component(props) or Component(props, ref), so
      // the last argument is always the JSX result — use rest params to be safe.
      PluginApi.patch.after("PerformerPage", function (...args: any[]) {
        const result = args[args.length - 1]; // last arg = rendered JSX
        const props = args[0];
        const pid = props?.performer?.id;
        if (pid) {
          const performerId = parseInt(String(pid), 10);
          // Schedule injection after React commits the DOM update
          setTimeout(() => injectFacesTab(performerId), 100);
        }
        return result; // return original JSX unchanged
      });
      integrated = true;
      console.log(LOG, "Registered patch.after('PerformerPage') for DOM injection");
    } catch (e) {
      console.warn(LOG, "patch.after('PerformerPage') failed:", e);
    }
  }

  // Fallback: URL-based detection if patch.after is unavailable
  if (!integrated) {
    console.log(LOG, "patch.after not available, using URL-based detection");

    function checkUrl() {
      const match = window.location.pathname.match(/\/performers\/(\d+)/);
      if (match) {
        const pid = parseInt(match[1], 10);
        setTimeout(() => injectFacesTab(pid), 300);
      } else {
        _currentPid = null;
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

  // Start the re-injection observer
  setupReinjectObserver();

  w.PerformerFacesPanel = PerformerFacesPanel;
  console.log(LOG, "Registered window.PerformerFacesPanel");
})();
