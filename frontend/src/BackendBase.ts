// Shared helper to determine the backend base URL used by the frontend.
// Exposes a default export and also attaches to window.AIDefaultBackendBase for
// non-module consumers in the minimal build.

const PLUGIN_NAME = 'AIOverhaul';
const CONFIG_QUERY = `query AIOverhaulPluginConfig($ids: [ID!]) {
  configuration {
    plugins(include: $ids)
  }
}`;

let configLoaded = false;
let configLoading = false;

function normalizeBase(raw: unknown): string | null {
  if (typeof raw !== 'string') return null;
  const trimmed = raw.trim();
  return trimmed ? trimmed.replace(/\/$/, '') : '';
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

function applyPluginConfig(base: string | null | undefined, captureEvents: boolean | null | undefined) {
  if (base !== undefined) {
    const normalized = normalizeBase(base);
    if (normalized !== null) {
      try {
        (window as any).AI_BACKEND_URL = normalized;
        window.dispatchEvent(new CustomEvent('AIBackendBaseUpdated', { detail: normalized }));
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
        applyPluginConfig(backendBase, interpretBool(captureEvents));
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

  const explicit = (window as any).AI_BACKEND_URL as string | undefined;
  if (typeof explicit === 'string') return explicit.replace(/\/$/, '');

  if (typeof location !== 'undefined' && location.origin) {
    try {
      const u = new URL(location.origin);
      if (u.hostname && u.hostname !== 'localhost') {
        return '';
      }
      return u.origin.replace(/\/$/, '');
    } catch {
      return '';
    }
  }

  return '';
}

// Also attach as a global so files that are executed before this module can still
// use the shared function when available.
try {
  (window as any).AIDefaultBackendBase = defaultBackendBase;
  (defaultBackendBase as any).loadPluginConfig = loadPluginConfig;
  (defaultBackendBase as any).applyPluginConfig = applyPluginConfig;
} catch {}
