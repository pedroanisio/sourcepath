"""Phase 6 — resource subscriptions + manifest-change watcher.

Only ``cbm://bundle/<name>/manifest`` URIs are subscribable. When the
on-disk ``run_manifest.json`` for a bundle changes:

1. The cached ``Bundle`` is invalidated so the next read sees the fresh
   data.
2. Every session subscribed to that bundle's manifest URI receives a
   ``notifications/resources/updated`` push.

The watcher uses lightweight polling (default 30 s) — adds no native
dependencies, runs in the server's asyncio loop, and exposes
``poll_once()`` for synchronous testing.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import anyio
from pydantic import AnyUrl

from .resources import parse_uri
from .validators import INVALID_ARGUMENT, ToolError

logger = logging.getLogger("cbm-mcp.subs")


def manifest_uri(bundle_name: str) -> str:
    return f"cbm://bundle/{bundle_name}/manifest"


# --------------------------------------------------------------------------
# Subscription manager
# --------------------------------------------------------------------------


@dataclass
class SubscriptionManager:
    """Tracks (uri -> {sessions}) pairs and pushes resource_updated.

    Sessions are kept as opaque objects (typed as ``Any`` because the
    SDK's ``ServerSession`` carries a generic). The only method we call
    on them is ``send_resource_updated(uri)``.
    """

    _subs: dict[str, set] = field(default_factory=dict)
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    on_subscribe: Callable[[str], None] | None = None

    async def subscribe(self, uri: str, session) -> None:
        if not self.is_subscribable(uri):
            raise ToolError(
                INVALID_ARGUMENT,
                f"only manifest URIs are subscribable; refusing {uri!r}",
            )
        async with self._lock:
            self._subs.setdefault(uri, set()).add(session)
        if self.on_subscribe:
            self.on_subscribe(uri)
        logger.info("subscribed: %s (total now %d)", uri, len(self._subs[uri]))

    async def unsubscribe(self, uri: str, session) -> None:
        async with self._lock:
            if uri in self._subs:
                self._subs[uri].discard(session)
                if not self._subs[uri]:
                    self._subs.pop(uri, None)
        logger.info("unsubscribed: %s", uri)

    async def notify(self, uri: str) -> int:
        """Push a resource_updated notification to every subscriber of uri.

        Returns the number of sessions notified. Closed sessions are
        silently dropped.
        """
        async with self._lock:
            sessions = list(self._subs.get(uri, ()))
        sent = 0
        for session in sessions:
            try:
                await session.send_resource_updated(AnyUrl(uri))
                sent += 1
            except Exception as e:  # noqa: BLE001 — defensive
                logger.warning("failed to notify session for %s: %s", uri, e)
                # drop the dead session
                async with self._lock:
                    if uri in self._subs:
                        self._subs[uri].discard(session)
        return sent

    def is_subscribable(self, uri: str) -> bool:
        try:
            parsed = parse_uri(uri)
        except ToolError:
            return False
        return parsed.kind == "bundle_manifest"

    def subscriber_count(self, uri: str) -> int:
        return len(self._subs.get(uri, ()))


# --------------------------------------------------------------------------
# Manifest watcher
# --------------------------------------------------------------------------


# Type alias — async function that takes a URI string and returns None.
ManifestCallback = Callable[[str], Awaitable[None]]


@dataclass
class ManifestWatcher:
    """Poll-based watcher for ``run_manifest.json`` mtimes under a bundles root.

    ``poll_once()`` is exposed so tests can drive the watcher
    deterministically. The long-running ``run()`` coroutine just calls
    ``poll_once()`` every ``interval`` seconds until cancelled.
    """

    root: Path
    on_change: ManifestCallback
    interval: float = 30.0
    _mtimes: dict[Path, float] = field(default_factory=dict)
    _seeded: bool = False

    def _scan(self) -> dict[Path, float]:
        out: dict[Path, float] = {}
        if not self.root.exists():
            return out
        for child in self.root.iterdir():
            if not child.is_dir():
                continue
            m = child / "run_manifest.json"
            try:
                if m.exists():
                    out[child] = m.stat().st_mtime
            except OSError:
                continue
        return out

    async def poll_once(self) -> list[str]:
        """Scan and fire ``on_change`` for every changed/added manifest.

        Returns the list of URIs that were notified, useful for tests.
        First call after construction *seeds* the mtime cache without
        firing — otherwise the watcher would notify on every existing
        bundle at startup.
        """
        current = self._scan()
        notified: list[str] = []
        if not self._seeded:
            self._mtimes = current
            self._seeded = True
            return notified
        for path, mtime in current.items():
            if self._mtimes.get(path) != mtime:
                uri = manifest_uri(path.name)
                try:
                    await self.on_change(uri)
                    notified.append(uri)
                except Exception as e:  # noqa: BLE001
                    logger.warning("manifest callback failed for %s: %s", uri, e)
        # detect deletions: bundles that disappeared
        for path in self._mtimes:
            if path not in current:
                uri = manifest_uri(path.name)
                try:
                    await self.on_change(uri)
                    notified.append(uri)
                except Exception as e:  # noqa: BLE001
                    logger.warning("manifest callback (deletion) failed for %s: %s", uri, e)
        self._mtimes = current
        return notified

    async def run(self) -> None:
        """Long-running loop — poll every ``interval`` seconds until cancelled."""
        await self.poll_once()  # seed
        while True:
            await anyio.sleep(self.interval)
            await self.poll_once()
