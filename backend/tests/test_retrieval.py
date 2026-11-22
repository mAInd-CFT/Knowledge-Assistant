from unittest.mock import patch

from app.retrieval import retriever


def test_search_returns_payload_fields():
    class Point:
        payload = {
            "chunk_text": "Текст",
            "title": "Документ",
            "source_uri": "https://www.cft.ru",
            "section": "Раздел",
            "is_active": True,
        }
        score = 0.5

    class Result:
        points = [Point()]

    class Client:
        def get_collection(self, name):
            return object()

        def query_points(self, **kwargs):
            assert kwargs["limit"] == 4
            assert kwargs["with_payload"] is True
            return Result()

    with patch.object(
        retriever, "dense_embed", return_value=[[0.1, 0.2]]
    ), patch.object(
        retriever,
        "sparse_query_embed",
        return_value={"indices": [1], "values": [0.2]},
    ), patch.object(
        retriever, "get_qdrant", return_value=Client()
    ):
        result = retriever.search("вопрос", 4)

    assert len(result) == 1
    assert result[0]["text"] == "Текст"
    assert result[0]["title"] == "Документ"
    assert result[0]["source_url"] == "https://www.cft.ru"
    assert result[0]["section"] == "Раздел"
    assert result[0]["score"] == 0.5