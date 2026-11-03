"""scripts/service-wiring.py finds a service compose or Prometheus was not told about.

Each test writes a scratch tree with one wired service, takes one piece of the wiring away, and runs
the check over it as a subprocess.

    python scripts/test_service_wiring.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "service-wiring.py")

COMPOSE = """services:
  account-service:
    build: ./services/account-service
    healthcheck:
      test: ["CMD", "true"]
      start_period: 120s
  db:
    image: postgres:16
"""
PROMETHEUS = """scrape_configs:
  - job_name: example-account-service
    static_configs:
      - targets: ["account-service:9090"]
"""
APPLICATION = "management:\n  server:\n    port: ${MANAGEMENT_PORT:9090}\n"


class ServiceWiringTest(unittest.TestCase):
    def tree(self, compose=COMPOSE, prometheus=PROMETHEUS, application=APPLICATION, extra=None):
        root = tempfile.mkdtemp()
        files = {"docker-compose.yml": compose, "observability/prometheus.yml": prometheus,
                 "services/account-service/Dockerfile": "FROM eclipse-temurin:21\n",
                 "services/account-service/src/main/resources/application.yaml": application, **(extra or {})}
        for path, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(root, path)), exist_ok=True)
            with open(os.path.join(root, path), "w", encoding="utf-8") as f:
                f.write(text)
        return subprocess.run([sys.executable, SCRIPT, root], capture_output=True, text=True)

    def assertMiss(self, run, text):
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn(text, run.stdout)

    def test_a_wired_service_passes(self):
        run = self.tree()
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("0 misses over 1 services", run.stdout)

    def test_a_service_with_no_scrape_target_is_a_miss(self):
        # D22-012 and D28-075: the new service's scrape job was never added.
        self.assertMiss(self.tree(prometheus="scrape_configs: []\n"), "no target account-service:9090")

    def test_a_short_or_missing_start_period_is_a_miss(self):
        # D28-077: 30 s, against a 39 s start.
        self.assertMiss(self.tree(compose=COMPOSE.replace("120s", "30s")), "start_period 30s, want at least 60s")
        self.assertMiss(self.tree(compose=COMPOSE.replace("      start_period: 120s\n", "")), "start_period None")
        self.assertEqual(self.tree(compose=COMPOSE.replace("120s", "1m30s")).returncode, 0)

    def test_a_service_with_no_healthcheck_is_a_miss(self):
        compose = COMPOSE.replace('    healthcheck:\n      test: ["CMD", "true"]\n      start_period: 120s\n', "")
        self.assertMiss(self.tree(compose=compose), "has no healthcheck")
        self.assertMiss(self.tree(compose=COMPOSE.replace('test: ["CMD", "true"]', "disable: true")), "has no healthcheck")

    def test_a_service_compose_does_not_build_is_a_miss(self):
        self.assertMiss(self.tree(compose=COMPOSE.replace("./services/account-service", "./services/other")),
                        "no service in docker-compose.yml is built from services/account-service")
        # A build given as a context is the same build.
        context = COMPOSE.replace("build: ./services/account-service", "build:\n      context: ./services/account-service")
        self.assertEqual(self.tree(compose=context).returncode, 0)

    def test_an_actuator_on_another_port_is_a_miss(self):
        self.assertMiss(self.tree(application=APPLICATION.replace("9090", "8081")),
                        "management.server.port is 8081, want 9090")

    def test_a_directory_that_is_not_a_jvm_service_is_not_checked(self):
        run = self.tree(extra={"services/notes/README.md": "not a service\n"})
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("over 1 services", run.stdout)


if __name__ == "__main__":
    unittest.main()
