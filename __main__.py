import pulumi
from config import load_config
from iam import create_eks_roles
from network import create_vpc, create_security_groups
from cluster import (
    build_base_tags,
    get_ami_for_group,
    create_kms_key,
    create_cluster_log_group,
    validate_instance_type_arch_pair,
    create_launch_template,
    create_eks_cluster,
    create_node_group,
    create_kube_provider,
    create_managed_addons,
)
from addons import setup_efs, setup_ebs, setup_ingress, setup_prometheus
from irsa_autoscaler import setup_oidc, setup_autoscaler

cfg = load_config()
base_tags = build_base_tags(cfg)

eks_role, node_group_role = create_eks_roles(cfg["cluster_name"], base_tags)

vpc_data = create_vpc(cfg["cluster_name"], cfg["vpc_cidr"], base_tags, cfg["max_azs"])
vpc = vpc_data["vpc"]
subnet_ids = vpc_data["subnet_ids"]
az_subnet_map = vpc_data["az_subnet_map"]

node_group_sg, eks_sg = create_security_groups(vpc, cfg["trusted_cidrs"], cfg["cluster_name"], base_tags)

kms_key = create_kms_key(cfg, base_tags)
log_group = create_cluster_log_group(cfg, base_tags)

cluster = create_eks_cluster(cfg, eks_role, eks_sg, subnet_ids, kms_key, base_tags, log_group)
kube_provider = create_kube_provider(cluster, cfg["cluster_name"])

created_node_groups = []
for ng_cfg in cfg["node_groups"]:
    name = ng_cfg["name"]
    itype = ng_cfg["instance_type"]
    arch = ng_cfg["architecture"]
    ami_family = ng_cfg["ami_family"]
    user_ami = ng_cfg.get("ami_id")
    validate_instance_type_arch_pair(itype, arch)
    ami_id = get_ami_for_group(cfg["cluster_version"], arch, ami_family, user_ami)
    lt = create_launch_template(
        name,
        node_group_sg,
        ng_cfg.get("ssh_keypair_name"),
        cfg["cluster_name"],
        ami_id,
        base_tags,
        ami_family,
        bool(user_ami),
    )
    node_group = create_node_group(
        name,
        cfg["cluster_name"],
        ng_cfg,
        node_group_role,
        subnet_ids,
        az_subnet_map,
        lt,
        cluster,
        base_tags,
    )
    created_node_groups.append(node_group)

create_managed_addons(cfg, cluster, base_tags)

primary_node_group = created_node_groups[0] if created_node_groups else None
if cfg["enable_efs"] and primary_node_group:
    setup_efs(cfg, vpc, node_group_sg, subnet_ids, cfg["cluster_name"], kube_provider, primary_node_group, base_tags)
if cfg["enable_ebs"] and primary_node_group:
    setup_ebs(cfg, kube_provider, primary_node_group, base_tags)
if cfg["enable_ingress"] and primary_node_group:
    setup_ingress(cfg, kube_provider, primary_node_group, base_tags)
if cfg["enable_prometheus"] and primary_node_group:
    setup_prometheus(cfg, kube_provider, primary_node_group, base_tags)

oidc = setup_oidc(cluster, cfg["oidc_thumbprint"])
if created_node_groups:
    setup_autoscaler(cfg, oidc, kube_provider, created_node_groups, cfg["cluster_name"], cfg["region"], base_tags)

pulumi.export("kubeconfig", pulumi.Output.secret(kube_provider.kubeconfig))
pulumi.export("cluster", {
    "name": cfg["cluster_name"],
    "arn": cluster.arn,
    "endpoint": cluster.endpoint,
    "region": cfg["region"],
    "version": cluster.version,
    "vpc_id": vpc.id,
    "vpc_cidr": vpc.cidr_block,
    "subnet_ids": subnet_ids,
    "az_subnet_map": az_subnet_map,
    "security_groups": {
        "control_plane": eks_sg.id,
        "nodes": node_group_sg.id,
    },
    "node_groups": [{
        "name": ng.node_group_name,
        "arn": ng.arn,
    } for ng in created_node_groups],
})