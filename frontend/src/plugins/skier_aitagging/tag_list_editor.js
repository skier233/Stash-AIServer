// Tag List Editor Frontend Component for Skier AI Tagging Plugin
// Patches PluginSettings.FieldRenderer to handle tag_list_editor type fields
(function() {
  'use strict';
  
  const w = window;
  
  // Wait for PluginApi and React to be available
  function waitForReact(callback, maxAttempts) {
    maxAttempts = maxAttempts || 50; // Try for up to 5 seconds
    let attempts = 0;
    
    function check() {
      const PluginApi = w.PluginApi;
      if (PluginApi && PluginApi.React) {
        console.log('[SkierAITagging] PluginApi and React now available');
        callback(PluginApi.React);
      } else {
        attempts++;
        if (attempts < maxAttempts) {
          setTimeout(check, 100);
        } else {
          console.error('[SkierAITagging] PluginApi or React not available after', maxAttempts, 'attempts');
        }
      }
    }
    
    check();
  }
  
  // Initialize when React is available
  waitForReact(function(React) {
    const PluginApi = w.PluginApi;
  
  // Helper to make API calls
  function jfetch(url, options) {
    const backendBase = w.AIBackendBase || 'http://localhost:4153';
    const fullUrl = url.startsWith('http') ? url : backendBase + url;
    return fetch(fullUrl, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options && options.headers || {})
      },
      body: options && options.body ? JSON.stringify(options.body) : undefined
    }).then(response => {
      if (!response.ok) {
        return response.json().then(err => {
          throw new Error(err.detail || err.message || 'HTTP ' + response.status);
        }).catch(() => {
          throw new Error('HTTP ' + response.status);
        });
      }
      return response.json();
    });
  }
  
  // Create the TagListEditor component
  function createTagListEditorComponent(props) {
    console.log('[SkierAITagging] createTagListEditorComponent called with props:', props);
    console.log('[SkierAITagging] field.type:', props.field ? props.field.type : 'missing');
    console.log('[SkierAITagging] pluginName:', props.pluginName);
    
    const field = props.field;
    const pluginName = props.pluginName;
    const backendBase = props.backendBase || (w.AIBackendBase || 'http://localhost:4153');
    const savePluginSetting = props.savePluginSetting;
    const loadPluginSettings = props.loadPluginSettings;
    const setError = props.setError;
    
    console.log('[SkierAITagging] Using backendBase:', backendBase);
    console.log('[SkierAITagging] savePluginSetting:', typeof savePluginSetting);
    console.log('[SkierAITagging] loadPluginSettings:', typeof loadPluginSettings);
    console.log('[SkierAITagging] setError:', typeof setError);
    
    const modalOpenState = React.useState(false);
    const modalOpen = modalOpenState[0];
    const setModalOpen = modalOpenState[1];
    
    const availableTagsState = React.useState([]);
    const availableTags = availableTagsState[0];
    const setAvailableTags = availableTagsState[1];
    
    const availableModelsState = React.useState([]);
    const availableModels = availableModelsState[0];
    const setAvailableModels = availableModelsState[1];
    
    const excludedTagsState = React.useState([]);
    const excludedTags = excludedTagsState[0];
    const setExcludedTags = excludedTagsState[1];
    
    const loadingState = React.useState(false);
    const loading = loadingState[0];
    const setLoading = loadingState[1];
    
    const savingState = React.useState(false);
    const saving = savingState[0];
    const setSaving = savingState[1];
    
    const expandedModelsState = React.useState(new Set());
    const expandedModels = expandedModelsState[0];
    const setExpandedModels = expandedModelsState[1];

    const csvDataState = React.useState(null);
    const csvData = csvDataState[0];
    const setCsvData = csvDataState[1];

    const loadTagData = React.useCallback(async function() {
      console.log('[SkierAITagging] loadTagData called for plugin:', pluginName);
      setLoading(true);
      try {
        // Use /available and /statuses endpoints for CSV-based mode
        const availableUrl = `/api/v1/plugins/settings/${pluginName}/tags/available`;
        const statusesUrl = `/api/v1/plugins/settings/${pluginName}/tags/statuses`;
        
        console.log('[SkierAITagging] Fetching from:', availableUrl, statusesUrl);
        
        const [availableResponse, statusesResponse] = await Promise.all([
          jfetch(availableUrl),
          jfetch(statusesUrl)
        ]);
        
        console.log('[SkierAITagging] Available response:', availableResponse);
        console.log('[SkierAITagging] Statuses response:', statusesResponse);
        
        // Extract tags from available endpoint (flat list from CSV)
        const tags = availableResponse.tags || [];
        const statuses = statusesResponse.statuses || {};
        
        console.log('[SkierAITagging] Tags count:', tags.length);
        console.log('[SkierAITagging] Statuses count:', Object.keys(statuses).length);
        
        // Build excluded list from statuses (tags that are disabled)
        const excludedList = [];
        tags.forEach(function(tagInfo) {
          const tagName = tagInfo.tag || tagInfo.name || '';
          const normalized = tagName.toLowerCase();
          // Tag is excluded if status is False
          if (statuses[normalized] === false) {
            excludedList.push(normalized);
          }
        });
        
        console.log('[SkierAITagging] Excluded tags count:', excludedList.length);
        
        setAvailableTags(tags);
        setAvailableModels([]); // Not using models for CSV-based view
        setExcludedTags(excludedList);
        setExpandedModels(new Set());
      } catch (e) {
        console.error('[SkierAITagging] Failed to load tag data:', e);
        console.error('[SkierAITagging] Error stack:', e.stack);
        if (setError) setError(e.message || 'Failed to load tag data');
        setAvailableTags([]);
        setAvailableModels([]);
        setExcludedTags([]);
      } finally {
        setLoading(false);
        console.log('[SkierAITagging] loadTagData completed');
      }
    }, [pluginName, setError]);

    const saveExcludedTags = React.useCallback(async function() {
      console.log('[SkierAITagging] saveExcludedTags called');
      console.log('[SkierAITagging] Excluded tags to save:', excludedTags);
      console.log('[SkierAITagging] Available tags count:', availableTags.length);
      
      setSaving(true);
      try {
        // Build tag_statuses dict: tag name (normalized) -> enabled (not excluded)
        const tagStatuses = {};
        availableTags.forEach(function(tagInfo) {
          const tagName = tagInfo.tag || tagInfo.name || '';
          const normalized = tagName.toLowerCase();
          // Tag is enabled if it's NOT in excludedTags
          tagStatuses[normalized] = excludedTags.indexOf(normalized) < 0;
        });
        
        console.log('[SkierAITagging] Tag statuses to save:', tagStatuses);
        
        const saveUrl = `/api/v1/plugins/settings/${pluginName}/tags/statuses`;
        console.log('[SkierAITagging] Saving statuses to:', saveUrl);
        
        const result = await jfetch(saveUrl, {
          method: 'PUT',
          body: {
            tag_statuses: tagStatuses
          }
        });
        
        console.log('[SkierAITagging] Save result:', result);
        
        setModalOpen(false);
        if (loadPluginSettings) {
          console.log('[SkierAITagging] Reloading plugin settings...');
          await loadPluginSettings(pluginName);
        }
        console.log('[SkierAITagging] Save completed successfully');
      } catch (e) {
        console.error('[SkierAITagging] Failed to save excluded tags:', e);
        console.error('[SkierAITagging] Error stack:', e.stack);
        if (setError) setError(e.message || 'Failed to save excluded tags');
      } finally {
        setSaving(false);
        console.log('[SkierAITagging] saveExcludedTags completed');
      }
    }, [pluginName, excludedTags, availableTags, loadPluginSettings, setError]);

    const wrap = { position: 'relative', padding: '4px 4px 6px', border: '1px solid #2a2a2a', borderRadius: 4, background: '#101010' };
    const smallBtn = { fontSize: 11, padding: '4px 8px', background: '#2a2a2a', color: '#eee', border: '1px solid #444', borderRadius: 3, cursor: 'pointer' };
    const labelTitle = field && field.description ? String(field.description) : undefined;
    const labelEl = React.createElement('span', { title: labelTitle }, field.label || field.key);

    function toggleTag(tagName) {
      setExcludedTags(function(prev) {
        const normalized = tagName.toLowerCase();
        if (prev.indexOf(normalized) >= 0) {
          return prev.filter(function(t) { return t !== normalized; });
        } else {
          return prev.concat([normalized]);
        }
      });
    }
    
    // Sort tags alphabetically
    const sortedTags = availableTags.slice().sort(function(a, b) {
      const nameA = (a.tag || a.name || '').toLowerCase();
      const nameB = (b.tag || b.name || '').toLowerCase();
      return nameA.localeCompare(nameB);
    });

    console.log('[SkierAITagging] Rendering TagListEditor component');
    console.log('[SkierAITagging] Modal open:', modalOpen);
    console.log('[SkierAITagging] Loading:', loading);
    console.log('[SkierAITagging] Available tags:', availableTags.length);
    
    return React.createElement(React.Fragment, null,
      React.createElement('div', { style: wrap },
        React.createElement('div', { style: { fontSize: 12, marginBottom: 6 } }, labelEl),
        React.createElement('button', {
          style: smallBtn,
          onClick: function() {
            console.log('[SkierAITagging] Edit Tags button clicked');
            setModalOpen(true);
            loadTagData();
          }
        }, 'Edit Tags')
      ),
      modalOpen && React.createElement('div', {
        style: {
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0,0,0,0.7)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 10000
        },
        onClick: function() {
          if (!saving) setModalOpen(false);
        }
      },
        React.createElement('div', {
          style: {
            background: '#1e1e1e',
            border: '1px solid #444',
            borderRadius: 8,
            padding: 20,
            maxWidth: '90vw',
            maxHeight: '90vh',
            width: 800,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden'
          },
          onClick: function(e) { e.stopPropagation(); }
        },
          React.createElement('div', {
            style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }
          },
            React.createElement('h3', { style: { margin: 0, fontSize: 18 } }, 'Edit Tagging Configuration'),
            React.createElement('button', {
              style: smallBtn,
              onClick: function() { setModalOpen(false); },
              disabled: saving
            }, '×')
          ),
          React.createElement('div', {
            style: { fontSize: 11, color: '#aaa', marginBottom: 16, lineHeight: 1.4, padding: '0 4px' }
          }, 'Toggle tags on or off. Unchecked tags will be excluded from tag generation. Changes are saved to tag_settings.csv.'),
          loading ? React.createElement('div', {
            style: { padding: 40, textAlign: 'center', fontSize: 12, opacity: 0.7 }
          }, 'Loading tags from CSV...') :
          availableTags.length === 0 ? React.createElement('div', {
            style: { padding: 40, textAlign: 'center', fontSize: 12, opacity: 0.7 }
          }, 'No tags available in tag_settings.csv.') :
          React.createElement(React.Fragment, null,
            React.createElement('div', {
              style: { flex: 1, overflow: 'auto', border: '1px solid #333', borderRadius: 4, padding: 8, background: '#111', marginBottom: 16 }
            },
              sortedTags.map(function(tagInfo) {
                const tagName = tagInfo.tag || tagInfo.name || '';
                const normalized = tagName.toLowerCase();
                const isExcluded = excludedTags.indexOf(normalized) >= 0;
                
                return React.createElement('label', {
                  key: normalized,
                  style: {
                    display: 'flex',
                    alignItems: 'center',
                    padding: '6px 8px',
                    fontSize: 11,
                    cursor: 'pointer',
                    borderRadius: 3,
                    marginBottom: 2
                  },
                  onMouseEnter: function(e) { e.currentTarget.style.background = '#1a1a1a'; },
                  onMouseLeave: function(e) { e.currentTarget.style.background = 'transparent'; }
                },
                  React.createElement('input', {
                    type: 'checkbox',
                    checked: !isExcluded,
                    onChange: function() { toggleTag(tagName); },
                    style: { marginRight: 8 }
                  }),
                  React.createElement('span', { style: { color: isExcluded ? '#666' : '#eee' } }, tagName)
                );
              })
            ),
            React.createElement('div', {
              style: { display: 'flex', justifyContent: 'flex-end', gap: 8 }
            },
              React.createElement('button', {
                style: smallBtn,
                onClick: function() { setModalOpen(false); },
                disabled: saving
              }, 'Cancel'),
              React.createElement('button', {
                style: Object.assign({}, smallBtn, { background: saving ? '#444' : '#2d5a3d', borderColor: saving ? '#555' : '#4a7c59' }),
                onClick: saveExcludedTags,
                disabled: saving
              }, saving ? 'Saving...' : 'Save')
            )
          )
        )
      )
    );
  }
  
    // Store globally so PluginSettings can access it
    // Register with multiple naming conventions for compatibility
    w.SkierAITaggingTagListEditor = createTagListEditorComponent; // Legacy naming
    w.tag_list_editor_Renderer = createTagListEditorComponent; // Standard naming convention
    w.skier_aitagging_tag_list_editor_Renderer = createTagListEditorComponent; // Plugin-specific naming
    console.log('[SkierAITagging] Component registered to multiple names:', {
      SkierAITaggingTagListEditor: typeof w.SkierAITaggingTagListEditor,
      tag_list_editor_Renderer: typeof w.tag_list_editor_Renderer,
      skier_aitagging_tag_list_editor_Renderer: typeof w.skier_aitagging_tag_list_editor_Renderer
    });
    
    // Try to patch PluginSettings dynamically
    function patchPluginSettings() {
      console.log('[SkierAITagging] patchPluginSettings called');
      console.log('[SkierAITagging] AIPluginSettings available:', !!w.AIPluginSettings);
      console.log('[SkierAITagging] PluginApi.patch available:', !!PluginApi.patch);
      
      // Wait for PluginSettings to be available
      if (!w.AIPluginSettings) {
        console.log('[SkierAITagging] AIPluginSettings not yet available, retrying in 100ms...');
        setTimeout(patchPluginSettings, 100);
        return;
      }
      
      console.log('[SkierAITagging] AIPluginSettings found, attempting to patch FieldRenderer...');
      
      // Try to use PluginApi.patch if available
      if (PluginApi.patch && PluginApi.patch.before) {
        try {
          console.log('[SkierAITagging] Attempting to patch PluginSettings.FieldRenderer using PluginApi.patch.before');
          // This won't work directly since FieldRenderer is internal, but we can try
          // The real solution is PluginSettings needs to check for our component
          console.log('[SkierAITagging] PluginApi.patch.before available but FieldRenderer is internal');
        } catch (e) {
          console.error('[SkierAITagging] Error patching:', e);
        }
      }
      
      // Store a flag that PluginSettings can check
      w.SkierAITaggingTagListEditorReady = true;
      console.log('[SkierAITagging] Set window.SkierAITaggingTagListEditorReady = true');
      console.log('[SkierAITagging] Component function:', w.SkierAITaggingTagListEditor);
      console.log('[SkierAITagging] NOTE: PluginSettings.tsx FieldRenderer must check for tag_list_editor type and use window.SkierAITaggingTagListEditor');
    }
    
    // Try patching immediately
    console.log('[SkierAITagging] Initial patch attempt...');
    patchPluginSettings();
    
    // Also listen for the ready event
    w.addEventListener('AIPluginSettingsReady', function() {
      console.log('[SkierAITagging] AIPluginSettingsReady event received');
      patchPluginSettings();
    });
    
    // Also try after a delay
    setTimeout(function() {
      console.log('[SkierAITagging] Delayed patch attempt (2s)...');
      patchPluginSettings();
    }, 2000);
    
    console.log('[SkierAITagging] Tag list editor support loaded and registered');
    console.log('[SkierAITagging] To verify: window.SkierAITaggingTagListEditor =', typeof w.SkierAITaggingTagListEditor);
  }); // End of waitForReact callback
})();
