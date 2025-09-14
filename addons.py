import pulumi_aws as aws
import pulumi_kubernetes as k8s
from pulumi import ResourceOptions

def setup_efs(cfg, vpc, node_group_sg, subnet_ids, cluster_name, kube_provider, node_group, base_tags):
    efs_sg = aws.ec2.SecurityGroup(
        "efs-sg",
        vpc_id=vpc.id,
        description="EFS",
        ingress=[
            aws.ec2.SecurityGroupIngressArgs(
                protocol="tcp",
                from_port=2049,
                to_port=2049,
                security_groups=[node_group_sg.id],
            )
        ],
        egress=[
            aws.ec2.SecurityGroupEgressArgs(protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"])
        ],
        tags={**base_tags, "Name": f"{cluster_name}-efs-sg"},
        opts=ResourceOptions(depends_on=[node_group_sg]),
    )
    fs = aws.efs.FileSystem(
        "efs-fs",
        deletion_protection=cfg["efs_deletion_protection"],
        tags={**base_tags, "Name": f"{cluster_name}-efs"},
    )
    for i, subnet_id in enumerate(subnet_ids):
        aws.efs.MountTarget(
            f"efs-mt-{i}",
            file_system_id=fs.id,
            subnet_id=subnet_id,
            security_groups=[efs_sg.id],
        )
    # External EFS CSI driver typically installed separately; skip chart here (optional).

def setup_ebs(cfg, kube_provider, node_group, base_tags):
    k8s.helm.v3.Chart(
        "ebs-csi",
        k8s.helm.v3.ChartOpts(
            chart="aws-ebs-csi-driver",
            version=cfg["ebs_csi_driver_version"],
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo="https://kubernetes-sigs.github.io/aws-ebs-csi-driver"
            ),
            namespace="kube-system",
            values={},
        ),
        opts=ResourceOptions(provider=kube_provider, depends_on=[node_group]),
    )

def setup_ingress(cfg, kube_provider, node_group, base_tags):
    k8s.helm.v3.Chart(
        "ingress-nginx",
        k8s.helm.v3.ChartOpts(
            chart="ingress-nginx",
            version=cfg["ingress_nginx_version"],
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo="https://kubernetes.github.io/ingress-nginx"
            ),
            namespace="ingress-nginx",
            values=cfg["ingress_nginx_values"],
        ),
        opts=ResourceOptions(provider=kube_provider, depends_on=[node_group]),
    )

def setup_prometheus(cfg, kube_provider, node_group, base_tags):
    k8s.helm.v3.Chart(
        "kube-prom-stack",
        k8s.helm.v3.ChartOpts(
            chart="kube-prometheus-stack",
            version=cfg["prometheus_stack_version"],
            fetch_opts=k8s.helm.v3.FetchOpts(
                repo="https://prometheus-community.github.io/helm-charts"
            ),
            namespace="monitoring",
            values=cfg["prometheus_stack_values"],
        ),
        opts=ResourceOptions(provider=kube_provider, depends_on=[node_group]),
    )