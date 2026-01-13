# core/database.py
import os

ENV = os.getenv("APP_ENV", "local").lower()

def get_connection():
    if ENV == "production":
        import pymssql
        return pymssql.connect(
            server=os.getenv("DB_SERVER"),
            user=os.getenv("DB_USERNAME"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_DATABASE"),
            port=int(os.getenv("DB_PORT", 1433))
        )
    else:
        import pyodbc
        return pyodbc.connect(
            f"DRIVER={{{os.getenv('DB_DRIVER')}}};"
            f"SERVER={os.getenv('DB_SERVER')};"
            f"DATABASE={os.getenv('DB_DATABASE')};"
            f"UID={os.getenv('DB_USERNAME')};"
            f"PWD={os.getenv('DB_PASSWORD')};"
            "TrustServerCertificate=yes;"
        )
