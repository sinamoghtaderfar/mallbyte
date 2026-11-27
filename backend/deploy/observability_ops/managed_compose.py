"""Compose selection shared by the deployment command and trusted host runner."""

DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]


def managed_compose(root, environment, project):
    args = [*DOCKER, "compose", "--project-name", project]
    if environment == "development":
        args += ["--project-directory", str(root), "--env-file", str(root / ".env"), "-f", str(root / "compose.yaml")]
        override = root / "compose.override.yaml"
        if override.is_file():
            args += ["-f", str(override)]
    else:
        backend = root / "backend"
        args += ["--project-directory", str(backend), "--env-file", str(backend / f".env.{environment}"),
                 "-f", str(backend / f"compose.{environment}.yaml"), "-f", str(root / "deploy/compose.remote.yaml")]
    return args
