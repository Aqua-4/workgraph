from __future__ import annotations

import json
import socket
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from services.sync_config import SyncEntity


class SyncState(Protocol):
    def __getitem__(self, key: str, /) -> Any: ...


class SyncRepository(Protocol):
    @property
    def sync_entities(self) -> Mapping[str, SyncEntity]: ...

    def get_sync_state(self) -> SyncState | None: ...

    def update_sync_state(
        self,
        *,
        last_push_cursor: str | None,
        last_pull_cursor: str | None,
    ) -> None: ...

    def list_changes_since(
        self,
        *,
        entity: str,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> Iterable[Any]: ...

    def upsert_sync_entity(self, entity: str, payload: dict[str, Any]) -> None: ...

    def mark_deleted(
        self,
        *,
        entity: str,
        row_uuid: str,
        deleted_at: str,
        updated_at: str | None = None,
    ) -> None: ...


class SyncClient(Protocol):
    def push(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def pull(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SyncWorkerSettings:
    batch_size: int = 1000
    pull_limit: int = 1000
    max_pull_pages: int = 20


class HttpSyncClient:
    def __init__(
        self, *, base_url: str, token: str, timeout_seconds: float = 10.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def push(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_json("/api/sync/v1/push", payload)

    def pull(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post_json("/api/sync/v1/pull", payload)

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        raw_body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}{path}",
            data=raw_body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Sync API HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, TimeoutError | socket.timeout):
                raise RuntimeError(
                    f"Sync API request timed out after {self.timeout_seconds:.1f}s. "
                    "Increase sync_timeout_seconds or retry."
                ) from exc
            raise RuntimeError(f"Sync API request failed: {reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError(
                f"Sync API request timed out after {self.timeout_seconds:.1f}s. "
                "Increase sync_timeout_seconds or retry."
            ) from exc


class SyncWorker:
    def __init__(
        self,
        repository: SyncRepository,
        client: SyncClient,
        settings: SyncWorkerSettings | None = None,
    ) -> None:
        self.repository = repository
        self.client = client
        self.settings = settings or SyncWorkerSettings()

    def run_once(self) -> dict[str, Any]:
        state = self.repository.get_sync_state()
        if state is None:
            raise RuntimeError("Sync state not initialized")

        device_id = str(state["device_id"])
        user_id = str(state["user_id"])
        push_cursor = state["last_push_cursor"]
        pull_cursor = state["last_pull_cursor"]

        push_summary = self._push_changes(
            device_id=device_id,
            user_id=user_id,
            initial_cursor=push_cursor,
        )
        if push_summary["last_cursor"] != push_cursor:
            self.repository.update_sync_state(
                last_push_cursor=push_summary["last_cursor"],
                last_pull_cursor=pull_cursor,
            )
            push_cursor = push_summary["last_cursor"]

        pull_summary = self._pull_changes(
            device_id=device_id,
            user_id=user_id,
            initial_cursor=pull_cursor,
        )
        if pull_summary["last_cursor"] != pull_cursor:
            self.repository.update_sync_state(
                last_push_cursor=push_cursor,
                last_pull_cursor=pull_summary["last_cursor"],
            )

        return {
            "push": push_summary,
            "pull": pull_summary,
        }

    def _push_changes(
        self,
        *,
        device_id: str,
        user_id: str,
        initial_cursor: str | None,
    ) -> dict[str, Any]:
        cursor = initial_cursor
        batches = 0
        entity_names = tuple(self.repository.sync_entities)
        sent_counts = {entity: 0 for entity in entity_names}

        while True:
            changes = {
                entity: [
                    {
                        key: value
                        for key, value in dict(row).items()
                        if key != self.repository.sync_entities[entity].local_id
                    }
                    for row in self.repository.list_changes_since(
                        entity=entity,
                        cursor=cursor,
                        limit=self.settings.batch_size,
                    )
                ]
                for entity in entity_names
            }

            if not any(changes.values()):
                break

            response = self.client.push(
                {
                    "device_id": device_id,
                    "user_id": user_id,
                    "supported_entities": list(entity_names),
                    "client_cursor": cursor,
                    "batch_id": _new_uuid(),
                    "changes": changes,
                }
            )
            self._validate_server_entities(response)
            accepted = response.get("accepted")
            if isinstance(accepted, dict):
                rejected = [
                    entity
                    for entity, rows in changes.items()
                    if len(rows) and int(accepted.get(entity, 0)) != len(rows)
                ]
                if rejected:
                    raise RuntimeError(
                        "Sync server did not accept configured entities: "
                        + ", ".join(rejected)
                    )

            for entity, rows in changes.items():
                sent_counts[entity] += len(rows)
            batches += 1

            cursor = (
                response.get("next_push_cursor")
                or _max_row_cursor([row for rows in changes.values() for row in rows])
                or cursor
            )

            if not response.get("next_push_cursor") and all(
                len(rows) < self.settings.batch_size for rows in changes.values()
            ):
                break

        return {
            "batches": batches,
            **sent_counts,
            "last_cursor": cursor,
        }

    def _pull_changes(
        self,
        *,
        device_id: str,
        user_id: str,
        initial_cursor: str | None,
    ) -> dict[str, Any]:
        cursor = initial_cursor
        pages = 0
        entity_names = tuple(self.repository.sync_entities)
        applied_counts = {entity: 0 for entity in entity_names}
        applied_tombstones = 0

        while pages < self.settings.max_pull_pages:
            response = self.client.pull(
                {
                    "device_id": device_id,
                    "user_id": user_id,
                    "supported_entities": list(entity_names),
                    "cursor": cursor,
                    "limit": self.settings.pull_limit,
                }
            )
            pages += 1

            self._validate_server_entities(response)
            changes = response.get("changes") or {}
            unknown_entities = set(changes) - set(entity_names) - {"tombstones"}
            if unknown_entities:
                raise RuntimeError(
                    "Sync server returned unsupported entities: "
                    + ", ".join(sorted(unknown_entities))
                )
            tombstones = changes.get("tombstones") or []

            pulled_rows: list[dict[str, Any]] = []
            for entity in entity_names:
                rows = changes.get(entity) or []
                for row in rows:
                    self.repository.upsert_sync_entity(entity, row)
                applied_counts[entity] += len(rows)
                pulled_rows.extend(rows)
            for tombstone in tombstones:
                deleted_at = tombstone.get("deleted_at")
                if not deleted_at:
                    continue
                self.repository.mark_deleted(
                    entity=str(tombstone.get("entity", "")),
                    row_uuid=str(tombstone.get("id", "")),
                    deleted_at=deleted_at,
                    updated_at=tombstone.get("updated_at") or deleted_at,
                )

            applied_tombstones += len(tombstones)

            next_cursor = (
                response.get("next_cursor") or _max_row_cursor(pulled_rows) or cursor
            )
            cursor = next_cursor
            if not response.get("has_more"):
                break

        return {
            "pages": pages,
            **applied_counts,
            "tombstones": applied_tombstones,
            "last_cursor": cursor,
        }

    def _validate_server_entities(self, response: dict[str, Any]) -> None:
        expected = self.repository.sync_entities
        advertised = response.get("supported_entities")
        requires_capability = any(
            entity.legacy_server_table is None for entity in expected.values()
        )
        if advertised is None:
            if requires_capability:
                raise RuntimeError(
                    "Sync server does not advertise configured entity support"
                )
            return
        if set(advertised) != set(expected):
            raise RuntimeError("Client and server sync entity manifests do not match")


def _max_row_cursor(rows: list[dict[str, Any]]) -> str | None:
    cursor_pairs: list[tuple[str, str]] = []
    for row in rows:
        updated_at = row.get("updated_at") or row.get("created_at")
        row_uuid = row.get("uuid")
        if not updated_at or not row_uuid:
            continue
        cursor_pairs.append((str(updated_at), str(row_uuid)))
    if not cursor_pairs:
        return None
    latest = max(cursor_pairs)
    return f"{latest[0]}|{latest[1]}"


def _new_uuid() -> str:
    from uuid import uuid4

    return str(uuid4())
