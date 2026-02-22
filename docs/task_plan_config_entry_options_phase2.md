---
type: task_plan
status: complete
project: ha-mcp-extended
path: D:\servers\ha-mcp-extended
created: 2026-02-18
updated: 2026-02-22
---

# Phase 2: Create Config Entry Options Tool Module

## Context

Phase 1 (complete) added 5 REST client methods to `rest_client.py` for options flow and subentry flow APIs. Phase 2 creates the tool module that exposes these as 6 MCP tools, giving Claude autonomous access to integration config and per-device settings in Home Assistant.

Primary use case: diagnosing and fixing MQTT device config issues (e.g., deprecated `color_mode`), but broadly useful for any integration.

## File to Create

`D:\servers\ha-mcp-extended\src\ha_mcp\tools\tools_config_entry_options.py`

No registry.py changes needed - auto-discovered via `tools_*.py` naming convention.

## Registration Function

```python
def register_config_entry_options_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
```

## Imports

```python
import logging
from typing import Annotated, Any

from pydantic import Field

from .helpers import exception_to_structured_error, log_tool_usage
from .util_helpers import parse_json_param, coerce_bool_param

logger = logging.getLogger(__name__)
```

## Inner Helpers

### `_handle_options_flow_steps(flow_id, config)`

Mirrors `_handle_flow_steps` from `tools_config_entry_flow.py:41-87` but calls `client.submit_options_flow_step` instead of `client.submit_config_flow_step`. Max 15 steps (some integrations have complex multi-step options), returns structured dict with success/error.

### `_validate_entry_supports_options(entry_id)`

Fast-fail helper: calls `client.get_config_entry(entry_id)`, checks `supports_options: True`. Returns `(entry, None)` on success or `(None, error_dict)` on failure. Pattern from `tools_integrations.py:64-77`.

## Tools (6 total)

### Tool 1: `ha_get_config_entry_options(entry_id)` [readOnly]

Read integration options by starting an options flow, capturing the form schema + current values, then aborting.

1. `_validate_entry_supports_options(entry_id)` - fast-fail
2. `client.start_options_flow(entry_id)` - get form with current values
3. Best-effort `client.abort_options_flow(flow_id)` - cleanup (try/except, ignore errors)
4. Return schema + current values from the form data

### Tool 2: `ha_update_config_entry_options(entry_id, options)` [destructive]

Update integration options through the options flow.

- Params: `entry_id: str`, `options: str | dict` (parsed via `parse_json_param`)
- Validate entry + supports_options
- **Merge semantics** (team-reviewed, Codex): Options flow is full-replace. Must:
  1. Start options flow to get current values from form data
  2. Merge user-provided options ON TOP of current values
  3. Submit merged dict via `_handle_options_flow_steps`
  This prevents accidentally clearing fields the user didn't mention.
- Handle multi-step flows (max 15 steps)
- Return success/error with validation details

### Tool 3: `ha_list_subentries(entry_id)` [readOnly]

List subentries via WebSocket.

- WS command: `config_entries/subentries/list` with `entry_id`
- `client.send_websocket_message({"type": "config_entries/subentries/list", "entry_id": entry_id})`
- Return list of `{subentry_id, subentry_type, title, unique_id}`
- Handle gracefully if HA version doesn't support subentries

### Tool 4: `ha_delete_subentry(entry_id, subentry_id, confirm)` [destructive]

Delete subentry via WebSocket. Requires `confirm=True`.

- WS command: `config_entries/subentries/delete` with `entry_id` and `subentry_id`
- Safety: `coerce_bool_param(confirm)` must be True
- Warn about cascade: removes associated entities and devices

### Tool 5: `ha_get_subentry_schema(entry_id, subentry_type)` [readOnly]

Get the configuration schema for creating a new subentry of a given type.

- **Renamed** from `ha_get_subentry_config` (team-reviewed, Codex): `start_subentry_flow` starts a *creation* flow, not a read of existing config. Name now reflects actual behavior.
- `client.start_subentry_flow(entry_id, subentry_type)` - get creation form schema
- Best-effort abort (no abort endpoint for subentry flows - let it expire)
- Return form schema showing available fields and their types
- Use case: discover what fields are needed before creating a subentry
- Note: requires HA 2025.x+

### Tool 6: `ha_mqtt_device_debug(device_id)` [readOnly]

MQTT-specific debug info via WebSocket.

- WS command: `mqtt/device/debug_info` with `device_id`
- Returns discovery payloads, subscribed topics, entity configs
- Handle case where MQTT integration not loaded

## Patterns to Reuse

| Pattern | Source | Usage |
|---------|--------|-------|
| `_handle_flow_steps` loop | `tools_config_entry_flow.py:41-87` | Adapt for `_handle_options_flow_steps` |
| Fast-fail entry lookup | `tools_integrations.py:64-77` | `_validate_entry_supports_options` |
| `@mcp.tool(annotations={...})` | `tools_config_entry_flow.py:89-95` | All 6 tools |
| `@log_tool_usage` decorator | `tools_config_entry_flow.py:96` | All 6 tools |
| `exception_to_structured_error(e, context)` | `tools_config_entry_flow.py:160` | All catch blocks |
| `parse_json_param(options)` | `tools_config_entry_flow.py:115` | Tool 2 (options param) |
| `coerce_bool_param(confirm)` | `tools_integrations.py:194` | Tool 4 (confirm param) |
| WebSocket via `client.send_websocket_message()` | `tools_integrations.py:202` | Tools 3, 4, 6 |

## Verification

1. **Syntax**: `uv run python -c "from ha_mcp.tools.tools_config_entry_options import register_config_entry_options_tools"`
2. **Unit tests**: `uv run pytest tests/src/unit/ -v` (no regressions)
3. **Tool annotations test**: New tools must have `readOnlyHint` or `destructiveHint` set (existing test at `test_tool_annotations.py:108`)
4. **Tool discovery**: After `restart_backend("home-assistant")`, `search_tools(query="config_entry_options")` finds new tools
5. **Live smoke test** (optional, if HA available):
   - `ha_get_integration(query="mqtt")` to get MQTT entry_id
   - `ha_get_config_entry_options(entry_id=...)` returns MQTT broker options form
   - `ha_list_subentries(entry_id=...)` lists MQTT device subentries
   - `ha_mqtt_device_debug(device_id=...)` returns debug info

## Team Review (2026-02-18)

Consulted: Perplexity, Codex, Gemini. All confirmed high confidence.

**Corrections applied:**
1. Tool 2 merge semantics - fetch current values before submitting (Codex)
2. Tool 5 renamed to `ha_get_subentry_schema` - clarifies it shows creation form, not existing config (Codex)
3. Max flow steps bumped from 10 to 15 (Codex)

**Deferred:**
- Split MQTT debug to separate file (Gemini) - 6 tools is still cohesive
- Per-entry async locks (Codex) - HA manages flow isolation via unique IDs
- Stronger confirm pattern (Codex) - matches existing codebase convention

## Implementation Session (2026-02-22)

**Status:** Complete - all 6 tools implemented, team-reviewed, live-tested.

**Created:** `src/ha_mcp/tools/tools_config_entry_options.py`
- 2 inner helpers: `_validate_entry_supports_options`, `_handle_options_flow_steps`
- 6 tools with proper annotations (4 readOnly, 2 destructive)
- Auto-discovered via `tools_*.py` naming convention (no registry changes)

**Post-implementation team review (Perplexity + Codex):**
1. Fixed: Tool 1 abort moved to try/finally (prevents orphaned flows on exception)
2. Fixed: Removed `data_schema_defaults` fallback (field doesn't exist in HA API)
3. Confirmed: Shallow merge is correct (HA options forms use flat top-level keys)
4. Confirmed: HA uses entry-level locking for concurrent flow access (no race conditions)

**Verification:** 500/501 tests pass (1 pre-existing), all 6 tools discovered via MCP proxy, live smoke tests pass (MQTT options read, subentry list, MQTT debug).

**Bumped:** test_tool_annotations.py MAX_TOOLS from 105 to 115 (was already stale at 103 pre-change).

**Uncommitted** - commit next session.
