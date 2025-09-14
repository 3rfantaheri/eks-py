import base64
import json
import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s
from pulumi import ResourceOptions

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
    if arch not in ["x86_64", "arm64"]:
        raise Exception("node architecture must be x86_64 or arm64")
    filters = []
    owners = []
    if ami_family == "al2":
        owners = ["602401143452"]
        filters = [
            {"name": "name", "values": [f"amazon-eks-node-{cluster_version}-*"]},
            {"name": "architecture", "values": [arch]},
        ]
    elif ami_family == "bottlerocket":
        owners = ["679593333241"]
        arch_map = {"x86_64": "x86_64", "arm64": "aarch64"}
        filters = [
            {"name": "name", "values": [f"bottlerocket-aws-k8s-{cluster_version}-{arch_map[arch]}-*"]},
        ]
    else:
        raise Exception("Unsupported ami_family (use al2 or bottlerocket)")
    ami = aws.get_ami(most_recent=True, owners=owners, filters=filters)
    return ami.id

def create_kms_key(cfg, base_tags):
    if not cfg["enable_kms_encryption"]:
        return None
    key = aws.kms.Key("eks-secrets-key",
        description=f"KMS key for EKS secrets encryption ({cfg['cluster_name']})",
        deletion_window_in_days=7,
        enable_key_rotation=True,
        tags=base_tags
    )
    aws.kms.Alias("eks-secrets-alias",
        target_key_id=key.key_id,
        name=f"alias/{cfg['cluster_name']}-secrets")
    return key

def create_cluster_log_group(cfg, base_tags):
    # Ensures retention is applied (EKS would create it automatically otherwise with no retention)
    return aws.cloudwatch.LogGroup("eks-cluster-log-group",
        name=f"/aws/eks/{cfg['cluster_name']}/cluster",
        retention_in_days=cfg["log_retention_days"],
        tags=base_tags
    )

def validate_instance_type_arch_pair(instance_type, arch):
    # simple heuristic; extendable
    is_arm_type = (".a1." in instance_type) or instance_type.split(".")[0].endswith("g") or instance_type.startswith("c7g") or instance_type.startswith("m7g")
    if arch == "arm64" and not is_arm_type:
        raise Exception(f"Instance type {instance_type} not ARM while architecture=arm64")
    if arch == "x86_64" and is_arm_type:
        raise Exception(f"Instance type {instance_type} appears ARM while architecture=x86_64")

def create_launch_template(name, node_group_sg, ssh_keypair_name, cluster_name, ami_id, base_tags, ami_family, user_supplied_ami):
    user_data_encoded = None
    if user_supplied_ami and ami_family == "al2":
        script = f"""#!/bin/bash
/etc/eks/bootstrap.sh {cluster_name}
"""
        user_data_encoded = base64.b64encode(script.encode()).decode()
    kwargs = {
        "vpc_security_group_ids": [node_group_sg.id],
        "key_name": ssh_keypair_name if ssh_keypair_name else None,
        "tags": base_tags,
        "tag_specifications": [{
            "resource_type": "instance",
            "tags": {**base_tags, "Name": f"{cluster_name}-{name}-node"}
        }]
    }
    if user_data_encoded:
        kwargs["user_data"] = user_data_encoded
    if ami_id:
        kwargs["image_id"] = ami_id
    return aws.ec2.LaunchTemplate(f"lt-{name}", **kwargs)

def create_eks_cluster(cfg, eks_role, eks_sg, subnet_ids, kms_key, base_tags, log_group):
    if not (cfg["public_access"] or cfg["private_access"]):
        raise Exception("At least one of public_access or private_access must be True.")
    encryption_config = None
    if kms_key:
        encryption_config = [aws.eks.ClusterEncryptionConfigArgs(
            provider=aws.eks.ClusterEncryptionConfigProviderArgs(key_arn=kms_key.arn),
            resources=["secrets"]
        )]
    opts = ResourceOptions(depends_on=[log_group] if log_group else None)
    return aws.eks.Cluster("eks-cluster",
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
            public_access_cidrs=cfg["public_access_cidrs"] if cfg["public_access"] else None
        ),
        deletion_protection=cfg["cluster_deletion_protection"],
        tags={**base_tags, "Name": cfg["cluster_name"]},
        opts=opts
    )

def create_node_group(name, cfg_ng, node_group_role, subnet_ids, lt, cluster, base_tags):
    if not (cfg_ng["min_capacity"] <= cfg_ng["desired_capacity"] <= cfg_ng["max_capacity"]):
        raise Exception(f"Capacity constraints violated for {name}")
    # base labels plus zone spread awareness handled at scheduling via topology labels automatically added by kubelet
    labels = {**cfg_ng.get("labels", {}), "node-group": name}
    return aws.eks.NodeGroup(f"ng-{name}",
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
        launch_template=aws.eks.NodeGroupLaunchTemplateArgs(
            id=lt.id,
            version="$Latest",
        ),
        tags={
            **base_tags,
            "Name": f"eks-node-group-{name}",
            "k8s.io/cluster-autoscaler/enabled": "true",
            f"k8s.io/cluster-autoscaler/{cluster.name}": "owned",
        },
        opts=ResourceOptions(depends_on=[cluster])
    )

def build_kubeconfig(cluster, cluster_name):
    return pulumi.Output.all(
        cluster.endpoint,
        cluster.certificate_authority["data"],
        cluster_name
    ).apply(lambda args: json.dumps({
        "apiVersion": "v1",
        "clusters": [{
            "cluster": {"server": args[0], "certificate-authority-data": args[1]},
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
            "user": {"exec": {"apiVersion": "client.authentication.k8s.io/v1", "command": "aws", "args": ["eks", "get-token", "--cluster-name", args[2]]}}
        }]
    }))

def create_kube_provider(cluster, cluster_name):
    return k8s.Provider("k8s-provider", kubeconfig=build_kubeconfig(cluster, cluster_name))

def create_managed_addons(cfg, cluster, base_tags):
    if not cfg["enable_managed_addons"]:
        return
    for addon_name, ver in cfg["addon_versions"].items():
        aws.eks.Addon(f"addon-{addon_name}",
            cluster_name=cluster.name,
            addon_name=addon_name,
            addon_version=ver,
            resolve_conflicts="OVERWRITE",
            tags=base_tags)