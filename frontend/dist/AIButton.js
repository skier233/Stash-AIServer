(function(){
const MinimalAIButton = () => {
    const React = window.PluginApi.React;
    const pageAPI = window.AIPageContext;
    const [context, setContext] = React.useState(pageAPI.get());
    const [showTooltip, setShowTooltip] = React.useState(false);
    const [openMenu, setOpenMenu] = React.useState(false);
    const [loadingActions, setLoadingActions] = React.useState(false);
    // Using untyped React from host environment: avoid generic args to satisfy TS build
    const [actions, setActions] = React.useState([]);
    const [executing, setExecuting] = React.useState(null);
    const backendBase = window.AI_BACKEND_URL || 'http://localhost:8000';
    React.useEffect(() => {
        const unsubscribe = pageAPI.subscribe((ctx) => setContext(ctx));
        return unsubscribe;
    }, []);
    React.useEffect(() => {
        console.log('🚀 AI Button: Context detected:', context);
    }, [context]);
    const refetchActions = React.useCallback(async (ctx) => {
        setLoadingActions(true);
        try {
            const res = await fetch(`${backendBase}/api/v1/actions/available`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ context: {
                        page: ctx.page,
                        entityId: ctx.entityId,
                        isDetailView: ctx.isDetailView,
                        selectedIds: ctx.selectedIds || []
                    } })
            });
            if (!res.ok)
                throw new Error('Failed to load actions');
            const data = await res.json();
            setActions(data);
        }
        catch (e) {
            console.warn('[AIButton] action fetch failed', e);
            setActions([]);
        }
        finally {
            setLoadingActions(false);
        }
    }, [backendBase]);
    React.useEffect(() => { refetchActions(context); }, [context, refetchActions]);
    const handleExecute = async (actionId) => {
        setExecuting(actionId);
        try {
            const res = await fetch(`${backendBase}/api/v1/actions/execute`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    action_id: actionId,
                    context: {
                        page: context.page,
                        entityId: context.entityId,
                        isDetailView: context.isDetailView,
                        selectedIds: context.selectedIds || []
                    },
                    params: {}
                })
            });
            if (!res.ok)
                throw new Error('Execution failed');
            const data = await res.json();
            // Simple demo UX: show alert for dialog/notification kinds
            if (data.result_kind === 'dialog' || data.result_kind === 'notification') {
                alert(`Action ${actionId} result:\n` + JSON.stringify(data.result, null, 2));
            }
        }
        catch (e) {
            alert(`Action ${actionId} failed: ${e.message}`);
        }
        finally {
            setExecuting(null);
            setOpenMenu(false);
        }
    };
    const toggleMenu = () => {
        if (!openMenu) {
            // ensure latest actions (already auto-refreshing on context, but optional manual refresh)
            refetchActions(context);
        }
        setOpenMenu(!openMenu);
    };
    const getButtonIcon = () => {
        switch (context.page) {
            case 'scenes': return '🎬';
            case 'galleries': return '🖼️';
            case 'images': return '🖼️';
            case 'performers': return '👤';
            case 'studios': return '🏢';
            case 'tags': return '🔖';
            case 'markers': return '⏱️';
            case 'home': return '🏠';
            default: return '🤖';
        }
    };
    const colorClass = context.isDetailView ? 'ai-btn--detail' : `ai-btn--${context.page}`;
    const ReactElems = [];
    ReactElems.push(React.createElement('button', {
        key: 'ai-button',
        className: `ai-btn ${colorClass}`,
        onClick: toggleMenu,
        onMouseEnter: () => setShowTooltip(true),
        onMouseLeave: () => setShowTooltip(false),
        disabled: loadingActions
    }, [
        React.createElement('div', { key: 'icon', className: 'ai-btn__icon' }, executing ? '⏳' : getButtonIcon()),
        React.createElement('div', { key: 'context', className: 'ai-btn__label' }, context.page.toUpperCase())
    ]));
    if (showTooltip && !openMenu) {
        ReactElems.push(React.createElement('div', { key: 'tooltip', className: 'ai-btn__tooltip' }, [
            React.createElement('div', { key: 'ctx-main', className: 'ai-btn__tooltip-main' }, context.contextLabel),
            React.createElement('div', { key: 'ctx-detail', className: 'ai-btn__tooltip-detail' }, context.detailLabel || ''),
            context.entityId && React.createElement('div', { key: 'ctx-id', className: 'ai-btn__tooltip-id' }, `ID: ${context.entityId}`),
            (context.selectedIds && context.selectedIds.length > 0) && React.createElement('div', { key: 'ctx-sel', className: 'ai-btn__tooltip-sel' }, `Selected: ${context.selectedIds.length}`)
        ]));
    }
    if (openMenu) {
        ReactElems.push(React.createElement('div', {
            key: 'menu',
            style: {
                position: 'absolute',
                top: '56px',
                right: 0,
                background: '#1f2937',
                border: '1px solid #374151',
                borderRadius: '8px',
                padding: '6px 0',
                minWidth: '220px',
                zIndex: 1000,
                boxShadow: '0 4px 12px rgba(0,0,0,0.3)'
            }
        }, [
            loadingActions && React.createElement('div', { key: 'loading', style: { padding: '8px 12px', color: '#9ca3af', fontSize: '12px' } }, 'Loading actions...'),
            !loadingActions && actions.length === 0 && React.createElement('div', { key: 'none', style: { padding: '8px 12px', color: '#9ca3af', fontSize: '12px' } }, 'No actions'),
            !loadingActions && actions.map((a) => React.createElement('button', {
                key: a.id,
                onClick: () => handleExecute(a.id),
                disabled: !!executing,
                style: {
                    display: 'flex',
                    alignItems: 'center',
                    gap: '6px',
                    width: '100%',
                    textAlign: 'left',
                    padding: '8px 12px',
                    background: 'transparent',
                    border: 'none',
                    color: '#e5e7eb',
                    fontSize: '13px',
                    cursor: 'pointer'
                },
                onMouseEnter: (e) => e.currentTarget.style.background = '#374151',
                onMouseLeave: (e) => e.currentTarget.style.background = 'transparent'
            }, [
                React.createElement('span', { key: 'svc', style: { opacity: 0.5, fontSize: '11px', letterSpacing: '0.5px' } }, a.service.toUpperCase()),
                React.createElement('span', { key: 'lbl', style: { flexGrow: 1 } }, a.label),
                a.result_kind === 'dialog' && React.createElement('span', { key: 'rk', style: { fontSize: '10px', color: '#93c5fd' } }, '↗'),
                executing === a.id && React.createElement('span', { key: 'exec', style: { fontSize: '10px' } }, '…')
            ]))
        ]));
    }
    return React.createElement('div', { className: 'minimal-ai-button', style: { position: 'relative', display: 'inline-block' } }, ReactElems);
};
window.MinimalAIButton = MinimalAIButton;
MinimalAIButton;
})();
