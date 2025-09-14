import pulumi_aws as aws

def create_eks_roles(cluster_name, base_tags):
    eks_role = aws.iam.Role(
        "eksClusterRole",
        name=f"{cluster_name}-eks-role",
        assume_role_policy=aws.iam.get_policy_document(statements=[
            aws.iam.GetPolicyDocumentStatementArgs(
                actions=["sts:AssumeRole"],
                principals=[aws.iam.GetPolicyDocumentStatementPrincipalArgs(
                    type="Service", identifiers=["eks.amazonaws.com"],
                )],
            )
        ]).json,
        tags=base_tags
    )
    for policy in [
        "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
        "arn:aws:iam::aws:policy/AmazonEKSServicePolicy",
    ]:
        aws.iam.RolePolicyAttachment(f"{eks_role._name}-{policy.split('/')[-1]}",
            role=eks_role.name, policy_arn=policy)

    node_group_role = aws.iam.Role(
        "eksNodeGroupRole",
        assume_role_policy=aws.iam.get_policy_document(statements=[
            aws.iam.GetPolicyDocumentStatementArgs(
                actions=["sts:AssumeRole"],
                principals=[aws.iam.GetPolicyDocumentStatementPrincipalArgs(
                    type="Service", identifiers=["ec2.amazonaws.com"],
                )],
            )
        ]).json,
        tags=base_tags
    )
    for policy in [
        "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
        "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
        "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    ]:
        aws.iam.RolePolicyAttachment(f"{node_group_role._name}-{policy.split('/')[-1]}",
            role=node_group_role.name, policy_arn=policy)

    return eks_role, node_group_role