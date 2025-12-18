// Frontend version bootstrapper. Attempts to detect the running AI Overhaul
// manifest version and exposes it globally so other bundles can read it.
(function initFrontendVersion() {
  const GLOBAL_KEY = 'AIOverhaulFrontendVersion';
  const EVENT_NAME = 'AIFrontendVersionDetected';
  const PLUGIN_NAME = 'AIOverhaul';
  const GRAPHQL_PLUGIN_VERSION_QUERY = `
    query AIOverhaulPluginVersion {
      plugins {
        id
        name
        version
      }
    }
  `;

  const win: any = typeof window !== 'undefined' ? window : undefined;
  if (!win) {
    return;
  }

  function applyVersion(value: string | null) {
    if (!value) return;
    const normalized = String(value).trim();
    if (!normalized) return;
    if (typeof win[GLOBAL_KEY] === 'string' && win[GLOBAL_KEY] === normalized) {
      return;
    }
    try { win[GLOBAL_KEY] = normalized; } catch (_) {}
    try {
      win.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: normalized }));
    } catch (_) {}
  }

  function detectFromGlobals(): string | null {
    try {
      const existing = win[GLOBAL_KEY];
      if (typeof existing === 'string' && existing.trim()) {
        return existing.trim();
      }
      const api = win.PluginApi;
      if (api) {
        const manifest = api.manifest || api.pluginManifest || (api.plugin && api.plugin.manifest);
        if (manifest && typeof manifest.version === 'string') {
          const normalized = manifest.version.trim();
          if (normalized) return normalized;
        }
        if (api.plugin && typeof api.plugin.version === 'string') {
          const normalized = api.plugin.version.trim();
          if (normalized) return normalized;
        }
        if (api.plugins) {
          const named = api.plugins.AIOverhaul || api.plugins.aioverhaul;
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
      const manifest = win.AIOverhaulManifest;
      if (manifest && typeof manifest.version === 'string') {
        const normalized = manifest.version.trim();
        if (normalized) return normalized;
      }
    } catch (_) {}
    return null;
  }

  function normalizeName(value: any): string {
    if (typeof value !== 'string') return '';
    return value.trim().toLowerCase();
  }

  async function fetchVersionFromStash(): Promise<string | null> {
    try {
      const resp = await fetch('/graphql', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ query: GRAPHQL_PLUGIN_VERSION_QUERY }),
      });
      if (!resp.ok) return null;
      const payload = await resp.json().catch(() => null);
      const plugins = payload?.data?.plugins;
      if (!Array.isArray(plugins)) return null;
      const target = normalizeName(PLUGIN_NAME);
      for (const entry of plugins) {
        const id = normalizeName(entry?.id);
        const name = normalizeName(entry?.name);
        if (id === target || name === target) {
          const version = typeof entry?.version === 'string' ? entry.version.trim() : '';
          if (version) return version;
        }
      }
    } catch (_) {
      return null;
    }
    return null;
  }

  function parseYamlVersion(text: string): string | null {
    if (!text) return null;
    const match = text.match(/^\s*version\s*:\s*([^\s#]+)/im);
    if (match && match[1]) {
      const value = match[1].trim();
      return value || null;
    }
    return null;
  }

  function resolveManifestUrl(): string {
    try {
      const currentScript = document.currentScript as HTMLScriptElement | null;
      if (currentScript && currentScript.src) {
        const url = new URL(currentScript.src, window.location.origin);
        const parts = url.pathname.split('/');
        parts.pop();
        url.pathname = [...parts, 'AIOverhaul.yml'].join('/');
        url.search = '';
        url.hash = '';
        return url.toString();
      }
    } catch (_) {}
    return 'AIOverhaul.yml';
  }

  async function fetchManifestVersion(): Promise<string | null> {
    const manifestUrl = resolveManifestUrl();
    try {
      const resp = await fetch(manifestUrl, { credentials: 'same-origin' });
      if (!resp.ok) return null;
      const text = await resp.text();
      return parseYamlVersion(text || '');
    } catch (_) {
      return null;
    }
  }

  (async () => {
    const existing = detectFromGlobals();
    if (existing) {
      applyVersion(existing);
      return;
    }
    const stashVersion = await fetchVersionFromStash();
    if (stashVersion) {
      applyVersion(stashVersion);
      return;
    }
    const fetched = await fetchManifestVersion();
    if (fetched) {
      applyVersion(fetched);
    }
  })();
})();
