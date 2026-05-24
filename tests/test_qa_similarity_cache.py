from assistx.pipeline import qa_pipeline


def test_similarity_fast_path_returns_cached_with_provenance(monkeypatch):
    monkeypatch.setattr(qa_pipeline, "_embed_text", lambda text: [1.0, 0.0] if "hello" in text else [0.99, 0.01])

    store = {}
    zset = []

    class FakeRedis:
        def get(self, key):
            return store.get(key)

        def setex(self, key, ttl, value):
            store[key] = value

        def zadd(self, key, mapping):
            for k, score in mapping.items():
                zset.append((k, score))

        def zrevrange(self, key, start, end):
            return [k.encode("utf-8") if isinstance(k, str) else k for k, _ in sorted(zset, key=lambda x: x[1], reverse=True)]

        def zremrangebyscore(self, key, minv, maxv):
            return 0

    monkeypatch.setattr(qa_pipeline, "_rds", FakeRedis())

    answer_obj = {"answer": "cached answer", "computed": {"n": 1}}
    qa_pipeline._store_similar_entry("hello world", "fp123", answer_obj)
    out = qa_pipeline._find_similar_cached("hello there", "fp123")

    assert out is not None
    assert out["answer"] == "cached answer"
    assert out["similar_cached"] is True
    assert out["source_question"] == "hello world"
