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

# Die steel positions
DIE_POSITIONS = ["upper", "lower"]

# Die steel types
DIE_STEEL_TYPES = ["enter", "exit"]

# Priority levels (categorical)
PRIORITY_LEVELS = ["low", "medium", "high", "urgent", "top"]

# Priority values for sorting (higher = more urgent)
PRIORITY_VALUES = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "urgent": 3,
    "top": 4
}

# Priority labels for display
PRIORITY_LABELS = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "urgent": "Urgent",
    "top": "Top"
}
