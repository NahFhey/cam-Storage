"""
Configuration settings for CAM Tracking Kiosk
"""
import os

# Database settings
DATABASE_PATH = os.getenv("CAM_DB_PATH", "./cam_tracking.db")

# Application settings
AUTO_BUMP_ENABLED = os.getenv("AUTO_BUMP_ENABLED", "false").lower() == "true"
DEFAULT_OPERATOR = os.getenv("DEFAULT_OPERATOR", "kiosk")

# Server settings
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Stations (enforce with CHECK constraints)
STATIONS = ["active", "sharpen", "cabinet", "refill"]

# Priority labels
PRIORITY_LABELS = {
    0: "Low",
    1: "Medium",
    2: "High",
    3: "Urgent"
}
