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