# Testing Plan: Tag Exclusion Flow

## Overview
This document outlines the testing plan for verifying that tag exclusions are properly respected when tagging scenes via the "AI tag scene" button.

## Flow Diagram
```
User clicks "AI tag scene" 
  → Frontend: POST /api/v1/actions/submit
    (Note: excluded_tags NOT in payload - they're system settings)
  → Backend: Resolve action, submit task
  → Task Handler: tag_scene_task() in logic.py
  → HTTP Handler: call_scene_api() in http_handler.py
    (excluded_tags retrieved from system settings HERE)
  → AI Model Server: POST /v3/process_video/ (with excluded_tags in payload)
  → AI Model Server: Returns filtered results
  → Backend: Store results in DB
  → Backend: apply_scene_tags() counts and applies tags
    (excluded_tags filtered again HERE when counting)
  → Frontend: Receives task completion with tags_applied count
```

**Important:** Excluded tags are NOT passed in the action submission payload. They are:
1. Retrieved from system settings in `http_handler.py` when calling the AI model server
2. Retrieved again in `scene_tagging.py` when counting/apply tags from storage

## Endpoints to Check

### 1. Frontend → Backend: Action Submission
**Endpoint:** `POST /api/v1/actions/submit`
- **Location:** `backend/stash_ai_server/api/actions.py:38`
- **What to check:**
  - Request includes `action_id` (should be `skier.ai_tag.scene` or similar)
  - Request includes `context` with `entityId` (scene ID)
  - Response includes `task_id`
  - **Note:** `excluded_tags` are NOT in this payload - they're system settings retrieved later

**Expected Logs:**
- Action resolution log
- Task submission log with task_id
- **Expected Payload:**
  ```json
  {
    "action_id": "skier.ai_tag.scene",
    "context": {
      "page": "scenes",
      "entityId": "106764",
      "isDetailView": true
    },
    "params": {}
  }
  ```
  (No `excluded_tags` here - this is expected!)

### 2. Backend → AI Model Server: Scene Processing
**Endpoint:** `POST /v3/process_video/` (external AI model server)
- **Location:** `plugins/skier_aitagging/http_handler.py:50` (`call_scene_api`)
- **What to check:**
  - Payload includes `excluded_tags` array
  - `excluded_tags` contains the correct tag names from system settings
  - Payload structure is correct

**Expected Logs:**
- Log showing excluded_tags being retrieved from system settings
- Log showing excluded_tags being added to payload
- Log showing full payload being sent (or at least confirmation of excluded_tags presence)

### 3. System Settings: Excluded Tags Save
**Endpoint:** `PUT /api/v1/plugins/system/settings/EXCLUDED_TAGS`
- **Location:** `backend/stash_ai_server/api/plugins.py:543` (`upsert_system_setting`)
- **What to check:**
  - Called when user clicks "OK" in plugin settings
  - Excluded tags are saved to database
  - Cache is invalidated after save
  - Verification that tags can be retrieved after save

**Expected Logs:**
```
[INFO] upsert_system_setting: Received request to update setting key=EXCLUDED_TAGS
[INFO] upsert_system_setting: Updating EXCLUDED_TAGS - previous value: [...], new value: ['tag1', 'tag2']
[INFO] upsert_system_setting: Successfully saved EXCLUDED_TAGS - saved value: ['tag1', 'tag2'] (type: list)
[INFO] upsert_system_setting: Verified EXCLUDED_TAGS retrieval after save - retrieved: ['tag1', 'tag2'] (type: list)
```

### 4. System Settings: Excluded Tags Retrieval
**Endpoint:** `GET /api/v1/plugins/system/tags/excluded`
- **Location:** `backend/stash_ai_server/api/plugins.py:510`
- **What to check:**
  - Returns list of excluded tag names
  - Matches what's configured in UI

**Expected Logs:**
- Log when excluded tags are retrieved

### 5. Tag Counting: Applied Tags
**Location:** `plugins/skier_aitagging/scene_tagging.py:18` (`apply_scene_tags`)
- **What to check:**
  - Tags from `get_scene_tag_totals_async` are filtered to exclude excluded tags
  - Count only includes non-excluded tags
  - Excluded tags are not in `tags_to_add` set

**Expected Logs:**
- Log showing excluded tags retrieved
- Log showing tag totals before filtering
- Log showing tag totals after filtering
- Log showing final tags_to_add count and list

## Test Steps

### Pre-Test Setup
1. **Configure Excluded Tags:**
   - Go to plugin settings
   - Add 2-3 tag names to excluded tags list
   - Click "OK" to save
   - **Check logs for:**
     ```
     [INFO] upsert_system_setting: Received request to update setting key=EXCLUDED_TAGS
     [INFO] upsert_system_setting: Updating EXCLUDED_TAGS - previous value: [...], new value: [...]
     [INFO] upsert_system_setting: Successfully saved EXCLUDED_TAGS - saved value: [...] (type: list)
     [INFO] upsert_system_setting: Verified EXCLUDED_TAGS retrieval after save - retrieved: [...] (type: list)
     ```
   - Verify via `GET /api/v1/plugins/system/tags/excluded` that tags are saved

2. **Identify Test Scene:**
   - Choose a scene that would normally get tags including at least one excluded tag
   - Note the scene ID

### Test Execution

#### Step 1: Save Excluded Tags in UI
1. Open plugin settings
2. Go to excluded tags section
3. Add tags to exclude
4. Click "OK"
5. **Check backend logs for save confirmation:**
   ```
   [INFO] upsert_system_setting: Successfully saved EXCLUDED_TAGS - saved value: ['tag1', 'tag2'] (type: list)
   ```

#### Step 2: Verify Excluded Tags Configuration
```bash
# Check excluded tags are saved
curl -X GET "http://localhost:8000/api/v1/plugins/system/tags/excluded" \
  -H "x-ai-api-key: YOUR_KEY"
```
**Expected:** Returns list of excluded tag names matching what you saved

#### Step 3: Trigger Scene Tagging
1. Navigate to scene detail page
2. Click "AI tag scene" button
3. Observe toast notification

#### Step 4: Monitor Backend Logs
Watch for these log entries in order:

1. **Action Submission:**
   ```
   [INFO] Action submitted: action_id=skier.ai_tag.scene, scene_id=X
   ```

2. **Excluded Tags Retrieval (http_handler.py):**
   ```
   [DEBUG] Retrieved excluded_tags from system settings: ['tag1', 'tag2', ...]
   ```

3. **Payload Construction (http_handler.py):**
   ```
   [DEBUG] Building scene API payload with excluded_tags: ['tag1', 'tag2', ...]
   [DEBUG] Full payload: {...}
   ```

4. **AI Model Server Request:**
   ```
   [DEBUG] Sending request to /v3/process_video/ with excluded_tags: [...]
   ```

5. **Tag Totals Retrieval (scene_tagging.py):**
   ```
   [DEBUG] Retrieved tag totals for scene_id=X: {tag_id: duration, ...}
   ```

6. **Excluded Tags Filtering (scene_tagging.py):**
   ```
   [DEBUG] Excluded tag names: ['tag1', 'tag2', ...]
   [DEBUG] Tag totals before filtering: {tag_id: duration, ...}
   [DEBUG] Tag totals after filtering excluded: {tag_id: duration, ...}
   ```

7. **Final Tag Count (scene_tagging.py):**
   ```
   [DEBUG] Tags to add (after filtering): [tag_id1, tag_id2, ...]
   [DEBUG] Final applied_tags count: N
   ```

8. **Task Completion (logic.py):**
   ```
   [INFO] Scene tagging completed: scene_id=X, tags_applied=N
   ```

#### Step 5: Verify Results
1. Check frontend toast shows correct count (should NOT include excluded tags)
2. Check scene in Stash - excluded tags should NOT be present
3. Check backend logs confirm excluded tags were filtered

## What to Look For

### ✅ Success Indicators
- Excluded tags are retrieved from system settings
- Excluded tags are included in payload to AI model server
- Excluded tags are filtered when counting applied tags
- Final count matches actual tags applied (excluding excluded ones)
- Excluded tags are not present on the scene

### ❌ Failure Indicators
- Excluded tags not retrieved from system settings (returns empty list)
- Excluded tags not included in payload to AI model server
- Excluded tags not filtered when counting (count includes excluded tags)
- Excluded tags appear on the scene after tagging
- Count in toast doesn't match actual tags on scene

## Debugging Checklist

If tag exclusion isn't working, check:

1. **System Settings:**
   - [ ] Excluded tags are saved in database
   - [ ] `GET /api/v1/plugins/system/tags/excluded` returns correct tags
   - [ ] `sys_get_value('EXCLUDED_TAGS', [])` returns correct tags

2. **HTTP Handler:**
   - [ ] `call_scene_api` retrieves excluded tags
   - [ ] Excluded tags are added to payload
   - [ ] Payload is sent correctly to AI model server

3. **AI Model Server:**
   - [ ] AI model server receives excluded_tags in request
   - [ ] AI model server filters excluded tags from response
   - [ ] Response doesn't include excluded tags

4. **Tag Application:**
   - [ ] `get_scene_tag_totals_async` returns all tags (including excluded)
   - [ ] Excluded tags are filtered before counting
   - [ ] Only non-excluded tags are in `tags_to_add`
   - [ ] Count matches actual tags applied

## Additional Notes

- The AI model server should filter excluded tags, but we also need to filter them when counting to ensure accuracy
- Excluded tags might still be in storage from previous runs - they should be filtered when counting
- Tag names vs tag IDs: excluded tags are stored as names, but we need to match them to tag IDs when filtering

