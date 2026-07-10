"""
Permanent standard-library security regression tests for AgroSat auth.

No real database connection or write is performed.  All DB-layer
interactions use unittest.mock.
"""

import os
import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock

from jose import jwt

# A test-only secret that meets all security requirements.
_TEST_SECRET_KEY = "task154_test_only_9d4e7f6a0b1c2d3e4f5a6b7c8d9e0f12"

# These values *must* match the ones in backend/config.py for the tests to be
# meaningful.  They are reproduced here as a guard against accidental drift.
_KNOWN_DEFAULT = "change_me_in_production"
_ENV_EXAMPLE_PLACEHOLDER = "замените_на_длинную_случайную_строку_минимум_32_символа"

# Known detection-listed secrets for rejection tests.
_FORBIDDEN_SAMPLE = "changeme"
_AGROSAT_DEV_DEFAULT = "agrosat_dev_secret_key_change_in_prod"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token(secret, payload_overrides=None, extra_claims=None,
                algorithm="HS256"):
    """Create a signed JWT for test purposes."""
    now = datetime.utcnow()
    claims = {
        "sub": "1",
        "exp": now + timedelta(hours=1),
        "iat": now,
        "type": "access",
    }
    if payload_overrides:
        claims.update(payload_overrides)
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, secret, algorithm=algorithm)


# ---------------------------------------------------------------------------
# 1-5  Secret Key Validation (config.py)
# ---------------------------------------------------------------------------

class SecretKeyValidationTests(unittest.TestCase):
    """Tests for config.validate_runtime_security()."""

    def setUp(self):
        # Save original to restore
        import config as cfg
        self._orig_key = cfg.settings.secret_key

    def tearDown(self):
        import config as cfg
        cfg.settings.secret_key = self._orig_key

    def _call_validate(self, key_value):
        import config
        config.settings.secret_key = key_value
        config.validate_runtime_security()

    # --- 1. empty secret rejected ---
    def test_empty_secret_rejected(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call_validate("")
        self.assertIn("empty", str(ctx.exception).lower())

    # --- 2. short secret rejected ---
    def test_short_secret_rejected(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call_validate("short")
        self.assertIn("too short", str(ctx.exception).lower())

    # --- 3. known default secret rejected ---
    def test_known_default_secret_rejected(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._call_validate(_KNOWN_DEFAULT)
        self.assertIn("insecure placeholder", str(ctx.exception).lower())

    # --- 4. .env.example placeholder rejected ---
    def test_env_example_placeholder_rejected(self):
        with self.assertRaises(RuntimeError):
            self._call_validate(_ENV_EXAMPLE_PLACEHOLDER)

    # --- 4b. project-specific known default rejected ---
    def test_agrosat_dev_default_rejected(self):
        with self.assertRaises(RuntimeError):
            self._call_validate(_AGROSAT_DEV_DEFAULT)

    # --- 4c. forbidden secret list entries rejected ---
    def test_forbidden_secret_list_rejected(self):
        for val in ("changeme", "change_me", "replace_me", "secret",
                    "default", "test_secret"):
            with self.subTest(key=val):
                with self.assertRaises(RuntimeError):
                    self._call_validate(val)

    # --- 4d. whitespace-only rejected ---
    def test_whitespace_only_rejected(self):
        with self.assertRaises(RuntimeError):
            self._call_validate("   ")

    # --- 5. secure secret accepted ---
    def test_secure_secret_accepted(self):
        try:
            self._call_validate(_TEST_SECRET_KEY)
        except RuntimeError:
            self.fail("validate_runtime_security() raised RuntimeError for a valid secret")


# ---------------------------------------------------------------------------
# 6-7  JWT encode/decode secret validation (auth.py)
# ---------------------------------------------------------------------------

class JWTSecretValidationTests(unittest.TestCase):
    """Tests that create_access_token and get_current_user validate the key."""

    def setUp(self):
        import config
        self._orig_key = config.settings.secret_key
        config.settings.secret_key = _TEST_SECRET_KEY

    def tearDown(self):
        import config
        config.settings.secret_key = self._orig_key

    # --- 6. create_access_token rejects insecure secret ---
    def test_create_access_token_rejects_insecure(self):
        import config
        config.settings.secret_key = _KNOWN_DEFAULT
        from api.auth import create_access_token
        with self.assertRaises(RuntimeError):
            create_access_token(data={"sub": "1"})

    # --- 7. decode path rejects insecure/default secret before any DB query ---
    def test_decode_rejects_insecure_secret(self):
        import config
        config.settings.secret_key = _KNOWN_DEFAULT
        from api.auth import get_current_user

        token = _make_token(_KNOWN_DEFAULT)
        mock_db = MagicMock()

        with self.assertRaises(RuntimeError):
            import asyncio
            asyncio.run(get_current_user(token=token, db=mock_db))

        # DB should NOT have been queried — failure is in config, not DB lookup.
        mock_db.query.assert_not_called()


# ---------------------------------------------------------------------------
# 8-13  Token validation at decode time (auth.py get_current_user)
# ---------------------------------------------------------------------------

class TokenValidationTests(unittest.TestCase):
    """Tests for JWT decode requirements in get_current_user."""

    def setUp(self):
        import config
        self._orig_key = config.settings.secret_key
        config.settings.secret_key = _TEST_SECRET_KEY

    def tearDown(self):
        import config
        config.settings.secret_key = self._orig_key

    def _call_get_current_user(self, token, fake_user=None):
        """Drive get_current_user with a fake DB session."""
        from api.auth import get_current_user

        mock_db = MagicMock()
        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value

        if fake_user is not None:
            mock_filter.first.return_value = fake_user
        else:
            mock_filter.first.return_value = None

        import asyncio
        return asyncio.run(get_current_user(token=token, db=mock_db))

    def _make_fake_user(self, **kwargs):
        user = MagicMock()
        user.id = 1
        user.is_active = True
        user.role = "admin"
        user.enterprise_id = 1
        for k, v in kwargs.items():
            setattr(user, k, v)
        return user

    # --- 8. valid access token round-trip reaches DB lookup and returns active user ---
    def test_valid_token_round_trip(self):
        token = _make_token(_TEST_SECRET_KEY)
        fake_user = self._make_fake_user()
        result = self._call_get_current_user(token, fake_user)
        self.assertIsNotNone(result)
        self.assertEqual(result.id, 1)

    # --- 9. expired token rejected ---
    def test_expired_token_rejected(self):
        expired_payload = {
            "exp": datetime.utcnow() - timedelta(hours=1),
        }
        token = _make_token(_TEST_SECRET_KEY, payload_overrides=expired_payload)
        with self.assertRaises(Exception) as ctx:
            self._call_get_current_user(token, self._make_fake_user())
        self.assertIn("401", str(ctx.exception))

    # --- 10. token without exp rejected ---
    def test_token_without_exp_rejected(self):
        now = datetime.utcnow()
        claims = {
            "sub": "1",
            "iat": now,
            "type": "access",
        }
        token = jwt.encode(claims, _TEST_SECRET_KEY, algorithm="HS256")
        with self.assertRaises(Exception):
            self._call_get_current_user(token, self._make_fake_user())

    # --- 11. token without iat rejected ---
    def test_token_without_iat_rejected(self):
        now = datetime.utcnow()
        claims = {
            "sub": "1",
            "exp": now + timedelta(hours=1),
            "type": "access",
        }
        token = jwt.encode(claims, _TEST_SECRET_KEY, algorithm="HS256")
        with self.assertRaises(Exception):
            self._call_get_current_user(token, self._make_fake_user())

    # --- 12. token without sub rejected ---
    def test_token_without_sub_rejected(self):
        now = datetime.utcnow()
        claims = {
            "exp": now + timedelta(hours=1),
            "iat": now,
            "type": "access",
        }
        token = jwt.encode(claims, _TEST_SECRET_KEY, algorithm="HS256")
        with self.assertRaises(Exception):
            self._call_get_current_user(token, self._make_fake_user())

    # --- 13. token with type != access rejected ---
    def test_token_type_not_access_rejected(self):
        token = _make_token(_TEST_SECRET_KEY, extra_claims={"type": "refresh"})
        with self.assertRaises(Exception):
            self._call_get_current_user(token, self._make_fake_user())


# ---------------------------------------------------------------------------
# 14-15  Public registration guard (auth.py register_user)
# ---------------------------------------------------------------------------

class PublicRegistrationTests(unittest.TestCase):
    """Tests for register_user guard."""

    def setUp(self):
        import config
        self._orig_env = config.settings.environment
        self._orig_flag = config.settings.public_registration_enabled
        self._orig_key = config.settings.secret_key
        config.settings.secret_key = _TEST_SECRET_KEY

    def tearDown(self):
        import config
        config.settings.environment = self._orig_env
        config.settings.public_registration_enabled = self._orig_flag
        config.settings.secret_key = self._orig_key

    # --- 14. public registration disabled by default before any DB query ---
    def test_registration_disabled_in_dev_by_default(self):
        import config
        config.settings.environment = "development"
        config.settings.public_registration_enabled = False

        from api.auth import register_user
        from schemas.auth import UserRegister
        mock_db = MagicMock()
        user_in = UserRegister(
            email="test@example.com",
            password="TestPass123!",
            full_name="Test User",
        )
        with self.assertRaises(Exception) as ctx:
            register_user(user_in=user_in, db=mock_db)
        self.assertIn("403", str(ctx.exception))
        mock_db.add.assert_not_called()

    # --- 15. registration still rejected outside dev even if flag true ---
    def test_registration_rejected_outside_dev(self):
        import config
        config.settings.environment = "production"
        config.settings.public_registration_enabled = True

        from api.auth import register_user
        from schemas.auth import UserRegister
        mock_db = MagicMock()
        user_in = UserRegister(
            email="test@example.com",
            password="TestPass123!",
            full_name="Test User",
        )
        with self.assertRaises(Exception) as ctx:
            register_user(user_in=user_in, db=mock_db)
        self.assertIn("403", str(ctx.exception))

    # --- 15b. registration works in dev with flag enabled ---
    def test_registration_works_in_dev_with_flag(self):
        import config
        config.settings.environment = "development"
        config.settings.public_registration_enabled = True

        from api.auth import register_user
        from schemas.auth import UserRegister
        mock_db = MagicMock()
        # Simulate no existing user
        mock_db.query.return_value.filter.return_value.first.return_value = None

        user_in = UserRegister(
            email="test@example.com",
            password="TestPass123!",
            full_name="Test User",
        )
        result = register_user(user_in=user_in, db=mock_db)
        self.assertIsNotNone(result)
        mock_db.add.assert_called_once()


# ---------------------------------------------------------------------------
# 16-18  Seed script password validation (scripts/seed_data.py)
# ---------------------------------------------------------------------------

class SeedPasswordValidationTests(unittest.TestCase):
    """Tests for seed_data password handling."""

    def tearDown(self):
        # Clean up any env var we might have set
        os.environ.pop("AGROSAT_BOOTSTRAP_ADMIN_PASSWORD", None)

    def test_seed_source_has_no_direct_hash_of_plaintext_password(self):
        """No line like pwd_context.hash('some_string') should exist."""
        import ast
        import inspect
        import scripts.seed_data as seed_mod

        source = inspect.getsource(seed_mod)
        tree = ast.parse(source)

        class HashCallFinder(ast.NodeVisitor):
            def __init__(self):
                self.found = []

            def visit_Call(self, node):
                if (isinstance(node.func, ast.Attribute)
                        and node.func.attr == "hash"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "pwd_context"):
                    if node.args and isinstance(node.args[0], ast.Constant) \
                            and isinstance(node.args[0].value, str):
                        self.found.append(node)
                self.generic_visit(node)

        finder = HashCallFinder()
        finder.visit(tree)
        self.assertEqual(
            len(finder.found), 0,
            f"Plaintext string passed to pwd_context.hash(): "
            f"{[ast.dump(n) for n in finder.found]}"
        )

    def test_seed_source_prints_no_password(self):
        """No print statement containing password value (env var name is OK)."""
        import inspect
        import scripts.seed_data as seed_mod
        source = inspect.getsource(seed_mod)
        # Match print() calls that contain a password-like value pattern,
        # as distinct from the env-var name AGROSAT_BOOTSTRAP_ADMIN_PASSWORD.
        # The variable name in an instruction message is acceptable.
        pattern = r'print\([^)]*парол[^)]*\)|print\([^)]*(?<!ADMIN_)password[^)]*\)'
        matches = re.findall(pattern, source, re.IGNORECASE)
        self.assertEqual(len(matches), 0,
                         f"Password-printing output found: {matches}")

    def test_seed_password_validator_rejects_missing(self):
        from scripts.seed_data import _validate_bootstrap_password
        with self.assertRaises(RuntimeError) as ctx:
            _validate_bootstrap_password("")
        self.assertIn("empty", str(ctx.exception).lower())

    def test_seed_password_validator_rejects_whitespace(self):
        from scripts.seed_data import _validate_bootstrap_password
        with self.assertRaises(RuntimeError):
            _validate_bootstrap_password("   ")

    def test_seed_password_validator_rejects_short(self):
        from scripts.seed_data import _validate_bootstrap_password
        with self.assertRaises(RuntimeError) as ctx:
            _validate_bootstrap_password("Ab1!")
        self.assertIn("at least 16", str(ctx.exception))

    def test_seed_password_validator_rejects_no_upper(self):
        from scripts.seed_data import _validate_bootstrap_password
        with self.assertRaises(RuntimeError):
            _validate_bootstrap_password("abcdefghijklmn1!")

    def test_seed_password_validator_rejects_no_lower(self):
        from scripts.seed_data import _validate_bootstrap_password
        with self.assertRaises(RuntimeError):
            _validate_bootstrap_password("ABCDEFGHIJKLMN1!")

    def test_seed_password_validator_rejects_no_digit(self):
        from scripts.seed_data import _validate_bootstrap_password
        with self.assertRaises(RuntimeError):
            _validate_bootstrap_password("ABCDEFGHIJKLMNo!")

    def test_seed_password_validator_rejects_no_special(self):
        from scripts.seed_data import _validate_bootstrap_password
        with self.assertRaises(RuntimeError):
            _validate_bootstrap_password("ABCDEFGHIJKLMNo1")

    def test_seed_password_validator_rejects_old_hardcoded(self):
        from scripts.seed_data import _validate_bootstrap_password
        with self.assertRaises(RuntimeError) as ctx:
            _validate_bootstrap_password("AgroSat2024!")
        self.assertIn("known insecure", str(ctx.exception).lower())

    def test_seed_password_validator_accepts_strong(self):
        from scripts.seed_data import _validate_bootstrap_password
        strong = "Task154_Seed_Admin_P@ssw0rd!"
        validated = _validate_bootstrap_password(strong)
        self.assertEqual(validated, strong)


# ---------------------------------------------------------------------------
# Additional: key not logged / included in exception message
# ---------------------------------------------------------------------------

class SecretNotExposedTests(unittest.TestCase):
    """Validate that exception messages don't include the actual secret."""

    def test_exception_does_not_contain_secret_value(self):
        import config
        orig = config.settings.secret_key
        config.settings.secret_key = _KNOWN_DEFAULT
        try:
            config.validate_runtime_security()
        except RuntimeError as e:
            msg = str(e)
            self.assertNotIn(_KNOWN_DEFAULT, msg)
        finally:
            config.settings.secret_key = orig


if __name__ == "__main__":
    unittest.main()
