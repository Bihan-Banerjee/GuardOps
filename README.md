```text
guardops/
│
├── cli/                          ← All CLI code lives here
│   ├── __init__.py               ← Makes cli/ a Python package
│   ├── main.py                   ← Root CLI group; all commands attach here
│   ├── commands/                 ← One file per CLI command
│   │   ├── __init__.py
│   │   ├── init_cmd.py           ← `guardops init`
│   │   ├── deploy_cmd.py         ← `guardops deploy`
│   │   ├── status_cmd.py         ← `guardops status`
│   │   └── logs_cmd.py           ← `guardops logs`
│   └── utils/                    ← Shared helpers used by all commands
│       ├── __init__.py
│       ├── output.py             ← Colored terminal output, spinners
│       ├── config.py             ← Read/write .guardops.yaml
│       └── system.py             ← Run shell commands, check dependencies
│
├── backend/                      ← Business logic (no CLI code here)
│   ├── __init__.py
│   └── pipeline/
│       ├── __init__.py
│       ├── builder.py            ← Docker build logic
│       └── deployer.py           ← kubectl/local k8s deploy logic
│
├── tests/                        ← Phase 1 basic tests
│   ├── __init__.py
│   ├── test_config.py
│   └── test_builder.py
│
├── scripts/
│   └── setup-local.sh            ← One-command local env setup
│
├── .env.example                  ← Template for environment variables
├── .gitignore
├── pyproject.toml                ← Package definition (replaces setup.py)
├── requirements.txt              ← Runtime dependencies
├── requirements-dev.txt          ← Dev-only dependencies (pytest, ruff, etc.)
└── README.md
```

# GuardOps

Autonomous DevSecOps CLI Platform for secure cloud deployments.

## Phase 1: Local Development Setup

### Prerequisites
- Python 3.11+
- Docker Desktop
- kubectl
- k3d (lightweight local Kubernetes)

### Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/yourname/guardops
cd guardops
chmod +x scripts/setup-local.sh
./scripts/setup-local.sh

# 2. Activate virtual environment (run in every new terminal)
source .venv/bin/activate

# 3. Start local Kubernetes cluster
k3d cluster create guardops-local --port "8080:80@loadbalancer"

# 4. Initialize a project
mkdir my-app && cd my-app
guardops init

# 5. Deploy
guardops deploy

# 6. Check status
guardops status

# 7. Stream logs
guardops logs
```

### CLI Commands

| Command | Description |
|---|---|
| `guardops init` | Initialize project config |
| `guardops deploy` | Build and deploy to Kubernetes |
| `guardops status` | Show deployment health |
| `guardops logs` | Stream pod logs |
| `guardops --help` | Full help |

### Development

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=. --cov-report=html

# Lint
ruff check .

# Type check
mypy .
```