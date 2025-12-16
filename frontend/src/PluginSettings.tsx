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
      const detail = body?.detail || res.statusText;
      if (health) {
        if ((res.status >= 500 || res.status === 0) && typeof health.reportError === 'function') {
          try { health.reportError(baseHint, detail || ('HTTP '+res.status)); reportedError = true; } catch (_) {}
        } else if (typeof health.reportOk === 'function') {
          try { health.reportOk(baseHint); } catch (_) {}
        }
      }
      throw new Error(detail || ('HTTP '+res.status));
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
  const backendHealthState = React.useMemo(() => {
    if (backendHealthApi && typeof backendHealthApi.getState === 'function') {
      return backendHealthApi.getState();
    }
    return null;
  }, [backendHealthApi, backendHealthTick]);

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
    // naive semver-ish compare fall back lexicographic
    try {
      const pa = a.split(/\.|-/).map(x=>parseInt(x,10)||0);
      const pb = b.split(/\.|-/).map(x=>parseInt(x,10)||0);
      for (let i=0;i<Math.max(pa.length,pb.length);i++) { const av=pa[i]||0, bv=pb[i]||0; if (av>bv) return true; if (av<bv) return false; }
      return false;
    } catch { return a > b; }
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

  const installPlugin = React.useCallback(async (source: string, plugin: string, overwrite=false, installDependencies=false) => {
    try {
      await jfetch(`${backendBase}/api/v1/plugins/install`, { method:'POST', body: JSON.stringify({ source, plugin, overwrite, install_dependencies: installDependencies }) });
      await loadInstalled();
    } catch(e:any){ setError(e.message); }
  }, [backendBase, loadInstalled]);

  const startInstall = React.useCallback(async (source: string, plugin: string, overwrite=false) => {
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
      await installPlugin(source, plugin, overwrite, installDeps);
    } catch(e:any){ setError(e.message); }
  }, [backendBase, installPlugin]);

  const updatePlugin = React.useCallback(async (source: string, plugin: string) => {
    try { await jfetch(`${backendBase}/api/v1/plugins/update`, { method:'POST', body: JSON.stringify({ source, plugin }) }); await loadInstalled(); } catch(e:any){ setError(e.message); }
  }, [backendBase, loadInstalled]);

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
  }, [loadInstalled, loadSources, loadCatalogFor, selectedSource, sources]);

  // Initial loads
  React.useEffect(() => { loadPluginSettings(THIS_PLUGIN_NAME); }, [loadPluginSettings]);
  React.useEffect(() => { loadInstalled(); loadSources(); }, [loadInstalled, loadSources]);
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

  const PathMapEditor = ({ value, defaultValue, onChange, onReset }: { value: any; defaultValue: any; onChange: (next: any) => Promise<void> | void; onReset: () => Promise<void> | void; }) => {
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

    return (
      <div style={{ border: '1px solid #2a2a2a', borderRadius: 4, padding: 8, background: '#101010' }}>
        <table style={{ ...tableStyle, marginBottom: 8 }}>
          <thead>
            <tr>
              <th style={thtd}>Stash Prefix</th>
              <th style={thtd}>Target Path</th>
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
                    placeholder="E:\\Content\\"
                    onChange={(e: any) => updateRow(idx, 'source', e.target.value)}
                    disabled={pending}
                  />
                </td>
                <td style={thtd}>
                  <input
                    style={cellInputStyle}
                    value={row.target}
                    placeholder="/mnt/content/"
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
        <div style={{ fontSize: 11, opacity: 0.7, marginTop: 6 }}>
          Entries match stash paths by prefix before requests are made. Slash mode normalizes separators; unix also ensures a leading '/'.
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
              await updatePlugin(match.source, p.name);
            };

            const handleReinstall = async () => {
              const match = findCatalogEntry(p.name, latest || undefined) || findCatalogEntry(p.name);
              if (!match) {
                alert('No catalog entry found to reinstall this plugin.');
                return;
              }
              await startInstall(match.source, p.name, true);
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
                  <div>{p.status || 'unknown'}</div>
                  {p.migration_head && (
                    <div style={{ fontSize: 10, opacity: 0.6 }}>Migration: {p.migration_head}</div>
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

  function FieldRenderer({ f, pluginName }: { f: any; pluginName: string }) {
    const t = f.type || 'string';
    const label = f.label || f.key;
    const savedValue = f.value === undefined ? f.default : f.value;

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
          />
        </div>
      );
    }

  const changed = savedValue !== undefined && savedValue !== null && f.default !== undefined && savedValue !== f.default;
  const inputStyle: React.CSSProperties = { padding: 6, background: '#111', color: '#eee', border: '1px solid #333', minWidth: 120 };
  const wrap: React.CSSProperties = { position: 'relative', padding: '4px 4px 6px', border: '1px solid #2a2a2a', borderRadius: 4, background: '#101010' };
  const resetStyle: React.CSSProperties = { position: 'absolute', top: 2, right: 4, fontSize: 9, padding: '1px 4px', cursor: 'pointer' };
  const labelTitle = f && f.description ? String(f.description) : undefined;
  const labelEl = <span title={labelTitle}>{label} {changed && <span style={{ color: '#ffa657', fontSize: 10 }}>•</span>}</span>;

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
      return true;
    } catch(e:any){
      setError(e.message);
      if (capturedPrev) {
        setSystemSettings((cur:any[]) => cur.map(f => f.key===key ? ({...f, value: previousValue}) : f));
      }
      return false;
    }
  }, [backendBase]);

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
              {!inst && <button style={smallBtn} onClick={()=> { if (selectedSource) startInstall(selectedSource, e.plugin_name); }} disabled={!selectedSource}>Install</button>}
              {inst && newer && <button style={smallBtn} onClick={()=>updatePlugin(selectedSource, e.plugin_name)}>Update</button>}
              {inst && !newer && <span style={{fontSize:10, opacity:0.7}}>Installed</span>}
            </td>
          </tr>;
        })}</tbody>
      </table>
    );
  }

  const backendNotice = backendHealthApi && typeof backendHealthApi.buildNotice === 'function'
    ? backendHealthApi.buildNotice(backendHealthState, { onRetry: retryBackendProbe, retryLabel: 'Retry backend request' })
    : null;
  const backendDraftClean = normalizeBaseValue(backendDraft);
  const backendDraftChanged = backendDraftClean !== backendBase;
  const sharedKeyDirty = sharedKeyDraft !== (sharedKeyRef.current || '');

  return (
    <div style={{padding:16, color:'#ddd', fontFamily:'sans-serif'}}>
      <h2 style={{marginTop:0}}>AI Overhaul Settings</h2>
      {backendNotice}
      {error && <div style={{background:'#402', color:'#fbb', padding:8, marginBottom:12, border:'1px solid #600'}}>
        <strong>Error:</strong> {error} <button style={smallBtn} onClick={()=>setError(null)}>x</button>
      </div>}
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
