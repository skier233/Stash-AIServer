# Logging Summary: Tag Exclusion Debugging

## Changes Made

### 1. HTTP Handler (`plugins/skier_aitagging/http_handler.py`)
**Added logging to:**
- `call_scene_api()`: Logs excluded tags retrieved from system settings and when they're added to payload
- `call_images_api()`: Logs excluded tags retrieved from system settings and when they're added to payload

**What to look for:**
```
[INFO] call_scene_api: Retrieved excluded_tags from system settings: ['tag1', 'tag2'] (count=2)
[INFO] call_scene_api: Added excluded_tags to payload: ['tag1', 'tag2']
[DEBUG] call_scene_api: Sending request to /v3/process_video/ with payload keys: [...]
```

### 2. Scene Tagging (`plugins/skier_aitagging/scene_tagging.py`)
**Added logging to:**
- `apply_scene_tags()`: 
  - Logs excluded tags retrieved from system settings
  - Logs tag totals before and after filtering excluded tags
  - Logs which tags are being excluded (by tag_id and name)
  - Logs final tag counts (applied and removed)

**What to look for:**
```
[INFO] apply_scene_tags: Retrieved excluded_tag_names from system settings: ['tag1', 'tag2'] (count=2) for scene_id=123
[INFO] apply_scene_tags: Retrieved tag totals for scene_id=123: 50 tags before filtering
[DEBUG] apply_scene_tags: Excluding tag_id=456 (name='tag1') from scene_id=123
[INFO] apply_scene_tags: Filtering out 2 excluded tags (tag_ids: [456, 789]) from scene_id=123
[INFO] apply_scene_tags: Tag totals after filtering excluded tags: 48 tags for scene_id=123
[INFO] apply_scene_tags: Final tag counts for scene_id=123: applied=48 tags (tag_ids: [...]), removed=0 tags (tag_ids: [])
```

**IMPORTANT FIX:** This also adds the actual filtering logic - excluded tags are now filtered from `aggregate_totals` before counting, which was the root cause of the issue.

### 3. Logic (`plugins/skier_aitagging/logic.py`)
**Added logging to:**
- `tag_scene_task()`: 
  - Logs when task starts with scene_id
  - Logs service name
  - Logs when scene tagging runs (with skip_categories)
  - Logs when response is received from AI model server
  - Logs final applied tag counts (applied, removed, markers)

**What to look for:**
```
[INFO] tag_scene_task: Starting scene tagging task for scene_id=123
[INFO] tag_scene_task: Service name=AI_Tagging for scene_id=123
[INFO] tag_scene_task: Running scene tagging for scene_id=123; skipping categories=('category1',)
[INFO] tag_scene_task: Received response from AI model server for scene_id=123
[INFO] tag_scene_task: Applied tags count for scene_id=123: applied=48, removed=0, markers=5
```

### 4. Actions API (`backend/stash_ai_server/api/actions.py`)
**Added logging to:**
- `submit_action()`: Logs action submission with action_id, context details, and resulting task_id

**What to look for:**
```
[INFO] submit_action: Action submission request - action_id=skier.ai_tag.scene, page=scenes, entity_id=123, is_detail_view=True
[INFO] submit_action: Task submitted successfully - action_id=skier.ai_tag.scene, task_id=abc123, priority=high, entity_id=123
```

### 5. System Settings API (`backend/stash_ai_server/api/plugins.py`)
**Added logging to:**
- `upsert_system_setting()`: Logs when EXCLUDED_TAGS are saved, including before/after values and verification

**What to look for:**
```
[INFO] upsert_system_setting: Received request to update setting key=EXCLUDED_TAGS
[INFO] upsert_system_setting: Updating EXCLUDED_TAGS - previous value: [], new value: ['tag1', 'tag2']
[INFO] upsert_system_setting: Successfully saved EXCLUDED_TAGS - saved value: ['tag1', 'tag2'] (type: list)
[INFO] upsert_system_setting: Verified EXCLUDED_TAGS retrieval after save - retrieved: ['tag1', 'tag2'] (type: list)
```

### 6. System Settings Get (`backend/stash_ai_server/core/system_settings.py`)
**Added logging to:**
- `get_value()`: Logs when EXCLUDED_TAGS are retrieved (debug level)

**What to look for:**
```
[DEBUG] get_value: Retrieved EXCLUDED_TAGS - value: ['tag1', 'tag2'] (type: list), default: []
```

## Complete Flow Log Sequence

### When Saving Excluded Tags (in Plugin Settings UI):

1. **Save Request:**
   ```
   [INFO] upsert_system_setting: Received request to update setting key=EXCLUDED_TAGS
   [INFO] upsert_system_setting: Updating EXCLUDED_TAGS - previous value: [...], new value: ['tag1', 'tag2']
   [INFO] upsert_system_setting: Successfully saved EXCLUDED_TAGS - saved value: ['tag1', 'tag2'] (type: list)
   [INFO] upsert_system_setting: Verified EXCLUDED_TAGS retrieval after save - retrieved: ['tag1', 'tag2'] (type: list)
   ```

### When Clicking "AI tag scene":

1. **Action Submission:**
   ```
   [INFO] submit_action: Action submission request - action_id=skier.ai_tag.scene, ...
   [INFO] submit_action: Task submitted successfully - action_id=skier.ai_tag.scene, task_id=..., ...
   ```
   **Note:** The action submission payload does NOT include `excluded_tags` - this is expected! Excluded tags are system settings retrieved later in the flow.

2. **Task Handler Start:**
   ```
   [INFO] tag_scene_task: Starting scene tagging task for scene_id=123
   [INFO] tag_scene_task: Service name=AI_Tagging for scene_id=123
   ```

3. **Excluded Tags Retrieved (HTTP Handler):**
   ```
   [INFO] call_scene_api: Retrieved excluded_tags from system settings: ['tag1', 'tag2'] (count=2)
   [INFO] call_scene_api: Added excluded_tags to payload: ['tag1', 'tag2']
   ```

4. **Scene Tagging - Excluded Tags Filtering:**
   ```
   [INFO] apply_scene_tags: Retrieved excluded_tag_names from system settings: ['tag1', 'tag2'] (count=2) for scene_id=123
   [INFO] apply_scene_tags: Retrieved tag totals for scene_id=123: 50 tags before filtering
   [INFO] apply_scene_tags: Filtering out 2 excluded tags (tag_ids: [456, 789]) from scene_id=123
   [INFO] apply_scene_tags: Tag totals after filtering excluded tags: 48 tags for scene_id=123
   ```

5. **Final Count:**
   ```
   [INFO] apply_scene_tags: Final tag counts for scene_id=123: applied=48 tags (tag_ids: [...]), removed=0 tags (tag_ids: [])
   [INFO] tag_scene_task: Applied tags count for scene_id=123: applied=48, removed=0, markers=5
   ```

## Key Debugging Points

### If excluded tags aren't working:

1. **Check system settings retrieval:**
   - Look for: `call_scene_api: Retrieved excluded_tags from system settings: ...`
   - If empty list `[]`, check database/UI settings
   - If None or error, check system_settings.py

2. **Check payload construction:**
   - Look for: `call_scene_api: Added excluded_tags to payload: ...`
   - If this log doesn't appear, excluded_tags was empty
   - Verify payload includes `excluded_tags` key

3. **Check tag filtering:**
   - Look for: `apply_scene_tags: Filtering out N excluded tags ...`
   - If this doesn't appear, excluded tags weren't filtered
   - Check that tag names match between excluded list and actual tags

4. **Check final count:**
   - Look for: `apply_scene_tags: Final tag counts ...`
   - Compare "before filtering" count vs "after filtering" count
   - The difference should equal the number of excluded tags found

## Testing Checklist

- [ ] Excluded tags are retrieved from system settings (check logs)
- [ ] Excluded tags are added to payload sent to AI model server (check logs)
- [ ] Excluded tags are filtered when counting (check "before" vs "after" counts)
- [ ] Final count matches actual tags applied (verify in Stash UI)
- [ ] Excluded tags do not appear on the scene after tagging

## Notes

- All logging uses `[INFO]` level for important flow points and `[DEBUG]` for detailed information
- Tag IDs are logged as sorted lists for easier comparison
- The fix ensures excluded tags are filtered both:
  1. When sent to AI model server (so it doesn't return them)
  2. When counting tags from storage (in case they were previously stored)

