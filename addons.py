import pulumi_aws as aws
import pulumi_kubernetes as k8s
from pulumi import ResourceOptions

def setup_efs(cfg, vpc, node_group_sg, subnet_ids, cluster_name, kube_provider, node_group):
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
        aws.efs.MountTarget(f"efs-mount-{i}",
            file_system_id=fs.id,
            subnet_id=subnet_id,
            security_groups=[efs_sg.id],
            tags={"Name": f"{cluster_name}-efs-mt-{i}"}
        )
    k8s.helm.v3.Chart("efs-csi-driver", k8s.helm.v3.ChartOpts(
        chart="aws-efs-csi-driver",
        version=cfg["efs_csi_driver_version"],
        fetch_opts=k8s.helm.v3.FetchOpts(repo="https://kubernetes-sigs.github.io/aws-efs-csi-driver/"),
        namespace="kube-system",
    ), opts=ResourceOptions(provider=kube_provider, depends_on=[node_group]))

def setup_ebs(cfg, kube_provider, node_group):
    k8s.helm.v3.Chart("ebs-csi-driver", k8s.helm.v3.ChartOpts(
        chart="aws-ebs-csi-driver",
        version=cfg["ebs_csi_driver_version"],
        fetch_opts=k8s.helm.v3.FetchOpts(repo="https://kubernetes-sigs.github.io/aws-ebs-csi-driver"),
        namespace="kube-system"
    ), opts=ResourceOptions(provider=kube_provider, depends_on=[node_group]))

def setup_ingress(cfg, kube_provider, node_group):
    k8s.core.v1.Namespace("ingress-nginx", metadata={"name": "ingress-nginx"}, opts=ResourceOptions(provider=kube_provider))
    k8s.helm.v3.Chart("ingress-nginx",
        k8s.helm.v3.ChartOpts(
            chart="ingress-nginx",
            version=cfg["ingress_nginx_version"],
            fetch_opts=k8s.helm.v3.FetchOpts(repo="https://kubernetes.github.io/ingress-nginx"),
            namespace="ingress-nginx",
            values=cfg["ingress_nginx_values"]
        ),
        opts=ResourceOptions(provider=kube_provider, depends_on=[node_group])
    )

def setup_prometheus(cfg, kube_provider, node_group):
    k8s.core.v1.Namespace("monitoring", metadata={"name": "monitoring"}, opts=ResourceOptions(provider=kube_provider))
    k8s.helm.v3.Chart("kube-prometheus-stack",
        k8s.helm.v3.ChartOpts(
            chart="kube-prometheus-stack",
            version=cfg["prometheus_stack_version"],
            fetch_opts=k8s.helm.v3.FetchOpts(repo="https://prometheus-community.github.io/helm-charts"),
            namespace="monitoring",
            values=cfg["prometheus_stack_values"]
        ),
        opts=ResourceOptions(provider=kube_provider, depends_on=[node_group])
    )