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
    const arrayFields = ['performers','tags','markers','scene_markers','galleries','images','files'];
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
    const [algorithm, setAlgorithm] = useState('similarity');
    const [minScore, setMinScore] = useState(0.5);
    const [zoomIndex, setZoomIndex] = useState(()=> readInitial(LS_ZOOM_KEY, 'z', 1));
    const [itemsPerPage, setItemsPerPage] = useState(()=> readInitial(LS_PER_PAGE_KEY, 'perPage', 40));
    const [page, setPage] = useState(()=> readInitial(LS_PAGE_KEY, 'p', 1));
  const [scenes, setScenes] = useState([] as BasicScene[]);
  const [loading, setLoading] = useState(false as boolean);
  const [error, setError] = useState(null as string|null);
    const zoomWidths = [280,340,480,640];
    const [componentRef, { width: containerWidth }] = useContainerDimensions();
    const cardWidth = useCardWidth(containerWidth, zoomIndex, zoomWidths);
    // fetch IDs (mock until backend provider integrated)
  const [sceneIds, setSceneIds] = useState(TEST_SCENE_IDS as number[]);

  // filtered scenes (placeholder for future filters/search)
  const filteredScenes = useMemo(()=> scenes, [scenes]);
  const totalPages = useMemo(()=> Math.max(1, Math.ceil(filteredScenes.length / itemsPerPage)), [filteredScenes.length, itemsPerPage]);

    // Sync & persist
    useEffect(()=>{ try { const usp = new URLSearchParams(location.search); usp.set('perPage', String(itemsPerPage)); usp.set('z', String(zoomIndex)); if(page>1) usp.set('p', String(page)); else usp.delete('p'); const qs=usp.toString(); const desired=location.pathname + (qs? ('?'+qs):''); if(desired !== location.pathname + location.search) history.replaceState(null,'',desired); localStorage.setItem(LS_PER_PAGE_KEY,String(itemsPerPage)); localStorage.setItem(LS_ZOOM_KEY,String(zoomIndex)); localStorage.setItem(LS_PAGE_KEY,String(page)); } catch(_){ } }, [itemsPerPage, zoomIndex, page]);
    useEffect(()=>{ function onStorage(e:StorageEvent){ if(!e.key) return; if(e.key===LS_PER_PAGE_KEY){ const n=parseInt(String(e.newValue||''),10); if(!isNaN(n)) setItemsPerPage(n);} if(e.key===LS_ZOOM_KEY){ const n=parseInt(String(e.newValue||''),10); if(!isNaN(n)) setZoomIndex(n);} if(e.key===LS_PAGE_KEY){ const n=parseInt(String(e.newValue||''),10); if(!isNaN(n)) setPage(n);} } window.addEventListener('storage', onStorage); return ()=> window.removeEventListener('storage', onStorage); }, []);

    // Fetch scenes when algorithm/minScore changes (placeholder logic currently just fetches IDs)
  useEffect(()=>{ (async()=>{ setLoading(true); setError(null); try { const ids = sceneIds; const seen = new Set<number>(); const unique = ids.filter((i:number)=>{ if(seen.has(i)) return false; seen.add(i); return true; }); let singleQuery = `query FindScene($id: ID!){ findScene(id:$id){ ${SCENE_FIELDS} } }`; const client = (PluginApi as any).graphqlClient || (PluginApi as any).client || (PluginApi as any).apiClient; const results:BasicScene[] = []; for(const id of unique){ try { const sc = await fetchScene(client, id, singleQuery); if(sc) results.push(sc); } catch(e:any){ const msg = e?.message || ''; if(pruneSchemaFromError(msg)){ singleQuery = `query FindScene($id: ID!){ findScene(id:$id){ ${SCENE_FIELDS} } }`; try { const retry = await fetchScene(client, id, singleQuery); if(retry) results.push(retry); } catch(e2:any){ warn('retry failed', id, e2?.message); } } else warn('fetch failed', id, msg); } }
    const byId:Record<number,BasicScene> = {}; results.forEach((r:BasicScene)=>{byId[r.id]=r;}); setScenes(ids.map((i:number)=> byId[i]).filter(Boolean)); } catch(e:any){ setError(e?.message||'Failed to load scenes'); } finally { setLoading(false); } })(); }, [algorithm, minScore]);

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

    const toolbar = React.createElement('div',{ key:'toolbar', role:'toolbar', className:'filtered-list-toolbar btn-toolbar flex-wrap w-100 mb-1 justify-content-center' }, [
      React.createElement('div',{ key:'cluster', className:'d-flex flex-wrap justify-content-center align-items-center gap-2'}, [
        React.createElement('div',{ key:'algGroup', role:'group', className:'mr-2 mb-2 btn-group'}, [
          React.createElement('select',{ key:'alg', className:'btn-secondary form-control form-control-sm', value:algorithm, onChange:(e:any)=> setAlgorithm(e.target.value)}, [
            React.createElement('option',{key:'similarity', value:'similarity'}, 'Similarity'),
            React.createElement('option',{key:'recent', value:'recent'}, 'Recent'),
            React.createElement('option',{key:'popular', value:'popular'}, 'Popular')
          ]),
          React.createElement('input',{ key:'score', className:'form-control form-control-sm', style:{width:70}, type:'number', step:0.05, min:0, max:1, value:minScore, onChange:(e:any)=> setMinScore(Number(e.target.value))})
        ]),
        React.createElement('div',{ key:'ps', className:'page-size-selector mr-2 mb-2'}, React.createElement('select',{ className:'btn-secondary form-control', value:itemsPerPage, onChange:(e:any)=>{ setItemsPerPage(Number(e.target.value)); setPage(1);} }, [20,40,80,120].map(n=> React.createElement('option',{key:n, value:n}, n)))) ,
        React.createElement('div',{ key:'zoomWrap', className:'mx-2 mb-2 d-inline-flex align-items-center'}, [
          React.createElement('input',{ key:'zr', min:0, max:3, type:'range', className:'zoom-slider ml-1 form-control-range', value:zoomIndex, onChange:(e:any)=> setZoomIndex(Number(e.target.value)) })
        ]),
        React.createElement('div',{ key:'act', role:'group', className:'mb-2 btn-group'}, [
          React.createElement(Button,{ key:'refresh', className:'btn btn-secondary minimal', disabled:loading, title:'Refresh', onClick:()=>{ setSceneIds([...sceneIds].sort(()=>Math.random()-0.5)); setAlgorithm((a:string)=>a); }}, '↻'),
          React.createElement(Button,{ key:'reset', className:'btn btn-secondary minimal', title:'Reset', onClick:()=>{ setAlgorithm('similarity'); setMinScore(0.5); setItemsPerPage(40); setZoomIndex(1); setSceneIds(TEST_SCENE_IDS); setPage(1); }}, 'Reset')
        ])
      ])
    ]);

    return React.createElement(React.Fragment,null,[
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
