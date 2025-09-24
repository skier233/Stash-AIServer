// Recommended Scenes (Full UI Restored w/ cleanup)
// Visual parity with the richer version you preferred:
//  • Algorithm & min score controls
//  • Zoom slider + dynamic card width (upstream parity logic)
//  • Native‑style pagination (top + bottom) w/ dropdown
//  • Duration + size aggregate stats
//  • Adaptive GraphQL fetch & schema pruning
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
  // const GQL = {} as any; // (legacy GraphQL client removed)
  
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
  // Use CSS variables for layout values
  const root = typeof window !== 'undefined' ? window.getComputedStyle(document.documentElement) : null;
  const containerPadding = root ? parseFloat(root.getPropertyValue('--ai-rec-container-padding')) : 30;
  const cardMargin = root ? parseFloat(root.getPropertyValue('--ai-rec-card-margin')) : 10;
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
  //

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

  //

  //

  const RecommendedScenesPage: any = () => {
    function readInitial(key:string, urlParam:string, fallback:number){
      try { const usp = new URLSearchParams(location.search); const v = usp.get(urlParam); if(v!=null){ const n=parseInt(v,10); if(!isNaN(n)) return n; } } catch(_){ }
      try { const raw = localStorage.getItem(key); if(raw!=null){ const n=parseInt(raw,10); if(!isNaN(n)) return n; } } catch(_){ }
      return fallback;
    }
  //
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
  //
  const [backendStatus, setBackendStatus] = useState('idle' as 'idle'|'loading'|'ok'|'error');
  const [discoveryAttempted, setDiscoveryAttempted] = useState(false);
  const pageAPI:any = (w as any).AIPageContext; // for contextual recommendation requests

  // ---------------- Config State (per recommender) -----------------
  const [configValues, setConfigValues] = useState({} as any);
  const configCacheRef = useRef({} as any);
  const configValuesRef = useRef({} as any);
  const [showConfig, setShowConfig] = useState(true as any);
  // Generic tick to force config panel rerender (for tag mode changes)
  const [configRerenderTick, setConfigRerenderTick] = useState(0);
  function forceConfigRerender(){ setConfigRerenderTick((t:number)=> t+1); }
  const textDebounceTimersRef = useRef({} as any);
  const compositeRawRef = useRef({} as any); // raw text for tags/performers inputs
  useEffect(()=>{ (configValuesRef as any).current = configValues; }, [configValues]);
  const currentRecommender = React.useMemo(()=> (recommenders||[])?.find((r:any)=> r.id===recommenderId), [recommenders, recommenderId]);

  // ---------------- Tag Include/Exclude Selector (Unified) -----------------
  // Sole implementation: single bar with inline mode toggle (+ include / - exclude) and chips inline.
  // Enhanced Constraint Editor Component with auto-save and advanced co-occurrence support
  const ConstraintEditor = React.useCallback(({ tagId, constraint, tagName, value, fieldName, onSave, onCancel, allowedConstraintTypes }: any) => {
    const [localConstraint, setLocalConstraint] = React.useState(constraint);

    // Reset local state when constraint prop changes (e.g., when switching constraint types)
    React.useEffect(() => {
      setLocalConstraint(constraint);
    }, [constraint]);

    const allConstraintTypes = [
      { value: 'presence', label: 'Include/Exclude' },
      { value: 'duration', label: 'Duration Filter' },
      { value: 'overlap', label: 'Co-occurrence' },
      { value: 'importance', label: 'Importance Weight' }
    ];

    // If the backend supplied allowedConstraintTypes, filter available types accordingly
    const constraintTypes = Array.isArray(allowedConstraintTypes) && allowedConstraintTypes.length > 0
      ? allConstraintTypes.filter(ct => allowedConstraintTypes.includes(ct.value))
      : allConstraintTypes;

    // Memoize expensive tag computations for overlap constraint
    const overlapTagData = React.useMemo(() => {
      if (localConstraint.type !== 'overlap') return { allCoOccurrencePrimaries: new Set(), availableTags: [] };
      
      // Get all currently selected tags (include + exclude) for co-occurrence selection
      // Exclude primary tags from other co-occurrence groups
      const allCoOccurrencePrimaries = new Set();
      [...(value?.include || []), ...(value?.exclude || [])].forEach(id => {
        const constraint = (value?.constraints || {})[id] || { type: 'presence' };
        if (constraint.type === 'overlap' && constraint.overlap?.coTags?.length > 0 && id !== tagId) {
          allCoOccurrencePrimaries.add(id);
        }
      });
      const availableTags = [...(value?.include || []), ...(value?.exclude || [])]
        .filter(id => id !== tagId && !allCoOccurrencePrimaries.has(id));
      
      return { allCoOccurrencePrimaries, availableTags };
    }, [localConstraint.type, value?.include, value?.exclude, value?.constraints, tagId]);

    function handleTypeChange(newType: string) {
      let newConstraint: any = { type: newType };
      
      // Initialize default values for each constraint type
      switch(newType) {
        case 'presence':
          newConstraint.presence = 'include';
          break;
        case 'duration':
          newConstraint.duration = { min: 10, max: 60, unit: 'percent' };
          break;
        case 'overlap':
          newConstraint.overlap = { minDuration: 5, maxDuration: 30, unit: 'percent' };
          break;
        case 'importance':
          newConstraint.importance = 0.5;
          break;
      }
      
      setLocalConstraint(newConstraint);
    }

    function renderOptions() {
      switch(localConstraint.type) {
        case 'presence':
          return React.createElement('div', { className: 'constraint-options' }, [
            React.createElement('label', { key: 'label' }, 'Mode: '),
            React.createElement('select', { 
              key: 'select',
              value: localConstraint.presence || 'include',
              onChange: (e: any) => setLocalConstraint((prev: any) => ({ ...prev, presence: e.target.value }))
            }, [
              React.createElement('option', { key: 'inc', value: 'include' }, 'Include'),
              React.createElement('option', { key: 'exc', value: 'exclude' }, 'Exclude')
            ])
          ]);
        case 'duration':
          return React.createElement('div', { className: 'constraint-options' }, [
            React.createElement('div', { key: 'range' }, [
              React.createElement('label', { key: 'label' }, 'Duration: '),
              React.createElement('input', { 
                key: 'min', type: 'number', placeholder: 'Min',
                value: localConstraint.duration?.min || '',
                onChange: (e: any) => setLocalConstraint((prev: any) => ({ 
                  ...prev, 
                  duration: { ...prev.duration, min: e.target.value ? Number(e.target.value) : undefined }
                }))
              }),
              React.createElement('span', { key: 'dash' }, ' - '),
              React.createElement('input', { 
                key: 'max', type: 'number', placeholder: 'Max',
                value: localConstraint.duration?.max || '',
                onChange: (e: any) => setLocalConstraint((prev: any) => ({ 
                  ...prev, 
                  duration: { ...prev.duration, max: e.target.value ? Number(e.target.value) : undefined }
                }))
              })
            ]),
            React.createElement('div', { key: 'unit' }, [
              React.createElement('label', { key: 'label' }, 'Unit: '),
              React.createElement('select', { 
                key: 'select',
                value: localConstraint.duration?.unit || 'percent',
                onChange: (e: any) => setLocalConstraint((prev: any) => ({ 
                  ...prev, 
                  duration: { ...prev.duration, unit: e.target.value }
                }))
              }, [
                React.createElement('option', { key: 'pct', value: 'percent' }, '% of video'),
                React.createElement('option', { key: 'sec', value: 'seconds' }, 'Seconds')
              ])
            ])
          ]);
        case 'overlap':
          // Use memoized tag data to avoid expensive recomputation on every render
          const { availableTags } = overlapTagData;
          const selectedCoTags = localConstraint.overlap?.coTags || [];
          const entity = localConstraint._entity || 'tag';
          
          return React.createElement('div', { className: 'constraint-options' }, [
            React.createElement('div', { key: 'info' }, 'Co-occurrence with other selected tags'),
            React.createElement('div', { key: 'tags-section' }, [
              React.createElement('label', { key: 'label' }, 'Selected for co-occurrence: '),
              React.createElement('div', { key: 'selected-tags', className: 'constraint-selected-tags' }, 
                selectedCoTags.length > 0 ? selectedCoTags.map((coTagId: number) => {
                  const nameMap = (compositeRawRef.current[fieldName + '__' + entity + 'NameMap'] || {});
                  const coTagName = nameMap[coTagId] || `${entity === 'performer' ? 'Performer' : 'Tag'} ${coTagId}`;
                  return React.createElement('span', { 
                    key: coTagId, 
                    className: 'constraint-cochip-tag'
                  }, [
                    coTagName,
                    React.createElement('button', {
                      key: 'remove',
                      onClick: () => {
                        const newCoTags = selectedCoTags.filter((id: number) => id !== coTagId);
                        setLocalConstraint((prev: any) => ({ 
                          ...prev, 
                          overlap: { ...prev.overlap, coTags: newCoTags }
                        }));
                      },
                      className: 'constraint-cochip-remove'
                    }, '×')
                  ]);
                }) : React.createElement('span', { className: 'constraint-selected-empty' }, 'No tags selected for co-occurrence')
              ),
              availableTags.length > 0 ? React.createElement('div', { key: 'available-tags', className: 'constraint-available-tags' }, 
                availableTags.map((coTagId: number) => {
                  const nameMap = (compositeRawRef.current[fieldName + '__' + entity + 'NameMap'] || {});
                  const coTagName = nameMap[coTagId] || `${entity === 'performer' ? 'Performer' : 'Tag'} ${coTagId}`;
                  const isSelected = selectedCoTags.includes(coTagId);
                  if (isSelected) return null; // Don't show already selected tags
                  return React.createElement('button', { 
                    key: coTagId, 
                    onClick: () => {
                      const newCoTags = [...selectedCoTags, coTagId];
                      setLocalConstraint((prev: any) => ({ 
                        ...prev, 
                        overlap: { ...prev.overlap, coTags: newCoTags }
                      }));
                    },
                    className: 'constraint-tag-button'
                  }, coTagName);
                })
              ) : null
            ]),
            React.createElement('div', { key: 'range' }, [
              React.createElement('label', { key: 'label' }, 'Overlap duration: '),
              React.createElement('input', { 
                key: 'min', type: 'number', placeholder: 'Min',
                value: localConstraint.overlap?.minDuration || '',
                onChange: (e: any) => setLocalConstraint((prev: any) => ({ 
                  ...prev, 
                  overlap: { ...prev.overlap, minDuration: e.target.value ? Number(e.target.value) : undefined }
                }))
              }),
              React.createElement('span', { key: 'dash' }, ' - '),
              React.createElement('input', { 
                key: 'max', type: 'number', placeholder: 'Max',
                value: localConstraint.overlap?.maxDuration || '',
                onChange: (e: any) => setLocalConstraint((prev: any) => ({ 
                  ...prev, 
                  overlap: { ...prev.overlap, maxDuration: e.target.value ? Number(e.target.value) : undefined }
                }))
              })
            ]),
            React.createElement('div', { key: 'unit' }, [
              React.createElement('label', { key: 'label' }, 'Unit: '),
              React.createElement('select', { 
                key: 'select',
                value: localConstraint.overlap?.unit || 'percent',
                onChange: (e: any) => setLocalConstraint((prev: any) => ({ 
                  ...prev, 
                  overlap: { ...prev.overlap, unit: e.target.value }
                }))
              }, [
                React.createElement('option', { key: 'pct', value: 'percent' }, '% of video'),
                React.createElement('option', { key: 'sec', value: 'seconds' }, 'Seconds')
              ])
            ])
          ]);
        case 'importance':
          return React.createElement('div', { className: 'constraint-options' }, [
            React.createElement('label', { key: 'label' }, 'Weight (0.0 - 1.0): '),
            React.createElement('input', { 
              key: 'input', type: 'number', step: '0.1', min: '0', max: '1',
              value: localConstraint.importance || 0.5,
              onChange: (e: any) => setLocalConstraint((prev: any) => ({ ...prev, importance: Number(e.target.value) }))
            })
          ]);
        default:
          return null;
      }
    }

    // Auto-save on unmount (click-out) without saving on every change
    const localConstraintRef = React.useRef(localConstraint);
    const canceledRef = React.useRef(false);
    React.useEffect(()=>{ localConstraintRef.current = localConstraint; }, [localConstraint]);

    React.useEffect(()=>{
      // save once on unmount unless canceled via Escape
      return ()=>{
        try { if(!canceledRef.current) onSave(localConstraintRef.current); } catch(e){}
      };
    }, [onSave]);

    React.useEffect(()=>{
      function onKey(e:any){ if(e.key === 'Escape') { canceledRef.current = true; onCancel(); } }
      document.addEventListener('keydown', onKey);
      return ()=> document.removeEventListener('keydown', onKey);
    }, [onCancel]);

    return React.createElement('div', { className: 'constraint-popup' }, [
      React.createElement('div', { key: 'title', className: 'constraint-title' }, `Configure: ${tagName}`),
      React.createElement('div', { key: 'type', className: 'constraint-type' }, [
        React.createElement('label', { key: 'label' }, 'Type: '),
        React.createElement('select', { 
          key: 'select',
          value: localConstraint.type,
          onChange: (e: any) => handleTypeChange(e.target.value)
        }, constraintTypes.map(ct => React.createElement('option', { key: ct.value, value: ct.value }, ct.label)))
      ]),
      renderOptions(),
      React.createElement('div', { key: 'actions', className: 'constraint-actions' }, [
        React.createElement('button', {
          key: 'save',
          className: 'btn-constraint btn-save',
          onClick: (e: any) => {
            e.stopPropagation();
            onSave(localConstraint);
          },
          title: 'Save changes'
        }, 'Save')
      ])
    ]);
  }, []);

  const TagIncludeExclude = ({ value, onChange, fieldName, initialTagCombination, allowedConstraintTypes, allowedCombinationModes, entity = 'tag' }: { value:any; onChange:(next:any)=>void; fieldName:string; initialTagCombination?: string; allowedConstraintTypes?: string[]; allowedCombinationModes?: string[]; entity?: 'tag'|'performer' }) => {
    const v = value || {};
    const include:number[] = Array.isArray(v) ? v : Array.isArray(v.include) ? v.include : [];
    const exclude:number[] = Array.isArray(v) ? [] : Array.isArray(v.exclude) ? v.exclude : [];
    
    // Enhanced value structure for constraints
    const constraints = v.constraints || {};
    
    // Use React state instead of ref-based state to avoid focus issues
    // Determine allowed combination modes: default to ['and','or'] unless field restricts.
    // Normalize and resolve allowed modes: prefer explicit allowedCombinationModes; else use initialTagCombination; else default ['and','or'].
    const normalizeMode = (m:any)=> (m==null? undefined : String(m).toLowerCase());
    const allowedNorm = Array.isArray(allowedCombinationModes) && allowedCombinationModes.length > 0
      ? allowedCombinationModes.map(normalizeMode).filter(Boolean) as string[]
      : [];
    const initLC = typeof initialTagCombination === 'string' ? normalizeMode(initialTagCombination) : undefined;
    const resolvedAllowedModes = (allowedNorm.length > 0 ? allowedNorm : (typeof initLC !== 'undefined' ? [initLC] : ['and','or'])) as ('and'|'or'|'not-applicable')[];
    // Determine initial mode from provided value or defaults; treat null/undefined/invalid as first allowed.
    const rawValueMode:any = (v && Object.prototype.hasOwnProperty.call(v,'tag_combination')) ? v.tag_combination : undefined;
    const valueMode = normalizeMode(rawValueMode);
    const isValidMode = (m:any)=> m==='and' || m==='or' || m==='not-applicable';
    const initialMode = (isValidMode(valueMode) ? valueMode : (isValidMode(initLC) ? initLC : resolvedAllowedModes[0])) as 'and'|'or'|'not-applicable';
    const [searchState, setSearchState] = React.useState({
      search: '',
      suggestions: [] as any[],
      loading: false,
      error: null as string|null,
      showDropdown: false,
      combinationMode: initialMode
    });
    // Debug: log initial props and resolved modes to help diagnose behavior
    // (Debug log removed)

    // Instance id for coordinating dropdowns between multiple tag selectors on the page
    const instanceIdRef = React.useRef(null as any);
    if(!instanceIdRef.current){
      try { (w as any).__aiTagFallbackCounter = ((w as any).__aiTagFallbackCounter || 0) + 1; instanceIdRef.current = (w as any).__aiTagFallbackCounter; } catch(e){ instanceIdRef.current = Math.floor(Math.random()*1000000); }
    }

    // When any instance opens, other instances should close their dropdowns
    React.useEffect(()=>{
      function onOtherOpen(ev:any){
        try{
          const otherId = ev && ev.detail && ev.detail.id;
          const myId = instanceIdRef.current;
          if((w as any).AIDebug) {
            console.log('[TagFallback] Received open event. Other ID:', otherId, 'My ID:', myId);
          }
          if(otherId && otherId !== myId){
            if((w as any).AIDebug) {
              console.log('[TagFallback] Closing dropdown for instance', myId);
            }
            setSearchState((prev:any)=> ({ ...prev, showDropdown:false }));
          }
        }catch(e){
          if((w as any).AIDebug) {
            console.warn('[TagFallback] Error handling open event:', e);
          }
        }
      }
      document.addEventListener('ai-tag-fallback-open', onOtherOpen as any);
      return ()=> document.removeEventListener('ai-tag-fallback-open', onOtherOpen as any);
    }, []);

    // Sync combinationMode from external value changes (persisted value may arrive asynchronously)
    React.useEffect(()=>{
      const externalModeRaw = v && Object.prototype.hasOwnProperty.call(v,'tag_combination') ? v.tag_combination : undefined;
      const externalMode = normalizeMode(externalModeRaw);
      if(externalMode && externalMode !== searchState.combinationMode && (externalMode==='and' || externalMode==='or' || externalMode==='not-applicable')){
        setSearchState((prev:any)=> ({ ...prev, combinationMode: externalMode }));
        if((w as any).AIDebug) {
          console.log('[TagFallback] synced combinationMode from value:', externalMode);
        }
      }
    }, [v && (v as any).tag_combination]);
    
  const [constraintPopup, setConstraintPopup] = React.useState(null as any);
    
    const nameMapKey = fieldName + '__' + (entity === 'performer' ? 'performerNameMap' : 'tagNameMap');
    if(!compositeRawRef.current[nameMapKey]){
      compositeRawRef.current[nameMapKey] = {};
    }
    const tagNameMap = compositeRawRef.current[nameMapKey];
    // Helper to look up a name for an id for a given entity (falls back to "Performer {id}" or "Tag {id}")
    function lookupName(id: number, forEntity?: 'tag'|'performer'){
      const ent = forEntity || entity || 'tag';
      const key = fieldName + '__' + (ent === 'performer' ? 'performerNameMap' : 'tagNameMap');
      const map = compositeRawRef.current[key] || {};
      return map[id] || (ent === 'performer' ? `Performer ${id}` : `Tag ${id}`);
    }
  const debounceTimerRef = React.useRef(null as any);

    function removeTag(id:number, list:'include'|'exclude'){
      const nextInclude = list==='include'? include.filter((i:any)=>i!==id): include;
      const nextExclude = list==='exclude'? exclude.filter((i:any)=>i!==id): exclude;
      // Also remove constraints for this tag
      const nextConstraints = { ...constraints };
      delete nextConstraints[id];
      onChange({ include: nextInclude, exclude: nextExclude, constraints: nextConstraints, tag_combination: searchState.combinationMode });
    }

    function updateTagConstraint(tagId: number, constraint: any) {
      const nextConstraints = { ...constraints };
      let nextInclude = [...include];
      let nextExclude = [...exclude];
      nextConstraints[tagId] = constraint;
      
      // If this is overlap with coTags, make sure those co-occurrence tags are included so they get hydrated
      if (constraint.type === 'overlap' && constraint.overlap && constraint.overlap.coTags) {
        constraint.overlap.coTags.forEach((coTagId: number) => {
          if (!nextInclude.includes(coTagId) && !nextExclude.includes(coTagId)) {
            nextInclude.push(coTagId);
          }
        });
      }
      
      // If this is a presence constraint, ensure tag is placed in the right set and removed from the other
      if (constraint.type === 'presence') {
        // remove from both then add to the selected list
        nextInclude = nextInclude.filter(id => id !== tagId);
        nextExclude = nextExclude.filter(id => id !== tagId);
        if (constraint.presence === 'exclude') {
          nextExclude.push(tagId);
        } else {
          nextInclude.push(tagId);
        }
        // store constraint and persist
        nextConstraints[tagId] = constraint;
        onChange({ include: nextInclude, exclude: nextExclude, constraints: nextConstraints, tag_combination: searchState.combinationMode });
        return;
      }

      // If this is an overlap constraint with coTags, remove those coTags from include/exclude lists
      if (constraint.type === 'overlap' && constraint.overlap && constraint.overlap.coTags) {
        const coTags = constraint.overlap.coTags;
        nextInclude = nextInclude.filter(id => !coTags.includes(id));
        nextExclude = nextExclude.filter(id => !coTags.includes(id));
        
        // Also remove constraints for the co-occurrence tags since they're now part of this tag's constraint
        coTags.forEach((coTagId: number) => {
          delete nextConstraints[coTagId];
        });
      }
      
      if((w as any).AIDebug) {
        console.log('New constraints object:', { include: nextInclude, exclude: nextExclude, constraints: nextConstraints });
      }
      // Ensure primary tag is present in include list for non-presence constraints
      if (!nextInclude.includes(tagId) && !nextExclude.includes(tagId)) {
        nextInclude.push(tagId);
      }
      onChange({ include: nextInclude, exclude: nextExclude, constraints: nextConstraints, tag_combination: searchState.combinationMode });
    }

    function getTagConstraint(tagId: number) {
      const constraint = constraints[tagId] || { type: 'presence', presence: include.includes(tagId) ? 'include' : 'exclude' };
      if((w as any).AIDebug) {
        console.log('Getting constraint for tag', tagId, ':', constraint);
      }
      return constraint;
    }

    function showConstraintPopup(tagId: number, event: any, popupEntity?: 'tag'|'performer') {
      const rect = event.target.getBoundingClientRect();
      setConstraintPopup({
        tagId,
        entity: popupEntity || entity,
        position: { x: rect.left, y: rect.bottom + 5 }
      });
      event.stopPropagation();
    }

    // Use a ref for the tag input for focus and positioning
  const tagInputRef = React.useRef(null) as React.RefObject<HTMLInputElement>;
    function addTag(id:number, name?:string){
      const supportsPresence = !Array.isArray(allowedConstraintTypes) || allowedConstraintTypes.length===0 || allowedConstraintTypes.includes('presence');
      if(!supportsPresence){
        const preferredType = (Array.isArray(allowedConstraintTypes) && allowedConstraintTypes.length>0) ? allowedConstraintTypes[0] : 'overlap';
        const init = { type: preferredType } as any;
        if(preferredType === 'presence') init.presence = 'include';
        if(preferredType === 'duration') init.duration = { min: 10, max: 60, unit: 'percent' };
        if(preferredType === 'overlap') init.overlap = { minDuration: 5, maxDuration: 30, unit: 'percent', coTags: [] };
        if(preferredType === 'importance') init.importance = 0.5;
        let position = { x: window.innerWidth/2 - 100, y: window.innerHeight/2 - 80 };
        if (tagInputRef.current) {
          const rect = tagInputRef.current.getBoundingClientRect();
          position = { x: rect.left, y: rect.bottom + 5 };
        }
        setConstraintPopup({ 
          tagId: id, 
          entity: entity,
          position,
          initialConstraint: init 
        });
        if(name) tagNameMap[id] = name;
        return;
      }
      if(!include.includes(id) && !exclude.includes(id)) {
        onChange({ include: [...include,id], exclude, constraints, tag_combination: searchState.combinationMode });
      }
      if(name) {
        tagNameMap[id] = name;
      }
      if(debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
      setSearchState((prev: any) => ({ ...prev, search:'', suggestions:[], showDropdown:false }));
    }

    function search(term:string){
      if(debounceTimerRef.current) clearTimeout(debounceTimerRef.current);
      setSearchState((prev: any) => ({ ...prev, search: term }));
      const q = term.trim();
      const immediate = q === '';
      const run = async () => {
        setSearchState((prev: any) => ({ ...prev, loading:true, error:null }));
        try {
          let gql: string;
          if(entity === 'performer'){
            gql = q
              ? `query PerformerSuggest($term: String!) { findPerformers(filter: { per_page: 20 }, performer_filter: { name: { value: $term, modifier: INCLUDES } }) { performers { id name } } }`
              : `query PerformerSuggest { findPerformers(filter: { per_page: 20 }) { performers { id name } } }`;
          } else {
            gql = q
              ? `query TagSuggest($term: String!) { findTags(filter: { per_page: 20 }, tag_filter: { name: { value: $term, modifier: INCLUDES } }) { tags { id name } } }`
              : `query TagSuggest { findTags(filter: { per_page: 20 }) { tags { id name } } }`;
          }
          const variables = q ? { term: q } : {};
          const res = await fetch('/graphql', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({query:gql, variables}) });
          if(!res.ok) throw new Error('HTTP '+res.status);
          const json = await res.json();
          if(json.errors){ throw new Error(json.errors.map((e:any)=> e.message).join('; ')); }
          const suggestions = entity === 'performer' ? (json?.data?.findPerformers?.performers || []) : (json?.data?.findTags?.tags || []);
          // populate name map so other UI (co-occurrence chips, popups) shows proper names
          try {
            suggestions.forEach((s:any) => { const sid = parseInt(s.id,10); if(!isNaN(sid)) tagNameMap[sid] = s.name; });
          } catch(e){}
          setSearchState((prev: any) => ({ ...prev, suggestions, loading:false, error: suggestions.length? null: null }));
        } catch(e:any){ setSearchState((prev: any) => ({ ...prev, error: 'Search failed', loading:false })); }
      };
      if(immediate){
        run();
      } else {
        debounceTimerRef.current = setTimeout(run, 200);
      }
    }

    function onInputFocus(){
      if(!searchState.showDropdown){
        try { document.dispatchEvent(new CustomEvent('ai-tag-fallback-open', { detail: { id: instanceIdRef.current } })); } catch(e){}
        setSearchState((prev:any)=> ({ ...prev, showDropdown:true }));
        if(!searchState.suggestions.length && !searchState.loading){ search(''); }
      }
    }

    // Close dropdown when clicking outside
    React.useEffect(() => {
      function handleClickOutside(event: Event) {
        const target = event.target as Element;
        if (!target.closest('.ai-tag-fallback.unified')) {
          setSearchState((prev: any) => ({ ...prev, showDropdown: false }));
        }
        // Close constraint popup when clicking outside
        if (!target.closest('.constraint-popup') && !target.closest('.constraint-btn')) {
          setConstraintPopup(null);
        }
      }
      if (searchState.showDropdown || constraintPopup) {
        document.addEventListener('click', handleClickOutside);
        return () => document.removeEventListener('click', handleClickOutside);
      }
    }, [searchState.showDropdown, constraintPopup]);

    const chips: any[] = [];
    const processedOverlapGroups = new Set();
    
    // Helper function to create co-occurrence group chip
    function createCoOccurrenceChip(primaryId: number, group: any, setType: 'include' | 'exclude', chipEntity: 'tag'|'performer' = 'tag') {
      const primaryName = lookupName(primaryId, chipEntity);
      const coTags = group.coTags || [];
      const allTagIds = [primaryId, ...coTags];
      const allTagNames = allTagIds.map((id: number) => lookupName(id, chipEntity));
      
      const min = group.minDuration || 0;
      const max = group.maxDuration || '∞';
      const unit = group.unit === 'percent' ? '%' : 's';
      
      const chipClass = `tag-chip overlap ${setType}`;
      const groupKey = allTagIds.sort().join('-');
      
      return React.createElement('span', { key: `co-${setType}-${groupKey}`, className: `${chipClass} co-chip` }, [
        React.createElement('span', { key: 'constraint-prefix', className: 'co-constraint-info' }, `[${min}-${max}${unit}]`),
        React.createElement('span', { key: 'tags', className: 'co-tags' }, 
          allTagNames.map((name, idx) => 
            React.createElement('span', { 
              key: allTagIds[idx], 
              className: 'co-tag-item'
            }, [
              React.createElement('span', { key: 'n', className: 'co-tag-name', title: name }, name),
              React.createElement('button', {
                key: 'x',
                onClick: (e: any) => {
                  e.stopPropagation();
                  const tagIdToRemove = allTagIds[idx];
                  if (tagIdToRemove === primaryId) {
                    removeTag(primaryId, setType);
                  } else {
                    const updatedCoTags = coTags.filter((id: number) => id !== tagIdToRemove);
                    updateTagConstraint(primaryId, {
                      type: 'overlap',
                      overlap: { ...group, coTags: updatedCoTags }
                    });
                  }
                },
                className: 'co-tag-remove',
                title: `Remove ${name} from group`
              }, '×')
            ])
          )
        ),
        React.createElement('span', { key: 'actions', className: 'co-actions' }, [
          React.createElement('button', { 
            key: 'gear', 
            className: 'constraint-btn', 
            onClick: (e: any) => showConstraintPopup(primaryId, e, entity), 
            title: 'Configure group constraint'
          }, '⚙'),
          React.createElement('button', { 
            key: 'remove-group', 
            onClick: (e: any) => { 
              e.stopPropagation(); 
              removeTag(primaryId, setType); 
            }, 
            className: 'co-chip-remove',
            title: 'Remove entire group'
          }, '×')
        ])
      ]);
    }
    
    include.forEach(id=> {
      const constraint = getTagConstraint(id);
      
      // Skip if this tag is part of a co-occurrence group already processed, or if it's ANY overlap constraint
      if (constraint.type === 'overlap' && constraint.overlap) {
        const coTags = constraint.overlap.coTags || [];
        const groupKey = [id, ...coTags].sort().join('-');
        if (processedOverlapGroups.has(groupKey)) {
          return; // Skip, already rendered as part of the group
        }
        processedOverlapGroups.add(groupKey);
  chips.push(createCoOccurrenceChip(id, constraint.overlap, 'include', entity));
        return;
      }
      
  const tagName = lookupName(id, entity);
      const chipClass = `tag-chip ${constraint.type === 'presence' ? 'include' : constraint.type}`;
      
      // Add constraint indicator text
      let constraintText = '';
      if (constraint.type === 'duration' && constraint.duration) {
        const min = constraint.duration.min || 0;
        const max = constraint.duration.max || '∞';
        const unit = constraint.duration.unit === 'percent' ? '%' : 's';
        constraintText = ` [${min}-${max}${unit}]`;
      } else if (constraint.type === 'importance' && constraint.importance !== undefined) {
        constraintText = ` [×${constraint.importance.toFixed(1)}]`;
      }
      
      chips.push(React.createElement('span',{ key:'i'+id, className: `${chipClass} tag-chip-flex` }, [
        React.createElement('span', { key: 'text', className: 'tag-chip-text' }, tagName),
        constraintText ? React.createElement('span', { key: 'constraint', className: 'tag-chip-constraint' }, constraintText) : null,
        React.createElement('div', { key: 'actions', className: 'tag-chip-actions' }, [
          React.createElement('button',{ key:'gear', className:'constraint-btn', onClick:(e:any)=> showConstraintPopup(id, e, entity), title:'Configure constraint' }, '⚙'),
          React.createElement('button',{ key:'x', onClick:(e:any)=>{ e.stopPropagation(); removeTag(id,'include'); }, title:'Remove', className: 'tag-chip-remove' }, '×')
        ])
      ].filter(Boolean)));
    });
    exclude.forEach(id=> {
      const constraint = getTagConstraint(id);
      
      // Skip if this tag is part of a co-occurrence group already processed, or if it's ANY overlap constraint
      if (constraint.type === 'overlap' && constraint.overlap) {
        const coTags = constraint.overlap.coTags || [];
        const groupKey = [id, ...coTags].sort().join('-');
        if (processedOverlapGroups.has(groupKey)) {
          return; // Skip, already rendered as part of the group
        }
        processedOverlapGroups.add(groupKey);
  chips.push(createCoOccurrenceChip(id, constraint.overlap, 'exclude', entity));
        return;
      }
      
  const tagName = lookupName(id, entity);
      const chipClass = `tag-chip ${constraint.type === 'presence' ? 'exclude' : constraint.type}`;
      
      // Add constraint indicator text
      let constraintText = '';
      if (constraint.type === 'duration' && constraint.duration) {
        const min = constraint.duration.min || 0;
        const max = constraint.duration.max || '∞';
        const unit = constraint.duration.unit === 'percent' ? '%' : 's';
        constraintText = ` [${min}-${max}${unit}]`;
      } else if (constraint.type === 'importance' && constraint.importance !== undefined) {
        constraintText = ` [×${constraint.importance.toFixed(1)}]`;
      }
      
      // Use consistent spacing - all constraints get the same padding
      
      chips.push(React.createElement('span',{ key:'e'+id, className: `${chipClass} tag-chip-flex` }, [
        React.createElement('span', { key: 'text', className: 'tag-chip-text' }, tagName),
        constraintText ? React.createElement('span', { key: 'constraint', className: 'tag-chip-constraint' }, constraintText) : null,
        React.createElement('div', { key: 'actions', className: 'tag-chip-actions' }, [
          React.createElement('button',{ key:'gear', className:'constraint-btn', onClick:(e:any)=> showConstraintPopup(id, e, entity), title:'Configure constraint' }, '⚙'),
          React.createElement('button',{ key:'x', onClick:(e:any)=>{ e.stopPropagation(); removeTag(id,'exclude'); }, title:'Remove', className: 'tag-chip-remove' }, '×')
        ])
      ].filter(Boolean)));
    });

    const suggestionsList = (searchState.showDropdown || searchState.search) && (searchState.suggestions.length || searchState.loading || searchState.error) ? React.createElement('div',{ className:'suggestions-list', key:'list' },
      searchState.loading ? React.createElement('div',{ className:'empty-suggest'}, 'Searching…') :
      searchState.error ? React.createElement('div',{ className:'empty-suggest'}, searchState.error) :
      searchState.suggestions.length ? searchState.suggestions.map((tg:any)=> React.createElement('div',{ key:tg.id, onClick:(e:any)=>{ e.stopPropagation(); addTag(parseInt(tg.id,10), tg.name); } }, tg.name+' (#'+tg.id+')')) :
      React.createElement('div',{ className:'empty-suggest'}, 'No matches')
    ) : null;

    function onKeyDown(e:any){
      if(e.key==='Enter'){
        if(searchState.suggestions.length){ 
          const firstTag = searchState.suggestions[0];
          addTag(parseInt(firstTag.id,10), firstTag.name); 
          e.preventDefault(); 
          return; 
        }
        const raw = searchState.search.trim();
        if(/^[0-9]+$/.test(raw)){ addTag(parseInt(raw,10)); e.preventDefault(); return; }
      } else if(e.key==='Backspace' && !searchState.search){
        // Remove the last tag from either include or exclude (prefer include first)
        e.preventDefault();
        if(include.length){ 
          removeTag(include[include.length-1],'include'); 
        } else if(exclude.length){ 
          removeTag(exclude[exclude.length-1],'exclude'); 
        }
      } else if(e.key==='Escape'){
        if(constraintPopup) {
          setConstraintPopup(null);
        } else {
          setSearchState((prev: any) => ({ ...prev, showDropdown: false, search: '', suggestions: [] }));
        }
      }
    }

    // Determine if combination toggle should be shown (show unless 'not-applicable')
    const showCombinationToggle = resolvedAllowedModes.length > 0 && resolvedAllowedModes.every(m => m !== 'not-applicable');
    const toggleClickable = resolvedAllowedModes.length > 1;
    
    // Debug: log combination toggle visibility
    // (Debug log removed)
    
    const combinationToggle = showCombinationToggle ? React.createElement('button',{ 
      key:'combo-toggle', 
      type:'button', 
      className:`combination-toggle ${searchState.combinationMode}${toggleClickable ? '' : ' disabled'}`, 
      disabled: !toggleClickable,
      onClick: toggleClickable ? (e:any)=>{ 
        e.stopPropagation(); 
        const currentIdx = resolvedAllowedModes.indexOf(searchState.combinationMode);
        const nextIdx = (currentIdx + 1) % resolvedAllowedModes.length;
        const nextMode = resolvedAllowedModes[nextIdx];
        setSearchState((prev: any) => ({ ...prev, combinationMode: nextMode })); 
        // Immediately persist the mode change
        onChange({ include, exclude, constraints, tag_combination: nextMode });
      } : undefined, 
      title: toggleClickable ? `Toggle combination mode (current: ${searchState.combinationMode})` : `Combination mode: ${searchState.combinationMode} (fixed)`
    }, (searchState.combinationMode ? String(searchState.combinationMode).toUpperCase() : '')) : null;

    // Constraint popup component
    const constraintPopupEl = constraintPopup ? React.createElement('div', {
      className: 'constraint-popup',
      style: { left: constraintPopup.position.x + 'px', top: constraintPopup.position.y + 'px' },
      onClick: (e: any) => e.stopPropagation()
    }, [
      React.createElement(ConstraintEditor, {
        key: 'editor',
        tagId: constraintPopup.tagId,
        constraint: constraintPopup.initialConstraint || getTagConstraint(constraintPopup.tagId),
  tagName: lookupName(constraintPopup.tagId, constraintPopup && constraintPopup.entity),
        value: v,
        fieldName: fieldName,
        allowedConstraintTypes,
        onSave: (constraint: any) => {
          updateTagConstraint(constraintPopup.tagId, constraint);
          setConstraintPopup(null);
        },
        onCancel: () => setConstraintPopup(null),
        onClose: () => setConstraintPopup(null)
      })
    ]) : null;

    return React.createElement('div',{
      className:'ai-tag-fallback unified w-100',
      onClick:()=>{ if(tagInputRef.current) tagInputRef.current.focus(); }
    }, [
      combinationToggle,
      chips.length? chips : React.createElement('span',{ key:'ph', className:'text-muted small'}, 'No tags'),
      React.createElement('input',{
        key:'inp',
        type:'text',
        className:'tag-input',
        value: searchState.search,
        placeholder:'Search tags…',
        onChange:(e:any)=> search(e.target.value),
        onKeyDown,
        onFocus: onInputFocus,
        onClick:(e:any)=> e.stopPropagation(),
        ref: tagInputRef
      }),
      suggestionsList,
      constraintPopupEl
    ]);
  };

  // Initialize defaults when recommender changes
  useEffect(()=>{
    if(!currentRecommender) return;
    const defs = (currentRecommender as any).config || [];
    let cached = configCacheRef.current[currentRecommender.id];
    if(!cached){
      cached = {};
      for(const field of defs){
        cached[field.name] = field.default;
        if(field.type==='tags' || field.type==='performers'){
          compositeRawRef.current[field.name] = '';
        }
      }
      configCacheRef.current[currentRecommender.id] = cached;
    }
    setConfigValues({...cached});
  }, [currentRecommender]);

  function scheduleFetchAfterConfigChange(previousPage:number){
    // If page changed to 1 because of config change, we rely on page effect; otherwise manual fetch
    if(previousPage === 1){
      // manual fetch to reflect immediate change
      queueMicrotask(()=> fetchRecommendations());
    }
  }

  function applyConfigImmediate(update:any){
    setConfigValues((v:any)=>{ const next = { ...v, ...update }; (configValuesRef as any).current = next; if(recommenderId){ (configCacheRef as any).current[recommenderId] = next; } return next; });
  }

  function updateConfigField(name:string, value:any, opts?:{ debounce?: boolean; field?: any }){
    const field = opts?.field;
    const prevPage = page;
    // Debounced text fields: update local state immediately but delay fetch
    if(opts?.debounce){
      applyConfigImmediate({ [name]: value });
      if(textDebounceTimersRef.current[name]) clearTimeout(textDebounceTimersRef.current[name]);
      textDebounceTimersRef.current[name] = setTimeout(()=>{
        // ensure still active recommender
        scheduleFetchAfterConfigChange(prevPage);
      }, 400);
    } else {
      applyConfigImmediate({ [name]: value });
      scheduleFetchAfterConfigChange(prevPage);
    }
    if(prevPage !== 1) setPage(1); // reset to first page
  }

  function parseIdList(raw:string):number[]{
    return raw.split(',').map(s=> s.trim()).filter(s=> s.length>0).map(s=> parseInt(s,10)).filter(n=> !isNaN(n) && n>=0);
  }

  function renderConfigPanel(){
    if(!currentRecommender || !Array.isArray((currentRecommender as any).config) || !(currentRecommender as any).config.length) return null;
    const defs:any[] = (currentRecommender as any).config;

    const rows = defs.map(field => {
      const val = configValues[field.name];
      const id = 'cfg_'+field.name;
      let control:any = null;
      switch(field.type){
        case 'number':
          control = React.createElement('input',{ id, type:'number', className:'text-input form-control form-control-sm w-num', value: val??'', min: field.min, max: field.max, step: field.step||1, onChange:(e:any)=> updateConfigField(field.name, e.target.value===''? null: Number(e.target.value)) });
          break;
        case 'slider':
          control = React.createElement('div',{ className:'range-wrapper' }, [
            React.createElement('input',{ key:'rng', id, type:'range', className:'zoom-slider', value: val ?? field.default ?? 0, min: field.min, max: field.max, step: field.step||1, onChange:(e:any)=> updateConfigField(field.name, Number(e.target.value)) }),
            React.createElement('div',{ key:'val', className:'range-value'}, String(val ?? field.default ?? 0))
          ]);
          break;
        case 'select':
        case 'enum':
          control = React.createElement('select',{ id, className:'input-control form-control form-control-sm w-select w-180', value: val ?? field.default ?? '', onChange:(e:any)=> updateConfigField(field.name, e.target.value) }, (field.options||[]).map((o:any)=> React.createElement('option',{ key:o.value, value:o.value }, o.label||o.value)));
          break;
        case 'boolean': {
          // Ensure the checkbox has an accessible description (help) and the visible label is rendered above via labelNode.
          const helpId = field.help ? (id + '-help') : undefined;
          // Keep the internal switch label empty so the above label (labelNode) is the visible caption
          control = React.createElement('div',{ className:'custom-control custom-switch'}, [
            React.createElement('input',{ key:'chk', id, type:'checkbox', className:'custom-control-input', checked: !!val, onChange:(e:any)=> updateConfigField(field.name, e.target.checked), 'aria-describedby': helpId }),
            React.createElement('label',{ key:'lb', htmlFor:id, className:'custom-control-label', title: field.help||'' }, '')
          ]);
          break;
        }
        case 'text':
          control = React.createElement('input',{ id, type:'text', className:'text-input form-control form-control-sm w-text w-180', value: val ?? '', placeholder: field.help || '', onChange:(e:any)=> updateConfigField(field.name, e.target.value, { debounce:true, field }) });
          break;
        case 'search':
          control = React.createElement('div',{ className:'clearable-input-group search-term-input w-180' }, [
            React.createElement('input',{ key:'in', id, type:'text', className:'clearable-text-field form-control form-control-sm w-180', value: val ?? '', placeholder: field.help || 'Search…', onChange:(e:any)=> updateConfigField(field.name, e.target.value, { debounce:true, field }) })
          ]);
          break;
        case 'tags': {
          // Always use custom fallback - no native TagSelect/TagIDSelect components
          let includeIds:number[] = []; let excludeIds:number[] = []; let constraints:any = {};
          if(Array.isArray(val)) {
            includeIds = val; 
          } else if(val && typeof val==='object'){ 
            includeIds = Array.isArray(val.include)? val.include: []; 
            excludeIds = Array.isArray(val.exclude)? val.exclude: []; 
            constraints = val.constraints || {};
          }
          // Custom searchable include/exclude selector with chips.
          control = React.createElement('div', { className:'w-tags' }, React.createElement(TagIncludeExclude, { 
            fieldName: field.name, 
            value: { include: includeIds, exclude: excludeIds, constraints, tag_combination: val?.tag_combination }, 
            onChange:(next:any)=> updateConfigField(field.name, next),
            initialTagCombination: field.tag_combination,
            allowedConstraintTypes: field.constraint_types,
            allowedCombinationModes: field.allowed_combination_modes
          }));
          break; }
        case 'performers': {
          // Use the unified selector for performers (reuses tag selector UI/behavior)
          let includeIds:number[] = []; let excludeIds:number[] = []; let constraints:any = {};
          if(Array.isArray(val)) {
            includeIds = val;
          } else if(val && typeof val==='object'){
            includeIds = Array.isArray(val.include)? val.include: [];
            excludeIds = Array.isArray(val.exclude)? val.exclude: [];
            constraints = val.constraints || {};
          }
          control = React.createElement('div', { className:'w-tags' }, React.createElement(TagIncludeExclude, {
            fieldName: field.name,
            value: { include: includeIds, exclude: excludeIds, constraints, tag_combination: val?.tag_combination },
            onChange:(next:any)=> updateConfigField(field.name, next),
            initialTagCombination: field.tag_combination,
            allowedConstraintTypes: field.constraint_types,
            allowedCombinationModes: field.allowed_combination_modes,
            entity: 'performer'
          }));
          break; }
        default:
          control = React.createElement('div',{ className:'text-muted small'}, 'Unsupported: '+field.type);
      }
  // Render labels above every control (including boolean switches) so layout is consistent
  const showLabelAbove = true;
      // Make labels inline-block and only as wide as the control beneath to prevent blocking layout
      const capWidth = field.type==='tags' ? 400 : (field.type==='slider' ? 92 : (['text','search','select','enum'].includes(field.type) ? 180 : undefined));
      const labelStyle = capWidth ? { display:'inline-block', width: capWidth+'px', maxWidth: capWidth+'px' } : undefined;
      const labelNode = showLabelAbove ? React.createElement('label',{ htmlFor:id, className:'form-label d-flex justify-content-between mb-0', style: labelStyle }, [
        React.createElement('span',{ key:'t', className:'label-text' }, field.label || field.name)
      ]) : null;
      // Use auto-width columns for compact fields; let large/complex fields take normal grid width
      const compactTypes = ['number', 'select', 'enum', 'boolean', 'slider', 'text', 'search', 'tags'];
      const colClass = compactTypes.includes(field.type) ? 'col-auto mb-1' : 'col-lg-4 col-md-6 col-12 mb-1';
      return React.createElement('div',{ key:field.name, className:colClass }, [
        React.createElement('div', { className: 'form-group mb-0' }, [
          labelNode,
          // Wrap control so it can be constrained to the same width as the label and centered
          React.createElement('div', { key: 'ctrlwrap', style: labelStyle, className: 'control-wrap' }, control)
        ])
      ]);
    });

    return React.createElement('div',{ className:'ai-rec-config mb-1'}, [
      React.createElement('div',{ key:'hdr', className:'d-flex justify-content-between align-items-center mb-1'}, [
        React.createElement('strong',{ key:'t', className:'small'}, 'Configuration'),
        React.createElement('div',{ key:'actions', className:'d-flex align-items-center gap-2'}, [
          React.createElement('button',{ key:'tgl', className:'btn btn-secondary btn-sm', onClick:()=> setShowConfig((s:any)=>!s) }, showConfig? 'Hide':'Show')
        ])
      ]),
      showConfig ? React.createElement('div',{ key:'body', className:'config-row row'}, rows) : null
    ]);
  }

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
        const body:any = { context: 'global_feed', recommenderId, limit: itemsPerPage, offset, config: configValuesRef.current || {} };
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

    const componentsToLoad = [
      PluginApi.loadableComponents?.SceneCard,
      // Attempt to also pre-load Tag selectors if they are loadable (some builds expose these)
      PluginApi.loadableComponents?.TagIDSelect || PluginApi.loadableComponents?.TagSelect
    ].filter(Boolean);
    const componentsLoading = PluginApi.hooks?.useLoadComponents ? PluginApi.hooks.useLoadComponents(componentsToLoad) : false;
    const { SceneCard, TagIDSelect, TagSelect } = PluginApi.components || {} as any;
    // Attempt alternate resolution if not found (some builds may expose under different keys or on window)
    const _w:any = window as any;
    const ResolvedTagIDSelect = TagIDSelect || _w.TagIDSelect || _w.TagSelectID || null;
    const ResolvedTagSelect = TagSelect || _w.TagSelect || null;
    if((w as any).AIDebug && !ResolvedTagIDSelect && !ResolvedTagSelect){
      console.debug('[RecommendedScenes] Tag selector components not found; falling back to text input');
    }
    const grid = useMemo(()=>{
      if(loading || componentsLoading) return React.createElement('div',{ className:'scene-grid-loading'}, 'Loading scenes...');
      if(error) return React.createElement('div',{ className:'scene-grid-error'}, error);
      if(!paginatedScenes.length) return React.createElement('div',{ className:'scene-grid-empty'}, 'No scenes');
      if(cardWidth===undefined) return React.createElement('div',{ className:'scene-grid-calculating'}, 'Calculating layout...');
  const children = paginatedScenes.map((s:BasicScene,i:number)=> React.createElement('div',{ key:s.id+'_'+i, style:{display:'contents'}}, SceneCard ? React.createElement(SceneCard,{ scene:s, zoomIndex, queue: undefined, index: i }) : null) );

      return React.createElement('div',{ className:'row ai-rec-grid d-flex flex-wrap justify-content-center', ref:componentRef, style:{ ['--ai-card-width' as any]: cardWidth+'px'}}, children);
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
        React.createElement('div',{ key:'ps', className:'page-size-selector mr-2 mb-2'}, React.createElement('select',{ className:'btn-secondary form-control form-control-sm', value:itemsPerPage, onChange:(e:any)=>{ setItemsPerPage(Number(e.target.value)); setPage(1);} }, [20,40,80,100].map(n=> React.createElement('option',{key:n, value:n}, n)))) ,
        React.createElement('div',{ key:'zoomWrap', className:'mx-2 mb-2 d-inline-flex align-items-center'}, [
          React.createElement('input',{ key:'zr', min:0, max:3, type:'range', className:'zoom-slider ml-1 form-control-range', value:zoomIndex, onChange:(e:any)=> setZoomIndex(Number(e.target.value)) })
        ]),
        React.createElement('div',{ key:'act', role:'group', className:'mb-2 btn-group'}, [
          React.createElement(Button,{ key:'refresh', className:'btn btn-secondary minimal', disabled:loading, title:'Refresh', onClick:manualRefresh }, '↻')
        ])
        , backendStatus==='error' && React.createElement('div',{ key:'err', className:'mb-2 ml-2 small text-danger d-flex align-items-center' }, [
          React.createElement('span',{ key:'lbl', className:'backend-status'}, 'Backend failed'),
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
      renderConfigPanel(),
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
