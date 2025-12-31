# Tag List Editor Integration

This plugin provides a tag list editor UI component that allows users to exclude specific tags from AI tagging.

## Frontend Component

The frontend component is in `tag_list_editor.js`. This file needs to be loaded by the AIOverhaul plugin.

### Loading the Component

Add `tag_list_editor.js` to the AIOverhaul plugin's `AIOverhaul.yml` file in the `ui.javascript` section:

```yaml
ui:
  javascript:
    # ... existing files ...
    - skier_aitagging/tag_list_editor.js  # Add this line
```

**Note:** The file path should be relative to where AIOverhaul loads its JavaScript files. You may need to copy `tag_list_editor.js` to the appropriate location or adjust the path.

### PluginSettings Integration

The `PluginSettings.tsx` component needs to check for `window.SkierAITaggingTagListEditor` when rendering fields with `type: tag_list_editor`.

Add this check in the `FieldRenderer` function in `PluginSettings.tsx`:

```typescript
if (t === 'tag_list_editor') {
  const TagListEditor = (window as any).SkierAITaggingTagListEditor;
  if (TagListEditor) {
    return React.createElement(TagListEditor, {
      field: f,
      pluginName: pluginName,
      backendBase: backendBase,
      savePluginSetting: savePluginSetting,
      loadPluginSettings: loadPluginSettings,
      setError: setError
    });
  }
  // Fallback to text input if component not available
  return (
    <div style={wrap}>
      <label style={{ fontSize: 12 }}>{labelEl}<br />
        <input
          style={inputStyle}
          defaultValue={savedValue || ''}
          disabled
          placeholder="Tag list editor not available"
        />
      </label>
    </div>
  );
}
```

## Backend API Endpoints

The backend needs API endpoints to expose the plugin's tag management functionality. See `api_endpoints.py` for the endpoint code that needs to be added to `stash_ai_server/api/plugins.py`.

The endpoints are:
- `GET /api/v1/plugins/settings/{plugin_name}/tags/available` - Get available tags
- `GET /api/v1/plugins/settings/{plugin_name}/tags/statuses` - Get tag enabled statuses  
- `PUT /api/v1/plugins/settings/{plugin_name}/tags/statuses` - Update tag enabled statuses

## Usage

Once integrated, users can click the "Edit Tags" button next to the "Edit Tag List" setting to open a modal where they can:
- View all available tags grouped by model
- Expand/collapse model sections
- Check/uncheck individual tags to enable/disable them
- Use "All" and "None" buttons to quickly enable/disable all tags for a model
- Save changes which will update the `tag_settings.csv` file
