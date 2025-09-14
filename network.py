import ipaddress
import pulumi
import pulumi_aws as aws


def create_vpc(cluster_name, vpc_cidr, base_tags, max_azs=None):
    """Create VPC + one public subnet per AZ (simple baseline)."""
    vpc = aws.ec2.Vpc(
        "vpc",
        cidr_block=vpc_cidr,
        enable_dns_hostnames=True,
        enable_dns_support=True,
        tags={**base_tags, "Name": f"{cluster_name}-vpc"},
    )
    igw = aws.ec2.InternetGateway(
        "igw",
        vpc_id=vpc.id,
        tags={**base_tags, "Name": f"{cluster_name}-igw"},
    )
    rt = aws.ec2.RouteTable(
        "rt-public",
        vpc_id=vpc.id,
        routes=[aws.ec2.RouteTableRouteArgs(cidr_block="0.0.0.0/0", gateway_id=igw.id)],
        tags={**base_tags, "Name": f"{cluster_name}-public-rt"},
    )

    azs = aws.get_availability_zones()
    total_azs = len(azs.names)
    if max_azs is None or max_azs > total_azs:
        max_azs = total_azs
    if max_azs < 2:
        pulumi.log.warn("Single AZ reduces availability.")

    try:
        net = ipaddress.ip_network(vpc_cidr)
    except ValueError:
        raise Exception(f"Invalid CIDR {vpc_cidr}")

    desired_prefix = 24
    if net.prefixlen >= desired_prefix:
        # If base block already narrower/equal than /24, subdivide minimally.
        fallback_prefix = min(net.prefixlen + 1, 28)
        if fallback_prefix > 32:
            raise Exception("CIDR too small for subdivision")
        subnets_pool = list(net.subnets(new_prefix=fallback_prefix))
    else:
        subnets_pool = list(net.subnets(new_prefix=desired_prefix))

    if len(subnets_pool) < max_azs:
        pulumi.log.warn(f"Insufficient subnets for {max_azs} AZs, using {len(subnets_pool)}")
        max_azs = len(subnets_pool)

    subnet_ids = []
    az_subnet_ids = {}
    for idx, az in enumerate(azs.names[:max_azs]):
        block = str(subnets_pool[idx])
        sn = aws.ec2.Subnet(
            f"subnet-{az}",
            vpc_id=vpc.id,
            cidr_block=block,
            map_public_ip_on_launch=True,
            availability_zone=az,
            tags={
                **base_tags,
                "Name": f"{cluster_name}-pub-{az}",
                f"kubernetes.io/cluster/{cluster_name}": "owned",
                "kubernetes.io/role/elb": "1",
            },
        )
        subnet_ids.append(sn.id)
        az_subnet_ids[az] = sn.id
        aws.ec2.RouteTableAssociation(
            f"rta-{az}",
            subnet_id=sn.id,
            route_table_id=rt.id,
        )

    return {
        "vpc": vpc,
        "internet_gateway": igw,
        "route_table_public": rt,
        "subnet_ids": subnet_ids,
        "az_subnet_map": az_subnet_ids,
    }


def create_security_groups(vpc, trusted_cidrs, cluster_name, base_tags):
    """Security groups for nodes and control plane."""
    node_sg = aws.ec2.SecurityGroup(
        "sg-nodes",
        vpc_id=vpc.id,
        description="EKS worker nodes",
        ingress=[],
        egress=[aws.ec2.SecurityGroupEgressArgs(protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"])],
        tags={**base_tags, "Name": f"{cluster_name}-nodes-sg"},
    )
    ingress_rules = [
        aws.ec2.SecurityGroupIngressArgs(
            protocol="tcp",
            from_port=443,
            to_port=443,
            security_groups=[node_sg.id],
        )
    ]
    if trusted_cidrs:
        ingress_rules.append(
            aws.ec2.SecurityGroupIngressArgs(
                protocol="tcp",
                from_port=443,
                to_port=443,
                cidr_blocks=trusted_cidrs,
            )
        )
    eks_sg = aws.ec2.SecurityGroup(
        "sg-controlplane",
        vpc_id=vpc.id,
        description="EKS control plane",
        ingress=ingress_rules,
        egress=[aws.ec2.SecurityGroupEgressArgs(protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"])],
        tags={**base_tags, "Name": f"{cluster_name}-cp-sg"},
    )
    aws.ec2.SecurityGroupRule(
        "nodes-self-all",
        type="ingress",
        from_port=0,
        to_port=0,
        protocol="-1",
        security_group_id=node_sg.id,
        self=True,
    )
    aws.ec2.SecurityGroupRule(
        "cp-to-kubelet",
        type="ingress",
        from_port=10250,
        to_port=10250,
        protocol="tcp",
        security_group_id=node_sg.id,
        source_security_group_id=eks_sg.id,
    )
    aws.ec2.SecurityGroupRule(
        "cp-to-nodeport",
        type="ingress",
        from_port=30000,
        to_port=32767,
        protocol="tcp",
        security_group_id=node_sg.id,
        source_security_group_id=eks_sg.id,
    )
    aws.ec2.SecurityGroupRule(
        "cp-to-ephemeral",
        type="ingress",
        from_port=1025,
        to_port=65535,
        protocol="tcp",
        security_group_id=node_sg.id,
        source_security_group_id=eks_sg.id,
    )
    return node_sg, eks_sg