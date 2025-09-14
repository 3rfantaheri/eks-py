import json
import pulumi_aws as aws
import pulumi_kubernetes as k8s
from pulumi import ResourceOptions


def setup_oidc(cluster, thumbprint):
    issuer = cluster.identity.apply(lambda ident: ident["oidc"]["issuer"])
    return aws.iam.OpenIdConnectProvider(
        "oidc-provider",
        client_id_list=["sts.amazonaws.com"],
        thumbprint_list=[thumbprint],
        url=issuer,
        opts=ResourceOptions(depends_on=[cluster]),
    )


def setup_autoscaler(cfg, oidc, kube_provider, node_groups, cluster_name, region, base_tags):
    # IAM policy
    policy = aws.iam.Policy(
        "cluster-autoscaler-policy",
        policy=json.dumps(
            {
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
                            "autoscaling:DescribeScalingActivities",
                            "autoscaling:DescribeScheduledActions",
                            "ec2:DescribeLaunchTemplateVersions",
                            "ec2:DescribeInstanceTypes",
                            "eks:DescribeCluster",
                            "eks:DescribeNodegroup",
                        ],
                        "Resource": "*",
                    }
                ],
            }
        ),
        tags=base_tags,
    )

    role = aws.iam.Role(
        "cluster-autoscaler-role",
        assume_role_policy=oidc.url.apply(
            lambda url: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Federated": oidc.arn},
                            "Action": "sts:AssumeRoleWithWebIdentity",
                            "Condition": {
                                "StringEquals": {
                                    f"{url.replace('https://', '')}:sub": "system:serviceaccount:kube-system:cluster-autoscaler",
                                    f"{url.replace('https://', '')}:aud": "sts.amazonaws.com",
                                }
                            },
                        }
                    ],
                }
            )
        ),
        tags=base_tags,
    )

    aws.iam.RolePolicyAttachment(
        "cluster-autoscaler-policy-attach",
        role=role.name,
        policy_arn=policy.arn,
    )

    # Determine architectures from config (multi-arch friendly)
    arches = {ng["architecture"] for ng in cfg["node_groups"]}
    # Only pin nodeSelector if single arch to avoid scheduling deadlock on multi-arch clusters
    node_selector = None
    if len(arches) == 1:
        single = arches.pop()
        k8s_arch = "amd64" if single == "x86_64" else "arm64"
        node_selector = {"kubernetes.io/arch": k8s_arch}

    values = {
        "autoDiscovery": {"clusterName": cluster_name},
        "awsRegion": region,
        "rbac": {
            "serviceAccount": {
                "create": True,
                "name": "cluster-autoscaler",
                "annotations": {"eks.amazonaws.com/role-arn": role.arn},
            }
        },
        "extraArgs": {
            "skip-nodes-with-local-storage": "false",
            "expander": "least-waste",
            "balance-similar-node-groups": "true",
            "stderrthreshold": "info",
        },
        "podAnnotations": {
            "cluster-autoscaler.kubernetes.io/safe-to-evict": "false"
        },
    }
    if node_selector:
        values["nodeSelector"] = node_selector

    k8s.helm.v3.Chart(
        "cluster-autoscaler",
        k8s.helm.v3.ChartOpts(
            chart="cluster-autoscaler",
            version=cfg["autoscaler_chart_version"],
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo="https://kubernetes.github.io/autoscaler"
            ),
            namespace="kube-system",
            values=values,
        ),
        opts=ResourceOptions(
            provider=kube_provider,
            depends_on=node_groups,  # wait for all node groups
        ),
    )