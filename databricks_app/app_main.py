"""Starts WishBridge inside a Databricks App (option 3). On a laptop, use `Start WishBridge.cmd` instead.

Databricks Apps gives the port in DATABRICKS_APP_PORT and puts the app behind its own sign-in, so Streamlit
listens on every address of the app's container and leaves cross-site checks to that sign-in.
"""

import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
# the WishBridge package sits next to this file; background runs (python -m wishbridge) find it the same way
os.environ["PYTHONPATH"] = here + os.pathsep + os.environ.get("PYTHONPATH", "")
os.environ.setdefault("WISHBRIDGE_HOME", os.path.join(os.path.expanduser("~"), "wishbridge"))

os.execv(sys.executable, [
    sys.executable, "-m", "streamlit", "run", os.path.join(here, "wishbridge", "ui", "app.py"),
    "--server.port", os.environ.get("DATABRICKS_APP_PORT", "8000"),
    "--server.address", "0.0.0.0",
    "--server.headless", "true",
    "--server.enableCORS", "false",
    "--server.enableXsrfProtection", "false",
    "--server.maxUploadSize", "500",
    "--browser.gatherUsageStats", "false",
    "--client.toolbarMode", "minimal",
])
