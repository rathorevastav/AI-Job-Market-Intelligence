from database.connection import get_db_session
from database.crud import get_jobs

with get_db_session() as db:
    result = get_jobs(db, city="Bangalore")

    print(f"Total jobs found: {result['total']}")

    for job in result["items"]:
        print(f"{job.title} | {job.company_name}")