import pymssql
import os

def get_connection():
    server = os.getenv("DB_SERVER")
    database = os.getenv("DB_DATABASE")
    user = os.getenv("DB_USERNAME")
    password = os.getenv("DB_PASSWORD")
    port = int(os.getenv("DB_PORT", 1433))

    conn = pymssql.connect(
        server=server,
        user=user,
        password=password,
        database=database,
        port=port
    )
    return conn
