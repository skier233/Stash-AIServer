// =============================================================================
// X-Ray Overlay — AI Debug Viewer integrated into the Stash Scene Player
//
// Injects a toggle button into the video player's control bar.  When active,
// draws face bounding boxes on a canvas over the video and shows a small
// heads-up panel with face/tag info.  Frame capture is available when paused.
//
// Scene ID is extracted from the URL — no dependency on PluginApi scene props.
// =============================================================================
(function () {
  const g: any = window as any;
  const PluginApi = g.PluginApi;
  if (!PluginApi) { console.warn('[XRay] PluginApi not ready'); return; }
  const React = PluginApi.React;
  const { useState, useEffect, useRef, useCallback, useMemo } = React;
  const ReactDOM = g.ReactDOM || PluginApi.libraries?.ReactDOM;

  const debug = !!g.AIDebug;
  const dlog = (...a: any[]) => { if (debug) console.log('[XRay]', ...a); };

  // ── Types ────────────────────────────────────────────────────────────

  interface FaceDetection {
    embedding_id: number;
    track_id: number;
    bbox: number[];
    timestamp_s: number | null;
    score: number;
    is_exemplar: boolean;
    cluster_id?: number;
    performer_id?: number;
    performer_label?: string;
    cluster_status?: string;
  }

  interface Track {
    track_id: number;
    bbox: number[];
    score: number;
    start_s: number | null;
    end_s: number | null;
    keyframes: any;
    cluster_id?: number;
    performer_id?: number;
    performer_label?: string;
    cluster_status?: string;
  }

  interface TagSpanEntry {
    tag_id: number;
    tag_name: string;
    category: string;
    spans: Array<{ start: number; end: number; confidence: number }>;
  }

  interface XRayData {
    scene_id: number;
    face_detections: FaceDetection[];
    tracks: Track[];
    tag_timespans: Record<string, TagSpanEntry[]>;
  }

  interface VisibleFace {
    track_id: number;
    bbox: number[];
    score: number;
    performer_id?: number;
    performer_label?: string;
    cluster_id?: number;
    cluster_status?: string;
  }

  // ── API helpers ──────────────────────────────────────────────────────

  function getApiBase(): string {
    if (g.AIBackendBaseURL) return g.AIBackendBaseURL;
    if (g.AIDefaultBackendBase?.get) {
      const val = g.AIDefaultBackendBase.get();
      if (val) return val;
    }
    try {
      const stored = sessionStorage.getItem('ai_backend_base_url');
      if (stored) return stored;
    } catch {}
    return 'http://localhost:4153';
  }

  function apiHeaders(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    const key = g.AISharedApiKeyHelper?.get?.() || g.AI_SHARED_API_KEY || '';
    if (key) h['x-ai-api-key'] = key;
    return h;
  }

  // ── Scene ID from URL ────────────────────────────────────────────────

  function getSceneIdFromUrl(): number | null {
    const m = location.pathname.match(/\/scenes\/(\d+)/);
    return m ? parseInt(m[1], 10) : null;
  }

  // ── Utilities ────────────────────────────────────────────────────────

  function formatTime(s: number): string {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  const COL = {
    performer: '#66bb6a',
    unknown: '#ffa726',
    ignored: '#888',
  };

  function faceColor(f: VisibleFace): string {
    if (f.cluster_status === 'ignored') return COL.ignored;
    if (f.performer_id) return COL.performer;
    return COL.unknown;
  }
  function faceLabel(f: VisibleFace): string {
    if (f.performer_label) return f.performer_label;
    if (f.cluster_id) return `#${f.cluster_id}`;
    return '?';
  }

  // ── Keyframe bbox interpolation ───────────────────────────────────

  function interpolateBBox(keyframes: Array<{t: number; bbox: number[]}>, time: number): number[] | null {
    if (!keyframes || keyframes.length === 0) return null;
    if (keyframes.length === 1) return keyframes[0].bbox;

    // Before first keyframe
    if (time <= keyframes[0].t) return keyframes[0].bbox;
    // After last keyframe
    if (time >= keyframes[keyframes.length - 1].t) return keyframes[keyframes.length - 1].bbox;

    // Find surrounding keyframes
    for (let i = 0; i < keyframes.length - 1; i++) {
      const a = keyframes[i], b = keyframes[i + 1];
      if (time >= a.t && time <= b.t) {
        const span = b.t - a.t;
        if (span <= 0) return a.bbox;
        const frac = (time - a.t) / span;
        return [
          a.bbox[0] + (b.bbox[0] - a.bbox[0]) * frac,
          a.bbox[1] + (b.bbox[1] - a.bbox[1]) * frac,
          a.bbox[2] + (b.bbox[2] - a.bbox[2]) * frac,
          a.bbox[3] + (b.bbox[3] - a.bbox[3]) * frac,
        ];
      }
    }
    return keyframes[keyframes.length - 1].bbox;
  }

  // ── Track-based face visibility ──────────────────────────────────────

  function getVisibleFaces(tracks: Track[], dets: FaceDetection[], time: number): VisibleFace[] {
    const validTracks = (tracks || []).filter(t => t.start_s != null && t.end_s != null && t.bbox?.length === 4);

    if (validTracks.length > 0) {
      const vis: VisibleFace[] = [];
      for (const t of validTracks) {
        if (time < (t.start_s as number) - 0.5 || time > (t.end_s as number) + 0.5) continue;

        // Interpolate bbox from keyframes when available; fall back to track bbox
        let bbox = t.bbox;
        const kf = t.keyframes;
        if (Array.isArray(kf) && kf.length > 0) {
          const interp = interpolateBBox(kf, time);
          if (interp) bbox = interp;
        }

        vis.push({ track_id: t.track_id, bbox, score: t.score, performer_id: t.performer_id, performer_label: t.performer_label, cluster_id: t.cluster_id, cluster_status: t.cluster_status });
      }
      return vis;
    }

    // Fallback: raw detections with wide tolerance
    return dets
      .filter(d => d.timestamp_s != null && Math.abs((d.timestamp_s as number) - time) <= 5 && d.bbox?.length === 4)
      .sort((a, b) => Math.abs((a.timestamp_s as number) - time) - Math.abs((b.timestamp_s as number) - time))
      .slice(0, 10)
      .map(d => ({ track_id: d.track_id, bbox: d.bbox, score: d.score, performer_id: d.performer_id, performer_label: d.performer_label, cluster_id: d.cluster_id, cluster_status: d.cluster_status }));
  }

  // ── Active tags at timestamp ─────────────────────────────────────────

  function getActiveTagsAt(ts: Record<string, TagSpanEntry[]>, time: number) {
    const active: Array<{ tag_name: string; category: string; confidence: number }> = [];
    if (!ts) return active;
    for (const [cat, entries] of Object.entries(ts)) {
      for (const e of entries) {
        let best = -1;
        for (const s of e.spans) { if (time >= s.start && time <= s.end) best = Math.max(best, s.confidence ?? 1.0); }
        if (best >= 0) active.push({ tag_name: e.tag_name, category: cat, confidence: best });
      }
    }
    active.sort((a, b) => a.category.localeCompare(b.category) || a.tag_name.localeCompare(b.tag_name));
    return active;
  }

  // ── DOM finders ──────────────────────────────────────────────────────

  function findVideo(): HTMLVideoElement | null {
    return document.querySelector('.scene-player-container video, video.vjs-tech, .VideoPlayer video, video') as HTMLVideoElement | null;
  }
  function findVideoContainer(): HTMLElement | null {
    return document.querySelector('.scene-player-container, .video-js, .VideoPlayer') as HTMLElement | null;
  }
  function findControlBar(): HTMLElement | null {
    return document.querySelector('.vjs-control-bar') as HTMLElement | null;
  }

  // ── Draw bboxes on canvas ────────────────────────────────────────────

  function drawBBoxes(
    canvas: HTMLCanvasElement,
    videoEl: HTMLVideoElement,
    faces: VisibleFace[],
  ) {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const rect = videoEl.getBoundingClientRect();
    canvas.width = rect.width;
    canvas.height = rect.height;
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const vw = videoEl.videoWidth || 1;
    const vh = videoEl.videoHeight || 1;

    for (const face of faces) {
      const [x1, y1, x2, y2] = face.bbox;
      const isNorm = x1 <= 1 && y1 <= 1 && x2 <= 1.01 && y2 <= 1.01;
      const sx = isNorm ? canvas.width : canvas.width / vw;
      const sy = isNorm ? canvas.height : canvas.height / vh;
      const px = x1 * sx, py = y1 * sy, pw = (x2 - x1) * sx, ph = (y2 - y1) * sy;

      const col = faceColor(face);
      ctx.strokeStyle = col;
      ctx.lineWidth = 2;
      ctx.strokeRect(px, py, pw, ph);

      // Label
      const label = faceLabel(face);
      ctx.font = 'bold 11px system-ui, sans-serif';
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = 'rgba(0,0,0,0.7)';
      ctx.fillRect(px, py - 18, tw + 8, 18);
      ctx.fillStyle = col;
      ctx.fillText(label, px + 4, py - 5);

      // Score
      ctx.font = '10px system-ui, sans-serif';
      const st = face.score.toFixed(2);
      const stw = ctx.measureText(st).width;
      ctx.fillStyle = 'rgba(0,0,0,0.55)';
      ctx.fillRect(px, py + ph, stw + 6, 14);
      ctx.fillStyle = '#bbb';
      ctx.fillText(st, px + 3, py + ph + 11);
    }
  }

  // ====================================================================
  // XRayController — imperative controller managing the overlay lifecycle
  // ====================================================================

  class XRayController {
    private active = false;
    private sceneId: number | null = null;
    private data: XRayData | null = null;
    private loading = false;

    private canvas: HTMLCanvasElement | null = null;
    private hudRoot: HTMLDivElement | null = null;
    private toggleBtn: HTMLButtonElement | null = null;
    private rafId = 0;
    private resizeObs: ResizeObserver | null = null;
    private lastTime = -1;
    private lastPaused = false;

    // ── toggle button injection ────────────────────────────────────────

    injectButton() {
      if (this.toggleBtn && document.body.contains(this.toggleBtn)) return;

      const bar = findControlBar();
      if (!bar) return;

      // Find a good insertion point — after the fullscreen button or at the end
      const btn = document.createElement('button');
      btn.className = 'vjs-control vjs-button xray-toggle-btn';
      btn.title = 'Toggle X-Ray overlay';
      btn.setAttribute('aria-label', 'Toggle X-Ray overlay');
      btn.innerHTML = `<span style="font-size:14px;line-height:1;pointer-events:none">\uD83D\uDD0D</span>`;
      btn.style.cssText = 'cursor:pointer;background:none;border:none;color:#fff;padding:0 6px;display:flex;align-items:center;justify-content:center;opacity:0.7;transition:opacity .2s;';
      btn.addEventListener('mouseenter', () => { btn.style.opacity = '1'; });
      btn.addEventListener('mouseleave', () => { btn.style.opacity = this.active ? '1' : '0.7'; });
      btn.addEventListener('click', () => this.toggle());

      bar.appendChild(btn);
      this.toggleBtn = btn;
      dlog('Injected X-Ray button into control bar');
    }

    // ── main toggle ────────────────────────────────────────────────────

    toggle() {
      this.active = !this.active;
      if (this.toggleBtn) {
        this.toggleBtn.style.opacity = this.active ? '1' : '0.7';
        this.toggleBtn.style.textShadow = this.active ? '0 0 6px #4fc3f7' : 'none';
      }
      if (this.active) {
        this.sceneId = getSceneIdFromUrl();
        if (this.sceneId) this.loadData();
        this.startLoop();
      } else {
        this.stopLoop();
        this.removeOverlay();
      }
    }

    // ── data loading ───────────────────────────────────────────────────

    private async loadData() {
      if (!this.sceneId || this.loading) return;
      this.loading = true;
      this.updateHud('loading');
      try {
        const res = await fetch(
          `${getApiBase()}/api/v1/plugins/skier_aitagging/xray/scenes/${this.sceneId}/data`,
          { headers: apiHeaders() },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this.data = await res.json();
        dlog('Loaded', this.data!.face_detections.length, 'dets,', this.data!.tracks.length, 'tracks');
      } catch (e: any) {
        console.error('[XRay] Data load failed:', e);
        this.updateHud('error', e.message);
        this.data = null;
      }
      this.loading = false;
    }

    // ── render loop ────────────────────────────────────────────────────

    private startLoop() {
      const tick = () => {
        if (!this.active) return;
        const vid = findVideo();
        if (vid && this.data) {
          const t = vid.currentTime;
          const pauseChanged = vid.paused !== this.lastPaused;
          // Redraw if time changed significantly (50ms) or pause state toggled
          if (Math.abs(t - this.lastTime) > 0.05 || pauseChanged) {
            this.lastTime = t;
            this.lastPaused = vid.paused;
            this.ensureCanvas(vid);
            const faces = getVisibleFaces(this.data.tracks, this.data.face_detections, t);
            const tags = getActiveTagsAt(this.data.tag_timespans, t);
            if (this.canvas) drawBBoxes(this.canvas, vid, faces);
            this.updateHud('active', undefined, { time: t, faces, tags, paused: vid.paused });
          }
        }
        this.rafId = requestAnimationFrame(tick);
      };
      this.rafId = requestAnimationFrame(tick);
    }

    private stopLoop() {
      cancelAnimationFrame(this.rafId);
      this.lastTime = -1;
      this.lastPaused = false;
    }

    // ── canvas management ──────────────────────────────────────────────

    private ensureCanvas(videoEl: HTMLVideoElement) {
      const container = findVideoContainer();
      if (!container) return;

      if (!container.style.position || container.style.position === 'static') {
        container.style.position = 'relative';
      }

      if (this.canvas && document.body.contains(this.canvas)) return;

      const c = document.createElement('canvas');
      c.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10;';
      container.appendChild(c);
      this.canvas = c;

      // Redraw on resize
      this.resizeObs = new ResizeObserver(() => {
        if (this.canvas && this.data) {
          const vid = findVideo();
          if (vid) {
            const faces = getVisibleFaces(this.data.tracks, this.data.face_detections, vid.currentTime);
            drawBBoxes(this.canvas, vid, faces);
          }
        }
      });
      this.resizeObs.observe(videoEl);
    }

    // ── HUD panel ──────────────────────────────────────────────────────

    private ensureHud() {
      if (this.hudRoot && document.body.contains(this.hudRoot)) return;

      const container = findVideoContainer();
      if (!container) return;

      const hud = document.createElement('div');
      hud.className = 'xray-hud';
      hud.style.cssText = [
        'position:absolute;top:8px;left:8px;z-index:12;',
        'background:rgba(0,0,0,0.78);border:1px solid rgba(255,255,255,0.12);',
        'border-radius:6px;padding:8px 10px;color:#ddd;font-size:11px;',
        'max-width:260px;max-height:50vh;overflow-y:auto;',
        'font-family:system-ui,-apple-system,sans-serif;',
        'pointer-events:auto;',
      ].join('');
      container.appendChild(hud);
      this.hudRoot = hud;
    }

    private updateHud(
      state: 'loading' | 'error' | 'active',
      errorMsg?: string,
      info?: { time: number; faces: VisibleFace[]; tags: Array<{ tag_name: string; category: string; confidence: number }>; paused: boolean },
    ) {
      this.ensureHud();
      if (!this.hudRoot) return;

      if (state === 'loading') {
        this.hudRoot.innerHTML = `<div style="color:#4fc3f7">Loading X-Ray data\u2026</div>`;
        return;
      }
      if (state === 'error') {
        this.hudRoot.innerHTML = `<div style="color:#ff6b6b">Error: ${this.escapeHtml(errorMsg || 'unknown')}</div>`;
        return;
      }
      if (!info) return;

      const { time, faces, tags, paused } = info;

      let html = `<div style="display:flex;justify-content:space-between;margin-bottom:4px">`;
      html += `<span style="color:#4fc3f7;font-weight:600">X-RAY</span>`;
      html += `<span style="color:#999">${formatTime(time)}</span></div>`;

      // Faces
      html += `<div style="font-weight:600;font-size:10px;color:#888;text-transform:uppercase;margin-top:4px">Faces (${faces.length})</div>`;
      if (faces.length === 0) {
        html += `<div style="color:#666;font-style:italic">None at this time</div>`;
      } else {
        for (const f of faces) {
          const col = faceColor(f);
          html += `<div style="display:flex;align-items:center;gap:4px;padding:1px 0">`;
          html += `<span style="width:6px;height:6px;border-radius:50%;background:${col};flex-shrink:0"></span>`;
          html += `<span>${this.escapeHtml(faceLabel(f))}</span>`;
          html += `<span style="margin-left:auto;color:#777;font-size:10px">${f.score.toFixed(2)}</span>`;
          html += `</div>`;
        }
      }

      // Tags
      html += `<div style="font-weight:600;font-size:10px;color:#888;text-transform:uppercase;margin-top:6px">Tags (${tags.length})</div>`;
      if (tags.length === 0) {
        html += `<div style="color:#666;font-style:italic">None at this time</div>`;
      } else {
        let lastCat = '';
        for (const t of tags) {
          if (t.category !== lastCat) {
            lastCat = t.category;
            html += `<div style="font-size:9px;color:#666;margin-top:3px">${this.escapeHtml(t.category)}</div>`;
          }
          html += `<div style="display:flex;justify-content:space-between;padding:1px 0">`;
          html += `<span>${this.escapeHtml(t.tag_name)}</span>`;
          html += `<span style="color:#777;font-size:10px">${(t.confidence * 100).toFixed(0)}%</span>`;
          html += `</div>`;
        }
      }

      // Capture button when paused
      if (paused && this.sceneId) {
        html += `<div style="margin-top:6px;text-align:center">`;
        html += `<button id="xray-capture-btn" style="padding:4px 12px;background:#0d6efd;color:#fff;border:none;border-radius:3px;font-size:11px;font-weight:600;cursor:pointer">\uD83D\uDCF7 Capture</button>`;
        html += `<span id="xray-capture-msg" style="display:block;font-size:10px;margin-top:2px;color:#777"></span>`;
        html += `</div>`;
      }

      // Stats footer
      if (this.data) {
        html += `<div style="margin-top:6px;font-size:9px;color:#555;border-top:1px solid rgba(255,255,255,0.06);padding-top:4px">`;
        html += `${this.data.face_detections.length} detections \u00B7 ${this.data.tracks.length} tracks`;
        html += `</div>`;
      }

      this.hudRoot.innerHTML = html;

      // Wire capture button
      const capBtn = this.hudRoot.querySelector('#xray-capture-btn') as HTMLButtonElement | null;
      if (capBtn) {
        capBtn.addEventListener('click', () => this.captureFrame(time, faces, tags));
      }
    }

    // ── frame capture ──────────────────────────────────────────────────

    private async captureFrame(
      time: number,
      faces: VisibleFace[],
      tags: Array<{ tag_name: string; category: string; confidence: number }>,
    ) {
      const msgEl = this.hudRoot?.querySelector('#xray-capture-msg') as HTMLElement | null;
      const btnEl = this.hudRoot?.querySelector('#xray-capture-btn') as HTMLButtonElement | null;
      if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Capturing\u2026'; }

      try {
        const res = await fetch(
          `${getApiBase()}/api/v1/plugins/skier_aitagging/xray/scenes/${this.sceneId}/capture-frame`,
          {
            method: 'POST',
            headers: apiHeaders(),
            body: JSON.stringify({
              timestamp_s: time,
              tags: tags.map(t => ({ tag_name: t.tag_name, category: t.category, confidence: t.confidence })),
              faces: faces.map(f => ({ bbox: f.bbox, score: f.score, performer_label: f.performer_label || null, cluster_id: f.cluster_id || null })),
            }),
          },
        );
        if (res.ok) {
          const d = await res.json();
          if (msgEl) {
            msgEl.style.color = '#66bb6a';
            const jsonKB = (d.json_size_bytes || 0) / 1024;
            const totalKB = jsonKB + ((d.image_size_bytes || 0) / 1024);
            msgEl.textContent = d.has_image
              ? `Saved + frame (${totalKB.toFixed(0)} KB)`
              : `Saved (${jsonKB.toFixed(0)} KB)`;
          }
        } else {
          const err = await res.json().catch(() => ({ detail: 'Error' }));
          if (msgEl) { msgEl.style.color = '#ff6b6b'; msgEl.textContent = err.detail || 'Error'; }
        }
      } catch (e: any) {
        if (msgEl) { msgEl.style.color = '#ff6b6b'; msgEl.textContent = e.message; }
      }
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = '\uD83D\uDCF7 Capture'; }
    }

    // ── cleanup ────────────────────────────────────────────────────────

    private removeOverlay() {
      if (this.canvas) { this.canvas.remove(); this.canvas = null; }
      if (this.hudRoot) { this.hudRoot.remove(); this.hudRoot = null; }
      if (this.resizeObs) { this.resizeObs.disconnect(); this.resizeObs = null; }
      this.data = null;
    }

    destroy() {
      this.active = false;
      this.stopLoop();
      this.removeOverlay();
      if (this.toggleBtn) { this.toggleBtn.remove(); this.toggleBtn = null; }
    }

    private escapeHtml(s: string): string {
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML;
    }
  }

  // ====================================================================
  // Lifecycle — watch for scene pages and manage the controller
  // ====================================================================

  let ctrl: XRayController | null = null;
  let lastPath = '';

  function checkPage() {
    const path = location.pathname;
    const isScene = /\/scenes\/\d+/.test(path);

    if (isScene && path !== lastPath) {
      // New scene page — reset controller
      if (ctrl) ctrl.destroy();
      ctrl = new XRayController();
      lastPath = path;
    } else if (!isScene && ctrl) {
      ctrl.destroy();
      ctrl = null;
      lastPath = '';
      return;
    }

    // Try to inject button if not yet present
    if (ctrl) ctrl.injectButton();
  }

  // Poll for page changes (Stash is a SPA)
  setInterval(checkPage, 500);
  window.addEventListener('popstate', checkPage);
  checkPage();

  dlog('X-Ray overlay loaded (player-integrated)');
})();
