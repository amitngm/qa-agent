"""
Platform domain knowledge — single source of truth for all 16 cloud platform features.

Used by:
- IntentRouter   (entity extraction, feature mapping)
- PromptLibrary  (injects test dimensions + risk into prompts)
- RAGEngine      (metadata filtering by feature)
- SelfCheck      (knows which sources are needed per feature)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FeatureSpec:
    name: str                           # canonical name
    aliases: list[str]                  # keywords that map to this feature
    domain: str                         # COMPUTE | STORAGE | NETWORKING | IDENTITY | DATA | PLATFORM
    risk_level: str                     # P0 | P1 | P2 | P3
    critical_flows: list[str]           # CRUD + key operations to always test
    known_failure_modes: list[str]      # most common production failure patterns
    test_dimensions: list[str]          # specific test areas for this feature
    api_prefix: str                     # typical REST prefix (for context)
    inter_dependencies: list[str]       # other features this one depends on


FEATURES: dict[str, FeatureSpec] = {

    # ──────────────── COMPUTE ────────────────

    "vm": FeatureSpec(
        name="Virtual Machine",
        aliases=["vm", "virtual machine", "instance", "compute instance", "server"],
        domain="COMPUTE",
        risk_level="P0",
        critical_flows=[
            "create VM with flavor + network + image",
            "start / stop / reboot VM",
            "delete VM",
            "attach volume to VM",
            "attach security group to VM",
            "console access to VM",
        ],
        known_failure_modes=[
            "nova-compute connection to message queue fails (RabbitMQ OOM)",
            "Keystone auth URL misconfigured — all VM operations 401",
            "No valid host found — resource quota exceeded silently",
            "VM stuck in BUILD state — hypervisor unreachable",
            "Volume attach fails after VM reaches ACTIVE",
            "Security group not applied on VM start",
        ],
        test_dimensions=[
            "valid flavor + image + network combinations",
            "invalid / missing network — clear error expected",
            "quota exceeded — 403 with quota error code",
            "concurrent create (2 VMs same name) — conflict handling",
            "VM state transitions: BUILDING → ACTIVE → STOPPED → DELETED",
            "delete while BUILDING — should handle gracefully",
            "auth: non-owner cannot delete another tenant's VM",
        ],
        api_prefix="/vms",
        inter_dependencies=["vpc", "volume", "security_group", "snapshot", "keycloak_auth"],
    ),

    "snapshot": FeatureSpec(
        name="VM Snapshot",
        aliases=["snapshot", "vm snapshot", "instance snapshot", "image snapshot"],
        domain="COMPUTE",
        risk_level="P1",
        critical_flows=[
            "create snapshot of running VM",
            "create snapshot of stopped VM",
            "restore VM from snapshot",
            "delete snapshot",
            "list snapshots per VM",
        ],
        known_failure_modes=[
            "snapshot creation silently fails when VM disk is full",
            "restore from snapshot fails if original flavor no longer available",
            "snapshot stuck in SAVING state — storage backend issue",
            "orphaned snapshot after VM deletion",
        ],
        test_dimensions=[
            "snapshot of running VM — data consistency",
            "snapshot of stopped VM — baseline",
            "restore from snapshot — VM reaches ACTIVE",
            "delete snapshot while restore in progress — blocked or error",
            "snapshot quota exceeded — clear error",
            "list snapshots — pagination with large sets",
        ],
        api_prefix="/snapshots",
        inter_dependencies=["vm", "volume", "object_storage"],
    ),

    "kubernetes": FeatureSpec(
        name="Kubernetes Cluster",
        aliases=["kubernetes", "k8s", "cluster", "kube", "managed kubernetes", "cks"],
        domain="COMPUTE",
        risk_level="P1",
        critical_flows=[
            "create K8s cluster (control plane + worker nodes)",
            "scale worker node pool",
            "upgrade cluster version",
            "delete cluster",
            "download kubeconfig",
            "add/remove node pool",
        ],
        known_failure_modes=[
            "cluster stuck in PROVISIONING — node registration timeout",
            "kubeconfig download returns expired token",
            "node pool scale-down leaves pods unscheduled",
            "version upgrade fails — etcd backup step skipped",
        ],
        test_dimensions=[
            "cluster creation end-to-end — PROVISIONING → ACTIVE",
            "kubeconfig valid — can connect to cluster API",
            "node pool scale up/down — nodes reach Ready",
            "cluster delete — all nodes and LBs cleaned up",
            "upgrade — existing workloads unaffected",
            "quota: max clusters per tenant enforced",
        ],
        api_prefix="/clusters",
        inter_dependencies=["vm", "vpc", "load_balancer", "volume"],
    ),

    # ──────────────── STORAGE ────────────────

    "volume": FeatureSpec(
        name="Volume",
        aliases=["volume", "block storage", "disk", "persistent disk", "pvc", "block device"],
        domain="STORAGE",
        risk_level="P0",
        critical_flows=[
            "create volume",
            "attach volume to VM",
            "detach volume from VM",
            "extend volume size",
            "delete volume",
            "create volume from snapshot",
        ],
        known_failure_modes=[
            "volume attach fails silently — iSCSI initiator issue on host",
            "extend volume fails when VM is running — driver limitation",
            "delete fails if volume still attached",
            "volume stuck in attaching state — hypervisor unreachable",
        ],
        test_dimensions=[
            "create with valid size + type",
            "attach to running VM — accessible inside VM",
            "detach — VM continues running, volume goes AVAILABLE",
            "extend — new size reflected inside VM after rescan",
            "delete attached volume — blocked with clear error",
            "create from snapshot — data preserved",
            "volume type: SSD vs HDD — correct backend",
        ],
        api_prefix="/volumes",
        inter_dependencies=["vm", "snapshot"],
    ),

    "volume_snapshot": FeatureSpec(
        name="Volume Snapshot",
        aliases=["volume snapshot", "disk snapshot", "block snapshot"],
        domain="STORAGE",
        risk_level="P1",
        critical_flows=[
            "create snapshot of volume",
            "create volume from snapshot",
            "delete snapshot",
        ],
        known_failure_modes=[
            "snapshot of attached volume — data inconsistency without quiesce",
            "create volume from snapshot — size smaller than snapshot rejected",
        ],
        test_dimensions=[
            "snapshot of detached volume",
            "snapshot of attached volume — consistency warning",
            "create volume from snapshot — data verified",
            "delete snapshot used by a volume — blocked",
        ],
        api_prefix="/volume-snapshots",
        inter_dependencies=["volume"],
    ),

    "object_storage": FeatureSpec(
        name="Object Storage",
        aliases=["object storage", "s3", "bucket", "blob storage", "obs", "swift"],
        domain="STORAGE",
        risk_level="P2",
        critical_flows=[
            "create bucket",
            "upload object",
            "download object",
            "delete object",
            "delete bucket",
            "set bucket ACL / policy",
            "generate presigned URL",
        ],
        known_failure_modes=[
            "bucket deletion fails when non-empty — user not warned",
            "presigned URL expiry not enforced",
            "ACL policy not applied immediately — eventual consistency",
        ],
        test_dimensions=[
            "create bucket — unique name per tenant",
            "upload: small file, large file (multipart), empty file",
            "download — content matches upload",
            "ACL: private bucket — public access blocked",
            "presigned URL — valid for duration, rejected after expiry",
            "delete non-empty bucket — error with count",
        ],
        api_prefix="/buckets",
        inter_dependencies=["keycloak_auth"],
    ),

    "filesystem": FeatureSpec(
        name="File System (NFS/CephFS)",
        aliases=["filesystem", "file system", "nfs", "shared storage", "cephfs", "file share"],
        domain="STORAGE",
        risk_level="P2",
        critical_flows=[
            "create file system",
            "mount file system on VM",
            "write / read files",
            "unmount",
            "delete file system",
        ],
        known_failure_modes=[
            "mount fails — NFS export rules not updated",
            "concurrent write from multiple VMs — locking issues",
        ],
        test_dimensions=[
            "create with valid size",
            "mount on VM — accessible",
            "concurrent access — no corruption",
            "delete while mounted — blocked or force-unmount",
        ],
        api_prefix="/filesystems",
        inter_dependencies=["vm", "vpc"],
    ),

    # ──────────────── NETWORKING ────────────────

    "vpc": FeatureSpec(
        name="VPC (Virtual Private Cloud)",
        aliases=["vpc", "virtual private cloud", "network", "vnet", "virtual network"],
        domain="NETWORKING",
        risk_level="P0",
        critical_flows=[
            "create VPC with CIDR",
            "create subnet inside VPC",
            "edit VPC name",
            "delete VPC",
            "list VPCs",
        ],
        known_failure_modes=[
            "CIDR overlap not detected — silent networking conflict",
            "VPC delete while subnets exist — orphaned resources",
            "tenant CIDR exceeds allowed range — not validated at create",
        ],
        test_dimensions=[
            "create — valid CIDR /16 to /28",
            "create — overlapping CIDR → 409",
            "create — CIDR out of range → 422",
            "edit — name change only (CIDR immutable)",
            "edit — CIDR change attempt → rejected",
            "delete — with active subnets → blocked",
            "delete — empty VPC → 204",
            "multi-tenant isolation — VPC A not visible to tenant B",
        ],
        api_prefix="/vpcs",
        inter_dependencies=["subnet", "router", "keycloak_auth"],
    ),

    "subnet": FeatureSpec(
        name="Subnet",
        aliases=["subnet", "sub network", "subnetwork"],
        domain="NETWORKING",
        risk_level="P1",
        critical_flows=[
            "create subnet inside VPC",
            "assign IP range to subnet",
            "attach subnet to router",
            "delete subnet",
        ],
        known_failure_modes=[
            "subnet CIDR not within VPC CIDR — not validated",
            "delete subnet with VMs attached — cascade not blocked",
        ],
        test_dimensions=[
            "create — CIDR within VPC range",
            "create — CIDR outside VPC range → rejected",
            "attach to router — routing works",
            "delete with VMs in subnet — blocked",
        ],
        api_prefix="/subnets",
        inter_dependencies=["vpc", "router"],
    ),

    "router": FeatureSpec(
        name="Router",
        aliases=["router", "virtual router", "l3 router"],
        domain="NETWORKING",
        risk_level="P1",
        critical_flows=[
            "create router",
            "attach subnet to router",
            "attach router to external network",
            "delete router",
        ],
        known_failure_modes=[
            "router delete fails when still attached to subnets",
            "external gateway not set — no outbound internet access",
        ],
        test_dimensions=[
            "create and attach subnet — subnet gains routing",
            "attach external gateway — VMs can reach internet",
            "delete with attached interfaces — blocked",
        ],
        api_prefix="/routers",
        inter_dependencies=["subnet", "nat_gateway"],
    ),

    "nat_gateway": FeatureSpec(
        name="NAT Gateway",
        aliases=["nat", "nat gateway", "snat", "network address translation"],
        domain="NETWORKING",
        risk_level="P2",
        critical_flows=[
            "create NAT gateway",
            "associate with subnet",
            "delete NAT gateway",
        ],
        known_failure_modes=[
            "NAT gateway not functional without external IP assigned",
        ],
        test_dimensions=[
            "create — VMs behind NAT can reach internet",
            "delete — VMs lose internet, no crash",
        ],
        api_prefix="/nat-gateways",
        inter_dependencies=["router", "public_ip", "subnet"],
    ),

    "public_ip": FeatureSpec(
        name="Public IP (Floating IP / EIP)",
        aliases=["public ip", "floating ip", "eip", "elastic ip", "fip", "external ip"],
        domain="NETWORKING",
        risk_level="P2",
        critical_flows=[
            "allocate public IP",
            "associate with VM or LB",
            "disassociate",
            "release public IP",
        ],
        known_failure_modes=[
            "IP pool exhausted — no clear error to user",
            "disassociate fails while VM is running",
        ],
        test_dimensions=[
            "allocate — IP assigned from pool",
            "associate with VM — VM reachable on public IP",
            "release without dissociate — rejected",
            "pool exhaustion — 429 or quota error",
        ],
        api_prefix="/public-ips",
        inter_dependencies=["vm", "load_balancer", "router"],
    ),

    "security_group": FeatureSpec(
        name="Security Group",
        aliases=["security group", "sg", "firewall", "network acl", "firewall rules"],
        domain="NETWORKING",
        risk_level="P1",
        critical_flows=[
            "create security group",
            "add inbound / outbound rules",
            "attach to VM",
            "detach from VM",
            "delete security group",
        ],
        known_failure_modes=[
            "rules not applied immediately — delay in SDN propagation",
            "delete SG attached to VM — not blocked, causes connectivity issues",
            "rule with 0.0.0.0/0 allows unintended public access",
        ],
        test_dimensions=[
            "create SG + add rule — traffic allowed on specified port",
            "traffic blocked on non-allowed port",
            "attach SG to VM — rules applied within 5s",
            "delete SG attached to VM — rejected",
            "rule with /0 — explicit warning or confirmation required",
        ],
        api_prefix="/security-groups",
        inter_dependencies=["vm", "vpc"],
    ),

    "load_balancer": FeatureSpec(
        name="Load Balancer",
        aliases=["load balancer", "lb", "alb", "nlb", "elb", "haproxy", "load balance"],
        domain="NETWORKING",
        risk_level="P1",
        critical_flows=[
            "create load balancer",
            "add listener (HTTP/HTTPS/TCP)",
            "add backend pool + members",
            "health check configuration",
            "delete load balancer",
        ],
        known_failure_modes=[
            "LB creation succeeds but VMs not added to pool — timeout",
            "HTTPS listener fails if cert not uploaded first",
            "LB delete hangs — backend pool not cleaned first",
        ],
        test_dimensions=[
            "create LB — ACTIVE within 120s",
            "add listener + pool + members — traffic balanced",
            "health check — unhealthy member removed automatically",
            "HTTPS — cert upload required before HTTPS listener",
            "delete — all dependent resources cleaned up",
        ],
        api_prefix="/loadbalancers",
        inter_dependencies=["vm", "vpc", "public_ip", "security_group"],
    ),

    # ──────────────── IDENTITY ────────────────

    "keycloak_auth": FeatureSpec(
        name="Keycloak Auth / SSO",
        aliases=["auth", "keycloak", "login", "sso", "authentication", "token", "oidc", "jwt", "rbac"],
        domain="IDENTITY",
        risk_level="P0",
        critical_flows=[
            "user login — username + password → token",
            "token refresh",
            "logout / token revocation",
            "RBAC — role-based access to resources",
            "cross-tenant access blocked",
        ],
        known_failure_modes=[
            "Keycloak URL misconfigured — all API calls fail with 401",
            "token expiry not handled — user sees silent 401",
            "RBAC not enforced — viewer can delete resources",
            "cross-tenant resource access not blocked",
        ],
        test_dimensions=[
            "valid credentials → 200 + token",
            "invalid password → 401 with clear message",
            "expired token → 401, refresh works",
            "viewer role cannot create/delete",
            "admin role can manage all resources",
            "tenant A cannot access tenant B resources",
            "logout — token revoked, subsequent calls rejected",
        ],
        api_prefix="/auth/token",
        inter_dependencies=[],
    ),

    # ──────────────── DATA ────────────────

    "dbaas": FeatureSpec(
        name="DBaaS (Database-as-a-Service)",
        aliases=["dbaas", "database", "db", "managed database", "rds", "mysql", "postgres", "postgresql"],
        domain="DATA",
        risk_level="P0",
        critical_flows=[
            "create database instance",
            "connect to database",
            "create database user",
            "backup database",
            "restore from backup",
            "delete database instance",
        ],
        known_failure_modes=[
            "DB creation succeeds but connection string has wrong port",
            "backup fails silently — no alert",
            "restore from backup overwrites wrong instance",
            "DB user creation does not grant correct privileges",
        ],
        test_dimensions=[
            "create DB — reachable on correct port within 5min",
            "create user — can connect and query",
            "backup — completes and is restorable",
            "restore — data from backup point verified",
            "delete — all associated backups optionally cleaned",
            "quota: max DBs per tenant enforced",
        ],
        api_prefix="/databases",
        inter_dependencies=["vpc", "subnet", "security_group", "keycloak_auth"],
    ),

    # ──────────────── PLATFORM ────────────────

    "monitoring": FeatureSpec(
        name="Monitoring & Alerts",
        aliases=["monitoring", "alert", "metric", "alarm", "grafana", "prometheus", "observability"],
        domain="PLATFORM",
        risk_level="P2",
        critical_flows=[
            "view resource metrics (CPU, memory, disk, network)",
            "create alert rule",
            "alert fires on threshold breach",
            "alert notification (email/webhook)",
        ],
        known_failure_modes=[
            "metrics not shown for new resources — scrape delay",
            "alert fires but notification not sent — webhook misconfigured",
            "alert not firing — threshold unit mismatch (% vs raw)",
        ],
        test_dimensions=[
            "metrics appear within 2 min of resource creation",
            "alert: threshold breach → fires within configured window",
            "alert: notification delivered to webhook",
            "alert: resolves when metric drops below threshold",
        ],
        api_prefix="/monitoring",
        inter_dependencies=["vm", "keycloak_auth"],
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: keyword → feature canonical name
# ─────────────────────────────────────────────────────────────────────────────

_ALIAS_INDEX: dict[str, str] = {}
for _key, _spec in FEATURES.items():
    for _alias in _spec.aliases:
        _ALIAS_INDEX[_alias.lower()] = _key


class PlatformDomain:
    """Utility class for intent routing and prompt enrichment."""

    @staticmethod
    def resolve(text: str) -> list[str]:
        """
        Find all known feature keys mentioned in a text string.

        Returns list of canonical feature keys, e.g. ["vpc", "vm"].
        """
        text_lower = text.lower()
        found: list[str] = []
        for alias, key in _ALIAS_INDEX.items():
            if alias in text_lower and key not in found:
                found.append(key)
        return found

    @staticmethod
    def get(feature_key: str) -> FeatureSpec | None:
        return FEATURES.get(feature_key)

    @staticmethod
    def risk_level(feature_key: str) -> str:
        spec = FEATURES.get(feature_key)
        return spec.risk_level if spec else "P3"

    @staticmethod
    def test_dimensions(feature_key: str) -> list[str]:
        spec = FEATURES.get(feature_key)
        return spec.test_dimensions if spec else []

    @staticmethod
    def known_failure_modes(feature_key: str) -> list[str]:
        spec = FEATURES.get(feature_key)
        return spec.known_failure_modes if spec else []

    @staticmethod
    def critical_flows(feature_key: str) -> list[str]:
        spec = FEATURES.get(feature_key)
        return spec.critical_flows if spec else []

    @staticmethod
    def inter_dependencies(feature_key: str) -> list[str]:
        spec = FEATURES.get(feature_key)
        return spec.inter_dependencies if spec else []

    @staticmethod
    def domain_context(feature_key: str) -> str:
        """
        Return a rich context string for a feature — used for prompt injection.
        """
        spec = FEATURES.get(feature_key)
        if not spec:
            return f"Feature '{feature_key}' not found in platform taxonomy."
        return (
            f"Feature: {spec.name}\n"
            f"Domain: {spec.domain}\n"
            f"Risk Level: {spec.risk_level}\n"
            f"Critical Flows:\n" +
            "\n".join(f"  - {f}" for f in spec.critical_flows) +
            f"\nKnown Failure Modes:\n" +
            "\n".join(f"  - {f}" for f in spec.known_failure_modes) +
            f"\nTest Dimensions:\n" +
            "\n".join(f"  - {f}" for f in spec.test_dimensions) +
            f"\nDependencies: {', '.join(spec.inter_dependencies) or 'none'}\n"
        )

    @staticmethod
    def all_feature_keys() -> list[str]:
        return list(FEATURES.keys())

    @staticmethod
    def features_by_domain(domain: str) -> list[str]:
        return [k for k, v in FEATURES.items() if v.domain == domain.upper()]

    @staticmethod
    def p0_features() -> list[str]:
        return [k for k, v in FEATURES.items() if v.risk_level == "P0"]
