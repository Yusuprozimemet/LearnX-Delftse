"""The infra summary's counts, cost and criteria, from small plans built inline.

    python scripts/test_infra_summary.py
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("infra_summary", os.path.join(HERE, "infra-summary.py"))
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)


def change(address, kind, after, actions=("create",)):
    return dict(address=address, type=kind, change=dict(actions=list(actions), after=after))


class InfraSummaryTest(unittest.TestCase):
    def test_resources_are_counted_by_the_second_token_of_their_type_and_the_vpc_as_one(self):
        changes = [change("a", "aws_lb_listener", {}), change("b", "aws_lb", {}), change("c", "aws_ecs_service", {}),
                   change("d", "aws_subnet", {}), change("e", "aws_vpc_security_group_ingress_rule", {})]
        self.assertEqual(sd.resource_counts(changes), {"ecs": 1, "lb": 2, "network": 2})

    def test_only_creates_are_counted(self):
        plan = {"resource_changes": [change("a", "aws_s3_bucket", {}, ("update",)),
                                     change("b", "aws_s3_bucket", {}, ("delete",)),
                                     change("c", "aws_s3_bucket", {}, ("delete", "create")),
                                     change("d", "aws_s3_bucket", {}, ("create",))]}
        self.assertEqual([c["address"] for c in sd.creates(plan)], ["c", "d"])

    def test_fargate_is_priced_by_vcpu_and_memory_per_task(self):
        changes = [change('module.service["web"].aws_ecs_task_definition.this', "aws_ecs_task_definition",
                          {"cpu": "512", "memory": "1024"}),
                   change('module.service["web"].aws_ecs_service.this', "aws_ecs_service",
                          {"desired_count": 2, "network_configuration": [{"assign_public_ip": False}]})]
        cost = sd.cost(changes)
        # 0.5 vCPU and 1 GB per task, two tasks
        self.assertEqual(cost["items"], [{"name": "Fargate tasks", "count": 2, "per_hour": 0.0494}])
        self.assertEqual(cost["per_hour"], 0.049)

    def test_public_addresses_are_counted_for_public_tasks_and_two_per_load_balancer(self):
        changes = [change('module.service["web"].aws_ecs_service.this', "aws_ecs_service",
                          {"desired_count": 2, "network_configuration": [{"assign_public_ip": True}]}),
                   change('module.service["worker"].aws_ecs_service.this', "aws_ecs_service",
                          {"desired_count": 1, "network_configuration": [{"assign_public_ip": False}]}),
                   change("aws_lb.app", "aws_lb", {})]
        items = {i["name"]: i for i in sd.cost(changes)["items"]}
        self.assertEqual({n: i["count"] for n, i in items.items()},
                         {"Fargate tasks": 3, "Public IPv4 addresses": 4, "Application load balancer": 1})
        self.assertEqual(items["Public IPv4 addresses"]["per_hour"], 0.02)

    def test_an_rds_class_without_a_price_is_listed_but_left_out_of_the_total(self):
        changes = [change("aws_db_instance.main", "aws_db_instance", {"instance_class": "db.t4g.micro"}),
                   change("aws_db_instance.big", "aws_db_instance", {"instance_class": "db.r6g.large"})]
        cost = sd.cost(changes)
        self.assertIn({"name": "RDS db.r6g.large", "count": 1, "per_hour": None}, cost["items"])
        self.assertIn({"name": "RDS db.t4g.micro", "count": 1, "per_hour": 0.018}, cost["items"])
        self.assertEqual(cost["per_hour"], 0.018)

    def test_criteria_that_failed_are_fail_and_the_rest_that_ran_pass(self):
        got = sd.criteria({"ran": ["C32.10", "C32.9", "C32.2", "C36.1"], "failed": ["C32.9", "C36.2"]})
        self.assertEqual(got, {"C32.2": "pass", "C32.9": "fail", "C32.10": "pass", "C36.1": "pass", "C36.2": "fail"})
        self.assertEqual(list(got), ["C32.2", "C32.9", "C32.10", "C36.1", "C36.2"])

    def test_missing_inputs_and_unrun_steps_still_write_a_summary(self):
        self.assertEqual(sd.steps(["fmt=success", "plan="]), {"fmt": "success", "plan": "skipped"})
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "summary.json")
            argv = ["infra-summary.py", "--main-plan", os.path.join(tmp, "absent.json"), "--out", out]
            with mock.patch.object(sys, "argv", argv):
                sd.main()
            with open(out, encoding="utf-8") as f:
                summary = json.load(f)
        self.assertEqual(summary["criteria"], {})
        self.assertEqual(summary["resources"], {})
        self.assertEqual(summary["cost"]["items"], [])
        self.assertEqual(summary["cost"]["per_hour"], 0)

    def test_the_started_plan_is_priced_and_the_resources_come_from_the_main_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for name, desired in (("main", 0), ("started", 2)):
                changes = [
                    change('module.service["web"].aws_ecs_task_definition.this', "aws_ecs_task_definition",
                           {"cpu": "512", "memory": "1024"}),
                    change('module.service["web"].aws_ecs_service.this', "aws_ecs_service",
                           {"desired_count": desired, "network_configuration": [{"assign_public_ip": False}]})]
                if name == "started":
                    changes.append(change('module.service["web"].aws_appautoscaling_target.service[0]',
                                          "aws_appautoscaling_target", {}))
                plan = {"resource_changes": changes}
                paths[name] = os.path.join(tmp, f"{name}.json")
                with open(paths[name], "w", encoding="utf-8") as f:
                    json.dump(plan, f)
            out = os.path.join(tmp, "summary.json")
            argv = ["infra-summary.py", "--main-plan", paths["main"], "--started-plan", paths["started"], "--out", out]
            with mock.patch.object(sys, "argv", argv):
                sd.main()
            with open(out, encoding="utf-8") as f:
                summary = json.load(f)
        self.assertEqual(summary["cost"]["items"], [{"name": "Fargate tasks", "count": 2, "per_hour": 0.0494}])
        self.assertEqual(summary["resources"], {"ecs": 2})


if __name__ == "__main__":
    unittest.main()
