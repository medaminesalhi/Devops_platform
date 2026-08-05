from __future__ import annotations

import copy
import re

from dataclasses import dataclass
from typing import Any


DNS_LABEL_PATTERN = re.compile(
    r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$"
)

DNS_HOST_PATTERN = re.compile(
    r"^(?:\*\.)?"
    r"[a-zA-Z0-9]"
    r"(?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\."
    r"[a-zA-Z0-9]"
    r"(?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r")*$"
)

ENV_NAME_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)

RESOURCE_QUANTITY_PATTERN = re.compile(
    r"^[0-9]+"
    r"(?:\.[0-9]+)?"
    r"(?:m|Ki|Mi|Gi|Ti|Pi|Ei)?$"
)

SENSITIVE_NAME_PATTERN = re.compile(
    (
        r"(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|"
        r"PRIVATE_KEY|DATABASE_URL|DSN|CREDENTIAL)"
    ),
    re.IGNORECASE,
)


DEFAULT_CPU_REQUEST = "100m"
DEFAULT_CPU_LIMIT = "500m"

DEFAULT_MEMORY_REQUEST = "128Mi"
DEFAULT_MEMORY_LIMIT = "512Mi"


@dataclass
class ContractValidationResult:
    normalized_contract: dict[str, Any]
    report: dict[str, Any]

    @property
    def is_valid(self) -> bool:
        return bool(
            self.report.get("valid")
        )


class ContractValidationError(
    ValueError
):
    pass


def sanitize_slug(
    value: str,
    fallback: str = "application",
) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        value.lower().strip(),
    ).strip("-")

    return normalized[:63] or fallback


def build_default_contract(
    *,
    project: dict[str, Any],
    components: list[dict[str, Any]],
    environment: dict[str, Any],
) -> dict[str, Any]:
    project_slug = sanitize_slug(
        str(
            project.get("slug")
            or project.get("name")
            or "project"
        )
    )

    environment_code = sanitize_slug(
        str(
            environment.get("code")
            or environment.get("name")
            or "environment"
        )
    )

    namespace = str(
        environment.get("namespace")
        or (
            f"{project_slug}-"
            f"{environment_code}"
        )
    ).strip()

    domain = _optional_string(
        environment.get("domain")
    )

    services = _service_index(
        environment.get("services")
        or []
    )

    kubernetes_service = (
        services.get("kubernetes")
        or {}
    )

    argocd_service = (
        services.get("argocd")
        or {}
    )

    contract_components = [
        _default_component_contract(
            component=component,
            project_slug=project_slug,
            environment_code=
                environment_code,
            base_domain=domain,
        )
        for component in components
    ]

    argocd_project_name = (
        sanitize_slug(
            (
                f"sapixi-"
                f"{project_slug}-"
                f"{environment_code}"
            )
        )
    )

    return {
        "schemaVersion": 1,

        "project": {
            "id":
                int(project["id"]),

            "name":
                str(
                    project.get("name")
                    or project_slug
                ),

            "slug":
                project_slug,

            "analysisRunId":
                int(
                    project[
                        "confirmed_analysis_run_id"
                    ]
                ),

            "commitSha":
                str(
                    project.get(
                        "analyzed_commit_sha"
                    )
                    or ""
                ),
        },

        "target": {
            "environmentId":
                int(environment["id"]),

            "environmentName":
                str(
                    environment.get("name")
                    or environment_code
                ),

            "environmentCode":
                environment_code,

            "namespace":
                namespace,

            "domain":
                domain,

            "kubernetes": {
                "server": str(
                    kubernetes_service.get(
                        "baseUrl"
                    )
                    or (
                        "https://"
                        "kubernetes.default.svc"
                    )
                ),
            },

            "registry": {
                "host": "",

                "repositoryPrefix":
                    project_slug,

                "imagePullSecretName":
                    "",
            },

            "gitops": {
                "repositoryUrl":
                    "",

                "targetRevision":
                    "main",

                "basePath":
                    "projects",
            },

            "argocd": {
                "serverUrl":
                    str(
                        argocd_service.get(
                            "baseUrl"
                        )
                        or ""
                    ),

                "namespace":
                    "argocd",

                "projectName":
                    argocd_project_name,

                "automaticSync":
                    False,

                "prune":
                    False,

                "selfHeal":
                    False,
            },
        },

        "policies": {
            "preserveExistingDockerfile":
                True,

            "preserveExistingHelmChart":
                True,

            "requireNonRoot":
                True,

            "allowPrivileged":
                False,

            "requireManualArgoSync":
                True,

            "maximumAiContextBytes":
                200_000,
        },

        "components":
            contract_components,
    }


def validate_contract(
    *,
    raw_contract: dict[str, Any],
    project: dict[str, Any],
    components: list[dict[str, Any]],
    environment: dict[str, Any],
) -> ContractValidationResult:
    if not isinstance(
        raw_contract,
        dict,
    ):
        raise ContractValidationError(
            (
                "Le contrat de déploiement "
                "doit être un objet JSON."
            )
        )

    contract = copy.deepcopy(
        raw_contract
    )

    errors: list[
        dict[str, str]
    ] = []

    warnings: list[
        dict[str, str]
    ] = []

    questions: list[
        dict[str, str]
    ] = []

    project_section = _dict_value(
        contract,
        "project",
    )

    target_section = _dict_value(
        contract,
        "target",
    )

    policies_section = _dict_value(
        contract,
        "policies",
    )

    raw_components = contract.get(
        "components"
    )

    if not isinstance(
        raw_components,
        list,
    ):
        raw_components = []

        errors.append(
            _issue(
                "components",
                "COMPONENTS_REQUIRED",
                (
                    "Le contrat doit contenir "
                    "une liste de composants."
                ),
            )
        )

    project_section["id"] = int(
        project["id"]
    )

    project_section[
        "analysisRunId"
    ] = int(
        project[
            "confirmed_analysis_run_id"
        ]
    )

    project_section["name"] = str(
        project.get("name")
        or ""
    )

    project_section["slug"] = (
        sanitize_slug(
            str(
                project.get("slug")
                or project.get("name")
                or "project"
            )
        )
    )

    project_section["commitSha"] = str(
        project.get(
            "analyzed_commit_sha"
        )
        or ""
    )

    target_section[
        "environmentId"
    ] = int(
        environment["id"]
    )

    target_section[
        "environmentName"
    ] = str(
        environment.get("name")
        or ""
    )

    target_section[
        "environmentCode"
    ] = sanitize_slug(
        str(
            environment.get("code")
            or environment.get("name")
            or "environment"
        )
    )

    namespace = str(
        target_section.get(
            "namespace"
        )
        or ""
    ).strip()

    if not namespace:
        errors.append(
            _issue(
                "target.namespace",
                "NAMESPACE_REQUIRED",
                (
                    "Le namespace Kubernetes "
                    "est obligatoire."
                ),
            )
        )

    elif (
        len(namespace) > 63
        or not DNS_LABEL_PATTERN.fullmatch(
            namespace
        )
    ):
        errors.append(
            _issue(
                "target.namespace",
                "NAMESPACE_INVALID",
                (
                    "Le namespace doit être "
                    "un label DNS Kubernetes "
                    "valide de 63 caractères "
                    "maximum."
                ),
            )
        )

    target_section[
        "namespace"
    ] = namespace

    domain = _optional_string(
        target_section.get(
            "domain"
        )
    )

    if (
        domain
        and (
            len(domain) > 255
            or not DNS_HOST_PATTERN.fullmatch(
                domain
            )
        )
    ):
        errors.append(
            _issue(
                "target.domain",
                "DOMAIN_INVALID",
                (
                    "Le domaine applicatif "
                    "est invalide."
                ),
            )
        )

    target_section[
        "domain"
    ] = domain

    _validate_target_configuration(
        target_section=
            target_section,

        errors=
            errors,

        warnings=
            warnings,

        questions=
            questions,
    )

    normalized_components = (
        _validate_components(
            raw_components=
                raw_components,

            detected_components=
                components,

            target=
                target_section,

            errors=
                errors,

            warnings=
                warnings,

            questions=
                questions,
        )
    )

    if not any(
        bool(
            component.get(
                "deployable"
            )
        )
        for component
        in normalized_components
    ):
        errors.append(
            _issue(
                "components",
                (
                    "DEPLOYABLE_"
                    "COMPONENT_REQUIRED"
                ),
                (
                    "Au moins un composant "
                    "doit être marqué comme "
                    "déployable."
                ),
            )
        )

    normalized = {
        "schemaVersion":
            1,

        "project":
            project_section,

        "target":
            target_section,

        "policies":
            _normalize_policies(
                policies_section
            ),

        "components":
            normalized_components,
    }

    report = {
        "valid":
            len(errors) == 0,

        "errorCount":
            len(errors),

        "warningCount":
            len(warnings),

        "questionCount":
            len(questions),

        "errors":
            errors,

        "warnings":
            warnings,

        "questions":
            questions,
    }

    return ContractValidationResult(
        normalized_contract=
            normalized,

        report=
            report,
    )


def build_ai_payload(
    *,
    contract: dict[str, Any],
    analysis_summary:
        dict[str, Any] | None,
    source_files:
        list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "task":
            "generation_plan",

        "contract":
            _sanitize_for_ai(
                contract
            ),

        "analysisSummary":
            _sanitize_for_ai(
                analysis_summary
                or {}
            ),

        "sourceFiles":
            source_files,

        "constraints": {
            "noSecretValues":
                True,

            "noDirectExecution":
                True,

            "noArgoAutoSync":
                bool(
                    contract.get(
                        "policies",
                        {},
                    ).get(
                        (
                            "requireManual"
                            "ArgoSync"
                        ),
                        True,
                    )
                ),

            "structuredJsonOnly":
                True,
        },
    }


def _default_component_contract(
    *,
    component: dict[str, Any],
    project_slug: str,
    environment_code: str,
    base_domain: str | None,
) -> dict[str, Any]:
    del environment_code

    configuration = component.get(
        "configuration"
    )

    if not isinstance(
        configuration,
        dict,
    ):
        configuration = {}

    component_name = str(
        component.get("name")
        or "component"
    )

    component_slug = (
        sanitize_slug(
            component_name
        )
    )

    component_type = str(
        component.get(
            "component_type"
        )
        or "unknown"
    ).lower()

    detected_port = (
        _integer_or_none(
            component.get(
                "detected_port"
            )
        )
        or _default_port(
            component_type
        )
    )

    framework = str(
        component.get("framework")
        or ""
    )

    runtime = str(
        component.get("runtime")
        or ""
    )

    package_manager = str(
        component.get(
            "package_manager"
        )
        or ""
    )

    environment_variables = (
        _normalize_detected_environment_variables(
            component.get(
                "environment_variables"
            )
        )
    )

    public_component = (
        component_type
        in {
            "frontend",
            "fullstack",
        }
    )

    ingress_host = (
        (
            f"{component_slug}-"
            f"{project_slug}."
            f"{base_domain}"
        )
        if (
            public_component
            and base_domain
        )
        else ""
    )

    readiness_path = (
        _suggest_probe_path(
            component_type=
                component_type,

            framework=
                framework,
        )
    )

    build_context = str(
        configuration.get(
            "buildContext"
        )
        or component.get(
            "root_path"
        )
        or "."
    )

    return {
        "id":
            int(component["id"]),

        "name":
            component_name,

        "slug":
            component_slug,

        "rootPath":
            str(
                component.get(
                    "root_path"
                )
                or "."
            ),

        "componentType":
            component_type,

        "runtime": {
            "name":
                runtime,

            "version":
                str(
                    configuration.get(
                        "runtimeVersion"
                    )
                    or ""
                ),
        },

        "framework":
            framework,

        "packageManager":
            package_manager,

        "deployable":
            bool(
                component.get(
                    "deployable",
                    True,
                )
            ),

        "build": {
            "context":
                build_context,

            "dockerfilePath":
                str(
                    component.get(
                        "dockerfile_path"
                    )
                    or ""
                ),

            "helmChartPath":
                str(
                    component.get(
                        "helm_chart_path"
                    )
                    or ""
                ),

            "installCommand":
                str(
                    configuration.get(
                        "installCommand"
                    )
                    or ""
                ),

            "buildCommand":
                str(
                    component.get(
                        "build_command"
                    )
                    or configuration.get(
                        "buildCommand"
                    )
                    or ""
                ),

            "outputPath":
                str(
                    configuration.get(
                        "outputPath"
                    )
                    or ""
                ),
        },

        "container": {
            "startCommand":
                str(
                    component.get(
                        "start_command"
                    )
                    or configuration.get(
                        "startCommand"
                    )
                    or ""
                ),

            "port":
                detected_port,

            "workingDirectory":
                "/app",

            "runAsUser":
                10001,

            "readOnlyRootFilesystem":
                False,
        },

        "replicas":
            1,

        "service": {
            "enabled":
                True,

            "type":
                "ClusterIP",

            "port":
                detected_port,

            "targetPort":
                detected_port,
        },

        "ingress": {
            "enabled":
                bool(ingress_host),

            "className":
                "nginx",

            "host":
                ingress_host,

            "path":
                "/",

            "pathType":
                "Prefix",

            "tlsSecretName":
                "",

            "annotations":
                {},
        },

        "resources": {
            "requests": {
                "cpu":
                    DEFAULT_CPU_REQUEST,

                "memory":
                    DEFAULT_MEMORY_REQUEST,
            },

            "limits": {
                "cpu":
                    DEFAULT_CPU_LIMIT,

                "memory":
                    DEFAULT_MEMORY_LIMIT,
            },
        },

        "probes": {
            "startup": {
                "enabled":
                    False,

                "path":
                    readiness_path,

                "initialDelaySeconds":
                    0,

                "periodSeconds":
                    10,

                "timeoutSeconds":
                    2,

                "failureThreshold":
                    30,
            },

            "readiness": {
                "enabled":
                    bool(
                        readiness_path
                    ),

                "path":
                    readiness_path,

                "initialDelaySeconds":
                    5,

                "periodSeconds":
                    10,

                "timeoutSeconds":
                    2,

                "failureThreshold":
                    3,
            },

            "liveness": {
                "enabled":
                    bool(
                        readiness_path
                    ),

                "path":
                    readiness_path,

                "initialDelaySeconds":
                    15,

                "periodSeconds":
                    20,

                "timeoutSeconds":
                    2,

                "failureThreshold":
                    3,
            },
        },

        "configuration":
            environment_variables[
                "configuration"
            ],

        "secrets":
            environment_variables[
                "secrets"
            ],

        "volumes":
            [],

        "migration": {
            "enabled":
                False,

            "command":
                "",

            "backoffLimit":
                1,
        },

        "dependencies":
            [],
    }


def _validate_target_configuration(
    *,
    target_section: dict[str, Any],
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
    questions: list[dict[str, str]],
) -> None:
    kubernetes = _dict_value(
        target_section,
        "kubernetes",
    )

    registry = _dict_value(
        target_section,
        "registry",
    )

    gitops = _dict_value(
        target_section,
        "gitops",
    )

    argocd = _dict_value(
        target_section,
        "argocd",
    )

    kubernetes["server"] = str(
        kubernetes.get("server")
        or (
            "https://"
            "kubernetes.default.svc"
        )
    ).strip()

    registry["host"] = str(
        registry.get("host")
        or ""
    ).strip().rstrip("/")

    registry[
        "repositoryPrefix"
    ] = sanitize_slug(
        str(
            registry.get(
                "repositoryPrefix"
            )
            or "images"
        )
    )

    registry[
        "imagePullSecretName"
    ] = str(
        registry.get(
            "imagePullSecretName"
        )
        or ""
    ).strip()

    if not registry["host"]:
        errors.append(
            _issue(
                "target.registry.host",
                "REGISTRY_HOST_REQUIRED",
                (
                    "Indiquez l'adresse du "
                    "registre Docker Nexus, "
                    "par exemple "
                    "registry.example.com:8082."
                ),
            )
        )

    gitops["repositoryUrl"] = str(
        gitops.get(
            "repositoryUrl"
        )
        or ""
    ).strip()

    gitops["targetRevision"] = str(
        gitops.get(
            "targetRevision"
        )
        or "main"
    ).strip()

    gitops["basePath"] = str(
        gitops.get("basePath")
        or "projects"
    ).strip().strip("/")

    if not gitops[
        "repositoryUrl"
    ]:
        errors.append(
            _issue(
                (
                    "target.gitops."
                    "repositoryUrl"
                ),
                (
                    "GITOPS_REPOSITORY_"
                    "REQUIRED"
                ),
                (
                    "Indiquez l'URL exacte "
                    "du dépôt GitOps utilisé "
                    "par Argo CD."
                ),
            )
        )

    argocd["serverUrl"] = str(
        argocd.get("serverUrl")
        or ""
    ).strip().rstrip("/")

    argocd["namespace"] = (
        sanitize_slug(
            str(
                argocd.get(
                    "namespace"
                )
                or "argocd"
            )
        )
    )

    argocd["projectName"] = (
        sanitize_slug(
            str(
                argocd.get(
                    "projectName"
                )
                or "sapixi"
            )
        )
    )

    argocd["automaticSync"] = (
        bool(
            argocd.get(
                "automaticSync",
                False,
            )
        )
    )

    argocd["prune"] = bool(
        argocd.get(
            "prune",
            False,
        )
    )

    argocd["selfHeal"] = bool(
        argocd.get(
            "selfHeal",
            False,
        )
    )

    if argocd[
        "automaticSync"
    ]:
        warnings.append(
            _issue(
                (
                    "target.argocd."
                    "automaticSync"
                ),
                "AUTO_SYNC_ENABLED",
                (
                    "La synchronisation "
                    "automatique est activée. "
                    "Une confirmation humaine "
                    "ne sera plus requise "
                    "côté Argo CD."
                ),
            )
        )

    if not argocd["serverUrl"]:
        questions.append(
            _issue(
                (
                    "target.argocd."
                    "serverUrl"
                ),
                (
                    "ARGOCD_SERVER_"
                    "OPTIONAL_FOR_GENERATION"
                ),
                (
                    "L'URL Argo CD n'est pas "
                    "nécessaire pour générer "
                    "les YAML, mais elle sera "
                    "requise au déploiement."
                ),
            )
        )


def _validate_components(
    *,
    raw_components: list[Any],
    detected_components:
        list[dict[str, Any]],
    target: dict[str, Any],
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
    questions: list[dict[str, str]],
) -> list[dict[str, Any]]:
    detected_by_id = {
        int(component["id"]):
            component

        for component
        in detected_components
    }

    normalized: list[
        dict[str, Any]
    ] = []

    seen_ids: set[int] = set()
    seen_slugs: set[str] = set()

    for (
        index,
        raw_component,
    ) in enumerate(
        raw_components
    ):
        path = (
            f"components[{index}]"
        )

        if not isinstance(
            raw_component,
            dict,
        ):
            errors.append(
                _issue(
                    path,
                    "COMPONENT_INVALID",
                    (
                        "Chaque composant doit "
                        "être un objet JSON."
                    ),
                )
            )

            continue

        component_id = (
            _integer_or_none(
                raw_component.get("id")
            )
        )

        if (
            component_id is None
            or component_id
            not in detected_by_id
        ):
            errors.append(
                _issue(
                    f"{path}.id",
                    (
                        "COMPONENT_"
                        "NOT_DETECTED"
                    ),
                    (
                        "Le composant ne "
                        "correspond pas à "
                        "l'analyse confirmée."
                    ),
                )
            )

            continue

        if component_id in seen_ids:
            errors.append(
                _issue(
                    f"{path}.id",
                    (
                        "COMPONENT_"
                        "DUPLICATED"
                    ),
                    (
                        "Le même composant "
                        "est présent "
                        "plusieurs fois."
                    ),
                )
            )

            continue

        seen_ids.add(
            component_id
        )

        detected = detected_by_id[
            component_id
        ]

        component = copy.deepcopy(
            raw_component
        )

        component["id"] = (
            component_id
        )

        component["name"] = str(
            component.get("name")
            or detected.get("name")
            or "component"
        ).strip()

        component["slug"] = (
            sanitize_slug(
                str(
                    component.get("slug")
                    or component["name"]
                )
            )
        )

        component["rootPath"] = str(
            detected.get("root_path")
            or "."
        )

        component[
            "componentType"
        ] = str(
            component.get(
                "componentType"
            )
            or detected.get(
                "component_type"
            )
            or "unknown"
        ).lower()

        component["framework"] = str(
            component.get("framework")
            or detected.get(
                "framework"
            )
            or ""
        )

        component[
            "packageManager"
        ] = str(
            component.get(
                "packageManager"
            )
            or detected.get(
                "package_manager"
            )
            or ""
        )

        component["deployable"] = (
            bool(
                component.get(
                    "deployable",
                    detected.get(
                        "deployable",
                        True,
                    ),
                )
            )
        )

        if (
            component["slug"]
            in seen_slugs
        ):
            errors.append(
                _issue(
                    f"{path}.slug",
                    (
                        "COMPONENT_SLUG_"
                        "DUPLICATED"
                    ),
                    (
                        "Deux composants "
                        "produisent le même "
                        "nom Kubernetes."
                    ),
                )
            )

        seen_slugs.add(
            component["slug"]
        )

        runtime = _dict_value(
            component,
            "runtime",
        )

        runtime["name"] = str(
            runtime.get("name")
            or detected.get("runtime")
            or ""
        )

        runtime["version"] = str(
            runtime.get("version")
            or ""
        )

        build = _dict_value(
            component,
            "build",
        )

        build["context"] = str(
            build.get("context")
            or detected.get(
                "root_path"
            )
            or "."
        ).strip()

        build[
            "dockerfilePath"
        ] = str(
            build.get(
                "dockerfilePath"
            )
            or detected.get(
                "dockerfile_path"
            )
            or ""
        ).strip()

        build[
            "helmChartPath"
        ] = str(
            build.get(
                "helmChartPath"
            )
            or detected.get(
                "helm_chart_path"
            )
            or ""
        ).strip()

        build[
            "installCommand"
        ] = str(
            build.get(
                "installCommand"
            )
            or ""
        ).strip()

        build["buildCommand"] = str(
            build.get("buildCommand")
            or detected.get(
                "build_command"
            )
            or ""
        ).strip()

        build["outputPath"] = str(
            build.get("outputPath")
            or ""
        ).strip()

        container = _dict_value(
            component,
            "container",
        )

        container[
            "startCommand"
        ] = str(
            container.get(
                "startCommand"
            )
            or detected.get(
                "start_command"
            )
            or ""
        ).strip()

        container["port"] = (
            _integer_or_none(
                container.get("port")
            )
        )

        container[
            "workingDirectory"
        ] = str(
            container.get(
                "workingDirectory"
            )
            or "/app"
        ).strip()

        container[
            "runAsUser"
        ] = (
            _integer_or_none(
                container.get(
                    "runAsUser"
                )
            )
            or 10001
        )

        container[
            "readOnlyRootFilesystem"
        ] = bool(
            container.get(
                (
                    "readOnlyRoot"
                    "Filesystem"
                ),
                False,
            )
        )

        if component["deployable"]:
            if (
                not container[
                    "startCommand"
                ]

                and component[
                    "componentType"
                ]
                not in {
                    "frontend",
                    "static",
                }
            ):
                errors.append(
                    _issue(
                        (
                            f"{path}.container."
                            "startCommand"
                        ),
                        (
                            "START_COMMAND_"
                            "REQUIRED"
                        ),
                        (
                            "La commande de "
                            "démarrage de "
                            f"{component['name']} "
                            "est obligatoire."
                        ),
                    )
                )

            if (
                container["port"]
                is None

                or not (
                    1
                    <= int(
                        container["port"]
                    )
                    <= 65535
                )
            ):
                errors.append(
                    _issue(
                        (
                            f"{path}.container."
                            "port"
                        ),
                        (
                            "CONTAINER_PORT_"
                            "INVALID"
                        ),
                        (
                            "Le port du composant "
                            f"{component['name']} "
                            "doit être compris "
                            "entre 1 et 65535."
                        ),
                    )
                )

        component["replicas"] = (
            _integer_or_none(
                component.get(
                    "replicas"
                )
            )
            or 1
        )

        if not (
            1
            <= component["replicas"]
            <= 100
        ):
            errors.append(
                _issue(
                    f"{path}.replicas",
                    "REPLICAS_INVALID",
                    (
                        "Le nombre de réplicas "
                        "doit être compris "
                        "entre 1 et 100."
                    ),
                )
            )

        _normalize_service(
            component,
            path,
            errors,
        )

        _normalize_ingress(
            component,
            path,
            target,
            errors,
            warnings,
        )

        _normalize_resources(
            component,
            path,
            errors,
        )

        _normalize_probes(
            component,
            path,
            errors,
            warnings,
            questions,
        )

        _normalize_environment(
            component,
            path,
            errors,
            warnings,
        )

        _normalize_volumes(
            component,
            path,
            errors,
        )

        _normalize_migration(
            component,
            path,
            errors,
        )

        _normalize_dependencies(
            component
        )

        normalized.append(
            component
        )

    missing_ids = (
        set(detected_by_id)
        - seen_ids
    )

    if missing_ids:
        warnings.append(
            _issue(
                "components",
                (
                    "DETECTED_COMPONENTS_"
                    "OMITTED"
                ),
                (
                    "Certains composants "
                    "détectés ne figurent pas "
                    "dans le contrat. Ils ne "
                    "seront pas générés."
                ),
            )
        )

    return normalized


def _normalize_service(
    component: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    service = _dict_value(
        component,
        "service",
    )

    container = component[
        "container"
    ]

    service["enabled"] = bool(
        service.get(
            "enabled",
            True,
        )
    )

    service["type"] = str(
        service.get("type")
        or "ClusterIP"
    )

    service["port"] = (
        _integer_or_none(
            service.get("port")
        )
        or container.get("port")
    )

    service["targetPort"] = (
        _integer_or_none(
            service.get(
                "targetPort"
            )
        )
        or container.get("port")
    )

    if service["type"] not in {
        "ClusterIP",
        "NodePort",
        "LoadBalancer",
    }:
        errors.append(
            _issue(
                f"{path}.service.type",
                "SERVICE_TYPE_INVALID",
                (
                    "Le type de Service "
                    "Kubernetes est invalide."
                ),
            )
        )

    if service["enabled"]:
        for key in (
            "port",
            "targetPort",
        ):
            value = service.get(key)

            if (
                value is None
                or not (
                    1
                    <= int(value)
                    <= 65535
                )
            ):
                errors.append(
                    _issue(
                        (
                            f"{path}."
                            f"service.{key}"
                        ),
                        (
                            "SERVICE_PORT_"
                            "INVALID"
                        ),
                        (
                            "Le port du Service "
                            "doit être compris "
                            "entre 1 et 65535."
                        ),
                    )
                )


def _normalize_ingress(
    component: dict[str, Any],
    path: str,
    target: dict[str, Any],
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> None:
    ingress = _dict_value(
        component,
        "ingress",
    )

    ingress["enabled"] = bool(
        ingress.get(
            "enabled",
            False,
        )
    )

    ingress["className"] = str(
        ingress.get("className")
        or "nginx"
    ).strip()

    ingress["host"] = str(
        ingress.get("host")
        or ""
    ).strip()

    ingress["path"] = str(
        ingress.get("path")
        or "/"
    ).strip()

    ingress["pathType"] = str(
        ingress.get("pathType")
        or "Prefix"
    )

    ingress[
        "tlsSecretName"
    ] = str(
        ingress.get(
            "tlsSecretName"
        )
        or ""
    ).strip()

    annotations = ingress.get(
        "annotations"
    )

    ingress["annotations"] = (
        annotations
        if isinstance(
            annotations,
            dict,
        )
        else {}
    )

    if not ingress["enabled"]:
        return

    if not component[
        "service"
    ]["enabled"]:
        errors.append(
            _issue(
                (
                    f"{path}.ingress."
                    "enabled"
                ),
                (
                    "INGRESS_REQUIRES_"
                    "SERVICE"
                ),
                (
                    "Un Ingress nécessite "
                    "un Service Kubernetes "
                    "activé."
                ),
            )
        )

    if not ingress["host"]:
        errors.append(
            _issue(
                f"{path}.ingress.host",
                (
                    "INGRESS_HOST_"
                    "REQUIRED"
                ),
                (
                    "Le nom d'hôte de "
                    "l'Ingress est "
                    "obligatoire."
                ),
            )
        )

    elif not DNS_HOST_PATTERN.fullmatch(
        ingress["host"]
    ):
        errors.append(
            _issue(
                f"{path}.ingress.host",
                "INGRESS_HOST_INVALID",
                (
                    "Le nom d'hôte de "
                    "l'Ingress est invalide."
                ),
            )
        )

    if not ingress[
        "path"
    ].startswith("/"):
        errors.append(
            _issue(
                f"{path}.ingress.path",
                "INGRESS_PATH_INVALID",
                (
                    "Le chemin Ingress doit "
                    "commencer par /."
                ),
            )
        )

    if ingress[
        "pathType"
    ] not in {
        "Prefix",
        "Exact",
        "ImplementationSpecific",
    }:
        errors.append(
            _issue(
                (
                    f"{path}.ingress."
                    "pathType"
                ),
                (
                    "INGRESS_PATH_TYPE_"
                    "INVALID"
                ),
                (
                    "Le pathType de "
                    "l'Ingress est invalide."
                ),
            )
        )

    if not target.get("domain"):
        warnings.append(
            _issue(
                f"{path}.ingress.host",
                (
                    "TARGET_DOMAIN_"
                    "NOT_SET"
                ),
                (
                    "Un Ingress est activé "
                    "alors que le domaine "
                    "global de "
                    "l'environnement "
                    "n'est pas défini."
                ),
            )
        )


def _normalize_resources(
    component: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    resources = _dict_value(
        component,
        "resources",
    )

    requests = _dict_value(
        resources,
        "requests",
    )

    limits = _dict_value(
        resources,
        "limits",
    )

    values = [
        (
            requests,
            "cpu",
            DEFAULT_CPU_REQUEST,
            "requests.cpu",
        ),
        (
            requests,
            "memory",
            DEFAULT_MEMORY_REQUEST,
            "requests.memory",
        ),
        (
            limits,
            "cpu",
            DEFAULT_CPU_LIMIT,
            "limits.cpu",
        ),
        (
            limits,
            "memory",
            DEFAULT_MEMORY_LIMIT,
            "limits.memory",
        ),
    ]

    for (
        section,
        property_name,
        default,
        field_path,
    ) in values:
        value = str(
            section.get(
                property_name
            )
            or default
        ).strip()

        section[
            property_name
        ] = value

        if not (
            RESOURCE_QUANTITY_PATTERN
            .fullmatch(value)
        ):
            errors.append(
                _issue(
                    (
                        f"{path}.resources."
                        f"{field_path}"
                    ),
                    (
                        "RESOURCE_QUANTITY_"
                        "INVALID"
                    ),
                    (
                        "La quantité Kubernetes "
                        f"{value!r} est invalide."
                    ),
                )
            )


def _normalize_probes(
    component: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
    questions: list[dict[str, str]],
) -> None:
    probes = _dict_value(
        component,
        "probes",
    )

    for probe_name in (
        "startup",
        "readiness",
        "liveness",
    ):
        probe = _dict_value(
            probes,
            probe_name,
        )

        probe["enabled"] = bool(
            probe.get(
                "enabled",
                False,
            )
        )

        probe["path"] = str(
            probe.get("path")
            or ""
        ).strip()

        probe[
            "initialDelaySeconds"
        ] = max(
            0,
            _integer_or_none(
                probe.get(
                    "initialDelaySeconds"
                )
            )
            or 0,
        )

        probe[
            "periodSeconds"
        ] = max(
            1,
            _integer_or_none(
                probe.get(
                    "periodSeconds"
                )
            )
            or 10,
        )

        probe[
            "timeoutSeconds"
        ] = max(
            1,
            _integer_or_none(
                probe.get(
                    "timeoutSeconds"
                )
            )
            or 2,
        )

        probe[
            "failureThreshold"
        ] = max(
            1,
            _integer_or_none(
                probe.get(
                    "failureThreshold"
                )
            )
            or 3,
        )

        if (
            probe["enabled"]
            and not probe[
                "path"
            ].startswith("/")
        ):
            errors.append(
                _issue(
                    (
                        f"{path}.probes."
                        f"{probe_name}.path"
                    ),
                    (
                        "PROBE_PATH_"
                        "INVALID"
                    ),
                    (
                        "Le chemin de la "
                        "probe doit commencer "
                        "par /."
                    ),
                )
            )

    if (
        component.get("deployable")
        and not probes[
            "readiness"
        ]["enabled"]
    ):
        questions.append(
            _issue(
                (
                    f"{path}.probes."
                    "readiness"
                ),
                (
                    "READINESS_"
                    "NOT_CONFIRMED"
                ),
                (
                    "Aucune readiness probe "
                    "n'est activée pour "
                    f"{component['name']}."
                ),
            )
        )

    if (
        component.get("deployable")
        and not probes[
            "liveness"
        ]["enabled"]
    ):
        warnings.append(
            _issue(
                (
                    f"{path}.probes."
                    "liveness"
                ),
                "LIVENESS_DISABLED",
                (
                    "La liveness probe est "
                    "désactivée pour "
                    f"{component['name']}."
                ),
            )
        )


def _normalize_environment(
    component: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> None:
    raw_configuration = component.get(
        "configuration"
    )

    raw_secrets = component.get(
        "secrets"
    )

    if not isinstance(
        raw_configuration,
        list,
    ):
        raw_configuration = []

    if not isinstance(
        raw_secrets,
        list,
    ):
        raw_secrets = []

    normalized_configuration: list[
        dict[str, Any]
    ] = []

    normalized_secrets: list[
        dict[str, Any]
    ] = []

    names: set[str] = set()

    groups = [
        (
            "configuration",
            raw_configuration,
            normalized_configuration,
        ),
        (
            "secrets",
            raw_secrets,
            normalized_secrets,
        ),
    ]

    for (
        source_name,
        values,
        destination,
    ) in groups:
        for (
            index,
            raw_item,
        ) in enumerate(values):
            item_path = (
                f"{path}."
                f"{source_name}"
                f"[{index}]"
            )

            if isinstance(
                raw_item,
                str,
            ):
                raw_item = {
                    "name":
                        raw_item,
                }

            if not isinstance(
                raw_item,
                dict,
            ):
                errors.append(
                    _issue(
                        item_path,
                        "ENV_ITEM_INVALID",
                        (
                            "La variable doit "
                            "être un objet ou "
                            "un nom de variable."
                        ),
                    )
                )

                continue

            name = str(
                raw_item.get("name")
                or ""
            ).strip()

            if not (
                ENV_NAME_PATTERN
                .fullmatch(name)
            ):
                errors.append(
                    _issue(
                        (
                            f"{item_path}."
                            "name"
                        ),
                        "ENV_NAME_INVALID",
                        (
                            "Le nom de variable "
                            f"{name!r} "
                            "est invalide."
                        ),
                    )
                )

                continue

            if name in names:
                errors.append(
                    _issue(
                        (
                            f"{item_path}."
                            "name"
                        ),
                        (
                            "ENV_NAME_"
                            "DUPLICATED"
                        ),
                        (
                            f"La variable {name} "
                            "est déclarée "
                            "plusieurs fois."
                        ),
                    )
                )

                continue

            names.add(name)

            normalized_item = {
                "name":
                    name,

                "required":
                    bool(
                        raw_item.get(
                            "required",
                            True,
                        )
                    ),

                "description":
                    str(
                        raw_item.get(
                            "description"
                        )
                        or ""
                    ).strip(),
            }

            if (
                source_name
                == "configuration"
            ):
                normalized_item[
                    "value"
                ] = str(
                    raw_item.get("value")
                    or ""
                )

                if (
                    SENSITIVE_NAME_PATTERN
                    .search(name)
                ):
                    warnings.append(
                        _issue(
                            item_path,
                            (
                                "SENSITIVE_"
                                "VARIABLE_IN_CONFIG"
                            ),
                            (
                                f"{name} semble "
                                "sensible et devrait "
                                "être déplacée dans "
                                "Secrets."
                            ),
                        )
                    )

            destination.append(
                normalized_item
            )

    component[
        "configuration"
    ] = normalized_configuration

    component[
        "secrets"
    ] = normalized_secrets


def _normalize_volumes(
    component: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    raw_volumes = component.get(
        "volumes"
    )

    if not isinstance(
        raw_volumes,
        list,
    ):
        raw_volumes = []

    normalized: list[
        dict[str, Any]
    ] = []

    names: set[str] = set()

    for (
        index,
        raw_volume,
    ) in enumerate(
        raw_volumes
    ):
        volume_path = (
            f"{path}.volumes"
            f"[{index}]"
        )

        if not isinstance(
            raw_volume,
            dict,
        ):
            errors.append(
                _issue(
                    volume_path,
                    "VOLUME_INVALID",
                    (
                        "Chaque volume doit "
                        "être un objet."
                    ),
                )
            )

            continue

        name = sanitize_slug(
            str(
                raw_volume.get("name")
                or (
                    f"volume-"
                    f"{index + 1}"
                )
            )
        )

        mount_path = str(
            raw_volume.get(
                "mountPath"
            )
            or ""
        ).strip()

        size = str(
            raw_volume.get("size")
            or "1Gi"
        ).strip()

        access_mode = str(
            raw_volume.get(
                "accessMode"
            )
            or "ReadWriteOnce"
        )

        storage_class = str(
            raw_volume.get(
                "storageClass"
            )
            or ""
        ).strip()

        if name in names:
            errors.append(
                _issue(
                    (
                        f"{volume_path}."
                        "name"
                    ),
                    (
                        "VOLUME_NAME_"
                        "DUPLICATED"
                    ),
                    (
                        "Deux volumes portent "
                        "le même nom."
                    ),
                )
            )

            continue

        names.add(name)

        if not mount_path.startswith(
            "/"
        ):
            errors.append(
                _issue(
                    (
                        f"{volume_path}."
                        "mountPath"
                    ),
                    (
                        "VOLUME_MOUNT_PATH_"
                        "INVALID"
                    ),
                    (
                        "Le chemin de montage "
                        "doit être absolu."
                    ),
                )
            )

        if access_mode not in {
            "ReadWriteOnce",
            "ReadOnlyMany",
            "ReadWriteMany",
            "ReadWriteOncePod",
        }:
            errors.append(
                _issue(
                    (
                        f"{volume_path}."
                        "accessMode"
                    ),
                    (
                        "VOLUME_ACCESS_MODE_"
                        "INVALID"
                    ),
                    (
                        "Le mode d'accès du "
                        "volume est invalide."
                    ),
                )
            )

        normalized.append(
            {
                "name":
                    name,

                "mountPath":
                    mount_path,

                "size":
                    size,

                "accessMode":
                    access_mode,

                "storageClass":
                    storage_class,

                "readOnly":
                    bool(
                        raw_volume.get(
                            "readOnly",
                            False,
                        )
                    ),
            }
        )

    component["volumes"] = (
        normalized
    )


def _normalize_migration(
    component: dict[str, Any],
    path: str,
    errors: list[dict[str, str]],
) -> None:
    migration = _dict_value(
        component,
        "migration",
    )

    migration["enabled"] = bool(
        migration.get(
            "enabled",
            False,
        )
    )

    migration["command"] = str(
        migration.get("command")
        or ""
    ).strip()

    migration[
        "backoffLimit"
    ] = max(
        0,
        _integer_or_none(
            migration.get(
                "backoffLimit"
            )
        )
        or 1,
    )

    if (
        migration["enabled"]
        and not migration["command"]
    ):
        errors.append(
            _issue(
                (
                    f"{path}.migration."
                    "command"
                ),
                (
                    "MIGRATION_COMMAND_"
                    "REQUIRED"
                ),
                (
                    "La commande de migration "
                    "est obligatoire lorsque "
                    "le Job est activé."
                ),
            )
        )


def _normalize_dependencies(
    component: dict[str, Any],
) -> None:
    dependencies = component.get(
        "dependencies"
    )

    if not isinstance(
        dependencies,
        list,
    ):
        dependencies = []

    component[
        "dependencies"
    ] = [
        sanitize_slug(
            str(dependency)
        )
        for dependency
        in dependencies
        if str(dependency).strip()
    ]


def _normalize_policies(
    raw_policies: dict[str, Any],
) -> dict[str, Any]:
    maximum_context = (
        _integer_or_none(
            raw_policies.get(
                "maximumAiContextBytes"
            )
        )
        or 200_000
    )

    return {
        "preserveExistingDockerfile":
            bool(
                raw_policies.get(
                    (
                        "preserveExisting"
                        "Dockerfile"
                    ),
                    True,
                )
            ),

        "preserveExistingHelmChart":
            bool(
                raw_policies.get(
                    (
                        "preserveExisting"
                        "HelmChart"
                    ),
                    True,
                )
            ),

        "requireNonRoot":
            bool(
                raw_policies.get(
                    "requireNonRoot",
                    True,
                )
            ),

        "allowPrivileged":
            bool(
                raw_policies.get(
                    "allowPrivileged",
                    False,
                )
            ),

        "requireManualArgoSync":
            bool(
                raw_policies.get(
                    (
                        "requireManual"
                        "ArgoSync"
                    ),
                    True,
                )
            ),

        "maximumAiContextBytes":
            min(
                500_000,
                max(
                    20_000,
                    maximum_context,
                ),
            ),
    }


def _normalize_detected_environment_variables(
    raw_value: Any,
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(
        raw_value,
        list,
    ):
        return {
            "configuration": [],
            "secrets": [],
        }

    configuration: list[
        dict[str, Any]
    ] = []

    secrets: list[
        dict[str, Any]
    ] = []

    seen: set[str] = set()

    for item in raw_value:
        if isinstance(
            item,
            dict,
        ):
            name = str(
                item.get("name")
                or item.get("variable")
                or ""
            ).strip()

        else:
            name = str(
                item
            ).strip()

        if (
            not name
            or name in seen
            or not (
                ENV_NAME_PATTERN
                .fullmatch(name)
            )
        ):
            continue

        seen.add(name)

        normalized = {
            "name":
                name,

            "required":
                True,

            "description":
                (
                    "Variable détectée "
                    "pendant l'analyse."
                ),
        }

        if (
            SENSITIVE_NAME_PATTERN
            .search(name)
        ):
            secrets.append(
                normalized
            )

        else:
            normalized["value"] = ""

            configuration.append(
                normalized
            )

    return {
        "configuration":
            configuration,

        "secrets":
            secrets,
    }


def _suggest_probe_path(
    *,
    component_type: str,
    framework: str,
) -> str:
    normalized_framework = (
        framework.lower()
    )

    if component_type in {
        "frontend",
        "static",
    }:
        return "/"

    known_frameworks = (
        "flask",
        "fastapi",
        "django",
        "express",
        "spring",
    )

    if any(
        name
        in normalized_framework

        for name
        in known_frameworks
    ):
        return "/health"

    return ""


def _service_index(
    services: list[Any],
) -> dict[str, dict[str, Any]]:
    result: dict[
        str,
        dict[str, Any],
    ] = {}

    for item in services:
        if (
            isinstance(item, dict)
            and item.get("role")
        ):
            result[
                str(item["role"])
            ] = item

    return result


def _dict_value(
    parent: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    value = parent.get(key)

    if not isinstance(
        value,
        dict,
    ):
        value = {}

        parent[key] = value

    return value


def _integer_or_none(
    value: Any,
) -> int | None:
    if value in {
        None,
        "",
    }:
        return None

    try:
        return int(value)

    except (
        TypeError,
        ValueError,
    ):
        return None


def _optional_string(
    value: Any,
) -> str | None:
    normalized = str(
        value or ""
    ).strip()

    return normalized or None


def _default_port(
    component_type: str,
) -> int:
    defaults = {
        "frontend":
            80,

        "static":
            80,

        "backend":
            8000,

        "api":
            8000,

        "fullstack":
            3000,

        "worker":
            8000,
    }

    return defaults.get(
        component_type,
        8000,
    )


def _issue(
    path: str,
    code: str,
    message: str,
) -> dict[str, str]:
    return {
        "path":
            path,

        "code":
            code,

        "message":
            message,
    }


def _sanitize_for_ai(
    value: Any,
    *,
    key_name: str = "",
) -> Any:
    if isinstance(
        value,
        dict,
    ):
        sanitized: dict[
            str,
            Any,
        ] = {}

        local_name = str(
            value.get("name")
            or key_name
        )

        local_name_is_sensitive = (
            bool(
                SENSITIVE_NAME_PATTERN
                .search(local_name)
            )
        )

        for (
            key,
            child,
        ) in value.items():
            key_text = str(key)
            lower_key = (
                key_text.lower()
            )

            if lower_key in {
                "secret",
                "password",
                "token",
                "credential",
                "privatekey",
                "private_key",
            }:
                sanitized[
                    key_text
                ] = "<redacted>"

                continue

            if (
                lower_key == "value"
                and local_name_is_sensitive
            ):
                sanitized[
                    key_text
                ] = "<redacted>"

                continue

            sanitized[
                key_text
            ] = _sanitize_for_ai(
                child,
                key_name=key_text,
            )

        return sanitized

    if isinstance(
        value,
        list,
    ):
        return [
            _sanitize_for_ai(
                item,
                key_name=key_name,
            )
            for item in value
        ]

    if (
        isinstance(value, str)
        and (
            SENSITIVE_NAME_PATTERN
            .search(key_name)
        )
    ):
        return (
            "<redacted>"
            if value
            else ""
        )

    return value