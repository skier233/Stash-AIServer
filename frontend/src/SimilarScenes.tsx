// SimilarScenes Component
// Mimics the queue tab structure but uses the 'similar_scene' context
// for scene-specific recommendations with dynamic inputs

(function(){
  const w: any = window as any;
  
  // Safer initialization - wait for everything to be ready
  function initializeSimilarScenes() {
    const PluginApi = w.PluginApi;
    if (!PluginApi || !PluginApi.React) {
      console.warn('[SimilarScenes] PluginApi or React not available');
      return;
    }
    
    const React = PluginApi.React;
    
    // Validate React hooks are available
    if (!React.useState || !React.useMemo || !React.useEffect || !React.useRef || !React.useCallback) {
      console.warn('[SimilarScenes] React hooks not available');
      return;
    }
    
    const { useState, useMemo, useEffect, useRef, useCallback } = React;
    
    // Import shared utilities
    const Utils = (w as any).AIRecommendationUtils;
    if (!Utils) {
      console.warn('[SimilarScenes] RecommendationUtils not found');
      return;
    }

    const { useContainerDimensions, useCardWidth } = Utils;

    // Bootstrap components
    const Bootstrap = PluginApi.libraries.Bootstrap || {} as any;
    const Button = Bootstrap.Button || ((p: any) => React.createElement('button', p, p.children));

  interface BasicSceneFile { duration?: number; size?: number; }
  interface BasicScene { 
    id: number; 
    title?: string; 
    rating100?: number; 
    rating?: number; 
    files?: BasicSceneFile[]; 
    paths?: any;
    studio?: { id: string; name: string; } | null;
    performers?: { id: string; name: string; }[];
    tags?: { id: string; name: string; }[];
    [k: string]: any;
  }

  interface RecommenderDef { 
    id: string; 
    label: string; 
    description?: string; 
    config?: any[]; 
    contexts?: string[]; 
  }

  function log(...args: any[]) { if ((w as any).AIDebug) console.log('[SimilarScenes]', ...args); }
  function warn(...args: any[]) { if ((w as any).AIDebug) console.warn('[SimilarScenes]', ...args); }

  function normalizeScene(sc: any): BasicScene | undefined {
    if (!sc || typeof sc !== 'object') return undefined;
    const arrayFields = ['performers', 'tags', 'markers', 'scene_markers', 'galleries', 'images', 'files', 'groups'];
    arrayFields.forEach(f => { 
      if (sc[f] == null) sc[f] = []; 
      else if (!Array.isArray(sc[f])) sc[f] = [sc[f]].filter(Boolean); 
    });
    if (!sc.studio) sc.studio = null;
    if (sc.rating100 == null && typeof sc.rating === 'number') sc.rating100 = sc.rating * 20;
    if (sc.rating == null && typeof sc.rating100 === 'number') sc.rating = Math.round(sc.rating100 / 20);
    return sc as BasicScene;
  }

  // Similar to QueueViewer but for similar scenes
  const SimilarScenesViewer: React.FC<any> = (props: any) => {
    // Accept either `currentSceneId` (old API) or `sceneId` (integration passes this)
    let currentSceneId = props.currentSceneId || props.sceneId || null;
    if (currentSceneId != null) currentSceneId = String(currentSceneId);
    
    // Early return if no scene ID - don't call hooks
    if (!currentSceneId) {
      return React.createElement('div', { className: 'similar-scenes-error' }, 'No scene ID provided for Similar tab');
    }
    
    const onSceneClicked = props.onSceneClicked;
    const [recommenders, setRecommenders] = useState(null as RecommenderDef[] | null);
    const [recommenderId, setRecommenderId] = useState(null as string | null);
    const [scenes, setScenes] = useState([] as BasicScene[]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null as string | null);
    const [configValues, setConfigValues] = useState({} as any);
    const [zoomIndex, setZoomIndex] = useState(1);
    
    const zoomWidths = [280, 340, 480, 640];
    const [componentRef, { width: containerWidth }] = useContainerDimensions();
    const cardWidth = useCardWidth(containerWidth, zoomIndex, zoomWidths);

    // Load components at the top level - before any conditional logic
    const componentsToLoad = useMemo(() => [
      PluginApi.loadableComponents?.SceneCard,
      PluginApi.loadableComponents?.QueueViewer,
      PluginApi.loadableComponents?.QueueItem
    ].filter(Boolean), []);
    const componentsLoading = PluginApi.hooks?.useLoadComponents ? PluginApi.hooks.useLoadComponents(componentsToLoad) : false;
    const { SceneCard, QueueViewer, QueueItem } = PluginApi.components || {} as any;

    const configValuesRef = useRef({} as any);
    const compositeRawRef = useRef({} as any);

    useEffect(() => { configValuesRef.current = configValues; }, [configValues]);

    const currentRecommender = useMemo(() => 
      (recommenders || [])?.find((r: any) => r.id === recommenderId), 
      [recommenders, recommenderId]
    );

    // Resolve backend base (mirror logic from RecommendedScenes for consistency)
    const backendBase = useMemo(() => {
      const explicit = (w as any).AI_BACKEND_URL as string | undefined;
      if (explicit) return explicit.replace(/\/$/, '');
      const loc = (location && location.origin) || '';
      try { const u = new URL(loc); if (u.port === '3000') { u.port = '8000'; return u.toString().replace(/\/$/, ''); } } catch {}
      return (loc || 'http://localhost:8000').replace(/\/$/, '');
    }, []);

    // Discover available recommenders using the backend recommendations API
    const discoverRecommenders = useCallback(async () => {
      try {
        setLoading(true);
        const recContext = 'similar_scene';
        const url = `${backendBase}/api/v1/recommendations/recommenders?context=${encodeURIComponent(recContext)}`;
        console.debug('[SimilarScenes] discoverRecommenders ->', url);
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const contentType = response.headers && response.headers.get ? response.headers.get('content-type') || '' : '';
        if (!contentType.includes('application/json')) {
          const text = await response.text();
          console.warn('[SimilarScenes] discoverRecommenders: non-JSON response body (truncated):', text && text.slice ? text.slice(0, 512) : text);
          setError('Failed to discover recommenders: server returned non-JSON response. See console for details.');
          setRecommenders(null);
          return;
        }
        const data = await response.json();
        if (Array.isArray(data.recommenders)) {
          setRecommenders(data.recommenders);
          const similarContextRec = data.recommenders.find((r: any) => r.contexts?.includes('similar_scene'));
          if (similarContextRec) {
            setRecommenderId(similarContextRec.id);
            log('Auto-selected recommender for similar_scene:', similarContextRec.id);
          }
        } else {
          setRecommenders(null);
        }
      } catch (e: any) {
        warn('Failed to discover recommenders:', e && e.message ? e.message : e);
        setError('Failed to discover recommenders: ' + (e && e.message ? e.message : String(e)));
      } finally {
        setLoading(false);
      }
    }, [backendBase]);

    // Fetch similar scenes from the unified recommendations query endpoint
    const fetchSimilarScenes = useCallback(async () => {
      if (!recommenderId || !currentSceneId) return;

      try {
        setLoading(true);
        setError(null);

        const payload = {
          context: 'similar_scene',
          recommenderId,
          scene_id: currentSceneId,
          config: configValuesRef.current || {},
          limit: 20
        };

        console.debug('[SimilarScenes] fetchSimilarScenes payload:', payload);
        log('Fetching similar scenes for scene:', currentSceneId, 'with payload:', payload);

        const url = `${backendBase}/api/v1/recommendations/query`;
        console.debug('[SimilarScenes] POST ->', url);
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const contentType = response.headers && response.headers.get ? response.headers.get('content-type') || '' : '';
        if (!contentType.includes('application/json')) {
          const text = await response.text();
          console.warn('[SimilarScenes] fetchSimilarScenes: non-JSON response body (truncated):', text && text.slice ? text.slice(0, 512) : text);
          throw new Error('Server returned non-JSON response');
        }

        const data = await response.json();
        if (data.scenes && Array.isArray(data.scenes)) {
          const normalizedScenes = data.scenes.map(normalizeScene).filter(Boolean) as BasicScene[];
          setScenes(normalizedScenes);
        } else {
          setScenes([]);
          setError('No similar scenes found or unexpected data format');
        }

      } catch (e: any) {
        warn('Failed to fetch similar scenes:', e && e.message ? e.message : e);
        setError('Failed to load similar scenes: ' + (e && e.message ? e.message : String(e)));
        setScenes([]);
      } finally {
        setLoading(false);
      }
    }, [recommenderId, currentSceneId, configValues]);

    // Auto-discover recommenders on mount
    useEffect(() => {
      discoverRecommenders();
    }, [discoverRecommenders]);

    // When the selected recommender changes, initialize config values from its defaults
    useEffect(() => {
      if (!currentRecommender) return;
      const defs: any[] = (currentRecommender as any).config || [];
      const defaults: any = {};
      defs.forEach(f => {
        if (Object.prototype.hasOwnProperty.call(f, 'default')) defaults[f.name] = f.default;
      });
      setConfigValues(defaults);
      configValuesRef.current = defaults;
    }, [currentRecommender]);

    // Fetch similar scenes when recommender or scene changes
    useEffect(() => {
      if (recommenderId && currentSceneId) {
        fetchSimilarScenes();
      }
    }, [fetchSimilarScenes]);

    // Handle scene click
    const handleSceneClick = useCallback((sceneId: string, event?: React.MouseEvent) => {
      if (event) event.preventDefault();
      if (onSceneClicked) {
        onSceneClicked(sceneId);
      } else {
        // Default behavior: navigate to scene
        window.location.href = `/scenes/${sceneId}`;
      }
    }, [onSceneClicked]);

    // Render scene card similar to queue viewer
    const renderSceneCard = useCallback((scene: BasicScene) => {
      const title = scene.title || `Scene ${scene.id}`;
      const studio = scene.studio?.name || '';
      const performers = scene.performers?.map(p => p.name).join(', ') || '';
      const duration = scene.files?.[0]?.duration;
      const screenshot = scene.paths?.screenshot;

      return React.createElement('div', {
        key: scene.id,
        className: 'similar-scene-card',
        style: cardWidth ? { width: cardWidth } : {},
        onClick: (e: React.MouseEvent) => handleSceneClick(scene.id.toString(), e)
      }, [
        React.createElement('div', { key: 'thumbnail', className: 'similar-scene-thumbnail' }, [
          screenshot ? React.createElement('img', {
            key: 'img',
            src: screenshot,
            alt: title,
            loading: 'lazy'
          }) : React.createElement('div', {
            key: 'placeholder',
            className: 'thumbnail-placeholder'
          }, '📹')
        ]),
        React.createElement('div', { key: 'details', className: 'similar-scene-details' }, [
          React.createElement('div', { key: 'title', className: 'similar-scene-title', title: title }, title),
          studio ? React.createElement('div', { key: 'studio', className: 'similar-scene-studio' }, studio) : null,
          performers ? React.createElement('div', { key: 'performers', className: 'similar-scene-performers' }, performers) : null,
          duration ? React.createElement('div', { key: 'duration', className: 'similar-scene-duration' }, 
            `${Math.floor(duration / 60)}:${String(duration % 60).padStart(2, '0')}`
          ) : null
        ])
      ]);
    }, [cardWidth, handleSceneClick]);

    // Render recommender selector when recommenders are available
    const renderRecommenderSelector = useCallback(() => {
      if (!recommenders || recommenders.length === 0) return null;

      // Prefer recommenders that advertise support for 'similar_scene'. If none do, fall back to all recommenders.
      const similarContextRecommenders = recommenders.filter(r => r.contexts?.includes('similar_scene'));
      const candidates = similarContextRecommenders.length > 0 ? similarContextRecommenders : recommenders;

      // Ensure a default is selected
      if (!recommenderId && candidates.length > 0) {
        // Defer setting state until next microtask to avoid during render
        setTimeout(() => {
          try { setRecommenderId((prev) => prev || candidates[0].id); } catch (_) {}
        }, 0);
      }

      return React.createElement('div', { className: 'similar-recommender-selector' }, [
        React.createElement('label', { key: 'label' }, 'Algorithm: '),
        React.createElement('select', {
          key: 'select',
          value: recommenderId || '',
          onChange: (e: any) => setRecommenderId(e.target.value)
        }, candidates.map((rec: any) => 
          React.createElement('option', { key: rec.id, value: rec.id }, rec.label || rec.id)
        ))
      ]);
    }, [recommenders, recommenderId]);

    // Config state update helper (simple debounce for text inputs)
    const textTimersRef = useRef({} as any);
    const updateConfigField = useCallback((name: string, value: any, opts?: { debounce?: boolean }) => {
      setConfigValues((prev: any) => ({ ...prev, [name]: value }));
      configValuesRef.current = { ...configValuesRef.current, [name]: value };
      if (opts && opts.debounce) {
        if (textTimersRef.current[name]) clearTimeout(textTimersRef.current[name]);
        textTimersRef.current[name] = setTimeout(() => {
          fetchSimilarScenes();
        }, 300);
      } else {
        // immediate fetch
        fetchSimilarScenes();
      }
    }, [fetchSimilarScenes]);

    // Render a compact config panel for the selected recommender
    const renderConfigPanel = useCallback(() => {
      if (!currentRecommender || !Array.isArray((currentRecommender as any).config) || !(currentRecommender as any).config.length) return null;
      const defs: any[] = (currentRecommender as any).config;
      const rows = defs.map(field => {
        const val = configValues[field.name];
        const id = 'sim_cfg_' + field.name;
        let control: any = null;
        switch (field.type) {
          case 'number':
            control = React.createElement('input', { id, type: 'number', value: val ?? '', onChange: (e: any) => updateConfigField(field.name, e.target.value === '' ? null : Number(e.target.value)) });
            break;
          case 'slider':
            control = React.createElement('div', {}, [
              React.createElement('input', { key: 'rng', id, type: 'range', value: val ?? field.default ?? 0, min: field.min, max: field.max, step: field.step || 1, onChange: (e: any) => updateConfigField(field.name, Number(e.target.value)) }),
              React.createElement('span', { key: 'val' }, String(val ?? field.default ?? 0))
            ]);
            break;
          case 'select':
          case 'enum':
            control = React.createElement('select', { id, value: val ?? field.default ?? '', onChange: (e: any) => updateConfigField(field.name, e.target.value) }, (field.options || []).map((o: any) => React.createElement('option', { key: o.value, value: o.value }, o.label || o.value)));
            break;
          case 'boolean':
            control = React.createElement('input', { id, type: 'checkbox', checked: !!val, onChange: (e: any) => updateConfigField(field.name, e.target.checked) });
            break;
          case 'text':
            control = React.createElement('input', { id, type: 'text', value: val ?? '', placeholder: field.help || '', onChange: (e: any) => updateConfigField(field.name, e.target.value, { debounce: true }) });
            break;
          case 'tags':
            // Simple comma-separated input for tags for now
            control = React.createElement('input', { id, type: 'text', value: Array.isArray(val) ? val.join(',') : (val ?? ''), placeholder: 'Comma separated tag ids', onChange: (e: any) => {
              const text = e.target.value;
              const arr = text.split(',').map((s: string) => s.trim()).filter(Boolean).map((n: string) => Number(n)).filter((n: number) => !isNaN(n));
              updateConfigField(field.name, arr, { debounce: true });
            }});
            break;
          default:
            control = React.createElement('input', { id, type: 'text', value: val ?? '', onChange: (e: any) => updateConfigField(field.name, e.target.value) });
        }
        return React.createElement('div', { key: field.name, className: 'similar-config-row' }, [
          React.createElement('label', { key: 'lbl', htmlFor: id }, field.label || field.name),
          control
        ]);
      });
      return React.createElement('div', { className: 'similar-config-panel' }, rows);
    }, [currentRecommender, configValues, updateConfigField]);

    // Note: Zoom slider intentionally omitted for queue-style display

    // Main render
    return React.createElement('div', { 
      className: 'similar-scenes-container',
      ref: componentRef
    }, [
      React.createElement('div', { key: 'controls', className: 'similar-scenes-controls' }, [
        renderRecommenderSelector(),
        React.createElement('button', {
          key: 'refresh',
          className: 'btn btn-secondary btn-sm',
          onClick: fetchSimilarScenes,
          disabled: loading
        }, loading ? 'Loading...' : 'Refresh')
      ]),

      // Config panel separate block (full width) so it doesn't overflow out of the tab
      currentRecommender ? React.createElement('div', { key: 'configBlock', className: 'similar-scenes-config-block' }, [
        renderConfigPanel()
      ]) : null,
      
      loading ? React.createElement('div', { key: 'loading', className: 'similar-scenes-loading' }, 'Loading similar scenes...') : null,
      
      error ? React.createElement('div', { key: 'error', className: 'similar-scenes-error' }, error) : null,
      
      !loading && !error && scenes.length === 0 ? 
        React.createElement('div', { key: 'empty', className: 'similar-scenes-empty' }, 'No similar scenes found') : null,
      
      !loading && scenes.length > 0 ? (() => {
        // Use already loaded components at top level
        if (!componentsLoading && SceneCard) {
          const children = scenes.map((s: BasicScene, i: number) => 
            React.createElement('div', { key: s.id + '_' + i, style: { display: 'contents' } }, 
              SceneCard ? React.createElement(SceneCard, { scene: s, zoomIndex: undefined, queue: undefined, index: i }) : null
            )
          );
          return React.createElement('div', { key: 'list', className: 'similar-scenes-list row d-flex flex-wrap justify-content-center' }, children);
        }

        // Fallback to original thumbnail grid
        return React.createElement('div', { key: 'grid', className: 'similar-scenes-grid' }, scenes.map(renderSceneCard));
      })() : null
    ]);
  };

  // Export to global namespace for integration
  (w as any).SimilarScenesViewer = SimilarScenesViewer;

  // Debug: log that component is loaded
  console.log('[SimilarScenes] SimilarScenesViewer component loaded and exported to window.SimilarScenesViewer');

  } // End initializeSimilarScenes
  
  // Wait for dependencies and initialize
  function waitAndInitialize() {
    if (w.PluginApi && w.PluginApi.React && w.AIRecommendationUtils) {
      console.log('[SimilarScenes] Dependencies ready, initializing...');
      initializeSimilarScenes();
    } else {
      console.log('[SimilarScenes] Waiting for dependencies...');
      setTimeout(waitAndInitialize, 100);
    }
  }
  
  waitAndInitialize();

})();