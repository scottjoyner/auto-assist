# src/assistx/worker.py
import os, redis
from rq import Worker, Queue, Connection

def main():
    listen = [os.getenv("RQ_QUEUE", "default")]
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    conn = redis.from_url(redis_url)
    with Connection(conn):
        w = Worker(list(map(Queue, listen)))
        # enable scheduler so delayed/retry jobs work if present
        w.work(with_scheduler=True)

if __name__ == "__main__":
    main()
