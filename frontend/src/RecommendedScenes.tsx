// Recommended Scenes (Full UI Restored w/ cleanup)
// Visual parity with the richer version you preferred:
//  • Algorithm & min score controls
//  • Zoom slider + dynamic card width (upstream parity logic)
//  • Native‑style pagination (top + bottom) w/ dropdown
//  • Duration + size aggregate stats
//  • Adaptive GraphQL fetch & schema pruning (lightly refactored)
//  • Independent persistence (aiRec.* keys) + shareable URL params + cross‑tab sync
// Cleanup changes:
//  • Extracted helpers & constants
//  • Added light typing & defensive guards
//  • Reduced duplicated pagination calculations
//  • Centralized fetch + prune logic
//  • Wrapped debug logs behind w.AIDebug
(function(){
  const BUILD_VERSION = 'rec-pagination-v2-' + new Date().toISOString();
  try { console.info('[RecommendedScenes] Loaded bundle version', BUILD_VERSION); } catch(_) {}
  const w:any = window as any;
  const PluginApi = w.PluginApi; if(!PluginApi || !PluginApi.React) return;
  const React = PluginApi.React; const { useState, useMemo, useEffect, useRef } = React;
  // Using only the new backend hydrated recommendations API.
  const GQL = {} as any; // legacy GraphQL client removed
  
  // Upstream grid hooks copied from GridCard.tsx for exact parity
  function useDebounce(fn:any, delay:number) {
    const timeoutRef = useRef(null as any);
    return useMemo(() => (...args:any[]) => {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => fn(...args), delay);
    }, [fn, delay]);
  }
  
  function useResizeObserver(target:any, callback:any) {
    useEffect(() => {
      if (!target.current || typeof ResizeObserver === 'undefined') return;
      const ro = new ResizeObserver((entries) => {
        // ResizeObserver passes an array of entries
        if (entries && entries.length > 0) {
          callback(entries[0]);
        }
      });
      ro.observe(target.current);
      return () => ro.disconnect();
    }, [target, callback]);
  }
  
  function calculateCardWidth(containerWidth:number, preferredWidth:number) {
    // Exact upstream parity (see GridCard.calculateCardWidth).
    const containerPadding = 30;
    const cardMargin = 10;
    const maxUsableWidth = containerWidth - containerPadding;
    const maxElementsOnRow = Math.ceil(maxUsableWidth / preferredWidth);
    const width = maxUsableWidth / maxElementsOnRow - cardMargin;
    (calculateCardWidth as any)._last = { maxElementsOnRow, preferredWidth, width, containerWidth };
    return width;
  }
  
  function useContainerDimensions(sensitivityThreshold = 20) {
    const target = useRef(null as any);
    const [dimension, setDimension] = useState({ width: 0, height: 0 });
    
    const debouncedSetDimension = useDebounce((entry:any) => {
      // SafeGuard against undefined contentBoxSize
      if (!entry.contentBoxSize || !entry.contentBoxSize.length) return;
      
      const { inlineSize: width, blockSize: height } = entry.contentBoxSize[0];
      let difference = Math.abs(dimension.width - width);
      if (difference > sensitivityThreshold) {
        setDimension({ width, height });
      }
    }, 50);
    
    useResizeObserver(target, debouncedSetDimension);
    
    // Initialize with current size if available
    useEffect(() => {
      if (target.current && dimension.width === 0) {
        const rect = target.current.getBoundingClientRect();
        if (rect.width > 0) {
          setDimension({ width: rect.width, height: rect.height });
        }
      }
    }, []);
    
    return [target, dimension];
  }
  
  function useCardWidth(containerWidth:number, zoomIndex:number, zoomWidths:number[]) {
    return useMemo(() => {
      // Check for mobile - upstream returns undefined for mobile devices
      const isMobile = window.innerWidth <= 768; // Simple mobile check
      if (isMobile) return undefined;
      
      // Provide a reasonable fallback if container width is not yet measured
      // Upstream measures a parent whose visual width includes the row's negative margins expanding into outer padding.
      // Our ref is on the .row itself (content box not enlarged by negative margins). Add 30px (15px each side) so
      // the effective width fed to the algorithm matches native measurement and prevents an extra trailing gap.
  const effectiveWidth = (containerWidth ? containerWidth : 1200); // use raw row width; padding provided by outer wrapper
      if (zoomIndex === undefined || zoomIndex < 0 || zoomIndex >= zoomWidths.length) {
        return undefined; // Return undefined instead of empty return
      }
      const preferredCardWidth = zoomWidths[zoomIndex];
      return calculateCardWidth(effectiveWidth, preferredCardWidth);
    }, [containerWidth, zoomIndex, zoomWidths]);
  }
  const { NavLink } = PluginApi.libraries.ReactRouterDOM || {} as any;
  const Bootstrap = PluginApi.libraries.Bootstrap || {} as any;
  const Button = Bootstrap.Button || ((p:any)=>React.createElement('button', p, p.children));

  const ROUTE = '/plugins/recommended-scenes';
  const LS_PER_PAGE_KEY = 'aiRec.perPage';
  const LS_ZOOM_KEY = 'aiRec.zoom';
  const LS_PAGE_KEY = 'aiRec.page';
  // Legacy test scene ID scaffolding removed.

  interface BasicSceneFile { duration?:number; size?:number; }
  interface BasicScene { id:number; title?:string; rating100?:number; rating?:number; files?:BasicSceneFile[]; [k:string]:any }
  interface RecommenderDef { id:string; label:string; description?:string; config?:any[]; contexts?:string[]; }

  // All scenes arrive hydrated from backend recommender query.

  function log(...args:any[]){ if((w as any).AIDebug) console.log('[RecommendedScenes]', ...args); }
  function warn(...args:any[]){ if((w as any).AIDebug) console.warn('[RecommendedScenes]', ...args); }

  function normalizeScene(sc:any):BasicScene|undefined{
    if(!sc || typeof sc!=='object') return undefined;
  const arrayFields = ['performers','tags','markers','scene_markers','galleries','images','files','groups'];
    arrayFields.forEach(f=>{ if(sc[f]==null) sc[f]=[]; else if(!Array.isArray(sc[f])) sc[f]=[sc[f]].filter(Boolean); });
    if(!sc.studio) sc.studio = null;
    if(sc.rating100 == null && typeof sc.rating === 'number') sc.rating100 = sc.rating * 20;
    if(sc.rating == null && typeof sc.rating100 === 'number') sc.rating = Math.round(sc.rating100/20);
    return sc as BasicScene;
  }

  // Removed legacy per-ID fetch & schema pruning utilities.

  const SceneCardFallback = (s:BasicScene) => React.createElement('div', { className:'scene-card stub', style:{background:'#1e1f22', border:'1px solid #333', borderRadius:4, padding:6}}, [
    React.createElement('div',{key:'img', style:{background:'#2a2d30', height:90, marginBottom:6, display:'flex', alignItems:'center', justifyContent:'center', fontSize:12, color:'#777'}}, 'Scene '+s.id),
    React.createElement('div',{key:'title', style:{fontSize:12, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}, s.title || ('ID '+s.id))
  ]);

  const RecommendedScenesPage: any = () => {
    function readInitial(key:string, urlParam:string, fallback:number){
      try { const usp = new URLSearchParams(location.search); const v = usp.get(urlParam); if(v!=null){ const n=parseInt(v,10); if(!isNaN(n)) return n; } } catch(_){ }
      try { const raw = localStorage.getItem(key); if(raw!=null){ const n=parseInt(raw,10); if(!isNaN(n)) return n; } } catch(_){ }
      return fallback;
    }
  // Recommender state only (legacy removed)
  const [recommenders, setRecommenders] = useState(null as RecommenderDef[]|null);
  const [recommenderId, setRecommenderId] = useState(null as string|null);
    const [zoomIndex, setZoomIndex] = useState(()=> readInitial(LS_ZOOM_KEY, 'z', 1));
    const [itemsPerPage, setItemsPerPage] = useState(()=> readInitial(LS_PER_PAGE_KEY, 'perPage', 40));
    const [page, setPage] = useState(()=> readInitial(LS_PAGE_KEY, 'p', 1));
  // Scenes for current page only (server paginated)
  const [scenes, setScenes] = useState([] as BasicScene[]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false as boolean);
  const [hasMore, setHasMore] = useState(false as boolean);
  const [error, setError] = useState(null as string|null);
    const zoomWidths = [280,340,480,640];
    const [componentRef, { width: containerWidth }] = useContainerDimensions();
    const cardWidth = useCardWidth(containerWidth, zoomIndex, zoomWidths);
  // fetch IDs (mock until new backend recommender query flow integrated)
  // Legacy sceneIds/recCursor removed; backend returns final scenes directly.
  const [backendStatus, setBackendStatus] = useState('idle' as 'idle'|'loading'|'ok'|'error');
  const [discoveryAttempted, setDiscoveryAttempted] = useState(false);
  const pageAPI:any = (w as any).AIPageContext; // for contextual recommendation requests

  // filtered scenes (placeholder for future filters/search)
  const filteredScenes = useMemo(()=> scenes, [scenes]);
  // totalPages is heuristic if hasMore: allow navigating one page past current computed value repeatedly
  const totalPages = useMemo(()=>{
    const base = Math.max(1, Math.ceil(total / itemsPerPage));
    if(hasMore && page >= base) {
      // Extend virtual page count so Next stays enabled
      return page + 1; // allow exploring next page until backend signals no more
    }
    return base;
  }, [total, itemsPerPage, hasMore, page]);

    // Sync & persist
    useEffect(()=>{ try { const usp = new URLSearchParams(location.search); usp.set('perPage', String(itemsPerPage)); usp.set('z', String(zoomIndex)); if(page>1) usp.set('p', String(page)); else usp.delete('p'); const qs=usp.toString(); const desired=location.pathname + (qs? ('?'+qs):''); if(desired !== location.pathname + location.search) history.replaceState(null,'',desired); localStorage.setItem(LS_PER_PAGE_KEY,String(itemsPerPage)); localStorage.setItem(LS_ZOOM_KEY,String(zoomIndex)); localStorage.setItem(LS_PAGE_KEY,String(page)); } catch(_){ } }, [itemsPerPage, zoomIndex, page]);
    useEffect(()=>{ function onStorage(e:StorageEvent){ if(!e.key) return; if(e.key===LS_PER_PAGE_KEY){ const n=parseInt(String(e.newValue||''),10); if(!isNaN(n)) setItemsPerPage(n);} if(e.key===LS_ZOOM_KEY){ const n=parseInt(String(e.newValue||''),10); if(!isNaN(n)) setZoomIndex(n);} if(e.key===LS_PAGE_KEY){ const n=parseInt(String(e.newValue||''),10); if(!isNaN(n)) setPage(n);} } window.addEventListener('storage', onStorage); return ()=> window.removeEventListener('storage', onStorage); }, []);

    // Resolve backend base (mirror logic from AIButton for consistency)
    const backendBase = useMemo(()=>{
      const explicit = (w as any).AI_BACKEND_URL as string | undefined;
      if (explicit) return explicit.replace(/\/$/, '');
      const loc = (location && location.origin) || '';
      try { const u = new URL(loc); if (u.port === '3000') { u.port = '8000'; return u.toString().replace(/\/$/, ''); } } catch {}
      return (loc || 'http://localhost:8000').replace(/\/$/, '');
    }, []);

    // Attempt new recommender discovery first; fallback to legacy algorithms if unavailable
    useEffect(()=>{ (async()=>{
      if(recommenders!==null) return;
      try {
        const ctxPage = pageAPI?.get?.()?.page;
        // map page to RecContext (minimal mapping for now)
        const recContext = 'global_feed';
        const url = `${backendBase}/api/v1/recommendations/recommenders?context=${encodeURIComponent(recContext)}`;
        const res = await fetch(url);
        if(!res.ok) throw new Error('status '+res.status);
        const j = await res.json();
        if(j && Array.isArray(j.recommenders)){
          setRecommenders(j.recommenders as RecommenderDef[]);
          const def = (j.defaultRecommenderId && (j.recommenders as any[]).find(r=>r.id===j.defaultRecommenderId)) || j.recommenders[0];
          if(def) { if((w as any).AIDebug) console.log('[RecommendedScenes] default recommender', def.id); setRecommenderId(def.id); }
          setDiscoveryAttempted(true);
          return; // skip marking empty
        }
      } catch(_e){ /* swallow and allow legacy path */ }
      setRecommenders([]); // mark attempted empty
      setDiscoveryAttempted(true);
    })(); }, [recommenders, backendBase, pageAPI]);


    // (legacy algorithm effects removed)

    // Unified function to request recommendations (first page)
    const latestRequestIdRef = React.useRef(0);
    const fetchRecommendations = React.useCallback(async ()=>{
      const myId = ++latestRequestIdRef.current;
      setBackendStatus('loading');
      setLoading(true); setError(null);
      try {
        if(!recommenderId){ setBackendStatus('idle'); setLoading(false); return; }
        const ctx = pageAPI?.get ? pageAPI.get() : null; // reserved for future context mapping
        const offset = (page-1) * itemsPerPage;
        const body:any = { context: 'global_feed', recommenderId, limit: itemsPerPage, offset, config:{} };
        if(ctx){ body.context = 'global_feed'; }
        const url = `${backendBase}/api/v1/recommendations/query`;
        if((w as any).AIDebug) console.log('[RecommendedScenes] query', body);
        const res = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
        if(res.ok){
          const j = await res.json();
          if(myId !== latestRequestIdRef.current){
            if((w as any).AIDebug) console.log('[RecommendedScenes] stale response ignored', {myId, current: latestRequestIdRef.current});
            return;
          }
          if(Array.isArray(j.scenes)){
            const norm = j.scenes.map((s:any)=> normalizeScene(s)).filter(Boolean) as BasicScene[];
            setScenes(norm);
            const serverTotal = (j.meta && typeof j.meta.total==='number') ? j.meta.total : norm.length;
            const floorTotal = offset + norm.length;
            const metaTotal = serverTotal < floorTotal ? floorTotal : serverTotal;
            setTotal(metaTotal);
            const hm = !!(j.meta && j.meta.hasMore);
            setHasMore(hm);
            if((w as any).AIDebug) console.log('[RecommendedScenes] meta', j.meta, {page, itemsPerPage, computedPages: Math.ceil(metaTotal / itemsPerPage), hasMore: hm});
            setBackendStatus('ok');
            setLoading(false);
            return;
          }
        }
        if(myId !== latestRequestIdRef.current){ return; }
        setBackendStatus('error');
      } catch(_e){ setBackendStatus('error'); setError('Failed to load scenes'); }
      if(myId === latestRequestIdRef.current){
      setLoading(false);
      }
    }, [recommenderId, backendBase, pageAPI, page, itemsPerPage]);

    // Fetch whenever recommender changes
  // When recommender changes, reset page then fetch (single sequence without double calling prior fetch)
  const prevRecommenderRef = React.useRef(null as any);
  useEffect(()=>{
    if(!discoveryAttempted) return;
    if(!recommenderId) return;
    if(prevRecommenderRef.current !== recommenderId){
      prevRecommenderRef.current = recommenderId;
      setPage(1);
      // fetch after synchronous state update using microtask
      queueMicrotask(()=> fetchRecommendations());
      return;
    }
  }, [recommenderId, discoveryAttempted, fetchRecommendations]);

    // Expose manual refresh
    const manualRefresh = () => { if((w as any).AIDebug) console.log('[RecommendedScenes] manual refresh'); fetchRecommendations(); };

  // Clamp page when per-page changes
  useEffect(()=>{
    if(loading) return; // avoid clamp while fetch pending
    if(!hasMore){
      const maxPages = Math.max(1, Math.ceil(total / itemsPerPage));
      if(page>maxPages){
        if((w as any).AIDebug) console.log('[RecommendedScenes] clamp page', {page, maxPages});
        setPage(maxPages);
      }
    }
  }, [itemsPerPage, total, page, hasMore, loading]);

  useEffect(()=>{ if((w as any).AIDebug) console.log('[RecommendedScenes] page change', {page, itemsPerPage, total, hasMore}); }, [page, itemsPerPage, total, hasMore]);
  // Fetch when page or itemsPerPage change (offset-based pagination)
  useEffect(()=>{ if(!discoveryAttempted) return; if(!recommenderId) return; fetchRecommendations(); }, [page, itemsPerPage, discoveryAttempted, recommenderId, fetchRecommendations]);

  const paginatedScenes = filteredScenes; // server already paginated
  const startIndex = useMemo(()=> (total? (page-1)*itemsPerPage + 1 : 0), [total, page, itemsPerPage]);
  const endIndex = useMemo(()=> Math.min(total, page*itemsPerPage), [total, page, itemsPerPage]);
    const { totalDuration, totalSizeBytes } = useMemo(()=>{ let duration=0, size=0; for(const sc of filteredScenes){ const files = sc.files||[]; let longest=0; for(const f of files){ if(typeof f?.duration==='number') longest=Math.max(longest,f.duration); if(typeof f?.size==='number') size+=f.size; } duration+=longest; } return { totalDuration:duration, totalSizeBytes:size }; }, [filteredScenes]);
    function formatDuration(seconds:number){ if(!seconds) return '0s'; const MIN=60,H=3600,D=86400,M=30*D; let rem=seconds; const months=Math.floor(rem/M); rem%=M; const days=Math.floor(rem/D); rem%=D; const hours=Math.floor(rem/H); rem%=H; const mins=Math.floor(rem/MIN); const parts:string[]=[]; if(months) parts.push(months+'M'); if(days) parts.push(days+'D'); if(hours) parts.push(hours+'h'); if(mins) parts.push(mins+'m'); return parts.length? parts.join(' '): seconds+'s'; }
    function formatSize(bytes:number){ if(!bytes) return '0 B'; const units=['B','KiB','MiB','GiB','TiB','PiB']; let i=0,val=bytes; while(val>1024 && i<units.length-1){ val/=1024; i++; } return (i>=3? val.toFixed(1): Math.round(val))+' '+units[i]; }

    const componentsToLoad = [PluginApi.loadableComponents?.SceneCard].filter(Boolean);
    const componentsLoading = PluginApi.hooks?.useLoadComponents ? PluginApi.hooks.useLoadComponents(componentsToLoad) : false;
    const { SceneCard } = PluginApi.components || {};
    const grid = useMemo(()=>{
      if(loading || componentsLoading) return React.createElement('div',{ style:{marginTop:24}}, 'Loading scenes...');
      if(error) return React.createElement('div',{ style:{marginTop:24, color:'#c66'}}, error);
      if(!paginatedScenes.length) return React.createElement('div',{ style:{marginTop:24}}, 'No scenes');
      if(cardWidth===undefined) return React.createElement('div',{ style:{marginTop:24}}, 'Calculating layout...');
  const children = paginatedScenes.map((s:BasicScene,i:number)=> SceneCard ? React.createElement('div',{ key:s.id+'_'+i, style:{display:'contents'}}, React.createElement(SceneCard,{ scene:s, zoomIndex, queue: undefined, index: i })) : React.createElement('div',{ key:s.id+'_'+i, style:{display:'contents'}}, SceneCardFallback(s)) );
      if(typeof document!=='undefined' && !document.getElementById('ai-rec-grid-style')){ const styleEl=document.createElement('style'); styleEl.id='ai-rec-grid-style'; styleEl.textContent = `.ai-rec-grid .scene-card { width: var(--ai-card-width) !important; }`; document.head.appendChild(styleEl); }
      return React.createElement('div',{ className:'row ai-rec-grid d-flex flex-wrap justify-content-center', ref:componentRef, style:{ gap:0, ['--ai-card-width' as any]: cardWidth+'px'}}, children);
    }, [loading, componentsLoading, error, paginatedScenes, SceneCard, cardWidth, zoomIndex]);

    useEffect(()=>{ if((w as any).AIDebug && cardWidth) log('layout', { containerWidth, zoomIndex, preferredWidth: zoomWidths[zoomIndex], cardWidth }); }, [containerWidth, zoomIndex, cardWidth, paginatedScenes]);

    function PaginationControl({ position }:{ position:'top'|'bottom' }){
  const disabledFirst = page<=1; const disabledLast = page>=totalPages && !hasMore;
      const controls = React.createElement('div',{ key:'pc', role:'group', className:'pagination btn-group' }, [
        React.createElement('button',{key:'first', disabled:disabledFirst, className:'btn btn-secondary', onClick:()=>setPage(1)}, '«'),
  React.createElement('button',{key:'prev', disabled:disabledFirst, className:'btn btn-secondary', onClick:()=>setPage((p:number)=>Math.max(1,p-1))}, '<'),
        React.createElement('div',{key:'cnt', className:'page-count-container'}, [
          React.createElement('div',{key:'grp', role:'group', className:'btn-group'}, [
            React.createElement('button',{ key:'lbl', type:'button', className:'page-count btn btn-secondary'}, `${page} of ${totalPages}`)
          ])
        ]),
  React.createElement('button',{key:'next', disabled:disabledLast, className:'btn btn-secondary', onClick:()=>setPage((p:number)=>Math.min(totalPages,p+1))}, '>'),
        React.createElement('button',{key:'last', disabled:disabledLast, className:'btn btn-secondary', onClick:()=>setPage(totalPages)}, '»')
      ]);
  const statsFragment = totalDuration>0 ? ` (${formatDuration(totalDuration)} - ${formatSize(totalSizeBytes)})` : '';
  const info = React.createElement('span',{ key:'info', className:'filter-container text-muted paginationIndex center-text w-100 text-center mt-1'}, `${startIndex}-${endIndex} of ${total}${statsFragment}`);
      return React.createElement('div',{ className:'d-flex flex-column align-items-center w-100 pagination-footer mt-2' }, position==='top'? [controls, info] : [info, controls]);
    }

    const recSelect = React.createElement('select', { key:'rec', className:'btn-secondary form-control form-control-sm', value:recommenderId||'', onChange:(e:any)=>{ setRecommenderId(e.target.value); }},
      (recommenders||[]).map((r:any)=> React.createElement('option',{ key:r.id, value:r.id }, r.label || r.id))
    );
    const toolbar = React.createElement('div',{ key:'toolbar', role:'toolbar', className:'filtered-list-toolbar btn-toolbar flex-wrap w-100 mb-1 justify-content-center' }, [
      React.createElement('div',{ key:'cluster', className:'d-flex flex-wrap justify-content-center align-items-center gap-2'}, [
  React.createElement('div',{ key:'recGroup', role:'group', className:'mr-2 mb-2 btn-group'}, [recSelect]),
        React.createElement('div',{ key:'ps', className:'page-size-selector mr-2 mb-2'}, React.createElement('select',{ className:'btn-secondary form-control', value:itemsPerPage, onChange:(e:any)=>{ setItemsPerPage(Number(e.target.value)); setPage(1);} }, [20,40,80,100].map(n=> React.createElement('option',{key:n, value:n}, n)))) ,
        React.createElement('div',{ key:'zoomWrap', className:'mx-2 mb-2 d-inline-flex align-items-center'}, [
          React.createElement('input',{ key:'zr', min:0, max:3, type:'range', className:'zoom-slider ml-1 form-control-range', value:zoomIndex, onChange:(e:any)=> setZoomIndex(Number(e.target.value)) })
        ]),
        React.createElement('div',{ key:'act', role:'group', className:'mb-2 btn-group'}, [
          React.createElement(Button,{ key:'refresh', className:'btn btn-secondary minimal', disabled:loading, title:'Refresh', onClick:manualRefresh }, '↻')
        ])
        , backendStatus==='error' && React.createElement('div',{ key:'err', className:'mb-2 ml-2 small text-danger d-flex align-items-center' }, [
          React.createElement('span',{ key:'lbl', style:{marginRight:6}}, 'Backend failed'),
          React.createElement(Button,{ key:'retry', className:'btn btn-secondary minimal btn-sm', disabled:loading, onClick:()=> manualRefresh() }, 'Retry')
        ])
      ])
    ]);

  const backendBadge = backendStatus==='ok' ? '✅ backend' : backendStatus==='loading' ? '… backend' : backendStatus==='error' ? '⚠ backend fallback' : '';

    // While recommender discovery hasn't finished, suppress legacy UI to avoid flash
    if(!discoveryAttempted){
      return React.createElement('div',{ className:'text-center mt-4'}, 'Loading recommendation engine…');
    }

    return React.createElement(React.Fragment,null,[
  backendBadge? React.createElement('div',{key:'bstat', className:'text-center small text-muted mb-1'}, backendBadge): null,
  toolbar,
      React.createElement(PaginationControl,{ key:'pgt', position:'top'}),
      grid,
      React.createElement(PaginationControl,{ key:'pgb', position:'bottom'})
    ]);
  };

  try { PluginApi.register.route(ROUTE, RecommendedScenesPage); } catch {}

  // Single canonical patch key based on provided MainNavbar source
  try {
    PluginApi.patch.before('MainNavBar.MenuItems', function(props:any){
      // Duplicate guard
      try {
        const arr = React.Children.toArray(props.children);
        if (arr.some((c:any)=> c?.props?.children?.props?.to === ROUTE || c?.props?.to === ROUTE)) return props;
      } catch {}

      const label = 'Recommended Scenes';
      let qs = '';
      try {
        const pp = localStorage.getItem(LS_PER_PAGE_KEY);
        const z = localStorage.getItem(LS_ZOOM_KEY);
        const p = localStorage.getItem(LS_PAGE_KEY);
        const params = new URLSearchParams();
        if(pp) params.set('perPage', pp);
        if(z) params.set('z', z);
        if(p && p !== '1') params.set('p', p);
        const s = params.toString();
        if(s) qs = '?' + s;
      } catch(_){ }
      const node = React.createElement(
        'div',
        { key:'recommended-scenes-link', className:'col-4 col-sm-3 col-md-2 col-lg-auto' },
        NavLink ? (
          React.createElement(NavLink, {
            exact: true,
            to: ROUTE+qs,
            activeClassName:'active',
            className:'btn minimal p-4 p-xl-2 d-flex d-xl-inline-block flex-column justify-content-between align-items-center'
          }, label)
        ) : (
          React.createElement('a', { href:'#'+ROUTE, className:'btn minimal p-4 p-xl-2 d-flex d-xl-inline-block flex-column justify-content-between align-items-center'}, label)
        )
      );
      return [{ children: (<>{props.children}{node}</>) }];
    });
  } catch {}

  w.RecommendedScenesPage = RecommendedScenesPage;
})();
