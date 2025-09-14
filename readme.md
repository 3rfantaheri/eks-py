# eks-py: EKS IaC By Pulumi python

Provision a production-ready AWS EKS cluster with optional EFS, EBS, Ingress, and Prometheus using Pulumi (Python).

**note:** The project is under development — known issues exist, and new features are on the roadmap. I’d love for you to give it a try and share your feedback!


## Features

- Parameterized EKS cluster, node group, and VPC
- Kubernetes Cluster Autoscaler
- Secure IAM roles and OIDC for IRSA
- Config‑driven multi architecture node groups (x86_64 + arm64)
- Optional:
  - EFS filesystem + mount targets (security‑group restricted)
  - EBS CSI driver (Helm) with required IAM policy attachment (conditional)
  - Ingress NGINX (customizable Helm values)
  - Prometheus / Grafana (kube-prometheus-stack) with override values


## Roadmap

- Managed Add-ons
- External Secrets Integration
- Private cluster access patterns (bastion or SSM Session Manager)


## Usage

1. **Configure your stack**
   - Copy `Pulumi.sample.yaml` to `Pulumi.<stack>.yaml` and edit values as needed.

3. **Deploy**
   ```sh
   pip install -r requirements.txt
   pulumi stack init dev        # if new
   pulumi config set aws:region us-west-2
   cp Pulumi.sample.yaml Pulumi.dev.yaml  # adjust values
   pulumi up
   ```

4. **Access outputs**
 - `kubeconfig` (secret) – can be written to a file:

   ```sh
   pulumi stack output kubeconfig --show-secrets > kubeconfig
   export KUBECONFIG=$PWD/kubeconfig
   kubectl get nodes
   ```

5. **Cleanup**

    ```sh
    pulumi destroy
    pulumi stack rm <stack>
   ```

## Requirements

- Pulumi CLI
- AWS credentials
- Python 3.8+
