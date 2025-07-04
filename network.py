import pulumi
import pulumi_aws as aws

def create_vpc(cluster_name):
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
    return vpc, igw, route_table, subnet_ids

def create_security_groups(vpc, subnet_ids, trusted_cidrs, cluster_name):
    node_group_sg = aws.ec2.SecurityGroup("nodegroup-sg",
        vpc_id=vpc.id,
        description="Security group for EKS worker nodes",
        ingress=[
            aws.ec2.SecurityGroupIngressArgs(
                protocol="-1",
                from_port=0,
                to_port=0,
                cidr_blocks=[vpc.cidr_block]
            ),
        ],
        egress=[
            aws.ec2.SecurityGroupEgressArgs(
                protocol="-1",
                from_port=0,
                to_port=0,
                cidr_blocks=["0.0.0.0/0"]
            )
        ]
    )
    eks_sg = aws.ec2.SecurityGroup("eks-sg",
        vpc_id=vpc.id,
        description="EKS cluster security group",
        ingress=[
            aws.ec2.SecurityGroupIngressArgs(
                protocol="tcp",
                from_port=443,
                to_port=443,
                security_groups=[node_group_sg.id],
            ),
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
            aws.ec2.SecurityGroupEgressArgs(
                protocol="-1",
                from_port=0,
                to_port=0,
                cidr_blocks=["0.0.0.0/0"]
            )
        ]
    )
    return node_group_sg, eks_sg