// =============================================================================
// Plugin Settings & Manager Panel (MVP)
//  - Local UI settings (backend URL override, interaction tracking toggle)
//  - Lists installed backend plugins (name/human/version/status)
//  - Shows update availability by comparing to catalog versions
//  - Source management: list/add/remove sources, refresh catalogs
//  - Browse a selected source's catalog and install/update/remove plugins
//  - Minimal styling via inline styles to avoid new CSS dependencies
//  - Exposed as window.AIPluginSettings and route /plugins/ai-settings (integration file registers it)
// =============================================================================

interface InstalledPlugin { name: string; version: string; status: string; required_backend: string; migration_head?: string|null; last_error?: string|null; human_name?: string|null; server_link?: string|null; }
interface Source { id: number; name: string; url: string; enabled: boolean; last_refreshed_at?: string|null; last_error?: string|null; }
interface CatalogEntry { plugin_name: string; version: string; description?: string; manifest?: any; }

const PATH_SLASH_MODES = ['auto', 'unix', 'win', 'unchanged'];
const PATH_SLASH_MODE_LABELS: Record<string, string> = {
  auto: 'Auto',
  unix: 'Unix',
  win: 'Windows',
  unchanged: 'Keep',
};
const PATH_SLASH_MODE_SET = new Set(PATH_SLASH_MODES);

// Legacy localStorage keys retained for one-time migration.
const LEGACY_BACKEND_URL = 'AI_BACKEND_URL_OVERRIDE';
const LEGACY_INTERACTIONS = 'AI_INTERACTIONS_ENABLED';
const THIS_PLUGIN_NAME = 'AIOverhaul';
// Fallback base used when no override has been persisted yet.
const DEFAULT_BACKEND_BASE_URL = 'http://localhost:4153';

const DEFAULT_MIN_BACKEND_VERSION = '>=0.8.0';
const DEV_VERSION_PATTERN = /(dev|local|snapshot|dirty)/i;

function resolveFrontendBackendRequirement(): string {
  try {
    if (typeof window !== 'undefined') {
      const globals = window as any;
      const override = globals.AIRequiredBackendVersion || globals.AI_MIN_BACKEND_VERSION || globals.AI_FRONTEND_MIN_BACKEND_VERSION;
      if (typeof override === 'string' && override.trim()) {
        return override.trim();
      }
    }
  } catch (_) {}
  return DEFAULT_MIN_BACKEND_VERSION;
}

const FRONTEND_MIN_BACKEND_VERSION = resolveFrontendBackendRequirement();

type VersionClause = { operator: string; version: string };

function isDevBuildVersion(value: string | null | undefined): boolean {
  if (typeof value !== 'string') return false;
  const normalized = value.trim().toLowerCase();
  if (!normalized) return false;
  if (normalized.startsWith('0.0.0')) return true;
  return DEV_VERSION_PATTERN.test(normalized);
}

function parseVersionClause(raw: string): VersionClause {
  const trimmed = raw.trim();
  if (!trimmed) return { operator: '==', version: '' };
  for (const op of ['>=', '<=', '>', '<', '==', '=']) {
    if (trimmed.startsWith(op)) {
      return { operator: op, version: trimmed.slice(op.length).trim() };
    }
  }
  return { operator: '==', version: trimmed };
}

type Token = { numeric: boolean; num: number; raw: string };

function tokenizeVersion(value: string): Token[] {
  return value
    .split(/[.\-+]/)
    .map((segment) => segment.trim())
    .filter((segment) => segment.length > 0)
    .map((segment) => {
      const parsed = parseInt(segment, 10);
      if (!Number.isNaN(parsed)) {
        return { numeric: true, num: parsed, raw: segment.toLowerCase() };
      }
      return { numeric: false, num: 0, raw: segment.toLowerCase() };
    });
}

function compareVersionsLoose(aRaw: string | null | undefined, bRaw: string | null | undefined): number {
  const a = typeof aRaw === 'string' ? aRaw.trim() : '';
  const b = typeof bRaw === 'string' ? bRaw.trim() : '';
  if (!a && !b) return 0;
  if (!a) return -1;
  if (!b) return 1;
  const tokensA = tokenizeVersion(a);
  const tokensB = tokenizeVersion(b);
  const max = Math.max(tokensA.length, tokensB.length);
  for (let i = 0; i < max; i += 1) {
    const tokenA = tokensA[i] || { numeric: true, num: 0, raw: '0' };
    const tokenB = tokensB[i] || { numeric: true, num: 0, raw: '0' };
    if (tokenA.numeric && tokenB.numeric) {
      if (tokenA.num > tokenB.num) return 1;
      if (tokenA.num < tokenB.num) return -1;
      continue;
    }
    if (tokenA.numeric !== tokenB.numeric) {
      return tokenA.numeric ? 1 : -1;
    }
    if (tokenA.raw === tokenB.raw) continue;
    return tokenA.raw > tokenB.raw ? 1 : -1;
  }
  return 0;
}

function versionSatisfiesRule(actual: string | null | undefined, rule: string | null | undefined): boolean {
  if (!rule || !rule.trim()) return true;
  if (!actual) return false;
  if (isDevBuildVersion(actual)) return true;
  const clauses = rule.replace(/,/g, ' ').split(/\s+/).map((c) => c.trim()).filter(Boolean);
  if (!clauses.length) return true;
  for (const clause of clauses) {
    const { operator, version } = parseVersionClause(clause);
    if (!version) continue;
    const cmp = compareVersionsLoose(actual, version);
    switch (operator) {
      case '>=':
        if (cmp < 0) return false;
        break;
      case '>':
        if (cmp <= 0) return false;
        break;
      case '<=':
        if (cmp > 0) return false;
        break;
      case '<':
        if (cmp >= 0) return false;
        break;
      case '=':
      case '==':
      default:
        if (cmp !== 0) return false;
        break;
    }
  }
  return true;
}

function formatVersionRequirement(rule: string | null | undefined): string {
  if (!rule || !rule.trim()) return '';
  const clauses = rule.replace(/,/g, ' ').split(/\s+/).map((c) => c.trim()).filter(Boolean);
  const symbolFor = (op: string) => {
    if (op === '>=') return '≥';
    if (op === '<=') return '≤';
    if (op === '==') return '=';
    if (op === '=') return '=';
    return op;
  };
  return clauses
    .map((clause) => {
      const { operator, version } = parseVersionClause(clause);
      if (!version) return '';
      return `${symbolFor(operator)} ${version}`.trim();
    })
    .filter(Boolean)
    .join(' ');
}

type SelfSettingDefinition = {
  key: string;
  label: string;
  type: 'string' | 'boolean';
  default: any;
  description?: string;
  options?: any;
};

const SELF_SETTING_DEFS: SelfSettingDefinition[] = [
  {
    key: 'backend_base_url',
    label: 'Backend Base URL Override',
    type: 'string',
    default: DEFAULT_BACKEND_BASE_URL,
    description: 'Override the base URL the AI Overhaul frontend uses when calling the AI backend.',
  },
  {
    key: 'capture_events',
    label: 'Capture Interaction Events',
    type: 'boolean',
    default: true,
    description: 'Mirror Stash interaction events to the AI backend for training and analytics.',
  },
  {
    key: 'shared_api_key',
    label: 'Shared API Key',
    type: 'string',
    default: '',
    description: 'Secret sent with every AI Overhaul request when the backend shared key is enabled.',
  },
];

const SELF_SETTING_DEF_BY_KEY: Record<string, SelfSettingDefinition> = SELF_SETTING_DEFS.reduce((acc, def) => {
  acc[def.key] = def;
  return acc;
}, {} as Record<string, SelfSettingDefinition>);

const STASH_PLUGIN_CONFIG_QUERY = `query AIOverhaulPluginConfig($ids: [ID!]) {
  configuration {
    plugins(include: $ids)
  }
}`;

const STASH_PLUGIN_CONFIG_MUTATION = `mutation ConfigureAIOverhaulPlugin($plugin_id: ID!, $input: Map!) {
  configurePlugin(plugin_id: $plugin_id, input: $input)
}`;

function buildSelfSettingFields(config: Record<string, any>): any[] {
  const fields: any[] = [];
  for (const def of SELF_SETTING_DEFS) {
    let value = config?.[def.key];
    if (value === undefined || value === null) {
      value = def.default;
    } else if (def.type === 'boolean') {
      value = coerceBoolean(value, !!def.default);
    } else if (def.type === 'string') {
      value = typeof value === 'string' ? value : String(value);
    }
    fields.push({
      key: def.key,
      label: def.label,
      type: def.type,
      default: def.default,
      options: def.options,
      description: def.description,
      value,
    });
  }
  return fields;
}

function normalizeSelfSettingValue(def: SelfSettingDefinition, raw: any) {
  if (raw === null) return null;
  if (def.type === 'boolean') {
    return coerceBoolean(raw, !!def.default);
  }
  if (def.type === 'string') {
    if (typeof raw === 'string') return raw;
    if (raw === undefined) return '';
    return String(raw ?? '');
  }
  return raw;
}

const normalizeBaseValue = (raw: any): string => {
  if (typeof raw !== 'string') return '';
  const trimmed = raw.trim();
  return trimmed ? trimmed.replace(/\/$/, '') : '';
};

const coerceBoolean = (raw: any, defaultValue = false): boolean => {
  if (typeof raw === 'boolean') return raw;
  if (typeof raw === 'number') return raw !== 0;
  if (typeof raw === 'string') {
    const lowered = raw.trim().toLowerCase();
    if (!lowered) return defaultValue;
    if (['1','true','yes','on'].includes(lowered)) return true;
    if (['0','false','no','off'].includes(lowered)) return false;
  }
  return defaultValue;
};

function getSharedApiKeyValue(): string {
  try {
    const helper = (window as any).AISharedApiKeyHelper;
    if (helper && typeof helper.get === 'function') {
      const value = helper.get();
      if (typeof value === 'string') {
        return value.trim();
      }
    }
  } catch {}
  const raw = (window as any).AI_SHARED_API_KEY;
  return typeof raw === 'string' ? raw.trim() : '';
}

function applySharedKeyHeaders(opts?: any): any {
  const helper = (window as any).AISharedApiKeyHelper;
  if (helper && typeof helper.withHeaders === 'function') {
    return helper.withHeaders(opts || {});
  }
  const key = getSharedApiKeyValue();
  if (!key) return opts || {};
  const headers = { ...(opts && opts.headers ? opts.headers : {}) };
  headers['x-ai-api-key'] = key;
  return { ...(opts || {}), headers };
}

function detectFrontendVersion(): string | null {
  try {
    if (typeof window === 'undefined') {
      return null;
    }
    const w: any = window as any;
    const direct = w.AIOverhaulFrontendVersion;
    if (direct !== undefined && direct !== null) {
      const normalized = String(direct).trim();
      if (normalized) return normalized;
    }
    const pluginApi = w.PluginApi;
    if (pluginApi) {
      const manifest = pluginApi.manifest || pluginApi.pluginManifest || (pluginApi.plugin && pluginApi.plugin.manifest);
      if (manifest && typeof manifest.version === 'string') {
        const normalized = manifest.version.trim();
        if (normalized) return normalized;
      }
      if (pluginApi.plugin && typeof pluginApi.plugin.version === 'string') {
        const normalized = pluginApi.plugin.version.trim();
        if (normalized) return normalized;
      }
      if (pluginApi.plugins) {
        const named = pluginApi.plugins.AIOverhaul || pluginApi.plugins.aioverhaul;
        if (named) {
          if (named.manifest && typeof named.manifest.version === 'string') {
            const normalized = named.manifest.version.trim();
            if (normalized) return normalized;
          }
          if (typeof named.version === 'string') {
            const normalized = named.version.trim();
            if (normalized) return normalized;
          }
        }
      }
    }
    const manifest = w.AIOverhaulManifest;
    if (manifest && typeof manifest.version === 'string') {
      const normalized = manifest.version.trim();
      if (normalized) return normalized;
    }
  } catch (_) {}
  return null;
}

// Use shared backend base helper when available. The build outputs each file as
// an IIFE so we also support the global `window.AIDefaultBackendBase` for
// consumers that execute before modules are loaded.
const defaultBackendBase = () => {
  const fn = (window as any).AIDefaultBackendBase;
  if (typeof fn !== 'function') throw new Error('AIDefaultBackendBase not initialized. Ensure backendBase is loaded first.');
  return fn();
};

function extractBackendBaseFromUrl(url: string): string {
  try {
    if (!url) return '';
    const base = new URL(url, (typeof location !== 'undefined' && location.origin) ? location.origin : 'http://localhost');
    return base.origin.replace(/\/$/, '');
  } catch (_) {
    return '';
  }
}

// Small fetch wrapper adding JSON handling + error capture
async function jfetch(url: string, opts: any = {}): Promise<any> {
  const health: any = (window as any).AIBackendHealth;
  const baseHint = extractBackendBaseFromUrl(url);
  let reportedError = false;
  let body: any = null;
  try {
    if (health && typeof health.reportChecking === 'function') {
      try { health.reportChecking(baseHint); } catch (_) {}
    }
    const mergedHeaders = { 'content-type': 'application/json', ...(opts.headers || {}) };
    const baseOpts = { ...opts, headers: mergedHeaders, credentials: opts.credentials ?? 'same-origin' };
    const fetchOpts = applySharedKeyHeaders(baseOpts);
    if ((window as any).AIDebug) console.debug('[jfetch] url=', url, 'opts=', fetchOpts);
    const res = await fetch(url, fetchOpts);
    const ct = (res.headers.get('content-type')||'').toLowerCase();
    if (ct.includes('application/json')) { try { body = await res.json(); } catch { body = null; } }
    if (!res.ok) {
      const detailPayload = (body && Object.prototype.hasOwnProperty.call(body, 'detail')) ? body.detail : body;
      let detailText: string | undefined;
      if (typeof detailPayload === 'string') {
        detailText = detailPayload;
      } else if (detailPayload && typeof detailPayload === 'object') {
        detailText = detailPayload.message || detailPayload.code;
      }
      if (health) {
        if ((res.status >= 500 || res.status === 0) && typeof health.reportError === 'function') {
          try { health.reportError(baseHint, detailText || ('HTTP '+res.status)); reportedError = true; } catch (_) {}
        } else if (typeof health.reportOk === 'function') {
          try { health.reportOk(baseHint); } catch (_) {}
        }
      }
      const error: any = new Error(detailText || res.statusText || ('HTTP '+res.status));
      error.status = res.status;
      error.detail = detailPayload;
      error.body = body;
      error.response = body;
      throw error;
    }
    if (health && typeof health.reportOk === 'function') {
      try { health.reportOk(baseHint); } catch (_) {}
    }
    return body;
  } catch (err: any) {
    if (!reportedError && health && typeof health.reportError === 'function') {
      try { health.reportError(baseHint, err && err.message ? err.message : undefined, err); } catch (_) {}
    }
    throw err;
  }
}

function formatPluginActionError(error: any, pluginLabel?: string, actionLabel?: string): string | null {
  const detail = error?.detail;
  const pluginName = detail?.plugin || pluginLabel || 'This plugin';
  const verb = actionLabel || 'complete this action';
  if (!detail) {
    return null;
  }
  if (typeof detail === 'string') {
    return detail;
  }
  if (typeof detail !== 'object') {
    return null;
  }
  const code = (detail.code || detail.error || detail.status || '').toString();
  const requiredBackend = detail.required_backend || detail.requiredBackend;
  const backendVersion = detail.backend_version || detail.backendVersion || detail.detected_backend;

  if (code === 'BACKEND_TOO_OLD' && requiredBackend) {
    const detected = backendVersion || 'your current backend version';
    return `${pluginName} requires backend ${requiredBackend}, but the server is ${detected}. Upgrade the backend before trying to ${verb}.`;
  }
  if (code === 'PLUGIN_INACTIVE' && detail.message) {
    return detail.message;
  }
  if (detail.message) {
    return detail.message;
  }
  return null;
}

const PluginSettings = () => {
  if ((window as any).AIDebug) console.debug('[PluginSettings] component render start');
  const React: any = (window as any).PluginApi?.React || (window as any).React;
  if (!React) { console.error('[PluginSettings] React not found'); return null; }

  // Core state
  const [backendBase, setBackendBase] = React.useState(() => defaultBackendBase());
  const [backendDraft, setBackendDraft] = React.useState(() => defaultBackendBase());
  // Using 'any' in generics because React reference might be untyped (window injection)
  const [installed, setInstalled] = React.useState([] as any as InstalledPlugin[]);
  const [sources, setSources] = React.useState([] as any as Source[]);
  const [catalog, setCatalog] = React.useState({} as Record<string, CatalogEntry[]>);
  const [pluginSettings, setPluginSettings] = React.useState({} as Record<string, any[]>);
  const [systemSettings, setSystemSettings] = React.useState([] as any[]);
  const [systemLoading, setSystemLoading] = React.useState(false);
  const [systemHealth, setSystemHealth] = React.useState(null as any);
  const [systemHealthError, setSystemHealthError] = React.useState(null as string | null);
  const [systemHealthLoading, setSystemHealthLoading] = React.useState(false);
  const [backendVersion, setBackendVersion] = React.useState(null as string | null);
  const [backendVersionStatus, setBackendVersionStatus] = React.useState('idle');
  const [backendVersionError, setBackendVersionError] = React.useState(null as string | null);
  const [backendVersionInfo, setBackendVersionInfo] = React.useState(null as any);
  const [pluginActionNotice, setPluginActionNotice] = React.useState(null as { level: 'error' | 'info' | 'success'; message: string } | null);
  const [frontendVersion, setFrontendVersion] = React.useState(() => detectFrontendVersion()) as [string | null, (next: string | null) => void];
  const [openConfig, setOpenConfig] = React.useState(null as string | null);
  const [selectedSource, setSelectedSource] = React.useState(null as string | null);
  const [loading, setLoading] = React.useState({installed:false, sources:false, catalog:false} as {installed:boolean; sources:boolean; catalog:boolean; action?:string});
  const [error, setError] = React.useState(null as string | null);
  const [addSrcName, setAddSrcName] = React.useState('');
  const [addSrcUrl, setAddSrcUrl] = React.useState('');
  const [interactionsEnabled, setInteractionsEnabled] = React.useState(() => {
    const globalFlag = (window as any).__AI_INTERACTIONS_ENABLED__;
    return typeof globalFlag === 'boolean' ? globalFlag : true;
  });
  const [selfSettingsInitialized, setSelfSettingsInitialized] = React.useState(false);
  const [selfMigrationAttempted, setSelfMigrationAttempted] = React.useState(false);
  const [backendSaving, setBackendSaving] = React.useState(false);
  const [interactionsSaving, setInteractionsSaving] = React.useState(false);
  const [sharedKeyDraft, setSharedKeyDraft] = React.useState('');
  const [sharedKeySaving, setSharedKeySaving] = React.useState(false);
  const [sharedKeyReveal, setSharedKeyReveal] = React.useState(false);
  const selfConfigRef = React.useRef({} as any);

  const showPluginActionMessage = React.useCallback((message: string, level: 'error' | 'info' | 'success' = 'error') => {
    if (!message) return;
    setPluginActionNotice({ level, message });
    if (level === 'error') {
      setError(message);
    }
  }, [setError]);

  const backendHealthApi: any = (window as any).AIBackendHealth;
  const backendHealthEvent = backendHealthApi?.EVENT_NAME || 'AIBackendHealthChange';
  const [backendHealthTick, setBackendHealthTick] = React.useState(0);
  React.useEffect(() => {
    if (!backendHealthApi || !backendHealthEvent) return;
    const handler = () => setBackendHealthTick((t: number) => t + 1);
    try { window.addEventListener(backendHealthEvent, handler as any); } catch (_) {}
    return () => { try { window.removeEventListener(backendHealthEvent, handler as any); } catch (_) {}; };
  }, [backendHealthApi, backendHealthEvent]);
  const backendBaseRef = React.useRef(backendBase);
  React.useEffect(() => { backendBaseRef.current = backendBase; }, [backendBase]);
  const interactionsRef = React.useRef(interactionsEnabled);
  React.useEffect(() => { interactionsRef.current = interactionsEnabled; }, [interactionsEnabled]);
  const sharedKeyRef = React.useRef('');
  const frontendVersionRef = React.useRef(frontendVersion);
  const backendHealthState = React.useMemo(() => {
    if (backendHealthApi && typeof backendHealthApi.getState === 'function') {
      return backendHealthApi.getState();
    }
    return null;
  }, [backendHealthApi, backendHealthTick]);
  React.useEffect(() => { frontendVersionRef.current = frontendVersion; }, [frontendVersion]);
  React.useEffect(() => {
    if (frontendVersionRef.current) return;
    let cancelled = false;
    let timer: number | null = null;
    const attempt = () => {
      if (cancelled) return;
      const resolved = detectFrontendVersion();
      if (resolved) {
        setFrontendVersion(resolved);
        try { (window as any).AIOverhaulFrontendVersion = resolved; } catch (_) {}
        return;
      }
      timer = window.setTimeout(attempt, 500);
    };
    attempt();
    return () => {
      cancelled = true;
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    };
  }, []);

  // Derived: update availability map plugin->latestVersionAcrossCatalogs
  const latestVersions = React.useMemo(() => {
    const map: Record<string,string> = {};
    for (const entries of Object.values(catalog) as any) {
      for (const c of entries as any[]) {
        const cur = map[c.plugin_name];
        if (!cur || isVersionNewer(c.version, cur)) map[c.plugin_name] = c.version;
      }
    }
    return map;
  }, [catalog]);

  function isVersionNewer(a: string, b: string) {
    return compareVersionsLoose(a, b) > 0;
  }

  // Loaders
  const loadInstalled = React.useCallback(async () => {
    setLoading((l:any) => ({...l, installed:true}));
  try { const data = await jfetch(`${backendBase}/api/v1/plugins/installed?include_removed=false`); setInstalled(Array.isArray(data)?data:data||[]); } catch(e:any){ setError(e.message); }
    finally { setLoading((l:any) => ({...l, installed:false})); }
  }, [backendBase]);

  const loadSources = React.useCallback(async () => {
    setLoading((l:any) => ({...l, sources:true}));
    try { const data = await jfetch(`${backendBase}/api/v1/plugins/sources`); setSources(Array.isArray(data)?data:data||[]); } catch(e:any){ setError(e.message); }
    finally { setLoading((l:any) => ({...l, sources:false})); }
  }, [backendBase]);

  const loadCatalogFor = React.useCallback(async (name: string) => {
    setLoading((l:any) => ({...l, catalog:true}));
    try { const data = await jfetch(`${backendBase}/api/v1/plugins/catalog/${name}`); setCatalog((c:any) => ({...c, [name]: Array.isArray(data)?data:[]})); } catch(e:any){ setError(e.message); }
    finally { setLoading((l:any) => ({...l, catalog:false})); }
  }, [backendBase]);

  const loadSystemHealth = React.useCallback(async () => {
    setSystemHealthLoading(true);
    setSystemHealthError(null);
    setBackendVersionStatus('loading');
    setBackendVersionError(null);

    const interpretVersionPayload = (snapshot: any) => {
      if (!snapshot || typeof snapshot !== 'object') return null;
      const candidate = snapshot.version_payload || snapshot.versionPayload;
      const info = candidate && typeof candidate === 'object' ? { ...candidate } : {};
      const fallbackVersionRaw = typeof snapshot.backend_version === 'string' ? snapshot.backend_version : (typeof snapshot.version === 'string' ? snapshot.version : undefined);
      const fallbackVersion = fallbackVersionRaw ? fallbackVersionRaw.trim() : undefined;
      const fallbackHeadRaw = typeof snapshot.db_alembic_head === 'string' ? snapshot.db_alembic_head : undefined;
      const fallbackHead = fallbackHeadRaw ? fallbackHeadRaw.trim() : undefined;
      if (fallbackVersion && !info.version) info.version = fallbackVersion;
      if (fallbackHead && !info.db_alembic_head) info.db_alembic_head = fallbackHead;
      return Object.keys(info).length ? info : null;
    };

    try {
      const data = await jfetch(`${backendBase}/api/v1/system/health`);
      setSystemHealth(data);
      const payload = interpretVersionPayload(data);
      const normalizedVersion = payload && typeof payload.version === 'string' ? payload.version.trim() : '';
      if (payload) {
        setBackendVersionInfo(payload);
      } else {
        const fallbackHead = typeof data?.db_alembic_head === 'string' ? data.db_alembic_head.trim() : null;
        setBackendVersionInfo({ version: normalizedVersion || null, db_alembic_head: fallbackHead });
      }
      if (normalizedVersion) {
        setBackendVersion(normalizedVersion);
        setBackendVersionStatus('ok');
      } else {
        setBackendVersion(null);
        setBackendVersionStatus('error');
        setBackendVersionError('Backend did not report a version');
      }
    } catch (e: any) {
      const message = e?.message || 'Failed to load system health';
      setSystemHealth(null);
      setSystemHealthError(message);
      setBackendVersion(null);
      setBackendVersionInfo(null);
      setBackendVersionStatus('error');
      setBackendVersionError(message);
    } finally {
      setSystemHealthLoading(false);
    }
  }, [backendBase]);

  const loadSystemSettings = React.useCallback(async () => {
    setSystemLoading(true);
    try {
      const data = await jfetch(`${backendBase}/api/v1/plugins/system/settings`);
      setSystemSettings(Array.isArray(data) ? data : []);
    } catch (e: any) {
      setError(e.message);
      setSystemSettings([]);
    } finally {
      setSystemLoading(false);
    }
  }, [backendBase]);

  const refreshSource = React.useCallback(async (name: string) => {
    try { await jfetch(`${backendBase}/api/v1/plugins/sources/${name}/refresh`, { method:'POST' }); await loadCatalogFor(name); await loadInstalled(); await loadSources(); } catch(e:any){ setError(e.message); }
  }, [backendBase, loadCatalogFor, loadInstalled, loadSources]);

  const addSource = React.useCallback(async () => {
    if (!addSrcName || !addSrcUrl) return;
    try { await jfetch(`${backendBase}/api/v1/plugins/sources`, { method:'POST', body: JSON.stringify({ name: addSrcName, url: addSrcUrl, enabled: true }) }); setAddSrcName(''); setAddSrcUrl(''); await loadSources(); } catch(e:any){ setError(e.message); }
  }, [backendBase, addSrcName, addSrcUrl, loadSources]);

  const removeSource = React.useCallback(async (name: string) => {
    if (!confirm(`Remove source ${name}?`)) return;
    try { await jfetch(`${backendBase}/api/v1/plugins/sources/${name}`, { method:'DELETE' }); setCatalog((c:any) => { const n = {...c}; delete n[name]; return n; }); if (selectedSource === name) setSelectedSource(null); await loadSources(); } catch(e:any){ setError(e.message); }
  }, [backendBase, selectedSource, loadSources]);

  const installPlugin = React.useCallback(async (source: string, plugin: string, overwrite=false, installDependencies=false, displayName?: string) => {
    try {
      await jfetch(`${backendBase}/api/v1/plugins/install`, { method:'POST', body: JSON.stringify({ source, plugin, overwrite, install_dependencies: installDependencies }) });
      await loadInstalled();
      showPluginActionMessage(`${displayName || plugin} installed successfully.`, 'success');
    } catch(e:any){
      const friendly = formatPluginActionError(e, displayName || plugin, 'install');
      showPluginActionMessage(friendly || e.message || 'Failed to install plugin');
    }
  }, [backendBase, loadInstalled, showPluginActionMessage]);

  const startInstall = React.useCallback(async (source: string, plugin: string, overwrite=false, displayName?: string) => {
    try {
      const plan = await jfetch(`${backendBase}/api/v1/plugins/install/plan`, { method:'POST', body: JSON.stringify({ source, plugin }) });
      const missing = (plan?.missing || []) as string[];
      if (missing.length) {
        alert(`Cannot install ${plan?.human_names?.[plugin] || plugin}. Missing dependencies: ${missing.join(', ')}`);
        return;
      }
      const deps = (plan?.dependencies || []) as string[];
      const already = new Set(plan?.already_installed || []);
      const needed = deps.filter(d => !already.has(d));
      let installDeps = false;
      if (needed.length) {
        const friendly = needed.map(name => plan?.human_names?.[name] || name).join(', ');
        if (!confirm(`Installing ${plan?.human_names?.[plugin] || plugin} will also install: ${friendly}. Continue?`)) return;
        installDeps = true;
      }
      const pluginLabel = plan?.human_names?.[plugin] || displayName || plugin;
      await installPlugin(source, plugin, overwrite, installDeps, pluginLabel);
    } catch(e:any){
      const friendly = formatPluginActionError(e, displayName || plugin, 'install');
      showPluginActionMessage(friendly || e.message || 'Failed to install plugin');
    }
  }, [backendBase, installPlugin, showPluginActionMessage]);

  const updatePlugin = React.useCallback(async (source: string, plugin: string, displayName?: string) => {
    try {
      await jfetch(`${backendBase}/api/v1/plugins/update`, { method:'POST', body: JSON.stringify({ source, plugin }) });
      await loadInstalled();
      showPluginActionMessage(`${displayName || plugin} updated.`, 'success');
    } catch(e:any){
      const friendly = formatPluginActionError(e, displayName || plugin, 'update');
      showPluginActionMessage(friendly || e.message || 'Failed to update plugin');
    }
  }, [backendBase, loadInstalled, showPluginActionMessage]);

  const reloadPlugin = React.useCallback(async (plugin: string) => {
    try {
      await jfetch(`${backendBase}/api/v1/plugins/reload`, { method: 'POST', body: JSON.stringify({ plugin }) });
      await loadInstalled();
    } catch (e: any) {
      setError(e.message);
    }
  }, [backendBase, loadInstalled]);

  const removePlugin = React.useCallback(async (plugin: string) => {
    try {
      const plan = await jfetch(`${backendBase}/api/v1/plugins/remove/plan`, { method:'POST', body: JSON.stringify({ plugin }) });
      const human = (plan?.human_names?.[plugin] || installed.find((p: InstalledPlugin) => p.name === plugin)?.human_name || plugin) as string;
      const dependents = ((plan?.dependents || []) as string[]).filter(name => name !== plugin);
      let cascade = false;
      if (dependents.length) {
        const friendly = dependents.map(name => plan?.human_names?.[name] || installed.find((p: InstalledPlugin) => p.name === name)?.human_name || name).join(', ');
        if (!confirm(`Removing ${human} will also remove: ${friendly}. Continue?`)) return;
        cascade = true;
      } else {
        if (!confirm(`Remove plugin ${human}?`)) return;
      }
      await jfetch(`${backendBase}/api/v1/plugins/remove`, { method:'POST', body: JSON.stringify({ plugin, cascade }) });
      await loadInstalled();
    } catch(e:any){ setError(e.message); }
  }, [backendBase, loadInstalled, installed]);

  const saveSelfPluginSetting = React.useCallback(async (key: string, rawValue: any) => {
    const def = SELF_SETTING_DEF_BY_KEY[key];
    if (!def) return false;
    const prevConfig = selfConfigRef.current ? { ...(selfConfigRef.current as any) } : {};
    const nextConfig = { ...prevConfig };
    const normalized = normalizeSelfSettingValue(def, rawValue);
    if (normalized === null) {
      delete nextConfig[key];
    } else {
      nextConfig[key] = normalized;
    }
    if ((window as any).AIDebug) console.debug('[PluginSettings] saving via GraphQL', key, normalized, nextConfig);
    setPluginSettings((p:any) => {
      const current = p[THIS_PLUGIN_NAME] || buildSelfSettingFields(prevConfig);
      const updated = current.map((field:any) => field.key === key ? ({ ...field, value: normalized === null ? def.default : normalized }) : field);
      return { ...p, [THIS_PLUGIN_NAME]: updated };
    });
    try {
      const resp = await fetch('/graphql', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ query: STASH_PLUGIN_CONFIG_MUTATION, variables: { plugin_id: THIS_PLUGIN_NAME, input: nextConfig } }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const payload = await resp.json().catch(() => null);
      const updatedConfig = payload?.data?.configurePlugin;
      const finalConfig = updatedConfig && typeof updatedConfig === 'object' ? updatedConfig : nextConfig;
      selfConfigRef.current = { ...(finalConfig as any) };
      setPluginSettings((p:any) => ({ ...p, [THIS_PLUGIN_NAME]: buildSelfSettingFields(selfConfigRef.current) }));
      return true;
    } catch (e: any) {
      setError(e?.message || 'Failed to update AI Overhaul plugin settings');
      selfConfigRef.current = prevConfig;
      setPluginSettings((p:any) => ({ ...p, [THIS_PLUGIN_NAME]: buildSelfSettingFields(prevConfig) }));
      return false;
    }
  }, [setPluginSettings, setError]);

  const ensureSelfSettingDefaults = React.useCallback(async (config: Record<string, any> | null) => {
    const working = (config && typeof config === 'object') ? { ...config } : {};
    const pending: Array<{ key: string; value: any }> = [];

    for (const def of SELF_SETTING_DEFS) {
      const raw = working[def.key];
      const needsDefault =
        raw === undefined ||
        raw === null ||
        (def.key === 'backend_base_url' && normalizeBaseValue(raw) === '');
      if (!needsDefault) continue;

      const baseDefault = def.type === 'boolean' ? !!def.default : def.default;
      if (def.key === 'backend_base_url') {
        const normalized = normalizeBaseValue(baseDefault) || DEFAULT_BACKEND_BASE_URL;
        working[def.key] = normalized;
        pending.push({ key: def.key, value: normalized });
      } else {
        working[def.key] = baseDefault;
        pending.push({ key: def.key, value: baseDefault });
      }
    }

    if (!pending.length) {
      return working;
    }

    let latest = working;
    for (const entry of pending) {
      const ok = await saveSelfPluginSetting(entry.key, entry.value);
      if (!ok) {
        continue;
      }
      latest = { ...(selfConfigRef.current || latest) };
    }

    return latest;
  }, [saveSelfPluginSetting]);

  const loadSelfPluginSettings = React.useCallback(async () => {
    try {
      if ((window as any).AIDebug) console.debug('[PluginSettings] loading AIOverhaul settings via GraphQL');
      const resp = await fetch('/graphql', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ query: STASH_PLUGIN_CONFIG_QUERY, variables: { ids: [THIS_PLUGIN_NAME] } }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const payload = await resp.json().catch(() => null);
      const plugins = payload?.data?.configuration?.plugins;
      const rawEntry = plugins && typeof plugins === 'object' ? plugins[THIS_PLUGIN_NAME] : null;
      const config = rawEntry && typeof rawEntry === 'object' ? rawEntry : {};
      selfConfigRef.current = { ...(config as any) };
      const ensured = await ensureSelfSettingDefaults(selfConfigRef.current);
      const finalConfig = ensured && typeof ensured === 'object' ? ensured : selfConfigRef.current;
      selfConfigRef.current = { ...(finalConfig as any) };
      setPluginSettings((p:any) => ({ ...p, [THIS_PLUGIN_NAME]: buildSelfSettingFields(selfConfigRef.current) }));
      return selfConfigRef.current;
    } catch (e: any) {
      setError(e?.message || 'Failed to load AI Overhaul plugin settings');
      const fallback = selfConfigRef.current || {};
      setPluginSettings((p:any) => ({ ...p, [THIS_PLUGIN_NAME]: buildSelfSettingFields(fallback) }));
      return null;
    }
  }, [ensureSelfSettingDefaults, setPluginSettings, setError]);

  const loadPluginSettings = React.useCallback(async (pluginName: string) => {
    if (pluginName === THIS_PLUGIN_NAME) {
      await loadSelfPluginSettings();
      return;
    }
    try {
      const data = await jfetch(`${backendBase}/api/v1/plugins/settings/${pluginName}`);
      setPluginSettings((p:any) => ({...p, [pluginName]: Array.isArray(data)?data:[]}));
    } catch(e:any) { setError(e.message); }
  }, [backendBase, loadSelfPluginSettings]);

  const savePluginSetting = React.useCallback(async (pluginName: string, key: string, value: any, baseOverride?: string) => {
    if (pluginName === THIS_PLUGIN_NAME) {
      return saveSelfPluginSetting(key, value);
    }
    let previousValue: any;
    let capturedPrev = false;
    setPluginSettings((p:any) => {
      const list = p[pluginName] || [];
      const cur = list.map((f:any) => {
        if (f.key !== key) return f;
        if (!capturedPrev) {
          previousValue = f.value === undefined ? f.default : f.value;
          capturedPrev = true;
        }
        return {...f, value};
      });
      return {...p, [pluginName]: cur};
    });
    try {
      const base = baseOverride !== undefined ? baseOverride : (backendBaseRef.current ?? backendBase);
      const targetBase = typeof base === 'string' ? base : backendBase;
  if ((window as any).AIDebug) console.debug('[savePluginSetting] saving', pluginName, key, value, 'to', targetBase || '(relative)');
  await jfetch(`${targetBase}/api/v1/plugins/settings/${pluginName}/${encodeURIComponent(key)}`, { method:'PUT', body: JSON.stringify({ value }) });
  try { await loadPluginSettings(pluginName); } catch (_) {}
  return true;
    } catch(e:any) {
      setError(e.message);
      if (capturedPrev) {
        setPluginSettings((p:any) => {
          const cur = (p[pluginName] || []).map((f:any) => f.key === key ? ({...f, value: previousValue}) : f);
          return {...p, [pluginName]: cur};
        });
      }
      return false;
    }
  }, [backendBase, saveSelfPluginSetting]);
  const updateInteractions = React.useCallback(async (next: boolean) => {
    // Always attempt to persist boolean value; avoid null/undefined to keep server-value explicit
    const normalized = !!next;
    if (normalized === interactionsRef.current && selfSettingsInitialized) return;
    const prev = interactionsRef.current;
    setInteractionsSaving(true);
    setInteractionsEnabled(normalized);
    try {
      const ok = await savePluginSetting(THIS_PLUGIN_NAME, 'capture_events', normalized);
      if (!ok) {
        setInteractionsEnabled(prev);
        return;
      }
      try {
        const helper = (window as any).AIDefaultBackendBase;
        if (helper && typeof helper.applyPluginConfig === 'function') helper.applyPluginConfig(undefined, normalized, undefined);
        else (window as any).__AI_INTERACTIONS_ENABLED__ = normalized;
      } catch {}
      try { await loadPluginSettings(THIS_PLUGIN_NAME); } catch {}
    } catch {
      setInteractionsEnabled(prev);
    } finally {
      setInteractionsSaving(false);
    }
  }, [loadPluginSettings, savePluginSetting, selfSettingsInitialized]);

  const retryBackendProbe = React.useCallback(() => {
    loadInstalled();
    loadSources();
    if (selectedSource) {
      loadCatalogFor(selectedSource);
    } else if (sources && sources.length && sources[0]?.name) {
      loadCatalogFor(sources[0].name);
    }
    void loadSystemHealth();
  }, [loadInstalled, loadSources, loadCatalogFor, loadSystemHealth, selectedSource, sources]);

  // Initial loads
  React.useEffect(() => { loadPluginSettings(THIS_PLUGIN_NAME); }, [loadPluginSettings]);
  React.useEffect(() => { loadInstalled(); loadSources(); }, [loadInstalled, loadSources]);
  React.useEffect(() => { void loadSystemSettings(); }, [loadSystemSettings]);
  React.useEffect(() => { void loadSystemHealth(); }, [loadSystemHealth]);
  // After sources load first time, auto refresh each source once to populate catalog
  const autoRefreshed = React.useRef(false);
  React.useEffect(() => {
    const rows = pluginSettings[THIS_PLUGIN_NAME];
    if (!rows) return;
    const lookup = (key: string) => {
      const field = rows.find((f: any) => f.key === key);
      if (!field) return undefined;
      return field.value !== undefined && field.value !== null ? field.value : field.default;
    };
    const remoteBase = normalizeBaseValue(lookup('backend_base_url'));
    const remoteInteractions = coerceBoolean(lookup('capture_events'), true);
    const remoteSharedRaw = lookup('shared_api_key');
    const remoteSharedKey = typeof remoteSharedRaw === 'string' ? remoteSharedRaw.trim() : '';

    try {
      const helper = (window as any).AIDefaultBackendBase;
      if (helper && typeof helper.applyPluginConfig === 'function') helper.applyPluginConfig(remoteBase, remoteInteractions, remoteSharedKey);
      else {
        (window as any).AI_BACKEND_URL = remoteBase;
        (window as any).__AI_INTERACTIONS_ENABLED__ = remoteInteractions;
        (window as any).AI_SHARED_API_KEY = remoteSharedKey;
      }
    } catch {}

    const editingDraft = normalizeBaseValue(backendDraft) !== backendBaseRef.current;
    const prevShared = sharedKeyRef.current;
    const editingSharedKey = sharedKeyDraft !== prevShared;
    sharedKeyRef.current = remoteSharedKey;
    if (!selfSettingsInitialized) {
      if (remoteBase !== backendBaseRef.current) {
        setBackendBase(remoteBase);
        setBackendDraft(remoteBase);
      }
      if (remoteInteractions !== interactionsRef.current) {
        setInteractionsEnabled(remoteInteractions);
      }
      setSharedKeyDraft(remoteSharedKey);
      setSelfSettingsInitialized(true);
    } else {
      if (!editingDraft) {
        if (remoteBase !== backendBaseRef.current) {
          setBackendBase(remoteBase);
          setBackendDraft(remoteBase);
        }
      } else if (remoteBase !== backendBaseRef.current) {
        setBackendBase(remoteBase);
      }
      if (remoteInteractions !== interactionsRef.current) {
        setInteractionsEnabled(remoteInteractions);
      }
      if (!editingSharedKey) {
        if (remoteSharedKey !== sharedKeyDraft) {
          setSharedKeyDraft(remoteSharedKey);
        }
      }
    }

    if (!selfMigrationAttempted) {
      setSelfMigrationAttempted(true);
      (async () => {
        let updated = false;
        try {
          let legacyBase = '';
          try { legacyBase = normalizeBaseValue(localStorage.getItem(LEGACY_BACKEND_URL)); } catch {}
          if (!remoteBase && legacyBase) {
            const ok = await savePluginSetting(THIS_PLUGIN_NAME, 'backend_base_url', legacyBase);
            if (ok) {
              updated = true;
              setBackendBase(legacyBase);
              setBackendDraft(legacyBase);
            }
          }
          let legacyInteractionsRaw: string | null = null;
          try { legacyInteractionsRaw = localStorage.getItem(LEGACY_INTERACTIONS); } catch {}
          const legacyInteractions = coerceBoolean(legacyInteractionsRaw, false);
          if (!remoteInteractions && legacyInteractions) {
            const ok = await savePluginSetting(THIS_PLUGIN_NAME, 'capture_events', true);
            if (ok) {
              updated = true;
              setInteractionsEnabled(true);
            }
          }
        } catch {}
        finally {
          try {
            localStorage.removeItem(LEGACY_BACKEND_URL);
            localStorage.removeItem(LEGACY_INTERACTIONS);
          } catch {}
          if (updated) {
            try { await loadPluginSettings(THIS_PLUGIN_NAME); } catch {}
          }
        }
      })();
    }
  }, [backendDraft, pluginSettings, savePluginSetting, loadPluginSettings, selfMigrationAttempted, selfSettingsInitialized, sharedKeyDraft]);
  React.useEffect(() => {
    (async () => {
      if (autoRefreshed.current) return; // only once
      if (!sources.length) return;
      // If any catalog already populated, skip bulk auto-refresh
      const haveAny = Object.values(catalog).some(arr => Array.isArray(arr) && arr.length);
      if (haveAny) { autoRefreshed.current = true; return; }
      autoRefreshed.current = true;
      for (const s of sources) {
        try { await refreshSource(s.name); } catch(e:any){ /* ignore individual errors */ }
      }
    })();
  }, [sources, catalog, refreshSource]);
  // System settings initial load
  React.useEffect(() => { loadSystemSettings(); }, [loadSystemSettings]);

  // If selected source changes and we don't have catalog, load it
  React.useEffect(() => { if (selectedSource && !catalog[selectedSource]) loadCatalogFor(selectedSource); }, [selectedSource, catalog, loadCatalogFor]);

  // Auto-select official source or first available when sources arrive
  React.useEffect(() => {
    if (!selectedSource && sources.length) {
  const official = sources.find((s: any) => s.name === 'official');
      setSelectedSource(official ? official.name : sources[0].name);
    }
  }, [sources, selectedSource]);

  // Interaction toggle persistence
  React.useEffect(() => {
    try { (window as any).__AI_INTERACTIONS_ENABLED__ = !!interactionsEnabled; } catch {}
    // Propagate to tracker runtime if already loaded
    try {
      const tracker = (window as any).stashAIInteractionTracker;
      if (tracker) {
        if (typeof tracker.setEnabled === 'function') tracker.setEnabled(!!interactionsEnabled);
        else if (typeof tracker.configure === 'function') tracker.configure({ enabled: !!interactionsEnabled });
      }
    } catch {}
  }, [interactionsEnabled]);

  const saveBackendBase = React.useCallback(async () => {
    const clean = normalizeBaseValue(backendDraft);
    const prev = backendBaseRef.current;
    const target = clean || DEFAULT_BACKEND_BASE_URL;
    if (target === prev && selfSettingsInitialized) return;
    setBackendSaving(true);
    setBackendBase(target);
    try {
      const ok = await savePluginSetting(THIS_PLUGIN_NAME, 'backend_base_url', target, prev);
      if (!ok) {
        setBackendBase(prev);
        setBackendDraft(prev);
        return;
      }
      setBackendDraft(target);
      try {
        const helper = (window as any).AIDefaultBackendBase;
        if (helper && typeof helper.applyPluginConfig === 'function') helper.applyPluginConfig(target || '', undefined, undefined);
        else (window as any).AI_BACKEND_URL = target;
      } catch {}
      setInstalled([]); setSources([]); setCatalog({}); setSelectedSource(null);
      setSystemSettings([]); setSystemLoading(true);
      await loadInstalled();
      await loadSources();
      try { await loadPluginSettings(THIS_PLUGIN_NAME); } catch {}
    } catch {
      setBackendBase(prev);
      setBackendDraft(prev);
    } finally {
      setBackendSaving(false);
    }
  }, [backendDraft, loadInstalled, loadSources, savePluginSetting, loadPluginSettings, selfSettingsInitialized]);

  const persistSharedApiKey = React.useCallback(async (rawValue: string) => {
    const clean = (rawValue || '').trim();
    const prev = sharedKeyRef.current || '';
    if (clean === prev && selfSettingsInitialized) return;
    setSharedKeySaving(true);
    try {
      const ok = await savePluginSetting(THIS_PLUGIN_NAME, 'shared_api_key', clean);
      if (!ok) {
        setSharedKeyDraft(prev);
        return;
      }
      sharedKeyRef.current = clean;
      setSharedKeyDraft(clean);
      try {
        const helper = (window as any).AIDefaultBackendBase;
        if (helper && typeof helper.applyPluginConfig === 'function') helper.applyPluginConfig(undefined, undefined, clean);
        else (window as any).AI_SHARED_API_KEY = clean;
      } catch {}
      try { await loadPluginSettings(THIS_PLUGIN_NAME); } catch {}
    } finally {
      setSharedKeySaving(false);
    }
  }, [loadPluginSettings, savePluginSetting, selfSettingsInitialized]);

  const saveSharedApiKey = React.useCallback(async () => {
    await persistSharedApiKey(sharedKeyDraft);
  }, [persistSharedApiKey, sharedKeyDraft]);

  const clearSharedApiKey = React.useCallback(async () => {
    setSharedKeyDraft('');
    await persistSharedApiKey('');
  }, [persistSharedApiKey]);

  // UI helpers
  const sectionStyle: React.CSSProperties = { border: '1px solid #444', padding: '12px 14px', borderRadius: 6, marginBottom: 16, background: '#1e1e1e' };
  const headingStyle: React.CSSProperties = { margin: '0 0 8px', fontSize: 16 };
  const smallBtn: React.CSSProperties = { fontSize: 12, padding: '2px 6px', cursor: 'pointer' };
  const tableStyle: React.CSSProperties = { width: '100%', borderCollapse: 'collapse' };
  const thtd: React.CSSProperties = { border: '1px solid #333', padding: '4px 6px', fontSize: 12, verticalAlign: 'top' };

  const normalizeSlashMode = (mode: any): string => {
    if (typeof mode !== 'string') return 'auto';
    const trimmed = mode.trim().toLowerCase();
    if (trimmed === 'windows') return 'win';
    if (trimmed === 'keep') return 'unchanged';
    return PATH_SLASH_MODE_SET.has(trimmed) ? trimmed : 'auto';
  };

  const normalizePathMappingList = (input: any): any[] => {
    if (!input) return [];
    if (Array.isArray(input)) {
      const rows: any[] = [];
      for (const raw of input) {
        if (raw == null) continue;
        let source = '';
        let target = '';
        let mode: any = undefined;
        if (typeof raw === 'object' && !Array.isArray(raw)) {
          source = typeof raw.source === 'string' ? raw.source : '';
          target = typeof raw.target === 'string' ? raw.target : '';
          mode = raw.slash_mode;
        } else if (Array.isArray(raw)) {
          source = typeof raw[0] === 'string' ? raw[0] : '';
          target = typeof raw[1] === 'string' ? raw[1] : '';
          mode = raw[2];
        }
        source = source.trim();
        target = target.trim();
        if (!source) continue;
        rows.push({ source, target, slash_mode: normalizeSlashMode(mode) });
      }
      return rows;
    }
    if (typeof input === 'string') {
      const text = input.trim();
      if (!text) return [];
      try {
        return normalizePathMappingList(JSON.parse(text));
      } catch {
        const rows = text
          .split(/\r?\n/)
          .map(line => line.trim())
          .filter(Boolean)
          .map(line => {
            const parts = line.split('|').map(part => part.trim());
            const source = parts[0] || '';
            if (!source) return null;
            const target = parts[1] || '';
            const mode = parts[2];
            return { source, target, slash_mode: normalizeSlashMode(mode) };
          })
          .filter(Boolean) as any[];
        return rows;
      }
    }
    if (typeof input === 'object') {
      return Object.entries(input)
        .map(([key, value]) => {
          const source = typeof key === 'string' ? key.trim() : String(key);
          if (!source) return null;
          const target =
            typeof value === 'string' ? value.trim() : value == null ? '' : String(value);
          return { source, target, slash_mode: 'auto' };
        })
        .filter(Boolean) as any[];
    }
    return [];
  };

  const ensurePathMappingRows = (rows: any[]): any[] => {
    if (!rows || !rows.length) return [{ source: '', target: '', slash_mode: 'auto' }];
    return rows.map((row: any) => ({
      source: typeof row?.source === 'string' ? row.source : '',
      target: typeof row?.target === 'string' ? row.target : '',
      slash_mode: normalizeSlashMode(row?.slash_mode),
    }));
  };

  const PathMapEditor = ({
    value,
    defaultValue,
    onChange,
    onReset,
    variant,
  }: {
    value: any;
    defaultValue: any;
    onChange: (next: any) => Promise<void> | void;
    onReset: () => Promise<void> | void;
    variant: 'system' | 'plugin';
  }) => {
    const storedRows = normalizePathMappingList(value);
    const defaultRows = normalizePathMappingList(defaultValue);
    const storedKey = JSON.stringify(storedRows);
    const defaultKey = JSON.stringify(defaultRows);
    const [draft, setDraft] = React.useState(() => ensurePathMappingRows(storedRows));

    React.useEffect(() => {
      setDraft(ensurePathMappingRows(storedRows));
    }, [storedKey]);

    const sanitizedDraft = React.useMemo(
      () =>
        draft.map((row: any) => ({
          source: (typeof row?.source === 'string' ? row.source : '').trim(),
          target: (typeof row?.target === 'string' ? row.target : '').trim(),
          slash_mode: normalizeSlashMode(row?.slash_mode),
        })),
      [draft],
    );
    const filteredDraft = React.useMemo(
      () => sanitizedDraft.filter((row: any) => row.source),
      [sanitizedDraft],
    );
    const dirty = JSON.stringify(filteredDraft) !== storedKey;
    const resetDisabled = storedKey === defaultKey && !dirty;
    const [pending, setPending] = React.useState(false);

    const updateRow = (index: number, field: 'source' | 'target' | 'slash_mode', value: string) => {
      setDraft((rows: any[]) =>
        rows.map((row: any, idx: number) => {
          if (idx !== index) return row;
          if (field === 'slash_mode') {
            return { ...row, slash_mode: value };
          }
          return { ...row, [field]: value };
        }),
      );
    };

    const removeRow = (index: number) => {
      setDraft((rows: any[]) => {
        const next = rows.filter((_: any, idx: number) => idx !== index);
        return ensurePathMappingRows(next);
      });
    };

    const addRow = () => {
      setDraft((rows: any[]) => [...rows, { source: '', target: '', slash_mode: 'auto' }]);
    };

    const handleSave = async () => {
      if (pending || !dirty) return;
      setPending(true);
      try {
        await onChange(filteredDraft);
        setDraft(ensurePathMappingRows(filteredDraft));
      } catch (err) {
        console.error('[PathMapEditor] save failed', err);
      } finally {
        setPending(false);
      }
    };

    const handleReset = async () => {
      if (pending) return;
      setPending(true);
      try {
        await onReset();
        setDraft(ensurePathMappingRows(defaultRows));
      } catch (err) {
        console.error('[PathMapEditor] reset failed', err);
      } finally {
        setPending(false);
      }
    };

    const cellInputStyle: React.CSSProperties = {
      width: '100%',
      padding: '4px 6px',
      background: '#111',
      color: '#eee',
      border: '1px solid #333',
      fontSize: 12,
    };
    const selectStyle: React.CSSProperties = { ...cellInputStyle, minWidth: 110 };
    const actionBtn: React.CSSProperties = {
      fontSize: 11,
      padding: '4px 6px',
      cursor: pending ? 'not-allowed' : 'pointer',
    };
    const footerStyle: React.CSSProperties = { display: 'flex', gap: 8, marginTop: 8 };

    const introCopy = variant === 'system'
      ? 'Convert the paths from the format used by stash into the format used by Stash-AI-Server (AI Overhaul Backend). This is needed when stash or Stash-AI-Server are running in docker or if they\'re on different computers.'
      : "Convert the paths from the format used by stash into the format used by this plugin's server. This is needed when stash or this plugin's server are running in docker or if they're on different computers.";
    const targetLabel = variant === 'system' ? 'Stash-AI-Server Path' : "Path From Plugin's Server";

    return (
      <div style={{ border: '1px solid #2a2a2a', borderRadius: 4, padding: 8, background: '#101010' }}>
        <div style={{ fontSize: 11, opacity: 0.75, marginBottom: 6 }}>{introCopy}</div>
        <table style={{ ...tableStyle, marginBottom: 8 }}>
          <thead>
            <tr>
              <th style={thtd}>Stash Path</th>
              <th style={thtd}>{targetLabel}</th>
              <th style={thtd}>Slash Mode</th>
              <th style={{ ...thtd, width: '1%', whiteSpace: 'nowrap' }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {draft.map((row: any, idx: number) => (
              <tr key={idx}>
                <td style={thtd}>
                  <input
                    style={cellInputStyle}
                    value={row.source}
                    placeholder="E:\\Media\\"
                    onChange={(e: any) => updateRow(idx, 'source', e.target.value)}
                    disabled={pending}
                  />
                </td>
                <td style={thtd}>
                  <input
                    style={cellInputStyle}
                    value={row.target}
                    placeholder="/media/"
                    onChange={(e: any) => updateRow(idx, 'target', e.target.value)}
                    disabled={pending}
                  />
                </td>
                <td style={thtd}>
                  <select
                    style={selectStyle}
                    value={normalizeSlashMode(row.slash_mode)}
                    onChange={(e: any) => updateRow(idx, 'slash_mode', e.target.value)}
                    disabled={pending}
                  >
                    {PATH_SLASH_MODES.map(mode => (
                      <option key={mode} value={mode}>
                        {PATH_SLASH_MODE_LABELS[mode]}
                      </option>
                    ))}
                  </select>
                </td>
                <td style={{ ...thtd, textAlign: 'right' }}>
                  <button
                    type="button"
                    style={actionBtn}
                    onClick={() => removeRow(idx)}
                    disabled={pending}
                  >
                    Remove
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={footerStyle}>
          <button type="button" style={actionBtn} onClick={addRow} disabled={pending}>
            Add Mapping
          </button>
          <button type="button" style={actionBtn} onClick={handleSave} disabled={pending || !dirty}>
            Save
          </button>
          <button
            type="button"
            style={actionBtn}
            onClick={handleReset}
            disabled={pending || resetDisabled}
          >
            Reset
          </button>
        </div>
      </div>
    );
  };

  // Compose installed plugin rows
  function renderInstalled() {
    if (!installed.length) {
      return <div style={{ fontSize: 12, opacity: 0.7 }}>No plugins installed.</div>;
    }

    const findCatalogEntry = (pluginName: string, version?: string) => {
      for (const [sourceName, entries] of Object.entries(catalog) as [string, any[]][]) {
        for (const entry of entries) {
          if (entry.plugin_name !== pluginName) continue;
          if (version && entry.version !== version) continue;
          return { source: sourceName, entry };
        }
      }
      return null;
    };

    const backendVersionLabel = backendVersion || (backendVersionStatus === 'loading' ? 'detecting…' : 'unknown');
    const pluginStatusTheme = (status?: string) => {
      const normalized = (status || 'unknown').toLowerCase();
      switch (normalized) {
        case 'active':
          return { label: 'Active', bg: '#2ea043', fg: '#0d1117', detailColor: '#b6f0c1' };
        case 'incompatible':
          return { label: 'Incompatible', bg: '#f85149', fg: '#0d1117', detailColor: '#f9b3ad' };
        case 'dependency_inactive':
          return { label: 'Dependency inactive', bg: '#d4a72c', fg: '#0d1117', detailColor: '#fbe39c' };
        case 'dependency_missing':
          return { label: 'Dependency missing', bg: '#d4a72c', fg: '#0d1117', detailColor: '#fbe39c' };
        case 'error':
          return { label: 'Error', bg: '#f85149', fg: '#0d1117', detailColor: '#f9b3ad' };
        case 'removed':
          return { label: 'Removed', bg: '#484f58', fg: '#f0f6fc', detailColor: '#c9d1d9' };
        default:
          return { label: normalized.replace(/_/g, ' ') || 'unknown', bg: '#484f58', fg: '#f0f6fc', detailColor: '#c9d1d9' };
      }
    };
    const shortenStatusDetail = (text: string) => {
      const trimmed = (text || '').trim();
      if (!trimmed) return trimmed;
      if (trimmed.length > 220) return `${trimmed.slice(0, 217)}...`;
      return trimmed;
    };

    return (
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thtd}>Plugin</th>
            <th style={thtd}>Version</th>
            <th style={thtd}>Latest</th>
            <th style={thtd}>Status</th>
            <th style={thtd}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {installed.map((p: any) => {
            const latest = latestVersions[p.name];
            const updateAvailable = latest && isVersionNewer(latest, p.version);
            const rowBackground = updateAvailable
              ? '#262214'
              : p.status === 'removed'
              ? '#201818'
              : p.status === 'error'
              ? '#2a1a1a'
              : 'transparent';

            const handleUpdate = async () => {
              const match = latest ? findCatalogEntry(p.name, latest) : null;
              if (!match) {
                alert('Latest version not found in loaded catalogs. Refresh sources and try again.');
                return;
              }
              await updatePlugin(match.source, p.name, p.human_name || p.name);
            };

            const handleReinstall = async () => {
              const match = findCatalogEntry(p.name, latest || undefined) || findCatalogEntry(p.name);
              if (!match) {
                alert('No catalog entry found to reinstall this plugin.');
                return;
              }
              await startInstall(match.source, p.name, true, p.human_name || p.name);
            };

            const handleConfigure = async () => {
              if (openConfig === p.name) {
                setOpenConfig(null);
                return;
              }
              setOpenConfig(p.name);
              if (!pluginSettings[p.name]) {
                await loadPluginSettings(p.name);
              }
            };
            const statusTheme = pluginStatusTheme(p.status);
            const requirementSpec = inferPluginBackendRequirement(p);
            const requirementLabel = requirementSpec ? formatVersionRequirement(requirementSpec) : null;
            const statusDetail = (() => {
              if (p.status === 'incompatible') {
                if (requirementLabel) {
                  return `Requires backend ${requirementLabel}; detected ${backendVersionLabel}.`;
                }
                if (p.last_error) {
                  return shortenStatusDetail(p.last_error);
                }
                return 'This plugin targets a newer backend build.';
              }
              if (p.status && p.status !== 'active' && p.last_error) {
                return shortenStatusDetail(p.last_error);
              }
              return null;
            })();

            return (
              <tr key={p.name} style={{ background: rowBackground }}>
                <td style={thtd} title={p.name}>
                  <div style={{ fontWeight: 600 }}>{p.human_name || p.name}</div>
                  {p.human_name && p.human_name !== p.name && (
                    <div style={{ fontSize: 10, opacity: 0.6 }}>{p.name}</div>
                  )}
                  {p.server_link && (
                    <div style={{ marginTop: 4 }}>
                      <a
                        href={p.server_link}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{ fontSize: 10, color: '#4aa3ff', textDecoration: 'underline' }}
                      >
                        Docs
                      </a>
                    </div>
                  )}
                  {p.last_error && (
                    <div style={{ fontSize: 10, color: '#ff928a', marginTop: 4 }}>
                      Error: {p.last_error}
                    </div>
                  )}
                </td>
                <td style={thtd}>{p.version || '—'}</td>
                <td style={thtd}>{latest || '—'}</td>
                <td style={thtd}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ background: statusTheme.bg, color: statusTheme.fg, fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.4, padding: '2px 8px', borderRadius: 999 }}>
                      {statusTheme.label}
                    </span>
                  </div>
                  {p.migration_head && (
                    <div style={{ fontSize: 10, opacity: 0.6, marginTop: 4 }}>Migration: {p.migration_head}</div>
                  )}
                  {statusDetail && (
                    <div style={{ fontSize: 10, marginTop: 4, color: statusTheme.detailColor }}>
                      {statusDetail}
                    </div>
                  )}
                </td>
                <td style={{ ...thtd, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {updateAvailable ? (
                    <button style={smallBtn} onClick={handleUpdate}>Update</button>
                  ) : null}
                  {p.status === 'error' ? (
                    <button style={smallBtn} onClick={() => reloadPlugin(p.name)}>Retry</button>
                  ) : null}
                  {p.status === 'removed' ? (
                    <button style={smallBtn} onClick={handleReinstall}>Reinstall</button>
                  ) : null}
                  <button style={smallBtn} onClick={() => removePlugin(p.name)}>Remove</button>
                  <button style={smallBtn} onClick={handleConfigure}>
                    {openConfig === p.name ? 'Close' : 'Configure'}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    );
  }

  // Component to handle dynamic loading of custom field renderer scripts
  function CustomFieldLoader({ fieldType, pluginName, field, backendBase, savePluginSetting, loadPluginSettings, setError, renderDefaultInput }: {
    fieldType: string;
    pluginName: string;
    field: any;
    backendBase: string;
    savePluginSetting: any;
    loadPluginSettings: any;
    setError: any;
    renderDefaultInput?: () => any;
  }) {
    const React: any = (window as any).PluginApi?.React || (window as any).React;
    const [renderer, setRenderer] = React.useState(null as any);
    const [loading, setLoading] = React.useState(true);
    const [failed, setFailed] = React.useState(false);
    
    React.useEffect(() => {
      const pluginSpecificName = `${pluginName}_${fieldType}_Renderer`;
      const genericName = `${fieldType}_Renderer`;
      const legacyName = fieldType === 'tag_list_editor' ? 'SkierAITaggingTagListEditor' : null;
      
      // Check if renderer is already available
      const checkRenderer = () => {
        const found = (window as any)[pluginSpecificName] || 
                     (window as any)[genericName] || 
                     (legacyName ? (window as any)[legacyName] : null);
        if (found && typeof found === 'function') {
          setRenderer(() => found);
          setLoading(false);
          return true;
        }
        return false;
      };
      
      if (checkRenderer()) return;
      
      // Try to load the script from the backend server
      // Normalize backendBase to ensure it doesn't end with a slash
      const normalizedBackendBase = backendBase.replace(/\/+$/, '');
      const possiblePaths = [
        `${normalizedBackendBase}/plugins/${pluginName}/${fieldType}.js`,
        `${normalizedBackendBase}/dist/plugins/${pluginName}/${fieldType}.js`,
      ];
      
      // Also try camelCase version
      const typeParts = fieldType.split('_');
      if (typeParts.length > 1) {
        const camelCase = typeParts[0] + typeParts.slice(1).map(p => p.charAt(0).toUpperCase() + p.slice(1)).join('');
        possiblePaths.push(`${normalizedBackendBase}/plugins/${pluginName}/${camelCase}.js`);
        possiblePaths.push(`${normalizedBackendBase}/dist/plugins/${pluginName}/${camelCase}.js`);
      }
      
      let attemptIndex = 0;
      const tryLoad = () => {
        if (attemptIndex >= possiblePaths.length) {
          setLoading(false);
          setFailed(true);
          if ((window as any).AIDebug) {
            console.warn('[PluginSettings.CustomFieldLoader] Failed to load renderer for', fieldType, 'tried:', possiblePaths);
          }
          return;
        }
        
        const path = possiblePaths[attemptIndex];
        // Use fetch + eval instead of script tag to work around CSP script-src-elem restrictions
        // This uses script-src (which has unsafe-eval) instead of script-src-elem
        fetch(path)
          .then(response => {
            if (!response.ok) {
              throw new Error(`HTTP ${response.status}`);
            }
            return response.text();
          })
          .then(scriptText => {
            console.log('[PluginSettings.CustomFieldLoader] Fetched script:', path);
            try {
              // Eval the script - this uses script-src (with unsafe-eval) instead of script-src-elem
              // Create a new function context to avoid polluting global scope
              const scriptFunction = new Function(scriptText);
              scriptFunction();
              // Wait a bit for the script to register, then check again
              setTimeout(() => {
                if (checkRenderer()) {
                  return;
                }
                // Script loaded but renderer not found, try next path
                attemptIndex++;
                tryLoad();
              }, 200);
            } catch (evalError) {
              console.error('[PluginSettings.CustomFieldLoader] Error evaluating script:', path, evalError);
              attemptIndex++;
              tryLoad();
            }
          })
          .catch(error => {
            console.warn('[PluginSettings.CustomFieldLoader] Failed to fetch script:', path, error);
            attemptIndex++;
            tryLoad();
          });
      };
      
      tryLoad();
      
      // Also poll for renderer in case it loads asynchronously (max 10 seconds)
      let pollCount = 0;
      const pollInterval = setInterval(() => {
        pollCount++;
        if (checkRenderer() || pollCount > 20) {
          clearInterval(pollInterval);
          if (pollCount > 20 && !renderer) {
            setLoading(false);
            setFailed(true);
          }
        }
      }, 500);
      
      return () => clearInterval(pollInterval);
    }, [fieldType, pluginName]);
    
    if (renderer) {
      return React.createElement(renderer, {
        field: field,
        pluginName: pluginName,
        backendBase: backendBase,
        savePluginSetting: savePluginSetting,
        loadPluginSettings: loadPluginSettings,
        setError: setError
      });
    }
    
    if (loading) {
      return React.createElement('div', { style: { padding: 8, fontSize: 11, color: '#888', fontStyle: 'italic' } }, 
        `Loading ${fieldType} editor...`);
    }
    
    // Failed to load - use default input if provided, otherwise show error message
    if (failed && renderDefaultInput) {
      return renderDefaultInput();
    }
    
    if (failed) {
      return React.createElement('div', { style: { padding: 8, fontSize: 11, color: '#f85149' } }, 
        `Failed to load ${fieldType} editor. Using default input.`);
    }
    
    return null;
  }

  function FieldRenderer({ f, pluginName }: { f: any; pluginName: string }) {
    const t = f.type || 'string';
    const label = f.label || f.key;
    const savedValue = f.value === undefined ? f.default : f.value;
    
    // Define styles and computed values early so they're available to callbacks
    const changed = savedValue !== undefined && savedValue !== null && f.default !== undefined && savedValue !== f.default;
    const inputStyle: React.CSSProperties = { padding: 6, background: '#111', color: '#eee', border: '1px solid #333', minWidth: 120 };
    const wrap: React.CSSProperties = { position: 'relative', padding: '4px 4px 6px', border: '1px solid #2a2a2a', borderRadius: 4, background: '#101010' };
    const resetStyle: React.CSSProperties = { position: 'absolute', top: 2, right: 4, fontSize: 9, padding: '1px 4px', cursor: 'pointer' };
    const labelTitle = f && f.description ? String(f.description) : undefined;
    const labelEl = React.createElement('span', { title: labelTitle }, 
      React.createElement(React.Fragment, null,
        label,
        changed ? React.createElement('span', { style: { color: '#ffa657', fontSize: 10 } }, ' •') : null
      )
    );

    if (t === 'path_map') {
      const containerStyle: React.CSSProperties = {
        position: 'relative',
        padding: '4px 4px 6px',
        border: '1px solid #2a2a2a',
        borderRadius: 4,
        background: '#101010',
      };
      const storedNormalized = normalizePathMappingList(savedValue);
      const defaultNormalized = normalizePathMappingList(f.default);
      const changedMap = JSON.stringify(storedNormalized) !== JSON.stringify(defaultNormalized);
      return (
        <div style={containerStyle}>
          <div title={f && f.description ? String(f.description) : undefined} style={{ fontSize: 12, marginBottom: 6 }}>
            {label} {changedMap && <span style={{ color: '#ffa657', fontSize: 10 }}>•</span>}
          </div>
          <PathMapEditor
            value={savedValue}
            defaultValue={f.default}
            onChange={async (next) => { await savePluginSetting(pluginName, f.key, next); }}
            onReset={async () => { await savePluginSetting(pluginName, f.key, null); }}
            variant="plugin"
          />
        </div>
      );
    }

    // Check for custom field renderers registered by plugins
    // Supports both plugin-specific (pluginName_type_Renderer) and generic (type_Renderer) naming
    if (t && typeof t === 'string' && t !== 'string' && t !== 'boolean' && t !== 'number' && t !== 'select' && t !== 'path_map') {
      const pluginSpecificName = `${pluginName}_${t}_Renderer`;
      const genericName = `${t}_Renderer`;
      const customRenderer = (window as any)[pluginSpecificName] || (window as any)[genericName];
      const renderer = customRenderer;
      
      // Debug logging
      if ((window as any).AIDebug) {
        console.log('[PluginSettings.FieldRenderer] Custom field type detected:', {
          type: t,
          pluginName: pluginName,
          pluginSpecificName: pluginSpecificName,
          genericName: genericName,
          hasPluginSpecific: !!(window as any)[pluginSpecificName],
          hasGeneric: !!(window as any)[genericName],
          renderer: renderer ? typeof renderer : 'null'
        });
      }
      
      if (renderer && typeof renderer === 'function') {
        if ((window as any).AIDebug) {
          console.log('[PluginSettings.FieldRenderer] Using custom renderer for', t);
        }
        return React.createElement(renderer, {
          field: f,
          pluginName: pluginName,
          backendBase: backendBase,
          savePluginSetting: savePluginSetting,
          loadPluginSettings: loadPluginSettings,
          setError: setError
        });
      } else {
        // Renderer not found - use CustomFieldLoader to dynamically load it
        // CustomFieldLoader will handle fallback to default input if renderer not found
        return React.createElement(CustomFieldLoader, {
          fieldType: t,
          pluginName: pluginName,
          field: f,
          backendBase: backendBase,
          savePluginSetting: savePluginSetting,
          loadPluginSettings: loadPluginSettings,
          setError: setError,
          // Pass the default input rendering logic as fallback
          renderDefaultInput: () => {
            // This will be called if renderer not found - render default text input
            const display = savedValue === undefined || savedValue === null ? '' : String(savedValue);
            const inputKey = `${pluginName}:${f.key}:${display}`;
            const handleBlur = async (event: any) => {
              const next = (event.target as any).value ?? '';
              if (next === display) return;
              await savePluginSetting(pluginName, f.key, next);
            };
            const handleKeyDown = (event: any) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                (event.target as HTMLInputElement).blur();
              }
            };
            const handleReset = async () => {
              await savePluginSetting(pluginName, f.key, null);
            };
            return React.createElement('div', { style: wrap },
              React.createElement('label', { style: { fontSize: 12 } }, 
                React.createElement(React.Fragment, null,
                  labelEl,
                  React.createElement('br'),
                  React.createElement('input', {
                    key: inputKey,
                    style: inputStyle,
                    defaultValue: display,
                    onBlur: handleBlur,
                    onKeyDown: handleKeyDown
                  })
                )
              ),
              changed ? React.createElement('button', { style: resetStyle, onClick: handleReset }, 'Reset') : null
            );
          }
        });
      }
    }

    if (t === 'boolean') {
      return (
        <div style={wrap}>
          <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
            <input type="checkbox" checked={!!savedValue} onChange={(e) => savePluginSetting(pluginName, f.key, (e.target as any).checked)} /> {labelEl}
          </label>
          {changed ? <button style={resetStyle} onClick={() => savePluginSetting(pluginName, f.key, null)}>Reset</button> : null}
        </div>
      );
    }

    if (t === 'number') {
      const display = savedValue === undefined || savedValue === null ? '' : String(savedValue);
      const inputKey = `${pluginName}:${f.key}:${display}`;

      const handleBlur = async (event: any) => {
        const raw = (event.target as any).value;
        if (raw === display) return;
        const trimmed = (raw ?? '').toString().trim();
        const payload = trimmed === '' ? null : Number(trimmed);
        if (payload !== null && Number.isNaN(payload)) {
          return;
        }
        await savePluginSetting(pluginName, f.key, payload);
      };

      const handleKeyDown = (event: any) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          (event.target as HTMLInputElement).blur();
        }
      };

      const handleReset = async () => {
        await savePluginSetting(pluginName, f.key, null);
      };

      return (
        <div style={wrap}>
          <label style={{ fontSize: 12 }}>{labelEl}<br />
            <input
              key={inputKey}
              style={inputStyle}
              type="number"
              defaultValue={display}
              onBlur={handleBlur}
              onKeyDown={handleKeyDown}
            />
          </label>
          {changed ? <button style={resetStyle} onClick={handleReset}>Reset</button> : null}
        </div>
      );
    }

    if (t === 'select' || (f.options && Array.isArray(f.options))) {
      const handleReset = async () => {
        await savePluginSetting(pluginName, f.key, null);
      };

      return (
        <div style={wrap}>
          <label style={{ fontSize: 12 }}>{labelEl}<br />
            <select style={inputStyle} value={savedValue ?? ''} onChange={(e) => savePluginSetting(pluginName, f.key, (e.target as any).value)}>
              <option value="">(unset)</option>
              {(f.options || []).map((o: any, i: number) => (
                <option key={i} value={o}>
                  {typeof o === 'object' ? (o.value ?? o.key ?? JSON.stringify(o)) : String(o)}
                </option>
              ))}
            </select>
          </label>
          {changed ? <button style={resetStyle} onClick={handleReset}>Reset</button> : null}
        </div>
      );
    }

    const display = savedValue === undefined || savedValue === null ? '' : String(savedValue);
    const inputKey = `${pluginName}:${f.key}:${display}`;

    const handleBlur = async (event: any) => {
      const next = (event.target as any).value ?? '';
      if (next === display) return;
      await savePluginSetting(pluginName, f.key, next);
    };

    const handleKeyDown = (event: any) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        (event.target as HTMLInputElement).blur();
      }
    };

    const handleReset = async () => {
      await savePluginSetting(pluginName, f.key, null);
    };

    return (
      <div style={wrap}>
        <label style={{ fontSize: 12 }}>{labelEl}<br />
          <input
            key={inputKey}
            style={inputStyle}
            defaultValue={display}
            onBlur={handleBlur}
            onKeyDown={handleKeyDown}
          />
        </label>
        {changed ? <button style={resetStyle} onClick={handleReset}>Reset</button> : null}
      </div>
    );
  }

  function SystemFieldRenderer({f}:{f:any}) {
    const t = f.type || 'string';
    const label = f.label || f.key;
    const savedValue = f.value === undefined ? f.default : f.value;
    if (t === 'path_map') {
      const containerStyle: React.CSSProperties = { position:'relative', padding:'4px 4px 6px', border:'1px solid #2a2a2a', borderRadius:4, background:'#101010' };
      const storedNormalized = normalizePathMappingList(savedValue);
      const defaultNormalized = normalizePathMappingList(f.default);
      const changedMap = JSON.stringify(storedNormalized) !== JSON.stringify(defaultNormalized);
      return (
        <div style={containerStyle}>
          <div title={f && f.description ? String(f.description) : undefined} style={{fontSize:12, marginBottom:6}}>
            {label} {changedMap && <span style={{color:'#ffa657', fontSize:10}}>•</span>}
          </div>
          <PathMapEditor
            value={savedValue}
            defaultValue={f.default}
            onChange={async (next) => { await saveSystemSetting(f.key, next); }}
            onReset={async () => { await saveSystemSetting(f.key, null); }}
            variant="system"
          />
        </div>
      );
    }
  const changed = savedValue !== undefined && savedValue !== null && f.default !== undefined && savedValue !== f.default;
  const inputStyle: React.CSSProperties = { padding:6, background:'#111', color:'#eee', border:'1px solid #333', minWidth:140 };
  const wrap: React.CSSProperties = { position:'relative', padding:'4px 4px 6px', border:'1px solid #2a2a2a', borderRadius:4, background:'#101010' };
  const resetStyle: React.CSSProperties = { position:'absolute', top:2, right:4, fontSize:9, padding:'1px 4px', cursor:'pointer' };
  const sysLabelTitle = f && f.description ? String(f.description) : undefined;
  const labelEl = <span title={sysLabelTitle}>{label} {changed && <span style={{color:'#ffa657', fontSize:10}}>•</span>}</span>;

    if (t === 'boolean') {
      return <div style={wrap}><label style={{fontSize:12, display:'flex', alignItems:'center', gap:8}}><input type="checkbox" checked={!!savedValue} onChange={e=>saveSystemSetting(f.key, (e.target as any).checked)} /> {labelEl}</label>{changed ? <button style={resetStyle} onClick={()=>saveSystemSetting(f.key, null)}>Reset</button> : null}</div>;
    }

    if (t === 'number') {
      const [draft, setDraft] = React.useState(() => (savedValue === undefined || savedValue === null ? '' : String(savedValue)));
      const [dirty, setDirty] = React.useState(false);
      React.useEffect(() => {
        if (!dirty) {
          setDraft(savedValue === undefined || savedValue === null ? '' : String(savedValue));
        }
      }, [savedValue, dirty]);

      const commit = React.useCallback(async () => {
        if (!dirty) return;
        const normalized = (draft ?? '').toString().trim();
        const payload = normalized === '' ? null : Number(normalized);
        if (payload !== null && Number.isNaN(payload)) {
          return;
        }
        const ok = await saveSystemSetting(f.key, payload);
        if (ok) {
          setDirty(false);
        }
      }, [dirty, draft, f.key]);

      const handleReset = React.useCallback(async () => {
        const prev = draft;
        setDraft('');
        setDirty(false);
        const ok = await saveSystemSetting(f.key, null);
        if (!ok) {
          setDraft(prev);
          setDirty(true);
        }
      }, [draft, f.key]);

      const handleChange = (event: any) => {
        setDraft(event.target.value);
        setDirty(true);
      };

      const handleKeyDown = (event: any) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          (event.target as HTMLInputElement).blur();
        }
      };

      return (
        <div style={wrap}>
          <label style={{fontSize:12}}>{labelEl}<br/>
            <input
              style={inputStyle}
              type="number"
              value={draft}
              onChange={handleChange}
              onBlur={commit}
              onKeyDown={handleKeyDown}
            />
          </label>
          {changed ? <button style={resetStyle} onClick={handleReset}>Reset</button> : null}
        </div>
      );
    }

    if (t === 'select' || (f.options && Array.isArray(f.options))) {
      const handleReset = async () => {
        await saveSystemSetting(f.key, null);
      };
      return (
        <div style={wrap}>
          <label style={{fontSize:12}}>{labelEl}<br/>
            <select style={inputStyle} value={savedValue ?? ''} onChange={e=>saveSystemSetting(f.key, (e.target as any).value)}>
              <option value="">(unset)</option>
              {(f.options||[]).map((o:any,i:number)=><option key={i} value={o}>{typeof o === 'object' ? (o.value ?? o.key ?? JSON.stringify(o)) : String(o)}</option>)}
            </select>
          </label>
          {changed ? <button style={resetStyle} onClick={handleReset}>Reset</button> : null}
        </div>
      );
    }

    const [draft, setDraft] = React.useState(() => (savedValue === undefined || savedValue === null ? '' : String(savedValue)));
    const [dirty, setDirty] = React.useState(false);
    React.useEffect(() => {
      if (!dirty) {
        setDraft(savedValue === undefined || savedValue === null ? '' : String(savedValue));
      }
    }, [savedValue, dirty]);

    const commit = React.useCallback(async () => {
      if (!dirty) return;
      const ok = await saveSystemSetting(f.key, draft);
      if (ok) {
        setDirty(false);
      }
    }, [dirty, draft, f.key]);

    const handleReset = React.useCallback(async () => {
      const prev = draft;
      setDraft('');
      setDirty(false);
      const ok = await saveSystemSetting(f.key, null);
      if (!ok) {
        setDraft(prev);
        setDirty(true);
      }
    }, [draft, f.key]);

    const handleChange = (event: any) => {
      setDraft(event.target.value ?? '');
      setDirty(true);
    };

    const handleKeyDown = (event: any) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        (event.target as HTMLInputElement).blur();
      }
    };

    return (
      <div style={wrap}>
        <label style={{fontSize:12}}>{labelEl}<br/>
          <input
            style={inputStyle}
            value={draft}
            onChange={handleChange}
            onBlur={commit}
            onKeyDown={handleKeyDown}
          />
        </label>
        {changed ? <button style={resetStyle} onClick={handleReset}>Reset</button> : null}
      </div>
    );
  }

  const saveSystemSetting = React.useCallback(async (key: string, value: any) => {
    let previousValue: any;
    let capturedPrev = false;
    setSystemSettings((cur:any[]) => cur.map(f => {
      if (f.key !== key) return f;
      if (!capturedPrev) {
        previousValue = f.value === undefined ? f.default : f.value;
        capturedPrev = true;
      }
      return ({...f, value});
    }));
    try {
      await jfetch(`${backendBase}/api/v1/plugins/system/settings/${encodeURIComponent(key)}`, { method:'PUT', body: JSON.stringify({ value }) });
      void loadSystemHealth();
      return true;
    } catch(e:any){
      setError(e.message);
      if (capturedPrev) {
        setSystemSettings((cur:any[]) => cur.map(f => f.key===key ? ({...f, value: previousValue}) : f));
      }
      return false;
    }
  }, [backendBase, loadSystemHealth]);

  function renderSources() {
    return (
      <div>
        <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
          {sources.map((s:any) => {
            const isSel = s.name === selectedSource;
            return <div key={s.id} style={{border:'1px solid #333', padding:'6px 8px', borderRadius:4, background:isSel?'#2d2d2d':'#1a1a1a'}}>
              <div style={{fontSize:12}}><strong>{s.name}</strong></div>
              <div style={{fontSize:10, opacity:0.7, maxWidth:220, wordBreak:'break-all'}}>{s.url}</div>
              <div style={{display:'flex', gap:4, marginTop:4, flexWrap:'wrap'}}>
                <button style={smallBtn} onClick={()=> setSelectedSource(s.name)} disabled={isSel}>{isSel ? 'Selected' : 'Select'}</button>
                <button style={smallBtn} onClick={()=> refreshSource(s.name)}>Refresh</button>
                <button style={{...smallBtn, color:'#e66'}} onClick={()=> removeSource(s.name)}>Remove</button>
              </div>
              {s.last_error && <div style={{color:'#e99', fontSize:10, marginTop:4}}>Err: {s.last_error}</div>}
            </div>;
          })}
        </div>
        <div style={{marginTop:8}}>
          <input placeholder="source name" value={addSrcName} onChange={e=>setAddSrcName((e.target as any).value)} style={{fontSize:12, padding:4, marginRight:4}} />
          <input placeholder="source url" value={addSrcUrl} onChange={e=>setAddSrcUrl((e.target as any).value)} style={{fontSize:12, padding:4, marginRight:4, width:240}} />
          <button style={smallBtn} onClick={addSource}>Add Source</button>
        </div>
      </div>
    );
  }

  function renderCatalog() {
    if (!selectedSource) return <div style={{fontSize:12, opacity:0.7}}>Select a source to view its catalog.</div>;
    const entries = catalog[selectedSource];
    if (!entries) return <div style={{fontSize:12}}>Loading catalog…</div>;
    if (!entries.length) return <div style={{fontSize:12}}>No entries in catalog.</div>;
    return (
      <table style={tableStyle}>
        <thead><tr><th style={thtd}>Plugin</th><th style={thtd}>Version</th><th style={thtd}>Description</th><th style={thtd}>Installation Instructions</th><th style={thtd}>Actions</th></tr></thead>
        <tbody>{entries.map((e:any) => {
          const inst = installed.find((p:any) => p.name === e.plugin_name);
          const newer = inst && isVersionNewer(e.version, inst.version);
          const serverLink = e.manifest?.serverLink || e.manifest?.server_link;
          const docsLink = e.manifest?.installation || e.manifest?.install || e.manifest?.docs;
          return <tr key={e.plugin_name}>
            <td style={thtd}>{e.manifest?.humanName || e.manifest?.human_name || e.plugin_name}</td>
            <td style={thtd}>{e.version}</td>
            <td style={{...thtd, maxWidth:260}}>{e.description || e.manifest?.description || ''}</td>
            <td style={thtd}>
              {serverLink && <a href={serverLink} target="_blank" rel="noopener noreferrer" style={{display:'inline-block', fontSize:10, marginRight:6, color:'#4aa3ff', textDecoration:'underline'}}>Instructions</a>}
              {docsLink && <a href={docsLink} target="_blank" rel="noopener noreferrer" style={{display:'inline-block', fontSize:10, color:'#4aa3ff', textDecoration:'underline'}}>Docs</a>}
              {(!serverLink && !docsLink) && <span style={{fontSize:10, opacity:0.4}}>—</span>}
            </td>
            <td style={thtd}>
              {!inst && <button style={smallBtn} onClick={()=> { if (selectedSource) startInstall(selectedSource, e.plugin_name, false, e.manifest?.humanName || e.manifest?.human_name || e.plugin_name); }} disabled={!selectedSource}>Install</button>}
              {inst && newer && <button style={smallBtn} onClick={()=>updatePlugin(selectedSource, e.plugin_name, e.manifest?.humanName || e.manifest?.human_name || e.plugin_name)}>Update</button>}
              {inst && !newer && <span style={{fontSize:10, opacity:0.7}}>Installed</span>}
            </td>
          </tr>;
        })}</tbody>
      </table>
    );
  }

  const statusColor = (status: string | undefined) => {
    switch (status) {
      case 'ok':
        return '#2ea043';
      case 'warn':
        return '#d4a72c';
      case 'error':
        return '#f85149';
      default:
        return '#6e7681';
    }
  };

  const backendStatusColor = (status: string | undefined) => {
    switch (status) {
      case 'ok':
        return '#2ea043';
      case 'checking':
        return '#d4a72c';
      case 'error':
        return '#f85149';
      default:
        return '#6e7681';
    }
  };

  const sanitizeRequirement = (rule: string | null | undefined) => {
    if (!rule) return '';
    const trimmed = rule.trim();
    if (!trimmed) return '';
    return trimmed.replace(/\s+/g, ' ');
  };

  const inferPluginBackendRequirement = (plugin: InstalledPlugin | null | undefined): string | null => {
    if (!plugin) return null;
    const direct = sanitizeRequirement(plugin.required_backend);
    if (direct && direct !== '0.0.0' && direct !== '>=0.0.0' && direct !== '= 0.0.0') {
      return direct;
    }
    const rawError = typeof plugin.last_error === 'string' ? plugin.last_error : '';
    if (rawError) {
      const match = rawError.match(/requires backend ([^;\n]+)(?:;|$)/i);
      if (match && match[1]) {
        const inferred = sanitizeRequirement(match[1]);
        if (inferred) return inferred;
      }
    }
    return direct || null;
  };

  const infoItemStyle: React.CSSProperties = {
    border: '1px solid #333',
    borderRadius: 6,
    background: '#121212',
    padding: '10px 12px',
    minWidth: 0,
  };
  const infoLabelStyle: React.CSSProperties = {
    fontSize: 10,
    textTransform: 'uppercase',
    letterSpacing: 0.4,
    opacity: 0.6,
    marginBottom: 4,
  };
  const infoValueStyle: React.CSSProperties = {
    fontSize: 12,
    lineHeight: 1.4,
  };
  const statusGridStyle: React.CSSProperties = {
    width: '100%',
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
    gap: 12,
  };
  const summaryCardStyle: React.CSSProperties = {
    border: '1px solid #333',
    borderRadius: 6,
    background: '#161b22',
    padding: '12px 14px',
  };
  const summaryListStyle: React.CSSProperties = {
    margin: 0,
    paddingLeft: 18,
    fontSize: 12,
    lineHeight: 1.4,
  };
  const infoBadgeStyle = (color: string): React.CSSProperties => ({
    display: 'inline-block',
    padding: '2px 6px',
    borderRadius: 999,
    fontSize: 10,
    fontWeight: 600,
    color: '#0d1117',
    textTransform: 'uppercase',
    letterSpacing: 0.4,
    background: color,
  });
  const pluginActionNoticePalette = {
    error: { bg: '#401', border: '#f85149', color: '#fbb' },
    info: { bg: '#123', border: '#246', color: '#9fc5ff' },
    success: { bg: '#142', border: '#2e8', color: '#b9ffcb' },
  } as const;
  const pluginActionNoticeStyle = (level: 'error' | 'info' | 'success'): React.CSSProperties => {
    const palette = pluginActionNoticePalette[level] || pluginActionNoticePalette.info;
    return {
      background: palette.bg,
      border: `1px solid ${palette.border}`,
      color: palette.color,
      padding: '10px 12px',
      borderRadius: 6,
      fontSize: 12,
      marginBottom: 12,
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      gap: 12,
    };
  };

  const renderHealthCard = (title: string, component: any, kind: 'stash' | 'database') => {
    if (!component) return null;
    const rows: { label: string; value: string }[] = [];
    const details = (component?.details || {}) as Record<string, any>;

    const pushRow = (label: string, raw: any, { allowEmpty = false } = {}) => {
      if (raw === undefined || raw === null) {
        if (allowEmpty) rows.push({ label, value: 'Not set' });
        return;
      }
      let value: string;
      if (typeof raw === 'boolean') {
        value = raw ? 'Yes' : 'No';
      } else if (typeof raw === 'number') {
        value = String(raw);
      } else {
        const str = String(raw).trim();
        if (!str && !allowEmpty) return;
        value = str || 'Not set';
      }
      rows.push({ label, value });
    };

    if (kind === 'stash') {
      pushRow('Configured URL', details.configured_url, { allowEmpty: true });
      if (details.effective_url && details.effective_url !== details.configured_url) {
        pushRow('Effective URL', details.effective_url);
      }
      if (typeof details.api_key_configured === 'boolean') {
        pushRow('API Key', details.api_key_configured ? 'Configured' : 'Not configured');
      }
    } else if (kind === 'database') {
      pushRow('Configured Path', details.configured_path, { allowEmpty: true });
      if (details.mutated_path && details.mutated_path !== details.configured_path) {
        pushRow('Mapped Path', details.mutated_path);
      }
      if (details.resolved_path && details.resolved_path !== details.mutated_path) {
        pushRow('Resolved Path', details.resolved_path);
      }
      if (typeof details.path_exists === 'boolean') {
        pushRow('Path Exists', details.path_exists ? 'Yes' : 'No');
      }
      if (details.mutation_error) {
        pushRow('Mapping Error', details.mutation_error);
      }
    }

    if (component.latency_ms != null) {
      const latency = Number(component.latency_ms);
      pushRow('Latency', `${latency.toFixed(latency >= 100 ? 0 : 1)} ms`);
    }
    if (details.last_error) {
      pushRow('Last Error', details.last_error);
    }

    let hint: string | null = null;
    if (kind === 'stash') {
      if (component.status === 'warn' && component.message.includes('not configured')) {
        hint = 'Set the Stash URL (and API key if needed) under Backend System Settings.';
      } else if (component.status === 'warn' && component.message.includes('API key')) {
        hint = 'Add the Stash API key in Backend System Settings if your Stash requires authentication.';
      } else if (component.status === 'error') {
        hint = 'Verify the Stash URL/API key and ensure the AI server can reach the Stash host.';
      }
    } else if (kind === 'database') {
      if (component.status === 'warn') {
        hint = 'Set the Stash database path in Backend System Settings.';
      } else if (component.status === 'error') {
        if (component.message.includes('not found')) {
          hint = 'Update the database path or add a path mutation so the AI server can see the file.';
        } else {
          hint = 'Confirm the database path points to the Stash SQLite file and adjust path mutations if needed.';
        }
      }
    }

    const badgeStyle: React.CSSProperties = {
      fontSize: 10,
      padding: '2px 6px',
      borderRadius: 999,
      background: statusColor(component.status),
      color: '#0d1117',
      fontWeight: 600,
      textTransform: 'uppercase',
      letterSpacing: 0.4,
    };

    return (
      <div key={title} style={{ flex: '1 1 280px', border: '1px solid #333', borderRadius: 6, background: '#121212', padding: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{title}</div>
          <span style={badgeStyle}>{(component.status || 'unknown').toUpperCase()}</span>
        </div>
        <div style={{ fontSize: 12, marginBottom: rows.length ? 8 : 0 }}>{component.message}</div>
        {rows.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: hint ? 8 : 0 }}>
            {rows.map((row) => (
              <div key={`${title}:${row.label}`} style={{ display: 'flex', fontSize: 11, lineHeight: 1.4 }}>
                <span style={{ width: 140, opacity: 0.7 }}>{row.label}</span>
                <span style={{ flex: 1, wordBreak: 'break-word' }}>{row.value}</span>
              </div>
            ))}
          </div>
        )}
        {hint && <div style={{ fontSize: 11, color: '#c9d1d9' }}>{hint}</div>}
      </div>
    );
  };

  const backendNotice = backendHealthApi && typeof backendHealthApi.buildNotice === 'function'
    ? backendHealthApi.buildNotice(backendHealthState, { onRetry: retryBackendProbe, retryLabel: 'Retry backend request' })
    : null;
  const backendConnectionStatus = backendHealthState?.status || 'idle';
  let backendConnectionLabel = 'Idle';
  switch (backendConnectionStatus) {
    case 'ok':
      backendConnectionLabel = 'Connected';
      break;
    case 'checking':
      backendConnectionLabel = 'Checking';
      break;
    case 'error':
      backendConnectionLabel = 'Offline';
      break;
    default:
      backendConnectionLabel = 'Idle';
      break;
  }
  const backendConnectionColor = backendStatusColor(backendConnectionStatus);
  const backendBaseDisplay = backendHealthState && typeof backendHealthState.backendBase === 'string'
    ? backendHealthState.backendBase.trim()
    : '';
  let backendConnectionErrorDetail: string | null = null;
  if (backendConnectionStatus === 'error') {
    const detail = backendHealthState?.lastError || backendHealthState?.message;
    if (detail) {
      backendConnectionErrorDetail = String(detail).trim();
      if (backendConnectionErrorDetail.length > 160) {
        backendConnectionErrorDetail = `${backendConnectionErrorDetail.slice(0, 157)}...`;
      }
    }
  }

  const backendVersionDisplay = (() => {
    if (backendVersionStatus === 'loading' || backendVersionStatus === 'idle') return 'Checking...';
    if (backendVersionStatus === 'ok') return backendVersion || 'Unknown';
    if (backendVersionStatus === 'error') return backendVersion || 'Unavailable';
    return backendVersion || 'Unknown';
  })();
  const backendSchemaHead = (() => {
    const infoValue = backendVersionInfo && typeof backendVersionInfo.db_alembic_head === 'string' ? backendVersionInfo.db_alembic_head.trim() : '';
    const snapshotValue = systemHealth && typeof systemHealth.db_alembic_head === 'string' ? systemHealth.db_alembic_head.trim() : '';
    const selected = infoValue || snapshotValue;
    return selected || null;
  })();
  const resolvedFrontendVersion = (() => {
    if (typeof frontendVersion === 'string') {
      const trimmed = frontendVersion.trim();
      return trimmed || 'Unknown';
    }
    return 'Detecting...';
  })();
  const backendDraftClean = normalizeBaseValue(backendDraft);
  const backendDraftChanged = backendDraftClean !== backendBase;
  const sharedKeyDirty = sharedKeyDraft !== (sharedKeyRef.current || '');

  const backendRequirementSpec = FRONTEND_MIN_BACKEND_VERSION && FRONTEND_MIN_BACKEND_VERSION.trim()
    ? FRONTEND_MIN_BACKEND_VERSION.trim()
    : null;
  const backendRequirementLabel = backendRequirementSpec ? formatVersionRequirement(backendRequirementSpec) : '';
  const backendRequiresFrontendSpec = React.useMemo(() => {
    const raw = backendVersionInfo?.frontend_min_version ?? backendVersionInfo?.frontendMinVersion;
    if (typeof raw === 'string') {
      const trimmed = raw.trim();
      if (trimmed) return trimmed;
    }
    return null;
  }, [backendVersionInfo]);
  const backendRequiresFrontendLabel = backendRequiresFrontendSpec ? formatVersionRequirement(backendRequiresFrontendSpec) : '';
  const backendRequirementMet = versionSatisfiesRule(backendVersion, backendRequirementSpec);
  const frontendRequirementMet = versionSatisfiesRule(frontendVersion, backendRequiresFrontendSpec);
  const compatibilityWarnings = React.useMemo(() => {
    const warnings: string[] = [];
    if (backendRequirementSpec) {
      if (backendVersion) {
        if (!backendRequirementMet) {
          warnings.push(`Frontend requires backend ${backendRequirementLabel || backendRequirementSpec}, but the detected backend is ${backendVersion}.`);
        }
      } else if (backendVersionStatus !== 'loading') {
        warnings.push(`Frontend requires backend ${backendRequirementLabel || backendRequirementSpec}, but the backend version is unavailable.`);
      }
    }
    if (backendRequiresFrontendSpec) {
      if (frontendVersion) {
        if (!frontendRequirementMet) {
          warnings.push(`Backend requires frontend ${backendRequiresFrontendLabel || backendRequiresFrontendSpec}, but this UI is ${frontendVersion}.`);
        }
      } else {
        warnings.push(`Backend requires frontend ${backendRequiresFrontendLabel || backendRequiresFrontendSpec}, but the frontend version has not been detected yet.`);
      }
    }
    return warnings;
  }, [backendRequirementLabel, backendRequirementMet, backendRequirementSpec, backendRequiresFrontendLabel, backendRequiresFrontendSpec, backendVersion, backendVersionStatus, frontendRequirementMet, frontendVersion]);
  const compatibilitySatisfiedLines = React.useMemo(() => {
    const lines: string[] = [];
    if (backendRequirementSpec && backendRequirementMet && backendVersion) {
      const label = backendRequirementLabel || backendRequirementSpec;
      lines.push(`Frontend requires backend ${label}; detected ${backendVersion}.`);
    }
    if (backendRequiresFrontendSpec && frontendRequirementMet && frontendVersion) {
      const label = backendRequiresFrontendLabel || backendRequiresFrontendSpec;
      lines.push(`Backend requires frontend ${label}; this UI is ${frontendVersion}.`);
    }
    return lines;
  }, [backendRequirementLabel, backendRequirementMet, backendRequirementSpec, backendRequiresFrontendLabel, backendRequiresFrontendSpec, backendVersion, frontendRequirementMet, frontendVersion]);
  const overallStatus = React.useMemo(() => {
    type Level = 'ok' | 'warn' | 'error';
    let level: Level = 'ok';
    const details: string[] = [];
    const push = (severity: Level, text?: string | null) => {
      if (severity === 'error') {
        level = 'error';
      } else if (severity === 'warn' && level === 'ok') {
        level = 'warn';
      }
      if (text) {
        details.push(text);
      }
    };

    if (backendConnectionStatus === 'error') {
      push('error', backendConnectionErrorDetail || 'Frontend cannot reach the backend service.');
    } else if (backendConnectionStatus === 'checking') {
      push('warn', 'Backend connection still initializing.');
    }

    if (backendVersionStatus === 'error') {
      push('warn', backendVersionError || 'Unable to determine backend version.');
    } else if (backendVersionStatus === 'loading' || backendVersionStatus === 'idle') {
      push('warn', 'Backend version check in progress.');
    }

    compatibilityWarnings.forEach((msg) => push('error', msg));

    if (systemHealthError) {
      push('warn', systemHealthError);
    } else if (systemHealthLoading) {
      push('warn', 'System health check running…');
    }

    const healthComponents = [
      { label: 'Stash API', data: systemHealth?.stash_api },
      { label: 'Database access', data: systemHealth?.database },
    ];
    healthComponents.forEach(({ label, data }) => {
      if (!data) return;
      if (data.status === 'error') {
        push('error', `${label}: ${data.message || 'error'}`);
      } else if (data.status === 'warn') {
        push('warn', `${label}: ${data.message || 'warning'}`);
      }
    });

    if (!details.length && compatibilitySatisfiedLines.length) {
      details.push(...compatibilitySatisfiedLines);
    }
    if (!details.length) {
      details.push('All required services reachable and versions compatible.');
    }

    const label = level === 'ok' ? 'Ready' : level === 'warn' ? 'Needs Attention' : 'Action Required';
    const color = level === 'ok' ? '#2ea043' : level === 'warn' ? '#d4a72c' : '#f85149';
    return { level, label, color, details };
  }, [backendConnectionErrorDetail, backendConnectionStatus, backendVersionError, backendVersionStatus, compatibilitySatisfiedLines, compatibilityWarnings, systemHealth, systemHealthError, systemHealthLoading]);

  return (
    <div style={{padding:16, color:'#ddd', fontFamily:'sans-serif'}}>
      <h2 style={{marginTop:0}}>AI Overhaul Settings</h2>
      {backendNotice}
      {error && <div style={{background:'#402', color:'#fbb', padding:8, marginBottom:12, border:'1px solid #600'}}>
        <strong>Error:</strong> {error} <button style={smallBtn} onClick={()=>setError(null)}>x</button>
      </div>}
      <div style={sectionStyle}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={headingStyle}>System Status</h3>
          <button
            style={smallBtn}
            onClick={() => { void loadSystemHealth(); }}
            disabled={systemHealthLoading}
          >{systemHealthLoading ? 'Checking...' : 'Refresh'}</button>
        </div>
        {systemHealthError && (
          <div style={{ fontSize: 12, color: '#f88', marginBottom: 6 }}>
            {systemHealthError}
          </div>
        )}
        {systemHealthLoading && (
          <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 6 }}>Checking status...</div>
        )}
        {!systemHealthLoading && !systemHealthError && !systemHealth && (
          <div style={{ fontSize: 12, opacity: 0.7 }}>Health information unavailable.</div>
        )}
        <div style={{ ...statusGridStyle, marginBottom: (systemHealthLoading || systemHealth) ? 12 : 0 }}>
          <div style={summaryCardStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              <span style={{ width: 18, height: 18, borderRadius: '50%', background: overallStatus.color, boxShadow: `0 0 12px ${overallStatus.color}66` }} />
              <div>
                <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.4, opacity: 0.7 }}>Configuration</div>
                <div style={{ fontSize: 18, fontWeight: 600 }}>{overallStatus.label}</div>
              </div>
            </div>
            <ul style={summaryListStyle}>
              {overallStatus.details.map((detail, idx) => (
                <li key={`overall-detail-${idx}`}>{detail}</li>
              ))}
            </ul>
          </div>
          <div style={infoItemStyle}>
            <div style={infoLabelStyle}>Backend Connection</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={infoBadgeStyle(backendConnectionColor)}>{backendConnectionLabel}</span>
            </div>
            {(backendBaseDisplay || backendConnectionStatus !== 'error') && (
              <div style={{ fontSize: 11, opacity: 0.7, marginTop: 6 }}>
                Base: {backendBaseDisplay || 'Auto-detected'}
              </div>
            )}
            {backendConnectionErrorDetail && (
              <div style={{ fontSize: 11, color: '#f85149', marginTop: 6 }}>{backendConnectionErrorDetail}</div>
            )}
          </div>
          <div style={infoItemStyle}>
            <div style={infoLabelStyle}>Backend Version</div>
            <div style={infoValueStyle}>{backendVersionDisplay}</div>
            {backendSchemaHead && (
              <div style={{ fontSize: 10, opacity: 0.7, marginTop: 4 }}>DB migration: {backendSchemaHead}</div>
            )}
            {backendVersionStatus === 'error' && backendVersionError && (
              <div style={{ fontSize: 10, color: '#f85149', marginTop: 4 }}>{backendVersionError}</div>
            )}
          </div>
          <div style={infoItemStyle}>
            <div style={infoLabelStyle}>Frontend Version</div>
            <div style={infoValueStyle}>{resolvedFrontendVersion}</div>
          </div>
        </div>
        {(systemHealthLoading || systemHealth) && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
            {renderHealthCard('Stash Connection', systemHealth?.stash_api, 'stash')}
            {renderHealthCard('Database Access', systemHealth?.database, 'database')}
          </div>
        )}
        {systemHealth?.timestamp && (
          <div style={{ fontSize: 11, opacity: 0.6, marginTop: 8 }}>
            Last checked: {new Date(systemHealth.timestamp).toLocaleString()}
          </div>
        )}
      </div>
      <div style={sectionStyle}>
        <h3 style={headingStyle}>AI Overhaul Plugin Settings</h3>
        <div style={{display:'flex', flexDirection:'column', gap:8, maxWidth:500}}>
          <label style={{fontSize:12, display:'flex', flexDirection:'column', gap:4}}>Backend Base URL
            <div style={{display:'flex', gap:4}}>
              <input
                value={backendDraft}
                onChange={e=>setBackendDraft((e.target as any).value)}
                style={{flex:1, padding:6, background:'#111', color:'#eee', border:'1px solid #333'}}
                disabled={!selfSettingsInitialized || backendSaving}
              />
              <button
                style={smallBtn}
                onClick={() => { void saveBackendBase(); }}
                disabled={!selfSettingsInitialized || backendSaving || !backendDraftChanged}
              >{backendSaving ? 'Saving...' : 'Save'}</button>
            </div>
            <span style={{fontSize:10, opacity:0.7}}>Stored in Stash plugin configuration. Leave blank to auto-detect the backend service.</span>
          </label>
          <label style={{fontSize:12}}>
            <input
              type="checkbox"
              checked={interactionsEnabled}
              disabled={!selfSettingsInitialized || interactionsSaving}
              onChange={e=>{ void updateInteractions((e.target as any).checked); }}
              style={{marginRight:6}}
            />
            Capture interaction events
            {interactionsSaving && <span style={{fontSize:10, marginLeft:6, opacity:0.7}}>saving...</span>}
          </label>
          <label style={{fontSize:12, display:'flex', flexDirection:'column', gap:4}}>Shared API Key
            <div style={{display:'flex', gap:4}}>
              <input
                type={sharedKeyReveal ? 'text' : 'password'}
                value={sharedKeyDraft}
                onChange={e=>setSharedKeyDraft((e.target as any).value)}
                style={{flex:1, padding:6, background:'#111', color:'#eee', border:'1px solid #333'}}
                placeholder="Not configured"
                autoComplete="new-password"
                disabled={!selfSettingsInitialized || sharedKeySaving}
              />
              <button
                style={smallBtn}
                type="button"
                onClick={() => setSharedKeyReveal(v => !v)}
                disabled={!selfSettingsInitialized}
              >{sharedKeyReveal ? 'Hide' : 'Show'}</button>
              <button
                style={smallBtn}
                onClick={() => { void saveSharedApiKey(); }}
                disabled={!selfSettingsInitialized || sharedKeySaving || !sharedKeyDirty}
              >{sharedKeySaving ? 'Saving…' : 'Save'}</button>
              <button
                style={{...smallBtn, color: '#f88'}}
                onClick={() => { void clearSharedApiKey(); }}
                disabled={!selfSettingsInitialized || sharedKeySaving || !(sharedKeyRef.current || '')}
              >Clear</button>
            </div>
            <span style={{fontSize:10, opacity:0.7}}>Stored in the plugin config and sent as the <code>x-ai-api-key</code> header (and <code>api_key</code> websocket query). This must match the backend system setting to enable the shared secret.</span>
          </label>
          <div style={{fontSize:10, opacity:0.7}}>Task dashboard: <a href="plugins/ai-tasks" style={{color:'#9cf'}}>Open</a></div>
          <div style={{fontSize:10, opacity:0.5}}>Restart backend button not yet implemented (needs backend endpoint).</div>
        </div>
      </div>
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Backend System Settings</h3>
        {systemLoading && <div style={{fontSize:11, opacity:0.7, marginBottom:8}}>Loading system settings…</div>}
        {!systemLoading && systemSettings.length === 0 && <div style={{fontSize:11, opacity:0.7}}>No system settings available.</div>}
        {systemSettings.length > 0 && (
          <div style={{display:'flex', flexWrap:'wrap', gap:12}}>
            {systemSettings.map((f:any)=><div key={f.key} style={{minWidth:220}}><SystemFieldRenderer f={f} /></div>)}
          </div>
        )}
      </div>
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Installed Plugins {loading.installed && <span style={{fontSize:11, opacity:0.7}}>loading…</span>}</h3>
        {pluginActionNotice && (
          <div style={pluginActionNoticeStyle(pluginActionNotice.level)}>
            <span style={{ flex: 1 }}>{pluginActionNotice.message}</span>
            <button style={smallBtn} onClick={() => setPluginActionNotice(null)}>Dismiss</button>
          </div>
        )}
        {renderInstalled()}
  {openConfig && pluginSettings[openConfig] && <div style={{marginTop:12, padding:10, border:'1px solid #333', borderRadius:6, background:'#151515'}}>
          <div style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>
            <div style={{fontSize:13}}><strong>Configure {openConfig}</strong></div>
            <div style={{fontSize:12, opacity:0.7}}><button style={smallBtn} onClick={()=>{ setOpenConfig(null); }}>Close</button></div>
          </div>
          <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:12, marginTop:10}}>
            {pluginSettings[openConfig].map((f:any)=>(<div key={f.key} style={{minWidth:200}}><FieldRenderer f={f} pluginName={openConfig} /></div>))}
          </div>
        </div>}
      </div>
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Sources {loading.sources && <span style={{fontSize:11, opacity:0.7}}>loading…</span>}</h3>
        {renderSources()}
      </div>
      <div style={sectionStyle}>
        <h3 style={headingStyle}>Catalog {selectedSource && <span style={{fontSize:11, opacity:0.7}}>({selectedSource})</span>} {loading.catalog && <span style={{fontSize:11, opacity:0.7}}>loading…</span>}</h3>
        {pluginActionNotice && (
          <div style={pluginActionNoticeStyle(pluginActionNotice.level)}>
            <span style={{ flex: 1 }}>{pluginActionNotice.message}</span>
            <button style={smallBtn} onClick={() => setPluginActionNotice(null)}>Dismiss</button>
          </div>
        )}
        {renderCatalog()}
      </div>
    </div>
  );
};

(function expose(){
  if ((window as any).AIDebug) console.debug('[PluginSettings] exposing global');
  (window as any).AIPluginSettings = PluginSettings;
  (window as any).AIPluginSettingsMount = function(container: HTMLElement) {
    const React: any = (window as any).PluginApi?.React || (window as any).React;
    const ReactDOM: any = (window as any).ReactDOM || (window as any).PluginApi?.ReactDOM;
    if (!React || !ReactDOM) { console.error('[PluginSettings] React/DOM missing'); return; }
    ReactDOM.render(React.createElement(PluginSettings, {}), container);
  };
  try { window.dispatchEvent(new CustomEvent('AIPluginSettingsReady')); } catch {}
})();

export default PluginSettings;
