import pulumi
from pulumi import Config

def load_config():
    config = Config("eks-cluster")
    return {
        "cluster_name": config.get("cluster_name") or "eks-cluster",
        "region": pulumi.Config("aws").get("region") or "us-west-2",
        "desired_capacity": config.get_int("desired_capacity") or 2,
        "node_group_name": config.get("node_group_name") or "default-node-group",
        "min_capacity": config.get_int("min_capacity") or 1,
        "max_capacity": config.get_int("max_capacity") or 4,
        "instance_type": config.get("instance_type") or "t3.medium",
        "ssh_keypair_name": config.get("ssh_keypair_name"),
        "ami_id": config.get("ami_id") or None,
        "public_access": config.get_bool("public_access") if config.get_bool("public_access") is not None else False,
        "public_access_cidrs": config.get_object("public_access_cidrs") or ["0.0.0.0/0"],
        "trusted_cidrs": config.get_object("trusted_cidrs") or [],
        "enable_efs": config.get_bool("enable_efs") if config.get_bool("enable_efs") is not None else False,
        "enable_ebs": config.get_bool("enable_ebs") if config.get_bool("enable_ebs") is not None else False,
        "enable_prometheus": config.get_bool("enable_prometheus") if config.get_bool("enable_prometheus") is not None else False,
        "enable_ingress": config.get_bool("enable_ingress") if config.get_bool("enable_ingress") is not None else False,
        "efs_csi_driver_version": config.get("efs_csi_driver_version") or "2.5.0",
        "ebs_csi_driver_version": config.get("ebs_csi_driver_version") or "2.26.1",
        "ingress_nginx_version": config.get("ingress_nginx_version") or "4.10.0",
        "prometheus_stack_version": config.get("prometheus_stack_version") or "55.5.0",
        "autoscaler_chart_version": config.get("autoscaler_chart_version") or "9.29.0",
        "ingress_nginx_values": config.get_object("ingress_nginx_values") or {
            "controller": {"service": {"type": "LoadBalancer"}}
        },
        "prometheus_stack_values": config.get_object("prometheus_stack_values") or {
            "prometheus": {"service": {"type": "ClusterIP"}},
        }
    }