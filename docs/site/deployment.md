# Deployment

TCKDB can run locally for development or on a single server for a lab,
group, or small public instance.

The core services are:

- FastAPI backend: public HTTP API.
- Postgres with RDKit cartridge: chemistry-aware relational storage.
- MinIO or another S3-compatible service: artifact storage.

Clients should talk to the API. Postgres and MinIO should remain
private services.

## Which Deployment Mode?

- **Local development:** use this for backend work and private testing.
- **Self-hosted single node:** use this for a lab server, home server,
  or small VPS.
- **Shared private deployment:** use this when a group needs a private
  instance with seeded accounts.
- **HPC client access:** use this when compute jobs need to query or
  upload to an existing TCKDB instance.

## Guides

- [Deployment modes](../deployment/deployment_modes.md)
- [Local development](../deployment/local-v0.md)
- [Self-hosted single node](../deployment/self_hosted_single_node.md)
- [Shared private deployment](../deployment/shared-private-deployment.md)
- [HPC client access](../deployment/client-access-from-hpc.md)
- [Troubleshooting](../deployment/troubleshooting.md)
