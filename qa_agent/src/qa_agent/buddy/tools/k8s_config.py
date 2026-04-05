"""K8s config tools — inspect ConfigMaps, Secrets metadata, rollout history, resource quotas, env vars."""

from __future__ import annotations

import logging
from typing import Any

from qa_agent.buddy.tool import BaseTool, RiskLevel, ToolResult

log = logging.getLogger("qa_agent.buddy.tools.k8s_config")


def _k8s_client():
    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        return client
    except ImportError:
        raise RuntimeError("kubernetes package not installed. Run: pip install kubernetes")


# ─────────────────────────────────────────────
# ConfigMap tools
# ─────────────────────────────────────────────

class ListConfigMapsTool(BaseTool):
    name = "k8s_list_configmaps"
    description = "List all ConfigMaps in a namespace with their keys (not values)."
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
        },
        "required": ["namespace"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            cms = v1.list_namespaced_config_map(namespace=params["namespace"])
            return ToolResult(ok=True, data=[
                {
                    "name": cm.metadata.name,
                    "keys": list((cm.data or {}).keys()),
                    "binary_keys": list((cm.binary_data or {}).keys()),
                }
                for cm in cms.items
            ])
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class GetConfigMapTool(BaseTool):
    name = "k8s_get_configmap"
    description = (
        "Get the full contents of a ConfigMap (all key-value pairs). "
        "Use this to inspect runtime configuration, find missing keys, or verify config values."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "name": {"type": "string", "description": "ConfigMap name"},
        },
        "required": ["namespace", "name"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            cm = v1.read_namespaced_config_map(name=params["name"], namespace=params["namespace"])
            return ToolResult(ok=True, data={
                "name": cm.metadata.name,
                "namespace": cm.metadata.namespace,
                "data": cm.data or {},
                "binary_keys": list((cm.binary_data or {}).keys()),
            })
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class ListSecretsTool(BaseTool):
    name = "k8s_list_secrets"
    description = (
        "List Secrets in a namespace — returns secret names and their KEY NAMES only "
        "(values are never exposed). Use to check if a required secret exists and has the expected keys."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
        },
        "required": ["namespace"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            secrets = v1.list_namespaced_secret(namespace=params["namespace"])
            return ToolResult(ok=True, data=[
                {
                    "name": s.metadata.name,
                    "type": s.type,
                    "keys": list((s.data or {}).keys()),
                }
                for s in secrets.items
            ])
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class GetPodEnvVarsTool(BaseTool):
    name = "k8s_get_env_vars"
    description = (
        "Get all environment variables configured for a pod's container spec — "
        "including values set directly, references to ConfigMap keys, and Secret key references. "
        "Secret values are NOT shown, only their key names. "
        "Use this to find missing or misconfigured env vars that could cause runtime errors."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "pod_name": {"type": "string"},
            "container": {"type": "string", "description": "Container name (uses first container if omitted)"},
        },
        "required": ["namespace", "pod_name"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            pod = v1.read_namespaced_pod(name=params["pod_name"], namespace=params["namespace"])
            containers = pod.spec.containers or []
            if params.get("container"):
                containers = [ct for ct in containers if ct.name == params["container"]]
            if not containers:
                return ToolResult(ok=False, error="Container not found")

            container = containers[0]
            env_vars = []
            for e in (container.env or []):
                if e.value is not None:
                    env_vars.append({"name": e.name, "value": e.value, "source": "literal"})
                elif e.value_from:
                    if e.value_from.config_map_key_ref:
                        ref = e.value_from.config_map_key_ref
                        env_vars.append({
                            "name": e.name,
                            "source": "configmap",
                            "configmap": ref.name,
                            "key": ref.key,
                            "optional": ref.optional,
                        })
                    elif e.value_from.secret_key_ref:
                        ref = e.value_from.secret_key_ref
                        env_vars.append({
                            "name": e.name,
                            "source": "secret",
                            "secret": ref.name,
                            "key": ref.key,
                            "optional": ref.optional,
                            "value": "<redacted>",
                        })
                    elif e.value_from.field_ref:
                        env_vars.append({
                            "name": e.name,
                            "source": "fieldRef",
                            "field_path": e.value_from.field_ref.field_path,
                        })

            # Also collect envFrom (entire ConfigMap/Secret mounted as env)
            env_from = []
            for ef in (container.env_from or []):
                if ef.config_map_ref:
                    env_from.append({"type": "configmap", "name": ef.config_map_ref.name,
                                     "optional": ef.config_map_ref.optional, "prefix": ef.prefix})
                elif ef.secret_ref:
                    env_from.append({"type": "secret", "name": ef.secret_ref.name,
                                     "optional": ef.secret_ref.optional, "prefix": ef.prefix})

            return ToolResult(ok=True, data={
                "pod": params["pod_name"],
                "container": container.name,
                "env_vars": env_vars,
                "env_from": env_from,
                "total_env_count": len(env_vars),
            })
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


# ─────────────────────────────────────────────
# Rollout & history tools
# ─────────────────────────────────────────────

class GetRolloutHistoryTool(BaseTool):
    name = "k8s_rollout_history"
    description = (
        "Get the rollout history of a Deployment — shows revision numbers, "
        "change cause annotations, and image versions. "
        "Use to identify what changed before an issue started."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "deployment": {"type": "string"},
        },
        "required": ["namespace", "deployment"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            apps = c.AppsV1Api()
            dep = apps.read_namespaced_deployment(name=params["deployment"], namespace=params["namespace"])

            # Get ReplicaSets owned by this deployment
            rs_list = apps.list_namespaced_replica_set(namespace=params["namespace"])
            owned_rs = []
            for rs in rs_list.items:
                for owner in (rs.metadata.owner_references or []):
                    if owner.kind == "Deployment" and owner.name == params["deployment"]:
                        revision = (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "?")
                        change_cause = (rs.metadata.annotations or {}).get("kubernetes.io/change-cause", "")
                        images = [c.image for c in (rs.spec.template.spec.containers or [])]
                        owned_rs.append({
                            "revision": revision,
                            "name": rs.metadata.name,
                            "replicas_desired": rs.spec.replicas or 0,
                            "replicas_ready": rs.status.ready_replicas or 0,
                            "images": images,
                            "change_cause": change_cause or "(none)",
                            "created": str(rs.metadata.creation_timestamp),
                        })

            owned_rs.sort(key=lambda x: str(x["revision"]), reverse=True)

            current_revision = (dep.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "?")
            return ToolResult(ok=True, data={
                "deployment": params["deployment"],
                "namespace": params["namespace"],
                "current_revision": current_revision,
                "history": owned_rs,
            })
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


# ─────────────────────────────────────────────
# Resource quota & limits
# ─────────────────────────────────────────────

class GetResourceQuotaTool(BaseTool):
    name = "k8s_get_resource_quota"
    description = (
        "Get resource quotas and current usage for a namespace. "
        "Use to diagnose OOM kills, pending pods, or deployment failures caused by resource limits."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
        },
        "required": ["namespace"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            quotas = v1.list_namespaced_resource_quota(namespace=params["namespace"])
            result = []
            for q in quotas.items:
                hard = q.status.hard or {}
                used = q.status.used or {}
                resources = []
                for key in sorted(set(list(hard.keys()) + list(used.keys()))):
                    h = hard.get(key, "unlimited")
                    u = used.get(key, "0")
                    resources.append({"resource": key, "used": u, "limit": h})
                result.append({
                    "name": q.metadata.name,
                    "resources": resources,
                })
            if not result:
                return ToolResult(ok=True, data={"namespace": params["namespace"],
                                                  "quotas": [], "note": "No resource quotas defined."})
            return ToolResult(ok=True, data={"namespace": params["namespace"], "quotas": result})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class GetPodResourcesTool(BaseTool):
    name = "k8s_get_pod_resources"
    description = (
        "Get CPU and memory requests/limits for all containers in a pod. "
        "Use to diagnose OOM kills or CPU throttling. "
        "Compare requests vs limits to find misconfigured resource specs."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "pod_name": {"type": "string"},
        },
        "required": ["namespace", "pod_name"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            pod = v1.read_namespaced_pod(name=params["pod_name"], namespace=params["namespace"])
            containers = []
            for ct in (pod.spec.containers or []):
                res = ct.resources or {}
                requests = {}
                limits = {}
                if hasattr(res, "requests") and res.requests:
                    requests = dict(res.requests)
                if hasattr(res, "limits") and res.limits:
                    limits = dict(res.limits)
                containers.append({
                    "container": ct.name,
                    "requests": requests,
                    "limits": limits,
                    "issues": _check_resource_issues(requests, limits),
                })
            return ToolResult(ok=True, data={
                "pod": params["pod_name"],
                "namespace": params["namespace"],
                "containers": containers,
            })
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


def _check_resource_issues(requests: dict, limits: dict) -> list[str]:
    issues = []
    if not requests.get("memory"):
        issues.append("No memory request set — scheduler cannot make good placement decisions")
    if not limits.get("memory"):
        issues.append("No memory limit set — pod can consume unbounded memory (OOM risk)")
    if not requests.get("cpu"):
        issues.append("No CPU request set")
    if not limits.get("cpu"):
        issues.append("No CPU limit set — noisy neighbor risk")
    return issues


def all_k8s_config_tools() -> list[BaseTool]:
    return [
        ListConfigMapsTool(),
        GetConfigMapTool(),
        ListSecretsTool(),
        GetPodEnvVarsTool(),
        GetRolloutHistoryTool(),
        GetResourceQuotaTool(),
        GetPodResourcesTool(),
    ]
