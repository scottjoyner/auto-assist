# /app/worker.py
import os, sys, signal, time
import redis
from rq import Worker, Queue, Connection

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
QUEUE_NAME = os.getenv("RQ_QUEUE", "assistx")

listen = [QUEUE_NAME]
conn = redis.from_url(REDIS_URL)

def main():
    with Connection(conn):
        worker = Worker(list(map(Queue, listen)))
        worker.work(with_scheduler=True)

if __name__ == "__main__":
    main()
