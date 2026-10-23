"""Starts WishBridge inside a Databricks App (option 3). On a laptop, use `Start WishBridge.cmd` instead.

Databricks Apps gives the port in DATABRICKS_APP_PORT and puts the app behind its own sign-in, so Streamlit
listens on every address of the app's container and leaves cross-site checks to that sign-in.
"""

import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
# The WishBridge package is in src/ at the root of the repository (a Git deployment and deploy-databricks-app.ps1
# upload the same layout). Background runs (python -m wishbridge) find it the same way.
src = os.path.join(os.path.dirname(here), "src")
os.environ["PYTHONPATH"] = src + os.pathsep + os.environ.get("PYTHONPATH", "")
os.environ.setdefault("WISHBRIDGE_HOME", os.path.join(os.path.expanduser("~"), "wishbridge"))

os.execv(sys.executable, [
    sys.executable, "-m", "streamlit", "run", os.path.join(src, "wishbridge", "ui", "app.py"),
    "--server.port", os.environ.get("DATABRICKS_APP_PORT", "8000"),
    "--server.address", "0.0.0.0",
    "--server.headless", "true",
    "--server.enableCORS", "false",
    "--server.enableXsrfProtection", "false",
    "--server.maxUploadSize", "500",
    "--browser.gatherUsageStats", "false",
    "--client.toolbarMode", "minimal",
])
