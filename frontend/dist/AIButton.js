(function(){
// AIButton (MinimalAIButton)
// Contract:
//  - Provides a single floating/contextual button that lists available AI actions for current page context.
//  - No polling: actions fetched on open + context change; task progress via shared websocket + global cache.
//  - Supports multiple concurrent parent/controller tasks; shows aggregate count or single progress ring.
//  - Exposes global aliases: window.AIButton & window.MinimalAIButton for integrations to mount.
//  - Debug logging gated by window.AIDebug = true.
//  - Assumes backend REST under /api/v1 and websocket under /api/v1/ws/tasks (with legacy fallback /ws/tasks).
//  - Only parent/controller task IDs are tracked in activeTasks; child task events still drive progress inference.
const MinimalAIButton = () => {
    var _a, _b;
    const React = ((_a = window.PluginApi) === null || _a === void 0 ? void 0 : _a.React) || window.React;
    if (!React) {
        console.error('[AIButton] React not found on window.PluginApi.React');
        return null;
    }
    const pageAPI = window.AIPageContext;
    if (!pageAPI) {
        console.error('[AIButton] AIPageContext missing on window');
        return null;
    }
    const [context, setContext] = React.useState(pageAPI.get());
    const [showTooltip, setShowTooltip] = React.useState(false);
    const [openMenu, setOpenMenu] = React.useState(false);
    const [loadingActions, setLoadingActions] = React.useState(false);
    const [actions, setActions] = React.useState([]);
    // Track multiple active tasks
    /** @type {[string[], Function]} */
    const [activeTasks, setActiveTasks] = React.useState([]);
    /** @type {[string[], Function]} */
    const [recentlyFinished, setRecentlyFinished] = React.useState([]);
    // Backend base: explicit override > map :3000 UI origin to :8000 backend > fallback localhost:8000
    const backendBase = (() => {
        const explicit = window.AI_BACKEND_URL;
        if (explicit)
            return explicit.replace(/\/$/, '');
        const loc = (location && location.origin) || '';
        try {
            const u = new URL(loc);
            if (u.port === '3000') {
                u.port = '8000';
                return u.toString().replace(/\/$/, '');
            }
        }
        catch { }
        return (loc || 'http://localhost:8000').replace(/\/$/, '');
    })();
    const actionsRef = React.useRef(null);
    React.useEffect(() => pageAPI.subscribe((ctx) => setContext(ctx)), []);
    const refetchActions = React.useCallback(async (ctx, opts = {}) => {
        if (!opts.silent)
            setLoadingActions(true);
        try {
            const res = await fetch(`${backendBase}/api/v1/actions/available`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ context: { page: ctx.page, entityId: ctx.entityId, isDetailView: ctx.isDetailView, selectedIds: ctx.selectedIds || [] } })
            });
            if (!res.ok)
                throw new Error('Failed to load actions');
            const data = await res.json();
            const prev = actionsRef.current;
            // Detect changes not only by id list length but also by label or result_kind (different variants same id).
            let changed = false;
            if (!prev || prev.length !== data.length) {
                changed = true;
            }
            else {
                for (let i = 0; i < data.length; i++) {
                    const p = prev[i];
                    const n = data[i];
                    if (p.id !== n.id || p.label !== n.label || p.result_kind !== n.result_kind) {
                        changed = true;
                        break;
                    }
                }
            }
            if (changed) {
                actionsRef.current = data;
                setActions(data);
            }
        }
        catch {
            if (!opts.silent)
                setActions([]);
        }
        finally {
            if (!opts.silent)
                setLoadingActions(false);
        }
    }, [backendBase]);
    React.useEffect(() => { refetchActions(context); }, [context, refetchActions]);
    // Websocket singleton with listener registry + cache
    const wsInitRef = React.useRef(false);
    const debug = !!window.AIDebug; // enable by setting window.AIDebug = true in console
    const dlog = (...a) => { if (debug)
        console.log('[AIButton]', ...a); };
    const ensureWS = React.useCallback(() => {
        const g = window;
        dlog('ensureWS invoked');
        if (g.__AI_TASK_WS__ && g.__AI_TASK_WS__.readyState === 1) {
            dlog('Reusing existing open WS');
            return g.__AI_TASK_WS__;
        }
        if (wsInitRef.current) {
            dlog('Init already in progress or socket placeholder present');
            return g.__AI_TASK_WS__;
        }
        wsInitRef.current = true;
        const base = backendBase.replace(/^http/, 'ws');
        const paths = [`${base}/api/v1/ws/tasks`, `${base}/ws/tasks`];
        let sock = null;
        for (const url of paths) {
            try {
                dlog('Attempt WS connect', url);
                sock = new WebSocket(url);
                window.__AI_TASK_WS__ = sock;
                break;
            }
            catch (e) {
                if (debug)
                    console.warn('[AIButton] WS connect failed candidate', url, e);
            }
        }
        if (!sock) {
            wsInitRef.current = false;
            return null;
        }
        const glob = window;
        if (!glob.__AI_TASK_WS_LISTENERS__)
            glob.__AI_TASK_WS_LISTENERS__ = {};
        if (!glob.__AI_TASK_ANY_LISTENERS__)
            glob.__AI_TASK_ANY_LISTENERS__ = [];
        if (!glob.__AI_TASK_CACHE__)
            glob.__AI_TASK_CACHE__ = {};
        sock.onopen = () => { dlog('WS open', sock === null || sock === void 0 ? void 0 : sock.url); };
        sock.onmessage = (evt) => {
            var _a;
            dlog('WS raw message', evt.data);
            try {
                const m = JSON.parse(evt.data);
                const task = m.task || ((_a = m.data) === null || _a === void 0 ? void 0 : _a.task) || m.data || m;
                if (!(task === null || task === void 0 ? void 0 : task.id)) {
                    dlog('Message without task id ignored', m);
                    return;
                }
                glob.__AI_TASK_CACHE__[task.id] = task;
                const ls = glob.__AI_TASK_WS_LISTENERS__[task.id];
                dlog('Task event', task.id, task.status, 'listeners:', ls ? ls.length : 0);
                if (ls)
                    ls.forEach((fn) => fn(task));
                // Broadcast to any-listeners (for progress updates / dashboards)
                const anyLs = glob.__AI_TASK_ANY_LISTENERS__;
                if (anyLs && anyLs.length) {
                    anyLs.forEach((fn) => { try {
                        fn(task);
                    }
                    catch (_) { } });
                }
            }
            catch (err) {
                if (debug)
                    console.error('[AIButton] Failed parse WS message', err);
            }
        };
        const cleanup = (ev) => {
            if (debug)
                console.warn('[AIButton] WS closed/error', ev === null || ev === void 0 ? void 0 : ev.code, ev === null || ev === void 0 ? void 0 : ev.reason);
            if (window.__AI_TASK_WS__ === sock)
                window.__AI_TASK_WS__ = null;
        };
        sock.onclose = cleanup;
        sock.onerror = cleanup;
        return sock;
    }, [backendBase]);
    React.useEffect(() => { ensureWS(); }, [ensureWS]);
    const handleExecute = async (actionId) => {
        var _a;
        dlog('Execute action', actionId, 'context', context);
        try {
            ensureWS();
            const g = window;
            const res = await fetch(`${backendBase}/api/v1/actions/submit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action_id: actionId, context: { page: context.page, entityId: context.entityId, isDetailView: context.isDetailView, selectedIds: context.selectedIds || [] }, params: {} }) });
            if (!res.ok)
                throw new Error('Submit failed');
            const { task_id: taskId } = await res.json();
            if (!g.__AI_TASK_WS_LISTENERS__)
                g.__AI_TASK_WS_LISTENERS__ = {};
            if (!g.__AI_TASK_WS_LISTENERS__[taskId])
                g.__AI_TASK_WS_LISTENERS__[taskId] = [];
            dlog('Registered task listener', taskId);
            setActiveTasks((prev) => prev.includes(taskId) ? prev : [...prev, taskId]);
            const finalize = (t) => {
                if (t.status === 'completed') {
                    if (t.result_kind === 'dialog' || t.result_kind === 'notification') {
                        alert(`Action ${actionId} result:\n` + JSON.stringify(t.result, null, 2));
                    }
                }
                else if (t.status === 'failed') {
                    alert(`Action ${actionId} failed: ${t.error || 'unknown error'}`);
                }
                setActiveTasks((prev) => prev.filter((id) => id !== t.id));
                setRecentlyFinished((prev) => [t.id, ...prev].slice(0, 20));
            };
            const listener = (t) => {
                if (t.id !== taskId)
                    return;
                dlog('Listener got task event', t.id, t.status);
                if (["completed", "failed", "cancelled"].includes(t.status)) {
                    dlog('Finalizing task', t.id, t.status);
                    finalize(t);
                    g.__AI_TASK_WS_LISTENERS__[taskId] = (g.__AI_TASK_WS_LISTENERS__[taskId] || []).filter((fn) => fn !== listener);
                }
            };
            g.__AI_TASK_WS_LISTENERS__[taskId].push(listener);
            if ((_a = g.__AI_TASK_CACHE__) === null || _a === void 0 ? void 0 : _a[taskId]) {
                dlog('Immediate cache hit for task', taskId, g.__AI_TASK_CACHE__[taskId]);
                listener(g.__AI_TASK_CACHE__[taskId]);
            }
        }
        catch (e) {
            alert(`Action ${actionId} failed: ${e.message}`);
        }
    };
    const toggleMenu = () => { if (!openMenu) {
        if (debug)
            console.debug('[AIButton] Opening menu, refetch actions');
        refetchActions(context, { silent: true });
    } setOpenMenu(!openMenu); };
    const getButtonIcon = () => { switch (context.page) {
        case 'scenes': return '🎬';
        case 'galleries':
        case 'images': return '🖼️';
        case 'performers': return '👤';
        case 'studios': return '🏢';
        case 'tags': return '🔖';
        case 'markers': return '⏱️';
        case 'home': return '🏠';
        default: return '🤖';
    } };
    const colorClass = context.isDetailView ? 'ai-btn--detail' : `ai-btn--${context.page}`;
    const elems = [];
    const activeCount = activeTasks.length;
    // Force re-render on relevant child task mutations for progress (version bump)
    const [progressVersion, setProgressVersion] = React.useState(0);
    React.useEffect(() => {
        const g = window;
        const listener = (t) => {
            if (!activeTasks.length)
                return;
            if (activeTasks.includes(t.id) || activeTasks.includes(t.group_id)) {
                setProgressVersion((v) => v + 1);
            }
        };
        if (!g.__AI_TASK_ANY_LISTENERS__)
            g.__AI_TASK_ANY_LISTENERS__ = [];
        g.__AI_TASK_ANY_LISTENERS__.push(listener);
        return () => {
            g.__AI_TASK_ANY_LISTENERS__ = (g.__AI_TASK_ANY_LISTENERS__ || []).filter((fn) => fn !== listener);
        };
    }, [activeTasks]);
    // Progress inference for single active parent/controller: compute via children states in global cache
    let singleProgress = null;
    if (activeCount === 1) {
        try {
            const g = window;
            const tid = activeTasks[0];
            const cache = g.__AI_TASK_CACHE__ || {};
            const tasks = Object.values(cache);
            const children = tasks.filter(t => t.group_id === tid);
            if (children.length) {
                let done = 0, running = 0, queued = 0, failed = 0, cancelled = 0;
                for (const c of children) {
                    switch (c.status) {
                        case 'completed':
                            done++;
                            break;
                        case 'running':
                            running++;
                            break;
                        case 'queued':
                            queued++;
                            break;
                        case 'failed':
                            failed++;
                            break;
                        case 'cancelled':
                            cancelled++;
                            break;
                    }
                }
                // Effective total excludes cancelled to keep progress intuitive when some children are aborted early.
                const effectiveTotal = done + running + queued + failed; // cancelled removed from denominator
                if (effectiveTotal > 0) {
                    // Weighted progress: completed=1.0, failed=1.0 (terminal), running=0.5, queued=0.
                    const weighted = done + failed + running * 0.5;
                    singleProgress = Math.min(1, weighted / effectiveTotal);
                }
            }
            else {
                // Fallback: show 0% instead of hiding percent for a controller whose children haven't arrived yet
                // We'll treat absence of children as 0% rather than null so UI shows ring.
                singleProgress = 0;
            }
        }
        catch { }
    }
    const progressPct = singleProgress != null ? Math.round(singleProgress * 100) : null;
    const progressRing = (singleProgress != null && activeCount === 1) ? React.createElement('div', { key: 'ring', className: 'ai-btn__progress-ring', style: { ['--ai-progress']: `${progressPct}%` } }) : null;
    elems.push(React.createElement('button', { key: 'ai-btn', className: `ai-btn ${colorClass}` + (singleProgress != null ? ' ai-btn--progress' : ''), onClick: toggleMenu, onMouseEnter: () => setShowTooltip(true), onMouseLeave: () => setShowTooltip(false), disabled: loadingActions }, [
        progressRing,
        React.createElement('div', { key: 'icon', className: 'ai-btn__icon' }, activeCount === 0 ? getButtonIcon() : (activeCount === 1 && progressPct != null ? `${progressPct}%` : '⏳')),
        React.createElement('div', { key: 'lbl', className: 'ai-btn__label' }, (context.page || 'AI').toUpperCase()),
        activeCount > 1 && React.createElement('span', { key: 'badge', className: 'ai-btn__badge' }, String(activeCount))
    ]));
    if (showTooltip && !openMenu) {
        elems.push(React.createElement('div', { key: 'tip', className: 'ai-btn__tooltip' }, [
            React.createElement('div', { key: 'main', className: 'ai-btn__tooltip-main' }, context.contextLabel),
            React.createElement('div', { key: 'detail', className: 'ai-btn__tooltip-detail' }, context.detailLabel || ''),
            context.entityId && React.createElement('div', { key: 'id', className: 'ai-btn__tooltip-id' }, `ID: ${context.entityId}`),
            ((_b = context.selectedIds) === null || _b === void 0 ? void 0 : _b.length) && React.createElement('div', { key: 'sel', className: 'ai-btn__tooltip-sel' }, `Selected: ${context.selectedIds.length}`)
        ]));
    }
    if (openMenu) {
        elems.push(React.createElement('div', { key: 'menu', className: 'ai-actions-menu' }, [
            loadingActions && React.createElement('div', { key: 'loading', className: 'ai-actions-menu__status' }, 'Loading actions...'),
            !loadingActions && actions.length === 0 && React.createElement('div', { key: 'none', className: 'ai-actions-menu__status' }, 'No actions'),
            !loadingActions && actions.map((a) => {
                var _a, _b;
                return React.createElement('button', { key: a.id, onClick: () => handleExecute(a.id), className: 'ai-actions-menu__item' }, [
                    React.createElement('span', { key: 'svc', className: 'ai-actions-menu__svc' }, ((_b = (_a = a.service) === null || _a === void 0 ? void 0 : _a.toUpperCase) === null || _b === void 0 ? void 0 : _b.call(_a)) || a.service),
                    React.createElement('span', { key: 'albl', style: { flexGrow: 1 } }, a.label),
                    a.result_kind === 'dialog' && React.createElement('span', { key: 'rk', className: 'ai-actions-menu__rk' }, '↗')
                ]);
            })
        ]));
    }
    return React.createElement('div', { className: 'minimal-ai-button', style: { position: 'relative', display: 'inline-block' } }, elems);
};
window.MinimalAIButton = MinimalAIButton;
window.AIButton = MinimalAIButton; // alias for integrations expecting AIButton
if (!window.__AI_BUTTON_LOADED__) {
    window.__AI_BUTTON_LOADED__ = true;
    if (window.AIDebug)
        console.log('[AIButton] Component loaded and globals registered');
}
MinimalAIButton;
})();
