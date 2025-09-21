// Recommended Scenes plugin page with configuration controls and a scene grid prototype
(function(){
  const w:any = window as any;
  const PluginApi = w.PluginApi;
  if (!PluginApi || !PluginApi.React) { return; }
  const React = PluginApi.React;
  const { useState, useMemo, useEffect, useRef } = React;
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
  
  function calculateCardWidth(containerWidth:number, preferredWidth:number, marginPerCard:number) {
    // Native scenes page replication:
    // effectiveWidth = containerWidth - 30 (assumed total horizontal padding) 
    // columns = ceil( (effective + marginPerCard) / (preferredWidth + marginPerCard) )
    // width = (effective - columns * marginPerCard) / columns
    // This allows shrinking below preferred when needed to fit an extra column (matching observed native widths).
    const PADDING_TOTAL = 30; // 15px each side assumed from native layout
    const safeMargin = marginPerCard || 10; // fallback if detection fails
    const effective = Math.max(0, containerWidth - PADDING_TOTAL);
    if (effective <= 0) return preferredWidth;
    let columns = Math.ceil( (effective + safeMargin) / (preferredWidth + safeMargin) );
    if (columns < 1) columns = 1;
    let width = (effective - columns * safeMargin) / columns;
    width = Math.round(width * 1000) / 1000; // keep 3 decimals for closer parity (native values observed)
    const leftover = Math.round( (effective - (width + safeMargin) * columns) * 1000 ) / 1000;
    (calculateCardWidth as any)._last = { columns, preferredWidth, width, effective, containerWidth, marginPerCard: safeMargin, leftover, padding: PADDING_TOTAL };
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
  
  function useCardWidth(containerWidth:number, zoomIndex:number, zoomWidths:number[], marginPerCard:number) {
    return useMemo(() => {
      // Check for mobile - upstream returns undefined for mobile devices
      const isMobile = window.innerWidth <= 768; // Simple mobile check
      if (isMobile) return undefined;
      
      // Provide a reasonable fallback if container width is not yet measured
      // Upstream measures a parent whose visual width includes the row's negative margins expanding into outer padding.
      // Our ref is on the .row itself (content box not enlarged by negative margins). Add 30px (15px each side) so
      // the effective width fed to the algorithm matches native measurement and prevents an extra trailing gap.
      const effectiveWidth = containerWidth ? containerWidth + 30 : 1200; // fallback width
      if (zoomIndex === undefined || zoomIndex < 0 || zoomIndex >= zoomWidths.length) {
        return undefined; // Return undefined instead of empty return
      }
      const preferredCardWidth = zoomWidths[zoomIndex];
      return calculateCardWidth(effectiveWidth, preferredCardWidth, marginPerCard);
    }, [containerWidth, zoomIndex, zoomWidths, marginPerCard]);
  }
  const { NavLink } = PluginApi.libraries.ReactRouterDOM || {} as any;
  const Bootstrap = PluginApi.libraries.Bootstrap || {} as any;
  const Button = Bootstrap.Button || ((p:any)=>React.createElement('button', p, p.children));
  const Form = Bootstrap.Form || { Group:(p:any)=>React.createElement('div',p,p.children), Label:(p:any)=>React.createElement('label',p,p.children), Control:(p:any)=>React.createElement('input',p)};

  const ROUTE = '/plugins/recommended-scenes';
  // Produce 40 test IDs by repeating the base set so we can validate multi-row layout.
  const TEST_SCENE_BASE = [14632,14586,14466,14447];
  const TEST_SCENE_IDS = Array.from({length:40}, (_,i)=> TEST_SCENE_BASE[i % TEST_SCENE_BASE.length]);

  // Base fragment
  // Start with a broader subset of fields used by the upstream SceneCard.
  // We'll prune dynamically if the schema rejects any of them.
  let SCENE_FIELDS = [
    'id',
    'title',
    'rating100',
    // simple scalars
    'o_counter', 'organized', 'interactive_speed', 'resume_time', 'date', 'details',
    // nested objects
    'studio{ id name }',
    'paths{ screenshot preview vtt interactive_heatmap }',
    'performers{ id name }',
    'tags{ id name }',
    'scene_markers{ id seconds title }',
    'groups{ group{ id name } scene_index }',
    'galleries{ id title }',
    'files{ width height duration size fingerprints{ type value } }'
  ].join(' ');
  let FORCE_FALLBACK = false; // attempt native SceneCard now

  // Attempt to reuse existing scene card component if exposed (future enhancement)
  const SceneCard = (id:number) => React.createElement('div', {
      key:id,
      className:'ai-rec-scene-card',
      style:{
        border:'1px solid #333',
        borderRadius:4,
        padding:8,
        background:'#1d1f21',
        display:'flex',
        flexDirection:'column',
        minHeight:180
      }
    },[
      React.createElement('div',{key:'thumb', style:{flex:1, background:'#2b2f33', marginBottom:8, display:'flex', alignItems:'center', justifyContent:'center', fontSize:12, color:'#888'}}, 'Scene '+id),
      React.createElement('div',{key:'meta', style:{fontSize:12, color:'#bbb'}}, 'ID: '+id)
    ]);

  const RecommendedScenesPage: any = () => {
  const [algorithm, setAlgorithm] = useState('similarity');
  const [minScore, setMinScore] = useState(0.5);
  // Removed search / sort / alternative view modes per user request
  const [zoomIndex, setZoomIndex] = useState(1); // default nearer to screenshot mid zoom
  // Pagination state
  const [itemsPerPage, setItemsPerPage] = useState(40);
  const [page, setPage] = useState(1); // 1-based
    const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null as any);
    // store scene IDs (placeholder from backend)
    const [sceneIds, setSceneIds] = useState(TEST_SCENE_IDS as number[]);
    const [scenes, setScenes] = useState([] as any[]); // full fetched scenes (all pages)
    // layout measurement (outer wrapper width drives card sizing) - using upstream hooks
  const zoomWidths = [280,340,480,640]; // exact same as SceneCardsGrid
  const [componentRef, { width: containerWidth }] = useContainerDimensions();
  const [cardMarginLR, setCardMarginLR] = useState(0); // measured left+right margin of scene-card
  const cardWidth = useCardWidth(containerWidth, zoomIndex, zoomWidths, cardMarginLR);
    
    // Debug logging
    useEffect(() => {
      if (w.AIDebug) {
        console.log('[RecommendedScenes] Grid debug:', {
          containerWidth,
          zoomIndex,
          cardWidth,
          zoomWidths,
          preferredWidth: zoomWidths[zoomIndex],
          hasContainer: !!componentRef.current
        });
      }
    }, [containerWidth, zoomIndex, cardWidth]);

    // Fetch scenes from Stash GraphQL
    async function fetchScenes(ids:number[]){
      if(!ids.length) { setScenes([]); return; }
      setLoading(true); setError(null);
      try {
        // unique IDs to avoid duplicate network calls
        const seen = new Set<number>();
        const limited = ids.filter(id=>{ if(seen.has(id)) return false; seen.add(id); return true; });
  let singleQuery = `query FindScene($id: ID!){ findScene(id:$id){ ${SCENE_FIELDS} } }`;
        const client = (PluginApi as any).graphqlClient || (PluginApi as any).client || (PluginApi as any).apiClient;
        const results:any[] = [];
        for (const id of limited) {
          try {
            let data:any = null;
            if (GQL && GQL.client?.query && GQL.gql) {
              const resp = await GQL.client.query({ query: GQL.gql(singleQuery), variables:{ id } });
              data = resp?.data?.findScene;
            } else if (client?.query) {
              const resp = await client.query({ query: singleQuery, variables:{ id } });
              data = resp?.data?.findScene;
            } else if (client?.request) {
              const resp = await client.request(singleQuery, { id });
              data = resp?.findScene;
            } else {
              const body = JSON.stringify({ query: singleQuery, variables:{ id } });
              const res = await fetch('/graphql', { method:'POST', headers:{'Content-Type':'application/json'}, body });
              if (!res.ok) {
                const txt = await res.text();
                if (w.AIDebug) console.warn('[RecommendedScenes] HTTP', res.status, 'body:', txt.slice(0,300));
                continue;
              }
              const j = await res.json();
              data = j?.data?.findScene;
            }
            if (data) {
              const arrayFields = ['performers','tags','markers','scene_markers','galleries','images','files'];
              for (const f of arrayFields) {
                if (data[f] == null) data[f] = [];
                else if (!Array.isArray(data[f])) data[f] = [data[f]].filter(Boolean);
              }
              if (!data.studio) data.studio = null;
              if (data.rating100 == null && typeof data.rating === 'number') data.rating100 = data.rating * 20;
              if (data.rating == null && typeof data.rating100 === 'number') data.rating = Math.round(data.rating100/20);
              results.push(data);
            } else if (w.AIDebug) {
              console.warn('[RecommendedScenes] no data for id', id);
            }
          } catch(e:any){
            // Adaptive schema fallback: if validation error mentions a field, strip it and retry once.
            const msg = e?.message || '';
            if (/Cannot query field/.test(msg)) {
              // Extract the field name and remove its segment.
              const m = msg.match(/Cannot query field \\"(\\w+)\\"/);
              const bad = m?.[1];
              if (bad) {
                if (w.AIDebug) console.warn('[RecommendedScenes] pruning field due to schema mismatch:', bad);
                // remove simple token
                const simpleRegex = new RegExp('\\b'+bad+'\\b','g');
                SCENE_FIELDS = SCENE_FIELDS.replace(simpleRegex, '');
                // remove composite selections bad{ ... }
                const compositeRegex = new RegExp(bad + '\\{[^}]*\\}','g');
                SCENE_FIELDS = SCENE_FIELDS.replace(compositeRegex,'');
              }
              // Specific legacy rating adjustment
              if (/rating\b/.test(msg) && !/rating100/.test(SCENE_FIELDS)) {
                SCENE_FIELDS = SCENE_FIELDS.replace(/rating(?!100)/g,'');
              }
              singleQuery = `query FindScene($id: ID!){ findScene(id:$id){ ${SCENE_FIELDS} } }`;
              try {
                if (w.AIDebug) console.warn('[RecommendedScenes] retrying id due to schema diff', id, 'fields now:', SCENE_FIELDS);
                // retry once
                let data:any = null;
                if (GQL && GQL.client?.query && GQL.gql) {
                  const resp2 = await GQL.client.query({ query: GQL.gql(singleQuery), variables:{ id } });
                  data = resp2?.data?.findScene;
                } else if (client?.query) {
                  const resp2 = await client.query({ query: singleQuery, variables:{ id } });
                  data = resp2?.data?.findScene;
                } else if (client?.request) {
                  const resp2 = await client.request(singleQuery, { id });
                  data = resp2?.findScene;
                } else {
                  const body2 = JSON.stringify({ query: singleQuery, variables:{ id } });
                  const res2 = await fetch('/graphql', { method:'POST', headers:{'Content-Type':'application/json'}, body: body2 });
                  if (res2.ok) {
                    const j2 = await res2.json();
                    data = j2?.data?.findScene;
                  }
                }
                if (data) {
                  const arrayFields = ['performers','tags','markers','scene_markers','galleries','images','files'];
                  for (const f of arrayFields) {
                    if (data[f] == null) data[f] = [];
                    else if (!Array.isArray(data[f])) data[f] = [data[f]].filter(Boolean);
                  }
                  if (!data.studio) data.studio = null;
                  if (data.rating100 == null && typeof data.rating === 'number') data.rating100 = data.rating * 20;
                  if (data.rating == null && typeof data.rating100 === 'number') data.rating = Math.round(data.rating100/20);
                  results.push(data);
                } else if (w.AIDebug) console.warn('[RecommendedScenes] still no data after retry', id);
              } catch(e2:any){ if (w.AIDebug) console.warn('[RecommendedScenes] retry failed', id, e2?.message); }
            } else if (w.AIDebug) {
              console.warn('[RecommendedScenes] fetch id failed', id, msg);
            }
          }
        }
        if (w.AIDebug) {
          console.log('[RecommendedScenes] fetched', { requested: limited, received: results.map(s=>s.id) });
          results.slice(0,5).forEach((sc:any, idx:number)=>{
            const summary:any = {};
            Object.keys(sc||{}).forEach(k=>{ summary[k] = Array.isArray(sc[k]) ? `Array(${sc[k].length})` : (sc[k] && typeof sc[k]==='object' ? 'Object' : typeof sc[k]); });
            console.log('[RecommendedScenes] scene sample', idx, sc.id, summary);
          });
        }
        // Map results back to original (possibly duplicated) order if original had duplicates
        const byId:Record<string,any> = {}; results.forEach(r=>{byId[r.id]=r;});
        const ordered = ids.map(i=> byId[i]).filter(Boolean);
        setScenes(ordered);
      } catch(e:any){
        setError(e?.message || 'Failed to load scenes');
      } finally { setLoading(false); }
    }

    // Initial + config change load
    useEffect(()=>{
      // For now use all test IDs; when backend provides recommended IDs replace here
      setSceneIds(TEST_SCENE_IDS);
      fetchScenes(TEST_SCENE_IDS);
      setPage(1);
    }, [algorithm, minScore]);

    // If page exceeds total pages after itemsPerPage change, clamp
    useEffect(()=>{
      const totalPages = Math.max(1, Math.ceil(scenes.length / itemsPerPage));
      if (page > totalPages) setPage(totalPages);
    }, [itemsPerPage, scenes.length, page]);

    // Debug logging to analyze sizing issues
    useEffect(()=>{
      if ((window as any).AIDebug) {
        console.log('[RecommendedScenes] layout', { containerWidth, zoomIndex, baseZoomWidth: zoomWidths[zoomIndex], cardWidth });
      }
    }, [containerWidth, zoomIndex, cardWidth]);

    // For now scenes are shown in the order provided by sceneIds (no search/sort)
    const filteredScenes = useMemo(()=> scenes, [scenes]);

    const paginatedScenes = useMemo(()=>{
      const start = (page-1)*itemsPerPage;
      return filteredScenes.slice(start, start+itemsPerPage);
    }, [filteredScenes, page, itemsPerPage]);

    const totalPages = useMemo(()=> Math.max(1, Math.ceil(filteredScenes.length / itemsPerPage)), [filteredScenes.length, itemsPerPage]);
    const startIndex = useMemo(()=> (filteredScenes.length? (page-1)*itemsPerPage + 1 : 0), [filteredScenes.length, page, itemsPerPage]);
    const endIndex = useMemo(()=> Math.min(filteredScenes.length, page*itemsPerPage), [filteredScenes.length, page, itemsPerPage]);

    // Load core SceneCard component if available
    const componentsToLoad = [PluginApi.loadableComponents?.SceneCard].filter(Boolean);
    const componentsLoading = PluginApi.hooks?.useLoadComponents ? PluginApi.hooks.useLoadComponents(componentsToLoad) : false;
    const { SceneCard } = PluginApi.components || {};

    const grid = useMemo(()=>{
      if (loading || componentsLoading) return React.createElement('div', { className:'loading-indicator', style:{marginTop:24}}, 'Loading scenes...');
      if (error) return React.createElement('div', { style:{marginTop:24, color:'#c66'}}, error);
      if (!paginatedScenes.length) return React.createElement('div', { style:{marginTop:24}}, 'No scenes');
      if (cardWidth === undefined) return React.createElement('div', { style:{marginTop:24}}, 'Calculating layout...');
      
      // Use exact SceneCardsGrid pattern. NOTE: PluginApi SceneCard interface (pluginApi.d.ts) does NOT expose the 'width' prop
      // used internally by core <SceneCard>/<GridCard>. So passing {width: cardWidth} is ignored in the plugin
      // environment. Upstream core sets an inline style on the card via that prop which is what allows smooth
      // column count transitions. Without it, only the CSS zoom classes apply (giving fixed breakpoints like 320 / 640px)
      // causing several zoom levels to visually collapse into the same layout (your observed 3-across).
      //
      // To mirror upstream behavior without hacking core CSS, we wrap each SceneCard in a simple div that we control.
      // This wrapper gets the computed dynamic width, while the inner SceneCard still receives zoomIndex so internal
      // height / overlay sizing behaves identically to the native page.
      const children = paginatedScenes.map((s:any,i:number) => {
        if (!FORCE_FALLBACK && SceneCard) {
          // Wrapper purely for keying; display: contents prevents creating an extra layout box.
          return React.createElement('div', { key:s.id+'_'+i, className:'ai-rec-card-wrapper', style:{display:'contents'} },
            React.createElement(SceneCard, { scene:s, zoomIndex, queue: undefined, index: i })
          );
        }
        return React.createElement('div', { key:s.id+'_'+i, className:'ai-rec-card-wrapper', style:{display:'contents'} }, SceneCardFallback(s));
      });

      // Apply CSS variable for width so real SceneCard root can size itself; inject stylesheet once.
      if (typeof document !== 'undefined' && !(document.getElementById('ai-rec-grid-style'))) {
        const styleEl = document.createElement('style');
        styleEl.id = 'ai-rec-grid-style';
        styleEl.textContent = `.ai-rec-grid .scene-card { width: var(--ai-card-width) !important; }`;
        document.head.appendChild(styleEl);
      }

      // Use native row negative margins so measured width matches upstream (container padding 15px each side, row -15px margins)
      return React.createElement('div', { className:'row ai-rec-grid d-flex flex-wrap', ref:componentRef, style:{gap:0, ['--ai-card-width' as any]: cardWidth+"px"}}, children);
    }, [loading, componentsLoading, error, paginatedScenes, SceneCard, cardWidth, zoomIndex]);

    // Post-render width verification & diagnostics (AIDebug only)
    // Margin detection + verification logging
    useEffect(() => {
      if (!componentRef.current) return;
      const firstCard = componentRef.current.querySelector('.scene-card') as HTMLElement | null;
      if (firstCard) {
        const cs = window.getComputedStyle(firstCard);
        const ml = parseFloat(cs.marginLeft)||0; const mr = parseFloat(cs.marginRight)||0;
        const total = ml + mr;
        if (total && Math.abs(total - cardMarginLR) > 0.5) { // update if changed significantly
          setCardMarginLR(total);
        }
      }
      if (!(w as any).AIDebug) return;
      if (cardWidth === undefined) return;
      const meta = (calculateCardWidth as any)._last || {};
      console.log('[RecommendedScenes][verify]', meta);
    }, [cardWidth, paginatedScenes, zoomIndex, containerWidth, cardMarginLR]);

    function SceneCardFallback(s:any){
      return React.createElement('div', { className:'scene-card stub', style:{background:'#1e1f22', border:'1px solid #333', borderRadius:4, padding:6}}, [
        React.createElement('div',{key:'img', style:{background:'#2a2d30', height:90, marginBottom:6, display:'flex', alignItems:'center', justifyContent:'center', fontSize:12, color:'#777'}}, 'Scene '+s.id),
        React.createElement('div',{key:'title', style:{fontSize:12, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}, s.title || ('ID '+s.id))
      ]);
    }

    // Pagination controls (reuse class names for theming)
    function paginationBar(position:'top'|'bottom'){
      const outerClass = position==='bottom' ? 'pagination-footer' : 'scene-list-header';
      return React.createElement('div', { key:'pag-'+position, className: outerClass, style:{marginTop: position==='bottom'?16:0}},
        React.createElement('div',{className:'d-flex w-100 align-items-center justify-content-between', style:{gap:12}},[
          React.createElement('div',{key:'controls', className:'btn-group'}, [
            React.createElement(Button,{key:'first', className:'minimal', disabled:page<=1, onClick:()=>setPage(1)}, '«'),
            React.createElement(Button,{key:'prev', className:'minimal', disabled:page<=1, onClick:()=>setPage((p:any)=>Math.max(1,p-1))}, '‹'),
            React.createElement('span',{key:'pi', className:'pagination-index', style:{padding:'4px 8px', fontSize:12}}, `${page} of ${totalPages}`),
            React.createElement(Button,{key:'next', className:'minimal', disabled:page>=totalPages, onClick:()=>setPage((p:any)=>Math.min(totalPages,p+1))}, '›'),
            React.createElement(Button,{key:'last', className:'minimal', disabled:page>=totalPages, onClick:()=>setPage(totalPages)}, '»')
          ]),
          React.createElement('div',{key:'range', style:{fontSize:12, opacity:.85}}, `${startIndex}-${endIndex} of ${filteredScenes.length}`)
        ])
      );
    }

  const toolbarInner = React.createElement('div', { className:'scene-list-toolbar btn-toolbar d-flex flex-wrap align-items-center', style:{gap:8}}, [
      // Items per page
      React.createElement('div',{key:'ipp', className:'page-size-select'}, React.createElement('select',{className:'form-control', value:itemsPerPage, onChange:(e:any)=>{setItemsPerPage(Number(e.target.value)); setPage(1);}}, [20,40,80,120].map(n=> React.createElement('option',{key:n, value:n}, n)))),
      // Zoom slider
      React.createElement('div',{key:'zoom', className:'d-flex align-items-center', style:{gap:6, minWidth:160}}, [
        React.createElement('span',{key:'zl', style:{fontSize:12, opacity:.7, whiteSpace:'nowrap'}}, 'Zoom'),
        React.createElement('input',{key:'zr', className:'form-range', style:{width:110}, type:'range', min:0, max:3, value:zoomIndex, onChange:(e:any)=>setZoomIndex(Number(e.target.value))})
      ]),
      // Algorithm select
      React.createElement('div',{key:'alg', style:{minWidth:140}}, React.createElement('select', {className:'form-control', value:algorithm, onChange:(e:any)=>setAlgorithm(e.target.value)}, [
        React.createElement('option',{key:'similarity', value:'similarity'},'Similarity'),
        React.createElement('option',{key:'recent', value:'recent'},'Recent'),
        React.createElement('option',{key:'popular', value:'popular'},'Popular')
      ])),
      // Min score
      React.createElement('div',{key:'minscore', style:{width:90}}, React.createElement('input',{className:'form-control', type:'number', step:0.05, min:0, max:1, value:minScore, onChange:(e:any)=>setMinScore(Number(e.target.value))})),
      // Actions
      React.createElement('div',{key:'actions', className:'btn-group'}, [
        React.createElement(Button,{key:'refresh', className:'minimal', disabled:loading, onClick:()=>{ const reshuffled=[...sceneIds].sort(()=>Math.random()-0.5); setSceneIds(reshuffled); fetchScenes(reshuffled); }}, loading?'…':'↻'),
        React.createElement(Button,{key:'reset', className:'minimal', onClick:()=>{ setAlgorithm('similarity'); setItemsPerPage(40); setMinScore(0.5); setZoomIndex(1); setSceneIds(TEST_SCENE_IDS); fetchScenes(TEST_SCENE_IDS); setPage(1); }}, 'Reset')
      ])
    ]);

    const toolbar = React.createElement('div', { key:'tbwrap', className:'scene-list-header', style:{marginBottom:4}},
      React.createElement('div',{className:'d-flex w-100 align-items-center justify-content-between flex-wrap', style:{rowGap:8, columnGap:12}},[
        React.createElement('h2',{key:'h', style:{margin:0, fontSize:'1.35rem'}},'Recommended Scenes'),
        toolbarInner
      ])
    );

    return React.createElement('div', { className:'recommended-scenes-page scene-list', style:{padding:'0 15px 24px'}}, [
      toolbar,
      paginationBar('top'),
      grid,
      paginationBar('bottom')
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
      const node = React.createElement(
        'div',
        { key:'recommended-scenes-link', className:'col-4 col-sm-3 col-md-2 col-lg-auto' },
        NavLink ? (
          React.createElement(NavLink, {
            exact: true,
            to: ROUTE,
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
