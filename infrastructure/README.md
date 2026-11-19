# Infrastructure

For the public GitHub/AWS demo, use [the step-by-step AWS guide](../docs/aws-from-scratch.md) and the Terraform in `aws/`.
The prepared kit uses us-west-2, one EC2 instance with public HTTP/HTTPS, local private logs, SSM, GitHub OIDC and owner approval. No Lambda, S3 or DynamoDB is deployed. Live AWS validation is pending.

The Docker/Caddy files in this directory remain an alternative for a private controlled lab with your own domains; they are not required by the AWS guide.


The following Docker route is an optional alternative. AWS provisioning has not been executed.
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
