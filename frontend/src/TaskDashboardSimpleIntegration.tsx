// Simplified integration modeled after testreact sample
(function(){
  const g:any = window as any;
  const PluginApi = g.PluginApi;
  if(!PluginApi){ console.warn('[TaskDashboardSimpleIntegration] PluginApi not ready'); return; }
  const React = PluginApi.React;
  // Backend base auto detection: if not explicitly provided, assume same-origin.
  (function ensureBackendBase(){
  })();
  const { Link, NavLink } = PluginApi.libraries.ReactRouterDOM || {};
  const { Button, Nav, Tab } = (PluginApi.libraries && PluginApi.libraries.Bootstrap) || { Button: (p:any)=>React.createElement('button',p,p.children) };

  // Ensure dashboard component (prefer existing global or inline minimal fallback)
  function ensureDashboard(){
    if (g.TaskDashboard) return g.TaskDashboard;
    const Comp = () => React.createElement('div',{style:{padding:16}},'Loading AI Tasks...');
    return Comp;
  }
  const TaskDashComp = ensureDashboard();

  // Register a route under /plugins/ai-tasks (consistent with sample route approach)
  try {
    PluginApi.register.route('/plugins/ai-tasks', () => React.createElement(TaskDashComp, {}));
    console.log('[TaskDashboardSimpleIntegration] Registered /plugins/ai-tasks route');
  } catch(e){ console.warn('[TaskDashboardSimpleIntegration] route register failed', e); }

  // Add settings tools entry (puts a button in settings tools section)
  try {
    PluginApi.patch.before('SettingsToolsSection', function(props:any){
      const Setting = PluginApi.components?.Setting;
      if(!Setting) return props;
      return [{ children: (<>
        {props.children}
        <Setting heading={
          <Link to="/plugins/ai-tasks">
            <Button>AI Tasks</Button>
          </Link>
        } />
      </>)}];
    });
  } catch(e){ console.warn('[TaskDashboardSimpleIntegration] settings tools patch failed', e); }

  // Add navbar utility icon (text button fallback)
  try {
    PluginApi.patch.before('MainNavBar.UtilityItems', function(props:any){
      return [{ children: (<>
        {props.children}
        <NavLink className="nav-utility" exact to="/plugins/ai-tasks">
          <Button className="minimal d-flex align-items-center h-100" title="AI Tasks">AI</Button>
        </NavLink>
      </>)}];
    });
  } catch(e){ console.warn('[TaskDashboardSimpleIntegration] main nav patch failed', e); }

  // (Optional) Scene page tab injection - disabled for now
})();
