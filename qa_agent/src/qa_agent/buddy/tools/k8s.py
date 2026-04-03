"""Kubernetes tools — read and write operations against any namespace."""

from __future__ import annotations

import logging
from typing import Any

from qa_agent.buddy.tool import BaseTool, RiskLevel, ToolResult

log = logging.getLogger("qa_agent.buddy.tools.k8s")


def _k8s_client():
    """Load K8s config — in-cluster first, then kubeconfig."""
    try:
        from kubernetes import client, config as k8s_config
        try:
            k8s_config.load_incluster_config()
        except Exception:
            k8s_config.load_kube_config()
        return client
    except ImportError:
        raise RuntimeError("kubernetes package not installed. Run: pip install kubernetes")


def _fmt_pod(pod: Any) -> dict:
    cs = pod.status.container_statuses or []
    return {
        "name": pod.metadata.name,
        "namespace": pod.metadata.namespace,
        "phase": pod.status.phase,
        "ready": all(c.ready for c in cs) if cs else False,
        "restarts": sum(c.restart_count for c in cs),
        "node": pod.spec.node_name,
        "age": str(pod.metadata.creation_timestamp),
    }


# ─────────────────────────────────────────────
# READ tools
# ─────────────────────────────────────────────

class ListNamespacesTool(BaseTool):
    name = "k8s_list_namespaces"
    description = "List all Kubernetes namespaces in the cluster."
    risk_level = RiskLevel.READ
    input_schema = {"type": "object", "properties": {}, "required": []}

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            ns_list = v1.list_namespace()
            namespaces = [
                {"name": ns.metadata.name, "status": ns.status.phase}
                for ns in ns_list.items
            ]
            return ToolResult(ok=True, data=namespaces)
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class ListPodsTool(BaseTool):
    name = "k8s_list_pods"
    description = (
        "List pods in a Kubernetes namespace. Use namespace='all' to list across all namespaces. "
        "Optionally filter by label_selector (e.g. 'app=nova-api')."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string", "description": "Namespace name or 'all'"},
            "label_selector": {"type": "string", "description": "Optional label selector"},
        },
        "required": ["namespace"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            ns = params.get("namespace", "default")
            sel = params.get("label_selector")
            kwargs = {"label_selector": sel} if sel else {}
            if ns == "all":
                pods = v1.list_pod_for_all_namespaces(**kwargs)
            else:
                pods = v1.list_namespaced_pod(namespace=ns, **kwargs)
            return ToolResult(ok=True, data=[_fmt_pod(p) for p in pods.items])
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class GetPodLogsTool(BaseTool):
    name = "k8s_get_logs"
    description = (
        "Fetch recent logs from a pod container. "
        "Set tail_lines to control how many lines to return (default 100). "
        "Use filter_text to grep for specific strings."
    )
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "pod_name": {"type": "string"},
            "container": {"type": "string", "description": "Container name (optional)"},
            "tail_lines": {"type": "integer", "default": 100},
            "filter_text": {"type": "string", "description": "Filter lines containing this text"},
            "previous": {"type": "boolean", "description": "Fetch logs from previous container instance"},
        },
        "required": ["namespace", "pod_name"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            kwargs: dict = {
                "namespace": params["namespace"],
                "name": params["pod_name"],
                "tail_lines": params.get("tail_lines", 100),
            }
            if params.get("container"):
                kwargs["container"] = params["container"]
            if params.get("previous"):
                kwargs["previous"] = True
            logs: str = v1.read_namespaced_pod_log(**kwargs) or ""
            if params.get("filter_text"):
                ft = params["filter_text"].lower()
                lines = [l for l in logs.splitlines() if ft in l.lower()]
                logs = "\n".join(lines)
            return ToolResult(ok=True, data={"pod": params["pod_name"], "logs": logs})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class GetEventsTool(BaseTool):
    name = "k8s_get_events"
    description = "Get Kubernetes events for a namespace or a specific resource (pod/deployment/node)."
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "resource_name": {"type": "string", "description": "Filter by involved object name"},
            "event_type": {"type": "string", "enum": ["Warning", "Normal", ""], "default": ""},
        },
        "required": ["namespace"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            ns = params["namespace"]
            events = v1.list_namespaced_event(namespace=ns)
            results = []
            for e in events.items:
                if params.get("resource_name") and params["resource_name"] not in (e.involved_object.name or ""):
                    continue
                if params.get("event_type") and e.type != params["event_type"]:
                    continue
                results.append({
                    "type": e.type,
                    "reason": e.reason,
                    "message": e.message,
                    "object": f"{e.involved_object.kind}/{e.involved_object.name}",
                    "count": e.count,
                    "last_seen": str(e.last_timestamp),
                })
            results.sort(key=lambda x: x["last_seen"] or "", reverse=True)
            return ToolResult(ok=True, data=results[:50])
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class DescribePodTool(BaseTool):
    name = "k8s_describe_pod"
    description = "Get detailed information about a specific pod (status, conditions, containers, volumes)."
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
            cs = pod.status.container_statuses or []
            containers = []
            for c_status in cs:
                state = {}
                if c_status.state.running:
                    state = {"running": True, "started_at": str(c_status.state.running.started_at)}
                elif c_status.state.waiting:
                    state = {"waiting": c_status.state.waiting.reason, "message": c_status.state.waiting.message}
                elif c_status.state.terminated:
                    state = {"terminated": c_status.state.terminated.reason, "exit_code": c_status.state.terminated.exit_code}
                containers.append({
                    "name": c_status.name,
                    "ready": c_status.ready,
                    "restarts": c_status.restart_count,
                    "image": c_status.image,
                    "state": state,
                })
            conditions = [
                {"type": cond.type, "status": cond.status, "reason": cond.reason}
                for cond in (pod.status.conditions or [])
            ]
            return ToolResult(ok=True, data={
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "phase": pod.status.phase,
                "node": pod.spec.node_name,
                "ip": pod.status.pod_ip,
                "conditions": conditions,
                "containers": containers,
                "labels": pod.metadata.labels or {},
            })
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class ListDeploymentsTool(BaseTool):
    name = "k8s_list_deployments"
    description = "List deployments in a namespace with replica counts and availability status."
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
            apps = c.AppsV1Api()
            deps = apps.list_namespaced_deployment(namespace=params["namespace"])
            return ToolResult(ok=True, data=[
                {
                    "name": d.metadata.name,
                    "desired": d.spec.replicas,
                    "ready": d.status.ready_replicas or 0,
                    "available": d.status.available_replicas or 0,
                    "image": d.spec.template.spec.containers[0].image if d.spec.template.spec.containers else "",
                }
                for d in deps.items
            ])
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class ListServicesTool(BaseTool):
    name = "k8s_list_services"
    description = "List services in a namespace with their type, cluster IP, and ports."
    risk_level = RiskLevel.READ
    input_schema = {
        "type": "object",
        "properties": {"namespace": {"type": "string"}},
        "required": ["namespace"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            v1 = c.CoreV1Api()
            svcs = v1.list_namespaced_service(namespace=params["namespace"])
            return ToolResult(ok=True, data=[
                {
                    "name": s.metadata.name,
                    "type": s.spec.type,
                    "cluster_ip": s.spec.cluster_ip,
                    "external_ip": (s.status.load_balancer.ingress or [{}])[0].get("ip", "") if s.status.load_balancer else "",
                    "ports": [f"{p.port}/{p.protocol}" for p in (s.spec.ports or [])],
                }
                for s in svcs.items
            ])
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


# ─────────────────────────────────────────────
# WRITE tools
# ─────────────────────────────────────────────

class RestartPodTool(BaseTool):
    name = "k8s_restart_pod"
    description = (
        "Delete a pod to trigger a restart (the deployment controller will recreate it). "
        "Use when a pod is stuck, OOMKilled, or in CrashLoopBackOff."
    )
    risk_level = RiskLevel.WRITE
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
            v1.delete_namespaced_pod(name=params["pod_name"], namespace=params["namespace"])
            return ToolResult(ok=True, data={"deleted": params["pod_name"], "namespace": params["namespace"],
                                              "note": "Pod deleted. Deployment controller will recreate it."})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class ScaleDeploymentTool(BaseTool):
    name = "k8s_scale_deployment"
    description = "Scale a Kubernetes deployment to the specified number of replicas."
    risk_level = RiskLevel.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "deployment": {"type": "string"},
            "replicas": {"type": "integer", "minimum": 0},
        },
        "required": ["namespace", "deployment", "replicas"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            c = _k8s_client()
            apps = c.AppsV1Api()
            body = {"spec": {"replicas": params["replicas"]}}
            apps.patch_namespaced_deployment_scale(
                name=params["deployment"],
                namespace=params["namespace"],
                body=body,
            )
            return ToolResult(ok=True, data={
                "deployment": params["deployment"],
                "namespace": params["namespace"],
                "replicas": params["replicas"],
            })
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


class ExecInPodTool(BaseTool):
    name = "k8s_exec"
    description = "Execute a shell command inside a running pod. Returns stdout and stderr."
    risk_level = RiskLevel.WRITE
    input_schema = {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "pod_name": {"type": "string"},
            "container": {"type": "string"},
            "command": {"type": "string", "description": "Shell command to run (e.g. 'ls /var/log')"},
        },
        "required": ["namespace", "pod_name", "command"],
    }

    def execute(self, params: dict) -> ToolResult:
        try:
            from kubernetes import stream
            c = _k8s_client()
            v1 = c.CoreV1Api()
            cmd = ["/bin/sh", "-c", params["command"]]
            kwargs: dict = {"name": params["pod_name"], "namespace": params["namespace"],
                            "command": cmd, "stderr": True, "stdin": False,
                            "stdout": True, "tty": False}
            if params.get("container"):
                kwargs["container"] = params["container"]
            resp = stream.stream(v1.connect_get_namespaced_pod_exec, **kwargs)
            return ToolResult(ok=True, data={"output": resp})
        except Exception as e:
            return ToolResult(ok=False, error=str(e))


def all_k8s_tools() -> list[BaseTool]:
    return [
        ListNamespacesTool(),
        ListPodsTool(),
        GetPodLogsTool(),
        GetEventsTool(),
        DescribePodTool(),
        ListDeploymentsTool(),
        ListServicesTool(),
        RestartPodTool(),
        ScaleDeploymentTool(),
        ExecInPodTool(),
    ]
