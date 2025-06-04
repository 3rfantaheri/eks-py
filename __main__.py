import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s
from pulumi import Config, export, ResourceOptions
import json

# Load configurations
config = Config("eks-cluster")
cluster_name = config.get("cluster_name") or "eks-cluster"
region = aws.config.region or "us-west-2"
desired_capacity = config.get_int("desired_capacity") or 2
node_group_name = config.get("node_group_name") or "default-node-group"
min_capacity = config.get_int("min_capacity") or 1
max_capacity = config.get_int("max_capacity") or 4
instance_type = config.get("instance_type") or "t3.medium"
ssh_keypair_name = config.get("ssh_keypair_name")
ami_id = config.get("ami_id") or False


public_access = config.get_bool("public_access")
if public_access is None:
    public_access = False
public_access_cidrs = config.get_object("public_access_cidrs") or ["0.0.0.0/0"]
trusted_cidrs = config.get_object("trusted_cidrs") or []


enable_efs = config.get_bool("enable_efs")
if enable_efs is None:
    enable_efs = False

enable_ebs = config.get_bool("enable_ebs")
if enable_ebs is None:
    enable_ebs = False

enable_prometheus = config.get_bool("enable_prometheus")
if enable_prometheus is None:
    enable_prometheus = False

enable_ingress = config.get_bool("enable_ingress")
if enable_ingress is None:
    enable_ingress = False

# Helm chart versions as config parameters (with defaults)
efs_csi_driver_version = config.get("efs_csi_driver_version") or "2.5.0"
ebs_csi_driver_version = config.get("ebs_csi_driver_version") or "2.26.1"
ingress_nginx_version = config.get("ingress_nginx_version") or "4.10.0"
prometheus_stack_version = config.get("prometheus_stack_version") or "55.5.0"
autoscaler_chart_version = config.get("autoscaler_chart_version") or "9.29.0"

ingress_nginx_values = config.get_object("ingress_nginx_values") or {
    "controller": {
        "service": {"type": "LoadBalancer"}
    }
}

prometheus_stack_values = config.get_object("prometheus_stack_values") or {
    "prometheus": {"service": {"type": "ClusterIP"}},
}


# If ami_id is not set, fetch the latest EKS-optimized AMI.
if not ami_id:
    eks_ami = aws.get_ami(
        most_recent=True,
        owners=["602401143452"],  # Amazon EKS AMI account
        filters=[
            {"name": "name", "values": [f"amazon-eks-node-*"]},
            {"name": "architecture", "values": ["x86_64"]},
        ],
    )
    ami_id = eks_ami.id


# ----------------------------------------------------------------------------
# IAM: Roles for EKS Cluster and Node Group
# ----------------------------------------------------------------------------

# EKS Cluster IAM Role
eks_role = aws.iam.Role(
    "eksClusterRole",
    name=f"{cluster_name}-eks-role",
    assume_role_policy=aws.iam.get_policy_document(statements=[
        aws.iam.GetPolicyDocumentStatementArgs(
            actions=["sts:AssumeRole"],
            principals=[aws.iam.GetPolicyDocumentStatementPrincipalArgs(
                type="Service",
                identifiers=["eks.amazonaws.com"],
            )],
        )
    ]).json,
)

# Attach EKS Cluster IAM Policies
for policy in [
    "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
    "arn:aws:iam::aws:policy/AmazonEKSServicePolicy",
]:
    aws.iam.RolePolicyAttachment(f"{eks_role._name}-{policy.split('/')[-1]}",
        role=eks_role.name,
        policy_arn=policy)

# Node Group IAM Role
node_group_role = aws.iam.Role(
    "eksNodeGroupRole",
    assume_role_policy=aws.iam.get_policy_document(statements=[
        aws.iam.GetPolicyDocumentStatementArgs(
            actions=["sts:AssumeRole"],
            principals=[aws.iam.GetPolicyDocumentStatementPrincipalArgs(
                type="Service",
                identifiers=["ec2.amazonaws.com"],
            )],
        )
    ]).json,
)

# Attach Node Group Policies
for policy in [
    "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
]:
    aws.iam.RolePolicyAttachment(f"{node_group_role._name}-{policy.split('/')[-1]}",
        role=node_group_role.name,
        policy_arn=policy)

# ----------------------------------------------------------------------------
# VPC and Networking
# ----------------------------------------------------------------------------

vpc = aws.ec2.Vpc("eks-vpc",
    cidr_block="10.100.0.0/16",
    enable_dns_hostnames=True,
    enable_dns_support=True,
    tags={"Name": f"{cluster_name}-vpc"})

igw = aws.ec2.InternetGateway("vpc-igw", vpc_id=vpc.id, tags={"Name": f"{cluster_name}-igw"})


route_table = aws.ec2.RouteTable("vpc-rt",
    vpc_id=vpc.id,
    routes=[aws.ec2.RouteTableRouteArgs(
        cidr_block="0.0.0.0/0",
        gateway_id=igw.id,
    )])

azs = aws.get_availability_zones()
subnet_ids = []

num_azs = min(3, len(azs.names))
if num_azs < 3:
    pulumi.log.warn(f"Only {num_azs} availability zones found. Creating {num_azs} subnets.")

for i, az in enumerate(azs.names[:num_azs]):
    subnet = aws.ec2.Subnet(f"subnet-{az}",
        vpc_id=vpc.id,
        cidr_block=f"10.100.{i}.0/24",
        map_public_ip_on_launch=True,
        availability_zone=az,
        tags={
            f"kubernetes.io/cluster/{cluster_name}": "owned",
            "kubernetes.io/role/elb": "1",
            "kubernetes.io/role/internal-elb": "1",
        })
    subnet_ids.append(subnet.id)
    aws.ec2.RouteTableAssociation(f"subnet-rta-{az}",
        subnet_id=subnet.id,
        route_table_id=route_table.id)

# Security Group for EKS Workers

node_group_sg = aws.ec2.SecurityGroup("nodegroup-sg",
    vpc_id=vpc.id,
    description="Security group for EKS worker nodes",
    ingress=[
        # Allow all traffic from within the VPC (for Kubernetes communication and LoadBalancer health checks)
        aws.ec2.SecurityGroupIngressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=[vpc.cidr_block]
        ),
    ],
    egress=[
        # Allow all outbound traffic (you can restrict this further if needed)
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"]
        )
    ]
)


# Security Group for EKS Control Plane (allow only worker node group and your IPs)
eks_sg = aws.ec2.SecurityGroup("eks-sg",
    vpc_id=vpc.id,
    description="EKS cluster security group",
    ingress=[
        # Allow traffic from the node group security group
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=443,
            to_port=443,
            security_groups=[node_group_sg.id],
        ),
        # Allow traffic from trusted CIDRs (if any)
        *([
            aws.ec2.SecurityGroupIngressArgs(
                protocol="tcp",
                from_port=443,
                to_port=443,
                cidr_blocks=trusted_cidrs,
            )
        ] if trusted_cidrs else []),
    ],
    egress=[
        # Allow all outbound traffic (you can restrict this further if needed)
        aws.ec2.SecurityGroupEgressArgs(
            protocol="-1",
            from_port=0,
            to_port=0,
            cidr_blocks=["0.0.0.0/0"]  # Or restrict to only needed destinations
        )
    ])


lt = aws.ec2.LaunchTemplate("eks-nodegroup-lt",
    vpc_security_group_ids=[node_group_sg.id],
    key_name=ssh_keypair_name if ssh_keypair_name else None,
    image_id=ami_id if ami_id else None
)


# ----------------------------------------------------------------------------
# EKS Cluster and Node Group
# ----------------------------------------------------------------------------


cluster = aws.eks.Cluster("eks-cluster",
    name=cluster_name,                      
    role_arn=eks_role.arn,
    vpc_config=aws.eks.ClusterVpcConfigArgs(
        subnet_ids=subnet_ids,
        security_group_ids=[eks_sg.id],
        endpoint_public_access=public_access,
        public_access_cidrs=public_access_cidrs if public_access and public_access_cidrs else []
    ),
    deletion_protection=True, 
    tags={"Name": cluster_name})

node_group = aws.eks.NodeGroup("eks-node-group",
    cluster_name=cluster_name,
    node_group_name=node_group_name,
    node_role_arn=node_group_role.arn,
    subnet_ids=subnet_ids,
    scaling_config=aws.eks.NodeGroupScalingConfigArgs(
        desired_size=desired_capacity,
        min_size=min_capacity,
        max_size=max_capacity,
    ),
    instance_types=[instance_type],
    launch_template=aws.eks.NodeGroupLaunchTemplateArgs(
        id=lt.id,
        version="$Latest",
    ),    
    tags={
        "Name": "eks-node-group",
        f"k8s.io/cluster-autoscaler/enabled": "true",
        f"k8s.io/cluster-autoscaler/{cluster_name}": "owned",
    })

kube_provider = k8s.Provider("k8s-provider", kubeconfig=cluster.endpoint.apply(
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

# ----------------------------------------------------------------------------
# Storage Provisioners
# ----------------------------------------------------------------------------

if enable_efs:
    efs_sg = aws.ec2.SecurityGroup(
        "efs-sg",
        vpc_id=vpc.id,
        description="Security group for EFS",
        ingress=[
            aws.ec2.SecurityGroupIngressArgs(
                protocol="tcp",
                from_port=2049,
                to_port=2049,
                security_groups=[node_group_sg.id],
                description="Allow EFS traffic from worker nodes",
            )
        ],
        egress=[
            aws.ec2.SecurityGroupEgressArgs(
                protocol="-1",
                from_port=0,
                to_port=0,
                cidr_blocks=["0.0.0.0/0"],
            )
        ],
        tags={"Name": "efs-sg"},
        opts=ResourceOptions(depends_on=[node_group_sg]),
    )
    fs = aws.efs.FileSystem("efs-fs",
        deletion_protection=True,
        tags={"Name": f"{cluster_name}-efs"}
    )
    for i, subnet_id in enumerate(subnet_ids):
        mount_target = aws.efs.MountTarget(f"efs-mount-{i}",
            file_system_id=fs.id,
            subnet_id=subnet_id,
            security_groups=[efs_sg.id],
            tags={"Name": f"{cluster_name}-efs-mt-{i}"}
        )
    k8s.helm.v3.Chart("efs-csi-driver", k8s.helm.v3.ChartOpts(
        chart="aws-efs-csi-driver",
        version=efs_csi_driver_version,
        fetch_opts=k8s.helm.v3.FetchOpts(repo="https://kubernetes-sigs.github.io/aws-efs-csi-driver/"),
        namespace="kube-system",
    ), opts=ResourceOptions(provider=kube_provider, depends_on=[node_group]))

if enable_ebs:
    k8s.helm.v3.Chart("ebs-csi-driver", k8s.helm.v3.ChartOpts(
        chart="aws-ebs-csi-driver",
        version=ebs_csi_driver_version,
        fetch_opts=k8s.helm.v3.FetchOpts(repo="https://kubernetes-sigs.github.io/aws-ebs-csi-driver"),
        namespace="kube-system"
    ), opts=ResourceOptions(provider=kube_provider, depends_on=[node_group]))


# ----------------------------------------------------------------------------
# Ingress Controller
# ----------------------------------------------------------------------------

if enable_ingress:
    k8s.core.v1.Namespace("ingress-nginx", metadata={"name": "ingress-nginx"}, opts=ResourceOptions(provider=kube_provider))
    ingress = k8s.helm.v3.Chart("ingress-nginx",
        k8s.helm.v3.ChartOpts(
            chart="ingress-nginx",
            version=ingress_nginx_version,
            fetch_opts=k8s.helm.v3.FetchOpts(repo="https://kubernetes.github.io/ingress-nginx"),
            namespace="ingress-nginx",
            values=ingress_nginx_values
        ),
        opts=ResourceOptions(provider=kube_provider, depends_on=[node_group])
    )

# ----------------------------------------------------------------------------
# Prometheus Stack
# ----------------------------------------------------------------------------

if enable_prometheus:
    k8s.core.v1.Namespace("monitoring", metadata={"name": "monitoring"}, opts=ResourceOptions(provider=kube_provider))

    prometheus = k8s.helm.v3.Chart("kube-prometheus-stack",
        k8s.helm.v3.ChartOpts(
            chart="kube-prometheus-stack",
            version=prometheus_stack_version,
            fetch_opts=k8s.helm.v3.FetchOpts(repo="https://prometheus-community.github.io/helm-charts"),
            namespace="monitoring",
            values=prometheus_stack_values
        ),
        opts=ResourceOptions(provider=kube_provider, depends_on=[node_group])
    )
# ----------------------------------------------------------------------------
# IRSA: Enable OIDC and create a sample IAM role for service accounts
# ----------------------------------------------------------------------------

oidc = aws.iam.OpenIdConnectProvider("oidc-provider",
    client_id_list=["sts.amazonaws.com"],
    thumbprint_list=["9e99a48a9960b14926bb7f3b02e22da0ecd2e9d0"],
    url=cluster.identity["oidc"]["issuer"])

# ----------------------------------------------------------------------------
# Cluster Autoscaler 
# ----------------------------------------------------------------------------

autoscaler_policy = aws.iam.Policy(
    "cluster-autoscaler-policy",
    policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "autoscaling:DescribeAutoScalingGroups",
                    "autoscaling:DescribeAutoScalingInstances",
                    "autoscaling:DescribeLaunchConfigurations",
                    "autoscaling:DescribeTags",
                    "autoscaling:SetDesiredCapacity",
                    "autoscaling:TerminateInstanceInAutoScalingGroup",
                    "ec2:DescribeLaunchTemplateVersions"
                ],
                "Resource": "*"
            }
        ]
    })
)


autoscaler_role = aws.iam.Role(
    "cluster-autoscaler-role",
    assume_role_policy=oidc.url.apply(lambda url: json.dumps({
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Federated": oidc.arn
                },
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{url.replace('https://', '')}:sub": "system:serviceaccount:kube-system:cluster-autoscaler"
                    }
                }
            }
        ]
    }))
)

aws.iam.RolePolicyAttachment(
    "cluster-autoscaler-attach-policy",
    role=autoscaler_role.name,
    policy_arn=autoscaler_policy.arn
)

autoscaler_chart = k8s.helm.v3.Chart(
    "cluster-autoscaler",
    k8s.helm.v3.ChartOpts(
        chart="cluster-autoscaler",
        version=autoscaler_chart_version,
        fetch_opts=k8s.helm.v3.FetchOpts(
            repo="https://kubernetes.github.io/autoscaler"
        ),
        namespace="kube-system",
        values={
            "autoDiscovery": {
                "clusterName": cluster_name,
            },
            "awsRegion": region,
            "rbac": {
                "serviceAccount": {
                    "create": True,
                    "name": "cluster-autoscaler",
                    "annotations": {
                        "eks.amazonaws.com/role-arn": autoscaler_role.arn
                    }
                }
            },
            "extraArgs": {
                "skip-nodes-with-local-storage": "false",
                "expander": "least-waste",
                "balance-similar-node-groups": "true"
            }
        },
    ),
    opts=ResourceOptions(provider=kube_provider, depends_on=[node_group])
)



# ----------------------------------------------------------------------------
# Export kubeconfig
# ----------------------------------------------------------------------------

def generate_kubeconfig(cluster):
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

export("kubeconfig", generate_kubeconfig(cluster))
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
