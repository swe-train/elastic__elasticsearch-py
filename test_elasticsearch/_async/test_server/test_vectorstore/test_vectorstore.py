#  Licensed to Elasticsearch B.V. under one or more contributor
#  license agreements. See the NOTICE file distributed with
#  this work for additional information regarding copyright
#  ownership. Elasticsearch B.V. licenses this file to you under
#  the Apache License, Version 2.0 (the "License"); you may
#  not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
# 	http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing,
#  software distributed under the License is distributed on an
#  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#  KIND, either express or implied.  See the License for the
#  specific language governing permissions and limitations
#  under the License.

import logging
import re
from functools import partial
from typing import Any, List, Optional, Union

import pytest

from elasticsearch import AsyncElasticsearch, NotFoundError
from elasticsearch.helpers import BulkIndexError
from elasticsearch.helpers.vectorstore import (
    AsyncBM25Strategy,
    AsyncDenseVectorScriptScoreStrategy,
    AsyncDenseVectorStrategy,
    AsyncSparseVectorStrategy,
    AsyncVectorStore,
    DistanceMetric,
)
from elasticsearch.helpers.vectorstore._async._utils import model_is_deployed

from . import AsyncConsistentFakeEmbeddings, AsyncFakeEmbeddings

pytestmark = pytest.mark.asyncio

logging.basicConfig(level=logging.DEBUG)

"""
docker-compose up elasticsearch

By default runs against local docker instance of Elasticsearch.
To run against Elastic Cloud, set the following environment variables:
- ES_CLOUD_ID
- ES_API_KEY

Some of the tests require the following models to be deployed in the ML Node:
- elser (can be downloaded and deployed through Kibana and trained models UI)
- sentence-transformers__all-minilm-l6-v2 (can be deployed through the API,
  loaded via eland)

These tests that require the models to be deployed are skipped by default.
Enable them by adding the model name to the modelsDeployed list below.
"""

ELSER_MODEL_ID = ".elser_model_2"
TRANSFORMER_MODEL_ID = "sentence-transformers__all-minilm-l6-v2"


class TestVectorStore:
    async def test_search_without_metadata(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to end construction and search without metadata."""

        def assert_query(query_body: dict, query: Optional[str]) -> dict:
            assert query_body == {
                "knn": {
                    "field": "vector_field",
                    "filter": [],
                    "k": 1,
                    "num_candidates": 50,
                    "query_vector": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
                }
            }
            return query_body

        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )

        texts = ["foo", "bar", "baz"]
        await store.add_texts(texts)

        output = await store.search(query="foo", k=1, custom_query=assert_query)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]

    async def test_search_without_metadata_async(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to end construction and search without metadata."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )

        texts = ["foo", "bar", "baz"]
        await store.add_texts(texts)

        output = await store.search(query="foo", k=1)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]

    async def test_add_vectors(self, es_client: AsyncElasticsearch, index: str) -> None:
        """
        Test adding pre-built embeddings instead of using inference for the texts.
        This allows you to separate the embeddings text and the page_content
        for better proximity between user's question and embedded text.
        For example, your embedding text can be a question, whereas page_content
        is the answer.
        """
        embeddings = AsyncConsistentFakeEmbeddings()
        texts = ["foo1", "foo2", "foo3"]
        metadatas = [{"page": i} for i in range(len(texts))]

        embedding_vectors = await embeddings.embed_documents(texts)

        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(),
            embedding_service=embeddings,
            client=es_client,
        )

        await store.add_texts(
            texts=texts, vectors=embedding_vectors, metadatas=metadatas
        )
        output = await store.search(query="foo1", k=1)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo1"]
        assert [doc["_source"]["metadata"]["page"] for doc in output] == [0]

    async def test_search_with_metadata(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to end construction and search with metadata."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(),
            embedding_service=AsyncConsistentFakeEmbeddings(),
            client=es_client,
        )

        texts = ["foo", "bar", "baz"]
        metadatas = [{"page": i} for i in range(len(texts))]
        await store.add_texts(texts=texts, metadatas=metadatas)

        output = await store.search(query="foo", k=1)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]
        assert [doc["_source"]["metadata"]["page"] for doc in output] == [0]

        output = await store.search(query="bar", k=1)
        assert [doc["_source"]["text_field"] for doc in output] == ["bar"]
        assert [doc["_source"]["metadata"]["page"] for doc in output] == [1]

    async def test_search_with_filter(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to end construction and search with metadata."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )

        texts = ["foo", "foo", "foo"]
        metadatas = [{"page": i} for i in range(len(texts))]
        await store.add_texts(texts=texts, metadatas=metadatas)

        def assert_query(query_body: dict, query: Optional[str]) -> dict:
            assert query_body == {
                "knn": {
                    "field": "vector_field",
                    "filter": [{"term": {"metadata.page": "1"}}],
                    "k": 3,
                    "num_candidates": 50,
                    "query_vector": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
                }
            }
            return query_body

        output = await store.search(
            query="foo",
            k=3,
            filter=[{"term": {"metadata.page": "1"}}],
            custom_query=assert_query,
        )
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]
        assert [doc["_source"]["metadata"]["page"] for doc in output] == [1]

    async def test_search_script_score(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to end construction and search with metadata."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorScriptScoreStrategy(),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )

        texts = ["foo", "bar", "baz"]
        await store.add_texts(texts)

        expected_query = {
            "query": {
                "script_score": {
                    "query": {"match_all": {}},
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'vector_field') + 1.0",  # noqa: E501
                        "params": {
                            "query_vector": [
                                1.0,
                                1.0,
                                1.0,
                                1.0,
                                1.0,
                                1.0,
                                1.0,
                                1.0,
                                1.0,
                                0.0,
                            ]
                        },
                    },
                }
            }
        }

        def assert_query(query_body: dict, query: Optional[str]) -> dict:
            assert query_body == expected_query
            return query_body

        output = await store.search(query="foo", k=1, custom_query=assert_query)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]

    async def test_search_script_score_with_filter(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to end construction and search with metadata."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorScriptScoreStrategy(),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )

        texts = ["foo", "bar", "baz"]
        metadatas = [{"page": i} for i in range(len(texts))]
        await store.add_texts(texts=texts, metadatas=metadatas)

        def assert_query(query_body: dict, query: Optional[str]) -> dict:
            expected_query = {
                "query": {
                    "script_score": {
                        "query": {"bool": {"filter": [{"term": {"metadata.page": 0}}]}},
                        "script": {
                            "source": "cosineSimilarity(params.query_vector, 'vector_field') + 1.0",  # noqa: E501
                            "params": {
                                "query_vector": [
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    0.0,
                                ]
                            },
                        },
                    }
                }
            }
            assert query_body == expected_query
            return query_body

        output = await store.search(
            query="foo",
            k=1,
            custom_query=assert_query,
            filter=[{"term": {"metadata.page": 0}}],
        )
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]
        assert [doc["_source"]["metadata"]["page"] for doc in output] == [0]

    async def test_search_script_score_distance_dot_product(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to end construction and search with metadata."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorScriptScoreStrategy(
                distance=DistanceMetric.DOT_PRODUCT,
            ),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )

        texts = ["foo", "bar", "baz"]
        await store.add_texts(texts)

        def assert_query(query_body: dict, query: Optional[str]) -> dict:
            assert query_body == {
                "query": {
                    "script_score": {
                        "query": {"match_all": {}},
                        "script": {
                            "source": """
            double value = dotProduct(params.query_vector, 'vector_field');
            return sigmoid(1, Math.E, -value);
            """,
                            "params": {
                                "query_vector": [
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    1.0,
                                    0.0,
                                ]
                            },
                        },
                    }
                }
            }
            return query_body

        output = await store.search(query="foo", k=1, custom_query=assert_query)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]

    async def test_search_knn_with_hybrid_search(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to end construction and search with metadata."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(hybrid=True),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )

        texts = ["foo", "bar", "baz"]
        await store.add_texts(texts)

        def assert_query(query_body: dict, query: Optional[str]) -> dict:
            assert query_body == {
                "knn": {
                    "field": "vector_field",
                    "filter": [],
                    "k": 1,
                    "num_candidates": 50,
                    "query_vector": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
                },
                "query": {
                    "bool": {
                        "filter": [],
                        "must": [{"match": {"text_field": {"query": "foo"}}}],
                    }
                },
                "rank": {"rrf": {}},
            }
            return query_body

        output = await store.search(query="foo", k=1, custom_query=assert_query)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]

    async def test_search_knn_with_hybrid_search_rrf(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to end construction and rrf hybrid search with metadata."""
        texts = ["foo", "bar", "baz"]

        def assert_query(
            query_body: dict,
            query: Optional[str],
            expected_rrf: Union[dict, bool],
        ) -> dict:
            cmp_query_body = {
                "knn": {
                    "field": "vector_field",
                    "filter": [],
                    "k": 3,
                    "num_candidates": 50,
                    "query_vector": [
                        1.0,
                        1.0,
                        1.0,
                        1.0,
                        1.0,
                        1.0,
                        1.0,
                        1.0,
                        1.0,
                        0.0,
                    ],
                },
                "query": {
                    "bool": {
                        "filter": [],
                        "must": [{"match": {"text_field": {"query": "foo"}}}],
                    }
                },
            }

            if isinstance(expected_rrf, dict):
                cmp_query_body["rank"] = {"rrf": expected_rrf}
            elif isinstance(expected_rrf, bool) and expected_rrf is True:
                cmp_query_body["rank"] = {"rrf": {}}

            assert query_body == cmp_query_body

            return query_body

        # 1. check query_body is okay
        rrf_test_cases: List[Union[dict, bool]] = [
            True,
            False,
            {"rank_constant": 1, "window_size": 5},
        ]
        for rrf_test_case in rrf_test_cases:
            store = AsyncVectorStore(
                index=index,
                retrieval_strategy=AsyncDenseVectorStrategy(
                    hybrid=True, rrf=rrf_test_case
                ),
                embedding_service=AsyncFakeEmbeddings(),
                client=es_client,
            )
            await store.add_texts(texts)

            ## without fetch_k parameter
            output = await store.search(
                query="foo",
                k=3,
                custom_query=partial(assert_query, expected_rrf=rrf_test_case),
            )

        # 2. check query result is okay
        es_output = await store.client.search(
            index=index,
            query={
                "bool": {
                    "filter": [],
                    "must": [{"match": {"text_field": {"query": "foo"}}}],
                }
            },
            knn={
                "field": "vector_field",
                "filter": [],
                "k": 3,
                "num_candidates": 50,
                "query_vector": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
            },
            size=3,
            rank={"rrf": {"rank_constant": 1, "window_size": 5}},
        )

        assert [o["_source"]["text_field"] for o in output] == [
            e["_source"]["text_field"] for e in es_output["hits"]["hits"]
        ]

        # 3. check rrf default option is okay
        store = AsyncVectorStore(
            index=f"{index}_default",
            retrieval_strategy=AsyncDenseVectorStrategy(hybrid=True),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )
        await store.add_texts(texts)

        ## with fetch_k parameter
        output = await store.search(
            query="foo",
            k=3,
            num_candidates=50,
            custom_query=partial(assert_query, expected_rrf={}),
        )

    async def test_search_knn_with_custom_query_fn(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """test that custom query function is called
        with the query string and query body"""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )

        def my_custom_query(query_body: dict, query: Optional[str]) -> dict:
            assert query == "foo"
            assert query_body == {
                "knn": {
                    "field": "vector_field",
                    "filter": [],
                    "k": 1,
                    "num_candidates": 50,
                    "query_vector": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
                }
            }
            return {"query": {"match": {"text_field": {"query": "bar"}}}}

        """Test end to end construction and search with metadata."""
        texts = ["foo", "bar", "baz"]
        await store.add_texts(texts)

        output = await store.search(query="foo", k=1, custom_query=my_custom_query)
        assert [doc["_source"]["text_field"] for doc in output] == ["bar"]

    async def test_search_with_knn_infer_instack(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """test end to end with knn retrieval strategy and inference in-stack"""

        if not await model_is_deployed(es_client, TRANSFORMER_MODEL_ID):
            pytest.skip(
                f"{TRANSFORMER_MODEL_ID} model not deployed in ML Node skipping test"
            )

        text_field = "text_field"

        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(
                model_id="sentence-transformers__all-minilm-l6-v2"
            ),
            client=es_client,
        )

        # setting up the pipeline for inference
        await store.client.ingest.put_pipeline(
            id="test_pipeline",
            processors=[
                {
                    "inference": {
                        "model_id": TRANSFORMER_MODEL_ID,
                        "field_map": {"query_field": text_field},
                        "target_field": "vector_query_field",
                    }
                }
            ],
        )

        # creating a new index with the pipeline,
        # not relying on langchain to create the index
        await store.client.indices.create(
            index=index,
            mappings={
                "properties": {
                    text_field: {"type": "text_field"},
                    "vector_query_field": {
                        "properties": {
                            "predicted_value": {
                                "type": "dense_vector",
                                "dims": 384,
                                "index": True,
                                "similarity": "l2_norm",
                            }
                        }
                    },
                }
            },
            settings={"index": {"default_pipeline": "test_pipeline"}},
        )

        # adding documents to the index
        texts = ["foo", "bar", "baz"]

        for i, text in enumerate(texts):
            await store.client.create(
                index=index,
                id=str(i),
                document={text_field: text, "metadata": {}},
            )

        await store.client.indices.refresh(index=index)

        def assert_query(query_body: dict, query: Optional[str]) -> dict:
            assert query_body == {
                "knn": {
                    "filter": [],
                    "field": "vector_query_field.predicted_value",
                    "k": 1,
                    "num_candidates": 50,
                    "query_vector_builder": {
                        "text_embedding": {
                            "model_id": TRANSFORMER_MODEL_ID,
                            "model_text": "foo",
                        }
                    },
                }
            }
            return query_body

        output = await store.search(query="foo", k=1, custom_query=assert_query)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]

        output = await store.search(query="bar", k=1)
        assert [doc["_source"]["text_field"] for doc in output] == ["bar"]

    async def test_search_with_sparse_infer_instack(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """test end to end with sparse retrieval strategy and inference in-stack"""

        if not await model_is_deployed(es_client, ELSER_MODEL_ID):
            reason = f"{ELSER_MODEL_ID} model not deployed in ML Node, skipping test"

            pytest.skip(reason)

        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncSparseVectorStrategy(model_id=ELSER_MODEL_ID),
            client=es_client,
        )

        texts = ["foo", "bar", "baz"]
        await store.add_texts(texts)

        output = await store.search(query="foo", k=1)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]

    async def test_deployed_model_check_fails_semantic(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """test that exceptions are raised if a specified model is not deployed"""
        with pytest.raises(NotFoundError):
            store = AsyncVectorStore(
                index=index,
                retrieval_strategy=AsyncDenseVectorStrategy(
                    model_id="non-existing model ID"
                ),
                client=es_client,
            )
            await store.add_texts(["foo", "bar", "baz"])

    async def test_search_bm25(self, es_client: AsyncElasticsearch, index: str) -> None:
        """Test end to end using the BM25Strategy retrieval strategy."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncBM25Strategy(),
            client=es_client,
        )

        texts = ["foo", "bar", "baz"]
        await store.add_texts(texts)

        def assert_query(query_body: dict, query: Optional[str]) -> dict:
            assert query_body == {
                "query": {
                    "bool": {
                        "must": [{"match": {"text_field": {"query": "foo"}}}],
                        "filter": [],
                    }
                }
            }
            return query_body

        output = await store.search(query="foo", k=1, custom_query=assert_query)
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]

    async def test_search_bm25_with_filter(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test end to using the BM25Strategy retrieval strategy with metadata."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncBM25Strategy(),
            client=es_client,
        )

        texts = ["foo", "foo", "foo"]
        metadatas = [{"page": i} for i in range(len(texts))]
        await store.add_texts(texts=texts, metadatas=metadatas)

        def assert_query(query_body: dict, query: Optional[str]) -> dict:
            assert query_body == {
                "query": {
                    "bool": {
                        "must": [{"match": {"text_field": {"query": "foo"}}}],
                        "filter": [{"term": {"metadata.page": 1}}],
                    }
                }
            }
            return query_body

        output = await store.search(
            query="foo",
            k=3,
            custom_query=assert_query,
            filter=[{"term": {"metadata.page": 1}}],
        )
        assert [doc["_source"]["text_field"] for doc in output] == ["foo"]
        assert [doc["_source"]["metadata"]["page"] for doc in output] == [1]

    async def test_delete(self, es_client: AsyncElasticsearch, index: str) -> None:
        """Test delete methods from vector store."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(),
            embedding_service=AsyncFakeEmbeddings(),
            client=es_client,
        )

        texts = ["foo", "bar", "baz", "gni"]
        metadatas = [{"page": i} for i in range(len(texts))]
        ids = await store.add_texts(texts=texts, metadatas=metadatas)

        output = await store.search(query="foo", k=10)
        assert len(output) == 4

        await store.delete(ids=ids[1:3])
        output = await store.search(query="foo", k=10)
        assert len(output) == 2

        await store.delete(ids=["not-existing"])
        output = await store.search(query="foo", k=10)
        assert len(output) == 2

        await store.delete(ids=[ids[0]])
        output = await store.search(query="foo", k=10)
        assert len(output) == 1

        await store.delete(ids=[ids[3]])
        output = await store.search(query="gni", k=10)
        assert len(output) == 0

    async def test_indexing_exception_error(
        self,
        es_client: AsyncElasticsearch,
        index: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test bulk exception logging is giving better hints."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncBM25Strategy(),
            client=es_client,
        )

        await store.client.indices.create(
            index=index,
            mappings={"properties": {}},
            settings={"index": {"default_pipeline": "not-existing-pipeline"}},
        )

        texts = ["foo"]

        with pytest.raises(BulkIndexError):
            await store.add_texts(texts)

        error_reason = "pipeline with id [not-existing-pipeline] does not exist"
        log_message = f"First error reason: {error_reason}"

        assert log_message in caplog.text

    async def test_user_agent_default(
        self, es_client_request_saving: AsyncElasticsearch, index: str
    ) -> None:
        """Test to make sure the user-agent is set correctly."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncBM25Strategy(),
            client=es_client_request_saving,
        )
        expected_pattern = r"^elasticsearch-py-vs/\d+\.\d+\.\d+$"

        got_agent = store.client._headers["User-Agent"]
        assert (
            re.match(expected_pattern, got_agent) is not None
        ), f"The user agent '{got_agent}' does not match the expected pattern."

        texts = ["foo", "bob", "baz"]
        await store.add_texts(texts)

        for request in store.client.transport.requests:  # type: ignore
            agent = request["headers"]["User-Agent"]
            assert (
                re.match(expected_pattern, agent) is not None
            ), f"The user agent '{agent}' does not match the expected pattern."

    async def test_user_agent_custom(
        self, es_client_request_saving: AsyncElasticsearch, index: str
    ) -> None:
        """Test to make sure the user-agent is set correctly."""
        user_agent = "this is THE user_agent!"

        store = AsyncVectorStore(
            user_agent=user_agent,
            index=index,
            retrieval_strategy=AsyncBM25Strategy(),
            client=es_client_request_saving,
        )

        assert store.client._headers["User-Agent"] == user_agent

        texts = ["foo", "bob", "baz"]
        await store.add_texts(texts)

        for request in store.client.transport.requests:  # type: ignore
            assert request["headers"]["User-Agent"] == user_agent

    async def test_bulk_args(self, es_client_request_saving: Any, index: str) -> None:
        """Test to make sure the bulk arguments work as expected."""
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncBM25Strategy(),
            client=es_client_request_saving,
        )

        texts = ["foo", "bob", "baz"]
        await store.add_texts(texts, bulk_kwargs={"chunk_size": 1})

        # 1 for index exist, 1 for index create, 3 to index docs
        assert len(store.client.transport.requests) == 5  # type: ignore

    async def test_max_marginal_relevance_search(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test max marginal relevance search."""
        texts = ["foo", "bar", "baz"]
        vector_field = "vector_field"
        text_field = "text_field"
        embedding_service = AsyncConsistentFakeEmbeddings()
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorScriptScoreStrategy(),
            embedding_service=embedding_service,
            vector_field=vector_field,
            text_field=text_field,
            client=es_client,
        )
        await store.add_texts(texts)

        mmr_output = await store.max_marginal_relevance_search(
            embedding_service=embedding_service,
            query=texts[0],
            vector_field=vector_field,
            k=3,
            num_candidates=3,
        )
        sim_output = await store.search(query=texts[0], k=3)
        assert mmr_output == sim_output

        mmr_output = await store.max_marginal_relevance_search(
            embedding_service=embedding_service,
            query=texts[0],
            vector_field=vector_field,
            k=2,
            num_candidates=3,
        )
        assert len(mmr_output) == 2
        assert mmr_output[0]["_source"][text_field] == texts[0]
        assert mmr_output[1]["_source"][text_field] == texts[1]

        mmr_output = await store.max_marginal_relevance_search(
            embedding_service=embedding_service,
            query=texts[0],
            vector_field=vector_field,
            k=2,
            num_candidates=3,
            lambda_mult=0.1,  # more diversity
        )
        assert len(mmr_output) == 2
        assert mmr_output[0]["_source"][text_field] == texts[0]
        assert mmr_output[1]["_source"][text_field] == texts[2]

        # if fetch_k < k, then the output will be less than k
        mmr_output = await store.max_marginal_relevance_search(
            embedding_service=embedding_service,
            query=texts[0],
            vector_field=vector_field,
            k=3,
            num_candidates=2,
        )
        assert len(mmr_output) == 2

    async def test_metadata_mapping(
        self, es_client: AsyncElasticsearch, index: str
    ) -> None:
        """Test that the metadata mapping is applied."""
        test_mappings = {
            "my_field": {"type": "keyword"},
            "another_field": {"type": "text"},
        }
        store = AsyncVectorStore(
            index=index,
            retrieval_strategy=AsyncDenseVectorStrategy(distance=DistanceMetric.COSINE),
            embedding_service=AsyncFakeEmbeddings(),
            num_dimensions=10,
            client=es_client,
            metadata_mappings=test_mappings,
        )

        texts = ["foo", "foo", "foo"]
        metadatas = [{"my_field": str(i)} for i in range(len(texts))]
        await store.add_texts(texts=texts, metadatas=metadatas)

        mapping_response = await es_client.indices.get_mapping(index=index)
        mapping_properties = mapping_response[index]["mappings"]["properties"]
        assert mapping_properties["vector_field"] == {
            "type": "dense_vector",
            "dims": 10,
            "index": True,
            "similarity": "cosine",
        }

        assert "metadata" in mapping_properties
        for key, val in test_mappings.items():
            assert mapping_properties["metadata"]["properties"][key] == val