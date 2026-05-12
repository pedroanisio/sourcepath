"""codebase_mapper plugin packages.

Each subpackage registers itself with the host's extension registries via
its `register_all()` function. Plugin `.name` values use `l2_*` / `l3_*`
prefixes for load-bearing sort order across registries.
"""
