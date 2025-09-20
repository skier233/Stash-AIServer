(function(){
// Clean minimal websocket-only AI button (no polling, single component definition)
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
    const [executing, setExecuting] = React.useState(null);
    const backendBase = window.AI_BACKEND_URL || 'http://localhost:8000';
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
            const changed = !prev || prev.length !== data.length || prev.some((p, i) => p.id !== data[i].id);
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
    const debug = true; // enable verbose
    const ensureWS = React.useCallback(() => {
        const g = window;
        if (debug)
            console.log('[AIButton] ensureWS invoked');
        if (g.__AI_TASK_WS__ && g.__AI_TASK_WS__.readyState === 1) {
            if (debug)
                console.log('[AIButton] Reusing existing open WS');
            return g.__AI_TASK_WS__;
        }
        if (wsInitRef.current) {
            if (debug)
                console.log('[AIButton] Init already in progress or socket placeholder present');
            return g.__AI_TASK_WS__;
        }
        wsInitRef.current = true;
        const base = backendBase.replace(/^http/, 'ws');
        const paths = [`${base}/api/v1/ws/tasks`, `${base}/ws/tasks`];
        let sock = null;
        for (const url of paths) {
            try {
                if (debug)
                    console.log('[AIButton] Attempt WS connect', url);
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
        if (!glob.__AI_TASK_CACHE__)
            glob.__AI_TASK_CACHE__ = {};
        sock.onopen = () => { if (debug)
            console.log('[AIButton] WS open', sock === null || sock === void 0 ? void 0 : sock.url); };
        sock.onmessage = (evt) => {
            var _a;
            if (debug)
                console.log('[AIButton] WS raw message', evt.data);
            try {
                const m = JSON.parse(evt.data);
                const task = m.task || ((_a = m.data) === null || _a === void 0 ? void 0 : _a.task) || m.data || m;
                if (!(task === null || task === void 0 ? void 0 : task.id)) {
                    if (debug)
                        console.log('[AIButton] Message without task id ignored', m);
                    return;
                }
                glob.__AI_TASK_CACHE__[task.id] = task;
                const ls = glob.__AI_TASK_WS_LISTENERS__[task.id];
                if (debug)
                    console.log('[AIButton] Task event', task.id, task.status, 'listeners:', ls ? ls.length : 0);
                if (ls)
                    ls.forEach((fn) => fn(task));
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
        if (debug)
            console.log('[AIButton] Execute action', actionId, 'context', context);
        setExecuting(actionId);
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
            if (debug)
                console.log('[AIButton] Registered task listener', taskId);
            const finalize = (t) => { if (t.status === 'completed') {
                if (t.result_kind === 'dialog' || t.result_kind === 'notification') {
                    alert(`Action ${actionId} result:\n` + JSON.stringify(t.result, null, 2));
                }
            }
            else if (t.status === 'failed') {
                alert(`Action ${actionId} failed: ${t.error || 'unknown error'}`);
            } setExecuting(null); setOpenMenu(false); };
            const listener = (t) => {
                if (t.id !== taskId)
                    return;
                if (debug)
                    console.log('[AIButton] Listener got task event', t.id, t.status);
                if (["completed", "failed", "cancelled"].includes(t.status)) {
                    if (debug)
                        console.log('[AIButton] Finalizing task', t.id, t.status);
                    finalize(t);
                    g.__AI_TASK_WS_LISTENERS__[taskId] = (g.__AI_TASK_WS_LISTENERS__[taskId] || []).filter((fn) => fn !== listener);
                }
            };
            g.__AI_TASK_WS_LISTENERS__[taskId].push(listener);
            if ((_a = g.__AI_TASK_CACHE__) === null || _a === void 0 ? void 0 : _a[taskId]) {
                if (debug)
                    console.log('[AIButton] Immediate cache hit for task', taskId, g.__AI_TASK_CACHE__[taskId]);
                listener(g.__AI_TASK_CACHE__[taskId]);
            }
        }
        catch (e) {
            alert(`Action ${actionId} failed: ${e.message}`);
            setExecuting(null);
            setOpenMenu(false);
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
    elems.push(React.createElement('button', { key: 'ai-btn', className: `ai-btn ${colorClass}`, onClick: toggleMenu, onMouseEnter: () => setShowTooltip(true), onMouseLeave: () => setShowTooltip(false), disabled: loadingActions }, [
        React.createElement('div', { key: 'icon', className: 'ai-btn__icon' }, executing ? '⏳' : getButtonIcon()),
        React.createElement('div', { key: 'lbl', className: 'ai-btn__label' }, (context.page || 'AI').toUpperCase())
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
                return React.createElement('button', { key: a.id, onClick: () => handleExecute(a.id), disabled: !!executing, className: 'ai-actions-menu__item' }, [
                    React.createElement('span', { key: 'svc', className: 'ai-actions-menu__svc' }, ((_b = (_a = a.service) === null || _a === void 0 ? void 0 : _a.toUpperCase) === null || _b === void 0 ? void 0 : _b.call(_a)) || a.service),
                    React.createElement('span', { key: 'albl', style: { flexGrow: 1 } }, a.label),
                    a.result_kind === 'dialog' && React.createElement('span', { key: 'rk', className: 'ai-actions-menu__rk' }, '↗'),
                    executing === a.id && React.createElement('span', { key: 'exec', className: 'ai-actions-menu__exec' }, '…')
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
    console.log('[AIButton] Component loaded and globals registered');
}
MinimalAIButton;
})();
