# Example: Infrastructure-as-Code Discovery

Demonstrates cortex discovering infrastructure-as-code artifacts across
a project -- Dockerfiles, Docker Compose services, GitHub Actions
workflows, and Terraform configurations. This shows cortex's
whole-codebase value proposition: it maps not just source code, but the
full operational topology of a project.

## What This Example Contains

- `Dockerfile` -- application container (Python/uvicorn API server)
- `Dockerfile.worker` -- background worker container
- `docker-compose.yml` -- three-service stack (API, worker, Redis cache)
  with dependency edges and port mappings
- `.github/workflows/ci.yml` -- CI workflow with test and build jobs
- `.github/workflows/deploy.yml` -- deploy workflow triggered on tags
- `terraform/main.tf` -- root Terraform config referencing two modules
- `terraform/modules/vpc/main.tf` -- VPC module (network layer)
- `terraform/modules/ecs/main.tf` -- ECS module (compute layer)
- `.cortex/discover.yaml` -- discovery configuration using five
  strategies together

## Project Structure

```
05-infrastructure-as-code/
  Dockerfile                   (API container)
  Dockerfile.worker            (worker container)
  docker-compose.yml           (service stack)
  .github/
    workflows/
      ci.yml                   (CI pipeline)
      deploy.yml               (deploy pipeline)
  terraform/
    main.tf                    (root config, references modules)
    modules/
      vpc/
        main.tf                (network layer)
      ecs/
        main.tf                (compute layer)
  .cortex/
    discover.yaml              (five strategies configured)
```

## Strategies Used

This example exercises five built-in strategies, each targeting a
different kind of infrastructure artifact:

| Strategy | Glob | What It Extracts |
|---|---|---|
| `dockerfile` | `Dockerfile*` | Base images, build stages, exposed ports |
| `compose` | `docker-compose.yml` | Services, dependencies (`depends_on`), port mappings |
| `gh_workflow` | `.github/workflows/*.yml` | Workflow triggers, jobs, step actions |
| `yaml_meta` | `terraform/**/*.tf` | Metadata from Terraform config files |
| `boundary_entrypoint` | `docker-compose.yml` | Network boundaries and exposed entry points |

## Running Discovery

```bash
cd examples/05-infrastructure-as-code
cortex discover
```

## What the Graph Contains

After running discovery, the output JSON graph includes nodes and edges
from all five strategies, providing a unified view of the project's
infrastructure:

### Container Nodes (dockerfile strategy)

```json
{
  "file:Dockerfile": {
    "type": "file",
    "label": "Dockerfile",
    "props": {
      "file": "Dockerfile",
      "base_image": "python:3.12-slim",
      "expose": ["8000"],
      "source_strategy": "dockerfile"
    }
  },
  "file:Dockerfile.worker": {
    "type": "file",
    "label": "Dockerfile.worker",
    "props": {
      "file": "Dockerfile.worker",
      "base_image": "python:3.12-slim",
      "source_strategy": "dockerfile"
    }
  }
}
```

### Service Nodes (compose strategy)

```json
{
  "service:api": {
    "type": "service",
    "label": "api",
    "props": {
      "file": "docker-compose.yml",
      "ports": ["8000:8000"],
      "depends_on": ["cache"],
      "source_strategy": "compose"
    }
  },
  "service:worker": {
    "type": "service",
    "label": "worker",
    "props": {
      "file": "docker-compose.yml",
      "depends_on": ["cache"],
      "source_strategy": "compose"
    }
  },
  "service:cache": {
    "type": "service",
    "label": "cache",
    "props": {
      "file": "docker-compose.yml",
      "image": "redis:7-alpine",
      "ports": ["6379:6379"],
      "source_strategy": "compose"
    }
  }
}
```

### Workflow Nodes (gh_workflow strategy)

```json
{
  "config:ci": {
    "type": "config",
    "label": "CI",
    "props": {
      "file": ".github/workflows/ci.yml",
      "triggers": ["push", "pull_request"],
      "jobs": ["test", "build"],
      "source_strategy": "gh_workflow"
    }
  },
  "config:deploy": {
    "type": "config",
    "label": "Deploy",
    "props": {
      "file": ".github/workflows/deploy.yml",
      "triggers": ["push"],
      "jobs": ["deploy"],
      "source_strategy": "gh_workflow"
    }
  }
}
```

### Infrastructure Nodes (yaml_meta strategy)

Terraform files are extracted as generic config nodes with their
metadata preserved:

```json
{
  "config:terraform/main.tf": {
    "type": "config",
    "label": "terraform/main.tf",
    "props": {
      "file": "terraform/main.tf",
      "source_strategy": "yaml_meta"
    }
  }
}
```

### Boundary Nodes (boundary_entrypoint strategy)

The boundary strategy identifies services that expose ports to the
host, marking them as network entry points:

```json
{
  "boundary:api:8000": {
    "type": "boundary",
    "label": "api:8000",
    "props": {
      "file": "docker-compose.yml",
      "service": "api",
      "port": "8000",
      "source_strategy": "boundary_entrypoint"
    }
  }
}
```

### Edges

The graph connects these nodes with typed edges:

- `service:api` --depends_on--> `service:cache`
- `service:worker` --depends_on--> `service:cache`
- `service:api` --builds--> `file:Dockerfile`
- `service:worker` --builds--> `file:Dockerfile.worker`
- `config:ci` --job_dependency--> (test -> build)

## Why This Matters

Traditional code intelligence tools see only source code. Cortex sees
the whole picture:

- **Container topology**: which services exist, what they depend on,
  and how they connect
- **CI/CD structure**: which workflows run, what triggers them, and
  how jobs relate
- **Infrastructure modules**: what cloud resources are defined and how
  Terraform modules compose
- **Network boundaries**: where the system is exposed to the outside
  world

This means `cortex query "api"` returns not just the Python module,
but also the Dockerfile that packages it, the Compose service that
runs it, the CI job that tests it, and the ECS service that deploys
it.

## Customizing

- Add more Dockerfiles or Compose files to discover additional
  container configurations
- Add Kubernetes manifests with the `yaml_meta` strategy to discover
  pod/service definitions
- Combine with `python_module` or `typescript_exports` strategies to
  build a graph that spans both source code and infrastructure
- Use `cortex enrich` after discovery to add semantic descriptions
  to infrastructure nodes
