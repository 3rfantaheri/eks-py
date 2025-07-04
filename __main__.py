import pulumi
import json

from config import load_config
from iam import create_eks_roles
from network import create_vpc, create_security_groups
from eks import create_launch_template, create_eks_cluster, create_node_group, create_kube_provider, get_ami
from addons import setup_efs, setup_ebs, setup_ingress, setup_prometheus
from irsa_autoscaler import setup_oidc, setup_autoscaler

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
            "name": "kubernetes",
        }],
        "contexts": [{
            "context": {
                "cluster": "kubernetes",
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

def export_outputs(export, cluster, cluster_name, region, vpc, subnet_ids, eks_sg, node_group_sg, node_group, instance_type, desired_capacity, min_capacity, max_capacity):
    export("kubeconfig", generate_kubeconfig(cluster, cluster_name))
    export("cluster", {
        "name": cluster_name,
        "arn": cluster.arn,
        "endpoint": cluster.endpoint,
        "region": region,
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
cfg["ami_id"] = get_ami(cfg["ami_id"])

eks_role, node_group_role = create_eks_roles(cfg["cluster_name"])
vpc, igw, route_table, subnet_ids = create_vpc(cfg["cluster_name"])
node_group_sg, eks_sg = create_security_groups(vpc, subnet_ids, cfg["trusted_cidrs"], cfg["cluster_name"])
lt = create_launch_template(node_group_sg, cfg["ssh_keypair_name"], cfg["ami_id"])
cluster = create_eks_cluster(cfg, eks_role, eks_sg, subnet_ids)
node_group = create_node_group(cfg, node_group_role, subnet_ids, lt)
kube_provider = create_kube_provider(cluster, cfg["cluster_name"])

if cfg["enable_efs"]:
    setup_efs(cfg, vpc, node_group_sg, subnet_ids, cfg["cluster_name"], kube_provider, node_group)
if cfg["enable_ebs"]:
    setup_ebs(cfg, kube_provider, node_group)
if cfg["enable_ingress"]:
    setup_ingress(cfg, kube_provider, node_group)
if cfg["enable_prometheus"]:
    setup_prometheus(cfg, kube_provider, node_group)

oidc = setup_oidc(cluster)
setup_autoscaler(cfg, oidc, kube_provider, node_group, cfg["cluster_name"], cfg["region"])

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