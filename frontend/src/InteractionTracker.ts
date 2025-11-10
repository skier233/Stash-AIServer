// =============================================================================
// InteractionTracker - Core user interaction & consumption analytics
// =============================================================================
// Purpose: Collect ONLY events useful for recommendation systems while keeping
// implementation lightweight and decoupled from UI components.
//
// Design Goals:
//  * Minimal public API; internal batching + robustness.
//  * Data model optimized for downstream recommendation pipelines.
//  * Focus on scenes (video consumption), images, galleries. Extendable.
//  * Session-scoped (tab/sessionStorage) with soft continuation if same tab.
//  * Graceful offline: localStorage queue + retry. sendBeacon on unload.
//  * Avoid over-emitting: aggregate watch segments; throttle progress.
//  * No dependency on legacy messy trackers; selectively inspired only.
//
// NOTE: This file intentionally avoids React imports so it can be built as a
// standalone IIFE like other integration utilities.
// =============================================================================

// ----------------------------- Event Taxonomy -------------------------------
// session_start            - New session established
// session_end              - Best-effort on unload/visibility change
// scene_view               - User navigated to a scene detail page
// scene_watch_start        - Playback started (from paused -> playing)
// scene_seek               - User seeks (metadata: from, to, delta, direction)
// scene_watch_progress     - Throttled periodic progress (metadata: position, percent)
// scene_watch_complete     - Video ended (natural end)
// (scene summary support removed)
// image_view               - Single image detail view (entity_id = image id)
// gallery_view             - Gallery detail view (entity_id = gallery id)
// (Future) performer_view, tag_view, recommendation_click, etc.
// ---------------------------------------------------------------------------

// ---------------------------- Type Definitions -----------------------------
export type InteractionEventType =
  | 'session_start'
  | 'session_end'
  | 'scene_view'
  | 'scene_page_enter'
  | 'scene_page_leave'
  | 'scene_watch_start'
  | 'scene_watch_pause'
  | 'scene_seek'
  | 'scene_watch_progress'
  | 'scene_watch_complete'
  | 'image_view'
  | 'gallery_view'
  | 'library_search';

// NOTE: This event interface intentionally mirrors the minimal backend schema
// (see backend InteractionEventIn) — legacy top-level fields like page_url,
// user_agent, viewport, schema_version were removed to reduce payload size.
export interface InteractionEvent<TMeta = any> {
  id: string;
  session_id: string;
  client_id?: string;
  ts: string;
  type: InteractionEventType;
  entity_type: 'scene' | 'image' | 'gallery' | 'session' | 'library';
  entity_id: number;
  metadata?: TMeta; // Arbitrary structured metadata; put page_url here if needed.
}

const NUMERIC_ENTITY_TYPES: ReadonlySet<InteractionEvent['entity_type']> = new Set(['scene', 'image', 'gallery']);

function hashToUint32(value: string): number {
  let hash = 0x811c9dc5; // FNV-1a 32-bit offset basis
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  const out = hash >>> 0;
  return out === 0 ? 1 : out; // avoid zero to keep sentinel-free
}

function normalizeEntityIdForEvent(entityType: InteractionEvent['entity_type'], entityId: string | number | null | undefined): number {
  if (entityId === null || entityId === undefined) throw new Error(`missing entity id for ${entityType}`);
  if (typeof entityId === 'number' && Number.isFinite(entityId)) {
    return Math.trunc(entityId);
  }
  const raw = String(entityId).trim();
  if (!raw) throw new Error(`missing entity id for ${entityType}`);
  if (NUMERIC_ENTITY_TYPES.has(entityType)) {
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) throw new Error(`expected numeric entity id for ${entityType}, received ${entityId}`);
    return Math.trunc(parsed);
  }
  return hashToUint32(`${entityType}:${raw}`);
}

// Internal persisted queue shape (allows future extension)
interface StoredQueueRecord {
  event: InteractionEvent;
  attempts: number;
}

export interface InteractionTrackerConfig {
  endpoint?: string;                  // Base URL (default '/')
  batchPath?: string;                 // POST relative path for batch
  sendIntervalMs?: number;            // Flush interval
  maxBatchSize?: number;              // Upper bound per flush
  progressThrottleMs?: number;        // scene_watch_progress min interval
  immediateTypes?: InteractionEventType[]; // Send ASAP bypassing interval
  localStorageKey?: string;           // Queue storage key
  maxQueueLength?: number;            // Hard cap for stored queue
  debug?: boolean;
  autoDetect?: boolean;               // Attempt automatic scene/video instrumentation
  integratePageContext?: boolean;     // Subscribe to AIPageContext for SPA nav
  videoAutoInstrument?: boolean;      // Instrument video on scene detail automatically
  enabled?: boolean;                  // Master switch to disable all tracking
}

interface WatchSegment { start: number; end: number; }

interface SceneWatchState {
  sceneId: string;
  duration: number | null;
  segments: WatchSegment[];     // merged segments
  lastPlayTs?: number;          // epoch ms when playback started/resumed
  lastProgressEmit?: number;    // epoch ms last progress event emitted
  lastPosition?: number;        // last known currentTime
  video?: HTMLVideoElement | null;
  player?: any;
  playerDispose?: (() => void) | null;
  completed?: boolean;
}

// Resolve backend base using the shared helper when available.
function _resolveBackendBase(): string {
  const globalFn = (window as any).AIDefaultBackendBase;
  if (typeof globalFn !== 'function') throw new Error('AIDefaultBackendBase not initialized. Ensure backendBase is loaded first.');
  return globalFn();
}

// ------------------------------ Tracker Class ------------------------------
export class InteractionTracker {
  private static _instance: InteractionTracker | null = null;
  static get instance(): InteractionTracker { return this._instance || (this._instance = new InteractionTracker()); }

  private cfg: Required<InteractionTrackerConfig>;
  private sessionId: string;
  private clientId: string;
  private queue: StoredQueueRecord[] = [];
  private flushTimer: any = null;
  private pageVisibilityHandler: (() => void) | null = null;
  private beforeUnloadHandler: (() => void) | null = null;
  private currentScene?: SceneWatchState;
  private lastEntityView: { type: string; id: number; rawId?: string; ts: number } | null = null;
  private initialized = false;
  private lastScenePageEntered: string | null = null; // track current scene page for leave events
  private lastLibrarySearchSignature: string | null = null; // dedupe library_search emissions
  private lastSceneViewSceneId: string | null = null; // dedupe rapid successive scene_view emissions
  private lastSceneViewAt: number | null = null; // epoch ms of last accepted scene_view

  private constructor() {
    this.cfg = this.buildConfig({});
    this.sessionId = this.ensureSession();
    this.clientId = this.ensureClientId();
    this.restoreQueue();
    this.bootstrap();
  }

  configure(partial: Partial<InteractionTrackerConfig>) {
    this.cfg = this.buildConfig(partial);
  }

  private buildConfig(partial: Partial<InteractionTrackerConfig>): Required<InteractionTrackerConfig> {
    const resolved = (partial.endpoint ?? _resolveBackendBase()).replace(/\/$/, '');
  // Determine persisted enabled flag from localStorage used by settings UI
  let storedEnabled = true;
  try { storedEnabled = localStorage.getItem('AI_INTERACTIONS_ENABLED') === '1'; } catch {}
  const base: Required<InteractionTrackerConfig> = {
  endpoint: resolved,
      batchPath: '/api/v1/interactions/sync',
      sendIntervalMs: 5000,
      maxBatchSize: 40,
  progressThrottleMs: 5000,
  immediateTypes: ['session_start','scene_watch_complete'],
      localStorageKey: 'ai_overhaul_event_queue',
      maxQueueLength: 1000,
  debug: false, // default off; can be toggled via enableInteractionDebug()
      autoDetect: true,
      integratePageContext: true,
      videoAutoInstrument: true,
      enabled: storedEnabled
    };
    return { ...base, ...partial };
  }
  private lastDetailKey: string | null = null; // prevent duplicate view events
  private videoJsRetryTimers: Map<string, number> = new Map();
  private playerReinstrumentTimers: WeakMap<any, number> = new WeakMap();
  private videoJsHooksInstalled = false;
  private pendingVideoJsPlayers: Set<any> = new Set();
  private trackedVideoJsPlayers: WeakSet<any> = new WeakSet();
  private videoJsDomObserver: MutationObserver | null = null;
  private videoJsFallbackActiveFor: string | null = null;
  private videoJsFallbackTimer: number | null = null;
  private readonly videoJsPrimaryIds: string[] = ['VideoJsPlayer'];

  private ensureSession(): string {
    let id = sessionStorage.getItem('ai_overhaul_session_id');
    if (!id) {
      id = 'sess_' + Date.now() + '_' + Math.random().toString(36).slice(2,8);
      sessionStorage.setItem('ai_overhaul_session_id', id);
    }
    return id;
  }

  private ensureClientId(): string {
    try {
      let id = localStorage.getItem('ai_overhaul_client_id');
      if (!id) {
        id = 'client_' + Date.now() + '_' + Math.random().toString(36).slice(2,8);
        localStorage.setItem('ai_overhaul_client_id', id);
      }
      return id;
    } catch (e) { return 'client_unknown'; }
  }

  private bootstrap() {
    if (this.initialized) return;
    this.initialized = true;
    this.trackInternal('session_start','session','session',{ started_at: Date.now() });
    this.startFlushTimer();
    this.installLifecycleHandlers();
    if (this.cfg.autoDetect) this.tryAutoDetect();
    if (this.cfg.integratePageContext) this.tryIntegratePageContext();
    this.installVideoJsHooks();
    // Capture library search from URL on init (e.g., /scenes?search=...)
    try { this.scanForLibrarySearch(); } catch (e) { /* ignore */ }
    try { this.installLibraryListeners(); } catch (e) { /* ignore */ }
  }

  // Lightweight debounce helper
  private debounce(fn: (...args: any[]) => void, wait = 300) {
    let t: any = null;
    return (...args: any[]) => { if (t) clearTimeout(t); t = setTimeout(()=> fn(...args), wait); };
  }

  // Install listeners to detect library search inputs and filter changes
  private installLibraryListeners() {
    try {
      // remove previous listeners if any by storing on window (best-effort cleanup)
      if ((window as any).__ai_lib_listeners_installed) return;
      (window as any).__ai_lib_listeners_installed = true;

      const collectFilters = (target?: HTMLElement | null): Record<string, any> => {
        const out: Record<string, any> = {};
        try {
          // If we have a target, prefer scanning its nearest filter-related ancestor
          const findFilterContainer = (el: HTMLElement | null) => {
            let node: HTMLElement | null = el;
            while (node) {
              const cls = (node.className || '').toString().toLowerCase();
              if (cls && /filter|filters|filter-panel|facets|facets-panel|sidebar|search-controls/.test(cls)) return node;
              node = node.parentElement;
            }
            return null;
          };

          let scope: Element | Document = document;
          if (target) {
            const container = findFilterContainer(target);
            if (container) scope = container;
          }

          // Collect inputs/selects within the chosen scope
          const nodes = Array.from((scope as Element | Document).querySelectorAll('input,select')) as HTMLInputElement[];
          for (const n of nodes) {
            const name = (n.name || n.getAttribute('data-filter') || n.id || '').toString();
            const cls = (n.className || '').toString().toLowerCase();
            // Accept anything that looks like a filter control or has an explicit data-filter
            const likely = name || cls || n.getAttribute('data-filter');
            if (!likely) continue;
            if (!(name.toLowerCase().includes('filter') || cls.includes('filter') || cls.includes('tag') || cls.includes('performer') || name.toLowerCase().includes('tag') || n.hasAttribute('data-filter'))) {
              // If we're scoped to a container, accept any control inside it
              if (scope === document) continue; // global scan should still be conservative
            }
            const key = name || n.id || (n.getAttribute('data-filter') || cls || 'filter');
            if (n.type === 'checkbox') {
              out[key] = n.checked;
            } else if (n.type === 'radio') {
              if (n.checked) out[key] = n.value;
            } else {
              out[key] = n.value;
            }
          }
        } catch (e) { /* ignore */ }
        return out;
      };

      // Input handler for text search boxes
      const onInput = this.debounce((ev: Event) => {
        try {
          const t = ev.target as HTMLInputElement;
          if (!t) return;
          const val = (t.value || '').trim();
          if (val.length < 2) return;
          // Heuristic: only treat as library search if on a library page
          const p = location.pathname || '';
          if (p.match(/\/scenes(\/|$)/i)) {
            this.trackLibrarySearch('scenes', val, { source: 'input', page_url: location.href });
          } else if (p.match(/\/images(\/|$)/i)) {
            this.trackLibrarySearch('images', val, { source: 'input', page_url: location.href });
          } else if (p.match(/\/galleries(\/|$)/i)) {
            this.trackLibrarySearch('galleries', val, { source: 'input', page_url: location.href });
          } else if (p.match(/\/performers(\/|$)/i)) {
            this.trackLibrarySearch('performers', val, { source: 'input', page_url: location.href });
          } else if (p.match(/\/tags(\/|$)/i)) {
            this.trackLibrarySearch('tags', val, { source: 'input', page_url: location.href });
          }
        } catch (e) { /* ignore */ }
      }, 600);

      document.addEventListener('input', (ev) => {
        try {
          const target = ev.target as HTMLInputElement | null;
          if (!target) return;
          // Only consider text inputs likely to be search boxes
          const isText = target.tagName === 'INPUT' && (target.type === 'text' || target.type === 'search');
          const placeholder = (target.placeholder || '').toLowerCase();
          const name = (target.name || '').toLowerCase();
          if (isText && (placeholder.includes('search') || name.includes('search') || target.className.toLowerCase().includes('search'))) {
            onInput(ev);
          }
        } catch (e) {}
      }, true);

      // Change handler for selects/checkboxes used as filters
      const onChange = this.debounce((ev: Event) => {
        try {
          const p = location.pathname || '';
          let lib: 'scenes'|'images'|'galleries'|'performers'|'tags'|null = null;
          if (p.match(/\/scenes(\/|$)/i)) lib = 'scenes'; else if (p.match(/\/images(\/|$)/i)) lib = 'images';
          else if (p.match(/\/galleries(\/|$)/i)) lib = 'galleries'; else if (p.match(/\/performers(\/|$)/i)) lib = 'performers'; else if (p.match(/\/tags(\/|$)/i)) lib = 'tags';
          if (!lib) return;
          const target = (ev && (ev.target as HTMLElement)) || null;
          let filters = collectFilters(target);
          // If no filters found, try to derive a single-control filter from the changed element.
          // This helps cases where the performers page uses controls without explicit "filter" names/classes.
          if (Object.keys(filters).length === 0 && target) {
            try {
              const el = target as HTMLInputElement | HTMLSelectElement | null;
              if (el) {
                let key = (el.getAttribute('name') || el.getAttribute('data-filter') || el.id || el.className || '').toString();
                key = key.trim() || (el.getAttribute('data-filter') || el.id || el.className || 'filter');
                let value: any = null;
                if ((el as HTMLInputElement).tagName && (el as HTMLInputElement).tagName.toLowerCase() === 'input') {
                  const inp = el as HTMLInputElement;
                  if (inp.type === 'checkbox') value = inp.checked;
                  else if (inp.type === 'radio') { if (inp.checked) value = inp.value; }
                  else value = inp.value;
                } else if ((el as HTMLSelectElement).tagName && (el as HTMLSelectElement).tagName.toLowerCase() === 'select') {
                  value = (el as HTMLSelectElement).value;
                } else {
                  // fallback: try dataset or text
                  value = (el as any).value ?? (el as any).dataset ?? null;
                }
                if (value !== null && value !== undefined && !(typeof value === 'string' && String(value).trim() === '')) {
                  filters = { [String(key)]: value };
                }
              }
            } catch (e) { /* ignore */ }
          }
          if (Object.keys(filters).length === 0) return;
          this.trackLibrarySearch(lib, undefined, { source: 'filters', filters, page_url: location.href });
        } catch (e) { /* ignore */ }
      }, 400);

      document.addEventListener('change', (ev) => {
        try {
          const target = ev.target as HTMLElement | null;
          if (!target) return;
          const tag = target.tagName.toLowerCase();
          if (tag === 'select' || (tag === 'input' && ((target as HTMLInputElement).type === 'checkbox' || (target as HTMLInputElement).type === 'radio'))) {
            onChange(ev);
          }
        } catch (e) {}
      }, true);

      // Re-scan on navigation via history API
      const hookNav = (orig: any) => {
        return function(this: any, ...args: any[]) {
          const res = orig.apply(this, args);
          try { setTimeout(()=>{ (window as any).stashAIInteractionTracker?.scanForLibrarySearch?.(); }, 100); } catch {}
          return res;
        };
      };
      const origPush = history.pushState;
      const origReplace = history.replaceState;
      history.pushState = hookNav(origPush);
      history.replaceState = hookNav(origReplace);
      window.addEventListener('popstate', () => { try { this.scanForLibrarySearch(); } catch {} });

    } catch (e) {
      // swallow errors; this is non-essential
    }
  }

  private tryAutoDetect() {
    const run = () => {
      try {
        const url = window.location.href;
        let sceneId: string | null = null;
        const sceneMatch = url.match(/scenes\/(\d+)/i);
        if (sceneMatch) sceneId = sceneMatch[1];
        const params = new URLSearchParams(window.location.search);
        if (!sceneId && params.get('sceneId')) sceneId = params.get('sceneId');
        if (sceneId) {
          this.log('auto-detect scene id', sceneId);
          this.trackSceneView(sceneId);
          if (this.cfg.videoAutoInstrument) this.ensureVideoInstrumentation(sceneId);
        } else {
          this.log('auto-detect: no scene id pattern matched');
        }
      } catch (e) {
        this.log('auto-detect failed', e);
      }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
  }

  private installVideoJsHooks() {
    if (this.videoJsHooksInstalled) return;

    const attachIfReady = (candidate: any): boolean => {
      if (!candidate || typeof candidate.hook !== 'function') return false;
      if (this.videoJsHooksInstalled) return true;

      this.videoJsHooksInstalled = true;
      try {
        candidate.hook('setup', (player: any) => {
          try {
            this.handleVideoJsPlayerRegistration(player);
          } catch (err) {
            if (this.cfg.debug) this.log('videojs setup hook error', err);
          }
        });
      } catch (err) {
        if (this.cfg.debug) this.log('videojs hook registration failed', err);
      }
      this.instrumentExistingVideoJsPlayers();
      return true;
    };

    if (attachIfReady((window as any).videojs)) return;

    const descriptor = Object.getOwnPropertyDescriptor(window, 'videojs');
    if (descriptor && !descriptor.configurable) {
      window.addEventListener('videojsready', (event: any) => {
        const value = event?.detail ?? (window as any).videojs;
        attachIfReady(value);
      });
    } else {
      let stored = descriptor?.value;
      const originalGetter = descriptor?.get;
      const originalSetter = descriptor?.set;

      Object.defineProperty(window, 'videojs', {
        configurable: true,
        enumerable: descriptor?.enumerable ?? true,
        get() {
          if (originalGetter) return originalGetter.call(window);
          return stored;
        },
        set(value) {
          if (originalSetter) {
            originalSetter.call(window, value);
          } else {
            stored = value;
          }
          const current = originalGetter ? originalGetter.call(window) : value;
          attachIfReady(current);
        },
      });

      const initial = originalGetter ? originalGetter.call(window) : stored;
      attachIfReady(initial);
    }

    if (!this.videoJsHooksInstalled) {
      let tries = 0;
      const maxTries = 40;
      const retry = () => {
        if (attachIfReady((window as any).videojs)) return;
        if (tries >= maxTries) return;
        tries += 1;
        setTimeout(retry, 250);
      };
      retry();
    }

  }

  private activateVideoJsFallbackMonitor(sceneId?: string) {
    const requested = sceneId ?? null;
    const scope = () => document.body || document.documentElement;

    const observerHandler = (mutations: MutationRecord[]) => {
      for (const mut of mutations) {
        mut.addedNodes.forEach(node => this.scanNodeForVideoJsPlayers(node));
      }
    };

    if (this.videoJsDomObserver) {
      this.videoJsFallbackActiveFor = requested;
      const target = scope();
      if (target) this.scanNodeForVideoJsPlayers(target);
      this.refreshVideoJsFallbackTimer();
      return;
    }

    try {
      const target = scope();
      if (!target) return;
      this.videoJsDomObserver = new MutationObserver(observerHandler);
      this.videoJsDomObserver.observe(target, { childList: true, subtree: true });
      this.videoJsFallbackActiveFor = requested;
      this.scanNodeForVideoJsPlayers(target);
      this.refreshVideoJsFallbackTimer();
    } catch (err) {
      if (this.cfg.debug) this.log('videoJs fallback observer attach failed', err);
    }
  }

  private deactivateVideoJsFallbackMonitor(sceneId?: string) {
    if (!this.videoJsDomObserver) return;
    if (sceneId && this.videoJsFallbackActiveFor && this.videoJsFallbackActiveFor !== sceneId) return;
    try {
      this.videoJsDomObserver.disconnect();
    } catch {}
    this.videoJsDomObserver = null;
    this.videoJsFallbackActiveFor = null;
    if (this.videoJsFallbackTimer !== null) {
      window.clearTimeout(this.videoJsFallbackTimer);
      this.videoJsFallbackTimer = null;
    }
  }

  private refreshVideoJsFallbackTimer() {
    if (this.videoJsFallbackTimer !== null) {
      window.clearTimeout(this.videoJsFallbackTimer);
    }
    this.videoJsFallbackTimer = window.setTimeout(() => {
      this.videoJsFallbackTimer = null;
      if (this.pendingVideoJsPlayers.size > 0) {
        this.refreshVideoJsFallbackTimer();
        return;
      }
      this.deactivateVideoJsFallbackMonitor();
    }, 30000);
  }

  private scanNodeForVideoJsPlayers(node: Node) {
    if (!(node instanceof HTMLElement)) return;
    const roots: HTMLElement[] = [];
    if (this.isVideoJsRoot(node)) roots.push(node);
    node.querySelectorAll?.('.video-js, video-js').forEach(el => {
      if (el instanceof HTMLElement && this.isVideoJsRoot(el)) roots.push(el);
    });
    for (const root of roots) {
      const player = this.extractVideoJsPlayer(root);
      if (player && this.shouldInstrumentVideoJsPlayer(player, { root })) {
  this.handleVideoJsPlayerRegistration(player, { root });
      }
    }
  }

  private isVideoJsRoot(el: HTMLElement): boolean {
    if (!el) return false;
    if (el.classList?.contains('video-js')) return true;
    if (el.tagName && el.tagName.toUpperCase() === 'VIDEO-JS') return true;
    if (el.hasAttribute && el.hasAttribute('data-vjs-player')) return true;
    return false;
  }

  private extractVideoJsPlayer(el: HTMLElement | null | undefined): any | null {
    if (!el) return null;
    const root = this.resolveVideoJsRootElement(el);
    if (!root) return null;
    const candidate = (root as any).player || (root as any).player_ || (root as any).Player || null;
    if (candidate && typeof candidate.ready === 'function') return candidate;
    const vjs = (window as any).videojs;
    if (vjs) {
      try {
        if (typeof vjs.getPlayer === 'function') {
          const fromId = root.id ? vjs.getPlayer(root.id) : null;
          if (fromId) return fromId;
          const viaRoot = vjs.getPlayer(root);
          if (viaRoot) return viaRoot;
        }
      } catch {}
    }
    // schedule a short retry in case player is attached asynchronously
    setTimeout(() => {
      const later = (root as any).player || (root as any).player_;
      if (later && typeof later.ready === 'function') this.handleVideoJsPlayerRegistration(later);
    }, 0);
    return null;
  }

  private resolveVideoJsRootElement(el: HTMLElement): HTMLElement | null {
    if (this.isVideoJsRoot(el)) return el;
    const found = el.querySelector?.('.video-js, video-js');
    return found instanceof HTMLElement ? found : null;
  }

  private getVideoJsRootFromPlayer(player: any): HTMLElement | null {
    if (!player) return null;
    try {
      const el = typeof player.el === 'function' ? player.el() : player.el;
      if (el instanceof HTMLElement) return el;
    } catch {}
    try {
      const alt = player.el_;
      if (alt instanceof HTMLElement) return alt;
    } catch {}
    return null;
  }

  private getVideoJsPlayerId(player: any): string | null {
    if (!player) return null;
    try {
      if (typeof player.id === 'function') {
        const id = player.id();
        if (id) return String(id);
      }
    } catch {}
    try {
      const id = player.id_;
      if (id) return String(id);
    } catch {}
    return null;
  }

  private isPrimaryVideoJsRoot(root: HTMLElement | null | undefined): boolean {
    if (!root) return false;
    const lowerId = (root.id || '').toLowerCase();
    for (const id of this.videoJsPrimaryIds) {
      if (lowerId === id.toLowerCase()) return true;
      if (root.closest && root.closest(`#${id}`)) return true;
    }
    const marker = root.getAttribute?.('data-player-id') || root.getAttribute?.('data-vjs-player-id');
    if (marker) {
      const lower = marker.toLowerCase();
      for (const id of this.videoJsPrimaryIds) {
        if (lower.includes(id.toLowerCase())) return true;
      }
    }
    return false;
  }

  private shouldInstrumentVideoJsPlayer(player: any, ctx?: { root?: HTMLElement | null }): boolean {
    if (!player) return false;
    const primaryIds = this.videoJsPrimaryIds;
    const playerId = this.getVideoJsPlayerId(player)?.toLowerCase() ?? null;
    if (playerId && primaryIds.some(id => playerId === id.toLowerCase() || playerId.includes(id.toLowerCase()))) return true;
    const root = ctx?.root ?? this.getVideoJsRootFromPlayer(player);
    if (root && this.isPrimaryVideoJsRoot(root)) return true;
    const tech = this.getVideoJsTechElement(player);
    if (tech?.id) {
      const techId = tech.id.toLowerCase();
      if (primaryIds.some(id => techId === id.toLowerCase() || techId.includes(id.toLowerCase()))) return true;
    }
    return false;
  }

  private instrumentExistingVideoJsPlayers() {
    try {
      const players = this.collectVideoJsPlayers();
      for (const player of players) {
        const root = this.getVideoJsRootFromPlayer(player);
        if (!this.shouldInstrumentVideoJsPlayer(player, { root })) continue;
        this.handleVideoJsPlayerRegistration(player);
      }
    } catch (err) {
      if (this.cfg.debug) this.log('instrumentExistingVideoJsPlayers failed', err);
    }
  }

  private handleVideoJsPlayerRegistration(player: any, ctx?: { root?: HTMLElement | null }) {
    if (!player) return;
    const initialRoot = ctx?.root ?? this.getVideoJsRootFromPlayer(player);
    if (!this.shouldInstrumentVideoJsPlayer(player, { root: initialRoot })) {
      if (this.cfg.debug) this.log('skipping non-primary videojs player', { playerId: this.getVideoJsPlayerId(player) });
      return;
    }
    if (this.trackedVideoJsPlayers.has(player)) return;
    this.trackedVideoJsPlayers.add(player);
    try {
      player.on('dispose', () => {
        this.pendingVideoJsPlayers.delete(player);
      });
    } catch {}
    player.ready(() => {
      const readyRoot = this.getVideoJsRootFromPlayer(player) ?? initialRoot;
      if (!this.shouldInstrumentVideoJsPlayer(player, { root: readyRoot })) {
        if (this.cfg.debug) this.log('skipping non-primary videojs player (ready)', { playerId: this.getVideoJsPlayerId(player) });
        return;
      }
      const sceneId = this.resolveSceneIdFromContext();
      if (!sceneId) {
        this.pendingVideoJsPlayers.add(player);
        return;
      }
      const success = this.instrumentSceneWithVideoJs(sceneId, { player });
      if (!success) {
        this.pendingVideoJsPlayers.add(player);
        this.queuePlayerReinstrument(sceneId, player);
      } else {
        this.pendingVideoJsPlayers.delete(player);
      }
    });
  }

  private resolveSceneIdFromContext(): string | null {
    if (this.currentScene?.sceneId) return this.currentScene.sceneId;
    if (this.lastScenePageEntered) return this.lastScenePageEntered;
    return this.extractSceneIdFromLocation();
  }

  private extractSceneIdFromLocation(): string | null {
    try {
      const url = window.location.href;
      const match = url.match(/scenes\/(\d+)/i);
      if (match) return match[1];
      const params = new URLSearchParams(window.location.search);
      const fromParam = params.get('sceneId') || params.get('id');
      return fromParam || null;
    } catch {
      return null;
    }
  }

  private flushPendingVideoJsPlayers(sceneId: string) {
    if (!this.pendingVideoJsPlayers.size) return;
    for (const player of Array.from(this.pendingVideoJsPlayers)) {
      const success = this.instrumentSceneWithVideoJs(sceneId, { player });
      if (success) {
        this.pendingVideoJsPlayers.delete(player);
      } else {
        this.queuePlayerReinstrument(sceneId, player);
      }
    }
  }

  private tryIntegratePageContext() {
    const attach = () => {
      const api: any = (window as any).AIPageContext;
      if (!api || typeof api.subscribe !== 'function') {
        this.log('PageContext not ready, retrying...');
        setTimeout(attach, 1000);
        return;
      }
      api.subscribe((ctx: any) => this.handlePageContext(ctx));
      this.log('subscribed to AIPageContext');
      try { this.handlePageContext(api.get()); } catch {}
    };
    attach();
  }

  private handlePageContext(ctx: any) {
    if (!ctx) return;
    if (!ctx.isDetailView || !ctx.entityId) return;
    const key = ctx.page + ':' + ctx.entityId;
    if (key === this.lastDetailKey) return;
    this.lastDetailKey = key;
    switch (ctx.page) {
      case 'scenes':
        this.trackSceneView(ctx.entityId, { from: 'PageContext' });
        if (this.cfg.videoAutoInstrument) this.ensureVideoInstrumentation(ctx.entityId);
        break;
      case 'images':
        this.trackImageView(ctx.entityId, { title: ctx.detailLabel });
        break;
      case 'galleries':
        this.trackGalleryView(ctx.entityId, { title: ctx.detailLabel });
        break;
      default:
        break;
    }
  }

  private ensureVideoInstrumentation(sceneId: string) {
    this.flushPendingVideoJsPlayers(sceneId);
    const existing = this.currentScene;
    if (existing && existing.sceneId === sceneId && existing.video && existing.player) return;
    if (this.instrumentSceneWithVideoJs(sceneId)) {
      this.deactivateVideoJsFallbackMonitor(sceneId);
      return;
    }
    this.activateVideoJsFallbackMonitor(sceneId);
    this.scheduleSceneVideoRetry(sceneId, 1);
  }

  private scheduleSceneVideoRetry(sceneId: string, attempt: number) {
    this.cancelSceneVideoRetry(sceneId);
    const maxAttempts = 12;
    if (attempt > maxAttempts) {
      this.log('videojs instrumentation failed after retries', { sceneId, attempt });
      if (typeof console !== 'undefined' && console.error) {
        console.error('[InteractionTracker] videojs instrumentation failed', { sceneId, attempt });
      }
      this.deactivateVideoJsFallbackMonitor(sceneId);
      return;
    }
    this.activateVideoJsFallbackMonitor(sceneId);
    const delay = Math.min(1200, 150 * attempt);
    const handle = window.setTimeout(() => {
      this.videoJsRetryTimers.delete(sceneId);
      const success = this.instrumentSceneWithVideoJs(sceneId, { attempt });
      if (!success) this.scheduleSceneVideoRetry(sceneId, attempt + 1);
    }, delay);
    this.videoJsRetryTimers.set(sceneId, handle);
  }

  private cancelSceneVideoRetry(sceneId: string) {
    const handle = this.videoJsRetryTimers.get(sceneId);
    if (handle !== undefined) {
      window.clearTimeout(handle);
      this.videoJsRetryTimers.delete(sceneId);
    }
  }

  private instrumentSceneWithVideoJs(sceneId: string, opts?: { player?: any; attempt?: number }): boolean {
    const attempt = opts?.attempt ?? 0;
    const player = opts?.player ?? this.getDefaultVideoJsPlayer() ?? this.resolveActiveVideoJsPlayer();
    if (!player) {
      if (attempt > 0) this.log('videojs player unavailable', { sceneId, attempt });
      return false;
    }
    if (typeof player.isDisposed === 'function' && player.isDisposed()) {
      this.log('videojs player disposed', { sceneId });
      return false;
    }
    const tech = this.getVideoJsTechElement(player);
    if (!tech) {
      this.log('videojs tech element missing', { sceneId, attempt });
      return false;
    }
    this.cancelSceneVideoRetry(sceneId);
    this.instrumentSceneVideo(sceneId, tech, player);
    this.deactivateVideoJsFallbackMonitor(sceneId);
    return true;
  }

  private getDefaultVideoJsPlayer(): any | null {
    try {
      const vjs = (window as any).videojs;
      if (!vjs || typeof vjs.getPlayer !== 'function') return null;
      const player = vjs.getPlayer('VideoJsPlayer');
      if (player) return player;
    } catch {}
    return null;
  }

  private collectVideoJsPlayers(): any[] {
    const vjs = (window as any).videojs;
    if (!vjs) return [];
    const seen = new Set<any>();
    const out: any[] = [];

    if (typeof vjs.getPlayers === 'function') {
      try {
        const players = vjs.getPlayers();
        if (players && typeof players === 'object') {
          for (const key of Object.keys(players)) {
            const player = (players as any)[key];
            if (player && !seen.has(player)) {
              seen.add(player);
              out.push(player);
            }
          }
        }
      } catch {}
    }

    try {
      const wrappers = Array.from(document.querySelectorAll('.video-js'));
      for (const wrapper of wrappers) {
        let player: any = null;
        if (typeof vjs.getPlayer === 'function') {
          try { player = vjs.getPlayer(wrapper as any); } catch {}
        }
        if (!player) {
          try { player = typeof vjs === 'function' ? vjs(wrapper) : null; } catch {}
        }
        if (player && !seen.has(player)) {
          seen.add(player);
          out.push(player);
        }
      }
    } catch {}

    return out;
  }

  private resolveActiveVideoJsPlayer(): any | null {
    const primary = this.getDefaultVideoJsPlayer();
    if (primary && (!(typeof primary.isDisposed === 'function') || !primary.isDisposed())) return primary;
    const candidates = this.collectVideoJsPlayers().filter(p => {
      try { return !(typeof p.isDisposed === 'function' && p.isDisposed()); } catch { return true; }
    }).filter(p => this.shouldInstrumentVideoJsPlayer(p));
    if (!candidates.length) return null;
    const scored = candidates.map(player => ({ player, score: this.scoreVideoJsPlayer(player) }));
    scored.sort((a,b) => b.score - a.score);
    return scored[0]?.player ?? null;
  }

  private scoreVideoJsPlayer(player: any): number {
    let score = 0;
    try {
      const el = typeof player.el === 'function' ? player.el() : player.el;
      score += this.isElementLikelyVisible(el) ? 40 : -30;
    } catch {}
    try {
      if (typeof player.paused === 'function' && !player.paused()) score += 25;
    } catch {}
    try {
      const ready = typeof player.readyState === 'function' ? player.readyState() : player.readyState;
      if (typeof ready === 'number' && ready >= 2) score += 10;
    } catch {}
    try {
      const source = typeof player.currentSource === 'function' ? player.currentSource() : null;
      const src = source?.src || (typeof player.currentSrc === 'function' ? player.currentSrc() : '');
      if (src) {
        score += 5;
        if (/transcode|stream|m3u8/i.test(src)) score += 5;
      }
    } catch {}
    try {
      if (typeof player.hasStarted === 'function' && player.hasStarted()) score += 10;
    } catch {}
    return score;
  }

  private isElementLikelyVisible(el: Element | null | undefined): boolean {
    try {
      if (!el) return false;
      if (!(el instanceof HTMLElement)) return true;
      if (!el.isConnected) return false;
      const style = window.getComputedStyle(el);
      if (style) {
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (style.opacity !== undefined && parseFloat(style.opacity) === 0) return false;
      }
      const rect = el.getBoundingClientRect?.();
      if (rect && (rect.width < 2 || rect.height < 2)) return false;
      return true;
    } catch {
      return true;
    }
  }

  private getVideoJsTechElement(player: any): HTMLVideoElement | null {
    if (!player) return null;
    const extract = (candidate: any): HTMLVideoElement | null => {
      if (!candidate) return null;
      if (candidate instanceof HTMLVideoElement) return candidate;
      try {
        const el = typeof candidate.el === 'function' ? candidate.el() : candidate.el;
        if (el instanceof HTMLVideoElement) return el;
        if (el && el.querySelector) {
          const nested = el.querySelector('video');
          if (nested instanceof HTMLVideoElement) return nested;
        }
      } catch {}
      try {
        const elAlt = candidate.el_;
        if (elAlt instanceof HTMLVideoElement) return elAlt;
        if (elAlt && elAlt.querySelector) {
          const nested = elAlt.querySelector('video');
          if (nested instanceof HTMLVideoElement) return nested;
        }
      } catch {}
      return null;
    };

    let tech: any = null;
    try { tech = typeof player.tech === 'function' ? player.tech(true) : player.tech_; } catch {}
    const viaTech = extract(tech);
    if (viaTech) return viaTech;

    const viaPlayer = extract(player);
    if (viaPlayer) return viaPlayer;

    try {
      const root = typeof player.el === 'function' ? player.el() : player.el;
      if (root && root.querySelector) {
        const candidate = root.querySelector('video');
        if (candidate instanceof HTMLVideoElement) return candidate;
      }
    } catch {}

    return null;
  }

  private readCurrentTime(player: any, fallbackVideo?: HTMLVideoElement | null): number | null {
    try {
      if (player && typeof player.currentTime === 'function') {
        const val = player.currentTime();
        if (typeof val === 'number' && Number.isFinite(val)) return val;
      }
    } catch {}
    try {
      if (fallbackVideo && typeof fallbackVideo.currentTime === 'number' && Number.isFinite(fallbackVideo.currentTime)) {
        return fallbackVideo.currentTime;
      }
    } catch {}
    return null;
  }

  private readDuration(player: any, fallbackVideo?: HTMLVideoElement | null): number | null {
    try {
      if (player && typeof player.duration === 'function') {
        const val = player.duration();
        if (typeof val === 'number' && Number.isFinite(val) && val >= 0) return val;
      }
    } catch {}
    try {
      if (fallbackVideo && typeof fallbackVideo.duration === 'number' && Number.isFinite(fallbackVideo.duration) && fallbackVideo.duration >= 0) {
        return fallbackVideo.duration;
      }
    } catch {}
    return null;
  }

  private getPlaybackSnapshot(state: SceneWatchState): { position?: number; duration?: number; percent?: number } {
    const position = this.readCurrentTime(state.player, state.video ?? null);
    let duration = this.readDuration(state.player, state.video ?? null);
    if (!duration && state.duration && Number.isFinite(state.duration)) {
      duration = state.duration;
    }
    if (duration && (!state.duration || Math.abs(state.duration - duration) > 0.5)) {
      state.duration = duration;
    }
    const percent = duration && position !== null && position !== undefined && duration > 0
      ? (position / duration) * 100
      : undefined;
    return {
      position: position === null ? undefined : position,
      duration: duration === null ? undefined : duration,
      percent,
    };
  }

  private isPlayerPaused(state: SceneWatchState): boolean {
    try {
      if (state.player && typeof state.player.paused === 'function') {
        return !!state.player.paused();
      }
    } catch {}
    if (state.video) {
      try { return state.video.paused; } catch {}
    }
    return false;
  }

  private isPlayerSeeking(state: SceneWatchState): boolean {
    try {
      if (state.player && typeof state.player.seeking === 'function') {
        return !!state.player.seeking();
      }
    } catch {}
    if (state.video) {
      try { return state.video.seeking; } catch {}
    }
    return false;
  }

  private attachVideoJsWatcher(state: SceneWatchState, sceneId: string, player: any | null | undefined) {
    if (!player || typeof player.on !== 'function') return;
    if (state.player === player && state.playerDispose) return;
    if (state.playerDispose) this.detachVideoJsWatcher(state);

    const events = ['sourceset', 'loadstart', 'techloadstart', 'playerreset'];
    const handler = () => { this.queuePlayerReinstrument(sceneId, player); };
    for (const evt of events) {
      try { player.on(evt, handler); } catch {}
    }
    try { player.on('ready', handler); } catch {}

    state.player = player;
    state.playerDispose = () => {
      for (const evt of events) {
        try { player.off(evt, handler); } catch {}
      }
      try { player.off('ready', handler); } catch {}
      this.cancelPlayerReinstrument(player);
      state.playerDispose = null;
      state.player = null;
    };
  }

  private detachVideoJsWatcher(state?: SceneWatchState) {
    if (!state) return;
    const dispose = state.playerDispose;
    if (dispose) {
      try { dispose(); } catch {}
    }
    if (state.player) this.cancelPlayerReinstrument(state.player);
    state.playerDispose = null;
    state.player = null;
  }

  private queuePlayerReinstrument(sceneId: string, player: any, attempt = 0) {
    if (!player) return;
    this.cancelPlayerReinstrument(player);
    this.activateVideoJsFallbackMonitor(sceneId);
    try {
      if (this.currentScene && this.currentScene.sceneId === sceneId) {
        // Close out any active segment before the tech swap replaces the <video> element
        const hadActivePlayback = this.captureSegment(true);
        if (!hadActivePlayback) {
          const snap = this.readCurrentTime(player, this.currentScene.video ?? null);
          if (snap !== null) this.currentScene.lastPosition = snap;
        }
      }
    } catch {}
    const delay = attempt === 0 ? 0 : Math.min(600, 80 + attempt * 80);
    const handle = window.setTimeout(() => {
      this.playerReinstrumentTimers.delete(player);
      const success = this.instrumentSceneWithVideoJs(sceneId, { player, attempt });
      if (success) {
        this.pendingVideoJsPlayers.delete(player);
      } else if (attempt < 6) {
        this.queuePlayerReinstrument(sceneId, player, attempt + 1);
      }
    }, delay);
    this.playerReinstrumentTimers.set(player, handle);
  }

  private cancelPlayerReinstrument(player: any) {
    if (!player) return;
    const handle = this.playerReinstrumentTimers.get(player);
    if (handle !== undefined) {
      window.clearTimeout(handle);
      this.playerReinstrumentTimers.delete(player);
    }
  }

  private cleanupVideoElement(video?: HTMLVideoElement | null) {
    if (!video) return;
    try {
      const cleanup = (video as any)._aiInteractionCleanup;
      if (typeof cleanup === 'function') cleanup();
    } catch {}
    try { delete (video as any)._aiInteractionCleanup; } catch {}
  }

  // ---------------------------- Public API ---------------------------------
  public trackSceneView(sceneId: string, opts?: { title?: string; from?: string }) {
    const now = Date.now();
    const dedupeWindowMs = 2000;
    if (this.lastSceneViewSceneId === sceneId && this.lastSceneViewAt !== null && (now - this.lastSceneViewAt) < dedupeWindowMs) {
      this.log('deduping rapid scene_view', { sceneId, msSinceLast: now - this.lastSceneViewAt, from: opts?.from });
      this.lastSceneViewAt = now;
      this.lastSceneViewSceneId = sceneId;
      return;
    }

    let normalizedSceneId: number;
    try {
      normalizedSceneId = normalizeEntityIdForEvent('scene', sceneId);
    } catch (err) {
      this.log('failed to normalize scene id', { sceneId, err });
      return;
    }

    // Emit scene_page_leave for previous scene if different
    if (this.lastScenePageEntered && this.lastScenePageEntered !== sceneId) {
      this.trackInternal('scene_page_leave', 'scene', this.lastScenePageEntered, { next_scene: sceneId });
    }
    
    this.trackInternal('scene_view','scene',sceneId,{ title: opts?.title, from: opts?.from, last_viewed_entity: this.lastEntityView });
    this.lastEntityView = { type: 'scene', id: normalizedSceneId, rawId: sceneId, ts: now };
    // Also emit scene_page_enter event to track page visit timing
    this.trackInternal('scene_page_enter', 'scene', sceneId, { title: opts?.title, from: opts?.from });
    this.lastScenePageEntered = sceneId;
    this.lastSceneViewSceneId = sceneId;
    this.lastSceneViewAt = now;
  }

  public instrumentSceneVideo(sceneId: string, video: HTMLVideoElement, player?: any) {
    if (!video) return;

    if (this.currentScene && this.currentScene.sceneId !== sceneId) {
      this.cleanupVideoElement(this.currentScene.video);
      this.detachVideoJsWatcher(this.currentScene);
      this.currentScene = undefined;
    }

  let state: SceneWatchState = this.currentScene || { sceneId, duration: null, segments: [] };
    if (!this.currentScene) {
      state.video = null;
      state.player = null;
      state.playerDispose = null;
    }

    if (state.video && state.video !== video) {
      this.cleanupVideoElement(state.video);
    }

    if (player !== undefined) {
      if (player && state.player && state.player !== player) {
        this.detachVideoJsWatcher(state);
      }
      state.player = player || null;
    }

    state.sceneId = sceneId;
    state.video = video;
    state.completed = false;
    state.lastPlayTs = undefined;
    state.lastProgressEmit = undefined;
    const playerInitial = this.readCurrentTime(state.player, video);
    const initialTime = playerInitial ?? (Number.isFinite(video.currentTime) ? video.currentTime : undefined);
    if (initialTime !== undefined) {
      if (state.lastPosition == null || Math.abs(state.lastPosition - initialTime) > 0.25) {
        state.lastPosition = initialTime;
      }
    }
    if (video.duration && isFinite(video.duration)) {
      if (!state.duration || Math.abs((state.duration ?? 0) - video.duration) > 0.5) {
        state.duration = video.duration;
      }
    }

    this.currentScene = state;

    this.cleanupVideoElement(video);

    const onPlay = () => {
      const snapshot = this.getPlaybackSnapshot(state);
      state.lastPlayTs = Date.now();
      if (snapshot.position !== undefined) state.lastPosition = snapshot.position;
      this.trackInternal('scene_watch_start','scene',sceneId,{
        position: snapshot.position ?? state.lastPosition ?? (isFinite(video.currentTime) ? video.currentTime : undefined),
        duration: snapshot.duration ?? state.duration ?? (isFinite(video.duration) ? video.duration : undefined)
      });
    };

    const onPause = () => {
      const added = this.captureSegment();
      const snapshot = this.getPlaybackSnapshot(state);
      try {
        const total = this.currentScene ? this.totalWatched(this.currentScene) : undefined;
        this.trackInternal('scene_watch_pause','scene',sceneId,{
          position: snapshot.position ?? state.lastPosition ?? (isFinite(video.currentTime) ? video.currentTime : undefined),
          total_watched: total,
          duration: snapshot.duration ?? state.duration ?? (isFinite(video.duration) ? video.duration : undefined),
          segment_added: added
        });
      } catch {}
    };

    const onEnded = () => {
      this.captureSegment(true);
      const snapshot = this.getPlaybackSnapshot(state);
      this.trackInternal('scene_watch_complete','scene',sceneId,{
        duration: snapshot.duration ?? state.duration ?? (isFinite(video.duration) ? video.duration : undefined),
        position: snapshot.position ?? state.lastPosition ?? state.duration ?? undefined,
        total_watched: this.totalWatched(state),
        segments: state.segments
      });
      state.completed = true;
    };

    const onTimeUpdate = () => {
      const current = this.readCurrentTime(state.player, video) ?? (Number.isFinite(video.currentTime) ? video.currentTime : null);
      if (current == null) return;
      const prev = state.lastPosition;
      if (prev != null) {
        const delta = current - prev;
        if (Math.abs(delta) > 1.0) {
          this.trackInternal('scene_seek','scene',sceneId,{ from: prev, to: current, delta, direction: delta > 0 ? 'forward':'backward', via: 'delta-detect' });
          if (this.cfg.debug) this.log('seek detected (delta)', { from: prev, to: current, delta });
        }
      }
      state.lastPosition = current;
      if (!state.duration && video.duration && isFinite(video.duration)) state.duration = video.duration;
      this.maybeEmitProgress();
    };

    const onLoaded = () => {
      if (video.duration && isFinite(video.duration)) state.duration = video.duration;
    };

    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    video.addEventListener('ended', onEnded);
    video.addEventListener('timeupdate', onTimeUpdate);
    video.addEventListener('loadedmetadata', onLoaded);

    (video as any)._aiInteractionCleanup = () => {
      video.removeEventListener('play', onPlay);
      video.removeEventListener('pause', onPause);
      video.removeEventListener('ended', onEnded);
      video.removeEventListener('timeupdate', onTimeUpdate);
      video.removeEventListener('loadedmetadata', onLoaded);
    };

    if (state.player) this.attachVideoJsWatcher(state, sceneId, state.player);

    if (!video.paused) {
      setTimeout(() => {
        if (video.isConnected && !video.paused) onPlay();
      }, 0);
    }
  }

  public trackImageView(imageId: string, opts?: { title?: string }) {
    let normalizedImageId: number;
    try {
      normalizedImageId = normalizeEntityIdForEvent('image', imageId);
    } catch (err) {
      this.log('failed to normalize image id', { imageId, err });
      return;
    }
    this.trackInternal('image_view','image',imageId,{ title: opts?.title, last_viewed_entity: this.lastEntityView });
    this.lastEntityView = { type: 'image', id: normalizedImageId, rawId: imageId, ts: Date.now() };
  }

  public trackGalleryView(galleryId: string, opts?: { title?: string }) {
    let normalizedGalleryId: number;
    try {
      normalizedGalleryId = normalizeEntityIdForEvent('gallery', galleryId);
    } catch (err) {
      this.log('failed to normalize gallery id', { galleryId, err });
      return;
    }
    this.trackInternal('gallery_view','gallery',galleryId,{ title: opts?.title, last_viewed_entity: this.lastEntityView });
    this.lastEntityView = { type: 'gallery', id: normalizedGalleryId, rawId: galleryId, ts: Date.now() };
  }

  /**
   * Persist a library search or filter action. library should be 'scenes' or 'images'.
   */
  public trackLibrarySearch(library: 'scenes'|'images'|'galleries'|'performers'|'tags', query?: string, filters?: any) {
    const meta: any = { library, query: query ?? null, filters: filters ?? null };
    try {
      // Build a stable signature to suppress duplicates from multiple detection paths
      const sig = library + '|' + JSON.stringify({ q: meta.query, f: meta.filters });
      if (this.lastLibrarySearchSignature === sig) return;
      this.lastLibrarySearchSignature = sig;
    } catch { /* ignore */ }
    this.trackInternal('library_search', 'library' as any, library, meta);
  }

  // Inspect current URL for library query params and emit a library_search if present
  private scanForLibrarySearch() {
    try {
      const p = location.pathname || '';
      const params = new URLSearchParams(location.search || '');
      const q = params.get('search') || params.get('query') || undefined;
      const collected: Record<string,string> = {};
      params.forEach((v,k) => { collected[k] = v; });

      // Determine if this is a library page and which one
      let lib: 'scenes'|'images'|'galleries'|'performers'|'tags'|null = null;
      if (p.match(/\/scenes(\/|$)/i)) lib = 'scenes';
      else if (p.match(/\/images(\/|$)/i)) lib = 'images';
      else if (p.match(/\/galleries(\/|$)/i)) lib = 'galleries';
      else if (p.match(/\/performers(\/|$)/i)) lib = 'performers';
      else if (p.match(/\/tags(\/|$)/i)) lib = 'tags';
      if (!lib) return; // not a library page

      // Decide if we should emit: either query present OR we have meaningful filter params.
      const noiseKeys = new Set(['page','per_page','perpage','offset','limit']);
      const hasMeaningfulFilter = Object.keys(collected).some(k => !noiseKeys.has(k.toLowerCase()));
      if (!q && !hasMeaningfulFilter) return; // nothing to report

      // Attempt light parsing of common encoded filter param 'c'
      if (collected['c']) {
        try {
          const decoded = decodeURIComponent(collected['c']);
          // Store both raw and decoded if different
          if (decoded && decoded !== collected['c']) {
            collected['c_decoded'] = decoded;
          }
        } catch { /* ignore */ }
      }

      this.trackLibrarySearch(lib, q, collected);
    } catch (e) { /* ignore */ }
  }

  public flushNow() { this.flushQueue(); }

  // Expose last viewed entity (scene/image/gallery) for external logic
  public getLastViewedEntity(){ return this.lastEntityView; }

  // Provide a lightweight snapshot of current scene watch state (without video element)
  public getCurrentSceneWatchSnapshot(){
    if (!this.currentScene) return null;
    const { sceneId, duration, segments, lastPosition, completed } = this.currentScene;
    return { sceneId, duration, segments: segments.map(s=>({...s})), lastPosition, completed, totalWatched: this.totalWatched(this.currentScene) };
  }

  // Runtime toggle for console debugging so integrators can verify events
  public enableDebug() { this.cfg.debug = true; this.log('debug enabled'); }
  public disableDebug() { this.log('debug disabled'); this.cfg.debug = false; }
  public setEnabled(v: boolean) { this.cfg.enabled = v; this.log('enabled set to '+v); }

  // --------------------------- Internal Helpers ----------------------------
  private trackInternal(type: InteractionEventType, entityType: InteractionEvent['entity_type'], entityId: string | number, metadata?: any) {
    let normalizedId: number;
    try {
      normalizedId = normalizeEntityIdForEvent(entityType, entityId);
    } catch (err) {
      this.log('failed to normalize entity id', { type, entityType, entityId, err });
      return;
    }

    let meta = metadata ? { ...metadata } : undefined;
    if (typeof entityId === 'string') {
      const raw = entityId.trim();
      if (raw && (meta?.raw_entity_id === undefined)) {
        if (meta) meta.raw_entity_id = raw; else meta = { raw_entity_id: raw };
      }
    }

    const ev: InteractionEvent = {
      id: 'evt_' + Date.now() + '_' + Math.random().toString(36).slice(2,6),
      session_id: this.sessionId,
      client_id: this.clientId,
      ts: new Date().toISOString(),
      type,
      entity_type: entityType,
      entity_id: normalizedId,
      metadata: meta
    };

    // Queue
    if (this.cfg.enabled) this.enqueue(ev); else return;
    // Immediate flush logic
    if (this.cfg.immediateTypes.includes(type)) this.flushQueue();

    // Always provide a clear structured console output when debug is on
    this.log('event captured', ev);
    // Additionally emit a more visible console.info for quick manual QA when debug is true
    if (this.cfg.debug && (console as any).info) {
      const displayId = typeof entityId === 'string' ? entityId : normalizedId;
      (console as any).info('%c[InteractionTracker] %c'+type+'%c -> '+entityType+':'+displayId, 'color:#888', 'color:#0A7;', 'color:#555', ev);
    }
  }

  private enqueue(ev: InteractionEvent) {
    this.queue.push({ event: ev, attempts: 0 });
    if (this.queue.length > this.cfg.maxQueueLength) {
      // Drop oldest events beyond cap
      this.queue.splice(0, this.queue.length - this.cfg.maxQueueLength);
    }
    this.persistQueue();
  }

  private startFlushTimer() {
    if (this.flushTimer) clearInterval(this.flushTimer);
    this.flushTimer = setInterval(() => this.flushQueue(), this.cfg.sendIntervalMs);
  }

  private flushInFlight = false;
  private async flushQueue() {
    if (this.flushInFlight || !this.queue.length) return;
    this.flushInFlight = true;
    try {
      const batch = this.queue.slice(0, this.cfg.maxBatchSize);
      const payload = batch.map(r => r.event);
      const url = this.cfg.endpoint + this.cfg.batchPath;
      // Always send an array (backend expects a list)
      const sendBody = JSON.stringify(payload.length > 1 ? payload : [payload[0]]);
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Explicit: single event must be sent as a 1-element array
        body: sendBody
      });
            let body: any = null;
            try { body = await res.json(); } catch (e) { /* ignore */ }
            if (!res.ok) {
              this.log('flush non-ok response', { status: res.status, body });
              throw new Error('HTTP ' + res.status);
            }
            // If backend returned diagnostics in body.errors, surface them
            if (body && Array.isArray(body.errors) && body.errors.length) {
              this.log('flush succeeded but returned errors', body.errors);
            }
      // Success: remove sent
      this.queue.splice(0, batch.length);
      this.persistQueue();
    } catch (err) {
      this.log('flush failed', err);
      // Mark attempts
  this.queue.forEach((r,i) => { if (i < this.cfg.maxBatchSize) r.attempts++; });
      // Optional: drop after N attempts (not implemented yet) to avoid poison queue
    } finally {
      this.flushInFlight = false;
    }
  }

  private installLifecycleHandlers() {
    this.pageVisibilityHandler = () => {
      if (document.visibilityState === 'hidden') {
        // Do not emit scene_page_leave on visibilitychange (tab switch) — let backend infer
        this.flushWithBeacon();
      }
    };
    document.addEventListener('visibilitychange', this.pageVisibilityHandler);

    this.beforeUnloadHandler = () => {
      // Do not emit scene_page_leave on unload; include last_entity in session_end metadata instead
      this.trackInternal('session_end','session','session',{ ended_at: Date.now(), last_entity: this.lastEntityView });
      this.flushWithBeacon();
    };
    window.addEventListener('beforeunload', this.beforeUnloadHandler);
    // pagehide is more reliable on mobile/Safari; treat like unload
    window.addEventListener('pagehide', () => {
      // Do not emit scene_page_leave on pagehide; let backend infer finalization
      this.flushWithBeacon();
    });
  }

  private flushWithBeacon() {
    if (!this.queue.length) return;
    try {
    const payload = this.queue.map(r => r.event);
    const url = this.cfg.endpoint + this.cfg.batchPath;
    // Always send as array (single element list if only one)
    const body = JSON.stringify(payload.length > 1 ? payload : [payload[0]]);
    const blob = new Blob([body], { type: 'application/json' });
      let ok = false;
      try {
        ok = (navigator as any).sendBeacon ? (navigator as any).sendBeacon(url, blob) : false;
      } catch (e) { ok = false; }
      if (!ok) {
        // Fallback: try fetch with keepalive (best-effort)
        try {
          const f = fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true });
          f.then(res => { if (res && res.ok) { this.queue = []; this.persistQueue(); } }).catch(()=>{});
          // Note: can't reliably await in unload, but attempt to clear queue anyway
        } catch (e) {
          // ignore
        }
      } else {
        this.queue = [];
        this.persistQueue();
      }
    } catch (e) {
      // swallow
    }
  }

  // ------------------------- Scene Watch Helpers ---------------------------
  private captureSegment(force = false): boolean {
    const state = this.currentScene;
    if (!state || (!state.video && !state.player)) return false;
    if (state.lastPlayTs == null) return false;
    const now = Date.now();
    const elapsed = (now - state.lastPlayTs) / 1000; // seconds
    if (elapsed < 0.5 && !force) return false; // ignore micro pauses
    const snapshot = this.getPlaybackSnapshot(state);
    const end = snapshot.position ?? (state.video && isFinite(state.video.currentTime) ? state.video.currentTime : undefined);
    if (end == null) return false;
    const start = Math.max(0, end - elapsed);
    this.mergeSegment(state, { start, end });
    state.lastPlayTs = undefined;
    state.lastPosition = end;
    if (snapshot.duration && (!state.duration || Math.abs(state.duration - snapshot.duration) > 0.5)) {
      state.duration = snapshot.duration;
    }
    return true;
  }

  private mergeSegment(state: SceneWatchState, seg: WatchSegment) {
    if (seg.end <= seg.start) return;
    // Merge overlapping/adjacent (within 1s) segments
    const margin = 1.0;
    let inserted = false;
    for (let i=0;i<state.segments.length;i++) {
      const s = state.segments[i];
      if (seg.start <= s.end + margin && seg.end >= s.start - margin) {
        s.start = Math.min(s.start, seg.start);
        s.end = Math.max(s.end, seg.end);
        inserted = true;
        // Possible chain merge
        this.normalizeSegments(state);
        break;
      }
    }
    if (!inserted) { state.segments.push(seg); this.normalizeSegments(state); }
  }

  private normalizeSegments(state: SceneWatchState) {
    state.segments.sort((a,b)=>a.start-b.start);
    const merged: WatchSegment[] = [];
    for (const s of state.segments) {
      if (!merged.length) { merged.push({...s}); continue; }
      const last = merged[merged.length-1];
      if (s.start <= last.end + 1.0) {
        last.end = Math.max(last.end, s.end);
      } else {
        merged.push({...s});
      }
    }
    state.segments = merged;
  }

  private totalWatched(state: SceneWatchState): number {
    return state.segments.reduce((acc,s) => acc + (s.end - s.start), 0);
  }

  private maybeEmitProgress() {
    const state = this.currentScene;
    if (!state || (!state.video && !state.player)) return;
    const now = Date.now();
    if (state.lastProgressEmit && now - state.lastProgressEmit < this.cfg.progressThrottleMs) return;
    if (this.isPlayerPaused(state) || this.isPlayerSeeking(state)) return;
    state.lastProgressEmit = now;
    const snapshot = this.getPlaybackSnapshot(state);
    const position = snapshot.position ?? state.lastPosition;
    const duration = snapshot.duration ?? state.duration;
    if (position === undefined) return;
    this.trackInternal('scene_watch_progress','scene',state.sceneId,{
      position,
      percent: snapshot.percent,
      duration: state.duration ?? duration
    });
    state.lastPosition = position;
  }

  

  // --------------------------- Queue Persistence ---------------------------
  private persistQueue() {
    try { localStorage.setItem(this.cfg.localStorageKey, JSON.stringify(this.queue)); } catch {}
  }
  private restoreQueue() {
    try {
      const raw = localStorage.getItem(this.cfg.localStorageKey);
      if (!raw) { this.queue = []; return; }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) { this.queue = []; return; }
      const restored: StoredQueueRecord[] = [];
      for (const rec of parsed) {
        if (!rec || typeof rec !== 'object') continue;
        const attempts = (rec as StoredQueueRecord).attempts ?? 0;
        const storedEvent = (rec as StoredQueueRecord).event as InteractionEvent | undefined;
        if (!storedEvent) continue;
        try {
          const normalizedEvent: InteractionEvent = {
            ...storedEvent,
            entity_id: normalizeEntityIdForEvent(storedEvent.entity_type, storedEvent.entity_id)
          };
          restored.push({ event: normalizedEvent, attempts });
        } catch {
          // drop invalid legacy record silently
        }
      }
      this.queue = restored;
    } catch {
      this.queue = [];
    }
  }

  // ------------------------------- Utilities -------------------------------
  private log(msg: string, data?: any) { if (this.cfg.debug) { try { console.log('[InteractionTracker]', msg, data||''); } catch {} } }
}

// ---------------------------- Global Exposure ------------------------------
;(function expose(){
  const inst = InteractionTracker.instance; // initialize immediately
  (window as any).stashAIInteractionTracker = inst;
  (window as any).trackInteractionEvent = function(type: InteractionEventType, entityType: InteractionEvent['entity_type'], entityId: string | number, metadata?: any){
    // Limited manual escape hatch
    (inst as any).trackInternal?.(type, entityType, entityId, metadata);
  };
  (window as any).trackLibrarySearch = function(library: string, query?: string, filters?: any){
    (inst as any).trackLibrarySearch?.(library, query, filters);
  };
  // simple global helpers for toggling debug console output
  (window as any).enableInteractionDebug = () => inst.enableDebug();
  (window as any).disableInteractionDebug = () => inst.disableDebug();
})();

// =============================================================================
// Usage (examples):
//  const tracker = (window as any).stashAIInteractionTracker as InteractionTracker;
//  tracker.trackSceneView('123', { title: 'Scene Title' });
//  tracker.instrumentSceneVideo('123', document.querySelector('video'));
//  tracker.trackImageView('55');
//  // Enable verbose console logging of every event:
//  (window as any).enableInteractionDebug();
//  // Disable again:
//  (window as any).disableInteractionDebug();
// =============================================================================
