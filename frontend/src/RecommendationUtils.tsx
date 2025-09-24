// Shared utilities for recommendation components
// Extracted from RecommendedScenes.tsx for reuse in SimilarScenes.tsx

(function(){
  const w: any = window as any;
  
  // Safer initialization - wait for everything to be ready
  function initializeRecommendationUtils() {
    const PluginApi = w.PluginApi;
    if (!PluginApi || !PluginApi.React) {
      console.warn('[RecommendationUtils] PluginApi or React not available');
      return;
    }
    
    // Validate React hooks are available
    if (!PluginApi.React.useState || !PluginApi.React.useMemo || !PluginApi.React.useEffect || !PluginApi.React.useRef) {
      console.warn('[RecommendationUtils] React hooks not available');
      return;
    }
    
    const React = PluginApi.React;
    const { useState, useMemo, useEffect, useRef } = React;

  // Upstream grid hooks copied from GridCard.tsx for exact parity
  function useDebounce(fn: any, delay: number) {
    const timeoutRef = useRef(null as any);
    return useMemo(() => (...args: any[]) => {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = setTimeout(() => fn(...args), delay);
    }, [fn, delay]);
  }
  
  function useResizeObserver(target: any, callback: any) {
    useEffect(() => {
      if (!target.current || typeof ResizeObserver === 'undefined') return;
      const ro = new ResizeObserver((entries) => {
        if (entries && entries.length > 0) {
          callback(entries[0]);
        }
      });
      ro.observe(target.current);
      return () => ro.disconnect();
    }, [target, callback]);
  }
  
  function calculateCardWidth(containerWidth: number, preferredWidth: number) {
    const root = typeof window !== 'undefined' ? window.getComputedStyle(document.documentElement) : null;
    const containerPadding = root ? parseFloat(root.getPropertyValue('--ai-rec-container-padding')) : 30;
    const cardMargin = root ? parseFloat(root.getPropertyValue('--ai-rec-card-margin')) : 10;
    const maxUsableWidth = containerWidth - containerPadding;
    const maxElementsOnRow = Math.ceil(maxUsableWidth / preferredWidth);
    const width = maxUsableWidth / maxElementsOnRow - cardMargin;
    return width;
  }
  
  function useContainerDimensions(sensitivityThreshold = 20) {
    const target = useRef(null as any);
    const [dimension, setDimension] = useState({ width: 0, height: 0 });
    
    const debouncedSetDimension = useDebounce((entry: any) => {
      if (!entry.contentBoxSize || !entry.contentBoxSize.length) return;
      
      const { inlineSize: width, blockSize: height } = entry.contentBoxSize[0];
      let difference = Math.abs(dimension.width - width);
      if (difference > sensitivityThreshold) {
        setDimension({ width, height });
      }
    }, 50);
    
    useResizeObserver(target, debouncedSetDimension);
    
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
  
  function useCardWidth(containerWidth: number, zoomIndex: number, zoomWidths: number[]) {
    return useMemo(() => {
      const isMobile = window.innerWidth <= 768;
      if (isMobile) return undefined;
      
      const effectiveWidth = (containerWidth ? containerWidth : 1200);
      if (zoomIndex === undefined || zoomIndex < 0 || zoomIndex >= zoomWidths.length) {
        return undefined;
      }
      const preferredCardWidth = zoomWidths[zoomIndex];
      return calculateCardWidth(effectiveWidth, preferredCardWidth);
    }, [containerWidth, zoomIndex, zoomWidths]);
  }

  // Constraint Editor Component
  function ConstraintEditor({ tagId, constraint, tagName, value, fieldName, onSave, onCancel, allowedConstraintTypes, entity: popupEntity, compositeRawRef, popupPosition }: any) {
    const [localConstraint, setLocalConstraint] = React.useState(constraint);

    function lookupLocalName(id: number, forEntity?: 'tag'|'performer'){
      const ent = forEntity || popupEntity || 'tag';
      const key = fieldName + '__' + (ent === 'performer' ? 'performerNameMap' : 'tagNameMap');
      const map = compositeRawRef.current[key] || {};
      return map[id] || (ent === 'performer' ? `Performer ${id}` : `Tag ${id}`);
    }

    React.useEffect(() => {
      setLocalConstraint(constraint);
    }, [constraint]);

    const allConstraintTypes = [
      { value: 'presence', label: 'Include/Exclude' },
      { value: 'duration', label: 'Duration Filter' },
      { value: 'overlap', label: 'Co-occurrence' },
      { value: 'importance', label: 'Importance Weight' }
    ];

    const constraintTypes = Array.isArray(allowedConstraintTypes) && allowedConstraintTypes.length > 0
      ? allConstraintTypes.filter(ct => allowedConstraintTypes.includes(ct.value))
      : allConstraintTypes;

  const overlapTagData = React.useMemo(() => {
      if (localConstraint.type !== 'overlap') return { allCoOccurrencePrimaries: new Set(), availableTags: [] };
        
      const allCoOccurrencePrimaries = new Set();
      [...(value?.include || []), ...(value?.exclude || [])].forEach(id => {
        const constraint = (value?.constraints || {})[id] || { type: 'presence' };
        if (constraint.type === 'overlap' && constraint.overlap?.coTags?.length > 0 && id !== tagId) {
          allCoOccurrencePrimaries.add(id);
        }
      });
      const entity = popupEntity || localConstraint._entity || 'tag';
      const availableTags = [...(value?.include || []), ...(value?.exclude || [])]
        .filter(id => id !== tagId && !allCoOccurrencePrimaries.has(id));
      
      return { allCoOccurrencePrimaries, availableTags };
    }, [localConstraint.type, value?.include, value?.exclude, value?.constraints, tagId]);

    function handleTypeChange(newType: string) {
      let newConstraint: any = { type: newType };
      
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
          const availableOptions = overlapTagData.availableTags.map(id => ({
            id,
            name: lookupLocalName(id, popupEntity || 'tag')
          }));
          
          return React.createElement('div', { className: 'constraint-options' }, [
            React.createElement('div', { key: 'duration' }, [
              React.createElement('label', { key: 'label' }, 'Overlap Duration: '),
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
              }),
              React.createElement('span', { key: 'unit' }, localConstraint.overlap?.unit === 'percent' ? '%' : 's')
            ]),
            React.createElement('div', { key: 'unit-select' }, [
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
            ]),
            React.createElement('div', { key: 'co-tags' }, [
              React.createElement('label', { key: 'label' }, 'Co-occurrence Tags: '),
              React.createElement('select', {
                key: 'select',
                multiple: true,
                value: localConstraint.overlap?.coTags || [],
                onChange: (e: any) => {
                  const selected = Array.from(e.target.selectedOptions, (option: any) => parseInt(option.value));
                  setLocalConstraint((prev: any) => ({ 
                    ...prev, 
                    overlap: { ...prev.overlap, coTags: selected }
                  }));
                }
              }, availableOptions.map(opt => 
                React.createElement('option', { key: opt.id, value: opt.id }, opt.name)
              ))
            ])
          ]);
        case 'importance':
          return React.createElement('div', { className: 'constraint-options' }, [
            React.createElement('label', { key: 'label' }, 'Weight: '),
            React.createElement('input', { 
              key: 'input',
              type: 'range', 
              min: '0', 
              max: '2', 
              step: '0.1',
              value: localConstraint.importance || 0.5,
              onChange: (e: any) => setLocalConstraint((prev: any) => ({ ...prev, importance: parseFloat(e.target.value) }))
            }),
            React.createElement('span', { key: 'value' }, `×${(localConstraint.importance || 0.5).toFixed(1)}`)
          ]);
        default:
          return null;
      }
    }

    return React.createElement('div', { className: 'constraint-popup-overlay' }, [
      React.createElement('div', { 
        key: 'popup',
        className: 'constraint-popup',
        style: popupPosition ? { 
          position: 'absolute', 
          left: popupPosition.x, 
          top: popupPosition.y,
          zIndex: 9999
        } : {}
      }, [
        React.createElement('div', { key: 'header', className: 'constraint-header' }, [
          React.createElement('h4', { key: 'title' }, `Configure: ${tagName || lookupLocalName(tagId)}`),
          React.createElement('button', { 
            key: 'close', 
            className: 'constraint-close', 
            onClick: onCancel 
          }, '×')
        ]),
        React.createElement('div', { key: 'type-selector' }, [
          React.createElement('label', { key: 'label' }, 'Constraint Type: '),
          React.createElement('select', {
            key: 'select',
            value: localConstraint.type,
            onChange: (e: any) => handleTypeChange(e.target.value)
          }, constraintTypes.map(ct => 
            React.createElement('option', { key: ct.value, value: ct.value }, ct.label)
          ))
        ]),
        renderOptions(),
        React.createElement('div', { key: 'actions', className: 'constraint-actions' }, [
          React.createElement('button', { 
            key: 'save', 
            onClick: () => onSave(localConstraint),
            className: 'btn btn-primary'
          }, 'Save'),
          React.createElement('button', { 
            key: 'cancel', 
            onClick: onCancel,
            className: 'btn btn-secondary'
          }, 'Cancel')
        ])
      ])
    ]);
  }

  // Tag Selector Component
  function createTagSelector(options: {
    value: any;
    onChange: (value: any) => void;
    entity?: 'tag' | 'performer';
    fieldName: string;
    label?: string;
    allowedConstraintTypes?: string[];
    allowedCombinationModes?: string[];
    initialTagCombination?: string;
    compositeRawRef: any;
  }) {
    const {
      value: v,
      onChange,
      entity = 'tag',
      fieldName,
      label = entity === 'performer' ? 'Performers' : 'Tags',
      allowedConstraintTypes,
      allowedCombinationModes,
      initialTagCombination,
      compositeRawRef
    } = options;

    const include: number[] = Array.isArray(v) ? v : Array.isArray(v?.include) ? v.include : [];
    const exclude: number[] = Array.isArray(v) ? [] : Array.isArray(v?.exclude) ? v.exclude : [];
    const constraints = v?.constraints || {};
    
    // Combination mode logic
    const normalizeMode = (m: any) => (m == null ? undefined : String(m).toLowerCase());
    const allowedNorm = Array.isArray(allowedCombinationModes) && allowedCombinationModes.length > 0
      ? allowedCombinationModes.map(normalizeMode).filter(Boolean) as string[]
      : [];
    const initLC = typeof initialTagCombination === 'string' ? normalizeMode(initialTagCombination) : undefined;
    const resolvedAllowedModes = (allowedNorm.length > 0 ? allowedNorm : (typeof initLC !== 'undefined' ? [initLC] : ['and','or'])) as ('and'|'or'|'not-applicable')[];
    
    const rawValueMode: any = (v && Object.prototype.hasOwnProperty.call(v,'tag_combination')) ? v.tag_combination : undefined;
    const valueMode = normalizeMode(rawValueMode);
    const isValidMode = (m: any) => m === 'and' || m === 'or' || m === 'not-applicable';
    const initialMode = (isValidMode(valueMode) ? valueMode : (isValidMode(initLC) ? initLC : resolvedAllowedModes[0])) as 'and'|'or'|'not-applicable';

    const [searchState, setSearchState] = React.useState({
      search: '',
      suggestions: [] as any[],
      loading: false,
      error: null as string | null,
      showDropdown: false,
      combinationMode: initialMode
    });

    const instanceIdRef = React.useRef(null as any);
    if (!instanceIdRef.current) {
      try { 
        (w as any).__aiTagFallbackCounter = ((w as any).__aiTagFallbackCounter || 0) + 1; 
        instanceIdRef.current = (w as any).__aiTagFallbackCounter; 
      } catch(e) { 
        instanceIdRef.current = Math.floor(Math.random() * 1000000); 
      }
    }

    const [constraintPopup, setConstraintPopup] = React.useState(null as any);
    
    const nameMapKey = fieldName + '__' + (entity === 'performer' ? 'performerNameMap' : 'tagNameMap');
    if (!compositeRawRef.current[nameMapKey]) {
      compositeRawRef.current[nameMapKey] = {};
    }
    const tagNameMap = compositeRawRef.current[nameMapKey];

    function lookupName(id: number, forEntity?: 'tag'|'performer') {
      const ent = forEntity || entity || 'tag';
      const key = fieldName + '__' + (ent === 'performer' ? 'performerNameMap' : 'tagNameMap');
      const map = compositeRawRef.current[key] || {};
      return map[id] || (ent === 'performer' ? `Performer ${id}` : `Tag ${id}`);
    }

    const debounceTimerRef = React.useRef(null as any);
    const tagInputRef = React.useRef(null) as React.RefObject<HTMLInputElement>;

    // Return the complete tag selector component
    return {
      lookupName,
      searchState,
      setSearchState,
      constraintPopup,
      setConstraintPopup,
      tagInputRef,
      instanceIdRef,
      resolvedAllowedModes,
      include,
      exclude,
      constraints
    };
  }

  // Export utilities to global namespace
  (w as any).AIRecommendationUtils = {
    useDebounce,
    useResizeObserver,
    calculateCardWidth,
    useContainerDimensions,
    useCardWidth,
    ConstraintEditor,
    createTagSelector
  };

  } // End initializeRecommendationUtils
  
  // Wait for dependencies and initialize
  function waitAndInitialize() {
    if (w.PluginApi && w.PluginApi.React) {
      console.log('[RecommendationUtils] Dependencies ready, initializing...');
      initializeRecommendationUtils();
    } else {
      console.log('[RecommendationUtils] Waiting for PluginApi and React...');
      setTimeout(waitAndInitialize, 100);
    }
  }
  
  waitAndInitialize();

})();