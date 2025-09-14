import ipaddress
import pulumi
import pulumi_aws as aws


def create_vpc(cluster_name, vpc_cidr, base_tags):
    vpc = aws.ec2.Vpc(
        "eks-vpc",
        cidr_block=vpc_cidr,
        enable_dns_hostnames=True,
        enable_dns_support=True,
        tags={**base_tags, "Name": f"{cluster_name}-vpc"},
    )

    igw = aws.ec2.InternetGateway(
        "vpc-igw", vpc_id=vpc.id, tags={**base_tags, "Name": f"{cluster_name}-igw"}
    )

    route_table = aws.ec2.RouteTable(
        "vpc-rt",
        vpc_id=vpc.id,
        routes=[
            aws.ec2.RouteTableRouteArgs(
                cidr_block="0.0.0.0/0",
                gateway_id=igw.id,
            )
        ],
        tags={**base_tags, "Name": f"{cluster_name}-rt"},
    )

    azs = aws.get_availability_zones()
    max_azs = min(3, len(azs.names))
    if max_azs < 3:
        pulumi.log.warn(f"Only {max_azs} AZs available; creating {max_azs} subnets.")

    # Derive subnets from provided VPC CIDR (instead of hard-coded 10.100.*)
    try:
        net = ipaddress.ip_network(vpc_cidr)
    except ValueError:
        raise Exception(f"Invalid vpc_cidr '{vpc_cidr}'")

    # Target /24 if possible; else subdivide one level deeper than current
    desired_prefix = 24
    if net.version != 4:
        raise Exception("Only IPv4 CIDRs are supported currently.")
    if net.prefixlen >= desired_prefix:
        # Cannot split into /24; fallback to splitting one bit deeper if possible
        fallback_prefix = min(net.prefixlen + 1, 28)
        if fallback_prefix <= 32 and fallback_prefix > net.prefixlen:
            subnets_iter = list(net.subnets(new_prefix=fallback_prefix))
        else:
            subnets_iter = [net]
    else:
        subnets_iter = list(net.subnets(new_prefix=desired_prefix))

    if len(subnets_iter) < max_azs:
        pulumi.log.warn(
            f"CIDR {vpc_cidr} does not yield {max_azs} distinct subnets at /24; using {len(subnets_iter)}."
        )
        max_azs = min(max_azs, len(subnets_iter))

    subnet_ids = []
    for i, az in enumerate(azs.names[:max_azs]):
        cidr = str(subnets_iter[i])
        subnet = aws.ec2.Subnet(
            f"subnet-{az}",
            vpc_id=vpc.id,
            cidr_block=cidr,
            map_public_ip_on_launch=True,
            availability_zone=az,
            tags={
                **base_tags,
                "Name": f"{cluster_name}-subnet-{az}",
                f"kubernetes.io/cluster/{cluster_name}": "owned",
                "kubernetes.io/role/elb": "1",
                "kubernetes.io/role/internal-elb": "1",
            },
        )
        subnet_ids.append(subnet.id)
        aws.ec2.RouteTableAssociation(
            f"subnet-rta-{az}", subnet_id=subnet.id, route_table_id=route_table.id
        )

    return vpc, igw, route_table, subnet_ids


def create_security_groups(vpc, trusted_cidrs, cluster_name, base_tags):
    node_group_sg = aws.ec2.SecurityGroup(
        "nodegroup-sg",
        vpc_id=vpc.id,
        description="Security group for EKS worker nodes",
        ingress=[],
        egress=[
            aws.ec2.SecurityGroupEgressArgs(
                protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"]
            )
        ],
        tags={**base_tags, "Name": f"{cluster_name}-node-sg"},
    )

    ingress_rules = [
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=443,
            to_port=443,
            security_groups=[node_group_sg.id],
        ),
    ]
    if trusted_cidrs:
        ingress_rules.append(
            aws.ec2.SecurityGroupIngressArgs(
                protocol="tcp", from_port=443, to_port=443, cidr_blocks=trusted_cidrs
            )
        )

    eks_sg = aws.ec2.SecurityGroup(
        "eks-sg",
        vpc_id=vpc.id,
        description="EKS control plane security group",
        ingress=ingress_rules,
        egress=[
            aws.ec2.SecurityGroupEgressArgs(
                protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"]
            )
        ],
        tags={**base_tags, "Name": f"{cluster_name}-controlplane-sg"},
    )

    aws.ec2.SecurityGroupRule(
        "node-self-all",
        type="ingress",
        from_port=0,
        to_port=0,
        protocol="-1",
        security_group_id=node_group_sg.id,
        self=True,
    )

    aws.ec2.SecurityGroupRule(
        "node-from-controlplane-kubelet",
        type="ingress",
        from_port=10250,
        to_port=10250,
        protocol="tcp",
        security_group_id=node_group_sg.id,
        source_security_group_id=eks_sg.id,
    )

    aws.ec2.SecurityGroupRule(
        "node-from-controlplane-nodeport",
        type="ingress",
        from_port=30000,
        to_port=32767,
        protocol="tcp",
        security_group_id=node_group_sg.id,
        source_security_group_id=eks_sg.id,
    )

    aws.ec2.SecurityGroupRule(
        "node-from-controlplane-ephemeral",
        type="ingress",
        from_port=1025,
        to_port=65535,
        protocol="tcp",
        security_group_id=node_group_sg.id,
        source_security_group_id=eks_sg.id,
    )

    return node_group_sg, eks_sg