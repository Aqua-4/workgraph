from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request

from db.repository import ActivityRepository


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
        repository: ActivityRepository,
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
        sent_sessions = 0
        sent_journals = 0
        sent_reflections = 0

        while True:
            sessions = [
                dict(row)
                for row in self.repository.list_session_changes_since(
                    cursor=cursor, limit=self.settings.batch_size
                )
            ]
            journals = [
                dict(row)
                for row in self.repository.list_journal_changes_since(
                    cursor=cursor, limit=self.settings.batch_size
                )
            ]
            reflections = [
                dict(row)
                for row in self.repository.list_reflection_changes_since(
                    cursor=cursor, limit=self.settings.batch_size
                )
            ]

            if not sessions and not journals and not reflections:
                break

            response = self.client.push(
                {
                    "device_id": device_id,
                    "user_id": user_id,
                    "client_cursor": cursor,
                    "batch_id": _new_uuid(),
                    "changes": {
                        "sessions": sessions,
                        "journal_entries": journals,
                        "daily_reflections": reflections,
                    },
                }
            )

            sent_sessions += len(sessions)
            sent_journals += len(journals)
            sent_reflections += len(reflections)
            batches += 1

            cursor = (
                response.get("next_push_cursor")
                or _max_row_cursor(sessions + journals + reflections)
                or cursor
            )

            if (
                not response.get("next_push_cursor")
                and len(sessions) < self.settings.batch_size
                and len(journals) < self.settings.batch_size
                and len(reflections) < self.settings.batch_size
            ):
                break

        return {
            "batches": batches,
            "sessions": sent_sessions,
            "journal_entries": sent_journals,
            "daily_reflections": sent_reflections,
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
        applied_sessions = 0
        applied_journals = 0
        applied_reflections = 0
        applied_tombstones = 0

        while pages < self.settings.max_pull_pages:
            response = self.client.pull(
                {
                    "device_id": device_id,
                    "user_id": user_id,
                    "cursor": cursor,
                    "limit": self.settings.pull_limit,
                }
            )
            pages += 1

            changes = response.get("changes") or {}
            sessions = changes.get("sessions") or []
            journals = changes.get("journal_entries") or []
            reflections = changes.get("daily_reflections") or []
            tombstones = changes.get("tombstones") or []

            for row in sessions:
                self.repository.upsert_session_by_uuid(row)
            for row in journals:
                self.repository.upsert_journal_by_uuid(row)
            for row in reflections:
                self.repository.upsert_reflection_by_uuid(row)
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

            applied_sessions += len(sessions)
            applied_journals += len(journals)
            applied_reflections += len(reflections)
            applied_tombstones += len(tombstones)

            next_cursor = (
                response.get("next_cursor")
                or _max_row_cursor(sessions + journals + reflections)
                or cursor
            )
            cursor = next_cursor
            if not response.get("has_more"):
                break

        return {
            "pages": pages,
            "sessions": applied_sessions,
            "journal_entries": applied_journals,
            "daily_reflections": applied_reflections,
            "tombstones": applied_tombstones,
            "last_cursor": cursor,
        }


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
