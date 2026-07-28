"""Regression tests ensuring web startup never mutates the database schema."""

import ast
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


BACKEND = Path(__file__).resolve().parents[1]
MAIN = BACKEND / "main.py"
TEST_SECRET = "task161_test_only_4c28b9d17e6f3a5b8c0d2e4f6a9b1c3d"


class WebStartupSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MAIN.read_text(encoding="utf-8-sig")
        cls.tree = ast.parse(cls.source, filename=str(MAIN))

    def test_main_does_not_import_init_db(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse(
                    any(alias.name == "init_db" for alias in node.names),
                    "main.py must not import database.init_db",
                )

    def test_main_does_not_call_schema_or_scheduler_startup(self):
        prohibited = {"init_db", "create_all", "start_scheduler"}
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Name):
                self.assertNotIn(function.id, prohibited)
                self.assertNotIn("BackgroundScheduler", function.id)
                self.assertNotIn("AsyncIOScheduler", function.id)
            elif isinstance(function, ast.Attribute):
                self.assertNotIn(function.attr, prohibited)
                self.assertFalse(
                    isinstance(function.value, ast.Name)
                    and function.value.id == "scheduler"
                    and function.attr == "start",
                    "main.py must not call scheduler.start()",
                )
                self.assertNotIn("BackgroundScheduler", function.attr)
                self.assertNotIn("AsyncIOScheduler", function.attr)

    def test_main_contains_no_sql_ddl_text(self):
        ddl_keywords = ("CREATE", "ALTER", "DROP", "TRUNCATE")
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertFalse(any(word in node.value.upper() for word in ddl_keywords))

    def test_apscheduler_runtime_is_removed(self):
        requirements = (BACKEND / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("apscheduler", requirements)
        self.assertFalse((BACKEND / "scheduler.py").exists())
        self.assertFalse((BACKEND / "scripts" / "run_ndvi_scheduler.py").exists())

    def test_lifespan_is_an_async_context_manager(self):
        lifespan = next(
            (
                node
                for node in self.tree.body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan"
            ),
            None,
        )
        self.assertIsNotNone(lifespan, "main.py must retain lifespan")
        self.assertTrue(
            any(
                (isinstance(decorator, ast.Name) and decorator.id == "asynccontextmanager")
                or (
                    isinstance(decorator, ast.Attribute)
                    and decorator.attr == "asynccontextmanager"
                )
                for decorator in lifespan.decorator_list
            ),
            "lifespan must remain an async context manager",
        )

    def test_isolated_lifespan_never_initializes_schema_and_keeps_routes(self):
        child = textwrap.dedent(
            """
            import asyncio
            import os
            import sys
            from unittest.mock import Mock, patch

            sys.path.insert(0, os.environ["TASK161_BACKEND"])
            import database

            forbidden_init = Mock(side_effect=AssertionError("init_db called"))
            forbidden_connect = AssertionError("network or database connection attempted")
            with patch.object(database, "init_db", forbidden_init), \\
                 patch.object(database, "engine", Mock(connect=Mock(side_effect=forbidden_connect))), \\
                 patch("redis.from_url", return_value=Mock(ping=Mock())):
                import main
                async def exercise():
                    async with main.lifespan(main.app):
                        pass
                asyncio.run(exercise())
                if forbidden_init.called:
                    raise AssertionError("init_db called")
                routes = set(main.app.openapi()["paths"])
                expected = {
                    "/", "/health", "/api/auth/login", "/api/auth/me",
                    "/api/fields/", "/api/alerts/", "/api/ndvi/{field_id}/latest",
                    "/api/satellite-indices/coverage",
                }
                if not expected.issubset(routes):
                    raise AssertionError("required OpenAPI routes are missing")
            """
        )
        environment = os.environ.copy()
        environment["SECRET_KEY"] = TEST_SECRET
        environment["TASK161_BACKEND"] = str(BACKEND)
        result = subprocess.run(
            [sys.executable, "-c", child],
            cwd=BACKEND,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, "isolated lifespan check failed")


if __name__ == "__main__":
    unittest.main()
