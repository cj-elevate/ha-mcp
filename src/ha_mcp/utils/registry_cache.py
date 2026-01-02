"""
Registry-based caching for Home Assistant entity search optimization.

This module provides a high-performance cache for entity search operations by:
1. Using entity_registry/list instead of get_states (much smaller payload)
2. TTL-based caching with stale-while-revalidate
3. Singleflight pattern to prevent concurrent fetch storms

Performance impact:
- get_states: ~700 entities × ~1KB each = ~700KB payload
- entity_registry/list: ~700 entities × ~200 bytes = ~140KB payload (5x smaller)
- Cache hit: <1ms latency vs 100-500ms for API call
"""

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class HARegistryCache:
    """
    High-performance cache for Home Assistant registry data.

    Features:
    - TTL-based expiration (default 60s)
    - Stale-while-revalidate (serve stale data while refreshing)
    - Singleflight (prevent concurrent fetches on cache miss)
    - Converts registry format to search-friendly format
    """

    def __init__(
        self,
        client: Any,
        ttl_s: float = 60.0,
        max_stale_s: float = 300.0,
    ):
        """
        Initialize registry cache.

        Args:
            client: HomeAssistantClient instance
            ttl_s: Time-to-live in seconds (default 60s)
            max_stale_s: Maximum staleness for stale-while-revalidate (default 300s)
        """
        self._client = client
        self._ttl_s = ttl_s
        self._max_stale_s = max_stale_s

        # Cache state
        self._lock = asyncio.Lock()
        self._entity_registry: Optional[list[dict[str, Any]]] = None
        self._area_registry: Optional[list[dict[str, Any]]] = None
        self._search_entities: Optional[list[dict[str, Any]]] = None
        self._expires_at = 0.0
        self._fetched_at = 0.0
        self._refresh_task: Optional[asyncio.Task] = None
        self._dirty = False

        # Stats for monitoring
        self._hits = 0
        self._misses = 0

    async def invalidate(self) -> None:
        """Mark cache as dirty, triggering refresh on next access.

        Thread-safe: acquires lock before mutating shared state.
        """
        async with self._lock:
            self._dirty = True
            self._expires_at = 0.0
            # Cancel any in-flight refresh to force fresh fetch
            if self._refresh_task and not self._refresh_task.done():
                self._refresh_task.cancel()
                self._refresh_task = None
        logger.debug("Registry cache invalidated")

    def _handle_refresh_done(self, task: asyncio.Task) -> None:
        """Handle refresh task completion, logging any exceptions.

        This prevents 'Task exception was never retrieved' warnings when
        returning stale data while the background refresh fails.
        """
        try:
            exc = task.exception()
            if exc is not None:
                logger.error(f"Background cache refresh failed: {exc}")
        except asyncio.CancelledError:
            logger.debug("Cache refresh task was cancelled")
        except asyncio.InvalidStateError:
            pass  # Task not done yet (shouldn't happen in done callback)

    @property
    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate_percent": round(hit_rate, 1),
            "cached_entities": len(self._search_entities) if self._search_entities else 0,
            "ttl_s": self._ttl_s,
            "max_stale_s": self._max_stale_s,
        }

    async def get_search_entities(self) -> list[dict[str, Any]]:
        """
        Get entities optimized for search (cached).

        Returns entities in search-friendly format:
        {
            "entity_id": "light.living_room",
            "friendly_name": "Living Room Light",
            "domain": "light",
            "area_id": "living_room",
            "disabled": False,
            "attributes": {},  # Empty for search - state not needed
            "state": "unknown",  # Placeholder - not fetched
        }

        Returns:
            List of entity dicts suitable for fuzzy search
        """
        now = time.monotonic()
        entities = self._search_entities

        # Fast-path: fresh cache
        if entities is not None and not self._dirty and now < self._expires_at:
            self._hits += 1
            return entities

        # Check if we can return stale data while refreshing
        # IMPORTANT: Don't serve stale data if cache was explicitly invalidated (_dirty)
        can_return_stale = (
            entities is not None
            and not self._dirty  # Don't serve stale after invalidate()
            and (now - self._fetched_at) < self._max_stale_s
        )

        async with self._lock:
            now = time.monotonic()
            entities = self._search_entities

            # Double-check after acquiring lock
            if entities is not None and not self._dirty and now < self._expires_at:
                self._hits += 1
                return entities

            # Start refresh if not already running (singleflight pattern)
            if self._refresh_task is None or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(self._refresh())
                # Add callback to log/handle exceptions when returning stale data
                self._refresh_task.add_done_callback(self._handle_refresh_done)

            refresh_task = self._refresh_task

        self._misses += 1

        # Return stale data if available, let refresh happen in background
        if can_return_stale and entities is not None:
            logger.debug("Returning stale cache while refreshing in background")
            return entities

        # Wait for refresh to complete
        # Shield protects the shared refresh task from caller cancellation
        # Without this, one cancelled caller would kill the refresh for ALL waiters
        return await asyncio.shield(refresh_task)

    async def _refresh(self) -> list[dict[str, Any]]:
        """Fetch registries and build search-friendly entity list."""
        logger.debug("Refreshing registry cache...")
        start_time = time.monotonic()

        try:
            # Fetch entity and area registries SEQUENTIALLY
            # (parallel WebSocket calls cause connection issues - each call creates new WS)
            try:
                entity_result = await asyncio.wait_for(
                    self._client.send_websocket_message({"type": "config/entity_registry/list"}),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                entity_result = Exception("Entity registry fetch timed out after 30s")
            except Exception as e:
                entity_result = e

            try:
                area_result = await asyncio.wait_for(
                    self._client.send_websocket_message({"type": "config/area_registry/list"}),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                area_result = Exception("Area registry fetch timed out after 30s")
            except Exception as e:
                area_result = e

            # Process entity registry
            if isinstance(entity_result, Exception):
                logger.error(f"Failed to fetch entity registry: {entity_result}")
                # If we have existing data, keep it
                if self._search_entities:
                    return self._search_entities
                raise entity_result

            if not entity_result.get("success"):
                error = entity_result.get("error", "Unknown error")
                logger.error(f"Entity registry fetch failed: {error}")
                if self._search_entities:
                    return self._search_entities
                raise RuntimeError(f"Entity registry fetch failed: {error}")

            entity_registry = entity_result.get("result", [])

            # Process area registry (optional, for area_id lookups)
            area_map: dict[str, str] = {}
            if not isinstance(area_result, Exception) and area_result.get("success"):
                for area in area_result.get("result", []):
                    area_id = area.get("area_id")
                    area_name = area.get("name", area_id)
                    if area_id:
                        area_map[area_id] = area_name

            # Convert to search-friendly format
            search_entities = []
            for entry in entity_registry:
                entity_id = entry.get("entity_id", "")
                if not entity_id:
                    continue

                # Skip disabled entities
                if entry.get("disabled_by"):
                    continue

                domain = entity_id.split(".")[0] if "." in entity_id else ""

                # Get friendly name: prefer user's custom name, then original_name, then entity_id
                friendly_name = (
                    entry.get("name")  # User's custom name
                    or entry.get("original_name")  # Integration's default name
                    or entity_id
                )

                area_id = entry.get("area_id")
                area_name = area_map.get(area_id, area_id) if area_id else None

                search_entities.append({
                    "entity_id": entity_id,
                    "friendly_name": friendly_name,
                    "domain": domain,
                    "area_id": area_id,
                    "area_name": area_name,
                    "device_id": entry.get("device_id"),
                    # Compatibility with existing fuzzy_search format
                    "attributes": {
                        "friendly_name": friendly_name,
                        "area_id": area_id,
                    },
                    "state": "unknown",  # Not fetched - search doesn't need it
                })

            # Update cache
            now = time.monotonic()
            async with self._lock:
                self._entity_registry = entity_registry
                self._area_registry = area_result.get("result", []) if not isinstance(area_result, Exception) else []
                self._search_entities = search_entities
                self._fetched_at = now
                self._expires_at = now + self._ttl_s
                self._dirty = False

            elapsed = (time.monotonic() - start_time) * 1000
            logger.info(
                f"Registry cache refreshed: {len(search_entities)} entities in {elapsed:.1f}ms"
            )

            return search_entities

        except Exception as e:
            logger.error(f"Registry cache refresh failed: {e}")
            # Return existing data if available
            if self._search_entities:
                logger.warning("Using stale cache due to refresh failure")
                return self._search_entities
            raise

    async def get_entity_states(self, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
        """
        Fetch current states for specific entities (for hybrid approach).

        Use this to get runtime state for the top N search results.

        Args:
            entity_ids: List of entity IDs to fetch states for

        Returns:
            Dict mapping entity_id to state data
        """
        if not entity_ids:
            return {}

        # Batch fetch states - could be optimized with parallel calls
        states = {}
        for entity_id in entity_ids:
            try:
                state = await self._client.get_entity_state(entity_id)
                states[entity_id] = state
            except Exception as e:
                logger.debug(f"Failed to fetch state for {entity_id}: {e}")
                states[entity_id] = {"state": "unavailable", "attributes": {}}

        return states


# Singleton cache instance (created per client)
# NOTE: Uses id(client) as key. In this MCP server, clients are long-lived
# singletons (process lifetime), so memory leak is not a concern. For
# short-lived clients, consider WeakKeyDictionary or explicit cleanup.
_cache_instances: dict[int, HARegistryCache] = {}


def get_registry_cache(client: Any, ttl_s: float = 60.0, max_stale_s: float = 300.0) -> HARegistryCache:
    """
    Get or create a registry cache for the given client.

    Uses client's id() as key to ensure one cache per client instance.
    The client is expected to be long-lived (process lifetime in MCP context).
    """
    client_id = id(client)
    if client_id not in _cache_instances:
        _cache_instances[client_id] = HARegistryCache(client, ttl_s, max_stale_s)
        logger.info(f"Created new registry cache for client {client_id}")
    return _cache_instances[client_id]


def clear_all_caches() -> None:
    """Clear all registry cache instances."""
    _cache_instances.clear()
    logger.info("All registry caches cleared")
