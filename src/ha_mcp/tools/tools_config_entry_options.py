"""
Config Entry Options and Subentry tools for Home Assistant MCP server.

This module provides tools for viewing/modifying integration options via the
Options Flow API, managing subentries (per-device config), and MQTT-specific
device debugging.
"""

import logging
from typing import Annotated, Any

from pydantic import Field

from .helpers import exception_to_structured_error, log_tool_usage
from .util_helpers import coerce_bool_param, parse_json_param

logger = logging.getLogger(__name__)


def register_config_entry_options_tools(
    mcp: Any, client: Any, **kwargs: Any
) -> None:
    """Register config entry options and subentry tools with the MCP server."""

    async def _validate_entry_supports_options(
        entry_id: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """
        Fast-fail helper: verify a config entry exists and supports options.

        Returns:
            (entry_dict, None) on success
            (None, error_dict) on failure
        """
        try:
            entry = await client.get_config_entry(entry_id)
        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "not found" in error_msg.lower():
                return None, {
                    "success": False,
                    "error": f"Config entry not found: {entry_id}",
                    "suggestion": "Use ha_get_integration() to find valid entry IDs",
                }
            return None, {
                "success": False,
                "error": f"Failed to look up config entry: {error_msg}",
                "entry_id": entry_id,
            }

        if not entry.get("supports_options", False):
            return None, {
                "success": False,
                "error": f"Integration '{entry.get('domain', 'unknown')}' does not support options",
                "entry_id": entry_id,
                "domain": entry.get("domain"),
                "suggestion": "Not all integrations expose configurable options",
            }

        return entry, None

    async def _handle_options_flow_steps(
        flow_id: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Handle multi-step options flow internally (max 15 steps).

        Mirrors _handle_flow_steps from tools_config_entry_flow.py but calls
        submit_options_flow_step instead of submit_config_flow_step.

        Args:
            flow_id: Flow ID from start_options_flow
            config: Configuration data to submit

        Returns:
            Result dict with success/error and flow details
        """
        max_steps = 15
        for step_num in range(max_steps):
            result = await client.submit_options_flow_step(flow_id, config)

            result_type = result.get("type")
            if result_type == "create_entry":
                return {"success": True, "entry": result}
            elif result_type == "abort":
                return {
                    "success": False,
                    "error": f"Options flow aborted: {result.get('reason')}",
                    "details": result,
                }
            elif result_type == "form":
                # Multi-step options flow - need more input
                return {
                    "success": False,
                    "error": "Multi-step options flow requires additional input",
                    "step_id": result.get("step_id"),
                    "data_schema": result.get("data_schema"),
                    "suggestion": "This integration has a multi-step options flow. "
                    "Additional configuration may be needed via the Home Assistant UI.",
                }
            else:
                return {
                    "success": False,
                    "error": f"Unexpected flow result type: {result_type}",
                    "details": result,
                }

        return {
            "success": False,
            "error": f"Options flow exceeded {max_steps} steps",
        }

    # ── Tool 1: Read integration options ──

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "tags": ["config"],
            "title": "Get Config Entry Options",
        }
    )
    @log_tool_usage
    async def ha_get_config_entry_options(
        entry_id: Annotated[
            str, Field(description="Config entry ID (from ha_get_integration)")
        ],
    ) -> dict[str, Any]:
        """Read current options for a config entry (integration settings).

        Starts an options flow to capture the form schema and current values,
        then aborts the flow without making changes.

        Use ha_get_integration(query="mqtt") to find entry IDs first.
        """
        try:
            entry, error = await _validate_entry_supports_options(entry_id)
            if error:
                return error

            # Start options flow to get current values
            flow_result = await client.start_options_flow(entry_id)
            flow_id = flow_result.get("flow_id")

            if not flow_id:
                return {
                    "success": False,
                    "error": "Failed to start options flow",
                    "details": flow_result,
                }

            try:
                # Capture schema and current values from the form
                # Extract current values from data_schema field defaults
                current_values = {}
                data_schema = flow_result.get("data_schema", [])
                for field_def in data_schema:
                    if isinstance(field_def, dict):
                        field_name = field_def.get("name")
                        if field_name and "default" in field_def:
                            current_values[field_name] = field_def["default"]

                options_data = {
                    "success": True,
                    "entry_id": entry_id,
                    "domain": entry.get("domain"),
                    "title": entry.get("title"),
                    "flow_type": flow_result.get("type"),
                    "step_id": flow_result.get("step_id"),
                    "data_schema": data_schema,
                    "current_values": current_values,
                }

                # Some integrations return description_placeholders
                if flow_result.get("description_placeholders"):
                    options_data["description_placeholders"] = flow_result[
                        "description_placeholders"
                    ]

                return options_data
            finally:
                # Best-effort abort - always clean up the flow
                try:
                    await client.abort_options_flow(flow_id)
                except Exception:
                    logger.debug(
                        f"Best-effort abort of options flow {flow_id} failed (expected)"
                    )

        except Exception as e:
            logger.error(f"Error reading config entry options: {e}")
            return exception_to_structured_error(
                e, context={"entry_id": entry_id}
            )

    # ── Tool 2: Update integration options (merge semantics) ──

    @mcp.tool(
        annotations={
            "destructiveHint": True,
            "tags": ["config"],
            "title": "Update Config Entry Options",
        }
    )
    @log_tool_usage
    async def ha_update_config_entry_options(
        entry_id: Annotated[
            str, Field(description="Config entry ID (from ha_get_integration)")
        ],
        options: Annotated[
            str | dict,
            Field(
                description="Options to update (JSON string or dict). "
                "Only include fields you want to change - existing values are preserved."
            ),
        ],
    ) -> dict[str, Any]:
        """Update integration options through the options flow.

        Uses merge semantics: fetches current values first, then overlays your
        changes on top. Fields you don't specify keep their current values.

        EXAMPLE:
        - ha_update_config_entry_options(entry_id="abc123", options='{"broker": "192.168.1.50"}')
        """
        try:
            entry, error = await _validate_entry_supports_options(entry_id)
            if error:
                return error

            # Parse options param
            parsed_options = parse_json_param(options, "options")
            if not isinstance(parsed_options, dict):
                return {
                    "success": False,
                    "error": "Options must be a JSON object/dict, not an array",
                }

            # Start options flow to get current values for merge
            flow_result = await client.start_options_flow(entry_id)
            flow_id = flow_result.get("flow_id")

            if not flow_id:
                return {
                    "success": False,
                    "error": "Failed to start options flow",
                    "details": flow_result,
                }

            # Extract current values from the form data
            # HA options flows pre-populate form fields with current config
            # as the "default" value in each data_schema field definition
            current_values = {}
            data_schema = flow_result.get("data_schema", [])
            for field_def in data_schema:
                if isinstance(field_def, dict):
                    field_name = field_def.get("name")
                    if field_name and "default" in field_def:
                        current_values[field_name] = field_def["default"]

            # Merge: current values as base, user options overlay
            merged_options = {**current_values, **parsed_options}

            # Submit merged options through flow
            result = await _handle_options_flow_steps(flow_id, merged_options)

            if result.get("success"):
                return {
                    "success": True,
                    "message": f"Options updated for {entry.get('domain', 'unknown')}",
                    "entry_id": entry_id,
                    "domain": entry.get("domain"),
                    "applied_changes": parsed_options,
                    "merged_values": merged_options,
                }
            else:
                return result

        except Exception as e:
            logger.error(f"Error updating config entry options: {e}")
            return exception_to_structured_error(
                e, context={"entry_id": entry_id}
            )

    # ── Tool 3: List subentries ──

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "tags": ["config"],
            "title": "List Subentries",
        }
    )
    @log_tool_usage
    async def ha_list_subentries(
        entry_id: Annotated[
            str, Field(description="Config entry ID (from ha_get_integration)")
        ],
    ) -> dict[str, Any]:
        """List subentries for a config entry (per-device/per-entity config).

        Subentries represent individual components within an integration,
        such as MQTT devices, AI conversation agents, or protocol-specific nodes.

        Use ha_get_integration(query="mqtt") to find entry IDs first.
        """
        try:
            message = {
                "type": "config_entries/subentries/list",
                "entry_id": entry_id,
            }

            result = await client.send_websocket_message(message)

            if not result.get("success"):
                error_msg = result.get("error", {})
                if isinstance(error_msg, dict):
                    error_code = error_msg.get("code", "")
                    error_text = error_msg.get("message", str(error_msg))
                    if error_code == "unknown_command":
                        return {
                            "success": False,
                            "error": "Subentry listing not supported by this Home Assistant version",
                            "suggestion": "Subentries require Home Assistant 2025.x or newer",
                        }
                    return {
                        "success": False,
                        "error": f"Failed to list subentries: {error_text}",
                        "entry_id": entry_id,
                    }
                return {
                    "success": False,
                    "error": f"Failed to list subentries: {error_msg}",
                    "entry_id": entry_id,
                }

            subentries = result.get("result", [])

            return {
                "success": True,
                "entry_id": entry_id,
                "total": len(subentries),
                "subentries": subentries,
            }

        except Exception as e:
            logger.error(f"Error listing subentries: {e}")
            return exception_to_structured_error(
                e, context={"entry_id": entry_id}
            )

    # ── Tool 4: Delete subentry ──

    @mcp.tool(
        annotations={
            "destructiveHint": True,
            "tags": ["config"],
            "title": "Delete Subentry",
        }
    )
    @log_tool_usage
    async def ha_delete_subentry(
        entry_id: Annotated[
            str, Field(description="Config entry ID (from ha_get_integration)")
        ],
        subentry_id: Annotated[
            str,
            Field(
                description="Subentry ID to delete (from ha_list_subentries)"
            ),
        ],
        confirm: Annotated[
            bool | str,
            Field(description="Must be True to confirm deletion"),
        ] = False,
    ) -> dict[str, Any]:
        """Delete a subentry from a config entry. Requires confirm=True.

        WARNING: Deleting a subentry removes all associated entities and devices.
        This action cannot be undone.

        Use ha_list_subentries() to find subentry IDs first.
        """
        try:
            confirm_bool = coerce_bool_param(confirm, "confirm", default=False)

            if not confirm_bool:
                return {
                    "success": False,
                    "error": "Deletion not confirmed. Set confirm=True to proceed.",
                    "entry_id": entry_id,
                    "subentry_id": subentry_id,
                    "warning": "Deleting a subentry removes all associated entities "
                    "and devices. This cannot be undone.",
                }

            message = {
                "type": "config_entries/subentries/delete",
                "entry_id": entry_id,
                "subentry_id": subentry_id,
            }

            result = await client.send_websocket_message(message)

            if not result.get("success"):
                error_msg = result.get("error", {})
                if isinstance(error_msg, dict):
                    error_text = error_msg.get("message", str(error_msg))
                else:
                    error_text = str(error_msg)
                return {
                    "success": False,
                    "error": f"Failed to delete subentry: {error_text}",
                    "entry_id": entry_id,
                    "subentry_id": subentry_id,
                }

            return {
                "success": True,
                "message": "Subentry deleted successfully",
                "entry_id": entry_id,
                "subentry_id": subentry_id,
                "note": "Associated entities and devices have been removed.",
            }

        except Exception as e:
            logger.error(f"Error deleting subentry: {e}")
            return exception_to_structured_error(
                e,
                context={
                    "entry_id": entry_id,
                    "subentry_id": subentry_id,
                },
            )

    # ── Tool 5: Get subentry creation schema ──

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "tags": ["config"],
            "title": "Get Subentry Schema",
        }
    )
    @log_tool_usage
    async def ha_get_subentry_schema(
        entry_id: Annotated[
            str, Field(description="Config entry ID (from ha_get_integration)")
        ],
        subentry_type: Annotated[
            str,
            Field(
                description="Subentry type to get schema for (integration-specific, e.g., 'device')"
            ),
        ],
    ) -> dict[str, Any]:
        """Get the configuration schema for creating a new subentry of a given type.

        Starts a subentry creation flow to capture the form schema, then lets
        it expire (no abort endpoint for subentry flows). Use this to discover
        what fields are needed before creating a subentry.

        Requires Home Assistant 2025.x or newer.
        """
        try:
            flow_result = await client.start_subentry_flow(
                entry_id, subentry_type
            )

            flow_type = flow_result.get("type")

            if flow_type == "form":
                return {
                    "success": True,
                    "entry_id": entry_id,
                    "subentry_type": subentry_type,
                    "flow_type": "form",
                    "step_id": flow_result.get("step_id"),
                    "data_schema": flow_result.get("data_schema", []),
                    "description_placeholders": flow_result.get(
                        "description_placeholders", {}
                    ),
                    "note": "This shows the fields needed to create a new subentry. "
                    "No subentry has been created yet.",
                }
            elif flow_type == "menu":
                return {
                    "success": True,
                    "entry_id": entry_id,
                    "subentry_type": subentry_type,
                    "flow_type": "menu",
                    "step_id": flow_result.get("step_id"),
                    "menu_options": flow_result.get("menu_options", []),
                    "description_placeholders": flow_result.get(
                        "description_placeholders", {}
                    ),
                    "note": "This subentry type requires a menu selection first.",
                }
            elif flow_type == "abort":
                return {
                    "success": False,
                    "error": f"Cannot create subentry: {flow_result.get('reason', 'unknown')}",
                    "entry_id": entry_id,
                    "subentry_type": subentry_type,
                    "details": flow_result,
                }
            else:
                return {
                    "success": False,
                    "error": f"Unexpected flow type: {flow_type}",
                    "details": flow_result,
                }

        except Exception as e:
            error_msg = str(e)
            if "404" in error_msg or "not found" in error_msg.lower():
                return {
                    "success": False,
                    "error": f"Subentry flow not available for entry {entry_id} "
                    f"with type '{subentry_type}'",
                    "suggestion": "Check that the integration supports subentries "
                    "and the subentry_type is correct. Requires HA 2025.x+.",
                }
            logger.error(f"Error getting subentry schema: {e}")
            return exception_to_structured_error(
                e,
                context={
                    "entry_id": entry_id,
                    "subentry_type": subentry_type,
                },
            )

    # ── Tool 6: MQTT device debug info ──

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "tags": ["config", "mqtt"],
            "title": "MQTT Device Debug Info",
        }
    )
    @log_tool_usage
    async def ha_mqtt_device_debug(
        device_id: Annotated[
            str,
            Field(
                description="Device ID to debug (from device registry, not entity ID)"
            ),
        ],
    ) -> dict[str, Any]:
        """Get MQTT-specific debug info for a device.

        Returns discovery payloads, subscribed topics, and entity configs
        for an MQTT-discovered device. Useful for diagnosing config issues
        like deprecated fields (e.g., color_mode).

        Requires the MQTT integration to be loaded.
        """
        try:
            message = {
                "type": "mqtt/device/debug_info",
                "device_id": device_id,
            }

            result = await client.send_websocket_message(message)

            if not result.get("success"):
                error_msg = result.get("error", {})
                if isinstance(error_msg, dict):
                    error_code = error_msg.get("code", "")
                    error_text = error_msg.get("message", str(error_msg))
                    if error_code == "unknown_command":
                        return {
                            "success": False,
                            "error": "MQTT debug info not available - MQTT integration may not be loaded",
                            "suggestion": "Ensure the MQTT integration is configured and running",
                        }
                    return {
                        "success": False,
                        "error": f"Failed to get MQTT debug info: {error_text}",
                        "device_id": device_id,
                    }
                return {
                    "success": False,
                    "error": f"Failed to get MQTT debug info: {error_msg}",
                    "device_id": device_id,
                }

            debug_data = result.get("result", {})

            return {
                "success": True,
                "device_id": device_id,
                "triggers": debug_data.get("triggers", []),
                "discovery_data": debug_data.get("discovery_data", {}),
                "entities": debug_data.get("entities", []),
            }

        except Exception as e:
            logger.error(f"Error getting MQTT device debug info: {e}")
            return exception_to_structured_error(
                e, context={"device_id": device_id}
            )
