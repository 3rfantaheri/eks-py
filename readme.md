# eks-py: EKS IaC with Pulumi python runtime

Provision a production-ready AWS EKS cluster with optional EFS, EBS, Ingress, and Prometheus using Pulumi (Python).

## Features

- Parameterized EKS cluster, node group, and VPC
- Optional EFS, EBS for storage
- Optional Ingress (NGINX), and Prometheus using Helm supporting and custom values
- Kubernetes Cluster Autoscaler
- Secure IAM roles and OIDC for IRSA

## Usage

1. **Install dependencies**
   ```sh
   pip install -r requirements.txt
   ```

2. **Configure your stack**
   - Copy `Pulumi.sample.yaml` to `Pulumi.<stack>.yaml` and edit values as needed.

note: if trusted_cidrs is empty, only nodes can access the API server (no direct kubectl from outside).

3. **Deploy**
   ```sh
   pulumi up
   ```

4. **Access outputs**
   - Kubeconfig and resource info are exported as stack outputs.



## Requirements

- Pulumi CLI
- AWS credentials
- Python 3.8+