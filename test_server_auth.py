# created 2026-11-26 by nia
# last modified 2026-11-26 by nia
# untested as of 2026-11-26
import os
import unittest
from unittest.mock import patch

import server

# authentication tests for server password
class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.username = "test-user"
        self.password = "test-password"
        self.salt = b"test-salt-16-bytes"
        self.password_hash = server.hash_password(self.password, self.salt)
        self.credentials = (self.username, self.salt, self.password_hash)

    # verifies credentials, accepts matching username and pass only
    def test_verify_credentials_accepts_only_matching_username_and_password(self):
        self.assertTrue(
            server.verify_credentials(self.username, self.password, self.credentials)
        )
        self.assertFalse(
            server.verify_credentials(self.username, "wrong-password", self.credentials)
        )
        self.assertFalse(
            server.verify_credentials("wrong-user", self.password, self.credentials)
        )
        self.assertFalse(server.verify_credentials(None, self.password, self.credentials))

    # read authentication credentials from environment variables
    def test_load_auth_credentials_reads_valid_environment_values(self):
        with patch.dict(
            os.environ,
            {
                "FILESERVER_USERNAME": self.username,
                "FILESERVER_PASSWORD_SALT": self.salt.hex(),
                "FILESERVER_PASSWORD_HASH": self.password_hash.hex(),
            },
        ):
            self.assertEqual(
                server.load_auth_credentials(),
                (self.username, self.salt, self.password_hash),
            )

    # reject missing credentials
    def test_load_auth_credentials_rejects_missing_or_malformed_values(self):
        with patch.dict(
            os.environ,
            {
                "FILESERVER_USERNAME": "",
                "FILESERVER_PASSWORD_SALT": self.salt.hex(),
                "FILESERVER_PASSWORD_HASH": self.password_hash.hex(),
            },
        ):
            with self.assertRaises(RuntimeError):
                server.load_auth_credentials()

        with patch.dict(
            os.environ,
            {
                "FILESERVER_USERNAME": self.username,
                "FILESERVER_PASSWORD_SALT": "not-hex",
                "FILESERVER_PASSWORD_HASH": self.password_hash.hex(),
            },
        ):
            with self.assertRaises(RuntimeError):
                server.load_auth_credentials()


if __name__ == "__main__":
    unittest.main()
