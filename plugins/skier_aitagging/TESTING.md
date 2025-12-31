# Testing the Tag List Editor

## Files Built and Present

✅ **Backend Files:**
- `service.py` - Updated with `get_available_tags_data(include_disabled=True)` and async `get_all_tag_statuses()`
- `tag_config.py` - Handles reading/writing to `tag_settings.csv`
- `api_endpoints.py` - API endpoint code (needs to be added to backend)

✅ **Frontend Files:**
- `tag_list_editor.js` - Component with exhaustive logging (20,921 bytes)
- `PluginSettings.tsx` - Updated to check for `tag_list_editor` type and use `window.SkierAITaggingTagListEditor`

✅ **Configuration:**
- `plugin.yml` - Has `edit_tag_list` field with `type: tag_list_editor`

## Verification Steps

### 1. Verify Files Are Present
```powershell
# Check plugin files
cd C:\Users\KittyTricks\Documents\stash_tagging\Stash-AIServer\plugins\skier_aitagging
dir tag_list_editor.js
dir service.py
dir tag_config.py
```

### 2. Verify JavaScript Component Registration

Open browser console (F12) and check:

```javascript
// Should return "function"
typeof window.SkierAITaggingTagListEditor

// Should return true
window.SkierAITaggingTagListEditorReady

// Check for logging
// Look for messages starting with [SkierAITagging]
```

### 3. Verify PluginSettings Integration

In browser console, check if PluginSettings detects the component:

```javascript
// Should see logs when rendering settings page
// Look for: [PluginSettings] Using SkierAITaggingTagListEditor for tag_list_editor field
// OR
// [PluginSettings] tag_list_editor type detected but SkierAITaggingTagListEditor not available
```

### 4. Check Backend API Endpoints

The API endpoints need to be added to `stash_ai_server/api/plugins.py`. Check if they exist:

```bash
# Check if endpoints are registered (after adding api_endpoints.py code)
curl http://localhost:4153/api/v1/plugins/settings/skier_aitagging/tags/available
curl http://localhost:4153/api/v1/plugins/settings/skier_aitagging/tags/statuses
```

## Testing Procedure

### Step 1: Load the JavaScript File

The `tag_list_editor.js` file needs to be loaded by the browser. Since it's in the plugin directory, you have two options:

**Option A: Add to AIOverhaul.yml** (Recommended)
Add this line to `Stash-AIServer/frontend/src/AIOverhaul.yml`:
```yaml
ui:
  javascript:
    # ... existing files ...
    - plugins/skier_aitagging/tag_list_editor.js  # Add this
```

Then rebuild the frontend:
```bash
cd Stash-AIServer/frontend
npm run build
```

**Option B: Load via Script Tag** (Quick test)
Add a script tag in the browser console or via a browser extension:
```javascript
const script = document.createElement('script');
script.src = '/plugins/skier_aitagging/tag_list_editor.js';
document.head.appendChild(script);
```

### Step 2: Add Backend API Endpoints

Copy the endpoint code from `api_endpoints.py` to `Stash-AIServer/backend/stash_ai_server/api/plugins.py`:

1. Add the import: `from stash_ai_server.services import registry as services_registry`
2. Add the `TagStatusUpdate` model class
3. Add the three endpoint functions after the existing plugin settings endpoints
4. Restart the backend server

### Step 3: Test the Component

1. Navigate to Plugin Settings page in Stash
2. Find the "Skier AI Tagging" plugin settings
3. Look for "Edit Tag List" field
4. **Expected:** Should see an "Edit Tags" button (not a text input)
5. Click "Edit Tags" button
6. **Expected:** Modal opens with tags grouped by model
7. Check/uncheck tags
8. Click "Save"
9. **Expected:** Changes saved to `tag_settings.csv`

## Debugging

### Check Console Logs

Open browser console and look for these log messages:

**On Page Load:**
```
[SkierAITagging] tag_list_editor.js loading...
[SkierAITagging] PluginApi and React available
[SkierAITagging] Component registered to window.SkierAITaggingTagListEditor: function
[SkierAITagging] Set window.SkierAITaggingTagListEditorReady = true
```

**When Rendering Settings:**
```
[PluginSettings] Using SkierAITaggingTagListEditor for tag_list_editor field
[SkierAITagging] createTagListEditorComponent called with props: {...}
[SkierAITagging] Rendering TagListEditor component
```

**When Clicking Edit Tags:**
```
[SkierAITagging] Edit Tags button clicked
[SkierAITagging] loadTagData called for plugin: skier_aitagging
[SkierAITagging] Fetching tags from: /api/v1/plugins/settings/skier_aitagging/tags/available
[SkierAITagging] Fetching statuses from: /api/v1/plugins/settings/skier_aitagging/tags/statuses
```

### Common Issues

1. **Text input instead of button:**
   - JavaScript file not loaded → Add to AIOverhaul.yml and rebuild
   - Component not registered → Check console for registration logs
   - PluginSettings not detecting type → Check console for detection logs

2. **Modal doesn't open:**
   - API endpoints not added → Add endpoints to plugins.py
   - Backend not running → Start backend server
   - CORS issues → Check network tab for failed requests

3. **No tags showing:**
   - AI server not configured → Set `server_url` in plugin settings
   - AI server not running → Start AI server on port 8000
   - Check console for API errors

## Expected Behavior

✅ **Correct:**
- "Edit Tag List" field shows an "Edit Tags" button
- Clicking button opens modal with tags grouped by model
- Tags show checkboxes (checked = enabled, unchecked = disabled)
- Can expand/collapse model sections
- Can use "All" and "None" buttons per model
- Saving updates `tag_settings.csv` file

❌ **Incorrect:**
- Text input field (component not loaded)
- Button doesn't open modal (API endpoints missing)
- Modal opens but no tags (AI server not configured/running)
- Changes don't persist (CSV write permissions issue)
