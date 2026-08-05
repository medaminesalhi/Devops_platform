from __future__ import annotations

import hashlib
import json
import re

from dataclasses import (
    asdict,
    dataclass,
)

from pathlib import Path

from typing import Any

from flask import current_app


TEXT_FILE_MAX_BYTES = 1_000_000


@dataclass
class GeneratedArtifact:
    component_id: int | None

    artifact_type: str
    relative_path: str

    content: str
    content_sha256: str

    artifact_status: str

    metadata: dict[str, Any]

    original_content: str | None = None

    def to_dict(
        self,
    ) -> dict[str, Any]:
        return asdict(self)


def build_generation_plan(
    *,
    source_root: Path,
    context: dict[str, Any],
    components: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
]:
    artifacts: list[
        GeneratedArtifact
    ] = []

    warnings: list[str] = []

    project_slug = sanitize_name(
        str(context["slug"])
    )

    environment_code = sanitize_name(
        str(
            context.get(
                "environment_code"
            )
            or context.get(
                "environment_name"
            )
            or "environment"
        )
    )

    version = str(
        context["confirmed_version"]
    )

    for component in components:
        (
            component_artifacts,
            component_warnings,
        ) = generate_component_artifacts(
            source_root=source_root,
            context=context,
            component=component,
            project_slug=project_slug,
            environment_code=
                environment_code,
            version=version,
        )

        artifacts.extend(
            component_artifacts
        )

        warnings.extend(
            component_warnings
        )

    for component in components:
        artifacts.append(
            generate_argocd_application(
                context=context,
                component=component,
                project_slug=project_slug,
                environment_code=
                    environment_code,
                version=version,
            )
        )

    artifact_dicts = [
        artifact.to_dict()
        for artifact in artifacts
    ]

    status_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}

    for artifact in artifacts:
        status_counts[
            artifact.artifact_status
        ] = (
            status_counts.get(
                artifact.artifact_status,
                0,
            )
            + 1
        )

        type_counts[
            artifact.artifact_type
        ] = (
            type_counts.get(
                artifact.artifact_type,
                0,
            )
            + 1
        )

    summary = {
        "project": {
            "id": context["id"],
            "name": context["name"],
            "slug": project_slug,
        },

        "analysis": {
            "id": context[
                "confirmed_analysis_run_id"
            ],

            "version": version,
            "shortVersion": version[:12],
        },

        "environment": {
            "id": context[
                "default_environment_id"
            ],

            "name": context[
                "environment_name"
            ],

            "code": environment_code,

            "namespace": context[
                "environment_namespace"
            ],

            "domain": context.get(
                "environment_domain"
            ),
        },

        "componentCount":
            len(components),

        "artifactCount":
            len(artifacts),

        "artifactStatusCounts":
            status_counts,

        "artifactTypeCounts":
            type_counts,

        "warningCount":
            len(warnings),

        "warnings":
            warnings,

        "readyForReview":
            len(artifacts) > 0,

        "nextPhase":
            4,
    }

    return artifact_dicts, summary


def generate_component_artifacts(
    *,
    source_root: Path,
    context: dict[str, Any],
    component: dict[str, Any],
    project_slug: str,
    environment_code: str,
    version: str,
) -> tuple[
    list[GeneratedArtifact],
    list[str],
]:
    artifacts: list[
        GeneratedArtifact
    ] = []

    warnings: list[str] = []

    component_slug = sanitize_name(
        str(component["name"])
    )

    component_root = resolve_inside_root(
        source_root,
        str(component["root_path"]),
    )

    dockerfile_path_value = (
        component.get(
            "dockerfile_path"
        )
    )

    if dockerfile_path_value:
        existing_dockerfile = (
            resolve_inside_root(
                source_root,
                str(
                    dockerfile_path_value
                ),
            )
        )

        if existing_dockerfile.is_file():
            content = read_text_file(
                existing_dockerfile
            )

            artifacts.append(
                make_artifact(
                    component_id=int(
                        component["id"]
                    ),

                    artifact_type=
                        "dockerfile",

                    relative_path=
                        normalize_path(
                            str(
                                dockerfile_path_value
                            )
                        ),

                    content=content,

                    artifact_status=
                        "existing",

                    metadata={
                        "componentName":
                            component["name"],

                        "source":
                            "repository",
                    },
                )
            )

        else:
            warnings.append(
                (
                    "Le Dockerfile déclaré "
                    f"pour {component['name']} "
                    "est introuvable."
                )
            )

            dockerfile_path_value = None

    if not dockerfile_path_value:
        (
            dockerfile_content,
            needs_review,
            notes,
        ) = generate_dockerfile(
            component_root=
                component_root,

            component=
                component,
        )

        relative_path = (
            join_relative_path(
                str(
                    component["root_path"]
                ),
                "Dockerfile",
            )
        )

        artifacts.append(
            make_artifact(
                component_id=int(
                    component["id"]
                ),

                artifact_type=
                    "dockerfile",

                relative_path=
                    relative_path,

                content=
                    dockerfile_content,

                artifact_status=(
                    "needs_review"
                    if needs_review
                    else "generated"
                ),

                metadata={
                    "componentName":
                        component["name"],

                    "notes":
                        notes,
                },
            )
        )

        if needs_review:
            warnings.append(
                (
                    "Le Dockerfile de "
                    f"{component['name']} "
                    "doit être vérifié."
                )
            )

    dockerignore_path = (
        component_root
        / ".dockerignore"
    )

    dockerignore_relative = (
        join_relative_path(
            str(component["root_path"]),
            ".dockerignore",
        )
    )

    if dockerignore_path.is_file():
        artifacts.append(
            make_artifact(
                component_id=int(
                    component["id"]
                ),

                artifact_type=
                    "dockerignore",

                relative_path=
                    dockerignore_relative,

                content=read_text_file(
                    dockerignore_path
                ),

                artifact_status=
                    "existing",

                metadata={
                    "componentName":
                        component["name"],

                    "source":
                        "repository",
                },
            )
        )

    else:
        artifacts.append(
            make_artifact(
                component_id=int(
                    component["id"]
                ),

                artifact_type=
                    "dockerignore",

                relative_path=
                    dockerignore_relative,

                content=
                    generate_dockerignore(
                        component
                    ),

                artifact_status=
                    "generated",

                metadata={
                    "componentName":
                        component["name"],
                },
            )
        )

    chart_root = (
        f"projects/{project_slug}/"
        f"{environment_code}/"
        f"{component_slug}"
    )

    existing_chart_path = (
        component.get(
            "helm_chart_path"
        )
    )

    if existing_chart_path:
        chart_directory = (
            resolve_inside_root(
                source_root,
                str(
                    existing_chart_path
                ),
            )
        )

        existing_chart_artifacts = (
            read_existing_chart(
                chart_directory=
                    chart_directory,

                chart_relative_path=
                    str(
                        existing_chart_path
                    ),

                component_id=int(
                    component["id"]
                ),

                component_name=str(
                    component["name"]
                ),
            )
        )

        if existing_chart_artifacts:
            artifacts.extend(
                existing_chart_artifacts
            )

        else:
            warnings.append(
                (
                    "Le chart Helm déclaré "
                    f"pour {component['name']} "
                    "est introuvable."
                )
            )

            artifacts.extend(
                generate_helm_chart(
                    context=context,
                    component=component,
                    project_slug=
                        project_slug,
                    component_slug=
                        component_slug,
                    chart_root=chart_root,
                    version=version,
                )
            )

    else:
        artifacts.extend(
            generate_helm_chart(
                context=context,
                component=component,
                project_slug=
                    project_slug,
                component_slug=
                    component_slug,
                chart_root=
                    chart_root,
                version=
                    version,
            )
        )

    return artifacts, warnings


def generate_dockerfile(
    *,
    component_root: Path,
    component: dict[str, Any],
) -> tuple[
    str,
    bool,
    list[str],
]:
    framework = str(
        component.get("framework")
        or ""
    ).lower()

    runtime = str(
        component.get("runtime")
        or ""
    ).lower()

    component_type = str(
        component.get(
            "component_type"
        )
        or "unknown"
    ).lower()

    port = int(
        component.get(
            "detected_port"
        )
        or default_port(
            component_type
        )
    )

    package_manager = str(
        component.get(
            "package_manager"
        )
        or "npm"
    ).lower()

    notes: list[str] = []

    if "angular" in framework:
        output_path = (
            detect_angular_output_path(
                component_root
            )
        )

        install_command = (
            node_install_command(
                component_root,
                package_manager,
            )
        )

        dependency_copy = (
            node_dependency_copy(
                component_root,
                package_manager,
            )
        )

        build_command = (
            component.get(
                "build_command"
            )
            or node_build_command(
                package_manager
            )
        )

        content = f"""FROM node:20-alpine AS build
WORKDIR /app

{dependency_copy}
RUN {install_command}

COPY . .
RUN {build_command}

FROM nginx:1.27-alpine
COPY --from=build /app/{output_path}/ /usr/share/nginx/html/

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
"""

        return content, False, notes

    if (
        "flask" in framework
        or "fastapi" in framework
        or "django" in framework
        or "python" in runtime
    ):
        start_command = str(
            component.get(
                "start_command"
            )
            or ""
        ).strip()

        needs_review = False

        if not start_command:
            if "fastapi" in framework:
                start_command = (
                    "uvicorn main:app "
                    "--host 0.0.0.0 "
                    f"--port {port}"
                )

            elif "django" in framework:
                start_command = (
                    "gunicorn "
                    f"--bind 0.0.0.0:{port} "
                    "config.wsgi:application"
                )

            else:
                start_command = (
                    "gunicorn "
                    f"--bind 0.0.0.0:{port} "
                    "wsgi:app"
                )

            needs_review = True

            notes.append(
                (
                    "La commande de démarrage "
                    "a été proposée automatiquement."
                )
            )

        requirement_file = (
            "requirements.txt"
            if (
                component_root
                / "requirements.txt"
            ).is_file()
            else None
        )

        install_block = (
            "COPY requirements.txt ./\n"
            "RUN pip install "
            "--no-cache-dir "
            "-r requirements.txt"
            if requirement_file
            else (
                "# Aucun requirements.txt détecté.\n"
                "# Ajoutez ici l'installation "
                "des dépendances."
            )
        )

        if requirement_file is None:
            needs_review = True

            notes.append(
                (
                    "Aucun requirements.txt "
                    "n'a été détecté."
                )
            )

        content = f"""FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1

WORKDIR /app

{install_block}

COPY . .

RUN useradd --create-home --uid 10001 appuser \\
    && chown -R appuser:appuser /app

USER appuser

EXPOSE {port}
CMD ["sh", "-c", {json.dumps(start_command)}]
"""

        return (
            content,
            needs_review,
            notes,
        )

    if (
        "node" in runtime
        or component_type
            == "backend"
    ):
        install_command = (
            node_install_command(
                component_root,
                package_manager,
            )
        )

        dependency_copy = (
            node_dependency_copy(
                component_root,
                package_manager,
            )
        )

        build_command = str(
            component.get(
                "build_command"
            )
            or ""
        ).strip()

        start_command = str(
            component.get(
                "start_command"
            )
            or node_start_command(
                package_manager
            )
        ).strip()

        build_line = (
            f"RUN {build_command}\n"
            if build_command
            else ""
        )

        content = f"""FROM node:20-alpine
WORKDIR /app

{dependency_copy}
RUN {install_command}

COPY . .
{build_line}
EXPOSE {port}
CMD ["sh", "-c", {json.dumps(start_command)}]
"""

        return content, False, notes

    notes.append(
        (
            "Le runtime du composant "
            "n'est pas suffisamment précis."
        )
    )

    content = f"""# Dockerfile à compléter après validation du runtime.
FROM alpine:3.20

WORKDIR /app
COPY . .

EXPOSE {port}

CMD ["sh", "-c", "echo 'Commande de démarrage à définir' && exit 1"]
"""

    return content, True, notes


def generate_dockerignore(
    component: dict[str, Any],
) -> str:
    runtime = str(
        component.get("runtime")
        or ""
    ).lower()

    entries = [
        ".git",
        ".gitignore",
        ".env",
        ".env.*",
        ".idea",
        ".vscode",
        "*.log",
        "coverage",
        "dist",
        "build",
    ]

    if "python" in runtime:
        entries.extend(
            [
                ".venv",
                "venv",
                "__pycache__",
                "*.pyc",
                ".pytest_cache",
                ".mypy_cache",
            ]
        )

    else:
        entries.extend(
            [
                "node_modules",
                ".angular",
                ".npm",
                ".pnpm-store",
            ]
        )

    return (
        "\n".join(
            dict.fromkeys(entries)
        )
        + "\n"
    )


def generate_helm_chart(
    *,
    context: dict[str, Any],
    component: dict[str, Any],
    project_slug: str,
    component_slug: str,
    chart_root: str,
    version: str,
) -> list[GeneratedArtifact]:
    component_id = int(
        component["id"]
    )

    port = int(
        component.get(
            "detected_port"
        )
        or default_port(
            str(
                component.get(
                    "component_type"
                )
                or "unknown"
            )
        )
    )

    registry = str(
        current_app.config.get(
            "NEXUS_DOCKER_REGISTRY",
            "nexus.docker.piximind.com",
        )
    ).rstrip("/")

    image_repository = (
        f"{registry}/{project_slug}/"
        f"{component_slug}"
    )

    domain = str(
        context.get(
            "environment_domain"
        )
        or ""
    ).strip()

    component_type = str(
        component.get(
            "component_type"
        )
        or "unknown"
    ).lower()

    ingress_enabled = bool(
        domain
        and component_type in {
            "frontend",
            "fullstack",
        }
    )

    ingress_host = (
        f"{component_slug}-"
        f"{project_slug}."
        f"{domain}"
        if ingress_enabled
        else ""
    )

    chart_yaml = f"""apiVersion: v2
name: {component_slug}
description: Chart Helm généré par Piximind
version: 0.1.0
appVersion: "{version[:12]}"
type: application
"""

    values_yaml = f"""replicaCount: 1

image:
  repository: {image_repository}
  tag: "{version[:12]}"
  pullPolicy: IfNotPresent

imagePullSecrets: []

nameOverride: ""
fullnameOverride: ""

serviceAccount:
  create: false
  name: ""

podAnnotations: {{}}
podLabels: {{}}

podSecurityContext:
  runAsNonRoot: true
  seccompProfile:
    type: RuntimeDefault

securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
  readOnlyRootFilesystem: false

service:
  type: ClusterIP
  port: {port}
  targetPort: {port}

ingress:
  enabled: {str(ingress_enabled).lower()}
  className: nginx
  annotations: {{}}
  host: {json.dumps(ingress_host)}
  path: /
  pathType: Prefix
  tls: []

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 512Mi

readinessProbe:
  enabled: false
  path: /
  initialDelaySeconds: 5
  periodSeconds: 10

livenessProbe:
  enabled: false
  path: /
  initialDelaySeconds: 15
  periodSeconds: 20

env: []
secretRef: ""

nodeSelector: {{}}
tolerations: []
affinity: {{}}
"""

    environment_code = sanitize_name(
        str(
            context[
                "environment_code"
            ]
        )
    )

    environment_values = f"""replicaCount: 1

image:
  tag: "{version[:12]}"

ingress:
  enabled: {str(ingress_enabled).lower()}
  host: {json.dumps(ingress_host)}

secretRef: "{project_slug}-{component_slug}-secrets"
"""

    helpers = """{{- define "piximind.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "piximind.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "piximind.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "piximind.labels" -}}
app.kubernetes.io/name: {{ include "piximind.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}
"""

    deployment = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "piximind.fullname" . }}
  labels:
    {{- include "piximind.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      app.kubernetes.io/name: {{ include "piximind.name" . }}
      app.kubernetes.io/instance: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: {{ include "piximind.name" . }}
        app.kubernetes.io/instance: {{ .Release.Name }}
        {{- with .Values.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
    spec:
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
          ports:
            - name: http
              containerPort: {{ .Values.service.targetPort }}
              protocol: TCP
          {{- if .Values.secretRef }}
          envFrom:
            - secretRef:
                name: {{ .Values.secretRef }}
          {{- end }}
          {{- with .Values.env }}
          env:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- if .Values.readinessProbe.enabled }}
          readinessProbe:
            httpGet:
              path: {{ .Values.readinessProbe.path }}
              port: http
            initialDelaySeconds: {{ .Values.readinessProbe.initialDelaySeconds }}
            periodSeconds: {{ .Values.readinessProbe.periodSeconds }}
          {{- end }}
          {{- if .Values.livenessProbe.enabled }}
          livenessProbe:
            httpGet:
              path: {{ .Values.livenessProbe.path }}
              port: http
            initialDelaySeconds: {{ .Values.livenessProbe.initialDelaySeconds }}
            periodSeconds: {{ .Values.livenessProbe.periodSeconds }}
          {{- end }}
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
"""

    service = """apiVersion: v1
kind: Service
metadata:
  name: {{ include "piximind.fullname" . }}
  labels:
    {{- include "piximind.labels" . | nindent 4 }}
spec:
  type: {{ .Values.service.type }}
  selector:
    app.kubernetes.io/name: {{ include "piximind.name" . }}
    app.kubernetes.io/instance: {{ .Release.Name }}
  ports:
    - name: http
      port: {{ .Values.service.port }}
      targetPort: http
      protocol: TCP
"""

    ingress = """{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ include "piximind.fullname" . }}
  labels:
    {{- include "piximind.labels" . | nindent 4 }}
  {{- with .Values.ingress.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  ingressClassName: {{ .Values.ingress.className }}
  {{- with .Values.ingress.tls }}
  tls:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  rules:
    - host: {{ .Values.ingress.host | quote }}
      http:
        paths:
          - path: {{ .Values.ingress.path }}
            pathType: {{ .Values.ingress.pathType }}
            backend:
              service:
                name: {{ include "piximind.fullname" . }}
                port:
                  name: http
{{- end }}
"""

    files = [
        (
            "helm_chart",
            "Chart.yaml",
            chart_yaml,
        ),
        (
            "helm_values",
            "values.yaml",
            values_yaml,
        ),
        (
            "helm_values",
            (
                f"values-"
                f"{environment_code}.yaml"
            ),
            environment_values,
        ),
        (
            "helm_template",
            "templates/_helpers.tpl",
            helpers,
        ),
        (
            "helm_template",
            "templates/deployment.yaml",
            deployment,
        ),
        (
            "helm_template",
            "templates/service.yaml",
            service,
        ),
        (
            "helm_template",
            "templates/ingress.yaml",
            ingress,
        ),
    ]

    return [
        make_artifact(
            component_id=component_id,

            artifact_type=
                artifact_type,

            relative_path=(
                f"{chart_root}/"
                f"{file_name}"
            ),

            content=content,

            artifact_status=
                "generated",

            metadata={
                "componentName":
                    component["name"],

                "chartRoot":
                    chart_root,

                "environment":
                    context[
                        "environment_name"
                    ],
            },
        )

        for (
            artifact_type,
            file_name,
            content,
        ) in files
    ]


def generate_argocd_application(
    *,
    context: dict[str, Any],
    component: dict[str, Any],
    project_slug: str,
    environment_code: str,
    version: str,
) -> GeneratedArtifact:
    component_slug = sanitize_name(
        str(component["name"])
    )

    application_name = sanitize_name(
        (
            f"{project_slug}-"
            f"{component_slug}-"
            f"{environment_code}"
        )
    )

    chart_path = (
        f"projects/{project_slug}/"
        f"{environment_code}/"
        f"{component_slug}"
    )

    gitops_repository = str(
        current_app.config.get(
            "GITOPS_REPOSITORY_URL",
            "",
        )
    ).strip()

    target_revision = str(
        current_app.config.get(
            "GITOPS_TARGET_REVISION",
            "main",
        )
    )

    argocd_project = str(
        current_app.config.get(
            "ARGOCD_PROJECT",
            "sapixi",
        )
    )

    argocd_namespace = str(
        current_app.config.get(
            "ARGOCD_NAMESPACE",
            "argocd",
        )
    )

    destination_server = str(
        current_app.config.get(
            "KUBERNETES_SERVER",
            (
                "https://"
                "kubernetes.default.svc"
            ),
        )
    )

    repository_value = (
        gitops_repository
        or (
            "REPLACE_WITH_"
            "GITOPS_REPOSITORY_URL"
        )
    )

    needs_review = not bool(
        gitops_repository
    )

    content = f"""apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {application_name}
  namespace: {argocd_namespace}
  labels:
    app.kubernetes.io/managed-by: piximind
    piximind.io/project: {project_slug}
    piximind.io/component: {component_slug}
spec:
  project: {argocd_project}
  source:
    repoURL: {repository_value}
    targetRevision: {target_revision}
    path: {chart_path}
    helm:
      valueFiles:
        - values.yaml
        - values-{environment_code}.yaml
  destination:
    server: {destination_server}
    namespace: {context['environment_namespace']}
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
"""

    return make_artifact(
        component_id=int(
            component["id"]
        ),

        artifact_type=
            "argocd_application",

        relative_path=(
            f"projects/{project_slug}/"
            f"{environment_code}/"
            "argocd/"
            f"{component_slug}"
            "-application.yaml"
        ),

        content=content,

        artifact_status=(
            "needs_review"
            if needs_review
            else "generated"
        ),

        metadata={
            "componentName":
                component["name"],

            "applicationName":
                application_name,

            "chartPath":
                chart_path,

            "version":
                version,

            "gitopsRepositoryConfigured":
                bool(
                    gitops_repository
                ),
        },
    )


def read_existing_chart(
    *,
    chart_directory: Path,
    chart_relative_path: str,
    component_id: int,
    component_name: str,
) -> list[GeneratedArtifact]:
    if (
        not chart_directory.exists()
        or not chart_directory.is_dir()
        or not (
            chart_directory
            / "Chart.yaml"
        ).is_file()
    ):
        return []

    artifacts: list[
        GeneratedArtifact
    ] = []

    for file_path in sorted(
        chart_directory.rglob("*")
    ):
        if not file_path.is_file():
            continue

        relative_inside_chart = (
            file_path.relative_to(
                chart_directory
            ).as_posix()
        )

        if not is_supported_chart_file(
            relative_inside_chart
        ):
            continue

        try:
            content = read_text_file(
                file_path
            )

        except ValueError:
            continue

        artifact_type = (
            "helm_chart"
            if relative_inside_chart
            == "Chart.yaml"

            else (
                "helm_values"

                if relative_inside_chart
                .startswith("values")

                else "helm_template"
            )
        )

        artifacts.append(
            make_artifact(
                component_id=
                    component_id,

                artifact_type=
                    artifact_type,

                relative_path=
                    join_relative_path(
                        chart_relative_path,
                        relative_inside_chart,
                    ),

                content=
                    content,

                artifact_status=
                    "existing",

                metadata={
                    "componentName":
                        component_name,

                    "source":
                        "repository",

                    "chartRoot":
                        chart_relative_path,
                },
            )
        )

    return artifacts


def detect_angular_output_path(
    component_root: Path,
) -> str:
    angular_json_path = (
        component_root
        / "angular.json"
    )

    if not angular_json_path.is_file():
        return "dist"

    try:
        data = json.loads(
            angular_json_path.read_text(
                encoding="utf-8"
            )
        )

        projects = data.get(
            "projects"
        )

        if not isinstance(
            projects,
            dict,
        ):
            return "dist"

        for project in projects.values():
            if not isinstance(
                project,
                dict,
            ):
                continue

            architect = (
                project.get(
                    "architect"
                )
                or project.get(
                    "targets"
                )
                or {}
            )

            if not isinstance(
                architect,
                dict,
            ):
                continue

            build = architect.get(
                "build"
            )

            if not isinstance(
                build,
                dict,
            ):
                continue

            options = build.get(
                "options"
            )

            if not isinstance(
                options,
                dict,
            ):
                continue

            output_path = options.get(
                "outputPath"
            )

            if isinstance(
                output_path,
                str,
            ):
                return normalize_path(
                    output_path
                )

            if isinstance(
                output_path,
                dict,
            ):
                base = output_path.get(
                    "base"
                )

                browser = output_path.get(
                    "browser"
                )

                if isinstance(base, str):
                    if isinstance(
                        browser,
                        str,
                    ):
                        return (
                            join_relative_path(
                                base,
                                browser,
                            )
                        )

                    return normalize_path(
                        base
                    )

    except (
        OSError,
        ValueError,
        TypeError,
    ):
        return "dist"

    return "dist"


def node_dependency_copy(
    component_root: Path,
    package_manager: str,
) -> str:
    if (
        package_manager == "pnpm"

        or (
            component_root
            / "pnpm-lock.yaml"
        ).is_file()
    ):
        return (
            "COPY package.json "
            "pnpm-lock.yaml ./"
        )

    if (
        package_manager == "yarn"

        or (
            component_root
            / "yarn.lock"
        ).is_file()
    ):
        return (
            "COPY package.json "
            "yarn.lock ./"
        )

    if (
        component_root
        / "package-lock.json"
    ).is_file():
        return (
            "COPY package.json "
            "package-lock.json ./"
        )

    return "COPY package.json ./"


def node_install_command(
    component_root: Path,
    package_manager: str,
) -> str:
    if (
        package_manager == "pnpm"

        or (
            component_root
            / "pnpm-lock.yaml"
        ).is_file()
    ):
        return (
            "corepack enable && "
            "pnpm install "
            "--frozen-lockfile"
        )

    if (
        package_manager == "yarn"

        or (
            component_root
            / "yarn.lock"
        ).is_file()
    ):
        return (
            "corepack enable && "
            "yarn install "
            "--frozen-lockfile"
        )

    if (
        component_root
        / "package-lock.json"
    ).is_file():
        return "npm ci"

    return "npm install"


def node_build_command(
    package_manager: str,
) -> str:
    if package_manager == "pnpm":
        return "pnpm build"

    if package_manager == "yarn":
        return "yarn build"

    return "npm run build"


def node_start_command(
    package_manager: str,
) -> str:
    if package_manager == "pnpm":
        return "pnpm start"

    if package_manager == "yarn":
        return "yarn start"

    return "npm start"


def default_port(
    component_type: str,
) -> int:
    if component_type == "frontend":
        return 80

    return 5000


def make_artifact(
    *,
    component_id: int | None,
    artifact_type: str,
    relative_path: str,
    content: str,
    artifact_status: str,
    metadata: dict[str, Any],
    original_content: str | None = None,
) -> GeneratedArtifact:
    normalized_content = (
        content.replace(
            "\r\n",
            "\n",
        )
    )

    if not normalized_content.endswith(
        "\n"
    ):
        normalized_content += "\n"

    digest = hashlib.sha256(
        normalized_content.encode(
            "utf-8"
        )
    ).hexdigest()

    return GeneratedArtifact(
        component_id=component_id,

        artifact_type=
            artifact_type,

        relative_path=
            normalize_path(
                relative_path
            ),

        content=
            normalized_content,

        original_content=
            original_content,

        content_sha256=
            digest,

        artifact_status=
            artifact_status,

        metadata=
            metadata,
    )


def resolve_inside_root(
    source_root: Path,
    relative_path: str,
) -> Path:
    normalized = normalize_path(
        relative_path
    )

    if normalized in {
        "",
        ".",
    }:
        return source_root.resolve()

    candidate = (
        source_root / normalized
    ).resolve()

    try:
        candidate.relative_to(
            source_root.resolve()
        )

    except ValueError as error:
        raise ValueError(
            (
                "Un chemin de composant "
                "sort du workspace autorisé."
            )
        ) from error

    return candidate


def read_text_file(
    file_path: Path,
) -> str:
    if (
        file_path.stat().st_size
        > TEXT_FILE_MAX_BYTES
    ):
        raise ValueError(
            (
                f"Le fichier "
                f"{file_path.name} "
                "est trop volumineux."
            )
        )

    try:
        return file_path.read_text(
            encoding="utf-8"
        )

    except UnicodeDecodeError as error:
        raise ValueError(
            (
                f"Le fichier "
                f"{file_path.name} "
                "n'est pas un fichier "
                "texte UTF-8."
            )
        ) from error


def sanitize_name(
    value: str,
) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+",
        "-",
        value.lower().strip(),
    ).strip("-")

    return (
        normalized[:63]
        or "application"
    )


def normalize_path(
    value: str,
) -> str:
    normalized = (
        value.replace(
            "\\",
            "/",
        )
        .strip("/")
    )

    return normalized or "."


def join_relative_path(
    *parts: str,
) -> str:
    normalized_parts = [
        normalize_path(part)

        for part in parts

        if part
        and normalize_path(part)
            != "."
    ]

    return (
        "/".join(
            normalized_parts
        )
        or "."
    )


def is_supported_chart_file(
    relative_path: str,
) -> bool:
    lower = relative_path.lower()

    return (
        (
            lower == "chart.yaml"

            or lower.startswith(
                "values"
            )

            or lower.startswith(
                "templates/"
            )
        )

        and lower.endswith(
            (
                ".yaml",
                ".yml",
                ".tpl",
            )
        )
    )