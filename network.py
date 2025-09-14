import pulumi
import pulumi_aws as aws

def create_vpc(cluster_name, vpc_cidr, base_tags):
    vpc = aws.ec2.Vpc("eks-vpc",
        cidr_block=vpc_cidr,
        enable_dns_hostnames=True,
        enable_dns_support=True,
        tags={**base_tags, "Name": f"{cluster_name}-vpc"})
    igw = aws.ec2.InternetGateway("vpc-igw",
        vpc_id=vpc.id,
        tags={**base_tags, "Name": f"{cluster_name}-igw"})
    route_table = aws.ec2.RouteTable("vpc-rt",
        vpc_id=vpc.id,
        routes=[aws.ec2.RouteTableRouteArgs(
            cidr_block="0.0.0.0/0",
            gateway_id=igw.id,
        )],
        tags={**base_tags, "Name": f"{cluster_name}-rt"})
    azs = aws.get_availability_zones()
    subnet_ids = []
    num_azs = min(3, len(azs.names))
    if num_azs < 3:
        pulumi.log.warn(f"Only {num_azs} AZs available; creating {num_azs} subnets.")
    for i, az in enumerate(azs.names[:num_azs]):
        subnet = aws.ec2.Subnet(f"subnet-{az}",
            vpc_id=vpc.id,
            cidr_block=f"10.100.{i}.0/24",
            map_public_ip_on_launch=True,
            availability_zone=az,
            tags={
                **base_tags,
                "Name": f"{cluster_name}-subnet-{az}",
                f"kubernetes.io/cluster/{cluster_name}": "owned",
                "kubernetes.io/role/elb": "1",
                "kubernetes.io/role/internal-elb": "1",
            })
        subnet_ids.append(subnet.id)
        aws.ec2.RouteTableAssociation(f"subnet-rta-{az}",
            subnet_id=subnet.id,
            route_table_id=route_table.id)
    return vpc, igw, route_table, subnet_ids

def create_security_groups(vpc, trusted_cidrs, cluster_name, base_tags):
    # Node group SG
    node_group_sg = aws.ec2.SecurityGroup("nodegroup-sg",
        vpc_id=vpc.id,
        description="Security group for EKS worker nodes",
        ingress=[],
        egress=[aws.ec2.SecurityGroupEgressArgs(
            protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"]
        )],
        tags={**base_tags, "Name": f"{cluster_name}-node-sg"}
    )

    # Control plane SG
    ingress_rules = [
        aws.ec2.SecurityGroupIngressArgs(  # API from nodes (reverse direction)
            protocol="tcp", from_port=443, to_port=443, security_groups=[node_group_sg.id]
        ),
    ]
    if trusted_cidrs:
        ingress_rules.append(
            aws.ec2.SecurityGroupIngressArgs(
                protocol="tcp", from_port=443, to_port=443, cidr_blocks=trusted_cidrs
            )
        )

    eks_sg = aws.ec2.SecurityGroup("eks-sg",
        vpc_id=vpc.id,
        description="EKS control plane security group",
        ingress=ingress_rules,
        egress=[aws.ec2.SecurityGroupEgressArgs(
            protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"]
        )],
        tags={**base_tags, "Name": f"{cluster_name}-controlplane-sg"}
    )

    # Node SG rules
    aws.ec2.SecurityGroupRule("node-self-all",
        type="ingress", from_port=0, to_port=0, protocol="-1",
        security_group_id=node_group_sg.id, self=True)

    # Kubelet
    aws.ec2.SecurityGroupRule("node-from-controlplane-kubelet",
        type="ingress", from_port=10250, to_port=10250, protocol="tcp",
        security_group_id=node_group_sg.id, source_security_group_id=eks_sg.id)

    # NodePort range
    aws.ec2.SecurityGroupRule("node-from-controlplane-nodeport",
        type="ingress", from_port=30000, to_port=32767, protocol="tcp",
        security_group_id=node_group_sg.id, source_security_group_id=eks_sg.id)

    # Ephemeral recommended (control plane -> kubelet for logs/exec)
    aws.ec2.SecurityGroupRule("node-from-controlplane-ephemeral",
        type="ingress", from_port=1025, to_port=65535, protocol="tcp",
        security_group_id=node_group_sg.id, source_security_group_id=eks_sg.id)

    return node_group_sg, eks_sg