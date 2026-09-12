import os
import tempfile

# wheelcam.app builds a module-level app at import time; point it away from the real data/ directory
# so test runs never open, migrate or lock the user's workspace.
os.environ["WHEELCAM_DATA_DIR"] = tempfile.mkdtemp(prefix="wheelcam-test-")
