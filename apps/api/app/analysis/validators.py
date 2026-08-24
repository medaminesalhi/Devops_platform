from __future__ import annotations

import re

from typing import Any


class AnalysisValidationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.message = message


def read_start_analysis_payload(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = payload or {}

    requested_commit_sha = str(
        payload.get("commitSha") or ""
    ).strip().lower()

    if requested_commit_sha and not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}",
        requested_commit_sha,
    ):
        raise AnalysisValidationError(
            "INVALID_COMMIT_SHA",
            "Le commit sélectionné doit être un SHA Git complet (40 ou 64 caractères hexadécimaux).",
        )

    commit_policy = str(
        payload.get(
            "commitPolicy",
            "latest",
        )
    ).strip()

    if commit_policy not in {
        "validated",
        "latest",
    }:
        raise AnalysisValidationError(
            "INVALID_COMMIT_POLICY",
            (
                "La politique de commit doit être "
                "« validated » ou « latest »."
            ),
        )

    # Un SHA explicite signifie toujours : analyser exactement cette version.
    if requested_commit_sha:
        commit_policy = "validated"

    return {
        "commit_policy": commit_policy,
        "requested_commit_sha": requested_commit_sha or None,
    }


def read_component_update_payload(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AnalysisValidationError(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
        )

    allowed_fields = {
        "name":
            "name",

        "componentType":
            "component_type",

        "runtime":
            "runtime",

        "framework":
            "framework",

        "packageManager":
            "package_manager",

        "buildCommand":
            "build_command",

        "startCommand":
            "start_command",

        "detectedPort":
            "detected_port",

        "deployable":
            "deployable",
    }

    changes: dict[str, Any] = {}

    for api_field, database_field in (
        allowed_fields.items()
    ):
        if api_field in payload:
            changes[database_field] = (
                payload[api_field]
            )

    if not changes:
        raise AnalysisValidationError(
            "NO_COMPONENT_CHANGE",
            (
                "Aucune modification "
                "de composant n'a été fournie."
            ),
        )

    if "detected_port" in changes:
        value = changes[
            "detected_port"
        ]

        if value in (
            None,
            "",
        ):
            changes[
                "detected_port"
            ] = None

        else:
            try:
                port = int(value)

            except (
                TypeError,
                ValueError,
            ) as error:
                raise AnalysisValidationError(
                    "INVALID_COMPONENT_PORT",
                    "Le port est invalide.",
                ) from error

            if port < 1 or port > 65535:
                raise AnalysisValidationError(
                    "INVALID_COMPONENT_PORT",
                    (
                        "Le port doit être compris "
                        "entre 1 et 65535."
                    ),
                )

            changes[
                "detected_port"
            ] = port

    if "deployable" in changes:
        changes["deployable"] = bool(
            changes["deployable"]
        )

    return changes