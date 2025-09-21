// TaskDashboard: Displays active tasks (live from websocket cache) and recent history (fetched via REST)
// Lightweight, no external deps beyond global React already used by AIButton.

interface TaskSummary { id: string; action_id: string; service: string; status: string; group_id?: string | null; submitted_at: number; started_at?: number; finished_at?: number; error?: string | null; }
interface HistoryItem { task_id: string; action_id: string; service: string; status: string; submitted_at: number; started_at?: number; finished_at?: number; duration_ms?: number | null; items_sent?: number | null; item_id?: string | null; error?: string | null; }

const TaskDashboard = () => {
  const React: any = (window as any).PluginApi?.React || (window as any).React;
  if (!React) { console.error('[TaskDashboard] React not found'); return null; }
  // Derive backend base more robustly: allow explicit override, else same-origin, else localhost dev.
  function resolveBackendBase(): string {
    const g: any = window as any;
    if (g.AI_BACKEND_URL && typeof g.AI_BACKEND_URL === 'string') return g.AI_BACKEND_URL.replace(/\/$/, '');
    // Prefer same-origin (assumes backend mounted at root /api/v1/* via proxy)
    if (location && location.origin) return location.origin;
    return 'http://localhost:8000';
  }
  const backendBase = resolveBackendBase();
  // Avoid generic type params because React may be the untyped global provided by host.
  const [active, setActive] = React.useState([] as any[]);
  const [history, setHistory] = React.useState([] as HistoryItem[]);
  const [loadingHistory, setLoadingHistory] = React.useState(false as boolean);
  const [filterService, setFilterService] = React.useState(null as string | null);

  // Derive tasks from global cache and re-render when any task updates
  React.useEffect(() => {
    const g: any = window as any;
    if (!g.__AI_TASK_ANY_LISTENERS__) g.__AI_TASK_ANY_LISTENERS__ = [];
    const pull = () => {
      const cache = g.__AI_TASK_CACHE__ || {};
      const tasks = Object.values(cache) as any[];
  const activeTasks = tasks.filter(t => !t.group_id && !['completed','failed','cancelled'].includes(t.status));
      setActive(activeTasks.sort((a,b) => (a.submitted_at||0) - (b.submitted_at||0)));
    };
    pull();
    const listener = (_t: any) => pull();
    g.__AI_TASK_ANY_LISTENERS__.push(listener);
    return () => { g.__AI_TASK_ANY_LISTENERS__ = (g.__AI_TASK_ANY_LISTENERS__ || []).filter((fn: any) => fn !== listener); };
  }, []);

  const fetchHistory = React.useCallback(async () => {
    setLoadingHistory(true);
    try {
      let lastErr: any = null;
      const bases: string[] = [];
      const g: any = window as any;
      if (g.AI_BACKEND_URL) bases.push(g.AI_BACKEND_URL.replace(/\/$/, ''));
      if (!bases.includes(backendBase)) bases.push(backendBase);
      if (!bases.includes('http://localhost:8000')) bases.push('http://localhost:8000');
    // Try canonical first; include transitional double-stack fallback for one release window.
    const pathVariants = ['/api/v1/tasks/history', '/api/v1/tasks/tasks/history'];
      for (const b of bases) {
        for (const pv of pathVariants) {
          try {
            const url = new URL(`${b}${pv}`);
            url.searchParams.set('limit','50');
            if (filterService) url.searchParams.set('service', filterService);
            const full = url.toString();
            const res = await fetch(full);
            if (!res.ok) {
              // Only log non-404 errors to reduce noise when one variant doesn't exist
              if (res.status !== 404) console.warn('[TaskDashboard] history fetch non-OK', full, res.status);
              lastErr = new Error('HTTP ' + res.status);
              continue;
            }
            const data = await res.json();
            setHistory(data.history || []);
            if ((window as any).AIDebug) console.log('[TaskDashboard] history loaded', data.history?.length, 'from', full);
            return;
          } catch (err) {
            lastErr = err;
            // proceed to next variant/base
          }
        }
      }
      console.error('[TaskDashboard] All history fetch attempts failed', lastErr);
    } finally { setLoadingHistory(false); }
  }, [backendBase, filterService]);

  React.useEffect(() => { fetchHistory(); }, [fetchHistory]);

  // Progress for parent tasks (controller) using children in cache
  function computeProgress(task: any): number | null {
    if (!task) return null;
    const g: any = window as any;
    const cache = g.__AI_TASK_CACHE__ || {};
  const children = (Object.values(cache) as any[]).filter((c: any) => c.group_id === task.id);
    if (!children.length) return null;
    let done=0, running=0, queued=0, failed=0, cancelled=0;
    for (const c of children as any[]) {
      switch(c.status){
        case 'completed': done++; break;
        case 'running': running++; break;
        case 'queued': queued++; break;
        case 'failed': failed++; break;
        case 'cancelled': cancelled++; break;
      }
    }
    const effectiveTotal = done + running + queued + failed; // ignore cancelled in denominator
    if (!effectiveTotal) return 0;
    const weighted = done + failed + running * 0.5;
    return Math.min(1, weighted / effectiveTotal);
  }

  const ReactEl = (window as any).React || (window as any).PluginApi?.React;
  const formatTs = (v?: number) => v ? new Date(v*1000).toLocaleTimeString() : '-';

  const services = Array.from(new Set((history as any[]).map((h: any) => h.service).concat((active as any[]).map((a: any) => a.service))));

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
        const prog = computeProgress(t);
        return React.createElement('div', { key: t.id, className: 'ai-task-row' }, [
          React.createElement('div', { key: 'svc', className: 'ai-task-row__svc' }, t.service),
          React.createElement('div', { key: 'act', className: 'ai-task-row__action' }, t.action_id),
          React.createElement('div', { key: 'status', className: 'ai-task-row__status' }, t.status),
          React.createElement('div', { key: 'progress', className: 'ai-task-row__progress' }, prog != null ? `${Math.round(prog*100)}%` : ''),
          React.createElement('div', { key: 'times', className: 'ai-task-row__times' }, formatTs(t.started_at))
        ]);
      })
    ]),
    React.createElement('div', { key: 'hist', className: 'ai-task-dash__section' }, [
      React.createElement('h4', { key: 'lbl' }, 'Recent History'),
      history.length === 0 && React.createElement('div', { key: 'none', className: 'ai-task-dash__empty' }, 'No recent tasks'),
  ...(history as any[]).map((h: any) => React.createElement('div', { key: h.task_id, className: 'ai-task-row ai-task-row--history' }, [
        React.createElement('div', { key: 'svc', className: 'ai-task-row__svc' }, h.service),
        React.createElement('div', { key: 'act', className: 'ai-task-row__action' }, h.action_id),
        React.createElement('div', { key: 'status', className: 'ai-task-row__status' }, h.status),
        React.createElement('div', { key: 'dur', className: 'ai-task-row__progress' }, h.duration_ms != null ? `${h.duration_ms}ms` : ''),
        React.createElement('div', { key: 'time', className: 'ai-task-row__times' }, formatTs(h.finished_at || h.started_at))
      ]))
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
