"""
TCKDB backend app core config module
"""

import os

from dotenv import load_dotenv

# Determine if the application is running in a testing environment
TESTING = os.getenv("TESTING", "false").lower() == "true"
RUNNING_IN_DOCKER = os.getenv("RUNNING_IN_DOCKER", "false").lower() == "true"

# Set the path to the appropriate .env file
if TESTING:
    if RUNNING_IN_DOCKER:
        env_path = "./tckdb/backend/app/core/.env.test.docker"
    else:
        env_path = "./tckdb/backend/app/core/.env.test"
else:
    env_path = "./tckdb/backend/app/core/.env"

# Load environment variables from the determined .env file
load_dotenv(dotenv_path=env_path, override=True)


def getenv_boolean(
    var_name,
    default_value: bool = False,
) -> bool:
    result = default_value
    env_value = os.getenv(var_name)
    if env_value is not None:
        result = env_value.upper() in ("TRUE", "1")
    return result


API_V1_STR = "/api/v1"
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = os.urandom(32)

ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 8  # 60 minutes * 24 hours * 8 days = 8 days

SERVER_NAME = os.getenv("SERVER_NAME")
SERVER_HOST = os.getenv("SERVER_HOST")

# a string of origins separated by commas, e.g:
# "http://localhost, http://localhost:4200, http://localhost:3000, http://localhost:8080"
BACKEND_CORS_ORIGINS = os.getenv("BACKEND_CORS_ORIGINS")

PROJECT_NAME = os.getenv("PROJECT_NAME")
SENTRY_DSN = os.getenv("SENTRY_DSN")

POSTGRES_SERVER = os.getenv("POSTGRES_SERVER")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")
SQLALCHEMY_DATABASE_URI = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_SERVER}/{POSTGRES_DB}"
)

SMTP_TLS = getenv_boolean("SMTP_TLS", True)
SMTP_PORT = None
_SMTP_PORT = os.getenv("SMTP_PORT")
if _SMTP_PORT is not None:
    SMTP_PORT = int(_SMTP_PORT)
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAILS_FROM_EMAIL = os.getenv("EMAILS_FROM_EMAIL")
EMAILS_FROM_NAME = PROJECT_NAME
EMAIL_RESET_TOKEN_EXPIRE_HOURS = 48
EMAIL_TEMPLATES_DIR = "/app/email-templates/build"
EMAILS_ENABLED = SMTP_HOST and SMTP_PORT and EMAILS_FROM_EMAIL

FIRST_SUPERUSER = os.getenv("FIRST_SUPERUSER")
FIRST_SUPERUSER_PASSWORD = os.getenv("FIRST_SUPERUSER_PASSWORD")

USERS_OPEN_REGISTRATION = getenv_boolean("USERS_OPEN_REGISTRATION")

EMAIL_TEST_USER = "test@example.com"

FAST_API_PORT = os.getenv("FAST_API_PORT", "8000")

ENV = os.getenv("ENV")
