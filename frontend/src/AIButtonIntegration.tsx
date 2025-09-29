// =============================================================================
// Unified Integration for AI Button + Task Dashboard
//  - Injects MinimalAIButton into MainNavBar.UtilityItems
//  - Registers /plugins/ai-tasks route mounting TaskDashboard
//  - Adds SettingsToolsSection entry linking to the dashboard
//  - Adds simple "AI" nav utility link (in case button not visible)
//  - All logging gated by window.AIDebug
// =============================================================================
(function(){
  const g:any = window as any;
  const PluginApi = g.PluginApi;
  if(!PluginApi){ console.warn('[AIIntegration] PluginApi not ready'); return; }
  const React = PluginApi.React;
  const debug = !!g.AIDebug;
  const dlog = (...a:any[]) => { if (debug) console.log('[AIIntegration]', ...a); };

  // Helper to safely get components
  const Button = (PluginApi.libraries?.Bootstrap?.Button) || ((p:any)=>React.createElement('button', p, p.children));
  const { Link, NavLink } = PluginApi.libraries?.ReactRouterDOM || {} as any;

  function getMinimalButton(){ return g.MinimalAIButton || g.AIButton; }
  function getTaskDashboard(){ return g.TaskDashboard || g.AITaskDashboard; }
  function getPluginSettings(){ return g.AIPluginSettings; }

  // Main nav utility items: inject AI button + nav link
  try {
    PluginApi.patch.before('MainNavBar.UtilityItems', function(props:any){
      const MinimalAIButton = getMinimalButton();
      const children: any[] = [props.children];
      if (MinimalAIButton) {
        children.push(React.createElement('div', { key:'ai-btn-wrap', style:{marginRight:8, display:'flex', alignItems:'center'}}, React.createElement(MinimalAIButton)));
      }
      return [{ children }];
    });
    dlog('Patched MainNavBar.UtilityItems');
  } catch(e){ if (debug) console.warn('[AIIntegration] main nav patch failed', e); }

  // Register dashboard route
  try {
    PluginApi.register.route('/plugins/ai-tasks', () => {
      const Dash = getTaskDashboard();
      return Dash ? React.createElement(Dash, {}) : React.createElement('div', { style:{padding:16}}, 'Loading AI Tasks...');
    });
    dlog('Registered /plugins/ai-tasks route');
  } catch(e){ if (debug) console.warn('[AIIntegration] route register failed', e); }

  // Register settings route (event-driven, no polling)
  try {
    const SettingsWrapper = () => {
      const [Comp, setComp] = React.useState(()=> getPluginSettings());
      React.useEffect(() => {
        if (Comp) return; // already there
        const handler = () => {
          const found = getPluginSettings();
          if (found) {
            if (debug) console.debug('[AIIntegration] AIPluginSettingsReady event captured');
            setComp(() => found);
          }
        };
        window.addEventListener('AIPluginSettingsReady', handler);
        // one immediate async attempt (in case script loaded right after)
        setTimeout(handler, 0);
        return () => window.removeEventListener('AIPluginSettingsReady', handler);
      }, [Comp]);
      const C = Comp;
      return C ? React.createElement(C, {}) : React.createElement('div', { style:{padding:16}}, 'Loading AI Plugin Settings...');
    };
    PluginApi.register.route('/plugins/ai-settings', () => React.createElement(SettingsWrapper));
    dlog('Registered /plugins/ai-settings route (event)');
  } catch(e){ if (debug) console.warn('[AIIntegration] settings route register failed', e); }

  // Settings tools entry
  try {
    PluginApi.patch.before('SettingsToolsSection', function(props:any){
      const Setting = PluginApi.components?.Setting;
      if(!Setting) return props;
      return [{ children: (<>
        {props.children}
        <Setting heading={
          Link ? <Link to="/plugins/ai-tasks"><Button>AI Tasks</Button></Link> : React.createElement(Button, { onClick:()=> (location.href = '/plugins/ai-tasks') }, 'AI Tasks')
        } />
        <Setting heading={
          Link ? <Link to="/plugins/ai-settings"><Button>AI Plugin Settings</Button></Link> : React.createElement(Button, { onClick:()=> (location.href = '/plugins/ai-settings') }, 'AI Plugin Settings')
        } />
      </>)}];
    });
    dlog('Patched SettingsToolsSection');
  } catch(e){ if (debug) console.warn('[AIIntegration] settings tools patch failed', e); }

  if (debug) console.log('[AIIntegration] Unified integration loaded');
})();