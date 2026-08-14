from __future__ import annotations

from typing import Any

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import require_auth
from app.notifications.repository import (
    count_unread_notifications,
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)


notifications_blueprint = Blueprint(
    "notifications",
    __name__,
)


def notification_to_json(
    notification: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": notification["id"],
        "type": notification["notification_type"],
        "severity": notification["severity"],
        "title": notification["title"],
        "message": notification["message"],
        "connectionId": notification["connection_id"],
        "projectId": notification["project_id"],
        "deploymentId": notification["deployment_id"],
        "environmentId": notification["environment_id"],
        "resourceType": notification["resource_type"],
        "resourceId": notification["resource_id"],
        "actionUrl": notification["action_url"],
        "metadata": notification["metadata"] or {},
        "readAt": (
            notification["read_at"].isoformat()
            if notification["read_at"]
            else None
        ),
        "resolvedAt": (
            notification["resolved_at"].isoformat()
            if notification["resolved_at"]
            else None
        ),
        "createdAt": notification["created_at"].isoformat(),
    }


@notifications_blueprint.get("")
@require_auth
def get_notifications():
    user_id = int(g.current_user["id"])

    try:
        limit = int(request.args.get("limit", "20"))
    except (TypeError, ValueError):
        limit = 20

    limit = max(1, min(limit, 50))

    unread_only = str(
        request.args.get("unreadOnly", "false")
    ).lower() in {
        "1",
        "true",
        "yes",
    }

    notifications = list_notifications(
        user_id=user_id,
        limit=limit,
        unread_only=unread_only,
    )

    unread_count = count_unread_notifications(
        user_id=user_id,
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "notifications": [
                    notification_to_json(notification)
                    for notification in notifications
                ],
                "unreadCount": unread_count,
            },
        }
    )


@notifications_blueprint.post("/<int:notification_id>/read")
@require_auth
def read_notification(notification_id: int):
    user_id = int(g.current_user["id"])

    notification = mark_notification_read(
        notification_id=notification_id,
        user_id=user_id,
    )

    if notification is None:
        return (
            jsonify(
                {
                    "success": False,
                    "error": {
                        "code": "NOTIFICATION_NOT_FOUND",
                        "message": "Notification introuvable.",
                    },
                }
            ),
            404,
        )

    return jsonify(
        {
            "success": True,
            "data": {
                "notification": notification_to_json(notification),
                "unreadCount": count_unread_notifications(
                    user_id=user_id,
                ),
            },
        }
    )


@notifications_blueprint.post("/read-all")
@require_auth
def read_all_notifications():
    user_id = int(g.current_user["id"])

    updated_count = mark_all_notifications_read(
        user_id=user_id,
    )

    return jsonify(
        {
            "success": True,
            "data": {
                "updatedCount": updated_count,
                "unreadCount": 0,
            },
        }
    )
