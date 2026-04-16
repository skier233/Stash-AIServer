// TaskDashboard (cleaned)
// Minimal responsibilities:
//  - Show active top-level tasks (no children listed) with progress inferred from children.
//  - Manual history fetch.
//  - Cancel queued/running parent tasks (single base URL resolution).
//  - Expand failed history rows to view/copy error.

interface HistoryItem { task_id: string; action_id: string; service: string; status: string; submitted_at: number; started_at?: number; finished_at?: number; duration_ms?: number | null; items_sent?: number | null; item_id?: string | null; error?: string | null; }

function resolveBackendBase(): string {
  try {
    const globalFn = (window as any).AIDefaultBackendBase;
    if (typeof globalFn === 'function') {
      const value = globalFn();
      if (typeof value === 'string') return value;
    }
  } catch {}
  try {
    const raw = (window as any).AI_BACKEND_URL;
    if (typeof raw === 'string') return raw.replace(/\/$/, '');
  } catch {
    return '';
  }
  return '';
}
const debug = () => !!(window as any).AIDebug;
const dlog = (...a:any[]) => { if (debug()) console.debug('[TaskDashboard]', ...a); };

function getSharedApiKey(): string {
  try {
    const helper = (window as any).AISharedApiKeyHelper;
    if (helper && typeof helper.get === 'function') {
      const value = helper.get();
      if (typeof value === 'string') return value.trim();
    }
  } catch {}
  const raw = (window as any).AI_SHARED_API_KEY;
  return typeof raw === 'string' ? raw.trim() : '';
}

function withSharedKeyHeaders(init?: any): any {
  const helper = (window as any).AISharedApiKeyHelper;
  if (helper && typeof helper.withHeaders === 'function') {
    return helper.withHeaders(init || {});
  }
  const key = getSharedApiKey();
  if (!key) return init || {};
  const headers = { ...(init && init.headers ? init.headers : {}) };
  headers['x-ai-api-key'] = key;
  return { ...(init || {}), headers };
}

function appendSharedKeyQuery(url: string): string {
  const helper = (window as any).AISharedApiKeyHelper;
  if (helper && typeof helper.appendQuery === 'function') {
    return helper.appendQuery(url);
  }
  const key = getSharedApiKey();
  if (!key) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}api_key=${encodeURIComponent(key)}`;
}

function ensureWS(baseHttp:string) {
  const g:any = window as any;
  if (!baseHttp) {
    try { g.__AI_TASK_WS__?.close?.(); } catch {}
    g.__AI_TASK_WS__ = null;
    g.__AI_TASK_WS_BASE__ = null;
    g.__AI_TASK_WS_INIT__ = false;
    return;
  }
  if (g.__AI_TASK_WS_BASE__ && g.__AI_TASK_WS_BASE__ !== baseHttp) {
    try { g.__AI_TASK_WS__?.close?.(); } catch {}
    g.__AI_TASK_WS__ = null;
    g.__AI_TASK_WS_INIT__ = false;
  }
  if (g.__AI_TASK_WS__ && g.__AI_TASK_WS__.readyState === 1 && g.__AI_TASK_WS_BASE__ === baseHttp) return;
  if (g.__AI_TASK_WS_INIT__) return;
  g.__AI_TASK_WS_INIT__ = true;
  g.__AI_TASK_WS_BASE__ = baseHttp;
  const base = baseHttp.replace(/^http/, 'ws');
  const candidates = [`${base}/api/v1/ws/tasks`, `${base}/ws/tasks`];
  const urls = candidates.map((u) => appendSharedKeyQuery(u));
  let connected = false;
  for (const u of urls) {
    try {
      const sock = new WebSocket(u);
      g.__AI_TASK_WS__ = sock;
      if (!g.__AI_TASK_CACHE__) g.__AI_TASK_CACHE__ = {};
      if (!g.__AI_TASK_PROGRESS__) g.__AI_TASK_PROGRESS__ = {};
      if (!g.__AI_TASK_WS_LISTENERS__) g.__AI_TASK_WS_LISTENERS__ = {};
      if (!g.__AI_TASK_ANY_LISTENERS__) g.__AI_TASK_ANY_LISTENERS__ = [];
      sock.onmessage = (evt: MessageEvent) => {
        try {
          const m = JSON.parse(evt.data);
          const task = m.task || m.data?.task || m.data || m;
          if (!task?.id) return;
          g.__AI_TASK_CACHE__[task.id] = task;
          // Store progress extra data for parent tasks
          if (m.type === 'task.progress' && m.extra) {
            g.__AI_TASK_PROGRESS__[task.id] = { ...m.extra, _ts: Date.now() };
          }
          const ls = g.__AI_TASK_WS_LISTENERS__[task.id]; if (ls) ls.forEach((fn: any) => fn(task));
          const anyLs = g.__AI_TASK_ANY_LISTENERS__; if (anyLs) anyLs.forEach((fn: any) => { try { fn(task); } catch {} });
        } catch {}
      };
      sock.onclose = () => {
        if (g.__AI_TASK_WS__ === sock) g.__AI_TASK_WS__ = null;
        g.__AI_TASK_WS_INIT__ = false;
        // Auto-reconnect
        if (baseHttp) {
          const delay = Math.min(30000, (g.__AI_TASK_WS_RECONNECT_DELAY__ || 1000));
          g.__AI_TASK_WS_RECONNECT_DELAY__ = Math.min(30000, delay * 2);
          setTimeout(() => { if (!g.__AI_TASK_WS__) ensureWS(baseHttp); }, delay);
        }
      };
      sock.onopen = () => { g.__AI_TASK_WS_RECONNECT_DELAY__ = 0; };
      connected = true;
      break;
    } catch {}
  }
  if (!connected) {
    g.__AI_TASK_WS_INIT__ = false;
  }
}

function listActiveParents(cache:any):any[] {
  const tasks = Object.values(cache || {}) as any[];
  return tasks.filter(t => !t.group_id && !['completed','failed','cancelled'].includes(t.status))
              .sort((a,b) => (a.submitted_at||0) - (b.submitted_at||0));
}

function computeProgress(task: any): { pct: number; done: number; total: number; running: number; failed: number } | null {
  const g: any = window as any;
  const cache = g.__AI_TASK_CACHE__ || {};
  const children = (Object.values(cache) as any[]).filter((c: any) => c.group_id === task.id);

  // Try progress extra data first (from WS emit_progress)
  const progressData = (g.__AI_TASK_PROGRESS__ || {})[task.id];
  if (progressData && progressData.total > 0) {
    const completed = progressData.completed || 0;
    const total = progressData.total;
    const pending = progressData.pending || 0;
    return {
      pct: Math.min(1, completed / total),
      done: completed,
      total: total,
      running: total - completed - pending - (progressData.failed || 0),
      failed: progressData.failed || 0,
    };
  }

  if (!children.length) return null;
  let done=0,running=0,queued=0,failed=0;
  for (const c of children) {
    switch(c.status) {
      case 'completed': done++; break;
      case 'running': running++; break;
      case 'queued': queued++; break;
      case 'failed': failed++; break;
    }
  }
  const effectiveTotal = done+running+queued+failed;
  if (!effectiveTotal) return null;
  const weighted = done + failed + running*0.5;
  return {
    pct: Math.min(1, weighted / effectiveTotal),
    done: done,
    total: effectiveTotal,
    running: running,
    failed: failed,
  };
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60) return `${totalSec}s`;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min < 60) return sec > 0 ? `${min}m ${sec}s` : `${min}m`;
  const hr = Math.floor(min / 60);
  const rm = min % 60;
  return rm > 0 ? `${hr}h ${rm}m` : `${hr}h`;
}

function computeEta(task: any, progress: ReturnType<typeof computeProgress>): string | null {
  if (!progress || progress.pct <= 0 || progress.pct >= 1) return null;
  const startedAt = task.started_at;
  if (!startedAt) return null;
  const elapsed = Date.now() / 1000 - startedAt;
  if (elapsed < 2) return null; // too early to estimate
  const totalEstimated = elapsed / progress.pct;
  const remaining = totalEstimated - elapsed;
  if (remaining < 1) return null;
  return formatDuration(remaining * 1000);
}

function statusColor(status: string): string {
  switch (status) {
    case 'running': return '#4dabf7';
    case 'queued': return '#868e96';
    case 'completed': return '#51cf66';
    case 'failed': return '#ff6b6b';
    case 'cancelled': return '#fcc419';
    default: return '#868e96';
  }
}

const TaskDashboard = () => {
  const React: any = (window as any).PluginApi?.React || (window as any).React;
  if (!React) { console.error('[TaskDashboard] React not found'); return null; }
  const [backendBase, setBackendBase] = React.useState(() => resolveBackendBase());
  const [active, setActive] = React.useState([] as any[]);
  const [history, setHistory] = React.useState([] as HistoryItem[]);
  const [loadingHistory, setLoadingHistory] = React.useState(false as boolean);
  const [filterService, setFilterService] = React.useState(null as string | null);
  const [expanded, setExpanded] = React.useState(new Set<string>());
  const [cancelling, setCancelling] = React.useState(new Set<string>());
  const [tick, setTick] = React.useState(0);

  React.useEffect(() => { ensureWS(backendBase); }, [backendBase]);

  React.useEffect(() => {
    const handleBaseUpdate = () => {
      const next = resolveBackendBase();
      setBackendBase((prev: string) => (next === prev ? prev : next));
    };
    try { window.addEventListener('AIBackendBaseUpdated', handleBaseUpdate as EventListener); } catch {}
    return () => { try { window.removeEventListener('AIBackendBaseUpdated', handleBaseUpdate as EventListener); } catch {} };
  }, []);

  // Active tasks tracking via WS "any" listener
  React.useEffect(() => {
    const g: any = window as any;
    if (!g.__AI_TASK_ANY_LISTENERS__) g.__AI_TASK_ANY_LISTENERS__ = [];
    const pull = () => { const cache = g.__AI_TASK_CACHE__ || {}; setActive(listActiveParents(cache)); };
    pull();
    const listener = () => pull();
    g.__AI_TASK_ANY_LISTENERS__.push(listener);
    return () => { g.__AI_TASK_ANY_LISTENERS__ = (g.__AI_TASK_ANY_LISTENERS__ || []).filter((fn: any) => fn !== listener); };
  }, []);

  // Periodic tick for ETA/elapsed updates (every 2s)
  React.useEffect(() => {
    const interval = setInterval(() => setTick((v: number) => v + 1), 2000);
    return () => clearInterval(interval);
  }, []);

  // Auto-refresh history every 30s
  const fetchHistory = React.useCallback(async () => {
    if (!backendBase) {
      setLoadingHistory(false);
      setHistory([]);
      return;
    }
    setLoadingHistory(true);
    try {
      const url = new URL(`${backendBase}/api/v1/tasks/history`);
      url.searchParams.set('limit','50');
      if (filterService) url.searchParams.set('service', filterService);
      const res = await fetch(url.toString(), withSharedKeyHeaders());
      if (!res.ok) return;
      const ct = (res.headers.get('content-type') || '').toLowerCase();
      if (!ct.includes('application/json')) return;
      const data = await res.json();
      if (data && Array.isArray(data.history)) setHistory(data.history);
    } finally { setLoadingHistory(false); }
  }, [backendBase, filterService]);

  React.useEffect(() => { fetchHistory(); }, [fetchHistory]);
  React.useEffect(() => {
    const interval = setInterval(fetchHistory, 30000);
    return () => clearInterval(interval);
  }, [fetchHistory]);

  function toggleExpand(id: string) { setExpanded((prev: Set<string>) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; }); }
  function copyToClipboard(text: string) { try { navigator.clipboard?.writeText(text); } catch { try { (window as any).prompt('Copy error text manually:', text); } catch {} } }
  async function cancelTask(id: string) {
    if (!backendBase) { alert('AI backend URL is not configured.'); return; }
    setCancelling((prev: Set<string>) => { const n = new Set(prev); n.add(id); return n; });
    try {
      const res = await fetch(`${backendBase}/api/v1/tasks/${id}/cancel`, withSharedKeyHeaders({ method: 'POST' }));
      if (!res.ok) throw new Error('Cancel failed HTTP '+res.status);
    } catch (e: any) {
      setCancelling((prev: Set<string>) => { const n = new Set(prev); n.delete(id); return n; });
      alert('Cancel failed: ' + (e.message || 'unknown'));
    }
  }

  const formatTs = (v?: number) => v ? new Date(v*1000).toLocaleTimeString() : '-';
  const services = Array.from(new Set((history as any[]).map(h => h.service).concat((active as any[]).map(a => a.service))));

  // Helper: elapsed since task started
  const elapsedStr = (t: any): string => {
    if (!t.started_at) return '-';
    const ms = (Date.now() / 1000 - t.started_at) * 1000;
    return formatDuration(ms);
  };

  // Helper: queue position
  const queuePosition = (t: any): number | null => {
    if (t.status !== 'queued') return null;
    const queued = active.filter((a: any) => a.status === 'queued').sort((a: any, b: any) => (a.submitted_at || 0) - (b.submitted_at || 0));
    const idx = queued.findIndex((a: any) => a.id === t.id);
    return idx >= 0 ? idx + 1 : null;
  };

  // ---- Render ----
  const h = React.createElement;

  // Progress bar component
  const ProgressBar = (props: { pct: number; color?: string }) => {
    const pctClamped = Math.max(0, Math.min(100, Math.round(props.pct * 100)));
    return h('div', { className: 'ai-task-progress-bar' }, [
      h('div', {
        key: 'fill',
        className: 'ai-task-progress-bar__fill',
        style: { width: `${pctClamped}%`, background: props.color || '#4dabf7' },
      }),
      h('span', { key: 'label', className: 'ai-task-progress-bar__label' }, `${pctClamped}%`),
    ]);
  };

  const renderActiveTask = (t: any) => {
    const prog = computeProgress(t);
    const eta = prog ? computeEta(t, prog) : null;
    const isCancelling = cancelling.has(t.id);
    const qPos = queuePosition(t);
    // Force re-read of tick to ensure elapsed/eta updates (it is used implicitly via Date.now())
    void tick;

    return h('div', { key: t.id, className: `ai-task-card ai-task-card--${t.status}` }, [
      // Header row
      h('div', { key: 'hdr', className: 'ai-task-card__header' }, [
        h('span', { key: 'svc', className: 'ai-task-card__service' }, t.service?.toUpperCase?.() || t.service),
        h('span', { key: 'action', className: 'ai-task-card__action' }, t.action_id),
        h('span', {
          key: 'status',
          className: 'ai-task-card__status',
          style: { color: statusColor(t.status) },
        }, t.status.toUpperCase() + (isCancelling ? ' (cancelling...)' : '') + (qPos ? ` #${qPos} in queue` : '')),
      ]),
      // Progress bar
      prog && h('div', { key: 'prog', className: 'ai-task-card__progress' }, [
        ProgressBar({ pct: prog.pct, color: prog.failed > 0 ? '#fcc419' : undefined }),
      ]),
      // Detail row: items, elapsed, ETA
      h('div', { key: 'detail', className: 'ai-task-card__detail' }, [
        prog && h('span', { key: 'items', className: 'ai-task-card__items' },
          `${prog.done}/${prog.total} items` + (prog.running > 0 ? ` (${prog.running} running)` : '') + (prog.failed > 0 ? ` (${prog.failed} failed)` : ''),
        ),
        t.started_at && h('span', { key: 'elapsed', className: 'ai-task-card__elapsed' }, `Elapsed: ${elapsedStr(t)}`),
        eta && h('span', { key: 'eta', className: 'ai-task-card__eta' }, `ETA: ~${eta}`),
      ]),
      // Cancel button
      (t.status === 'queued' || t.status === 'running') && h('button', {
        key: 'cancel',
        disabled: isCancelling,
        className: 'ai-task-card__cancel',
        onClick: () => cancelTask(t.id),
      }, isCancelling ? 'Cancelling…' : 'Cancel'),
    ]);
  };

  const renderHistoryRow = (item: HistoryItem) => {
    const isFailed = item.status === 'failed';
    const isExpanded = expanded.has(item.task_id);
    const rowClasses = ['ai-task-hist-row'];
    if (isFailed) rowClasses.push('ai-task-hist-row--failed');
    if (isExpanded) rowClasses.push('ai-task-hist-row--expanded');

    const durStr = item.duration_ms != null ? formatDuration(item.duration_ms) : '-';

    return h(React.Fragment, { key: item.task_id }, [
      h('div', {
        key: 'row',
        className: rowClasses.join(' '),
        onClick: () => { if (isFailed) toggleExpand(item.task_id); },
        style: isFailed ? { cursor: 'pointer' } : undefined,
      }, [
        h('span', { key: 'svc', className: 'ai-task-hist-row__svc' }, item.service),
        h('span', { key: 'act', className: 'ai-task-hist-row__action' }, item.action_id),
        h('span', {
          key: 'status',
          className: 'ai-task-hist-row__status',
          style: { color: statusColor(item.status) },
        }, item.status + (isFailed ? (isExpanded ? ' ▲' : ' ▼') : '')),
        h('span', { key: 'dur', className: 'ai-task-hist-row__dur' }, durStr),
        h('span', { key: 'time', className: 'ai-task-hist-row__time' }, formatTs(item.finished_at || item.started_at)),
      ]),
      isFailed && isExpanded && item.error && h('div', { key: 'err', className: 'ai-task-hist-row__error' }, [
        h('pre', { key: 'pre', style: { margin: 0, whiteSpace: 'pre-wrap', fontSize: '12px', lineHeight: '1.3', background: '#330', color: '#fdd', padding: '6px', borderRadius: '4px', maxHeight: '200px', overflow: 'auto' } }, item.error),
        h('div', { key: 'btns', style: { marginTop: '4px', display: 'flex', gap: '8px' } }, [
          h('button', { key: 'copy', onClick: (e: any) => { e.stopPropagation(); copyToClipboard(item.error!); } }, 'Copy Error'),
          h('button', { key: 'close', onClick: (e: any) => { e.stopPropagation(); toggleExpand(item.task_id); } }, 'Close'),
        ]),
      ]),
    ]);
  };

  return h('div', { className: 'ai-task-dashboard' }, [
    // Header
    h('div', { key: 'hdr', className: 'ai-task-dash__header' }, [
      h('h3', { key: 'title' }, 'AI Tasks'),
      h('div', { key: 'filters', className: 'ai-task-dash__filters' }, [
        h('select', { key: 'svc', value: filterService || '', onChange: (e: any) => setFilterService(e.target.value || null) }, [
          h('option', { key: 'all', value: '' }, 'All Services'),
          ...services.map((s: string) => h('option', { key: s, value: s }, s)),
        ]),
        h('button', { key: 'refresh', onClick: fetchHistory, disabled: loadingHistory },
          loadingHistory ? 'Refreshing…' : 'Refresh'),
      ]),
    ]),
    // Active tasks
    h('div', { key: 'active', className: 'ai-task-dash__section' }, [
      h('h4', { key: 'lbl' }, `Active (${active.length})`),
      active.length === 0 && h('div', { key: 'none', className: 'ai-task-dash__empty' }, 'No active tasks'),
      ...(active as any[]).map(renderActiveTask),
    ]),
    // History
    h('div', { key: 'hist', className: 'ai-task-dash__section' }, [
      h('h4', { key: 'lbl' }, 'Recent History'),
      history.length === 0 && h('div', { key: 'none', className: 'ai-task-dash__empty' }, 'No recent tasks'),
      ...(history as any[]).map(renderHistoryRow),
    ]),
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
