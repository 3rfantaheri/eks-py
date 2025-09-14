import base64
import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s
from pulumi import ResourceOptions
import json

def get_ami(ami_id, version, architecture):
    if ami_id:
        return ami_id
    if architecture not in ["x86_64", "arm64"]:
        raise Exception("node_architecture must be x86_64 or arm64")
    # EKS optimized worker AMI pattern includes version: amazon-eks-node-<version>-*
    eks_ami = aws.get_ami(
        most_recent=True,
        owners=["602401143452"],  # Amazon EKS AMI account
        filters=[
            {"name": "name", "values": [f"amazon-eks-node-{version}-*"]},
            {"name": "architecture", "values": [architecture]},
        ],
    )
    return eks_ami.id

def create_launch_template(node_group_sg, ssh_keypair_name, cluster_name, ami_id=None):
    user_data_script = f"""#!/bin/bash
/etc/eks/bootstrap.sh {cluster_name}
"""
    user_data_encoded = base64.b64encode(user_data_script.encode()).decode()
    kwargs = {
        "vpc_security_group_ids": [node_group_sg.id],
        "key_name": ssh_keypair_name if ssh_keypair_name else None,
        "user_data": user_data_encoded,
    }
    if ami_id:
        kwargs["image_id"] = ami_id
    return aws.ec2.LaunchTemplate("eks-nodegroup-lt", **kwargs)

def create_eks_cluster(cfg, eks_role, eks_sg, subnet_ids):
    endpoint_public = cfg["public_access"]
    # If public disabled, enable private automatically
    endpoint_private = True if not endpoint_public else None  # None lets provider default if public=True
    return aws.eks.Cluster("eks-cluster",
        name=cfg["cluster_name"],
        role_arn=eks_role.arn,
        version=cfg["cluster_version"],
        vpc_config=aws.eks.ClusterVpcConfigArgs(
            subnet_ids=subnet_ids,
            security_group_ids=[eks_sg.id],
            endpoint_public_access=endpoint_public,
            endpoint_private_access=endpoint_private,
            public_access_cidrs=cfg["public_access_cidrs"] if endpoint_public else None,
        ),
        enabled_cluster_log_types=cfg["cluster_log_types"],
        deletion_protection=cfg["cluster_deletion_protection"],
        tags={"Name": cfg["cluster_name"]}
    )

def create_node_group(cfg, node_group_role, subnet_ids, lt, cluster):
    return aws.eks.NodeGroup("eks-node-group",
        cluster_name=cfg["cluster_name"],
        node_group_name=cfg["node_group_name"],
        node_role_arn=node_group_role.arn,
        subnet_ids=subnet_ids,
        scaling_config=aws.eks.NodeGroupScalingConfigArgs(
            desired_size=cfg["desired_capacity"],
            min_size=cfg["min_capacity"],
            max_size=cfg["max_capacity"],
        ),
        instance_types=[cfg["instance_type"]],
        launch_template=aws.eks.NodeGroupLaunchTemplateArgs(
            id=lt.id,
            version="$Latest",
        ),
        tags={
            "Name": "eks-node-group",
            "k8s.io/cluster-autoscaler/enabled": "true",
            f"k8s.io/cluster-autoscaler/{cfg['cluster_name']}": "owned",
        },
        opts=ResourceOptions(depends_on=[cluster])
    )

def create_kube_provider(cluster, cluster_name):
    return k8s.Provider("k8s-provider", kubeconfig=cluster.endpoint.apply(
        lambda ep: json.dumps({
            "apiVersion": "v1",
            "clusters": [{"cluster": {
                "server": ep,
                "certificate-authority-data": cluster.certificate_authority["data"],
            }, "name": cluster_name}],
            "contexts": [{"context": {"cluster": cluster_name, "user": "aws"}, "name": "aws"}],
            "current-context": "aws",
            "kind": "Config",
            "users": [{"name": "aws", "user": {
                "exec": {"apiVersion": "client.authentication.k8s.io/v1", "command": "aws",
                         "args": ["eks", "get-token", "--cluster-name", cluster_name]}}}]
        })
    ))