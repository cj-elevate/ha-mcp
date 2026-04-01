---
type: handoff
project: ha-mcp-extended
session_id: 20260214-1001-3e46a
created: 2026-02-14T10:01:14Z
status: in_progress
phase: Person/zone/tag config store fix shipped, pending upstream PR
updated: 2026-02-14 10:01 UTC
next_action: Prepare and submit upstream PR to homeassistant-ai/ha-mcp
blocker: none
routing:
  method: scored
  confidence: high
  score: 0.82
  signals:
    recency: 1.0
    git_dirty: 0.8
    conversation: 1.0
    active_plan: 0.0
    has_handoff_dir: 0.0
  runner_up: workspace (0.35)
---

# You Are Here
Fixed ha_config_set_helper update path for person/zone/tag types. The tool now routes these types to their native websocket APIs (person/update, zone/update, tag/update) instead of only updating the entity registry. Fix is tested and deployed locally. Next step: prepare upstream PR to homeassistant-ai/ha-mcp.

# This Session
- Diagnosed CJ Arrival automation failure: tablet GPS (device_tracker.tablet_3) in person.cj caused 25 state flips in 47 minutes, defeating 10-min debounce
- Removed tablet_3 from person.cj device trackers (phone/watch/cj_watch only now)
- Fixed ha_config_set_helper: person/zone/tag updates now route to config store APIs with fetch-merge-send pattern
- Key finding: person/list returns dict with "storage" key (not flat array), person/update is full-replace (not patch)
- Team (Perplexity/Codex/Gemini) unanimously recommended Option A (fix existing tool, not new tool)
- Added Capability Gap Detection rule to workspace CLAUDE.md

# Hot Files
- D:/servers/ha-mcp-extended/src/ha_mcp/tools/tools_config_helpers.py
- D:/workspace/CLAUDE.md

# Resume
- Prepare upstream PR: fetch upstream/master, create clean branch fix/config-store-updates, cherry-pick fix
- Remove debug repr truncation from error message before PR
- Use /team to review PR diff
- Upstream remote: homeassistant-ai/ha-mcp (already configured)
