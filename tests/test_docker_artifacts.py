import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class DockerArtifactsTests(unittest.TestCase):
    def test_dockerfile_exists_and_uses_python_slim(self):
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM python:3.14-slim-bookworm", dockerfile)
        self.assertIn("WORKDIR /app", dockerfile)
        self.assertIn("EXPOSE 8765", dockerfile)
        self.assertIn('"uvicorn"', dockerfile)
        self.assertIn('"app.main:app"', dockerfile)
        self.assertIn('"0.0.0.0"', dockerfile)
        self.assertIn('"8765"', dockerfile)
        self.assertNotIn("COPY .env", dockerfile)

    def test_docker_compose_maps_data_volume_and_inline_environment(self):
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("wechat-md-server:", compose)
        self.assertIn('image: your-namespace/wechat-md-server:latest', compose)
        self.assertIn('user: "0:0"', compose)
        self.assertIn("environment:", compose)
        self.assertIn("WECHAT_MD_APP_MASTER_KEY:", compose)
        self.assertIn("./data:/app/data", compose)
        self.assertIn('"8765:8765"', compose)

    def test_docker_compose_prod_uses_inline_runtime_environment_and_public_port_binding(self):
        compose = (PROJECT_ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")

        self.assertIn("wechat-md-server:", compose)
        self.assertIn('image: your-namespace/wechat-md-server:latest', compose)
        self.assertIn("environment:", compose)
        self.assertIn("WECHAT_MD_APP_MASTER_KEY:", compose)
        self.assertIn("WECHAT_MD_ADMIN_USERNAME:", compose)
        self.assertIn("WECHAT_MD_ADMIN_PASSWORD:", compose)
        self.assertIn('"8765:8765"', compose)
        self.assertIn("./data:/app/data", compose)
        self.assertIn("healthcheck:", compose)

    def test_dockerignore_excludes_local_and_runtime_artifacts(self):
        dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

        self.assertIn(".git/", dockerignore)
        self.assertIn(".venv/", dockerignore)
        self.assertIn("tests/", dockerignore)
        self.assertIn(".env", dockerignore)
        self.assertIn(".env.*", dockerignore)
        self.assertIn("_integration_output*/", dockerignore)

    def test_env_example_uses_container_paths(self):
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("WECHAT_MD_RUNTIME_CONFIG_PATH=/app/data/runtime-config.json", env_example)
        self.assertIn("WECHAT_MD_DEFAULT_OUTPUT_DIR=/app/data/workdir-output", env_example)

    def test_env_prod_example_uses_placeholders_and_container_paths(self):
        env_example = (PROJECT_ROOT / ".env.prod.example").read_text(encoding="utf-8")

        self.assertIn("WECHAT_MD_RUNTIME_CONFIG_PATH=/app/data/runtime-config.json", env_example)
        self.assertIn("WECHAT_MD_DEFAULT_OUTPUT_DIR=/app/data/workdir-output", env_example)
        self.assertIn("replace-with-your-fns-token", env_example)
        self.assertIn("replace-with-secret-access-key", env_example)


if __name__ == "__main__":
    unittest.main()
