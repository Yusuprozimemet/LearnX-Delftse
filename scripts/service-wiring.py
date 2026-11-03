"""Every service under services/ is wired into compose and Prometheus the way the first one was.

    python scripts/service-wiring.py [root]

Each extraction day had to repeat the same wiring for its new service, and the record shows it
dropped twice: matching-service had no scrape job after Day 21 (D22-012), application-service none
after Day 25 (D28-075), and a 30 s start_period set on Day 25 was shorter than the service's 39 s
start (D28-077; docs/paper, Appendix F). No day's spec owned it, so no day's audit saw it. This makes
it a rule instead: for each JVM service (a directory under services/ with a Dockerfile and an
application.yaml),

- docker-compose.yml has a service built from that directory, with a healthcheck;
- the healthcheck's start_period is at least MIN_START_PERIOD;
- the service's actuator listens on the management port, and observability/prometheus.yml scrapes
  it there, at <compose name>:<port>.

It prints each miss and exits 1 if there is any.
"""
import os
import re
import sys

import yaml

MANAGEMENT_PORT = 9090
# Application-service took 39 s to start on Day 28 (D28-077); 60 s leaves room on a slow machine.
MIN_START_PERIOD = 60
PORT_VALUE = re.compile(r"^(?:\$\{MANAGEMENT_PORT:(\d+)\}|(\d+))$")


def seconds(duration):
    """A compose duration (`120s`, `2m`, `1m30s`) in seconds, or None."""
    parts = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", str(duration or ""))
    if not parts or not any(parts.groups()):
        return None
    return int(parts.group(1) or 0) * 60 + int(parts.group(2) or 0)


def management_port(config):
    """The port an application.yaml's management.server.port names, its default if it is a variable."""
    value = str(((config or {}).get("management") or {}).get("server", {}).get("port", ""))
    m = PORT_VALUE.match(value)
    return int(m.group(1) or m.group(2)) if m else None


def services(root):
    """The JVM services under services/: those with a Dockerfile and an application.yaml."""
    base = os.path.join(root, "services")
    out = {}
    for name in sorted(os.listdir(base)):
        resources = os.path.join(base, name, "src", "main", "resources")
        config = next((os.path.join(resources, f) for f in ("application.yaml", "application.yml")
                       if os.path.exists(os.path.join(resources, f))), None)
        if os.path.exists(os.path.join(base, name, "Dockerfile")) and config:
            with open(config, encoding="utf-8") as f:
                out[name] = next(yaml.safe_load_all(f), None)
    return out


def built_from(entry):
    """The directory a compose service is built from, normalised (`services/account-service`)."""
    build = entry.get("build")
    context = build.get("context") if isinstance(build, dict) else build
    return os.path.normpath(context).replace(os.sep, "/") if context else None


def misses(root):
    with open(os.path.join(root, "docker-compose.yml"), encoding="utf-8") as f:
        compose = (yaml.safe_load(f) or {}).get("services") or {}
    with open(os.path.join(root, "observability", "prometheus.yml"), encoding="utf-8") as f:
        scrape = (yaml.safe_load(f) or {}).get("scrape_configs") or []
    targets = {t for job in scrape for sc in job.get("static_configs") or [] for t in sc.get("targets") or []}
    found = []
    for name, config in services(root).items():
        entry = next(((n, e) for n, e in compose.items() if built_from(e or {}) == f"services/{name}"), None)
        if not entry:
            found.append(f"{name}: no service in docker-compose.yml is built from services/{name}")
            continue
        compose_name, e = entry
        check = e.get("healthcheck")
        if not check or check.get("disable"):
            found.append(f"{name}: compose service {compose_name} has no healthcheck")
        else:
            start = seconds(check.get("start_period"))
            if start is None or start < MIN_START_PERIOD:
                found.append(f"{name}: healthcheck start_period {check.get('start_period')}, "
                             f"want at least {MIN_START_PERIOD}s")
        port = management_port(config)
        if port != MANAGEMENT_PORT:
            found.append(f"{name}: management.server.port is {port}, want {MANAGEMENT_PORT}")
        if f"{compose_name}:{MANAGEMENT_PORT}" not in targets:
            found.append(f"{name}: observability/prometheus.yml has no target {compose_name}:{MANAGEMENT_PORT}")
    return found


def main(argv):
    root = argv[1] if len(argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    found = misses(root)
    for line in found:
        print(line)
    print(f"{len(found)} misses over {len(services(root))} services")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
