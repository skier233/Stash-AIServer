// TaskDashboard (cleaned)
// Minimal responsibilities:
//  - Show active top-level tasks (no children listed) with progress inferred from children.
//  - Manual history fetch.
//  - Cancel queued/running parent tasks (single base URL resolution).
//  - Expand failed history rows to view/copy error.

interface HistoryItem { task_id: string; action_id: string; service: string; status: string; submitted_at: number; started_at?: number; finished_at?: number; duration_ms?: number | null; items_sent?: number | null; item_id?: string | null; error?: string | null; }

defaultBackendBase(); // hoist helper for potential tree-shake clarity (no effect at runtime)
function defaultBackendBase() {
  const explicit = (window as any).AI_BACKEND_URL as string | undefined;
  if (explicit) return explicit.replace(/\/$/, '');
  const loc = (location && location.origin) || '';
  try { const u = new URL(loc); if (u.port === '3000') { u.port = '8000'; return u.toString().replace(/\/$/, ''); } } catch {}
  return (loc || 'http://localhost:8000').replace(/\/$/, '');
}
const debug = () => !!(window as any).AIDebug;
const dlog = (...a:any[]) => { if (debug()) console.debug('[TaskDashboard]', ...a); };

function ensureWS(baseHttp:string) {
  const g:any = window as any;
  if (g.__AI_TASK_WS__ && g.__AI_TASK_WS__.readyState === 1) return;
  if (g.__AI_TASK_WS_INIT__) return;
  g.__AI_TASK_WS_INIT__ = true;
  const base = baseHttp.replace(/^http/, 'ws'); const urls = [`${base}/api/v1/ws/tasks`, `${base}/ws/tasks`];
  for (const u of urls) {
    try {
      const sock = new WebSocket(u);
      g.__AI_TASK_WS__ = sock;
      if (!g.__AI_TASK_CACHE__) g.__AI_TASK_CACHE__ = {};
      if (!g.__AI_TASK_WS_LISTENERS__) g.__AI_TASK_WS_LISTENERS__ = {};
      if (!g.__AI_TASK_ANY_LISTENERS__) g.__AI_TASK_ANY_LISTENERS__ = [];
      sock.onmessage = (evt: MessageEvent) => {
        try {
          const m = JSON.parse(evt.data); const task = m.task || m.data?.task || m.data || m; if (!task?.id) return;
          g.__AI_TASK_CACHE__[task.id] = task;
          const ls = g.__AI_TASK_WS_LISTENERS__[task.id]; if (ls) ls.forEach((fn: any) => fn(task));
          const anyLs = g.__AI_TASK_ANY_LISTENERS__; if (anyLs) anyLs.forEach((fn: any) => { try { fn(task); } catch {} });
        } catch {}
      };
      sock.onclose = () => { if (g.__AI_TASK_WS__ === sock) g.__AI_TASK_WS__ = null; g.__AI_TASK_WS_INIT__ = false; };
      break;
    } catch {}
  }
}

function listActiveParents(cache:any):any[] {
  const tasks = Object.values(cache || {}) as any[];
  return tasks.filter(t => !t.group_id && !['completed','failed','cancelled'].includes(t.status))
              .sort((a,b) => (a.submitted_at||0) - (b.submitted_at||0));
}

function computeProgress(task: any): number | null {
  const g: any = window as any; const cache = g.__AI_TASK_CACHE__ || {}; const children = (Object.values(cache) as any[]).filter((c: any) => c.group_id === task.id);
  if (!children.length) return null;
  let done=0,running=0,queued=0,failed=0,cancelled=0;
  for (const c of children) { switch(c.status){ case 'completed': done++; break; case 'running': running++; break; case 'queued': queued++; break; case 'failed': failed++; break; case 'cancelled': cancelled++; break; } }
  const effectiveTotal = done+running+queued+failed; if (!effectiveTotal) return 0; const weighted = done + failed + running*0.5; return Math.min(1, weighted / effectiveTotal);
}

const TaskDashboard = () => {
  const React: any = (window as any).PluginApi?.React || (window as any).React;
  if (!React) { console.error('[TaskDashboard] React not found'); return null; }
  const [backendBase] = React.useState(() => defaultBackendBase());
  const [active, setActive] = React.useState([] as any[]);
  const [history, setHistory] = React.useState([] as HistoryItem[]);
  const [loadingHistory, setLoadingHistory] = React.useState(false as boolean);
  const [filterService, setFilterService] = React.useState(null as string | null);
  const [expanded, setExpanded] = React.useState(new Set<string>());
  const [cancelling, setCancelling] = React.useState(new Set<string>());

  React.useEffect(() => { ensureWS(backendBase); }, [backendBase]);

  // Active tasks tracking
  React.useEffect(() => {
    const g: any = window as any; if (!g.__AI_TASK_ANY_LISTENERS__) g.__AI_TASK_ANY_LISTENERS__ = [];
    const pull = () => { const cache = g.__AI_TASK_CACHE__ || {}; setActive(listActiveParents(cache)); };
    pull();
    const listener = () => pull();
    g.__AI_TASK_ANY_LISTENERS__.push(listener);
    return () => { g.__AI_TASK_ANY_LISTENERS__ = (g.__AI_TASK_ANY_LISTENERS__ || []).filter((fn: any) => fn !== listener); };
  }, []);

  const fetchHistory = React.useCallback(async () => {
    setLoadingHistory(true);
    try {
      const url = new URL(`${backendBase}/api/v1/tasks/history`); url.searchParams.set('limit','50'); if (filterService) url.searchParams.set('service', filterService); if (debug()) dlog('Fetch history URL:', url.toString());
      const res = await fetch(url.toString()); if (!res.ok) return; const ct = (res.headers.get('content-type') || '').toLowerCase(); if (!ct.includes('application/json')) return; const data = await res.json(); if (data && Array.isArray(data.history)) setHistory(data.history);
    } finally { setLoadingHistory(false); }
  }, [backendBase, filterService]);
  React.useEffect(() => { fetchHistory(); }, [fetchHistory]);

  function toggleExpand(id: string) { setExpanded((prev: Set<string>) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; }); }
  function copyToClipboard(text: string) { try { navigator.clipboard?.writeText(text); } catch { try { (window as any).prompt('Copy error text manually:', text); } catch {} } }
  async function cancelTask(id: string) { setCancelling((prev: Set<string>) => { const n = new Set(prev); n.add(id); return n; }); try { const res = await fetch(`${backendBase}/api/v1/tasks/${id}/cancel`, { method: 'POST' }); if (!res.ok) throw new Error('Cancel failed HTTP '+res.status); } catch (e: any) { setCancelling((prev: Set<string>) => { const n = new Set(prev); n.delete(id); return n; }); alert('Cancel failed: ' + (e.message || 'unknown')); } }

  const formatTs = (v?: number) => v ? new Date(v*1000).toLocaleTimeString() : '-';
  const services = Array.from(new Set((history as any[]).map(h => h.service).concat((active as any[]).map(a => a.service))));

  // ---- Render (structure & classNames intentionally unchanged) ----
  return React.createElement('div', { className: 'ai-task-dashboard' }, [
    React.createElement('div', { key: 'hdr', className: 'ai-task-dash__header' }, [
      React.createElement('h3', { key: 'title' }, 'AI Tasks'),
      React.createElement('div', { key: 'filters', className: 'ai-task-dash__filters' }, [
        React.createElement('select', { key: 'svc', value: filterService || '', onChange: (e: any) => setFilterService(e.target.value || null) }, [
          React.createElement('option', { key: 'all', value: '' }, 'All Services'),
          ...services.map(s => React.createElement('option', { key: s, value: s }, s))
        ]),
        React.createElement('button', { key: 'refresh', onClick: fetchHistory, disabled: loadingHistory }, loadingHistory ? 'Refreshing…' : 'Refresh')
      ])
    ]),
    React.createElement('div', { key: 'active', className: 'ai-task-dash__section' }, [
      React.createElement('h4', { key: 'lbl' }, 'Active'),
      active.length === 0 && React.createElement('div', { key: 'none', className: 'ai-task-dash__empty' }, 'No active tasks'),
      ...(active as any[]).map((t: any) => {
        const prog = computeProgress(t); const isCancelling = cancelling.has(t.id);
        return React.createElement('div', { key: t.id, className: 'ai-task-row' }, [
          React.createElement('div', { key: 'svc', className: 'ai-task-row__svc' }, t.service),
            React.createElement('div', { key: 'act', className: 'ai-task-row__action' }, t.action_id),
            React.createElement('div', { key: 'status', className: 'ai-task-row__status' }, t.status + (isCancelling ? ' (cancelling...)' : '')),
            React.createElement('div', { key: 'progress', className: 'ai-task-row__progress' }, prog != null ? `${Math.round(prog*100)}%` : ''),
            React.createElement('div', { key: 'times', className: 'ai-task-row__times' }, formatTs(t.started_at)),
            (t.status === 'queued' || t.status === 'running') && React.createElement('button', { key: 'cancel', disabled: isCancelling, className: 'ai-task-row__cancel', onClick: () => cancelTask(t.id), style: { marginLeft: 8 } }, isCancelling ? 'Cancelling…' : 'Cancel')
        ]);
      })
    ]),
    React.createElement('div', { key: 'hist', className: 'ai-task-dash__section' }, [
      React.createElement('h4', { key: 'lbl' }, 'Recent History'),
      history.length === 0 && React.createElement('div', { key: 'none', className: 'ai-task-dash__empty' }, 'No recent tasks'),
      ...(history as any[]).map(h => {
        const isFailed = h.status === 'failed'; const isExpanded = expanded.has(h.task_id);
        const rowClasses = ['ai-task-row','ai-task-row--history']; if (isFailed) rowClasses.push('ai-task-row--failed'); if (isExpanded) rowClasses.push('ai-task-row--expanded');
        return React.createElement(React.Fragment, { key: h.task_id }, [
          React.createElement('div', { key: 'row', className: rowClasses.join(' '), onClick: () => { if (isFailed) toggleExpand(h.task_id); }, style: isFailed ? { cursor: 'pointer' } : undefined }, [
            React.createElement('div', { key: 'svc', className: 'ai-task-row__svc' }, h.service),
            React.createElement('div', { key: 'act', className: 'ai-task-row__action' }, h.action_id),
            React.createElement('div', { key: 'status', className: 'ai-task-row__status' }, h.status + (isFailed ? (isExpanded ? ' ▲' : ' ▼') : '')),
            React.createElement('div', { key: 'dur', className: 'ai-task-row__progress' }, h.duration_ms != null ? `${h.duration_ms}ms` : ''),
            React.createElement('div', { key: 'time', className: 'ai-task-row__times' }, formatTs(h.finished_at || h.started_at))
          ]),
          isFailed && isExpanded && h.error && React.createElement('div', { key: 'err', className: 'ai-task-row__errorDetail' }, [
            React.createElement('pre', { key: 'pre', style: { margin: 0, whiteSpace: 'pre-wrap', fontSize: '12px', lineHeight: '1.3', background: '#330', color: '#fdd', padding: '6px', borderRadius: '4px', maxHeight: '200px', overflow: 'auto' } }, h.error),
            React.createElement('div', { key: 'btns', style: { marginTop: '4px', display: 'flex', gap: '8px' } }, [
              React.createElement('button', { key: 'copy', onClick: (e: any) => { e.stopPropagation(); copyToClipboard(h.error!); } }, 'Copy Error'),
              React.createElement('button', { key: 'close', onClick: (e: any) => { e.stopPropagation(); toggleExpand(h.task_id); } }, 'Close')
            ])
          ])
        ]);
      })
    ])
  ]);
};

(window as any).TaskDashboard = TaskDashboard;
(window as any).AITaskDashboard = TaskDashboard;
(window as any).AITaskDashboardMount = function(container: HTMLElement) {
  const React: any = (window as any).PluginApi?.React || (window as any).React;
  const ReactDOM: any = (window as any).ReactDOM || (window as any).PluginApi?.ReactDOM;
  if (!React || !ReactDOM) { console.error('[TaskDashboard] React or ReactDOM not available'); return; }
  ReactDOM.render(React.createElement(TaskDashboard, {}), container);
};
export default TaskDashboard;
