import logging

# Configure the root logger once
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s"
)

# Expose logger object for convenience
logger = logging.getLogger()
