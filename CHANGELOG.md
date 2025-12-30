# Changelog - ha-mcp-extended

Extended Home Assistant MCP with automation management

All notable changes to this server will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [2025-12-26]

### Added
- `ha_update_entity` tool - Update entity icon or friendly name without renaming
  - Uses WebSocket `config/entity_registry/update` API
  - Validates MDI icon format (`mdi:icon-name`)
  - Validates icon exists in MDI library before setting (rejects invalid with suggestions)
  - Simpler alternative to `ha_rename_entity` when only updating icon/name
- `ha_search_icons` tool - Search Material Design Icons by keyword
  - Queries 7400+ MDI icons from cached metadata
  - Returns `mdi:icon-name` format ready for `ha_update_entity`
  - Example: `ha_search_icons("flood")` returns `mdi:light-flood-down`, `mdi:light-flood-up`
- `ha_validate_icon` tool - Check if an icon name exists
  - Returns valid/invalid status with suggestions for invalid icons
  - Normalizes input (accepts "home" or "mdi:home")
- **Icon cache system** for MDI metadata
  - Cache location: `%LOCALAPPDATA%/ha-mcp/cache/` (Windows) or `~/.cache/ha-mcp/` (Linux)
  - 7-day TTL with automatic refresh from CDN
  - Async download via `asyncio.to_thread()` (non-blocking)
  - Atomic writes using tempfile + os.replace()

### Changed
- **OPTIMIZATION: `ha_search_entities` now uses registry cache**
  - Uses `entity_registry/list` instead of `get_states()` (5x smaller payload: ~140KB vs ~700KB)
  - TTL-based caching with 60s TTL and stale-while-revalidate (300s max stale)
  - Singleflight pattern prevents concurrent fetch storms on cache miss
  - Search latency: 100-500ms → <1ms on cache hit
  - New file: `src/ha_mcp/utils/registry_cache.py` - HARegistryCache class
  - Modified: `src/ha_mcp/tools/smart_search.py` - now uses cached registry data

### Fixed
- **Registry cache**: Stale-while-revalidate now respects `_dirty` flag (Codex review)
  - After `invalidate()`, stale data is not returned until fresh data is fetched
- **Registry cache**: Added done-callback to prevent "Task exception was never retrieved" warnings
  - Background refresh failures are now properly logged

---

## How to Add Entries

When making changes to this server, add entries under today's date:

```markdown
## [YYYY-MM-DD]

### Added
- New feature description

### Changed
- What was modified

### Fixed
- Bug that was fixed

### Removed
- What was removed

### Security
- Security-related changes
```
