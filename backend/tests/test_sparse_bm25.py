from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from app.retrieval import embeddings


class FakeBM25:
    def __init__(self):
        self.query_called = False
        self.passage_called = False

    def query_embed(self, text):
        self.query_called = True
        yield SimpleNamespace(indices=np.array([10]), values=np.array([1.0]))

    def embed(self, text):
        self.passage_called = True
        yield SimpleNamespace(indices=np.array([10]), values=np.array([1.5]))


def test_query_uses_query_embed():
    fake = FakeBM25()
    with patch.object(embeddings, "sparse_model", return_value=fake):
        result = embeddings.sparse_query_embed("продукты ЦФТ")
    assert fake.query_called is True
    assert fake.passage_called is False
    assert result == {"indices": [10], "values": [1.0]}


def test_document_uses_weighted_passage_embed():
    fake = FakeBM25()
    with patch.object(embeddings, "sparse_model", return_value=fake):
        result = embeddings.sparse_passage_embed("продукты ЦФТ ЦФТ")
    assert fake.passage_called is True
    assert fake.query_called is False
    assert result == {"indices": [10], "values": [1.5]}
