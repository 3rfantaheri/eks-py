import base64
import json
import pulumi
from pulumi import ResourceOptions
import pulumi_aws as aws
import pulumi_kubernetes as k8s

def build_base_tags(cfg):
    return {
        "Environment": cfg["environment"],
        "Owner": cfg["owner"],
        "CostCenter": cfg["cost_center"],
        "ManagedBy": "Pulumi",
        "Stack": pulumi.get_stack(),
        "Project": pulumi.get_project(),
        "Cluster": cfg["cluster_name"],
    }

def get_ami_for_group(cluster_version, arch, ami_family, user_supplied_ami_id):
    if user_supplied_ami_id:
        return user_supplied_ami_id
    if arch not in ("x86_64", "arm64"):
        raise Exception(f"Unsupported architecture {arch}")
    owners = ["602401143452"]
    if ami_family == "al2":
        filters = [
            aws.ec2.GetAmiFilterArgs(name="name", values=[f"amazon-eks-node-{cluster_version}-*"]),
            aws.ec2.GetAmiFilterArgs(name="architecture", values=[arch]),
        ]
    elif ami_family == "bottlerocket":
        owners = ["679593333241"]
        filters = [
            aws.ec2.GetAmiFilterArgs(name="name", values=[f"bottlerocket-aws-k8s-{cluster_version}-*"]),
            aws.ec2.GetAmiFilterArgs(name="architecture", values=[arch]),
        ]
    else:
        raise Exception(f"Unsupported ami_family {ami_family}")
    ami = aws.ec2.get_ami(most_recent=True, owners=owners, filters=filters)
    if not ami or not ami.id:
        raise Exception("AMI lookup failed")
    return ami.id

def create_kms_key(cfg, base_tags):
    if not cfg["enable_kms_encryption"]:
        return None
    key = aws.kms.Key(
        "kms-secrets",
        description=f"EKS secrets encryption ({cfg['cluster_name']})",
        deletion_window_in_days=7,
        enable_key_rotation=True,
        tags=base_tags,
    )
    aws.kms.Alias(
        "kms-secrets-alias",
        target_key_id=key.key_id,
        name=f"alias/{cfg['cluster_name']}-secrets",
    )
    return key

def create_cluster_log_group(cfg, base_tags):
    return aws.cloudwatch.LogGroup(
        "lg-cluster",
        name=f"/aws/eks/{cfg['cluster_name']}/cluster",
        retention_in_days=cfg["log_retention_days"],
        tags=base_tags,
    )

def validate_instance_type_arch_pair(instance_type, arch):
    family = instance_type.split(".")[0]
    arm_families = ("c6g","c7g","m6g","m7g","r6g","r7g","t4g","x2g","a1")
    is_arm = (family in arm_families) or (family.endswith("g") and family not in ("g5","g4dn"))
    if arch == "arm64" and not is_arm:
        raise Exception(f"{instance_type} not ARM")
    if arch == "x86_64" and is_arm:
        raise Exception(f"{instance_type} is ARM family")

def create_launch_template(name, node_group_sg, ssh_keypair_name, cluster_name, ami_id, base_tags, ami_family, user_supplied_ami):
    user_data_encoded = None
    if user_supplied_ami and ami_family == "al2":
        user_data = f"#!/bin/bash\n/etc/eks/bootstrap.sh {cluster_name}"
        user_data_encoded = base64.b64encode(user_data.encode()).decode()
    kwargs = {
        "vpc_security_group_ids": [node_group_sg.id],
        "key_name": ssh_keypair_name if ssh_keypair_name else None,
        "tag_specifications": [{
            "resource_type": "instance",
            "tags": {**base_tags, "Name": f"{cluster_name}-{name}-node"}
        }],
        "tags": base_tags,
    }
    if user_data_encoded:
        kwargs["user_data"] = user_data_encoded
    if ami_id:
        kwargs["image_id"] = ami_id
    return aws.ec2.LaunchTemplate(f"lt-{name}", **kwargs)

def create_eks_cluster(cfg, eks_role, eks_sg, subnet_ids, kms_key, base_tags, log_group):
    if not (cfg["public_access"] or cfg["private_access"]):
        raise Exception("Enable at least one endpoint access mode")
    encryption_config = None
    if kms_key:
        encryption_config = [aws.eks.ClusterEncryptionConfigArgs(
            provider=aws.eks.ClusterEncryptionConfigProviderArgs(key_arn=kms_key.arn),
            resources=["secrets"]
        )]
    return aws.eks.Cluster(
        "eks-cluster",
        name=cfg["cluster_name"],
        role_arn=eks_role.arn,
        version=cfg["cluster_version"],
        encryption_config=encryption_config,
        enabled_cluster_log_types=cfg["cluster_log_types"],
        vpc_config=aws.eks.ClusterVpcConfigArgs(
            subnet_ids=subnet_ids,
            security_group_ids=[eks_sg.id],
            endpoint_public_access=cfg["public_access"],
            endpoint_private_access=cfg["private_access"],
            public_access_cidrs=cfg["public_access_cidrs"] if cfg["public_access"] else None,
        ),
        deletion_protection=cfg["cluster_deletion_protection"],
        tags={**base_tags, "Name": cfg["cluster_name"]},
        opts=ResourceOptions(depends_on=[log_group] if log_group else None),
    )

def create_node_group(
    name,
    cluster_name,
    cfg_ng,
    node_group_role,
    global_subnet_ids,
    az_subnet_map,
    lt,
    cluster,
    base_tags,
):
    if cfg_ng.get("subnet_ids"):
        subnet_ids = cfg_ng["subnet_ids"]
    elif cfg_ng.get("subnet_azs"):
        missing = [az for az in cfg_ng["subnet_azs"] if az not in az_subnet_map]
        if missing:
            raise Exception(f"NodeGroup {name} unknown AZ(s): {missing}")
        subnet_ids = [az_subnet_map[az] for az in cfg_ng["subnet_azs"]]
    else:
        subnet_ids = global_subnet_ids
    if not (cfg_ng["min_capacity"] <= cfg_ng["desired_capacity"] <= cfg_ng["max_capacity"]):
        raise Exception(f"Capacity invalid for {name}")
    taints_args = [
        aws.eks.NodeGroupTaintArgs(
            key=t["key"],
            value=t.get("value"),
            effect=t["effect"],
        )
        for t in cfg_ng.get("taints", [])
    ]
    labels = {**cfg_ng.get("labels", {}), "node-group": name}
    tags = {
        **base_tags,
        "Name": f"eks-ng-{name}",
        "k8s.io/cluster-autoscaler/enabled": "true",
        f"k8s.io/cluster-autoscaler/{cluster_name}": "owned",
    }
    return aws.eks.NodeGroup(
        f"ng-{name}",
        cluster_name=cluster.name,
        node_group_name=name,
        node_role_arn=node_group_role.arn,
        subnet_ids=subnet_ids,
        scaling_config=aws.eks.NodeGroupScalingConfigArgs(
            desired_size=cfg_ng["desired_capacity"],
            min_size=cfg_ng["min_capacity"],
            max_size=cfg_ng["max_capacity"],
        ),
        instance_types=[cfg_ng["instance_type"]],
        labels=labels,
        taints=taints_args or None,
        launch_template=aws.eks.NodeGroupLaunchTemplateArgs(
            id=lt.id,
            version="$Latest",
        ),
        tags=tags,
        opts=ResourceOptions(depends_on=[cluster]),
    )

def build_kubeconfig(cluster, cluster_name):
    return pulumi.Output.all(
        cluster.endpoint,
        cluster.certificate_authority.data,
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
            "context": {"cluster": args[2], "user": "aws"},
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
                    "args": ["eks", "get-token", "--cluster-name", args[2]],
                }
            },
        }],
    }))

def create_managed_addons(cfg, cluster, base_tags):
    if not cfg["enable_managed_addons"]:
        return
    for addon_name, ver in cfg["addon_versions"].items():
        kwargs = {
            "cluster_name": cluster.name,
            "addon_name": addon_name,
            "resolve_conflicts": "OVERWRITE",
            "tags": base_tags,
        }
        if ver:
            kwargs["addon_version"] = ver
        aws.eks.Addon(f"addon-{addon_name}", **kwargs, opts=ResourceOptions(depends_on=[cluster]))

def create_kube_provider(cluster, cluster_name):
    return k8s.Provider(
        "k8s-provider",
        kubeconfig=build_kubeconfig(cluster, cluster_name),
    )