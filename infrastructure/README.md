# Infrastructure

For the public GitHub/AWS demo, use [the step-by-step AWS guide](../docs/aws-from-scratch.md) and the Terraform in `aws/`.
The prepared kit uses us-west-2, EC2 without inbound ports, SSM, private S3/DynamoDB, two Lambda Function URLs, GitHub OIDC and owner approval. Live AWS validation is pending.

The Docker/Caddy files in this directory remain an alternative for a private controlled lab with your own domains; they are not required by the AWS guide.


Local development is the current target. AWS provisioning has not been executed.
Docker Compose is an optional co-located public HTTPS deployment; it has not been
validated against a running Docker daemon in this workspace.

1. Obtain two DNS names you control; point both A records to an isolated lab host.
2. Copy `config.example.json` to `config.json`, set lowercase real origins,
   `transport: public-https`, `data_dir: data`. Remove local socket / CA entries.
3. Use the same absolute repository/data directory for the runner and services.
   Set LAB_UID and LAB_GID to the dedicated unprivileged runner user's numeric
   IDs (`id -u` and `id -g`). Compose runs the endpoint containers as that same
   owner so 0600 config/evidence files remain private and writable. Never use
   UID 0 or make the data tree world-writable. No secrets belong in config.json.
4. Set DIAGNOSTIC_HOST and RECEIVER_HOST in your shell (hostnames, no https://).
   Run `docker compose -f infrastructure/compose.yaml up --build -d`.
5. Keep the dashboard bound to localhost; use an SSH tunnel if demonstrating remotely.
6. Register runs on this host using the same config; services reject unregistered IDs.
7. Stop with `docker compose -f infrastructure/compose.yaml down`.
   Add `--volumes` only when ready to delete Caddy certificates.

For EC2 later: use a dedicated account/VPC, no instance profile credentials, IMDSv2
required and preferably metadata disabled, encrypted small root disk, a current
Linux image, an ephemeral repository-scoped Actions runner, and no production
peering. Allow inbound 80/443 only as needed for ACME and diagnostics, SSH only
from your own address (or use SSM with a separately scoped admin role). No public
dashboard or Docker socket mount. Never run jobs as root. Tool restrictions are
lab safeguards, not a simulated gateway.

Use a short-lived instance and scheduled instance shutdown, a small instance type,
CloudWatch/AWS Budget alerts, 8-minute workflow limits and per-trial model caps.
Confirm exact regional prices before deploying. Teardown: deregister runner,
stop containers, terminate instance, remove EBS/EIP/snapshots and DNS records,
revoke lab credentials, then delete only the lab's retained data after export.
Terraform is intentionally deferred until the AWS account/network is specified.
