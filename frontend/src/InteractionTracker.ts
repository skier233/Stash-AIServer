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
  | 'scene_watch_start'
  | 'scene_watch_pause'
  | 'scene_seek'
  | 'scene_watch_progress'
  | 'scene_watch_complete'
  | 'image_view'
  | 'gallery_view';

export interface InteractionEvent<TMeta = any> {
  id: string;                 // unique client event id
  session_id: string;         // session scope
  client_id?: string;         // persistent client identifier (localStorage)
  ts: string;                 // ISO timestamp
  type: InteractionEventType; // event type
  entity_type: 'scene' | 'image' | 'gallery' | 'session';
  entity_id: string;          // numeric id as string or 'session'
  metadata?: TMeta;           // structured extras
  page_url: string;
  user_agent: string;
  viewport?: { w: number; h: number };
  // versioning for evolution
  schema_version: 1;
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

// Derive backend base similarly to AIButton.tsx so we hit the correct host/port.
function _resolveBackendBase(): string {
  try {
    const explicit = (window as any).AI_BACKEND_URL as string | undefined;
    if (explicit) return explicit.replace(/\/$/, '');
    let origin = (location && location.origin) || '';
    if (origin) {
      try { const u = new URL(origin); if (u.port === '3000') { u.port = '8000'; origin = u.toString(); } } catch { /* ignore */ }
    }
    if (!origin) origin = 'http://localhost:8000';
    return origin.replace(/\/$/, '');
  } catch { return 'http://localhost:8000'; }
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
    const base: Required<InteractionTrackerConfig> = {
  endpoint: resolved,
      batchPath: '/api/v1/interactions/sync',
      sendIntervalMs: 5000,
      maxBatchSize: 40,
  progressThrottleMs: 5000,
  immediateTypes: ['session_start','scene_watch_complete'],
      localStorageKey: 'ai_overhaul_event_queue',
      maxQueueLength: 1000,
      debug: true, // enable verbose logging by default for initial verification
      autoDetect: true,
      integratePageContext: true,
      videoAutoInstrument: true,
      enabled: true
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
        this.log('pageContext not ready, retrying...');
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
        this.trackSceneView(ctx.entityId, { from: 'pageContext' });
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
        this.log('late video instrumentation via pageContext');
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
    this.trackInternal('scene_view','scene',sceneId,{ title: opts?.title, from: opts?.from, last_viewed_entity: this.lastEntityView });
    this.lastEntityView = { type: 'scene', id: sceneId, ts: Date.now() };
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
      if (!state.duration && video.duration && isFinite(video.duration)) state.duration = video.duration;
      state.lastPlayTs = Date.now();
      this.trackInternal('scene_watch_start','scene',sceneId,{ position: video.currentTime });
    };
    const onPause = () => {
      const added = this.captureSegment();
      // Emit an explicit pause event (position + cumulative watched)
      try {
        const total = this.currentScene ? this.totalWatched(this.currentScene) : undefined;
        this.trackInternal('scene_watch_pause','scene',sceneId,{ position: video.currentTime, total_watched: total, segment_added: added });
      } catch {}
    };
  const onEnded = () => { this.captureSegment(true); this.trackInternal('scene_watch_complete','scene',sceneId,{ duration: state.duration, total_watched: this.totalWatched(state), segments: state.segments }); state.completed = true; };
    const onTimeUpdate = () => { this.maybeEmitProgress(); };
    const onSeeked = (e: Event) => {
      const prev = state.lastPosition ?? 0;
      const next = video.currentTime;
      if (Math.abs(next - prev) > 1.0) {
        this.trackInternal('scene_seek','scene',sceneId,{ from: prev, to: next, delta: next - prev, direction: next > prev ? 'forward':'backward' });
        state.lastPosition = next;
      }
    };
    const onLoaded = () => { if (video.duration && isFinite(video.duration)) state.duration = video.duration; };

    video.addEventListener('play', onPlay);
    video.addEventListener('pause', onPause);
    video.addEventListener('ended', onEnded);
    video.addEventListener('timeupdate', onTimeUpdate);
    video.addEventListener('seeked', onSeeked);
    video.addEventListener('loadedmetadata', onLoaded);

    // Store cleanup on element for manual removal if needed
    (video as any)._aiInteractionCleanup = () => {
      video.removeEventListener('play', onPlay);
      video.removeEventListener('pause', onPause);
      video.removeEventListener('ended', onEnded);
      video.removeEventListener('timeupdate', onTimeUpdate);
      video.removeEventListener('seeked', onSeeked);
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

  // --------------------------- Internal Helpers ----------------------------
  private trackInternal(type: InteractionEventType, entityType: InteractionEvent['entity_type'], entityId: string, metadata?: any) {
    const ev: InteractionEvent = {
      id: 'evt_' + Date.now() + '_' + Math.random().toString(36).slice(2,6),
      session_id: this.sessionId,
      // attach stable client id for session merging on backend
      client_id: this.clientId,
      ts: new Date().toISOString(),
      type,
      entity_type: entityType,
      entity_id: entityId,
      metadata,
      page_url: window.location.href,
      user_agent: navigator.userAgent,
      viewport: { w: window.innerWidth, h: window.innerHeight },
      schema_version: 1
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
    if (this.queue.length > this.cfg.maxQueueLength) this.queue.splice(0, this.queue.length - this.cfg.maxQueueLength);
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
      const isBatch = payload.length > 1;
      // Prefer navigator.sendBeacon for unload scenarios (handled separately)
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
        this.flushWithBeacon();
      }
    };
    document.addEventListener('visibilitychange', this.pageVisibilityHandler);

    this.beforeUnloadHandler = () => {
      this.trackInternal('session_end','session','session',{ ended_at: Date.now(), last_entity: this.lastEntityView });
      this.flushWithBeacon();
    };
    window.addEventListener('beforeunload', this.beforeUnloadHandler);
    // pagehide is more reliable on mobile/Safari; treat like unload
    window.addEventListener('pagehide', () => {
      this.flushWithBeacon();
    });
  }

  private flushWithBeacon() {
    if (!this.queue.length) return;
    try {
      const payload = this.queue.map(r => r.event);
  const url = this.cfg.endpoint + this.cfg.batchPath;
  // Always send an array payload to match /sync expectation
  // Explicit: always send an array; wrap single-event into a 1-element array
  const blob = new Blob([JSON.stringify(payload.length > 1 ? payload : [payload[0]])], { type: 'application/json' });
      const ok = (navigator as any).sendBeacon ? (navigator as any).sendBeacon(url, blob) : false;
      if (ok) {
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
    const duration = state.video.duration || state.duration;
    const percent = duration ? (position / duration) * 100 : undefined;
    this.trackInternal('scene_watch_progress','scene',state.sceneId,{ position, percent });
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
