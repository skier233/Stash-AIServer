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
  const w:any = window as any;
  const PluginApi = w.PluginApi; if(!PluginApi || !PluginApi.React) return;
  const React = PluginApi.React; const { useState, useMemo, useEffect, useRef } = React;
  const GQL = (PluginApi as any).graphql || (PluginApi as any).GQL || {};
  // TODO(recommendations-migration): Migrate this component to use the new
  // /api/v1/recommendations/recommenders + /api/v1/recommendations/query
  // hydrated scene endpoints instead of the legacy /algorithms + /scenes
  // ID-only workflow. Once migrated we can remove the client-side per-ID
  // GraphQL hydration loop and rely entirely on backend-provided hydrated
  // scenes (SceneModel) for faster first paint.
  
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
  const TEST_SCENE_BASE = [14632,14586,14466,14447];
  const TEST_SCENE_IDS = Array.from({length:40}, (_,i)=> TEST_SCENE_BASE[i % TEST_SCENE_BASE.length]);

  interface BasicSceneFile { duration?:number; size?:number; }
  interface BasicScene { id:number; title?:string; rating100?:number; rating?:number; files?:BasicSceneFile[]; [k:string]:any }
  interface RecommenderDef { id:string; label:string; description?:string; config?:any[]; contexts?:string[]; }

  // Initial broad scene field list (pruned adaptively if schema rejects fields)
  let SCENE_FIELDS = [
    'id','title','rating100',
    'o_counter','organized','interactive_speed','resume_time','date','details',
    'studio{ id name }',
    'paths{ screenshot preview vtt interactive_heatmap }',
    'performers{ id name }',
    'tags{ id name }',
    'scene_markers{ id seconds title }',
    'groups{ group{ id name } scene_index }',
    'galleries{ id title }',
    'files{ width height duration size fingerprints{ type value } }'
  ].join(' ');
  let FORCE_FALLBACK = false; // toggle to force simplified card fallback

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

  async function fetchScene(client:any, id:number, singleQuery:string):Promise<BasicScene|undefined>{
    try {
      let data:any = null;
      if (GQL && GQL.client?.query && GQL.gql) {
        const resp = await GQL.client.query({ query: GQL.gql(singleQuery), variables:{ id } }); data = resp?.data?.findScene;
      } else if (client?.query) {
        const resp = await client.query({ query: singleQuery, variables:{ id } }); data = resp?.data?.findScene;
      } else if (client?.request) {
        const resp = await client.request(singleQuery, { id }); data = resp?.findScene;
      } else {
        const body = JSON.stringify({ query: singleQuery, variables:{ id } });
        const res = await fetch('/graphql', { method:'POST', headers:{'Content-Type':'application/json'}, body });
        if(!res.ok){ warn('HTTP', res.status); return; }
        const j = await res.json(); data = j?.data?.findScene;
      }
      return normalizeScene(data);
    } catch(e:any){ throw e; }
  }

  function pruneSchemaFromError(message:string){
    if(!/Cannot query field/.test(message)) return false;
    const m = message.match(/Cannot query field \"(\w+)\"/); const bad = m?.[1];
    if(bad){
      warn('Pruning field due to schema mismatch:', bad);
      const simpleRegex = new RegExp('\\b'+bad+'\\b','g'); SCENE_FIELDS = SCENE_FIELDS.replace(simpleRegex,'');
      const compositeRegex = new RegExp(bad + '\\{[^}]*\\}','g'); SCENE_FIELDS = SCENE_FIELDS.replace(compositeRegex,'');
    }
    if(/rating\b/.test(message) && !/rating100/.test(SCENE_FIELDS)) {
      SCENE_FIELDS = SCENE_FIELDS.replace(/rating(?!100)/g,'');
    }
    return true;
  }

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
  // Dynamic algorithm discovery + parameter state (legacy path) & new recommender discovery
  interface AlgoParam { name:string; type:'number'|'string'|'enum'|'boolean'; label?:string; min?:number; max?:number; step?:number; options?:Array<{value:string; label?:string}>; default?:any; }
  interface AlgorithmDef { name:string; label?:string; description?:string; params?:AlgoParam[]; }
  const [algorithms, setAlgorithms] = useState(null as any as (AlgorithmDef[]|null)); // legacy algorithms endpoint
  const [algorithmsError, setAlgorithmsError] = useState(null as any as (string|null));
  const [algorithm, setAlgorithm] = useState('similarity');
  const [algoParams, setAlgoParams] = useState({} as Record<string, any>);
  const [minScore, setMinScore] = useState(0.5);
  // New recommender API state
  /** @type {[RecommenderDef[]|null, Function]} */
  const [recommenders, setRecommenders] = useState(/** @type {any} */(null));
  /** @type {[string|null, Function]} */
  const [recommenderId, setRecommenderId] = useState(/** @type {any} */(null));
  const [usingNewAPI, setUsingNewAPI] = useState(false);
    const [zoomIndex, setZoomIndex] = useState(()=> readInitial(LS_ZOOM_KEY, 'z', 1));
    const [itemsPerPage, setItemsPerPage] = useState(()=> readInitial(LS_PER_PAGE_KEY, 'perPage', 40));
    const [page, setPage] = useState(()=> readInitial(LS_PAGE_KEY, 'p', 1));
  const [scenes, setScenes] = useState([] as BasicScene[]);
  const [loading, setLoading] = useState(false as boolean);
  const [error, setError] = useState(null as string|null);
    const zoomWidths = [280,340,480,640];
    const [componentRef, { width: containerWidth }] = useContainerDimensions();
    const cardWidth = useCardWidth(containerWidth, zoomIndex, zoomWidths);
  // fetch IDs (mock until new backend recommender query flow integrated)
  const [sceneIds, setSceneIds] = useState(TEST_SCENE_IDS as number[]); // legacy hydration path
  const [recCursor, setRecCursor] = useState(undefined as string|undefined); // backend paging cursor (if provided)
  const [backendStatus, setBackendStatus] = useState('idle' as 'idle'|'loading'|'ok'|'error');
  const [discoveryAttempted, setDiscoveryAttempted] = useState(false);
  const pageAPI:any = (w as any).AIPageContext; // for contextual recommendation requests

  // filtered scenes (placeholder for future filters/search)
  const filteredScenes = useMemo(()=> scenes, [scenes]);
  const totalPages = useMemo(()=> Math.max(1, Math.ceil(filteredScenes.length / itemsPerPage)), [filteredScenes.length, itemsPerPage]);

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
          if(def) { if((w as any).AIDebug) console.log('[RecommendedScenes] new recommender default', def.id); setRecommenderId(def.id); setUsingNewAPI(true); }
          setDiscoveryAttempted(true);
          return; // skip marking empty
        }
      } catch(_e){ /* swallow and allow legacy path */ }
      setRecommenders([]); // mark attempted empty
      setDiscoveryAttempted(true);
    })(); }, [recommenders, backendBase, pageAPI]);


    // Discover legacy algorithms only AFTER recommender discovery fully attempted & failed
    useEffect(()=>{ (async()=>{
      // Block until recommender discovery finishes; prevents legacy UI "flash" before new API decides
      if(!discoveryAttempted) return;              // still discovering new API
      if(usingNewAPI) return;                      // new API active => never load legacy list
      if(algorithms!==null) return;                // already loaded/attempted
      if(recommenders && recommenders.length>0) return; // recommender list exists (even if not activated) => no legacy
      try {
        const url = `${backendBase}/api/v1/recommendations/algorithms`;
        const res = await fetch(url, { method:'GET' });
        if(!res.ok) throw new Error('status '+res.status);
        const j = await res.json();
        if(Array.isArray(j)) {
          setAlgorithms(j.map((raw:any):AlgorithmDef=>({
            name: String(raw.name||raw.id||'').trim()||'unknown',
            label: raw.label || raw.name || raw.id,
            description: raw.description,
            params: Array.isArray(raw.params)? raw.params.map((p:any):AlgoParam=>({
              name: String(p.name||'').trim(),
              type: (p.type||'string').toLowerCase(),
              label: p.label||p.name,
              min: typeof p.min==='number'? p.min: undefined,
              max: typeof p.max==='number'? p.max: undefined,
              step: typeof p.step==='number'? p.step: undefined,
              options: Array.isArray(p.options)? p.options.map((o:any)=>({value:String(o.value), label:o.label||o.value})): undefined,
              default: p.default
            })): []
          })));
        } else if (Array.isArray(j.algorithms)) {
          setAlgorithms(j.algorithms as AlgorithmDef[]);
        } else throw new Error('unexpected payload');
      } catch(e:any){
        setAlgorithmsError(e?.message||'Failed to load algorithms');
        // Fallback: static minimal list mirrors legacy hard-coded options
        setAlgorithms([
          { name:'similarity', label:'Similarity', params:[{ name:'min_score', type:'number', min:0, max:1, step:0.05, default:0.5 }]},
          { name:'recent', label:'Recent', params:[]},
          { name:'popular', label:'Popular', params:[]}
        ]);
      }
    })(); }, [algorithms, backendBase, usingNewAPI, discoveryAttempted, recommenders]);

    // Ensure algoParams has defaults when algorithm changes or algorithms list loads
    useEffect(()=>{
      if(usingNewAPI) return; // legacy algorithm param defaulting
      if(!algorithms) return; const def = algorithms.find((a:AlgorithmDef)=>a.name===algorithm) || algorithms[0]; if(!def) return;
      const next:Record<string, any> = { ...algoParams };
      (def.params||[]).forEach((p:AlgoParam)=>{ if(next[p.name]==null) next[p.name] = p.default!=null? p.default : (p.type==='number'? 0 : p.type==='boolean'? false : ''); });
      setAlgoParams(next);
      if(def.params?.some((p:AlgoParam)=>p.name==='min_score')){ const val = next['min_score']; if(typeof val==='number') setMinScore(val); }
    }, [algorithms, algorithm, usingNewAPI]);

    // Unified function to request recommendations (first page)
    const fetchRecommendations = React.useCallback(async ()=>{
      setBackendStatus('loading');
      try {
        const ctx = pageAPI?.get ? pageAPI.get() : null;
        if(usingNewAPI){
          if(!recommenderId){ if((w as any).AIDebug) console.log('[RecommendedScenes] newAPI skip: no recommenderId'); setBackendStatus('idle'); return; }
          const body:any = { context: 'global_feed', recommenderId, limit: 200, config:{} };
          if(ctx){ body.context = 'global_feed'; }
          const url = `${backendBase}/api/v1/recommendations/query`;
          if((w as any).AIDebug) console.log('[RecommendedScenes] newAPI query', body);
          const res = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
          if(res.ok){
            const j = await res.json();
            if(Array.isArray(j.scenes)){
              const norm = j.scenes.map((s:any)=> normalizeScene(s)).filter(Boolean) as BasicScene[];
              setScenes(norm);
              setBackendStatus('ok');
              return; // success path (do not fall through)
            } else if((w as any).AIDebug) console.warn('[RecommendedScenes] newAPI unexpected payload', j);
          } else {
            if((w as any).AIDebug) console.warn('[RecommendedScenes] newAPI status', res.status);
            if(res.status === 404 || res.status === 501){
              if((w as any).AIDebug) console.warn('[RecommendedScenes] disabling newAPI fallback to legacy');
              setUsingNewAPI(false);
            }
          }
          // Keep usingNewAPI true on soft failure so user can retry; fall through to legacy only if disabled.
          if(usingNewAPI) { setBackendStatus('error'); return; }
        }

        // Legacy path (only executes if not using new API)
        let ids:number[] = [];
        const def = algorithms?.find((a:AlgorithmDef)=>a.name===algorithm);
        const body:any = { algorithm, limit:200, min_score: minScore };
        if(ctx){ body.context = { page: ctx.page, entityId: ctx.entityId, isDetailView: ctx.isDetailView, selectedIds: ctx.selectedIds||[] }; }
        if(def && def.params){ const paramPayload:Record<string,any>={}; def.params.forEach((p:AlgoParam)=>{ if(algoParams[p.name]!=null) paramPayload[p.name]=algoParams[p.name]; }); body.params = paramPayload; }
        const url = `${backendBase}/api/v1/recommendations/scenes`;
        if ((w as any).AIDebug) console.log('[RecommendedScenes] legacy fetch', body);
        const res = await fetch(url, { method:'POST', headers:{ 'Content-Type':'application/json' }, body: JSON.stringify(body) });
        if(res.ok){ const j = await res.json(); if(Array.isArray(j.ids) && j.ids.length){ ids = j.ids.map((n:any)=> parseInt(n,10)).filter((n:number)=> !isNaN(n)); } setBackendStatus('ok'); }
        else { setBackendStatus('error'); }
        if(!ids.length){ ids = [...TEST_SCENE_IDS]; }
        const orderedUnique = (()=>{ const seen=new Set<number>(); const out:number[]=[]; for(const i of ids){ if(!seen.has(i)){ seen.add(i); out.push(i);} } return out; })();
        setSceneIds(orderedUnique);
      } catch(_e){ setBackendStatus('error'); }
    }, [usingNewAPI, recommenderId, backendBase, pageAPI, algorithms, algorithm, minScore, algoParams]);

    // Fetch whenever recommender changes (new API path)
    useEffect(()=>{
      if(usingNewAPI && recommenderId && discoveryAttempted){
        // Clear current scenes to prevent stale rendering while new fetch in-flight
        setScenes([]);
        if((w as any).AIDebug) console.log('[RecommendedScenes] recommender changed -> refetch', recommenderId);
        fetchRecommendations();
      }
    }, [usingNewAPI, recommenderId, discoveryAttempted, fetchRecommendations]);

    // Initial & dependency-based recommendation fetch (legacy path only)
    useEffect(()=>{ if(!discoveryAttempted) return; if(usingNewAPI) return; fetchRecommendations(); }, [discoveryAttempted, usingNewAPI, algorithm, minScore, backendBase, fetchRecommendations]);

    // Expose manual refresh with context
  const manualRefresh = () => { if((w as any).AIDebug) console.log('[RecommendedScenes] manual refresh'); fetchRecommendations(); };

    // Fetch scenes whenever sceneIds changes
    useEffect(()=>{ if(usingNewAPI) return; (async()=>{ setLoading(true); setError(null); try { const ids = sceneIds; const seen = new Set<number>(); const unique = ids.filter((i:number)=>{ if(seen.has(i)) return false; seen.add(i); return true; }); let singleQuery = `query FindScene($id: ID!){ findScene(id:$id){ ${SCENE_FIELDS} } }`; const client = (PluginApi as any).graphqlClient || (PluginApi as any).client || (PluginApi as any).apiClient; const results:BasicScene[] = []; for(const id of unique){ try { const sc = await fetchScene(client, id, singleQuery); if(sc) results.push(sc); } catch(e:any){ const msg = e?.message || ''; if(pruneSchemaFromError(msg)){ singleQuery = `query FindScene($id: ID!){ findScene(id:$id){ ${SCENE_FIELDS} } }`; try { const retry = await fetchScene(client, id, singleQuery); if(retry) results.push(retry); } catch(e2:any){ warn('retry failed', id, e2?.message); } } else warn('fetch failed', id, msg); } }
      const byId:Record<number,BasicScene> = {}; results.forEach((r:BasicScene)=>{byId[r.id]=r;}); setScenes(ids.map((i:number)=> byId[i]).filter(Boolean)); } catch(e:any){ setError(e?.message||'Failed to load scenes'); } finally { setLoading(false); } })(); }, [sceneIds, usingNewAPI]);

  // Clamp page when per-page changes
  useEffect(()=>{ const totalPagesInner = Math.max(1, Math.ceil(scenes.length / itemsPerPage)); if(page>totalPagesInner) setPage(totalPagesInner); }, [itemsPerPage, scenes.length, page]);

  const paginatedScenes = useMemo(()=>{ const start=(page-1)*itemsPerPage; return filteredScenes.slice(start, start+itemsPerPage); }, [filteredScenes, page, itemsPerPage]);
    const startIndex = useMemo(()=> (filteredScenes.length? (page-1)*itemsPerPage + 1 : 0), [filteredScenes.length, page, itemsPerPage]);
    const endIndex = useMemo(()=> Math.min(filteredScenes.length, page*itemsPerPage), [filteredScenes.length, page, itemsPerPage]);
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
      const children = paginatedScenes.map((s:BasicScene,i:number)=> !FORCE_FALLBACK && SceneCard ? React.createElement('div',{ key:s.id+'_'+i, style:{display:'contents'}}, React.createElement(SceneCard,{ scene:s, zoomIndex, queue: undefined, index: i })) : React.createElement('div',{ key:s.id+'_'+i, style:{display:'contents'}}, SceneCardFallback(s)) );
      if(typeof document!=='undefined' && !document.getElementById('ai-rec-grid-style')){ const styleEl=document.createElement('style'); styleEl.id='ai-rec-grid-style'; styleEl.textContent = `.ai-rec-grid .scene-card { width: var(--ai-card-width) !important; }`; document.head.appendChild(styleEl); }
      return React.createElement('div',{ className:'row ai-rec-grid d-flex flex-wrap justify-content-center', ref:componentRef, style:{ gap:0, ['--ai-card-width' as any]: cardWidth+'px'}}, children);
    }, [loading, componentsLoading, error, paginatedScenes, SceneCard, cardWidth, zoomIndex]);

    useEffect(()=>{ if((w as any).AIDebug && cardWidth) log('layout', { containerWidth, zoomIndex, preferredWidth: zoomWidths[zoomIndex], cardWidth }); }, [containerWidth, zoomIndex, cardWidth, paginatedScenes]);

    function PaginationControl({ position }:{ position:'top'|'bottom' }){
      const disabledFirst = page<=1; const disabledLast = page>=totalPages;
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
      const info = React.createElement('span',{ key:'info', className:'filter-container text-muted paginationIndex center-text w-100 text-center mt-1'}, `${startIndex}-${endIndex} of ${filteredScenes.length}${statsFragment}`);
      return React.createElement('div',{ className:'d-flex flex-column align-items-center w-100 pagination-footer mt-2' }, position==='top'? [controls, info] : [info, controls]);
    }

    const algoSelect = usingNewAPI
      ? React.createElement('select', { key:'rec', className:'btn-secondary form-control form-control-sm', value:recommenderId||'', onChange:(e:any)=>{ setRecommenderId(e.target.value); }},
          (recommenders||[]).map((r:any)=> React.createElement('option',{ key:r.id, value:r.id }, r.label || r.id))
        )
      : React.createElement('select', { key:'alg', className:'btn-secondary form-control form-control-sm', value:algorithm, onChange:(e:any)=>{ setAlgorithm(e.target.value); }},
          (algorithms||[{name:'similarity', label:'Similarity'},{name:'recent', label:'Recent'},{name:'popular', label:'Popular'}]).map((a:AlgorithmDef)=> React.createElement('option',{ key:a.name, value:a.name }, a.label||a.name))
        );
    // Dynamic params (excluding min_score which keeps legacy width & style)
    const paramInputs: any[] = [];
  const currentAlgo = (algorithms||[]).find((a:AlgorithmDef)=>a.name===algorithm);
  if(!usingNewAPI && currentAlgo && currentAlgo.params){
      currentAlgo.params.forEach((p:any)=>{
        if(p.name==='min_score') return; // handled separately
        const commonProps = { key:p.name, className:'form-control form-control-sm', style:{ width: p.type==='number'? 80: 110, marginLeft:4 }, title: p.description||p.label||p.name };
        let input:any = null;
        if(p.type==='number'){
          input = React.createElement('input',{ ...commonProps, type:'number', step:p.step||0.01, min:p.min, max:p.max, value: algoParams[p.name] ?? '', onChange:(e:any)=> setAlgoParams((prev:Record<string,any>)=> ({...prev, [p.name]: e.target.value===''? undefined : Number(e.target.value) }))});
        } else if(p.type==='boolean'){
          input = React.createElement('select',{ ...commonProps, value: String(!!algoParams[p.name]), onChange:(e:any)=> setAlgoParams((prev:Record<string,any>)=> ({...prev, [p.name]: e.target.value==='true'})) }, [
            React.createElement('option',{key:'true', value:'true'}, 'True'),
            React.createElement('option',{key:'false', value:'false'}, 'False')
          ]);
        } else if(p.type==='enum' && Array.isArray(p.options)){
          input = React.createElement('select',{ ...commonProps, value: algoParams[p.name] ?? (p.options[0]?.value||''), onChange:(e:any)=> setAlgoParams((prev:Record<string,any>)=> ({...prev, [p.name]: e.target.value })) }, p.options.map((o:any)=> React.createElement('option',{ key:o.value, value:o.value }, o.label||o.value)));
        } else { // string fallback
          input = React.createElement('input',{ ...commonProps, type:'text', value: algoParams[p.name] ?? '', onChange:(e:any)=> setAlgoParams((prev:Record<string,any>)=> ({...prev, [p.name]: e.target.value }))});
        }
        paramInputs.push(input);
      });
    }
  const minScoreInput = !usingNewAPI ? React.createElement('input',{ key:'score', className:'form-control form-control-sm', style:{width:70}, type:'number', step:0.05, min:0, max:1, value:minScore, onChange:(e:any)=> { const v=Number(e.target.value); setMinScore(v); setAlgoParams((prev:Record<string,any>)=> ({...prev, min_score:v })); }}) : null;
    const toolbar = React.createElement('div',{ key:'toolbar', role:'toolbar', className:'filtered-list-toolbar btn-toolbar flex-wrap w-100 mb-1 justify-content-center' }, [
      React.createElement('div',{ key:'cluster', className:'d-flex flex-wrap justify-content-center align-items-center gap-2'}, [
  React.createElement('div',{ key:'algGroup', role:'group', className:'mr-2 mb-2 btn-group'}, [algoSelect, ...(minScoreInput? [minScoreInput]: []), ...paramInputs]),
        React.createElement('div',{ key:'ps', className:'page-size-selector mr-2 mb-2'}, React.createElement('select',{ className:'btn-secondary form-control', value:itemsPerPage, onChange:(e:any)=>{ setItemsPerPage(Number(e.target.value)); setPage(1);} }, [20,40,80,120].map(n=> React.createElement('option',{key:n, value:n}, n)))) ,
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
    if(!discoveryAttempted && !usingNewAPI){
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
