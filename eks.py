import pulumi_aws as aws
import pulumi_kubernetes as k8s
from pulumi import ResourceOptions
import json

def get_ami(ami_id):
    if ami_id:
        return ami_id
    eks_ami = aws.get_ami(
        most_recent=True,
        owners=["602401143452"],
        filters=[
            {"name": "name", "values": ["amazon-eks-node-*"]},
            {"name": "architecture", "values": ["x86_64"]},
        ],
    )
    return eks_ami.id

def create_launch_template(node_group_sg, ssh_keypair_name, ami_id):
    return aws.ec2.LaunchTemplate("eks-nodegroup-lt",
        vpc_security_group_ids=[node_group_sg.id],
        key_name=ssh_keypair_name if ssh_keypair_name else None,
        image_id=ami_id if ami_id else None
    )

def create_eks_cluster(cfg, eks_role, eks_sg, subnet_ids):
    return aws.eks.Cluster("eks-cluster",
        name=cfg["cluster_name"],
        role_arn=eks_role.arn,
        vpc_config=aws.eks.ClusterVpcConfigArgs(
            subnet_ids=subnet_ids,
            security_group_ids=[eks_sg.id],
            endpoint_public_access=cfg["public_access"],
            public_access_cidrs=cfg["public_access_cidrs"] if cfg["public_access"] and cfg["public_access_cidrs"] else []
        ),
        deletion_protection=True,
        tags={"Name": cfg["cluster_name"]}
    )

def create_node_group(cfg, node_group_role, subnet_ids, lt):
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
            f"k8s.io/cluster-autoscaler/enabled": "true",
            f"k8s.io/cluster-autoscaler/{cfg['cluster_name']}": "owned",
        }
    )

def create_kube_provider(cluster, cluster_name):
    return k8s.Provider("k8s-provider", kubeconfig=cluster.endpoint.apply(
        lambda ep: json.dumps({
            "apiVersion": "v1",
            "clusters": [{"cluster": {
                "server": ep,
                "certificate-authority-data": cluster.certificate_authority["data"],
            }, "name": "k8s"}],
            "contexts": [{"context": {"cluster": "k8s", "user": "aws"}, "name": "aws"}],
            "current-context": "aws",
            "kind": "Config",
            "users": [{"name": "aws", "user": {
                "exec": {"apiVersion": "client.authentication.k8s.io/v1", "command": "aws",
                         "args": ["eks", "get-token", "--cluster-name", cluster_name]}}}]
        })
    ))