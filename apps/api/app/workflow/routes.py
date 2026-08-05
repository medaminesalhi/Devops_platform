from __future__ import annotations

from typing import Any

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    request,
)

from app.auth.decorators import (
    require_auth,
)

from app.workflow.ai import (
    AiProviderError,
    list_models_for_connection,
)

from app.workflow.contracts import (
    ContractValidationError,
    build_default_contract,
    validate_contract,
)

from app.workflow.generation_repository import (
    confirm_generation_review,
    create_workflow_generation,
    find_generation_artifact,
    find_generation_for_project,
    find_latest_workflow_generation,
    list_generation_artifacts,
    list_generation_events,
    review_artifact,
    update_artifact_content,
)

from app.workflow.renderers import (
    validate_artifact_content,
)

from app.workflow.repository import (
    confirm_contract,
    find_ai_connection,
    find_contract_environment,
    find_contract_for_project,
    find_latest_contract,
    find_project_workflow_context,
    list_ai_connections,
    list_analysis_components,
    list_contract_environments,
    save_contract,
)


workflow_blueprint = Blueprint(
    "workflow",
    __name__,
)


WORKFLOW_WRITE_ROLES = {
    "admin",
    "administrator",
    "devops",
    "developer",
}


MAX_ARTIFACT_CONTENT_BYTES = (
    1_000_000
)

MAX_REVIEW_COMMENT_LENGTH = (
    2_000
)

MAX_AI_MODEL_LENGTH = (
    255
)


# ============================================================
# RÉPONSES ET AUTORISATIONS
# ============================================================

def error_response(
    code: str,
    message: str,
    status: int,
):
    return (
        jsonify(
            {
                "success": False,

                "error": {
                    "code":
                        code,

                    "message":
                        message,
                },
            }
        ),

        status,
    )


def date_to_json(
    value: Any,
) -> str | None:
    return (
        value.isoformat()

        if value

        else None
    )


def current_user_id(
) -> int:
    return int(
        g.current_user["id"]
    )


def current_user_roles(
) -> set[str]:
    return {
        str(role).lower()

        for role
        in (
            g.current_user.get(
                "roles"
            )
            or []
        )
    }


def current_user_can_write(
) -> bool:
    return bool(
        current_user_roles()
        .intersection(
            WORKFLOW_WRITE_ROLES
        )
    )


def ensure_write_permission(
) -> None:
    if not current_user_can_write():
        raise PermissionError(
            (
                "Votre rôle ne permet pas "
                "de modifier le workflow "
                "du projet."
            )
        )


# ============================================================
# SÉRIALISATION DU CONTRAT
# ============================================================

def contract_to_json(
    contract:
        dict[str, Any]
        | None,
) -> dict[str, Any] | None:
    if contract is None:
        return None


    return {
        "id":
            contract["id"],

        "projectId":
            contract[
                "project_id"
            ],

        "analysisRunId":
            contract[
                "analysis_run_id"
            ],

        "environmentId":
            contract[
                "environment_id"
            ],

        "status":
            contract["status"],

        "revision":
            contract["revision"],

        "namespace":
            contract["namespace"],

        "domain":
            contract.get(
                "domain"
            ),

        "contract":
            contract.get(
                "contract"
            )
            or {},

        "validation":
            contract.get(
                "validation"
            )
            or {},


        "project": {
            "name":
                contract.get(
                    "project_name"
                ),

            "slug":
                contract.get(
                    "project_slug"
                ),
        },


        "analysis": {
            "version":
                contract.get(
                    "analyzed_commit_sha"
                ),

            "confirmedAt":
                date_to_json(
                    contract.get(
                        (
                            "analysis_"
                            "confirmed_at"
                        )
                    )
                ),
        },


        "environment": {
            "name":
                contract.get(
                    "environment_name"
                ),

            "code":
                contract.get(
                    "environment_code"
                ),

            "environmentType":
                contract.get(
                    "environment_type"
                ),
        },


        "createdBy":
            contract.get(
                "created_by"
            ),

        "updatedBy":
            contract.get(
                "updated_by"
            ),

        "confirmedBy":
            contract.get(
                "confirmed_by"
            ),

        "createdAt":
            date_to_json(
                contract.get(
                    "created_at"
                )
            ),

        "updatedAt":
            date_to_json(
                contract.get(
                    "updated_at"
                )
            ),

        "confirmedAt":
            date_to_json(
                contract.get(
                    "confirmed_at"
                )
            ),
    }


# ============================================================
# SÉRIALISATION D’UN ENVIRONNEMENT
# ============================================================

def environment_to_json(
    environment:
        dict[str, Any],
) -> dict[str, Any]:
    return {
        "id":
            environment["id"],

        "name":
            environment["name"],

        "code":
            environment["code"],

        "environmentType":
            environment[
                "environment_type"
            ],

        "description":
            environment.get(
                "description"
            ),

        "namespace":
            environment[
                "namespace"
            ],

        "domain":
            environment.get(
                "domain"
            ),

        "configurationStatus":
            environment[
                "configuration_status"
            ],

        "isDefault":
            environment[
                "is_default"
            ],

        "services":
            environment.get(
                "services"
            )
            or [],
    }


# ============================================================
# SÉRIALISATION D’UNE CONNEXION IA
#
# Le credential chiffré n’est jamais retourné au frontend.
# ============================================================

def ai_connection_to_json(
    connection:
        dict[str, Any],
) -> dict[str, Any]:
    return {
        "id":
            connection["id"],

        "name":
            connection["name"],

        "providerType":
            connection[
                "provider_type"
            ],

        "baseUrl":
            connection[
                "base_url"
            ],

        "description":
            connection.get(
                "description"
            ),

        "enabled":
            connection["enabled"],

        "verifySsl":
            connection[
                "verify_ssl"
            ],

        "status":
            connection["status"],

        "authType":
            connection.get(
                "auth_type"
            )
            or "none",

        "credentialConfigured":
            bool(
                connection.get(
                    (
                        "credential_"
                        "configured"
                    )
                )
            ),

        "lastCheckedAt":
            date_to_json(
                connection.get(
                    "last_checked_at"
                )
            ),

        "lastLatencyMs":
            connection.get(
                "last_latency_ms"
            ),
    }


# ============================================================
# SÉRIALISATION D’UNE GÉNÉRATION
# ============================================================

def generation_to_json(
    generation:
        dict[str, Any]
        | None,
) -> dict[str, Any] | None:
    if generation is None:
        return None


    generation_error = None


    if (
        generation.get(
            "error_code"
        )

        or generation.get(
            "error_message"
        )
    ):
        generation_error = {
            "code":
                generation.get(
                    "error_code"
                ),

            "message":
                generation.get(
                    "error_message"
                ),
        }


    return {
        "id":
            generation["id"],

        "projectId":
            generation[
                "project_id"
            ],

        "analysisRunId":
            generation[
                "analysis_run_id"
            ],

        "environmentId":
            generation[
                "environment_id"
            ],

        "contractId":
            generation.get(
                "contract_id"
            ),

        "aiRunId":
            generation.get(
                "ai_run_id"
            ),

        "aiConnectionId":
            generation.get(
                "ai_connection_id"
            ),

        "aiModel":
            generation.get(
                "ai_model"
            ),

        "generationMode":
            generation.get(
                "generation_mode"
            ),

        "promptVersion":
            generation.get(
                "prompt_version"
            ),

        "status":
            generation["status"],

        "progress":
            generation["progress"],

        "currentStep":
            generation[
                "current_step"
            ],

        "summary":
            generation.get(
                "summary"
            )
            or {},

        "error":
            generation_error,


        "project": {
            "name":
                generation.get(
                    "project_name"
                ),

            "slug":
                generation.get(
                    "project_slug"
                ),
        },


        "analysis": {
            "version":
                generation.get(
                    "analyzed_commit_sha"
                ),

            "confirmedAt":
                date_to_json(
                    generation.get(
                        (
                            "analysis_"
                            "confirmed_at"
                        )
                    )
                ),
        },


        "environment": {
            "name":
                generation.get(
                    "environment_name"
                ),

            "code":
                generation.get(
                    "environment_code"
                ),

            "environmentType":
                generation.get(
                    "environment_type"
                ),

            "namespace":
                generation.get(
                    (
                        "environment_"
                        "namespace"
                    )
                ),

            "domain":
                generation.get(
                    (
                        "environment_"
                        "domain"
                    )
                ),
        },


        "contract": {
            "status":
                generation.get(
                    "contract_status"
                ),

            "revision":
                generation.get(
                    "contract_revision"
                ),

            "validation":
                generation.get(
                    "contract_validation"
                )
                or {},
        },


        "createdBy":
            generation.get(
                "created_by"
            ),

        "confirmedBy":
            generation.get(
                "confirmed_by"
            ),

        "createdAt":
            date_to_json(
                generation.get(
                    "created_at"
                )
            ),

        "startedAt":
            date_to_json(
                generation.get(
                    "started_at"
                )
            ),

        "finishedAt":
            date_to_json(
                generation.get(
                    "finished_at"
                )
            ),

        "confirmedAt":
            date_to_json(
                generation.get(
                    "confirmed_at"
                )
            ),
    }


# ============================================================
# SÉRIALISATION DES ÉVÉNEMENTS
# ============================================================

def generation_event_to_json(
    event:
        dict[str, Any],
) -> dict[str, Any]:
    return {
        "id":
            event["id"],

        "generationRunId":
            event[
                "generation_run_id"
            ],

        "level":
            event["level"],

        "step":
            event["step"],

        "message":
            event["message"],

        "details":
            event.get(
                "details"
            )
            or {},

        "createdAt":
            date_to_json(
                event.get(
                    "created_at"
                )
            ),
    }


# ============================================================
# SÉRIALISATION DES ARTEFACTS
# ============================================================

def artifact_to_json(
    artifact:
        dict[str, Any],

    *,
    include_content: bool,
) -> dict[str, Any]:
    result:dict[str, Any] = {
            "id":
                artifact["id"],

            "generationRunId":
                artifact[
                    "generation_run_id"
                ],

            "projectId":
                artifact[
                    "project_id"
                ],

            "componentId":
                artifact.get(
                    "component_id"
                ),

            "componentName":
                artifact.get(
                    "component_name"
                ),

            "componentRootPath":
                artifact.get(
                    (
                        "component_"
                        "root_path"
                    )
                ),

            "artifactType":
                artifact[
                    "artifact_type"
                ],

            "relativePath":
                artifact[
                    "relative_path"
                ],

            "contentSha256":
                artifact[
                    "content_sha256"
                ],

            "artifactStatus":
                artifact[
                    "artifact_status"
                ],

            "reviewStatus":
                artifact[
                    "review_status"
                ],

            "validationStatus":
                artifact[
                    "validation_status"
                ],

            "validationMessages":
                artifact.get(
                    (
                        "validation_"
                        "messages"
                    )
                )
                or [],

            "reviewComment":
                artifact.get(
                    "review_comment"
                ),

            "reviewedBy":
                artifact.get(
                    "reviewed_by"
                ),

            "reviewedAt":
                date_to_json(
                    artifact.get(
                        "reviewed_at"
                    )
                ),

            "editedBy":
                artifact.get(
                    "edited_by"
                ),

            "editedAt":
                date_to_json(
                    artifact.get(
                        "edited_at"
                    )
                ),

            "metadata":
                artifact.get(
                    "metadata"
                )
                or {},

            "createdAt":
                date_to_json(
                    artifact.get(
                        "created_at"
                    )
                ),

            "updatedAt":
                date_to_json(
                    artifact.get(
                        "updated_at"
                    )
                ),
        }


    if include_content:
        result["content"] = (
            artifact["content"]
        )

        result[
            "originalContent"
        ] = artifact.get(
            "original_content"
        )


    return result


# ============================================================
# VALIDATION D’UN ARTEFACT MODIFIÉ
#
# Cette fonction accepte les deux signatures rencontrées
# durant la refonte :
#
# 1. validate_artifact_content() retourne :
#    (status, messages)
#
# 2. validate_artifact_content() retourne :
#    messages
# ============================================================

def validate_edited_artifact(
    *,
    artifact_type: str,
    relative_path: str,
    content: str,
) -> tuple[
    str,
    list[dict[str, Any]],
]:
    validation_result = (
        validate_artifact_content(
            artifact_type=
                artifact_type,

            relative_path=
                relative_path,

            content=
                content,
        )
    )


    if isinstance(
        validation_result,
        tuple,
    ):
        (
            validation_status,
            validation_messages,
        ) = validation_result


        return (
            str(
                validation_status
            ),

            list(
                validation_messages
            ),
        )


    validation_messages = list(
        validation_result
    )


    severities = {
        str(
            message.get(
                "severity"
            )

            or message.get(
                "level"
            )

            or ""
        )

        for message
        in validation_messages

        if isinstance(
            message,
            dict,
        )
    }


    if "error" in severities:
        validation_status = (
            "failed"
        )


    elif "warning" in severities:
        validation_status = (
            "warning"
        )


    else:
        validation_status = (
            "passed"
        )


    return (
        validation_status,
        validation_messages,
    )


# ============================================================
# OVERVIEW DU WORKFLOW
# ============================================================

@workflow_blueprint.get(
    "/<int:project_id>/workflow"
)
@require_auth
def workflow_overview_route(
    project_id: int,
):
    context = (
        find_project_workflow_context(
            project_id
        )
    )


    if context is None:
        return error_response(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )


    analysis_run_id = (
        context.get(
            "confirmed_analysis_run_id"
        )
    )


    components = (
        list_analysis_components(
            int(
                analysis_run_id
            )
        )

        if analysis_run_id

        else []
    )


    environments = (
        list_contract_environments()
    )


    ai_connections = (
        list_ai_connections()
    )


    latest_contract = (
        find_latest_contract(
            project_id
        )
    )


    latest_generation = (
        find_latest_workflow_generation(
            project_id
        )
    )


    return jsonify(
        {
            "success": True,

            "data": {
                "canWrite":
                    current_user_can_write(),


                "project": {
                    "id":
                        context["id"],

                    "name":
                        context["name"],

                    "slug":
                        context["slug"],

                    "description":
                        context.get(
                            "description"
                        ),

                    "status":
                        context["status"],

                    "analysisStatus":
                        context.get(
                            "analysis_status"
                        ),

                    "generationStatus":
                        context.get(
                            "generation_status"
                        ),

                    "deploymentContractStatus":
                        context.get(
                            (
                                "deployment_"
                                "contract_status"
                            )
                        ),
                },


                "analysis": (
                    {
                        "id":
                            analysis_run_id,

                        "version":
                            context.get(
                                (
                                    "analyzed_"
                                    "commit_sha"
                                )
                            ),

                        "selectedSubdirectory":
                            context.get(
                                (
                                    "selected_"
                                    "subdirectory"
                                )
                            ),

                        "summary":
                            context.get(
                                "analysis_summary"
                            )
                            or {},

                        "confirmedAt":
                            date_to_json(
                                context.get(
                                    (
                                        "analysis_"
                                        "confirmed_at"
                                    )
                                )
                            ),
                    }

                    if analysis_run_id

                    else None
                ),


                "components": [
                    {
                        "id":
                            component["id"],

                        "name":
                            component["name"],

                        "componentType":
                            component[
                                "component_type"
                            ],

                        "rootPath":
                            component[
                                "root_path"
                            ],

                        "runtime":
                            component.get(
                                "runtime"
                            ),

                        "framework":
                            component.get(
                                "framework"
                            ),

                        "packageManager":
                            component.get(
                                "package_manager"
                            ),

                        "buildCommand":
                            component.get(
                                "build_command"
                            ),

                        "startCommand":
                            component.get(
                                "start_command"
                            ),

                        "detectedPort":
                            component.get(
                                "detected_port"
                            ),

                        "deployable":
                            component[
                                "deployable"
                            ],

                        "dockerfilePath":
                            component.get(
                                "dockerfile_path"
                            ),

                        "helmChartPath":
                            component.get(
                                "helm_chart_path"
                            ),

                        "kubernetesPaths":
                            component.get(
                                "kubernetes_paths"
                            )
                            or [],

                        "environmentVariables":
                            component.get(
                                (
                                    "environment_"
                                    "variables"
                                )
                            )
                            or [],

                        "confidence":
                            component.get(
                                "confidence"
                            ),

                        "configuration":
                            component.get(
                                "configuration"
                            )
                            or {},

                        "userModified":
                            bool(
                                component.get(
                                    "user_modified"
                                )
                            ),
                    }

                    for component
                    in components
                ],


                "environments": [
                    environment_to_json(
                        environment
                    )

                    for environment
                    in environments
                ],


                "latestContract":
                    contract_to_json(
                        latest_contract
                    ),


                "aiConnections": [
                    ai_connection_to_json(
                        connection
                    )

                    for connection
                    in ai_connections
                ],


                "latestGeneration":
                    generation_to_json(
                        latest_generation
                    ),
            },
        }
    )


# ============================================================
# PHASE 2 — PRÉVISUALISER UN CONTRAT
# ============================================================

@workflow_blueprint.post(
    (
        "/<int:project_id>/"
        "deployment-contracts/preview"
    )
)
@require_auth
def preview_contract_route(
    project_id: int,
):
    payload = request.get_json(
        silent=True
    )


    if not isinstance(
        payload,
        dict,
    ):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )


    try:
        environment_id = int(
            payload.get(
                "environmentId"
            )
        )

    except (
        TypeError,
        ValueError,
    ):
        return error_response(
            "ENVIRONMENT_REQUIRED",
            (
                "Sélectionnez "
                "un environnement."
            ),
            400,
        )


    context = (
        find_project_workflow_context(
            project_id
        )
    )


    if context is None:
        return error_response(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )


    analysis_run_id = (
        context.get(
            "confirmed_analysis_run_id"
        )
    )


    if not analysis_run_id:
        return error_response(
            (
                "CONFIRMED_ANALYSIS_"
                "REQUIRED"
            ),
            (
                "Confirmez d'abord "
                "l'analyse de la phase 2."
            ),
            409,
        )


    environment = (
        find_contract_environment(
            environment_id
        )
    )


    if environment is None:
        return error_response(
            "ENVIRONMENT_NOT_FOUND",
            (
                "L'environnement "
                "est introuvable."
            ),
            404,
        )


    components = (
        list_analysis_components(
            int(
                analysis_run_id
            )
        )
    )


    try:
        default_contract = (
            build_default_contract(
                project=
                    context,

                components=
                    components,

                environment=
                    environment,
            )
        )


        validation = (
            validate_contract(
                raw_contract=
                    default_contract,

                project=
                    context,

                components=
                    components,

                environment=
                    environment,
            )
        )


    except (
        ContractValidationError,
        ValueError,
    ) as error:
        return error_response(
            (
                "CONTRACT_PREVIEW_"
                "FAILED"
            ),
            str(error),
            400,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "contract":
                    validation
                    .normalized_contract,

                "validation":
                    validation.report,
            },
        }
    )


# ============================================================
# PHASE 2 — ENREGISTRER LE CONTRAT
# ============================================================

@workflow_blueprint.put(
    (
        "/<int:project_id>/"
        "deployment-contracts"
    )
)
@require_auth
def save_contract_route(
    project_id: int,
):
    try:
        ensure_write_permission()


    except PermissionError as error:
        return error_response(
            "WORKFLOW_FORBIDDEN",
            str(error),
            403,
        )


    payload = request.get_json(
        silent=True
    )


    if not isinstance(
        payload,
        dict,
    ):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )


    raw_contract = payload.get(
        "contract"
    )


    if not isinstance(
        raw_contract,
        dict,
    ):
        return error_response(
            "CONTRACT_REQUIRED",
            (
                "Le contrat JSON "
                "est obligatoire."
            ),
            400,
        )


    raw_target = raw_contract.get(
        "target"
    )


    target = (
        raw_target

        if isinstance(
            raw_target,
            dict,
        )

        else {}
    )


    try:
        environment_id = int(
            payload.get(
                "environmentId"
            )

            or target.get(
                "environmentId"
            )
        )


    except (
        TypeError,
        ValueError,
    ):
        return error_response(
            "ENVIRONMENT_REQUIRED",
            (
                "Sélectionnez "
                "un environnement."
            ),
            400,
        )


    context = (
        find_project_workflow_context(
            project_id
        )
    )


    if context is None:
        return error_response(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )


    analysis_run_id = (
        context.get(
            "confirmed_analysis_run_id"
        )
    )


    if not analysis_run_id:
        return error_response(
            (
                "CONFIRMED_ANALYSIS_"
                "REQUIRED"
            ),
            (
                "Confirmez d'abord "
                "l'analyse."
            ),
            409,
        )


    environment = (
        find_contract_environment(
            environment_id
        )
    )


    if environment is None:
        return error_response(
            "ENVIRONMENT_NOT_FOUND",
            (
                "L'environnement "
                "est introuvable."
            ),
            404,
        )


    components = (
        list_analysis_components(
            int(
                analysis_run_id
            )
        )
    )


    try:
        validation = (
            validate_contract(
                raw_contract=
                    raw_contract,

                project=
                    context,

                components=
                    components,

                environment=
                    environment,
            )
        )


        normalized_contract = (
            validation
            .normalized_contract
        )


        saved_contract = (
            save_contract(
                project_id=
                    project_id,

                analysis_run_id=
                    int(
                        analysis_run_id
                    ),

                environment_id=
                    environment_id,

                namespace=
                    str(
                        normalized_contract[
                            "target"
                        ][
                            "namespace"
                        ]
                    ),

                domain=(
                    normalized_contract[
                        "target"
                    ].get(
                        "domain"
                    )
                ),

                contract=
                    normalized_contract,

                validation=
                    validation.report,

                user_id=
                    current_user_id(),
            )
        )


    except (
        ContractValidationError,
        ValueError,
    ) as error:
        return error_response(
            "CONTRACT_INVALID",
            str(error),
            400,
        )


    except Exception:
        current_app.logger.exception(
            (
                "Échec de l'enregistrement "
                "du contrat du projet %s."
            ),
            project_id,
        )


        return error_response(
            "CONTRACT_SAVE_FAILED",
            (
                "Le contrat n'a pas pu "
                "être enregistré."
            ),
            500,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "contract":
                    contract_to_json(
                        saved_contract
                    ),
            },
        }
    )


# ============================================================
# PHASE 2 — CONFIRMER LE CONTRAT
# ============================================================

@workflow_blueprint.post(
    (
        "/<int:project_id>/"
        "deployment-contracts/"
        "<int:contract_id>/confirm"
    )
)
@require_auth
def confirm_contract_route(
    project_id: int,
    contract_id: int,
):
    try:
        ensure_write_permission()


        confirmed_contract = (
            confirm_contract(
                project_id=
                    project_id,

                contract_id=
                    contract_id,

                user_id=
                    current_user_id(),
            )
        )


    except PermissionError as error:
        return error_response(
            "WORKFLOW_FORBIDDEN",
            str(error),
            403,
        )


    except ValueError as error:
        return error_response(
            (
                "CONTRACT_NOT_"
                "CONFIRMABLE"
            ),
            str(error),
            409,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "contract":
                    contract_to_json(
                        confirmed_contract
                    ),
            },
        }
    )


# ============================================================
# PHASE 3 — LISTER LES CONNEXIONS IA
# ============================================================

@workflow_blueprint.get(
    (
        "/<int:project_id>/"
        "workflow/ai-connections"
    )
)
@require_auth
def ai_connections_route(
    project_id: int,
):
    if (
        find_project_workflow_context(
            project_id
        )
        is None
    ):
        return error_response(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )


    connections = (
        list_ai_connections()
    )


    return jsonify(
        {
            "success": True,

            "data": {
                "connections": [
                    ai_connection_to_json(
                        connection
                    )

                    for connection
                    in connections
                ],
            },
        }
    )


# ============================================================
# PHASE 3 — LISTER LES MODÈLES D’UNE CONNEXION
# ============================================================

@workflow_blueprint.get(
    (
        "/<int:project_id>/"
        "workflow/ai-connections/"
        "<int:connection_id>/models"
    )
)
@require_auth
def ai_models_route(
    project_id: int,
    connection_id: int,
):
    if (
        find_project_workflow_context(
            project_id
        )
        is None
    ):
        return error_response(
            "PROJECT_NOT_FOUND",
            "Le projet est introuvable.",
            404,
        )


    try:
        models = (
            list_models_for_connection(
                connection_id
            )
        )


    except AiProviderError as error:
        return error_response(
            error.code,
            str(error),
            502,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "models":
                    models,
            },
        }
    )


# ============================================================
# PHASE 3 — CRÉER UNE GÉNÉRATION
# ============================================================

@workflow_blueprint.post(
    (
        "/<int:project_id>/"
        "workflow/generations"
    )
)
@require_auth
def create_generation_route(
    project_id: int,
):
    try:
        ensure_write_permission()


    except PermissionError as error:
        return error_response(
            "WORKFLOW_FORBIDDEN",
            str(error),
            403,
        )


    payload = request.get_json(
        silent=True
    )


    if not isinstance(
        payload,
        dict,
    ):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )


    try:
        contract_id = int(
            payload.get(
                "contractId"
            )
        )


    except (
        TypeError,
        ValueError,
    ):
        return error_response(
            "CONTRACT_REQUIRED",
            (
                "Le contrat confirmé "
                "est obligatoire."
            ),
            400,
        )


    generation_mode = str(
        payload.get(
            "generationMode"
        )
        or "hybrid"
    ).strip().lower()


    ai_connection_id:int | None = None


    ai_model:str | None = None


    if generation_mode == "hybrid":
        try:
            ai_connection_id = int(
                payload.get(
                    "aiConnectionId"
                )
            )


        except (
            TypeError,
            ValueError,
        ):
            return error_response(
                (
                    "AI_CONNECTION_"
                    "REQUIRED"
                ),
                (
                    "Sélectionnez "
                    "une connexion IA."
                ),
                400,
            )


        ai_model = str(
            payload.get(
                "aiModel"
            )
            or ""
        ).strip()


        if (
            not ai_model

            or len(
                ai_model
            )
            > MAX_AI_MODEL_LENGTH
        ):
            return error_response(
                "AI_MODEL_REQUIRED",
                (
                    "Le modèle IA est "
                    "obligatoire et limité "
                    "à 255 caractères."
                ),
                400,
            )


        connection = (
            find_ai_connection(
                ai_connection_id
            )
        )


        if connection is None:
            return error_response(
                (
                    "AI_CONNECTION_"
                    "NOT_FOUND"
                ),
                (
                    "La connexion IA "
                    "est introuvable."
                ),
                404,
            )


        if connection["status"] in {
            "offline",
            "not_configured",
        }:
            return error_response(
                (
                    "AI_CONNECTION_"
                    "UNAVAILABLE"
                ),
                (
                    "La connexion IA n'est "
                    "pas utilisable. Testez-la "
                    "dans Intégrations."
                ),
                409,
            )


    elif (
        generation_mode
        == "deterministic"
    ):
        ai_connection_id = None
        ai_model = None


    else:
        return error_response(
            (
                "GENERATION_MODE_"
                "INVALID"
            ),
            (
                "Le mode doit être hybrid "
                "ou deterministic."
            ),
            400,
        )


    contract = (
        find_contract_for_project(
            project_id=
                project_id,

            contract_id=
                contract_id,
        )
    )


    if contract is None:
        return error_response(
            "CONTRACT_NOT_FOUND",
            "Le contrat est introuvable.",
            404,
        )


    try:
        generation = (
            create_workflow_generation(
                project_id=
                    project_id,

                contract_id=
                    contract_id,

                generation_mode=
                    generation_mode,

                ai_connection_id=
                    ai_connection_id,

                ai_model=
                    ai_model,

                created_by=
                    current_user_id(),
            )
        )


    except ValueError as error:
        return error_response(
            (
                "GENERATION_NOT_"
                "CREATED"
            ),
            str(error),
            409,
        )


    except Exception:
        current_app.logger.exception(
            (
                "Échec de la création "
                "d'une génération pour "
                "le projet %s."
            ),
            project_id,
        )


        return error_response(
            (
                "GENERATION_CREATE_"
                "FAILED"
            ),
            (
                "La génération n'a pas "
                "pu être créée."
            ),
            500,
        )


    return (
        jsonify(
            {
                "success": True,

                "data": {
                    "generation":
                        generation_to_json(
                            generation
                        ),

                    "workerRequired":
                        True,
                },
            }
        ),

        202,
    )


# ============================================================
# PHASE 3/4 — DERNIÈRE GÉNÉRATION
# ============================================================

@workflow_blueprint.get(
    (
        "/<int:project_id>/"
        "workflow/generations/latest"
    )
)
@require_auth
def latest_generation_route(
    project_id: int,
):
    generation = (
        find_latest_workflow_generation(
            project_id
        )
    )


    if generation is None:
        return error_response(
            (
                "GENERATION_NOT_"
                "FOUND"
            ),
            (
                "Aucune génération workflow "
                "n'existe pour ce projet."
            ),
            404,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "generation":
                    generation_to_json(
                        generation
                    ),
            },
        }
    )


# ============================================================
# PHASE 3/4 — DÉTAIL D’UNE GÉNÉRATION
# ============================================================

@workflow_blueprint.get(
    (
        "/<int:project_id>/"
        "workflow/generations/"
        "<int:generation_run_id>"
    )
)
@require_auth
def generation_detail_route(
    project_id: int,
    generation_run_id: int,
):
    generation = (
        find_generation_for_project(
            project_id=
                project_id,

            generation_run_id=
                generation_run_id,
        )
    )


    if generation is None:
        return error_response(
            "GENERATION_NOT_FOUND",
            (
                "La génération "
                "est introuvable."
            ),
            404,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "generation":
                    generation_to_json(
                        generation
                    ),
            },
        }
    )


# ============================================================
# PHASE 3 — ÉVÉNEMENTS / LOGS
#
# Le frontend appelle régulièrement cette route avec afterId.
# Seuls les nouveaux événements sont renvoyés.
# ============================================================

@workflow_blueprint.get(
    (
        "/<int:project_id>/"
        "workflow/generations/"
        "<int:generation_run_id>/events"
    )
)
@require_auth
def generation_events_route(
    project_id: int,
    generation_run_id: int,
):
    generation = (
        find_generation_for_project(
            project_id=
                project_id,

            generation_run_id=
                generation_run_id,
        )
    )


    if generation is None:
        return error_response(
            "GENERATION_NOT_FOUND",
            (
                "La génération "
                "est introuvable."
            ),
            404,
        )


    try:
        after_id = max(
            0,

            int(
                request.args.get(
                    "afterId",
                    "0",
                )
            ),
        )


    except ValueError:
        return error_response(
            "AFTER_ID_INVALID",
            "afterId est invalide.",
            400,
        )


    events = (
        list_generation_events(
            generation_run_id=
                generation_run_id,

            after_id=
                after_id,
        )
    )


    response = jsonify(
        {
            "success": True,

            "data": {
                "events": [
                    generation_event_to_json(
                        event
                    )

                    for event
                    in events
                ],

                "lastEventId": (
                    events[-1]["id"]

                    if events

                    else after_id
                ),

                "generation":
                    generation_to_json(
                        generation
                    ),
            },
        }
    )


    response.headers[
        "Cache-Control"
    ] = "no-store"


    return response


# ============================================================
# PHASE 4 — LISTER LES ARTEFACTS
# ============================================================

@workflow_blueprint.get(
    (
        "/<int:project_id>/"
        "workflow/generations/"
        "<int:generation_run_id>/artifacts"
    )
)
@require_auth
def generation_artifacts_route(
    project_id: int,
    generation_run_id: int,
):
    generation = (
        find_generation_for_project(
            project_id=
                project_id,

            generation_run_id=
                generation_run_id,
        )
    )


    if generation is None:
        return error_response(
            "GENERATION_NOT_FOUND",
            (
                "La génération "
                "est introuvable."
            ),
            404,
        )


    artifacts = (
        list_generation_artifacts(
            generation_run_id
        )
    )


    return jsonify(
        {
            "success": True,

            "data": {
                "artifacts": [
                    artifact_to_json(
                        artifact,

                        include_content=
                            False,
                    )

                    for artifact
                    in artifacts
                ],
            },
        }
    )


# ============================================================
# PHASE 4 — LIRE UN ARTEFACT
# ============================================================

@workflow_blueprint.get(
    (
        "/<int:project_id>/"
        "workflow/generations/"
        "<int:generation_run_id>/"
        "artifacts/<int:artifact_id>"
    )
)
@require_auth
def artifact_detail_route(
    project_id: int,
    generation_run_id: int,
    artifact_id: int,
):
    generation = (
        find_generation_for_project(
            project_id=
                project_id,

            generation_run_id=
                generation_run_id,
        )
    )


    if generation is None:
        return error_response(
            "GENERATION_NOT_FOUND",
            (
                "La génération "
                "est introuvable."
            ),
            404,
        )


    artifact = (
        find_generation_artifact(
            generation_run_id=
                generation_run_id,

            artifact_id=
                artifact_id,
        )
    )


    if artifact is None:
        return error_response(
            "ARTIFACT_NOT_FOUND",
            (
                "L'artefact "
                "est introuvable."
            ),
            404,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "artifact":
                    artifact_to_json(
                        artifact,

                        include_content=
                            True,
                    ),
            },
        }
    )


# ============================================================
# PHASE 4 — MODIFIER UN ARTEFACT
# ============================================================

@workflow_blueprint.put(
    (
        "/<int:project_id>/"
        "workflow/generations/"
        "<int:generation_run_id>/"
        "artifacts/<int:artifact_id>"
    )
)
@require_auth
def update_artifact_route(
    project_id: int,
    generation_run_id: int,
    artifact_id: int,
):
    try:
        ensure_write_permission()


    except PermissionError as error:
        return error_response(
            "WORKFLOW_FORBIDDEN",
            str(error),
            403,
        )


    generation = (
        find_generation_for_project(
            project_id=
                project_id,

            generation_run_id=
                generation_run_id,
        )
    )


    if generation is None:
        return error_response(
            "GENERATION_NOT_FOUND",
            (
                "La génération "
                "est introuvable."
            ),
            404,
        )


    if (
        generation["status"]
        != "awaiting_review"
    ):
        return error_response(
            (
                "GENERATION_NOT_"
                "EDITABLE"
            ),
            (
                "La génération n'est pas "
                "en phase de revue."
            ),
            409,
        )


    artifact = (
        find_generation_artifact(
            generation_run_id=
                generation_run_id,

            artifact_id=
                artifact_id,
        )
    )


    if artifact is None:
        return error_response(
            "ARTIFACT_NOT_FOUND",
            (
                "L'artefact "
                "est introuvable."
            ),
            404,
        )


    payload = request.get_json(
        silent=True
    )


    if (
        not isinstance(
            payload,
            dict,
        )

        or not isinstance(
            payload.get(
                "content"
            ),
            str,
        )
    ):
        return error_response(
            "CONTENT_REQUIRED",
            (
                "Le contenu texte "
                "est obligatoire."
            ),
            400,
        )


    content = payload["content"]


    if (
        len(
            content.encode(
                "utf-8"
            )
        )
        > MAX_ARTIFACT_CONTENT_BYTES
    ):
        return error_response(
            "CONTENT_TOO_LARGE",
            (
                "Le contenu dépasse "
                "1 Mo."
            ),
            413,
        )


    (
        validation_status,
        validation_messages,
    ) = validate_edited_artifact(
        artifact_type=
            str(
                artifact[
                    "artifact_type"
                ]
            ),

        relative_path=
            str(
                artifact[
                    "relative_path"
                ]
            ),

        content=
            content,
    )


    try:
        updated_artifact = (
            update_artifact_content(
                artifact_id=
                    artifact_id,

                generation_run_id=
                    generation_run_id,

                content=
                    content,

                validation_status=
                    validation_status,

                validation_messages=
                    validation_messages,

                user_id=
                    current_user_id(),
            )
        )


    except ValueError as error:
        return error_response(
            (
                "ARTIFACT_NOT_"
                "UPDATED"
            ),
            str(error),
            404,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "artifact":
                    artifact_to_json(
                        updated_artifact,

                        include_content=
                            True,
                    ),
            },
        }
    )


# ============================================================
# PHASE 4 — APPROUVER OU REJETER UN ARTEFACT
# ============================================================

@workflow_blueprint.post(
    (
        "/<int:project_id>/"
        "workflow/generations/"
        "<int:generation_run_id>/"
        "artifacts/<int:artifact_id>/review"
    )
)
@require_auth
def review_artifact_route(
    project_id: int,
    generation_run_id: int,
    artifact_id: int,
):
    try:
        ensure_write_permission()


    except PermissionError as error:
        return error_response(
            "WORKFLOW_FORBIDDEN",
            str(error),
            403,
        )


    generation = (
        find_generation_for_project(
            project_id=
                project_id,

            generation_run_id=
                generation_run_id,
        )
    )


    if generation is None:
        return error_response(
            "GENERATION_NOT_FOUND",
            (
                "La génération "
                "est introuvable."
            ),
            404,
        )


    if (
        generation["status"]
        != "awaiting_review"
    ):
        return error_response(
            (
                "GENERATION_NOT_"
                "REVIEWABLE"
            ),
            (
                "La génération n'est pas "
                "en phase de revue."
            ),
            409,
        )


    payload = request.get_json(
        silent=True
    )


    if not isinstance(
        payload,
        dict,
    ):
        return error_response(
            "INVALID_JSON",
            "Le corps JSON est invalide.",
            400,
        )


    decision = str(
        payload.get(
            "decision"
        )
        or ""
    ).strip().lower()


    comment = (
        str(
            payload.get(
                "comment"
            )
            or ""
        ).strip()

        or None
    )


    if decision not in {
        "approved",
        "rejected",
    }:
        return error_response(
            (
                "REVIEW_DECISION_"
                "INVALID"
            ),
            (
                "La décision doit être "
                "approved ou rejected."
            ),
            400,
        )


    if (
        comment

        and len(comment)
        > MAX_REVIEW_COMMENT_LENGTH
    ):
        return error_response(
            "COMMENT_TOO_LONG",
            (
                "Le commentaire ne peut pas "
                "dépasser 2000 caractères."
            ),
            400,
        )


    try:
        reviewed_artifact = (
            review_artifact(
                artifact_id=
                    artifact_id,

                generation_run_id=
                    generation_run_id,

                decision=
                    decision,

                comment=
                    comment,

                user_id=
                    current_user_id(),
            )
        )


    except ValueError as error:
        return error_response(
            (
                "ARTIFACT_NOT_"
                "REVIEWED"
            ),
            str(error),
            409,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "artifact":
                    artifact_to_json(
                        reviewed_artifact,

                        include_content=
                            True,
                    ),
            },
        }
    )


# ============================================================
# PHASE 4 — CONFIRMATION FINALE
# ============================================================

@workflow_blueprint.post(
    (
        "/<int:project_id>/"
        "workflow/generations/"
        "<int:generation_run_id>/confirm"
    )
)
@require_auth
def confirm_generation_route(
    project_id: int,
    generation_run_id: int,
):
    try:
        ensure_write_permission()


        confirmed_generation = (
            confirm_generation_review(
                project_id=
                    project_id,

                generation_run_id=
                    generation_run_id,

                user_id=
                    current_user_id(),
            )
        )


    except PermissionError as error:
        return error_response(
            "WORKFLOW_FORBIDDEN",
            str(error),
            403,
        )


    except ValueError as error:
        return error_response(
            (
                "GENERATION_NOT_"
                "CONFIRMABLE"
            ),
            str(error),
            409,
        )


    return jsonify(
        {
            "success": True,

            "data": {
                "generation":
                    generation_to_json(
                        confirmed_generation
                    ),
            },
        }
    )