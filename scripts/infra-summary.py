#!/usr/bin/env python3
"""
Summarises one infra-ci run for the dashboard: the criteria that ran and failed (infra-checks.py
--summary), the step outcomes, what the main plan creates by AWS service, and an estimate of what
it costs an hour, priced from the started plan since the default plan starts no tasks. Any input
may be missing; the summary still has what exists.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

# Approximate eu-west-1 list prices, recorded 2026-10; an estimate, not a quote.
PRICES = {"vcpu_hour": 0.04048, "gb_hour": 0.004445, "public_ipv4_hour": 0.005, "alb_hour": 0.0252,
          "rds": {"db.t4g.micro": 0.018}, "secret_month": 0.40}
HOURS_PER_MONTH = 730


def load(path):
    """The JSON at path, or None if the file is missing, empty or not JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None


def creates(plan):
    """The planned resource changes that create something (a replacement creates too)."""
    return [c for c in (plan or {}).get("resource_changes", []) if "create" in c["change"]["actions"]]


# The VPC's parts, named by several type prefixes, count as one: aws_subnet and aws_security_group
# are network, as aws_vpc_security_group_ingress_rule is.
NETWORK = {"vpc", "subnet", "route", "internet", "security", "nat", "eip"}


def resource_counts(changes):
    """Created resources by AWS service: aws_lb_listener counts under lb, aws_ecs_service under ecs."""
    services = (c["type"].split("_")[1] for c in changes)
    return dict(sorted(Counter("network" if s in NETWORK else s for s in services).items()))


def module_of(address):
    """The module a resource is in: module.service["x"].aws_ecs_service.this is in module.service["x"]."""
    return address.rsplit(".aws_", 1)[0]


def cost(changes):
    """What the created resources would cost an hour at PRICES, as items and a total."""
    def of_type(kind):
        return [(c["address"], c["change"]["after"]) for c in changes if c["type"] == kind]

    definitions = {module_of(a): after for a, after in of_type("aws_ecs_task_definition")}
    tasks = public = 0
    fargate = 0.0
    for address, service in of_type("aws_ecs_service"):
        task = definitions.get(module_of(address), {})
        count = service.get("desired_count") or 0
        vcpu = int(task.get("cpu") or 0) / 1024
        gb = int(task.get("memory") or 0) / 1024
        tasks += count
        fargate += count * (vcpu * PRICES["vcpu_hour"] + gb * PRICES["gb_hour"])
        if (service.get("network_configuration") or [{}])[0].get("assign_public_ip"):
            public += count

    # Each load balancer has a public address in each of its two availability zones.
    lbs = len(of_type("aws_lb"))
    addresses = public + 2 * lbs
    secrets = len(of_type("aws_secretsmanager_secret"))
    rows = [("Fargate tasks", tasks, fargate),
            ("Public IPv4 addresses", addresses, addresses * PRICES["public_ipv4_hour"]),
            ("Application load balancer", lbs, lbs * PRICES["alb_hour"]),
            ("Secrets Manager secrets", secrets, secrets * PRICES["secret_month"] / HOURS_PER_MONTH)]
    # An RDS class with no price is listed with no rate, and left out of the total.
    for instance_class, count in Counter(a["instance_class"] for _, a in of_type("aws_db_instance")).items():
        rate = PRICES["rds"].get(instance_class)
        rows.append((f"RDS {instance_class}", count, None if rate is None else count * rate))

    rows = [r for r in rows if r[1]]
    total = sum((p for _, _, p in rows if p is not None), 0.0)
    items = [dict(name=n, count=c, per_hour=None if p is None else round(p, 4)) for n, c, p in rows]
    return dict(items=items, per_hour=round(total, 3), region="eu-west-1",
                note="estimate from list prices, not billed spend; usage charges (logs, DynamoDB, SQS, data transfer) not counted")


def criteria(checks):
    """Each criterion that ran: pass, or fail when infra-checks.py named it in a failure."""
    if checks is None:
        return {}
    failed = set(checks.get("failed", []))
    ids = set(checks.get("ran", [])) | failed
    return {c: "fail" if c in failed else "pass"
            for c in sorted(ids, key=lambda c: tuple(int(p) for p in c[1:].split(".")))}


def steps(pairs):
    """name=outcome pairs; a step that never ran has an empty outcome, shown as skipped."""
    return {name: outcome or "skipped" for name, _, outcome in (p.partition("=") for p in pairs)}


def main():
    ap = argparse.ArgumentParser(description="Summarise an infra-ci run for the dashboard")
    ap.add_argument("--main-plan", help="the main root plan JSON (terraform show -json)")
    ap.add_argument("--started-plan",
                    help="the main root plan JSON with -var start_services=true; priced instead of --main-plan when given")
    ap.add_argument("--checks", help="the JSON that infra-checks.py --summary wrote")
    ap.add_argument("--step", action="append", default=[], help="name=outcome of a workflow step")
    ap.add_argument("--out", required=True, help="where to write the summary JSON")
    args = ap.parse_args()

    changes = creates(load(args.main_plan))
    started = load(args.started_plan)
    priced = creates(started) if started is not None else changes
    summary = dict(criteria=criteria(load(args.checks)), steps=steps(args.step),
                   resources=resource_counts(changes),
                   cost=cost(priced))
    Path(args.out).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
