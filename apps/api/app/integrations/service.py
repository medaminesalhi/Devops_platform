from __future__ import annotations

from typing import Any

from app.integrations.adapters import (
    IntegrationTestResult,
    get_adapter,
)

from app.integrations.repository import (
    find_connection,
    list_due_connection_ids,
    save_health_result,
)

from app.integrations.security import (
    decrypt_credential,
)


def test_configuration(
    configuration: dict[str, Any],
    credential: str | None,
) -> dict[str, Any]:
    """
    Teste une configuration non encore enregistrée.
    """

    adapter = get_adapter(
        configuration["provider_type"]
    )

    result = adapter.test_connection(
        configuration,
        credential,
    )

    return result.to_dict()


def test_saved_connection(
    connection_id: int,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
]:
    """
    Teste une connexion enregistrée et sauvegarde
    son nouvel état dans PostgreSQL.
    """

    connection = find_connection(
        connection_id
    )

    if connection is None:
        raise ValueError(
            "Connexion introuvable."
        )

    credential = decrypt_credential(
        connection.get(
            "secret_ciphertext"
        )
    )

    adapter = get_adapter(
        connection["provider_type"]
    )

    raw_result = adapter.test_connection(
        connection,
        credential,
    )

    final_status, failures = (
        calculate_final_status(
            connection,
            raw_result,
        )
    )

    result_dictionary = (
        raw_result.to_dict()
    )

    result_dictionary["status"] = (
        final_status
    )

    updated_connection = save_health_result(
        connection_id=connection_id,
        final_status=final_status,
        consecutive_failures=failures,
        result=result_dictionary,
    )

    return (
        updated_connection,
        result_dictionary,
    )


def calculate_final_status(
    connection: dict[str, Any],
    result: IntegrationTestResult,
) -> tuple[str, int]:
    """
    Évite une notification dès le premier échec.

    Exemple :
    - seuil = 3
    - premier échec → degraded
    - deuxième échec → degraded
    - troisième échec → offline
    """

    if result.status == "online":
        return "online", 0

    if result.status == "not_configured":
        return "not_configured", 0

    failures = (
        int(
            connection[
                "consecutive_failures"
            ]
        )
        + 1
    )

    threshold = int(
        connection["failure_threshold"]
    )

    if failures >= threshold:
        return "offline", failures

    return "degraded", failures


def check_due_connections() -> int:
    """
    Vérifie toutes les connexions arrivées
    à leur échéance de monitoring.
    """

    connection_ids = (
        list_due_connection_ids()
    )

    checked_count = 0

    for connection_id in connection_ids:
        try:
            test_saved_connection(
                connection_id
            )

            checked_count += 1

        except Exception:
            # Une erreur sur une intégration ne doit
            # pas arrêter les autres contrôles.
            continue

    return checked_count