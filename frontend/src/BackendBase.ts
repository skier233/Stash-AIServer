// Shared helper to determine the backend base URL used by the frontend.
// Exposes a default export and also attaches to window.AIDefaultBackendBase for
// non-module consumers in the minimal build.

const PLUGIN_NAME = 'AIOverhaul';
// Local default to keep the UI functional before plugin config loads.
const DEFAULT_BACKEND_BASE = 'http://localhost:4153';
const STORAGE_KEY = 'ai_backend_base_url';
const CONFIG_QUERY = `query AIOverhaulPluginConfig($ids: [ID!]) {
  configuration {
    plugins(include: $ids)
  }
}`;
const SHARED_KEY_EVENT = 'AISharedApiKeyUpdated';
const SHARED_KEY_HEADER = 'x-ai-api-key';
const SHARED_KEY_QUERY = 'api_key';
const SHARED_KEY_STORAGE = 'ai_shared_api_key';

let configLoaded = false;
let configLoading = false;
let sharedApiKeyValue = '';

function getOrigin(): string {
  try {
    if (typeof location !== 'undefined' && location.origin) {
      return location.origin.replace(/\/$/, '');
    }
  } catch {}
  return '';
}

function normalizeBase(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  if (!trimmed) return '';
  const cleaned = trimmed.replace(/\/$/, '');
  const origin = getOrigin();
  if (origin && cleaned === origin) {
    return '';
  }
  return cleaned;
}

function interpretBool(raw: unknown): boolean | null {
  if (typeof raw === 'boolean') return raw;
  if (typeof raw === 'number') return raw !== 0;
  if (typeof raw === 'string') {
    const lowered = raw.trim().toLowerCase();
    if (!lowered) return false;
    if (['1', 'true', 'yes', 'on'].includes(lowered)) return true;
    if (['0', 'false', 'no', 'off'].includes(lowered)) return false;
  }
  return null;
}

function normalizeSharedKey(raw: unknown): string {
  if (typeof raw !== 'string') return '';
  return raw.trim();
}

function setSharedApiKey(raw: unknown) {
  const normalized = normalizeSharedKey(raw);
  if (normalized === sharedApiKeyValue) return;
  sharedApiKeyValue = normalized;
  try {
    if (normalized) {
      try { sessionStorage.setItem(SHARED_KEY_STORAGE, normalized); } catch {}
    } else {
      try { sessionStorage.removeItem(SHARED_KEY_STORAGE); } catch {}
    }
    (window as any).AI_SHARED_API_KEY = normalized;
    window.dispatchEvent(new CustomEvent(SHARED_KEY_EVENT, { detail: normalized }));
  } catch {}
}

export function getSharedApiKey(): string {
  if (sharedApiKeyValue) return sharedApiKeyValue;
  try {
    const stored = sessionStorage.getItem(SHARED_KEY_STORAGE);
    if (typeof stored === 'string' && stored.trim()) {
      sharedApiKeyValue = stored.trim();
      return sharedApiKeyValue;
    }
  } catch {}
  try {
    const globalValue = (window as any).AI_SHARED_API_KEY;
    if (typeof globalValue === 'string') {
      sharedApiKeyValue = globalValue.trim();
      return sharedApiKeyValue;
    }
  } catch {}
  return '';
}

function withSharedKeyHeaders(init?: RequestInit): RequestInit {
  const key = getSharedApiKey();
  if (!key) return init ? init : {};
  const next: RequestInit = { ...(init || {}) };
  const headers = new Headers(init?.headers || {});
  headers.set(SHARED_KEY_HEADER, key);
  next.headers = headers;
  return next;
}

function appendSharedApiKeyQuery(url: string): string {
  const key = getSharedApiKey();
  if (!key) return url;
  try {
    const base = getOrigin() || undefined;
    const resolved = new URL(url, url.startsWith('http://') || url.startsWith('https://') || url.startsWith('ws://') || url.startsWith('wss://') ? undefined : base);
    resolved.searchParams.set(SHARED_KEY_QUERY, key);
    return resolved.toString();
  } catch {
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}${SHARED_KEY_QUERY}=${encodeURIComponent(key)}`;
  }
}

function applyPluginConfig(base: string | null | undefined, captureEvents: boolean | null | undefined, sharedKey: string | null | undefined) {
  if (base !== undefined) {
    const normalized = normalizeBase(base);
    if (normalized !== null) {
      const value = normalized || '';
      try {
        (window as any).AI_BACKEND_URL = value;
        try {
          if (value) {
            sessionStorage.setItem(STORAGE_KEY, value);
          } else {
            sessionStorage.removeItem(STORAGE_KEY);
          }
        } catch {}
        window.dispatchEvent(new CustomEvent('AIBackendBaseUpdated', { detail: value }));
      } catch {}
    }
  }
  if (captureEvents !== undefined && captureEvents !== null) {
    const normalized = !!captureEvents;
    try { (window as any).__AI_INTERACTIONS_ENABLED__ = normalized; } catch {}
    try {
      const tracker = (window as any).stashAIInteractionTracker;
      if (tracker) {
        if (typeof tracker.setEnabled === 'function') tracker.setEnabled(normalized);
        else if (typeof tracker.configure === 'function') tracker.configure({ enabled: normalized });
      }
    } catch {}
  }
  if (sharedKey !== undefined) {
    setSharedApiKey(sharedKey);
  }
}

async function loadPluginConfig(): Promise<void> {
  if (configLoaded || configLoading) return;
  configLoading = true;
  try {
    const resp = await fetch('/graphql', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ query: CONFIG_QUERY, variables: { ids: [PLUGIN_NAME] } }),
    });
    if (!resp.ok) return;
    const payload = await resp.json().catch(() => null);
    const plugins = payload?.data?.configuration?.plugins;
    if (plugins && typeof plugins === 'object') {
      const entry = plugins[PLUGIN_NAME];
      if (entry && typeof entry === 'object') {
        const backendBase = (entry as any).backend_base_url ?? (entry as any).backendBaseUrl ?? (entry as any).backendBaseURL;
        const captureEvents = (entry as any).capture_events ?? (entry as any).captureEvents ?? (entry as any).captureEventsEnabled;
        const sharedKey = (entry as any).shared_api_key ?? (entry as any).sharedApiKey ?? (entry as any).sharedKey;
        applyPluginConfig(backendBase, interpretBool(captureEvents), typeof sharedKey === 'string' ? sharedKey : undefined);
      }
    }
  } catch {}
  finally {
    configLoaded = true;
    configLoading = false;
  }
}

export default function defaultBackendBase(): string {
  try {
    if (!configLoaded) loadPluginConfig();
  } catch {}

  try {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (stored && typeof stored === 'string') {
      const normalized = normalizeBase(stored);
      if (normalized !== null && normalized !== undefined) {
        return normalized;
      }
    }
  } catch {}

  if (typeof (window as any).AI_BACKEND_URL === 'string') {
    const explicit = normalizeBase((window as any).AI_BACKEND_URL);
    if (explicit !== null && explicit !== undefined) {
      return explicit;
    }
    return '';
  }

  return DEFAULT_BACKEND_BASE;
}

// Also attach as a global so files that are executed before this module can still
// use the shared function when available.
try {
  (window as any).AIDefaultBackendBase = defaultBackendBase;
  (defaultBackendBase as any).loadPluginConfig = loadPluginConfig;
  (defaultBackendBase as any).applyPluginConfig = applyPluginConfig;
  (window as any).AISharedApiKeyHelper = {
    get: getSharedApiKey,
    withHeaders: withSharedKeyHeaders,
    appendQuery: appendSharedApiKeyQuery,
  };
} catch {}
