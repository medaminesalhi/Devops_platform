from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

import requests
from flask import current_app

from app.integrations.security import decrypt_credential


class DiagnosticProviderError(RuntimeError):
    pass


SENSITIVE_PATTERN = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|private[_-]?key|authorization)"
    r"\s*[:=]\s*([^\s,;]+)"
)


def sanitize_text(value: str, maximum: int = 12000) -> str:
    cleaned = SENSITIVE_PATTERN.sub(r"\1=[REDACTED]", value)
    cleaned = re.sub(
        r"(?i)bearer\s+[a-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        cleaned,
    )
    return cleaned[-maximum:]


def _extract_json(text: str) -> dict[str, Any]:
    normalized = text.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized)
        normalized = re.sub(r"\s*```$", "", normalized)
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start < 0 or end <= start:
            raise DiagnosticProviderError(
                "Le provider IA n'a pas retourné un objet JSON exploitable."
            )
        try:
            parsed = json.loads(normalized[start : end + 1])
        except json.JSONDecodeError as error:
            raise DiagnosticProviderError(
                "La réponse JSON du provider IA est invalide."
            ) from error
    if not isinstance(parsed, dict):
        raise DiagnosticProviderError(
            "La réponse du provider IA doit être un objet JSON."
        )
    return parsed


def _normalize_corrections(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        target_phase = str(item.get("targetPhase") or "deployment")
        if target_phase not in {
            "integration",
            "analysis",
            "proposal",
            "generation",
            "deployment",
        }:
            target_phase = "deployment"
        risk = str(item.get("risk") or "medium")
        if risk not in {"low", "medium", "high"}:
            risk = "medium"
        title = str(item.get("title") or "Correction proposée").strip()[:220]
        summary = str(item.get("summary") or "").strip()[:4000]
        if not summary:
            continue
        result.append(
            {
                "title": title,
                "summary": summary,
                "target_phase": target_phase,
                "target_file": (
                    str(item.get("targetFile")).strip()[:1000]
                    if item.get("targetFile")
                    else None
                ),
                "diff": (
                    str(item.get("diff"))[:12000]
                    if item.get("diff")
                    else None
                ),
                "risk": risk,
            }
        )
    return result


def normalize_diagnostic(payload: dict[str, Any]) -> dict[str, Any]:
    confidence = str(payload.get("confidence") or "medium")
    if confidence not in {"low", "medium", "high"}:
        confidence = "medium"

    target_phase = str(payload.get("targetPhase") or "deployment")
    if target_phase not in {
        "integration",
        "analysis",
        "proposal",
        "generation",
        "deployment",
    }:
        target_phase = "deployment"

    evidence_value = payload.get("evidence")
    evidence = (
        [str(item)[:1000] for item in evidence_value[:12]]
        if isinstance(evidence_value, list)
        else []
    )

    return {
        "cause": str(payload.get("cause") or "Cause non déterminée.")[:4000],
        "explanation": str(
            payload.get("explanation")
            or "Les informations disponibles ne suffisent pas pour une explication détaillée."
        )[:8000],
        "confidence": confidence,
        "target_phase": target_phase,
        "evidence": evidence,
        "corrections": _normalize_corrections(payload.get("corrections")),
        "raw": payload,
    }


def fallback_diagnostic(
    *,
    incident: dict[str, Any],
    logs: list[dict[str, Any]],
) -> dict[str, Any]:
    code = str(incident.get("code") or "DEPLOYMENT_FAILED")
    integration_name = incident.get("integration_name")

    if code in {
        "REGISTRY_AUTHENTICATION_FAILED",
        "REGISTRY_LOGIN_FAILED",
    }:
        return {
            "cause": (
                "Le registre Nexus a refusé le credential utilisé pour publier l’image."
            ),
            "explanation": (
                "La construction Docker est terminée et l’échec apparaît pendant "
                "l’authentification ou le push vers le registre. Vérifiez le username, "
                "le token, les droits du repository Docker et l’adresse exacte du registre."
            ),
            "confidence": "high",
            "target_phase": "integration",
            "evidence": [
                f"Code d’incident : {code}",
                f"Intégration concernée : {integration_name or 'registre'}",
                "L’étape ayant échoué est Publication Nexus.",
            ],
            "corrections": [
                {
                    "title": "Remplacer ou corriger le credential Nexus",
                    "summary": (
                        "Ouvrez l’intégration du registre, enregistrez un credential "
                        "disposant des droits push, testez la connexion puis relancez "
                        "depuis l’étape Publication Nexus."
                    ),
                    "target_phase": "integration",
                    "target_file": None,
                    "diff": None,
                    "risk": "low",
                }
            ],
            "raw": {"fallback": True},
        }

    if code in {
        "HELM_INVALID",
        "KUBERNETES_APPLY_FAILED",
        "APPLICATION_UNHEALTHY",
        "PROBE_FAILED",
    }:
        return {
            "cause": (
                "Un artefact de déploiement ou un paramètre Kubernetes est probablement incorrect."
            ),
            "explanation": (
                "La correction doit créer une nouvelle révision de génération. "
                "La version déjà approuvée et exécutée ne doit pas être modifiée silencieusement."
            ),
            "confidence": "medium",
            "target_phase": "generation",
            "evidence": [
                f"Code d’incident : {code}",
                sanitize_text(str(incident.get("message") or ""), 1000),
            ],
            "corrections": [
                {
                    "title": "Créer une révision de correction",
                    "summary": (
                        "Retournez à Génération et validation, corrigez l’artefact indiqué, "
                        "relancez les validations puis créez un nouveau déploiement."
                    ),
                    "target_phase": "generation",
                    "target_file": None,
                    "diff": None,
                    "risk": "medium",
                }
            ],
            "raw": {"fallback": True},
        }

    log_evidence = [
        sanitize_text(str(item.get("message") or ""), 500)
        for item in logs[-5:]
        if item.get("level") == "error"
    ]
    return {
        "cause": str(incident.get("title") or "Le pipeline de déploiement a échoué."),
        "explanation": sanitize_text(
            str(incident.get("message") or "Consultez les logs de l’étape échouée."),
            3000,
        ),
        "confidence": "medium",
        "target_phase": "deployment",
        "evidence": log_evidence or [f"Code d’incident : {code}"],
        "corrections": [
            {
                "title": "Examiner l’étape échouée",
                "summary": (
                    "Corrigez la cause indiquée dans les logs puis relancez depuis "
                    "l’étape échouée lorsque l’incident est temporaire."
                ),
                "target_phase": "deployment",
                "target_file": None,
                "diff": None,
                "risk": "low",
            }
        ],
        "raw": {"fallback": True},
    }


class DeploymentAiClient:
    def __init__(self, connection: dict[str, Any]) -> None:
        self.connection = connection
        self.base_url = str(connection.get("base_url") or "").rstrip("/")
        self.provider_type = str(connection.get("provider_type") or "").lower()
        self.verify_ssl = bool(connection.get("verify_ssl", True))
        self.secret = decrypt_credential(connection.get("secret_ciphertext"))
        self.username = connection.get("username")
        self.timeout = int(
            current_app.config.get("DEPLOYMENT_AI_TIMEOUT_SECONDS", 90)
        )
        self.model = str(
            current_app.config.get("DEPLOYMENT_AI_MODEL", "llama3.1:8b")
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        return headers

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise DiagnosticProviderError("L’URL du provider IA est absente.")

        if self.provider_type == "ollama":
            response = requests.post(
                urljoin(self.base_url + "/", "api/chat"),
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                headers=self._headers(),
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            body = response.json()
            content = (
                body.get("message", {}).get("content")
                if isinstance(body, dict)
                else None
            )
        else:
            endpoint = (
                self.base_url
                if self.base_url.endswith("/chat/completions")
                else urljoin(self.base_url + "/", "v1/chat/completions")
            )
            response = requests.post(
                endpoint,
                json={
                    "model": self.model,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                headers=self._headers(),
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            body = response.json()
            choices = body.get("choices") if isinstance(body, dict) else None
            content = (
                choices[0].get("message", {}).get("content")
                if isinstance(choices, list) and choices
                else None
            )

        if not isinstance(content, str) or not content.strip():
            raise DiagnosticProviderError("Le provider IA a retourné une réponse vide.")
        return _extract_json(content)

    def complete_text(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> str:
        if self.provider_type == "ollama":
            response = requests.post(
                urljoin(self.base_url + "/", "api/chat"),
                json={
                    "model": self.model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        *messages,
                    ],
                },
                headers=self._headers(),
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            body = response.json()
            content = body.get("message", {}).get("content")
        else:
            endpoint = (
                self.base_url
                if self.base_url.endswith("/chat/completions")
                else urljoin(self.base_url + "/", "v1/chat/completions")
            )
            response = requests.post(
                endpoint,
                json={
                    "model": self.model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        *messages,
                    ],
                },
                headers=self._headers(),
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
            response.raise_for_status()
            body = response.json()
            choices = body.get("choices") if isinstance(body, dict) else None
            content = (
                choices[0].get("message", {}).get("content")
                if isinstance(choices, list) and choices
                else None
            )
        if not isinstance(content, str) or not content.strip():
            raise DiagnosticProviderError("Le provider IA a retourné une réponse vide.")
        return sanitize_text(content.strip(), 12000)


def diagnose_with_ai(
    *,
    ai_connection: dict[str, Any] | None,
    deployment: dict[str, Any],
    incident: dict[str, Any],
    logs: list[dict[str, Any]],
    resources: list[dict[str, Any]],
) -> dict[str, Any]:
    if ai_connection is None:
        return fallback_diagnostic(incident=incident, logs=logs)

    safe_logs = [
        {
            "scope": item.get("scope"),
            "level": item.get("level"),
            "message": sanitize_text(str(item.get("message") or ""), 1200),
        }
        for item in logs[-80:]
    ]
    safe_resources = [
        {
            "kind": item.get("kind"),
            "name": item.get("name"),
            "status": item.get("status"),
            "health": item.get("health"),
            "message": sanitize_text(str(item.get("message") or ""), 600),
        }
        for item in resources[-80:]
    ]

    system_prompt = (
        "Vous êtes l’assistant de diagnostic de SApixi. Analysez uniquement "
        "les informations fournies. Ne demandez jamais de token, mot de passe, "
        "clé privée ou valeur de secret. Ne proposez aucune modification directe "
        "de production. Retournez uniquement un objet JSON avec les clés : "
        "cause, explanation, confidence, targetPhase, evidence, corrections. "
        "confidence vaut low, medium ou high. targetPhase vaut integration, "
        "analysis, proposal, generation ou deployment. corrections est une liste "
        "d’objets title, summary, targetPhase, targetFile, diff, risk."
    )
    user_prompt = json.dumps(
        {
            "deployment": {
                "id": deployment.get("id"),
                "project": deployment.get("project_name"),
                "version": deployment.get("version"),
                "environment": deployment.get("environment_name"),
                "namespace": deployment.get("namespace"),
                "stage": deployment.get("current_stage"),
            },
            "incident": {
                "code": incident.get("code"),
                "title": incident.get("title"),
                "message": sanitize_text(str(incident.get("message") or ""), 3000),
                "stage": incident.get("stage"),
                "component": incident.get("component_name"),
                "integration": incident.get("integration_name"),
            },
            "logs": safe_logs,
            "resources": safe_resources,
        },
        ensure_ascii=False,
    )

    try:
        client = DeploymentAiClient(ai_connection)
        payload = client.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        result = normalize_diagnostic(payload)
        result["provider_connection_id"] = ai_connection.get("id")
        result["model"] = client.model
        return result
    except Exception as error:
        current_app.logger.exception("Diagnostic IA indisponible.")
        result = fallback_diagnostic(incident=incident, logs=logs)
        result["raw"] = {
            "fallback": True,
            "providerError": sanitize_text(str(error), 1000),
        }
        return result


def chat_with_ai(
    *,
    ai_connection: dict[str, Any] | None,
    deployment: dict[str, Any],
    incident: dict[str, Any] | None,
    diagnostic: dict[str, Any] | None,
    messages: list[dict[str, Any]],
) -> str:
    if ai_connection is None:
        return (
            "Le provider IA de cet environnement est indisponible. "
            "Le diagnostic déterministe reste visible. Corrigez l’intégration "
            "ou appliquez la procédure proposée, puis relancez le déploiement."
        )

    system_prompt = (
        "Vous êtes l’assistant interactif de SApixi pour une exécution de "
        "déploiement précise. Expliquez les preuves, comparez les solutions et "
        "préparez des corrections contrôlées. Ne demandez ni ne révélez jamais "
        "de secrets. N’affirmez pas qu’une correction a été appliquée. Toute "
        "modification exige une validation humaine et une nouvelle révision. "
        f"Projet : {deployment.get('project_name')}. "
        f"Incident : {sanitize_text(str((incident or {}).get('message') or 'aucun'), 1500)}. "
        f"Diagnostic : {sanitize_text(str((diagnostic or {}).get('cause') or 'non disponible'), 1500)}."
    )
    history = [
        {
            "role": item["role"] if item["role"] in {"assistant", "user"} else "assistant",
            "content": sanitize_text(str(item.get("content") or ""), 4000),
        }
        for item in messages[-20:]
        if item.get("role") in {"assistant", "user", "system"}
    ]
    try:
        client = DeploymentAiClient(ai_connection)
        return client.complete_text(
            system_prompt=system_prompt,
            messages=history,
        )
    except Exception:
        current_app.logger.exception("Conversation IA indisponible.")
        return (
            "Je ne peux pas joindre le provider IA pour le moment. "
            "Le diagnostic déjà enregistré reste disponible. Vérifiez la connexion "
            "IA de l’environnement puis réessayez."
        )
