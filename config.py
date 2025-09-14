import pulumi
from pulumi import Config


def load_config():
    cfg = Config("eks-cluster")
    aws_region = pulumi.Config("aws").get("region") or "us-west-2"

    def get_bool(key, default):
        v = cfg.get_bool(key)
        return default if v is None else v

    environment = cfg.get("environment") or "dev"

    cluster_del_prot = cfg.get_bool("cluster_deletion_protection")
    if cluster_del_prot is None:
        cluster_del_prot = (environment == "prod")

    efs_del_prot = cfg.get_bool("efs_deletion_protection")
    if efs_del_prot is None:
        efs_del_prot = (environment == "prod")

    node_groups = cfg.get_object("node_groups")
    if not node_groups or not isinstance(node_groups, list):
        raise Exception("'node_groups' config must be a non-empty list of node group objects.")

    normalized = []
    for i, ng in enumerate(node_groups):
        if not isinstance(ng, dict):
            raise Exception(f"node_groups[{i}] must be an object")
        name = ng.get("name") or f"node-group-{i}"
        instance_type = ng.get("instance_type")
        if not instance_type:
            raise Exception(f"node_groups[{i}] missing 'instance_type'")
        desired = ng.get("desired_capacity")
        min_c = ng.get("min_capacity")
        max_c = ng.get("max_capacity")
        if desired is None or min_c is None or max_c is None:
            raise Exception(f"node_groups[{i}] must include desired_capacity, min_capacity, max_capacity")
        arch = ng.get("architecture") or "x86_64"
        ami_family = ng.get("ami_family") or "al2"
        labels = ng.get("labels") or {}
        normalized.append({
            "name": name,
            "instance_type": instance_type,
            "desired_capacity": desired,
            "min_capacity": min_c,
            "max_capacity": max_c,
            "architecture": arch,
            "ami_family": ami_family,
            "ami_id": ng.get("ami_id"),
            "ssh_keypair_name": ng.get("ssh_keypair_name"),
            "labels": labels,
        })
    node_groups = normalized

    max_azs = cfg.get_int("max_azs")  # None => use all available
    if max_azs is not None and max_azs <= 0:
        raise Exception("max_azs must be > 0 when provided")

    return {
        "environment": environment,
        "owner": cfg.get("owner") or "team-platform",
        "cost_center": cfg.get("cost_center") or "shared",
        "cluster_name": cfg.get("cluster_name") or "eks-cluster",
        "region": aws_region,
        "cluster_version": cfg.get("cluster_version") or "1.30",
        "cluster_log_types": cfg.get_object("cluster_log_types") or ["api","audit","authenticator","controllerManager","scheduler"],
        "log_retention_days": cfg.get_int("log_retention_days") or 30,
        "public_access": get_bool("public_access", False),
        "private_access": get_bool("private_access", True),
        "public_access_cidrs": cfg.get_object("public_access_cidrs") or ["0.0.0.0/0"],
        "trusted_cidrs": cfg.get_object("trusted_cidrs") or [],
        "enable_efs": get_bool("enable_efs", False),
        "enable_ebs": get_bool("enable_ebs", False),
        "enable_prometheus": get_bool("enable_prometheus", False),
        "enable_ingress": get_bool("enable_ingress", False),
        "enable_managed_addons": get_bool("enable_managed_addons", True),
        "enable_kms_encryption": get_bool("enable_kms_encryption", True),
        "efs_csi_driver_version": cfg.get("efs_csi_driver_version") or "2.5.0",
        "ebs_csi_driver_version": cfg.get("ebs_csi_driver_version") or "2.26.1",
        "ingress_nginx_version": cfg.get("ingress_nginx_version") or "4.10.0",
        "prometheus_stack_version": cfg.get("prometheus_stack_version") or "55.5.0",
        "autoscaler_chart_version": cfg.get("autoscaler_chart_version") or "9.29.0",
        "ingress_nginx_values": cfg.get_object("ingress_nginx_values") or {"controller": {"service": {"type": "LoadBalancer"}}},
        "prometheus_stack_values": cfg.get_object("prometheus_stack_values") or {"prometheus": {"service": {"type": "ClusterIP"}}},
        "cluster_deletion_protection": cluster_del_prot,
        "efs_deletion_protection": efs_del_prot,
        "vpc_cidr": cfg.get("vpc_cidr") or "10.100.0.0/16",
        "oidc_thumbprint": cfg.get("oidc_thumbprint") or "9e99a48a9960b14926bb7f3b02e22da0ecd2e9d0",
        "addon_versions": cfg.get_object("addon_versions") or {"vpc-cni": None, "kube-proxy": None, "coredns": None},
        "node_groups": node_groups,
        "max_azs": max_azs,
    }