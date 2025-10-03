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
  entity_id: string;
  metadata?: TMeta; // Arbitrary structured metadata; put page_url here if needed.
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
  private lastEntityView: { type: string; id: string; ts: number } | null = null;
  private initialized = false;
  private lastScenePageEntered: string | null = null; // track current scene page for leave events
  private lastLibrarySearchSignature: string | null = null; // dedupe library_search emissions

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
  private waitingVideoObserver: MutationObserver | null = null;

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
    // Defer until DOM ready
    const run = () => {
      try {
        // Heuristic: look for /scenes/<id> or ?sceneId=123 in URL
        const url = window.location.href;
        let sceneId: string | null = null;
        const sceneMatch = url.match(/scenes\/(\d+)/i);
        if (sceneMatch) sceneId = sceneMatch[1];
        const params = new URLSearchParams(window.location.search);
        if (!sceneId && params.get('sceneId')) sceneId = params.get('sceneId');
        if (sceneId) {
          this.log('auto-detect scene id', sceneId);
          this.trackSceneView(sceneId);
          // Attempt to instrument first <video>
          const video = document.querySelector('video') as HTMLVideoElement | null;
          if (video) {
            this.log('auto-instrument video element');
            this.instrumentSceneVideo(sceneId, video);
          } else {
            // Observe for late-loaded video
            const mo = new MutationObserver((muts, obs) => {
              const v = document.querySelector('video');
              if (v) {
                this.log('auto-instrument late video element');
                this.instrumentSceneVideo(sceneId!, v as HTMLVideoElement);
                obs.disconnect();
              }
            });
            mo.observe(document.documentElement, { childList: true, subtree: true });
            setTimeout(()=>mo.disconnect(), 15000); // safety
          }
        } else {
          this.log('auto-detect: no scene id pattern matched');
        }
      } catch (e) {
        this.log('auto-detect failed', e);
      }
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
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
      // Fire immediately for current context
      try { this.handlePageContext(api.get()); } catch {}
    };
    attach();
  }

  private handlePageContext(ctx: any) {
    if (!ctx) return;
    // Only emit view events for detail pages we care about
    if (!ctx.isDetailView || !ctx.entityId) return;
    const key = ctx.page + ':' + ctx.entityId;
    if (key === this.lastDetailKey) return; // duplicate
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
    // If already instrumented for this scene, skip
    if (this.currentScene && this.currentScene.sceneId === sceneId && this.currentScene.video) return;
    // Try immediate
    const video = document.querySelector('video') as HTMLVideoElement | null;
    if (video) { this.instrumentSceneVideo(sceneId, video); return; }
    // Observe for late video
    if (this.waitingVideoObserver) { this.waitingVideoObserver.disconnect(); this.waitingVideoObserver = null; }
    this.waitingVideoObserver = new MutationObserver(() => {
      const v = document.querySelector('video') as HTMLVideoElement | null;
      if (v) {
        this.log('late video instrumentation via PageContext');
        this.instrumentSceneVideo(sceneId, v);
        this.waitingVideoObserver?.disconnect();
        this.waitingVideoObserver = null;
      }
    });
    this.waitingVideoObserver.observe(document.documentElement, { childList: true, subtree: true });
    setTimeout(() => { this.waitingVideoObserver?.disconnect(); this.waitingVideoObserver = null; }, 15000);
  }

  // ---------------------------- Public API ---------------------------------
  public trackSceneView(sceneId: string, opts?: { title?: string; from?: string }) {
    // Emit scene_page_leave for previous scene if different
    if (this.lastScenePageEntered && this.lastScenePageEntered !== sceneId) {
      this.trackInternal('scene_page_leave', 'scene', this.lastScenePageEntered, { next_scene: sceneId });
    }
    
    this.trackInternal('scene_view','scene',sceneId,{ title: opts?.title, from: opts?.from, last_viewed_entity: this.lastEntityView });
    this.lastEntityView = { type: 'scene', id: sceneId, ts: Date.now() };
    // Also emit scene_page_enter event to track page visit timing
    this.trackInternal('scene_page_enter', 'scene', sceneId, { title: opts?.title, from: opts?.from });
    this.lastScenePageEntered = sceneId;
  }

  public instrumentSceneVideo(sceneId: string, video: HTMLVideoElement) {
    // Reset existing state if switching scenes
    if (this.currentScene && this.currentScene.sceneId !== sceneId) {
      // switching scenes: clear previous state without emitting summary
      this.currentScene = undefined;
    }
    const state: SceneWatchState = this.currentScene || { sceneId, duration: null, segments: [], video };
    state.video = video;
    this.currentScene = state;

    const onPlay = () => {
      // Refresh duration if metadata now available
      if (video.duration && isFinite(video.duration)) {
        if (!state.duration || Math.abs((state.duration ?? 0) - video.duration) > 0.5) {
          state.duration = video.duration;
        }
      }
      state.lastPlayTs = Date.now();
      this.trackInternal('scene_watch_start','scene',sceneId,{
        position: video.currentTime,
        duration: state.duration ?? (isFinite(video.duration) ? video.duration : undefined)
      });
    };
    const onPause = () => {
      const added = this.captureSegment();
      // Refresh duration if newly known
      if (video.duration && isFinite(video.duration)) {
          if (!state.duration || Math.abs((state.duration ?? 0) - video.duration) > 0.5) state.duration = video.duration;
      }
      try {
        const total = this.currentScene ? this.totalWatched(this.currentScene) : undefined;
        this.trackInternal('scene_watch_pause','scene',sceneId,{
          position: video.currentTime,
          total_watched: total,
            duration: state.duration ?? (isFinite(video.duration) ? video.duration : undefined),
          segment_added: added
        });
      } catch {}
    };
    const onEnded = () => {
      this.captureSegment(true);
      if (video.duration && isFinite(video.duration)) {
        state.duration = video.duration;
      }
      this.trackInternal('scene_watch_complete','scene',sceneId,{
        duration: state.duration ?? (isFinite(video.duration) ? video.duration : undefined),
        total_watched: this.totalWatched(state),
        segments: state.segments
      });
      state.completed = true;
    };
    const onTimeUpdate = () => {
      const current = video.currentTime;
      const prev = state.lastPosition;
      if (prev != null) {
        const delta = current - prev;
        // Consider this a seek if jump magnitude >1s and not just normal progression
        // Typical timeupdate cadence ~0.25s or less; so a >1s jump is almost certainly a seek
        if (Math.abs(delta) > 1.0) {
          this.trackInternal('scene_seek','scene',sceneId,{ from: prev, to: current, delta, direction: delta > 0 ? 'forward':'backward', via: 'delta-detect' });
          if (this.cfg.debug) this.log('seek detected (delta)', { from: prev, to: current, delta });
        }
      }
      state.lastPosition = current;
      // Capture duration mid-playback if it becomes available
      if (!state.duration && video.duration && isFinite(video.duration)) state.duration = video.duration;
      this.maybeEmitProgress();
    };
    const onLoaded = () => { if (video.duration && isFinite(video.duration)) state.duration = video.duration; };

    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    video.addEventListener('ended', onEnded);
    video.addEventListener('timeupdate', onTimeUpdate);
    video.addEventListener('loadedmetadata', onLoaded);

    // Store cleanup on element for manual removal if needed
    (video as any)._aiInteractionCleanup = () => {
      video.removeEventListener('play', onPlay);
      video.removeEventListener('pause', onPause);
      video.removeEventListener('ended', onEnded);
      video.removeEventListener('timeupdate', onTimeUpdate);
      video.removeEventListener('loadedmetadata', onLoaded);
    };
  }

  public trackImageView(imageId: string, opts?: { title?: string }) {
    this.trackInternal('image_view','image',imageId,{ title: opts?.title, last_viewed_entity: this.lastEntityView });
    this.lastEntityView = { type: 'image', id: imageId, ts: Date.now() };
  }

  public trackGalleryView(galleryId: string, opts?: { title?: string }) {
    this.trackInternal('gallery_view','gallery',galleryId,{ title: opts?.title, last_viewed_entity: this.lastEntityView });
    this.lastEntityView = { type: 'gallery', id: galleryId, ts: Date.now() };
  }

  /**
   * Persist a library search or filter action. library should be 'scenes' or 'images'.
   */
  public trackLibrarySearch(library: 'scenes'|'images'|'galleries'|'performers'|'tags', query?: string, filters?: any) {
    const meta: any = { query: query ?? null, filters: filters ?? null };
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
  private trackInternal(type: InteractionEventType, entityType: InteractionEvent['entity_type'], entityId: string, metadata?: any) {
    const ev: InteractionEvent = {
      id: 'evt_' + Date.now() + '_' + Math.random().toString(36).slice(2,6),
      session_id: this.sessionId,
      client_id: this.clientId,
      ts: new Date().toISOString(),
      type,
      entity_type: entityType,
      entity_id: entityId,
      metadata
    };

    // Queue
    if (this.cfg.enabled) this.enqueue(ev); else return;
    // Immediate flush logic
  if (this.cfg.immediateTypes.includes(type)) this.flushQueue();
    // Always provide a clear structured console output when debug is on
    this.log('event captured', ev);
    // Additionally emit a more visible console.info for quick manual QA when debug is true
    if (this.cfg.debug && (console as any).info) {
      (console as any).info('%c[InteractionTracker] %c'+type+'%c -> '+entityType+':'+entityId, 'color:#888', 'color:#0A7;', 'color:#555', ev);
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
    if (!state || !state.video) return false;
    if (state.lastPlayTs == null) return false;
    const now = Date.now();
    const elapsed = (now - state.lastPlayTs) / 1000; // seconds
    if (elapsed < 0.5 && !force) return false; // ignore micro pauses
    const end = state.video.currentTime;
    const start = Math.max(0, end - elapsed);
    this.mergeSegment(state, { start, end });
    state.lastPlayTs = undefined;
    state.lastPosition = end;
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
    if (!state || !state.video) return;
    const now = Date.now();
    if (state.lastProgressEmit && now - state.lastProgressEmit < this.cfg.progressThrottleMs) return;
    if (state.video.paused || state.video.seeking) return;
    state.lastProgressEmit = now;
    const position = state.video.currentTime;
    const duration = (state.video.duration && isFinite(state.video.duration)) ? state.video.duration : state.duration;
    if (duration && (!state.duration || Math.abs(state.duration - duration) > 0.5)) {
      state.duration = duration;
    }
    const percent = duration ? (position / duration) * 100 : undefined;
    this.trackInternal('scene_watch_progress','scene',state.sceneId,{
      position,
      percent,
      duration: state.duration ?? duration
    });
    state.lastPosition = position;
  }

  

  // --------------------------- Queue Persistence ---------------------------
  private persistQueue() {
    try { localStorage.setItem(this.cfg.localStorageKey, JSON.stringify(this.queue)); } catch {}
  }
  private restoreQueue() {
    try { const raw = localStorage.getItem(this.cfg.localStorageKey); if (raw) this.queue = JSON.parse(raw); } catch { this.queue = []; }
  }

  // ------------------------------- Utilities -------------------------------
  private log(msg: string, data?: any) { if (this.cfg.debug) { try { console.log('[InteractionTracker]', msg, data||''); } catch {} } }
}

// ---------------------------- Global Exposure ------------------------------
;(function expose(){
  const inst = InteractionTracker.instance; // initialize immediately
  (window as any).stashAIInteractionTracker = inst;
  (window as any).trackInteractionEvent = function(type: InteractionEventType, entityType: InteractionEvent['entity_type'], entityId: string, metadata?: any){
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
