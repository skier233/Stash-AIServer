(function(){
// =============================================================================
// Minimal AI Button Integration - Simple Integration for Context Display
// =============================================================================
(function () {
    const PluginApi = window.PluginApi;
    const React = PluginApi.React;
    // Do NOT force-set AI_BACKEND_URL here; frontend components will handle fallbacks.
    // Add the minimal button to the main navigation
    PluginApi.patch.before('MainNavBar.UtilityItems', function (props) {
        // Check if MinimalAIButton is available
        const MinimalAIButton = window.MinimalAIButton;
        if (!MinimalAIButton) {
            console.warn('MinimalAIButton not available yet, skipping integration');
            return [{ children: props.children }];
        }
        return [
            {
                children: React.createElement('div', {
                    style: { display: 'flex', alignItems: 'center' }
                }, [
                    props.children,
                    React.createElement('div', {
                        key: 'minimal-ai-button-wrapper',
                        style: { marginRight: '8px' }
                    }, React.createElement(MinimalAIButton))
                ])
            }
        ];
    });
    console.log('🚀 Minimal AI Button integration loaded');
})();
})();
