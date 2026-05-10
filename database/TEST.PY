from sqlalchemy import create_engine

DB_USER = "postgres"
DB_PASSWORD = "Vastav_9829"
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "job_market_db"

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)

try:
    with engine.connect() as conn:
        print("Database connected successfully!")

except Exception as e:
    print("Connection failed:")
    print(e)