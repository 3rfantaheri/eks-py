import pulumi_aws as aws
import pulumi_kubernetes as k8s
from pulumi import ResourceOptions
import json

def setup_oidc(cluster):
    return aws.iam.OpenIdConnectProvider("oidc-provider",
        client_id_list=["sts.amazonaws.com"],
        thumbprint_list=["9e99a48a9960b14926bb7f3b02e22da0ecd2e9d0"],
        url=cluster.identity["oidc"]["issuer"],
        opts=ResourceOptions(depends_on=[cluster])
    )

def setup_autoscaler(cfg, oidc, kube_provider, node_group, cluster_name, region):
    autoscaler_policy = aws.iam.Policy(
        "cluster-autoscaler-policy",
        policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
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
                    "eks:DescribeNodegroup"
                ],
                "Resource": "*"
            }]
        })
    )
    autoscaler_role = aws.iam.Role(
        "cluster-autoscaler-role",
        assume_role_policy=oidc.url.apply(lambda url: json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Federated": oidc.arn},
                "Action": "sts:AssumeRoleWithWebIdentity",
                "Condition": {
                    "StringEquals": {
                        f"{url.replace('https://', '')}:sub": "system:serviceaccount:kube-system:cluster-autoscaler",
                        f"{url.replace('https://', '')}:aud": "sts.amazonaws.com"
                    }
                }
            }]
        }))
    )
    aws.iam.RolePolicyAttachment(
        "cluster-autoscaler-attach-policy",
        role=autoscaler_role.name,
        policy_arn=autoscaler_policy.arn
    )
    k8s.helm.v3.Chart(
        "cluster-autoscaler",
        k8s.helm.v3.ChartOpts(
            chart="cluster-autoscaler",
            version=cfg["autoscaler_chart_version"],
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