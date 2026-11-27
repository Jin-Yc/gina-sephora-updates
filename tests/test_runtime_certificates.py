"""Frozen HTTPS keeps verification enabled without developer certificate paths."""
import os
from pathlib import Path
import runpy
import ssl
import unittest
from unittest.mock import patch

import certifi


HOOK = Path(__file__).resolve().parents[1] / "packaging" / "runtime_certificates.py"


class RuntimeCertificateTests(unittest.TestCase):
    def test_bundle_supplies_trust_without_system_or_conda_certificates(self):
        with patch.dict(os.environ, {"SSL_CERT_FILE": "", "SSL_CERT_DIR": ""}), \
                patch.object(ssl.SSLContext, "_windows_cert_stores", ()):
            runpy.run_path(str(HOOK))
            context = ssl.create_default_context()
            self.assertEqual(certifi.where(), os.environ["SSL_CERT_FILE"])
            self.assertGreater(context.cert_store_stats()["x509_ca"], 100)
            self.assertTrue(context.check_hostname)
            self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)

    def test_explicit_company_bundle_is_preserved(self):
        with patch.dict(os.environ, {"SSL_CERT_FILE": "company-trust.pem"}):
            runpy.run_path(str(HOOK))
            self.assertEqual("company-trust.pem", os.environ["SSL_CERT_FILE"])


if __name__ == "__main__":
    unittest.main()
