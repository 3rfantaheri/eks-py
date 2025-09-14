import pulumi
from pulumi import Config

def load_config():
    cfg = Config("eks-cluster")
    aws_region = pulumi.Config("aws").get("region") or "us-west-2"

    # Helper to safely read bools (avoid double evaluation)
    def get_bool(key, default):
        v = cfg.get_bool(key)
        return v if v is not None else default

    return {
        "cluster_name": cfg.get("cluster_name") or "eks-cluster",
        "region": aws_region,
        "cluster_version": cfg.get("cluster_version") or "1.30",
        "cluster_log_types": cfg.get_object("cluster_log_types") or ["api","audit","authenticator","controllerManager","scheduler"],
        "desired_capacity": cfg.get_int("desired_capacity") or 2,
        "node_group_name": cfg.get("node_group_name") or "default-node-group",
        "min_capacity": cfg.get_int("min_capacity") or 1,
        "max_capacity": cfg.get_int("max_capacity") or 4,
        "instance_type": cfg.get("instance_type") or "t3.medium",
        "node_architecture": cfg.get("node_architecture") or "x86_64",  # or arm64
        "ssh_keypair_name": cfg.get("ssh_keypair_name"),
        "ami_id": cfg.get("ami_id") or None,
        "public_access": get_bool("public_access", False),
        "public_access_cidrs": cfg.get_object("public_access_cidrs") or ["0.0.0.0/0"],
        "trusted_cidrs": cfg.get_object("trusted_cidrs") or [],
        "enable_efs": get_bool("enable_efs", False),
        "enable_ebs": get_bool("enable_ebs", False),
        "enable_prometheus": get_bool("enable_prometheus", False),
        "enable_ingress": get_bool("enable_ingress", False),
        "efs_csi_driver_version": cfg.get("efs_csi_driver_version") or "2.5.0",
        "ebs_csi_driver_version": cfg.get("ebs_csi_driver_version") or "2.26.1",
        "ingress_nginx_version": cfg.get("ingress_nginx_version") or "4.10.0",
        "prometheus_stack_version": cfg.get("prometheus_stack_version") or "55.5.0",
        "autoscaler_chart_version": cfg.get("autoscaler_chart_version") or "9.29.0",
        "ingress_nginx_values": cfg.get_object("ingress_nginx_values") or {
            "controller": {"service": {"type": "LoadBalancer"}}
        },
        "prometheus_stack_values": cfg.get_object("prometheus_stack_values") or {
            "prometheus": {"service": {"type": "ClusterIP"}},
        },
        "cluster_deletion_protection": get_bool("cluster_deletion_protection", False),
        "efs_deletion_protection": get_bool("efs_deletion_protection", False),
        "vpc_cidr": cfg.get("vpc_cidr") or "10.100.0.0/16",
        "private_access": get_bool("private_access", True),  # default True so cluster always reachable
    }