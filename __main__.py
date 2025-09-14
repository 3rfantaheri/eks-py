import pulumi
import json

from config import load_config
from iam import create_eks_roles
from network import create_vpc, create_security_groups
from eks import (
    create_launch_template,
    create_eks_cluster,
    create_node_group,
    create_kube_provider,
    get_ami,
    build_base_tags,
    create_kms_key,
    create_managed_addons,
    validate_instance_type_arch,
    create_cluster_log_group,
)
from addons import setup_efs, setup_ebs, setup_ingress, setup_prometheus
from irsa_autoscaler import setup_oidc, setup_autoscaler

# (Optional) keep for exports; could reuse eks.build_kubeconfig
def generate_kubeconfig(cluster, cluster_name):
    return pulumi.Output.all(
        cluster.endpoint,
        cluster.certificate_authority["data"],
        cluster_name
    ).apply(lambda args: json.dumps({
        "apiVersion": "v1",
        "clusters": [{
            "cluster": {
                "server": args[0],
                "certificate-authority-data": args[1],
            },
            "name": args[2],
        }],
        "contexts": [{
            "context": {
                "cluster": args[2],
                "user": "aws",
            },
            "name": "aws",
        }],
        "current-context": "aws",
        "kind": "Config",
        "users": [{
            "name": "aws",
            "user": {
                "exec": {
                    "apiVersion": "client.authentication.k8s.io/v1",
                    "command": "aws",
                    "args": ["eks", "get-token", "--cluster-name", args[2]]
                }
            }
        }]
    }))

def export_outputs(cluster, cluster_name, region, vpc, subnet_ids, eks_sg, node_group_sg, node_group, instance_type, desired_capacity, min_capacity, max_capacity):
    pulumi.export("kubeconfig", generate_kubeconfig(cluster, cluster_name))
    pulumi.export("cluster", {
        "name": cluster_name,
        "arn": cluster.arn,
        "endpoint": cluster.endpoint,
        "region": region,
        "version": cluster.version,
        "vpc_id": vpc.id,
        "vpc_cidr": vpc.cidr_block,
        "subnet_ids": subnet_ids,
        "security_groups": {
            "eks_control_plane": eks_sg.id,
            "node_group": node_group_sg.id,
        },
        "node_group": {
            "name": node_group.node_group_name,
            "instance_type": instance_type,
            "desired_capacity": desired_capacity,
            "min_capacity": min_capacity,
            "max_capacity": max_capacity,
        },
    })

cfg = load_config()

# Build consistent tags first
base_tags = build_base_tags(cfg)

# Capture whether user explicitly supplied an AMI before resolution
user_supplied_ami = bool(cfg["ami_id"])
if not user_supplied_ami:
    cfg["ami_id"] = get_ami(cfg)

# Validate instance type vs architecture early
validate_instance_type_arch(cfg)

# IAM roles
eks_role, node_group_role = create_eks_roles(cfg["cluster_name"], base_tags)

# Networking
vpc, igw, route_table, subnet_ids = create_vpc(cfg["cluster_name"], cfg["vpc_cidr"], base_tags)
node_group_sg, eks_sg = create_security_groups(vpc, cfg["trusted_cidrs"], cfg["cluster_name"], base_tags)

# KMS (optional)
kms_key = create_kms_key(cfg, base_tags)

# CloudWatch log group (retention) before cluster
log_group = create_cluster_log_group(cfg, base_tags)

# Launch template (bootstrap only for user-supplied AL2 AMI)
lt = create_launch_template(
    node_group_sg,
    cfg["ssh_keypair_name"],
    cfg["cluster_name"],
    cfg["ami_id"],
    base_tags,
    cfg["ami_family"],
    user_supplied_ami
)

# Cluster
cluster = create_eks_cluster(cfg, eks_role, eks_sg, subnet_ids, kms_key, base_tags, log_group)

# Node group
node_group = create_node_group(cfg, node_group_role, subnet_ids, lt, cluster, base_tags)

# Kubernetes provider
kube_provider = create_kube_provider(cluster, cfg["cluster_name"])

# Managed core addons
create_managed_addons(cfg, cluster, base_tags)

# Optional addons
if cfg["enable_efs"]:
    setup_efs(cfg, vpc, node_group_sg, subnet_ids, cfg["cluster_name"], kube_provider, node_group, base_tags)
if cfg["enable_ebs"]:
    setup_ebs(cfg, kube_provider, node_group, base_tags)
if cfg["enable_ingress"]:
    setup_ingress(cfg, kube_provider, node_group, base_tags)
if cfg["enable_prometheus"]:
    setup_prometheus(cfg, kube_provider, node_group, base_tags)

# OIDC + autoscaler
oidc = setup_oidc(cluster, cfg["oidc_thumbprint"])
setup_autoscaler(cfg, oidc, kube_provider, node_group, cfg["cluster_name"], cfg["region"], base_tags)

# Final guard (redundant)
if not (cfg["public_access"] or cfg.get("private_access", True)):
    raise Exception("At least one of public_access or private_access must be True.")

export_outputs(
    cluster,
    cfg["cluster_name"],
    cfg["region"],
    vpc,
    subnet_ids,
    eks_sg,
    node_group_sg,
    node_group,
    cfg["instance_type"],
    cfg["desired_capacity"],
    cfg["min_capacity"],
    cfg["max_capacity"]
)