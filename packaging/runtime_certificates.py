"""Give frozen urllib HTTPS clients a portable CA bundle before app imports."""
import os

import certifi


# Python still loads Windows' trusted ROOT/CA stores. The additional PEM avoids
# depending on a developer's Conda certificate path on another computer.
# Preserve an explicitly configured company CA bundle, including in child probes.
if not os.environ.get("SSL_CERT_FILE"):
    os.environ["SSL_CERT_FILE"] = certifi.where()
