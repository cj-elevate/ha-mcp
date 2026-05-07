---
type: project
updated: 2026-03-23
project: ha-mcp-extended
area: servers
path: D:\servers\ha-mcp-extended
status: active
tags: [ha, mcp, tools, config, entities, services, helpers]
---
# ha-mcp-extended

## Purpose

Fork of homeassistant-ai/ha-mcp providing 104 MCP tools for Home Assistant control, automation management,            system monitoring via natural language.

---

## Quick Commands

| Command | Purpose |
|---------|---------|
| `uv sync` | Install dependencies |
| `uv run pytest tests/ -v` | Run tests |
| `uv run python -m ha_mcp.smoke_test` | Smoke test against live HA |
| `uv run python -m src.ha_mcp` | Start server |

---

## Key Files

| File | Purpose |
|------|---------|
| `src/ha_mcp/server.py` | FastMCP server entry point |
| `src/ha_mcp/config.py` | Configuration handling |
| `src/ha_mcp/tools/registry.py` | Tool registration and discovery |
| `src/ha_mcp/client/rest_client.py` | HTTP client for HA API |
| `src/ha_mcp/client/websocket_client.py` | WebSocket client for real-time |

---

## Patterns

### Tool Organization

Tools are split by domain in `src/ha_mcp/tools/`:

```
tools_config_automations.py  # Automation CRUD
tools_config_helpers.py      # Input helpers
tools_service.py             # Service calls
tools_search.py              # Entity search
```

### Tool Registration

Tools use the `@mcp.tool()` decorator with structured return types:

```python
@mcp.tool()
async def ha_search_entities(query: str, limit: int = 10) -> dict:
    """Search for entities with fuzzy matching."""
    ...
```

### Error Handling

All tools use `HAError` exception class from `errors.py` with structured error responses.

---

## Programmatic Testing

| Field | Value |
|-------|-------|
| Service | STDIO via master-mcp-proxy (PM2) |
| Host | n/a (STDIO, not HTTP) |
| Auth | none (proxy-mediated) |
| Secret Source | Proxy `.env` (if backend needs secrets) |
| Secret Keys | n/a (proxy handles auth to external APIs) |

### Backend Health (via proxy)
```bash
# Verify backend is reachable through the proxy
# MCP tool: health_check(backends=["home-assistant"])
# Or via curl to proxy health:
curl -sf http://127.0.0.1:3005/health | python -m json.tool
```

### Tool Verification
```bash
# MCP tool: search_tools("home-assistant")
# Expected: list of tools registered by this backend
```

**Note:** This server has no standalone HTTP endpoint. All access is mediated through
the master-mcp-proxy. To test specific tools, use `execute_indexed_tool` or the
tool's hot name if available. Enable scope first: `enable_scopes(["ha"])`.

## Gotchas

- **Fork of upstream**: Check upstream for updates (`git fetch upstream`)
- **Rule 05 protection**: Automations require user approval before modification
- **Long-running token**: Use a long-lived access token, not session tokens
- **Entity caching**: Registry cache has 60s TTL; call `registry_cache.invalidate()` after HA config changes
- **Async context**: All tools are async; use `await` for client calls
- **Search performance**: `ha_search_entities` uses:
  - Registry cache (5x smaller than get_states)
  - Thread pool for CPU-heavy fuzzy matching
  - 5s timeout to prevent hangs
  - stdlib difflib (zero external dependencies)

---

## Dependencies

| Dependency | Purpose | Notes |
|------------|---------|-------|
| `fastmcp` | MCP server framework | Core dependency |
| `httpx` | Async HTTP client | For REST API |
| `websockets` | WebSocket client | For real-time events |

---

## Testing

### Running Tests

```powershell
# All tests
uv run pytest tests/ -v

# With coverage
uv run pytest tests/ --cov=src --cov-report=term-missing

# Smoke test (requires live HA)
uv run python -m ha_mcp.smoke_test
```

### Test Environment

Requires `HA_URL` and `HA_TOKEN` environment variables for integration tests.

---

## Related

- Upstream: https://github.com/homeassistant-ai/ha-mcp
- Rule: `.claude/rules/05_home_assistant.md` - HA protection rules
- Skill: `.claude/skills/home-assistant/SKILL.md` - Usage guidance
- Master MCP config: `D:\servers\master-mcp-server\config.json`
