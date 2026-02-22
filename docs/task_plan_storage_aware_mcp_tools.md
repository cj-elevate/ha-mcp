---
type: task_plan
status: draft
project: ha-mcp-extended
path: D:\servers\ha-mcp-extended
created: 2026-02-17
updated: 2026-02-18
---

# Add Storage-Aware MCP Tools to ha-mcp-extended

## Context

We need tools to read and modify integration configurations stored in HA's
`.storage/` layer (config entry options, per-device subentry configs). The
primary use case is fixing MQTT device settings (e.g., deprecated `color_mode`),
but these tools are broadly useful for any integration.

**Key API discovery:** HA's config entry `data` dict is NOT accessible via
external API (security). Instead, HA exposes three flow-based APIs:

1. **Options flow** - integration-level settings (broker config, polling, etc.)
2. **Subentry flow** - per-device config within an integration (MQTT devices, AI agents, etc.)
3. **Subentry CRUD** - list/update/delete subentries via WebSocket

The subentry API (added in HA 2025.x) is the path to per-device config
management that the original task was looking for.

## Implementation

### Phase 1: Add REST client methods

**File:** `D:\servers\ha-mcp-extended\src\ha_mcp\client\rest_client.py`

Add 5 methods to `HomeAssistantClient` class (after `submit_config_flow_step`
at line ~650). These mirror the existing `start_config_flow`/`submit_config_flow_step`
pattern:

- [x] `start_options_flow(entry_id)` → `POST /api/config/config_entries/options/flow`
- [x] `submit_options_flow_step(flow_id, user_input)` → `POST .../options/flow/{flow_id}`
- [x] `abort_options_flow(flow_id)` → `DELETE .../options/flow/{flow_id}`
- [x] `start_subentry_flow(entry_id, subentry_type)` → `POST .../subentries/flow`
- [x] `submit_subentry_flow_step(flow_id, user_input)` → `POST .../subentries/flow/{flow_id}`

Each method: 3-5 lines, calls `self._request()`, has docstring + type hints.

### Phase 2: Create tool module

**File:** `D:\servers\ha-mcp-extended\src\ha_mcp\tools\tools_config_entry_options.py` (NEW)

Auto-discovered by registry.py via `tools_*.py` naming convention. Registration
function: `register_config_entry_options_tools(mcp, client, **kwargs)`.

#### Tool 1: `ha_get_config_entry_options(entry_id)` [readOnly]

Reads integration options via options flow:
1. Fast-fail check: `get_config_entry()` → verify `supports_options: True`
2. Start options flow → returns form schema with current values
3. Best-effort abort flow (cleanup)
4. Return schema + current values

#### Tool 2: `ha_update_config_entry_options(entry_id, options)` [destructive]

Updates integration options through options flow:
1. Verify entry exists + supports_options
2. Start options flow → get current values from form data
3. Merge user-provided options ON TOP of current values (full-replace semantics)
4. Submit merged dict, handle multi-step flows (max 15 steps)
5. Return success/error with validation details

#### Tool 3: `ha_list_subentries(entry_id)` [readOnly]

Lists subentries for a config entry via WebSocket:
- WS command: `config_entries/subentries/list` with `entry_id`
- Returns: list of `{subentry_id, subentry_type, title, unique_id}`
- Use case: find MQTT device subentries to inspect/modify

#### Tool 4: `ha_delete_subentry(entry_id, subentry_id)` [destructive]

Deletes a subentry via WebSocket:
- WS command: `config_entries/subentries/delete`
- Cascades: removes associated entities and devices
- Requires `confirm=True` parameter for safety

#### Tool 5: `ha_get_subentry_schema(entry_id, subentry_type)` [readOnly]

Gets creation form schema for a subentry type (team-reviewed: renamed from
`ha_get_subentry_config` since `start_subentry_flow` starts a creation flow,
not a read of existing config):
1. Start subentry flow → returns creation form schema with available fields
2. Best-effort abort (let expire - no abort endpoint for subentry flows)
3. Return schema showing fields and types

#### Tool 6: `ha_mqtt_device_debug(device_id)` [readOnly]

MQTT-specific debug info via WebSocket:
- WS command: `mqtt/device/debug_info` with `device_id`
- Returns: discovery payloads, subscribed topics, entity configs
- Use case: diagnose deprecated settings like `color_mode`

### Phase 3: Tests

**File:** `D:\servers\ha-mcp-extended\tests\src\unit\test_tools_config_entry_options.py` (NEW)

Test cases per tool using mock client pattern from existing tests:
- Options: entry not found, not supported, form success, menu type, validation errors
- Subentries: list success, list empty, delete with/without confirm, config read
- MQTT debug: success, device not found, MQTT not loaded

### Phase 4: Documentation

- [ ] Update `CHANGELOG.md` with new tools
- [ ] Update `README.md` tool list

## Critical Files

| File | Action | Purpose |
|------|--------|---------|
| `src/ha_mcp/client/rest_client.py` | Modify | Add 5 HTTP methods for options + subentry flows |
| `src/ha_mcp/tools/tools_config_entry_options.py` | Create | 6 new tools |
| `src/ha_mcp/tools/tools_config_entry_flow.py` | Reference | Pattern for `_handle_flow_steps()` |
| `src/ha_mcp/tools/tools_integrations.py` | Reference | Pattern for `supports_options` check |
| `src/ha_mcp/tools/helpers.py` | Import | `exception_to_structured_error`, `log_tool_usage` |
| `src/ha_mcp/errors.py` | Import | `ErrorCode`, `create_error_response` |
| `src/ha_mcp/tools/util_helpers.py` | Import | `parse_json_param` |

## Reusable Patterns

- `exception_to_structured_error(e, context)` from `helpers.py` - all catch blocks
- `log_tool_usage` decorator from `helpers.py` - all tools
- `parse_json_param(options)` from `util_helpers.py` - JSON string parsing
- `_handle_flow_steps()` pattern from `tools_config_entry_flow.py:41-87` - multi-step flow loop
- `get_config_entry(entry_id)` from `rest_client.py:652` - fast-fail entry lookup

## Verification

1. **Syntax**: `uv run python -c "from ha_mcp.tools.tools_config_entry_options import register_config_entry_options_tools"`
2. **Unit tests**: `uv run pytest tests/src/unit/test_tools_config_entry_options.py -v`
3. **Tool discovery**: After restart, `search_tools(query="config_entry_options")` should find new tools
4. **Live smoke test** (if HA available):
   - `ha_get_integration(query="mqtt")` → get MQTT entry_id
   - `ha_get_config_entry_options(entry_id=...)` → should return MQTT broker options form
   - `ha_list_subentries(entry_id=...)` → should list MQTT device subentries
   - `ha_mqtt_device_debug(device_id=...)` → should return debug info

## Notes

- **No registry.py changes needed** - auto-discovery handles `tools_*.py` files
- **Subentry API requires HA 2025.x+** - older versions will return WS errors; tools should handle gracefully
- **Options flow is read-then-replace** - must submit ALL fields, not just changes (note this in tool docs)
- **Z2M devices**: If color_mode deprecation is on Z2M-bridged devices, the fix is in Z2M config, not HA subentries. These tools help with native MQTT integration devices.
