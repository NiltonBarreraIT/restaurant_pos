import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    if not SQLALCHEMY_DATABASE_URI:
        raise RuntimeError("❌ DATABASE_URL no está definida")

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    TIMEZONE = os.getenv("TIMEZONE", "America/Santiago")
    DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "CLP")
