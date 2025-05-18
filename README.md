# NCAcademy Platform

NCAcademy is an experimental platform for AI‑supported education. The project aims to provide an environment for building machine learning models and agentic workflows that can help educators and students.

## Project Goals

* **Teacher Assistance** – tools that help generate lesson plans, build homework sets from textbooks and assignments, and assist with grading.
* **Student Tutoring** – interactive agents that guide students through homework with access to specialised models.

The long‑term vision is a containerised service that can be deployed on‑premises or to the cloud. This repository contains the starting point for that effort.

## Directory Structure

```text
src/              Application source code (empty for now)
docker/           Docker build context
  Dockerfile      Base image used by `docker-compose`
docker-compose.yml  Compose file for local development
requirements.txt    Python dependencies
```

## Tooling

* **Docker / Docker‑Compose** – containerisation for the application.
* **Python 3.11** – initial runtime for agentic workflows; can be extended with TypeScript or other languages.

## Getting Started

1. Build and start the development container:

   ```bash
   docker-compose up --build
   ```

2. Modify source code in `src/` to implement features.

