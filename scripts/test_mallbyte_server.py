"""Offline tests for host tooling. Subprocesses and system unit paths are mocked."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("server", Path(__file__).with_name("mallbyte-server.py"))
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
import install_timer as timer  # noqa: E402


def result(stdout="", code=0):
    return SimpleNamespace(stdout=stdout, stderr="redaction-test-sensitive-value", returncode=code)


class BootstrapTests(unittest.TestCase):
    def test_environment_and_scope_must_be_explicit(self):
        for argv in (["plan"], ["plan", "--environment", "qa"],
                     ["plan", "--environment", "production", "--project", "../other"],
                     ["deploy", "--environment", "development", "--pull"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                server.arguments(argv)

    def test_compose_selection_preserves_live_dev_override_and_separates_remote(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            override = root / "compose.override.yaml"
            override.touch()
            dev = server.managed_compose(root, "development", "mallbyte-example")
            prod = server.managed_compose(root, "production", "mallbyte-prod")
        self.assertIn(str(override), dev)
        self.assertNotIn(str(override), prod)
        self.assertIn(str(root / "backend/.env.production"), prod)
        self.assertIn(str(root / "deploy/compose.remote.yaml"), prod)

    def test_check_host_rejects_unsupported_platform(self):
        with patch.object(server.sys, "platform", "darwin"), self.assertRaisesRegex(server.Failure, "Linux"):
            server.check_host()

    def test_install_plan_uses_no_privileged_commands_or_unit_writes(self):
        opts = server.arguments(["install", "--environment", "staging", "--linger"])
        with (tempfile.TemporaryDirectory() as temp, patch.object(server, "SYSTEMD", Path(temp)),
              patch.object(server, "timer_conflicts", return_value=[]),
              patch.object(server, "execute", return_value=result()) as execute,
              patch("builtins.print") as display):
            server.reconcile_unit(opts)
            self.assertFalse(list(Path(temp).iterdir()))
        execute.assert_called_once()
        self.assertIn("--dry-run", execute.call_args.args[0])
        unit = display.call_args_list[0].args[0]
        self.assertIn("Requires=docker.service", unit)
        self.assertIn("boot --environment staging --project mallbyte-staging", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertNotIn("deploy --environment", unit)

    def test_repeated_install_accepts_same_unit_and_refuses_unmanaged_unit(self):
        opts = server.arguments(["install", "--environment", "staging", "--apply"])
        with (tempfile.TemporaryDirectory() as temp, patch.object(server, "SYSTEMD", Path(temp)),
              patch.object(server, "timer_conflicts", return_value=[]),
              patch.object(server, "execute", return_value=result()) as execute):
            unit = Path(temp) / "mallbyte-staging.service"
            unit.write_text(server.system_unit(opts))
            server.reconcile_unit(opts)
            before = execute.call_count
            unit.write_text("[Unit]\nDescription=Existing unrelated service\n")
            with self.assertRaisesRegex(server.Failure, "different configuration"):
                server.reconcile_unit(opts)
            self.assertEqual(execute.call_count, before)

    def test_lock_rejects_concurrent_rollout_and_releases_after_failure(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, "LOCK_DIR", Path(temp)):
            with server.deployment_lock("mallbyte-test"):
                with self.assertRaisesRegex(server.Failure, "host lock"), server.deployment_lock("mallbyte-test"):
                    self.fail("A second lock was acquired")
            with server.deployment_lock("mallbyte-test"):
                pass

    def test_runner_errors_do_not_expose_stdout_or_stderr(self):
        with patch.object(server.subprocess, "run", return_value=result("secret-stdout", code=1)):
            with self.assertRaises(server.Failure) as caught:
                server.execute(["docker", "compose", "config"], "Configuration")
        self.assertEqual(str(caught.exception),
                         "Configuration failed (exit 1). Check the corresponding service logs privately.")

    def test_boot_never_builds_or_pulls_and_waits_in_order(self):
        opts = server.arguments(["boot", "--environment", "development"])
        with (patch.object(server, "deployment_lock", unittest.mock.MagicMock()),
              patch.object(server, "assert_project"),
              patch.object(server, "execute", return_value=result()) as execute,
              patch.object(server, "wait_for") as wait):
            server.rollout(opts, ["compose"], {})
        commands = [args[0] for args, _ in execute.call_args_list]
        self.assertEqual([cmd[1] for cmd in commands], ["up", "run", "up", "up", "up", "up"])
        self.assertEqual([cmd[-1] for cmd in commands], ["redis", "migrate", "web", "celery_worker", "celery_beat", "frontend"])
        for cmd in commands:
            self.assertIn("never", cmd)
            if cmd[1] == "up":
                self.assertIn("--no-recreate", cmd)
        self.assertEqual([args[1] for args, _ in wait.call_args_list],
                         ["Celery Worker task delivery", "Fresh scheduled health snapshot"])

    def test_failed_migration_never_advances_to_workloads(self):
        opts = server.arguments(["boot", "--environment", "development"])
        with (patch.object(server, "deployment_lock", unittest.mock.MagicMock()),
              patch.object(server, "assert_project"),
              patch.object(server, "execute", side_effect=[result(), server.Failure("Migration")]) as execute,
              self.assertRaisesRegex(server.Failure, "Migration")):
            server.rollout(opts, ["compose"], {})
        self.assertEqual(execute.call_count, 2)

    def test_project_storage_cannot_be_replaced(self):
        source = json.dumps([{"Destination": "/data", "Name": "existing_data", "Source": "/existing"}])
        config = {"services": {"redis": {"volumes": [{"source": "data", "type": "volume", "target": "/data"}]}},
                  "volumes": {"data": {"name": "different_data"}}}
        with patch.object(server, "execute", side_effect=[result("standalone|redis|||\nabcd|redis|/owned checkout|/owned checkout/compose.yaml|1\n"), result(source)]):
            with self.assertRaisesRegex(server.Failure, "Persistent mounts would change"):
                server.assert_project(["compose", "--project-directory", "/owned checkout", "-f", "/owned checkout/compose.yaml"], "mallbyte-development", config)

    def test_config_rejects_restart_loops_unsafe_ports_and_socket(self):
        names = {"web", "celery_worker", "celery_beat", "frontend", "db", "redis", "migrate"}
        config = {"services": {name: {"restart": "no" if name == "migrate" else "unless-stopped",
                                     "environment": {"SECRET_KEY": "test", "DB_PASSWORD": "test", "DB_HOST": "db",
                                                     "DJANGO_SETTINGS_MODULE": "config.settings.base"}}
                               for name in names}}
        for name, targets in server.PERSISTENT_TARGETS.items():
            config["services"][name]["volumes"] = [{"source": "store", "target": target} for target in targets]
        server.validate_config(config, "development")
        with patch.dict(config["services"]["migrate"]["environment"], {"DB_HOST": "foreign-db"}):
            with self.assertRaisesRegex(server.Failure, "differ from web"):
                server.validate_config(config, "development")
        for name, field, value, reason in (
            ("migrate", "restart", "always", "restart policy"),
            ("web", "ports", [{"host_ip": "0.0.0.0"}], "loopback"),
            ("celery_beat", "scale", 2, "singleton"),
            ("web", "volumes", [{"type": "bind", "source": "/run", "target": "/host-run"}], "socket"),
        ):
            with patch.dict(config["services"][name], {field: value}), self.assertRaisesRegex(server.Failure, reason):
                server.validate_config(config, "development")
        with patch.dict(config["services"]["migrate"]["environment"], {"DB_HOST": "foreign-db"}):
            with self.assertRaisesRegex(server.Failure, "differ from web"):
                server.validate_config(config, "development")

    def test_production_rejects_non_https_and_unverified_redis(self):
        self.assertTrue(server.https_origin("https://api.example.invalid"))
        self.assertFalse(server.https_origin("https://api.example.invalid/path"))
        self.assertFalse(server.https_origin("http://api.example.invalid"))
        self.assertTrue(server.verified_redis("rediss://cache.example.invalid?ssl_cert_reqs=required"))
        self.assertFalse(server.verified_redis("rediss://cache.example.invalid?ssl_cert_reqs=none"))
        self.assertFalse(server.verified_redis("redis://cache.example.invalid"))


class TimerTests(unittest.TestCase):
    def test_enabled_legacy_and_active_disabled_timers_block_install(self):
        replies = [result("mallbyte-observability-ops.timer enabled enabled\n"),
                   result("mallbyte-observability-ops-monorepo.timer loaded active waiting\n")]
        with tempfile.TemporaryDirectory() as temp, patch.object(timer, "USER_DIR", Path(temp)):
            conflicts = timer.timer_conflicts("monorepo", "mallbyte-dev", run=unittest.mock.Mock(side_effect=replies))
        self.assertEqual(conflicts, ["mallbyte-observability-ops-monorepo.timer", "mallbyte-observability-ops.timer"])

    def test_repeated_install_does_not_overwrite_units(self):
        with (tempfile.TemporaryDirectory() as temp, patch.object(timer, "USER_DIR", Path(temp)),
              patch.object(timer, "execute"), patch.object(timer, "timer_conflicts", return_value=[])):
            timer.install_units("install", "staging", "mallbyte-staging", dry_run=True)
            self.assertFalse(list(Path(temp).iterdir()))
            timer.install_units("install", "staging", "mallbyte-staging", dry_run=False)
            before = {p.name: p.stat().st_mtime_ns for p in Path(temp).iterdir()}
            timer.install_units("install", "staging", "mallbyte-staging", dry_run=False)
            self.assertEqual({p.name: p.stat().st_mtime_ns for p in Path(temp).iterdir()}, before)
            script = next(Path(temp).glob("*.service")).read_text()
            self.assertIn("--environment staging --project mallbyte-staging", script)
            timer.install_units("uninstall", "staging", "mallbyte-staging", dry_run=False)
            self.assertFalse(list(Path(temp).iterdir()))
