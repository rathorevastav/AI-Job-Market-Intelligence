from sqlalchemy import create_engine, text

DB_USER = "postgres"
DB_PASSWORD = "Vastav_9829"
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "job_market_db"

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL)

create_jobs_table = """
CREATE TABLE IF NOT EXISTS jobs (
    id SERIAL PRIMARY KEY,
    title TEXT,
    company TEXT,
    location TEXT,
    salary TEXT,
    skills TEXT,
    job_link TEXT,
    source TEXT,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

with engine.connect() as conn:
    conn.execute(text(create_jobs_table))
    conn.commit()

print("Jobs table created successfully!")