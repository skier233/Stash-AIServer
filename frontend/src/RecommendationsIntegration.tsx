(function(){
  const w:any = window as any;
  const PluginApi = w.PluginApi;
  if(!PluginApi || !PluginApi.React){ return; }
  const React = PluginApi.React;
  const ROUTE = '/plugins/recommendations';

  // Lightweight wrapper that defers loading of panel until needed.
  function PanelWrapper(){
    const Panel = (w as any).RecommendationPanel;
    const types = (w as any).RecTypes;
    // If our TS-built harness exported globals differently, attempt fallback.
    if(!Panel){ return React.createElement('div', { style:{padding:16}}, 'Loading recommender harness...'); }
    const RecContext = types?.RecContext || (w as any).RecContext;
    return React.createElement(Panel, { context: RecContext?.GlobalFeed || 'global_feed', limit: 60 });
  }

  try {
    PluginApi.register.route(ROUTE, PanelWrapper);
  } catch(e){ if(w.AIDebug) console.warn('[RecommendationsIntegration] route register failed', e); }

  // Add nav link (similar patch style as other integration)
  try {
    PluginApi.patch.before('MainNavBar.MenuItems', function(props:any){
      try {
        const existing = React.Children.toArray(props.children).some((c:any)=> c?.props?.to === ROUTE || c?.props?.children?.props?.to === ROUTE);
        if (existing) return props;
      } catch {}
      const { NavLink } = PluginApi.libraries?.ReactRouterDOM || {} as any;
      const label = 'AI Recs';
      const node = React.createElement(
        'div',
        { key:'ai-recs-link', className:'col-4 col-sm-3 col-md-2 col-lg-auto' },
        NavLink ? React.createElement(NavLink, { exact:true, to:ROUTE, activeClassName:'active', className:'btn minimal p-4 p-xl-2 d-flex d-xl-inline-block flex-column justify-content-between align-items-center' }, label)
                : React.createElement('a', { href:'#'+ROUTE, className:'btn minimal p-4 p-xl-2 d-flex d-xl-inline-block flex-column justify-content-between align-items-center'}, label)
      );
      return [{ children: (<>{props.children}{node}</>) }];
    });
  } catch(e){ if(w.AIDebug) console.warn('[RecommendationsIntegration] nav patch failed', e); }
})();
