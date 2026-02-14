"""
Entity management tools for Home Assistant MCP server.

This module provides tools for managing entity lifecycle and properties
via the Home Assistant entity registry API.
"""

import asyncio
import logging
from typing import Annotated, Any

from pydantic import Field

from ..errors import ErrorCode, create_error_response
from .helpers import exception_to_structured_error, log_tool_usage
from .util_helpers import coerce_bool_param, parse_string_list_param

logger = logging.getLogger(__name__)


def register_entity_tools(mcp: Any, client: Any, **kwargs: Any) -> None:
    """Register entity management tools with the MCP server."""

    @mcp.tool(
        annotations={
            "destructiveHint": True,
            "idempotentHint": True,
            "tags": ["entity"],
            "title": "Set Entity",
        }
    )
    @log_tool_usage
    async def ha_set_entity(
        entity_id: Annotated[
            str, Field(description="Entity ID to update (e.g., 'sensor.temperature')")
        ],
        area_id: Annotated[
            str | None,
            Field(
                description="Area/room ID to assign the entity to. Use empty string '' to unassign from current area.",
                default=None,
            ),
        ] = None,
        name: Annotated[
            str | None,
            Field(
                description="Display name for the entity. Use empty string '' to remove custom name and revert to default.",
                default=None,
            ),
        ] = None,
        icon: Annotated[
            str | None,
            Field(
                description="Icon for the entity (e.g., 'mdi:thermometer'). Use empty string '' to remove custom icon.",
                default=None,
            ),
        ] = None,
        enabled: Annotated[
            bool | str | None,
            Field(
                description="True to enable the entity, False to disable it.",
                default=None,
            ),
        ] = None,
        hidden: Annotated[
            bool | str | None,
            Field(
                description="True to hide the entity from UI, False to show it.",
                default=None,
            ),
        ] = None,
        aliases: Annotated[
            str | list[str] | None,
            Field(
                description="List of voice assistant aliases for the entity (replaces existing aliases).",
                default=None,
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Update entity properties in the entity registry.

        Allows modifying entity metadata such as area assignment, display name,
        icon, enabled/disabled state, visibility, and aliases.

        Use ha_search_entities() or ha_get_device() to find entity IDs.
        Use ha_manage_entity_labels() to manage entity labels.

        PARAMETERS:
        - area_id: Assigns entity to an area/room. Use '' to remove from area.
        - name: Custom display name. Use '' to revert to default name.
        - icon: Custom icon (e.g., 'mdi:lightbulb'). Use '' to revert to default.
        - enabled: True to enable, False to disable.
        - hidden: True to hide from UI, False to show.
        - aliases: Voice assistant aliases (e.g., ["living room light", "main light"]).

        EXAMPLES:
        - Assign to area: ha_set_entity("sensor.temp", area_id="living_room")
        - Rename: ha_set_entity("sensor.temp", name="Living Room Temperature")
        - Change icon: ha_set_entity("sensor.temp", icon="mdi:thermometer")
        - Disable: ha_set_entity("sensor.temp", enabled=False)
        - Enable: ha_set_entity("sensor.temp", enabled=True)
        - Hide: ha_set_entity("sensor.temp", hidden=True)
        - Show: ha_set_entity("sensor.temp", hidden=False)
        - Set aliases: ha_set_entity("light.lamp", aliases=["bedroom light", "lamp"])
        - Clear area: ha_set_entity("sensor.temp", area_id="")

        NOTE: To rename an entity_id (e.g., sensor.old -> sensor.new), use ha_rename_entity() instead.
        """
        try:
            # Parse list parameters if provided as strings
            parsed_aliases = None
            if aliases is not None:
                try:
                    parsed_aliases = parse_string_list_param(aliases, "aliases")
                except ValueError as e:
                    return create_error_response(
                        ErrorCode.VALIDATION_INVALID_PARAMETER,
                        f"Invalid aliases parameter: {e}",
                    )

            # Build update message
            message: dict[str, Any] = {
                "type": "config/entity_registry/update",
                "entity_id": entity_id,
            }

            updates_made = []

            if area_id is not None:
                # Empty string means remove from area (set to None in API)
                message["area_id"] = area_id if area_id else None
                updates_made.append(
                    f"area_id='{area_id}'" if area_id else "area cleared"
                )

            if name is not None:
                # Empty string means remove custom name (set to None in API)
                message["name"] = name if name else None
                updates_made.append(f"name='{name}'" if name else "name cleared")

            if icon is not None:
                # Empty string means remove custom icon (set to None in API)
                message["icon"] = icon if icon else None
                updates_made.append(f"icon='{icon}'" if icon else "icon cleared")

            if enabled is not None:
                # Convert boolean to API format: True=enable (None), False=disable ("user")
                enabled_bool = coerce_bool_param(enabled, "enabled")
                message["disabled_by"] = None if enabled_bool else "user"
                updates_made.append("enabled" if enabled_bool else "disabled")

            if hidden is not None:
                # Convert boolean to API format: True=hide ("user"), False=show (None)
                hidden_bool = coerce_bool_param(hidden, "hidden")
                message["hidden_by"] = "user" if hidden_bool else None
                updates_made.append("hidden" if hidden_bool else "visible")

            if parsed_aliases is not None:
                message["aliases"] = parsed_aliases
                updates_made.append(f"aliases={parsed_aliases}")

            if not updates_made:
                return {
                    "success": False,
                    "error": "No updates specified",
                    "suggestion": "Provide at least one of: area_id, name, icon, enabled, hidden, or aliases",
                }

            logger.info(f"Updating entity {entity_id}: {', '.join(updates_made)}")
            result = await client.send_websocket_message(message)

            if result.get("success"):
                entity_entry = result.get("result", {}).get("entity_entry", {})
                return {
                    "success": True,
                    "entity_id": entity_id,
                    "updates": updates_made,
                    "entity_entry": {
                        "entity_id": entity_entry.get("entity_id"),
                        "name": entity_entry.get("name"),
                        "original_name": entity_entry.get("original_name"),
                        "icon": entity_entry.get("icon"),
                        "area_id": entity_entry.get("area_id"),
                        "disabled_by": entity_entry.get("disabled_by"),
                        "hidden_by": entity_entry.get("hidden_by"),
                        "aliases": entity_entry.get("aliases", []),
                        "labels": entity_entry.get("labels", []),
                    },
                    "message": f"Entity updated: {', '.join(updates_made)}",
                }
            else:
                error = result.get("error", {})
                error_msg = (
                    error.get("message", str(error))
                    if isinstance(error, dict)
                    else str(error)
                )
                return {
                    "success": False,
                    "error": f"Failed to update entity: {error_msg}",
                    "entity_id": entity_id,
                    "suggestions": [
                        "Verify the entity_id exists using ha_search_entities()",
                        "Check that area_id exists if specified",
                        "Some entities may not support all update options",
                    ],
                }

        except Exception as e:
            logger.error(f"Error updating entity: {e}")
            return exception_to_structured_error(e, context={"entity_id": entity_id})

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "idempotentHint": True,
            "tags": ["entity"],
            "title": "Get Entity",
        }
    )
    @log_tool_usage
    async def ha_get_entity(
        entity_id: Annotated[
            str | list[str],
            Field(
                description="Entity ID or list of entity IDs to retrieve (e.g., 'sensor.temperature' or ['light.living_room', 'switch.porch'])"
            ),
        ],
    ) -> dict[str, Any]:
        """Get entity registry information for one or more entities.

        Returns detailed entity registry metadata including area assignment,
        custom name/icon, enabled/hidden state, aliases, labels, and more.

        RELATED TOOLS:
        - ha_set_entity(): Modify entity properties (area, name, icon, enabled, hidden, aliases)
        - ha_get_state(): Get current state/attributes (on/off, temperature, etc.)
        - ha_search_entities(): Find entities by name, domain, or area

        EXAMPLES:
        - Single entity: ha_get_entity("sensor.temperature")
        - Multiple entities: ha_get_entity(["light.living_room", "switch.porch"])

        RESPONSE FIELDS:
        - entity_id: Full entity identifier
        - name: Custom display name (null if using original_name)
        - original_name: Default name from integration
        - icon: Custom icon (null if using default)
        - area_id: Assigned area/room ID (null if unassigned)
        - disabled_by: Why disabled (null=enabled, "user"/"integration"/etc)
        - hidden_by: Why hidden (null=visible, "user"/"integration"/etc)
        - enabled: Boolean shorthand (True if disabled_by is null)
        - hidden: Boolean shorthand (True if hidden_by is not null)
        - aliases: Voice assistant aliases
        - labels: Assigned label IDs
        - platform: Integration platform (e.g., "hue", "zwave_js")
        - device_id: Associated device ID (null if standalone)
        - unique_id: Integration's unique identifier
        """
        try:
            # Validate and parse entity_id parameter
            entity_ids: list[str]
            is_bulk: bool

            if isinstance(entity_id, str):
                entity_ids = [entity_id]
                is_bulk = False
            elif isinstance(entity_id, list):
                if not entity_id:
                    return {
                        "success": True,
                        "entity_entries": [],
                        "count": 0,
                        "message": "No entities requested",
                    }
                if not all(isinstance(e, str) for e in entity_id):
                    return {
                        "success": False,
                        "error": "All entity_id values must be strings",
                    }
                entity_ids = entity_id
                is_bulk = True
            else:
                return {
                    "success": False,
                    "error": f"entity_id must be string or list of strings, got {type(entity_id).__name__}",
                }

            async def _fetch_entity(eid: str) -> dict[str, Any]:
                """Fetch a single entity from the registry."""
                message: dict[str, Any] = {
                    "type": "config/entity_registry/get",
                    "entity_id": eid,
                }
                result = await client.send_websocket_message(message)

                if not result.get("success"):
                    error = result.get("error", {})
                    error_msg = (
                        error.get("message", str(error))
                        if isinstance(error, dict)
                        else str(error)
                    )
                    return {
                        "success": False,
                        "entity_id": eid,
                        "error": error_msg,
                    }

                entry = result.get("result", {})
                return {
                    "success": True,
                    "entity_id": entry.get("entity_id"),
                    "name": entry.get("name"),
                    "original_name": entry.get("original_name"),
                    "icon": entry.get("icon"),
                    "area_id": entry.get("area_id"),
                    "disabled_by": entry.get("disabled_by"),
                    "hidden_by": entry.get("hidden_by"),
                    "enabled": entry.get("disabled_by") is None,
                    "hidden": entry.get("hidden_by") is not None,
                    "aliases": entry.get("aliases", []),
                    "labels": entry.get("labels", []),
                    "platform": entry.get("platform"),
                    "device_id": entry.get("device_id"),
                    "unique_id": entry.get("unique_id"),
                }

            # Single entity case
            if not is_bulk:
                eid = entity_ids[0]
                logger.info(f"Getting entity registry entry for {eid}")
                result = await _fetch_entity(eid)

                if result.get("success"):
                    return {
                        "success": True,
                        "entity_id": eid,
                        "entity_entry": {
                            k: v for k, v in result.items() if k not in ("success",)
                        },
                    }
                else:
                    return {
                        "success": False,
                        "entity_id": eid,
                        "error": f"Entity not found: {result.get('error', 'Unknown error')}",
                        "suggestions": [
                            "Use ha_search_entities() to find valid entity IDs",
                            "Check the entity_id spelling and format (e.g., 'sensor.temperature')",
                        ],
                    }

            # Bulk case - fetch all entities
            logger.info(f"Getting entity registry entries for {len(entity_ids)} entities")
            results = await asyncio.gather(
                *[_fetch_entity(eid) for eid in entity_ids],
                return_exceptions=True,
            )

            entity_entries: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []

            for eid, fetch_result in zip(entity_ids, results, strict=True):
                if isinstance(fetch_result, BaseException):
                    errors.append({
                        "entity_id": eid,
                        "error": str(fetch_result),
                    })
                    continue
                if fetch_result.get("success"):
                    entity_entries.append(
                        {k: v for k, v in fetch_result.items() if k not in ("success",)}
                    )
                else:
                    errors.append({
                        "entity_id": eid,
                        "error": fetch_result.get("error", "Unknown error"),
                    })

            response: dict[str, Any] = {
                "success": True,
                "count": len(entity_entries),
                "entity_entries": entity_entries,
            }

            if errors:
                response["errors"] = errors
                response["suggestions"] = [
                    "Use ha_search_entities() to find valid entity IDs for failed lookups"
                ]

            return response

        except Exception as e:
            logger.error(f"Error getting entity: {e}")
            return exception_to_structured_error(
                e, context={"entity_id": entity_id if isinstance(entity_id, str) else entity_ids}
            )

    @mcp.tool(
        annotations={
            "destructiveHint": True,
            "idempotentHint": True,
            "tags": ["entity"],
            "title": "Remove Entity",
        }
    )
    @log_tool_usage
    async def ha_remove_entity(
        entity_id: Annotated[
            str | list[str],
            Field(
                description="Entity ID or list of entity IDs to remove from the registry (e.g., 'sensor.old_sensor' or ['sensor.a', 'sensor.b'])"
            ),
        ],
        confirm: Annotated[
            bool | str,
            Field(
                description="Must be True to execute removal. False (default) = dry-run preview showing what would be removed."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Remove entities from the Home Assistant entity registry.

        Permanently removes entity registry entries. This does NOT remove the
        underlying integration or device -- only the entity record itself.

        MODES:
        - Dry-run (confirm=False): Preview what would be removed with registry details
        - Execute (confirm=True): Actually remove the entities

        WARNING: Entities backed by active integrations may reappear after HA restart.
        To fully remove an integration's entities, use ha_delete_config_entry() instead.

        EXAMPLES:
        - Preview: ha_remove_entity("sensor.old_sensor")
        - Remove: ha_remove_entity("sensor.old_sensor", confirm=True)
        - Batch preview: ha_remove_entity(["sensor.a", "sensor.b", "sensor.c"])
        - Batch remove: ha_remove_entity(["sensor.a", "sensor.b"], confirm=True)

        RELATED TOOLS:
        - ha_get_entity(): Inspect entity registry details
        - ha_search_entities(): Find entities by name, domain, or area
        - ha_delete_config_entry(): Remove an entire integration (all its entities)
        - ha_set_entity(enabled=False): Disable without removing
        """
        try:
            # Coerce confirm parameter
            confirm_bool = coerce_bool_param(confirm, "confirm", default=False)

            # Parse and validate entity_id parameter
            entity_ids: list[str]
            is_bulk: bool

            if isinstance(entity_id, str):
                entity_ids = [entity_id]
                is_bulk = False
            elif isinstance(entity_id, list):
                if not entity_id:
                    return {
                        "success": True,
                        "dry_run": not confirm_bool,
                        "count": 0,
                        "message": "No entities specified",
                    }
                if not all(isinstance(e, str) for e in entity_id):
                    return create_error_response(
                        ErrorCode.VALIDATION_INVALID_PARAMETER,
                        "All entity_id values must be strings",
                    )
                entity_ids = entity_id
                is_bulk = True
            else:
                return create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    f"entity_id must be string or list of strings, got {type(entity_id).__name__}",
                )

            # Validate entity_id format: must contain '.' separator
            invalid_ids = [eid for eid in entity_ids if "." not in eid]
            if invalid_ids:
                return create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    f"Invalid entity_id format (must contain '.' separator): {invalid_ids}",
                )

            # Cap batch size at 50
            if len(entity_ids) > 50:
                return create_error_response(
                    ErrorCode.VALIDATION_INVALID_PARAMETER,
                    f"Batch size {len(entity_ids)} exceeds maximum of 50. Split into multiple calls.",
                )

            # Deduplicate while preserving order
            seen: set[str] = set()
            unique_ids: list[str] = []
            for eid in entity_ids:
                if eid not in seen:
                    seen.add(eid)
                    unique_ids.append(eid)
            entity_ids = unique_ids

            # Bounded concurrency for registry lookups
            semaphore = asyncio.Semaphore(10)

            async def _lookup_entity(eid: str) -> dict[str, Any]:
                """Fetch entity registry info for preview."""
                async with semaphore:
                    message: dict[str, Any] = {
                        "type": "config/entity_registry/get",
                        "entity_id": eid,
                    }
                    result = await client.send_websocket_message(message)

                    if not result.get("success"):
                        return {
                            "entity_id": eid,
                            "found": False,
                            "error": "Entity not found in registry",
                        }

                    entry = result.get("result", {})
                    return {
                        "entity_id": eid,
                        "found": True,
                        "name": entry.get("name") or entry.get("original_name"),
                        "platform": entry.get("platform"),
                        "device_id": entry.get("device_id"),
                        "disabled_by": entry.get("disabled_by"),
                    }

            async def _remove_entity(eid: str) -> dict[str, Any]:
                """Remove a single entity from the registry."""
                async with semaphore:
                    message: dict[str, Any] = {
                        "type": "config/entity_registry/remove",
                        "entity_id": eid,
                    }
                    result = await client.send_websocket_message(message)

                    if result.get("success"):
                        return {"entity_id": eid, "removed": True}

                    error = result.get("error", {})
                    error_msg = (
                        error.get("message", str(error))
                        if isinstance(error, dict)
                        else str(error)
                    )
                    # Treat "not found" as idempotent success
                    if "not found" in error_msg.lower() or "unknown" in error_msg.lower():
                        return {
                            "entity_id": eid,
                            "removed": True,
                            "note": "Entity was already removed",
                        }
                    return {
                        "entity_id": eid,
                        "removed": False,
                        "error": error_msg,
                    }

            # Preview phase: look up all entities
            logger.info(f"Looking up {len(entity_ids)} entities for removal preview")
            lookup_results = await asyncio.gather(
                *[_lookup_entity(eid) for eid in entity_ids],
                return_exceptions=True,
            )

            previews: list[dict[str, Any]] = []
            lookup_errors: list[dict[str, Any]] = []

            for eid, result in zip(entity_ids, lookup_results, strict=True):
                if isinstance(result, BaseException):
                    lookup_errors.append({"entity_id": eid, "error": str(result)})
                elif result.get("found"):
                    previews.append(result)
                else:
                    lookup_errors.append({"entity_id": eid, "error": result.get("error", "Not found")})

            # Dry-run mode: return preview
            if not confirm_bool:
                response: dict[str, Any] = {
                    "dry_run": True,
                    "count": len(previews),
                    "entities": previews,
                    "message": "Set confirm=True to execute removal.",
                    "warning": "Entities backed by active integrations may reappear after HA restart.",
                }
                if lookup_errors:
                    response["not_found"] = lookup_errors
                    response["not_found_count"] = len(lookup_errors)

                # Single entity: flatten response
                if not is_bulk and len(previews) == 1:
                    entity = previews[0]
                    return {
                        "dry_run": True,
                        **entity,
                        "message": "Set confirm=True to execute removal.",
                        "warning": "Entities backed by active integrations may reappear after HA restart.",
                    }
                if not is_bulk and not previews:
                    return {
                        "dry_run": True,
                        "entity_id": entity_ids[0],
                        "found": False,
                        "error": "Entity not found in registry. Nothing to remove.",
                        "suggestions": [
                            "Use ha_search_entities() to find valid entity IDs",
                            "The entity may have already been removed",
                        ],
                    }

                return response

            # Execute mode: remove found entities
            if not previews:
                return {
                    "success": True,
                    "removed_count": 0,
                    "message": "No entities found to remove.",
                    "not_found": lookup_errors,
                }

            found_ids = [p["entity_id"] for p in previews]
            logger.info(f"Removing {len(found_ids)} entities from registry")
            remove_results = await asyncio.gather(
                *[_remove_entity(eid) for eid in found_ids],
                return_exceptions=True,
            )

            removed: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []

            for eid, result in zip(found_ids, remove_results, strict=True):
                if isinstance(result, BaseException):
                    errors.append({"entity_id": eid, "error": str(result)})
                elif result.get("removed"):
                    removed.append(result)
                else:
                    errors.append(result)

            # Single entity: flat response
            if not is_bulk:
                if removed:
                    return {
                        "success": True,
                        **removed[0],
                        "message": f"Entity {removed[0]['entity_id']} removed from registry.",
                    }
                elif errors:
                    return {
                        "success": False,
                        **errors[0],
                        "message": f"Failed to remove entity: {errors[0].get('error')}",
                    }

            # Batch response
            response = {
                "success": len(errors) == 0,
                "removed_count": len(removed),
                "error_count": len(errors),
                "removed": removed,
            }
            if errors:
                response["errors"] = errors
            if lookup_errors:
                response["not_found"] = lookup_errors

            return response

        except Exception as e:
            logger.error(f"Error removing entity: {e}")
            return exception_to_structured_error(
                e, context={"entity_id": entity_id if isinstance(entity_id, str) else entity_ids}
            )
