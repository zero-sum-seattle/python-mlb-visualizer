# MLB Stats Visualizer

Interactive MLB statistics visualization web application powered by
`python-mlb-statsapi`.

## Status

**Milestone 0 — Repository Foundation** is complete.

The project currently provides a FastAPI application with Jinja2 templates,
Pydantic Settings configuration, pytest coverage, Ruff linting/formatting, and
GitHub Actions CI. MLB data features are not included yet.

## Planned MVP

A local web application that:

- Retrieves MLB data through `python-mlb-statsapi`
- Presents interactive baseball statistics visualizations
- Runs with a clean Python web stack (FastAPI + Jinja2)

Later milestones will add data access, persistence, and visualization libraries.

## Technology stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.12 |
| Packaging | Poetry |
| Web framework | FastAPI |
| Templates | Jinja2 |
| Configuration | Pydantic Settings |
| Testing | pytest, httpx |
| Lint / format | Ruff |
| CI | GitHub Actions |

## Requirements

- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation)

## Installation

Install Poetry if you do not already have it:

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

Clone the repository and install dependencies:

```bash
git clone https://github.com/zero-sum-seattle/python-mlb-visualizer.git
cd python-mlb-visualizer
poetry install
```

Optionally copy the example environment file (defaults work without it):

```bash
cp .env.example .env
```

## Local development

Start the development server:

```bash
poetry run uvicorn app.main:app --reload
```

Then open [http://127.0.0.1:8000](http://127.0.0.1:8000).

- `/` — foundation HTML page
- `/health` — JSON health check

## Testing

```bash
poetry run pytest
```

## Lint and formatting

```bash
poetry run ruff check .
poetry run ruff format .
poetry run ruff format --check .
```

## Project structure

```text
.
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   └── web/
│       ├── __init__.py
│       ├── routes.py
│       └── templates/
│           ├── base.html
│           └── index.html
├── tests/
│   ├── __init__.py
│   └── test_web.py
├── .github/
│   └── workflows/
│       └── test.yml
├── .env.example
├── .gitignore
├── poetry.lock
├── pyproject.toml
└── README.md
```

## Later milestones

MLB data integration via `python-mlb-statsapi`, databases, and visualization
libraries will be added in later milestones. This foundation intentionally
excludes those dependencies.

## Disclaimer

This project is educational and is **not affiliated with Major League Baseball,
MLB Advanced Media, or any MLB club**. MLB names and marks belong to their
respective owners.
