"""MCP server for codebase-mapper bundles (read-only).

Phase 1 ships the schemas + validator helpers. Phase 2 adds the handler
registry. Phases 3+ add the stdio transport, resources, and prompts.
"""
from __future__ import annotations

from .handlers import HANDLERS, dispatch
from .prompts import PROMPTS, get_prompt, list_prompts
from .resources import (
    list_resource_templates,
    list_static_resources,
    parse_uri,
    read_resource,
)
from .auth import (
    AuthError,
    JwtVerifier,
    StaticTokenVerifier,
    TokenVerifier,
    build_verifier_from_env,
    jwks_key_resolver,
    static_key_resolver,
)
from .http_transport import (
    BearerAuthMiddleware,
    make_bearer_dependency,
    mount_mcp,
)
from .sparql import (
    ENABLE_ENV as SPARQL_ENABLE_ENV,
    MAX_QUERY_LEN as SPARQL_MAX_QUERY_LEN,
    MAX_ROWS as SPARQL_MAX_ROWS,
    MUTATING_KEYWORDS as SPARQL_MUTATING_KEYWORDS,
    clear_graph_cache as clear_sparql_graph_cache,
    is_enabled as sparql_is_enabled,
    run_sparql,
)
from .observability import (
    DEFAULT_TIMEOUT_SECONDS,
    TIMEOUTS,
    ToolTimeoutError,
    audit_log,
    audit_logger,
    configure_audit_logger,
    dispatch_with_budget,
    timeout_for,
)
from .subscriptions import ManifestWatcher, SubscriptionManager, manifest_uri
from .schemas import (
    DESCRIPTIONS,
    INPUT_SCHEMAS,
    OUTPUT_SCHEMAS,
    RESOURCE_URI_TEMPLATES,
    TOOL_NAMES,
    validate_in,
    validate_out,
)
from .validators import (
    INTERNAL,
    INVALID_ARGUMENT,
    NOT_FOUND,
    TOO_LARGE,
    ToolError,
)

__all__ = [
    "DESCRIPTIONS",
    "HANDLERS",
    "INPUT_SCHEMAS",
    "INTERNAL",
    "INVALID_ARGUMENT",
    "NOT_FOUND",
    "AuthError",
    "BearerAuthMiddleware",
    "DEFAULT_TIMEOUT_SECONDS",
    "JwtVerifier",
    "ManifestWatcher",
    "OUTPUT_SCHEMAS",
    "PROMPTS",
    "RESOURCE_URI_TEMPLATES",
    "SPARQL_ENABLE_ENV",
    "SPARQL_MAX_QUERY_LEN",
    "SPARQL_MAX_ROWS",
    "SPARQL_MUTATING_KEYWORDS",
    "StaticTokenVerifier",
    "SubscriptionManager",
    "TIMEOUTS",
    "TokenVerifier",
    "ToolTimeoutError",
    "TOO_LARGE",
    "TOOL_NAMES",
    "ToolError",
    "audit_log",
    "audit_logger",
    "build_verifier_from_env",
    "clear_sparql_graph_cache",
    "configure_audit_logger",
    "dispatch",
    "dispatch_with_budget",
    "jwks_key_resolver",
    "run_sparql",
    "sparql_is_enabled",
    "static_key_resolver",
    "timeout_for",
    "get_prompt",
    "list_prompts",
    "list_resource_templates",
    "list_static_resources",
    "make_bearer_dependency",
    "manifest_uri",
    "mount_mcp",
    "parse_uri",
    "read_resource",
    "validate_in",
    "validate_out",
]
