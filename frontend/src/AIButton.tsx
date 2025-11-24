// AIButton (MinimalAIButton)
// Contract:
//  - Provides a single floating/contextual button that lists available AI actions for current page context.
//  - No polling: actions fetched on open + context change; task progress via shared websocket + global cache.
//  - Supports multiple concurrent parent/controller tasks; shows aggregate count or single progress ring.
//  - Exposes global aliases: window.AIButton & window.MinimalAIButton for integrations to mount.
//  - Debug logging gated by window.AIDebug = true.
//  - Assumes backend REST under /api/v1 and websocket under /api/v1/ws/tasks (with legacy fallback /ws/tasks).
//  - Only parent/controller task IDs are tracked in activeTasks; child task events still drive progress inference.

interface PageContext {
  page: string;
  entityId: string | null;
  isDetailView: boolean;
  contextLabel: string;
  detailLabel: string;
  selectedIds?: string[];
  visibleIds?: string[];
}

interface AIAction {
  id: string;
  label: string;
  service: string;
  result_kind?: string;
}

// Result types for AI actions
interface SingleSceneTagResult {
  scene_id: number;
  status: string;
  message: string;
  scene_tags?: {
    applied: (string | number)[];
    removed: (string | number)[];
  };
  summary?: string;
  markers_applied?: number;
  tags_applied: number;
  tags_removed?: number;
  processed_ids?: number[];
  failed_ids?: number[];
}

interface MultipleScenesTagResult {
  status: string;
  message: string;
  scenes_requested?: number;
  scenes_completed: number;
  scenes_failed?: number;
  spawned?: string[];
  count?: number;
  held?: boolean;
}

type AITaskResult = SingleSceneTagResult | MultipleScenesTagResult | any;

interface AITaskEvent {
  id: string;
  status: string;
  result?: AITaskResult;
  error?: string;
  group_id?: string | null;
  action_id?: string;
  service?: string;
}

// ---- Toast notification system ----
const showToast = (
  message: string,
  type: "success" | "error" = "success",
  link?: { url: string; text: string },
) => {
  const toastId = `ai-toast-${Date.now()}`;
  const toast = document.createElement("div");
  toast.id = toastId;
  toast.style.cssText = `
    position: fixed;
    top: 20px;
    right: 20px;
    background: ${type === "success" ? "#2d5016" : "#5a1a1a"};
    color: ${type === "success" ? "#d4edda" : "#f8d7da"};
    padding: 12px 20px;
    border-radius: 6px;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
    z-index: 10000;
    font-size: 14px;
    line-height: 1.4;
    max-width: 400px;
    word-wrap: break-word;
    animation: slideIn 0.3s ease-out;
    border: 1px solid ${type === "success" ? "rgba(72, 180, 97, 0.3)" : "rgba(220, 53, 69, 0.3)"};
    display: flex;
    align-items: center;
    gap: 12px;
  `;

  // Add animation keyframes if not already present
  if (!document.getElementById("ai-toast-styles")) {
    const style = document.createElement("style");
    style.id = "ai-toast-styles";
    style.textContent = `
      @keyframes slideIn {
        from {
          transform: translateX(100%);
          opacity: 0;
        }
        to {
          transform: translateX(0);
          opacity: 1;
        }
      }
      @keyframes slideOut {
        from {
          transform: translateX(0);
          opacity: 1;
        }
        to {
          transform: translateX(100%);
          opacity: 0;
        }
      }
    `;
    document.head.appendChild(style);
  }

  // Create dismiss button
  const dismissButton = document.createElement("button");
  dismissButton.textContent = "×";
  dismissButton.style.cssText = `
    background: transparent;
    border: none;
    color: ${type === "success" ? "#d4edda" : "#f8d7da"};
    font-size: 20px;
    font-weight: bold;
    line-height: 1;
    padding: 0;
    width: 20px;
    height: 20px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    opacity: 0.8;
    transition: opacity 0.2s;
  `;
  dismissButton.onmouseenter = () => {
    dismissButton.style.opacity = "1";
  };
  dismissButton.onmouseleave = () => {
    dismissButton.style.opacity = "0.8";
  };

  // Create message container
  const messageContainer = document.createElement("div");
  messageContainer.style.cssText = `
    flex: 1;
    word-wrap: break-word;
    display: flex;
    gap: 8px;
  `;

  const messageText = document.createElement("div");
  messageText.textContent = message;
  messageContainer.appendChild(messageText);

  // Add link if provided
  if (link) {
    const linkElement = document.createElement("a");
    linkElement.href = link.url;
    linkElement.textContent = link.text;
    linkElement.style.cssText = `
      color: ${type === "success" ? "#90ee90" : "#ffb3b3"};
      text-decoration: underline;
      cursor: pointer;
      font-weight: 500;
    `;
    linkElement.onmouseenter = () => {
      linkElement.style.opacity = "0.8";
    };
    linkElement.onmouseleave = () => {
      linkElement.style.opacity = "1";
    };
    messageContainer.appendChild(linkElement);
  }

  // Dismiss function
  let dismissTimeout: number | null = null;
  const dismissToast = () => {
    if (dismissTimeout) {
      clearTimeout(dismissTimeout);
      dismissTimeout = null;
    }
    toast.style.animation = "slideOut 0.3s ease-out";
    setTimeout(() => {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    }, 300);
  };

  dismissButton.onclick = dismissToast;

  toast.appendChild(messageContainer);
  toast.appendChild(dismissButton);
  document.body.appendChild(toast);

  // Toasts persist forever by default (only dismissed via button click)
};

// ---- Small internal helpers (pure / non-visual) ----
const sanitizeBackendBase = (value: string | undefined | null): string => {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  if (!trimmed) return "";
  const cleaned = trimmed.replace(/\/$/, "");
  try {
    if (typeof location !== "undefined" && location.origin) {
      const origin = location.origin.replace(/\/$/, "");
      if (cleaned === origin) return "";
    }
  } catch {}
  return cleaned;
};

const getBackendBase = () => {
  const fn = (window as any).AIDefaultBackendBase;
  if (typeof fn !== "function")
    throw new Error(
      "AIDefaultBackendBase not initialized. Ensure backendBase is loaded first.",
    );
  return sanitizeBackendBase(fn());
};
const debugEnabled = () => !!(window as any).AIDebug;
const dlog = (...a: any[]) => {
  if (debugEnabled()) console.log("[AIButton]", ...a);
};
const parseActionsChanged = (prev: AIAction[] | null, next: AIAction[]) => {
  if (!prev || prev.length !== next.length) return true;
  for (let i = 0; i < next.length; i++) {
    const p = prev[i];
    const n = next[i];
    if (p.id !== n.id || p.label !== n.label || p.result_kind !== n.result_kind)
      return true;
  }
  return false;
};
const computeSingleProgress = (activeIds: string[]): number | null => {
  if (activeIds.length !== 1) return null;
  try {
    const g: any = window as any;
    const tid = activeIds[0];
    const cache = g.__AI_TASK_CACHE__ || {};
    const tasks = Object.values(cache) as any[];
    const children = tasks.filter((t) => t.group_id === tid);
    if (!children.length) return 0; // show ring at 0%, matches previous UX
    let done = 0,
      running = 0,
      queued = 0,
      failed = 0,
      cancelled = 0; // cancelled intentionally excluded from denominator
    for (const c of children) {
      switch (c.status) {
        case "completed":
          done++;
          break;
        case "running":
          running++;
          break;
        case "queued":
          queued++;
          break;
        case "failed":
          failed++;
          break;
        case "cancelled":
          cancelled++;
          break;
      }
    }
    const effectiveTotal = done + running + queued + failed;
    if (!effectiveTotal) return 0;
    const weighted = done + failed + running * 0.5;
    return Math.min(1, weighted / effectiveTotal);
  } catch {
    return null;
  }
};

const ensureTaskWebSocket = (backendBase: string) => {
  const g: any = window as any;
  dlog("ensureWS invoked");
  if (g.__AI_TASK_WS__ && g.__AI_TASK_WS__.readyState === 1)
    return g.__AI_TASK_WS__;
  if (g.__AI_TASK_WS_INIT__) return g.__AI_TASK_WS__;
  g.__AI_TASK_WS_INIT__ = true;
  const base = backendBase.replace(/^http/, "ws");
  const paths = [`${base}/api/v1/ws/tasks`, `${base}/ws/tasks`];
  for (const url of paths) {
    try {
      dlog("Attempt WS connect", url);
      const sock = new WebSocket(url);
      g.__AI_TASK_WS__ = sock;
      wireSocket(sock);
      return sock;
    } catch (e) {
      if (debugEnabled())
        console.warn("[AIButton] WS connect failed candidate", url, e);
    }
  }
  g.__AI_TASK_WS_INIT__ = false;
  return null;
};

function wireSocket(sock: WebSocket) {
  const g: any = window as any;
  if (!g.__AI_TASK_WS_LISTENERS__) g.__AI_TASK_WS_LISTENERS__ = {};
  if (!g.__AI_TASK_ANY_LISTENERS__) g.__AI_TASK_ANY_LISTENERS__ = [];
  if (!g.__AI_TASK_CACHE__) g.__AI_TASK_CACHE__ = {};
  sock.onopen = () => {
    dlog("WS open", sock.url);
  };
  sock.onmessage = (evt: MessageEvent) => {
    dlog("WS raw message", evt.data);
    try {
      const m = JSON.parse(evt.data);
      const task = m.task || m.data?.task || m.data || m;
      if (!task?.id) {
        dlog("Message without task id ignored", m);
        return;
      }
      g.__AI_TASK_CACHE__[task.id] = task;
      const ls = g.__AI_TASK_WS_LISTENERS__[task.id];
      if (ls) ls.forEach((fn: any) => fn(task));
      const anyLs = g.__AI_TASK_ANY_LISTENERS__;
      if (anyLs && anyLs.length)
        anyLs.forEach((fn: any) => {
          try {
            fn(task);
          } catch {}
        });
    } catch (err) {
      if (debugEnabled())
        console.error("[AIButton] Failed parse WS message", err);
    }
  };
  const cleanup = (ev?: any) => {
    if (debugEnabled())
      console.warn("[AIButton] WS closed/error", ev?.code, ev?.reason);
    if ((window as any).__AI_TASK_WS__ === sock)
      (window as any).__AI_TASK_WS__ = null;
    (window as any).__AI_TASK_WS_INIT__ = false;
  };
  sock.onclose = cleanup;
  sock.onerror = cleanup;
}

const MinimalAIButton = () => {
  const React: any = (window as any).PluginApi?.React || (window as any).React;
  if (!React) {
    console.error("[AIButton] React not found on window.PluginApi.React");
    return null;
  }
  const pageAPI: any = (window as any).AIPageContext;
  if (!pageAPI) {
    console.error("[AIButton] AIPageContext missing on window");
    return null;
  }

  const [context, setContext] = React.useState(pageAPI.get() as PageContext);
  const [showTooltip, setShowTooltip] = React.useState(false);
  const [openMenu, setOpenMenu] = React.useState(false);
  const [loadingActions, setLoadingActions] = React.useState(false);
  const [actions, setActions] = React.useState([] as AIAction[]);
  const [activeTasks, setActiveTasks] = React.useState([] as string[]);
  const [recentlyFinished, setRecentlyFinished] = React.useState(
    [] as string[],
  ); // retained for potential future UX
  const [backendBase, setBackendBase] = React.useState(() => getBackendBase());
  React.useEffect(() => {
    const updateBase = (event?: Event) => {
      const customEvent = event as
        | CustomEvent<string | null | undefined>
        | undefined;
      const detail = customEvent?.detail;
      if (typeof detail === "string") {
        setBackendBase(sanitizeBackendBase(detail));
      } else {
        setBackendBase(getBackendBase());
      }
    };
    updateBase();
    window.addEventListener(
      "AIBackendBaseUpdated",
      updateBase as EventListener,
    );
    return () =>
      window.removeEventListener(
        "AIBackendBaseUpdated",
        updateBase as EventListener,
      );
  }, []);
  const actionsRef: { current: AIAction[] | null } = React.useRef(null);

  React.useEffect(
    () => pageAPI.subscribe((ctx: PageContext) => setContext(ctx)),
    [],
  );

  const refetchActions = React.useCallback(
    async (ctx: PageContext, opts: { silent?: boolean } = {}) => {
      if (!backendBase) {
        if (!opts.silent) setLoadingActions(false);
        setActions([]);
        return;
      }
      if (!opts.silent) setLoadingActions(true);
      try {
        const res = await fetch(`${backendBase}/api/v1/actions/available`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            context: {
              page: ctx.page,
              entityId: ctx.entityId,
              isDetailView: ctx.isDetailView,
              selectedIds: ctx.selectedIds || [],
              visibleIds: ctx.visibleIds || [],
            },
          }),
        });
        if (!res.ok) throw new Error("Failed to load actions");
        const data: AIAction[] = await res.json();
        if (parseActionsChanged(actionsRef.current, data)) {
          actionsRef.current = data;
          setActions(data);
        }
      } catch {
        if (!opts.silent) setActions([]);
      } finally {
        if (!opts.silent) setLoadingActions(false);
      }
    },
    [backendBase],
  );
  React.useEffect(() => {
    refetchActions(context);
  }, [context, refetchActions]);

  // Websocket ensure
  React.useEffect(() => {
    if (!backendBase) return;
    ensureTaskWebSocket(backendBase);
  }, [backendBase]);

  const executeAction = async (actionId: string) => {
    if (!backendBase) {
      alert(
        "AI backend URL is not configured. Update it under AI Overhaul settings.",
      );
      return;
    }
    dlog("Execute action", actionId, "context", context);
    ensureTaskWebSocket(backendBase);
    try {
      const g: any = window as any;
      let liveContext: PageContext = context;
      try {
        if (pageAPI.forceRefresh) pageAPI.forceRefresh();
        if (pageAPI.get) {
          liveContext = pageAPI.get();
          setContext(liveContext);
        }
      } catch {
        /* fall back to current state */
      }
      const actionMeta = actionsRef.current?.find((a) => a.id === actionId);
      const resultKind = actionMeta?.result_kind || "none";
      const res = await fetch(`${backendBase}/api/v1/actions/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_id: actionId,
          context: {
            page: liveContext.page,
            entityId: liveContext.entityId,
            isDetailView: liveContext.isDetailView,
            selectedIds: liveContext.selectedIds || [],
            visibleIds: liveContext.visibleIds || [],
          },
          params: {},
        }),
      });
      if (!res.ok) {
        let message = "Submit failed";
        try {
          const err = await res.json();
          if (err?.detail) {
            if (typeof err.detail === "string") {
              message = err.detail;
            } else if (typeof err.detail?.message === "string") {
              message = err.detail.message;
            }
          }
        } catch {}
        throw new Error(message);
      }
      const { task_id: taskId } = await res.json();
      if (!g.__AI_TASK_WS_LISTENERS__) g.__AI_TASK_WS_LISTENERS__ = {};
      if (!g.__AI_TASK_WS_LISTENERS__[taskId])
        g.__AI_TASK_WS_LISTENERS__[taskId] = [];
      setActiveTasks((prev: string[]) =>
        prev.includes(taskId) ? prev : [...prev, taskId],
      );
      const finalize = (t: AITaskEvent) => {
        if (t.status === "completed") {
          if (resultKind === "dialog" || resultKind === "notification") {
            const result = t.result;
            let message = "";

            // Check if it's a single scene result
            if (
              result &&
              typeof result === "object" &&
              "scene_id" in result &&
              "tags_applied" in result
            ) {
              const singleResult = result as SingleSceneTagResult;
              const tagsCount = singleResult.tags_applied || 0;
              const sceneId = singleResult.scene_id;
              console.log("got single tag results", singleResult);
              message = `Applied ${tagsCount} tag${tagsCount !== 1 ? "s" : ""} to scene`;

              // Construct scene URL from current origin
              const sceneUrl = `${window.location.origin}/scenes/${sceneId}/`;
              showToast(message, "success", { url: sceneUrl, text: "view" });
              return; // Early return to avoid showing toast twice
            }
            // Check if it's a multiple scenes result
            else if (
              result &&
              typeof result === "object" &&
              "scenes_completed" in result
            ) {
              const multiResult = result as MultipleScenesTagResult;
              const scenesCount = multiResult.scenes_completed || 0;
              const scenesFailed = multiResult.scenes_failed || 0;
              console.log("got multiple tag results", multiResult);
              let messageSuccessPart = `${scenesCount} scene${scenesCount !== 1 ? "s" : ""} tagged`;
              let messageFailedPart = `${scenesFailed} scene${scenesFailed !== 1 ? "s" : ""} failed`;
              let fullMessage = "";
              if (scenesFailed > 0 && scenesCount > 0) {
                fullMessage = `${messageSuccessPart}, ${messageFailedPart}`;
              } else if (scenesFailed > 0) {
                fullMessage = messageFailedPart;
              } else {
                fullMessage = messageSuccessPart;
              }
              message = fullMessage;

              // Construct URL to recently updated scenes
              const scenesUrl = `${window.location.origin}/scenes?sortby=updated_at&sortdir=desc`;
              showToast(message, "success", { url: scenesUrl, text: "view" });
              return; // Early return to avoid showing toast twice
            }
            // Fallback for other result types
            else {
              message = `Action ${actionId} completed`;
            }

            if (message) {
              showToast(message, "success");
            }
          }
        } else if (t.status === "failed") {
          showToast(
            `Action ${actionId} failed: ${t.error || "unknown error"}. Is the nsfw_ai_model_server (usually port 8000) running?`,
            "error",
          );
        }
        setActiveTasks((prev: string[]) =>
          prev.filter((id: string) => id !== t.id),
        );
        setRecentlyFinished((prev: string[]) => [t.id, ...prev].slice(0, 20));
      };
      const listener = (t: AITaskEvent) => {
        if (t.id !== taskId) return;
        if (["completed", "failed", "cancelled"].includes(t.status)) {
          finalize(t);
          g.__AI_TASK_WS_LISTENERS__[taskId] = (
            g.__AI_TASK_WS_LISTENERS__[taskId] || []
          ).filter((fn: any) => fn !== listener);
        }
      };
      g.__AI_TASK_WS_LISTENERS__[taskId].push(listener);
      if (g.__AI_TASK_CACHE__?.[taskId]) listener(g.__AI_TASK_CACHE__[taskId]);
    } catch (e: any) {
      showToast(
        `Action ${actionId} failed: ${e.message}. Is the nsfw_ai_model_server (usually port 8000) running?`,
        "error",
      );
    }
  };

  // Any-task listener for progress updates
  React.useEffect(() => {
    const g: any = window as any;
    if (!g.__AI_TASK_ANY_LISTENERS__) g.__AI_TASK_ANY_LISTENERS__ = [];
    const listener = (t: any) => {
      if (!activeTasks.length) return;
      if (activeTasks.includes(t.id) || activeTasks.includes(t.group_id))
        setProgressVersion((v: number) => v + 1);
    };
    g.__AI_TASK_ANY_LISTENERS__.push(listener);
    return () => {
      g.__AI_TASK_ANY_LISTENERS__ = (g.__AI_TASK_ANY_LISTENERS__ || []).filter(
        (fn: any) => fn !== listener,
      );
    };
  }, [activeTasks]);
  const [progressVersion, setProgressVersion] = React.useState(0); // triggers re-render on child task activity

  const singleProgress = computeSingleProgress(activeTasks);
  const progressPct =
    singleProgress != null ? Math.round(singleProgress * 100) : null;
  const toggleMenu = () => {
    if (!openMenu) {
      let liveContext: PageContext = context;
      try {
        if (pageAPI.forceRefresh) pageAPI.forceRefresh();
        if (pageAPI.get) {
          liveContext = pageAPI.get();
          setContext(liveContext);
        }
      } catch {
        /* best effort */
      }
      refetchActions(liveContext, { silent: true });
    }
    setOpenMenu((o: boolean) => !o);
  };
  const getButtonIcon = () => {
    switch (context.page) {
      case "scenes":
        return "🎬";
      case "galleries":
      case "images":
        return "🖼️";
      case "performers":
        return "👤";
      case "studios":
        return "🏢";
      case "tags":
        return "🔖";
      case "markers":
        return "⏱️";
      case "home":
        return "🏠";
      case "settings":
        return "⚙️";
      default:
        return "🤖";
    }
  };
  // Map page keys to more compact labels where necessary (e.g. 'performers' -> 'Actors')
  const getButtonLabel = () => {
    if (!context || !context.page) return "AI";
    switch (context.page) {
      case "performers":
        return "Actors";
      default:
        return context.page;
    }
  };

  const colorClass = context.isDetailView
    ? "ai-btn--detail"
    : `ai-btn--${context.page}`;

  // Build children (unchanged structure / classes)
  const elems: any[] = [];
  const activeCount = activeTasks.length;
  const progressRing =
    singleProgress != null && activeCount === 1
      ? React.createElement("div", {
          key: "ring",
          className: "ai-btn__progress-ring",
          style: { ["--ai-progress" as any]: `${progressPct}%` },
        })
      : null;
  elems.push(
    React.createElement(
      "button",
      {
        key: "ai-btn",
        className:
          `ai-btn ${colorClass}` +
          (singleProgress != null ? " ai-btn--progress" : ""),
        onClick: toggleMenu,
        onMouseEnter: () => setShowTooltip(true),
        onMouseLeave: () => setShowTooltip(false),
        disabled: loadingActions,
      },
      [
        progressRing,
        React.createElement(
          "div",
          { key: "icon", className: "ai-btn__icon" },
          activeCount === 0
            ? getButtonIcon()
            : activeCount === 1 && progressPct != null
              ? `${progressPct}%`
              : "⏳",
        ),
        React.createElement(
          "div",
          { key: "lbl", className: "ai-btn__label" },
          String(getButtonLabel() || "AI").toUpperCase(),
        ),
        activeCount > 1 &&
          React.createElement(
            "span",
            { key: "badge", className: "ai-btn__badge" },
            String(activeCount),
          ),
      ],
    ),
  );

  if (showTooltip && !openMenu) {
    elems.push(
      React.createElement("div", { key: "tip", className: "ai-btn__tooltip" }, [
        React.createElement(
          "div",
          { key: "main", className: "ai-btn__tooltip-main" },
          context.contextLabel,
        ),
        React.createElement(
          "div",
          { key: "detail", className: "ai-btn__tooltip-detail" },
          context.detailLabel || "",
        ),
        context.entityId &&
          React.createElement(
            "div",
            { key: "id", className: "ai-btn__tooltip-id" },
            `ID: ${context.entityId}`,
          ),
        context.selectedIds?.length &&
          React.createElement(
            "div",
            { key: "sel", className: "ai-btn__tooltip-sel" },
            `Selected: ${context.selectedIds.length}`,
          ),
      ]),
    );
  }

  if (openMenu) {
    elems.push(
      React.createElement(
        "div",
        { key: "menu", className: "ai-actions-menu" },
        [
          loadingActions &&
            React.createElement(
              "div",
              { key: "loading", className: "ai-actions-menu__status" },
              "Loading actions...",
            ),
          !loadingActions &&
            actions.length === 0 &&
            React.createElement(
              "div",
              { key: "none", className: "ai-actions-menu__status" },
              "No actions",
            ),
          !loadingActions &&
            actions.map((a: any) =>
              React.createElement(
                "button",
                {
                  key: a.id,
                  onClick: () => executeAction(a.id),
                  className: "ai-actions-menu__item",
                },
                [
                  React.createElement(
                    "span",
                    { key: "svc", className: "ai-actions-menu__svc" },
                    a.service?.toUpperCase?.() || a.service,
                  ),
                  React.createElement(
                    "span",
                    { key: "albl", style: { flexGrow: 1 } },
                    a.label,
                  ),
                  a.result_kind === "dialog" &&
                    React.createElement(
                      "span",
                      { key: "rk", className: "ai-actions-menu__rk" },
                      "↗",
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  return React.createElement(
    "div",
    {
      className: "minimal-ai-button",
      style: { position: "relative", display: "inline-block" },
    },
    elems,
  );
};

(window as any).MinimalAIButton = MinimalAIButton;
(window as any).AIButton = MinimalAIButton; // alias for integrations expecting AIButton
if (!(window as any).__AI_BUTTON_LOADED__) {
  (window as any).__AI_BUTTON_LOADED__ = true;
  if ((window as any).AIDebug)
    console.log("[AIButton] Component loaded and globals registered");
}
export default MinimalAIButton;
