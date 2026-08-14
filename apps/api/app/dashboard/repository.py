from __future__ import annotations

from typing import Any

from app.database import (
    get_database_connection,
)


INTEGRATION_PROVIDER_KEYS = {
    "gitlab": "gitlab",
    "nexus": "nexus",
    "argocd": "argoCd",
    "kubernetes": "kubernetes",
    "ollama": "ollama",
}


def _aggregate_integration_statuses(
    rows: list[dict[str, Any]],
) -> dict[str, str]:
    """
    Transforme les connexions visibles en un statut synthétique par provider.

    Priorité :
      offline > degraded > online > unchecked > not_configured

    Ainsi une seule connexion GitLab en erreur suffit à signaler le provider
    comme indisponible sur le dashboard.
    """

    statuses_by_provider: dict[str, list[str]] = {}

    for row in rows:
        provider_type = str(row["provider_type"])
        statuses_by_provider.setdefault(
            provider_type,
            [],
        ).append(str(row["status"]))

    result: dict[str, str] = {}

    for provider_type, output_key in INTEGRATION_PROVIDER_KEYS.items():
        statuses = statuses_by_provider.get(
            provider_type,
            [],
        )

        if not statuses:
            result[output_key] = "not_configured"
            continue

        if "offline" in statuses:
            result[output_key] = "offline"
            continue

        if "degraded" in statuses:
            result[output_key] = "degraded"
            continue

        if "online" in statuses:
            result[output_key] = "online"
            continue

        if "unchecked" in statuses:
            result[output_key] = "unchecked"
            continue

        result[output_key] = "not_configured"

    return result


def get_dashboard_overview(
    owner_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Récupère les statistiques générales depuis PostgreSQL.

    owner_user_id = None : vue globale administrateur.
    owner_user_id = N    : vue limitée aux ressources de N.
    """

    with get_database_connection() as connection:
        project_statistics = connection.execute(
            """
                SELECT
                    COUNT(*) FILTER (
                        WHERE status <> 'archived'
                    )::INTEGER AS total_projects,

                    COUNT(*) FILTER (
                        WHERE status = 'active'
                    )::INTEGER AS active_projects

                FROM projects

                WHERE (
                    %s::BIGINT IS NULL
                    OR created_by = %s
                );
            """,
            (owner_user_id, owner_user_id),
        ).fetchone()

        deployment_statistics = connection.execute(
            """
                SELECT
                    COUNT(*) FILTER (
                        WHERE deployment.created_at >=
                            DATE_TRUNC(
                                'day',
                                CURRENT_TIMESTAMP
                            )
                    )::INTEGER AS deployments_today,

                    COUNT(*) FILTER (
                        WHERE deployment.status = 'running'
                    )::INTEGER AS running_deployments,

                    COUNT(*) FILTER (
                        WHERE
                            deployment.status = 'succeeded'
                            AND deployment.created_at >=
                                CURRENT_TIMESTAMP
                                - INTERVAL '7 days'
                    )::INTEGER AS successful_deployments_7d,

                    COUNT(*) FILTER (
                        WHERE
                            deployment.status = 'failed'
                            AND deployment.created_at >=
                                CURRENT_TIMESTAMP
                                - INTERVAL '7 days'
                    )::INTEGER AS failed_deployments_7d

                FROM deployments AS deployment

                INNER JOIN projects AS project
                    ON project.id = deployment.project_id

                WHERE (
                    %s::BIGINT IS NULL
                    OR project.created_by = %s
                );
            """,
            (owner_user_id, owner_user_id),
        ).fetchone()

        project_status_rows = connection.execute(
            """
                SELECT
                    status,
                    COUNT(*)::INTEGER AS count

                FROM projects

                WHERE (
                    %s::BIGINT IS NULL
                    OR created_by = %s
                )

                GROUP BY status
                ORDER BY status;
            """,
            (owner_user_id, owner_user_id),
        ).fetchall()

        recent_deployments = connection.execute(
            """
                SELECT
                    deployment.id,
                    deployment.environment,
                    deployment.status,
                    deployment.commit_sha,
                    deployment.image_tag,
                    deployment.created_at,
                    deployment.started_at,
                    deployment.finished_at,

                    project.id AS project_id,
                    project.name AS project_name,
                    project.slug AS project_slug,

                    COALESCE(
                        NULLIF(
                            TRIM(
                                CONCAT_WS(
                                    ' ',
                                    platform_user.first_name,
                                    platform_user.last_name
                                )
                            ),
                            ''
                        ),
                        platform_user.username,
                        'Système'
                    ) AS triggered_by_name

                FROM deployments AS deployment

                INNER JOIN projects AS project
                    ON project.id = deployment.project_id

                LEFT JOIN users AS platform_user
                    ON platform_user.id = deployment.triggered_by

                WHERE (
                    %s::BIGINT IS NULL
                    OR project.created_by = %s
                )

                ORDER BY deployment.created_at DESC
                LIMIT 8;
            """,
            (owner_user_id, owner_user_id),
        ).fetchall()

        integration_rows = connection.execute(
            """
                SELECT
                    provider_type,
                    status
                FROM integration_connections
                WHERE enabled = TRUE
                  AND (
                      %s::BIGINT IS NULL
                      OR created_by = %s
                  );
            """,
            (owner_user_id, owner_user_id),
        ).fetchall()

    total_finished = (
        deployment_statistics[
            "successful_deployments_7d"
        ]
        + deployment_statistics[
            "failed_deployments_7d"
        ]
    )

    if total_finished > 0:
        success_rate = round(
            (
                deployment_statistics[
                    "successful_deployments_7d"
                ]
                / total_finished
            )
            * 100,
            1,
        )
    else:
        success_rate = 0.0

    return {
        "metrics": {
            "totalProjects": project_statistics[
                "total_projects"
            ],
            "activeProjects": project_statistics[
                "active_projects"
            ],
            "deploymentsToday": deployment_statistics[
                "deployments_today"
            ],
            "runningDeployments": deployment_statistics[
                "running_deployments"
            ],
            "successfulDeployments7d": deployment_statistics[
                "successful_deployments_7d"
            ],
            "failedDeployments7d": deployment_statistics[
                "failed_deployments_7d"
            ],
            "successRate7d": success_rate,
        },
        "projectStatus": [
            {
                "status": row["status"],
                "count": row["count"],
            }
            for row in project_status_rows
        ],
        "recentDeployments": [
            {
                "id": row["id"],
                "projectId": row["project_id"],
                "projectName": row["project_name"],
                "projectSlug": row["project_slug"],
                "environment": row["environment"],
                "status": row["status"],
                "commitSha": row["commit_sha"],
                "imageTag": row["image_tag"],
                "triggeredBy": row[
                    "triggered_by_name"
                ],
                "createdAt": row[
                    "created_at"
                ].isoformat(),
                "startedAt": (
                    row["started_at"].isoformat()
                    if row["started_at"]
                    else None
                ),
                "finishedAt": (
                    row["finished_at"].isoformat()
                    if row["finished_at"]
                    else None
                ),
            }
            for row in recent_deployments
        ],
        "integrationServices": _aggregate_integration_statuses(
            integration_rows,
        ),
    }
