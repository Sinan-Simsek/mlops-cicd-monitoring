import os
from dotenv import load_dotenv
from sqlmodel import create_engine, SQLModel, Session

load_dotenv()  # take environment variables from .env.
SQLALCHEMY_DATABASE_URL = os.getenv('SQLALCHEMY_DATABASE_URL')
# print(SQLALCHEMY_DATABASE_URL)

# pool_pre_ping tests each pooled connection with a lightweight query before
# handing it out, transparently replacing dead ones. Without it, a Postgres
# restart leaves stale connections in the pool and requests fail with 500
# until the pool cycles them out.
engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=True, pool_pre_ping=True)

def create_db_and_tables():
    # SQLModel türünden tanımlanmış tüm sınıflara ait objelerin veri tabanı tabloslarını yarat ancak table=True ise.
    SQLModel.metadata.create_all(engine)

def get_db():
    db = Session(engine)  # 1. Create session
    try:
        # yield ensures database connections are always properly closed, preventing memory leaks and connection pool exhaustion.
        yield db              # 2. Pause here, give session to route 
        # Route uses the session...
        # Route finishes...
    finally:
        db.close()           # 3. Resume here, close session

