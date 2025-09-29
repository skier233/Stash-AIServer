// Shared helper to determine the backend base URL used by the frontend.
// Exposes a default export and also attaches to window.AIDefaultBackendBase for
// non-module consumers in the minimal build.

const LS_BACKEND_URL = 'AI_BACKEND_URL_OVERRIDE';

export default function defaultBackendBase() {
  const explicit = (window as any).AI_BACKEND_URL as string | undefined;
  if (explicit) return explicit.replace(/\/$/, '');
  const stored = localStorage.getItem(LS_BACKEND_URL);
  if (stored) return stored.replace(/\/$/, '');
  const loc = (location && location.origin) || '';
  try {
    const u = new URL(loc);
    if (u.port === '3000') {
      u.port = (window as any).AI_BACKEND_DEV_PORT || '8000';
      // In dev, still return absolute so CORS is explicit
      return u.toString().replace(/\/$/, '');
    }
    // If we have a port mismatch scenario where backend would normally be same host different port (CSP risk),
    // prefer relative base ('') so requests become same-origin relative paths proxied by host (if backend is embedded).
    // Only do this when not on localhost dev port 3000 and no explicit override.
    // Example: page served from http://stash/ (no port) and backend expected at http://stash:8000 -> use relative.
    if (u.hostname && u.hostname !== 'localhost') {
      // Return empty string to signal relative usage
      return '';
    }
  } catch {}
  return (loc || 'http://localhost:8000').replace(/\/$/, '');
}

// Also attach as a global so files that are executed before this module can still
// use the shared function when available.
try { (window as any).AIDefaultBackendBase = defaultBackendBase; } catch {}
