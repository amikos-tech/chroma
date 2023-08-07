from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from chromadb.api.types import (
    CollectionMetadata,
    Include,
)


class AddEmbedding(BaseModel):  # type: ignore
    # Pydantic doesn't handle Union types cleanly like Embeddings which has
    # Union[int, float] so we use Any here to ensure data is parsed
    # to its original type.
    embeddings: Optional[List[Any]] = Field(None,
                                            description="The embeddings to add. If None, embeddings will be computed "
                                                        "based on the documents using the embedding_function set for "
                                                        "the Collection.",
                                            example=[[0.1, 0.2, 0.3], [-0.1, -0.2, -0.3]])
    metadatas: Optional[List[Optional[Dict[Any, Any]]]] = Field(None,
                                                                description="The metadata to associate with the "
                                                                            "embeddings. When querying, you can filter "
                                                                            "on this metadata",
                                                                example=[{"type": "page", "docId": 1, "pdf": False},
                                                                         {"type": "page",
                                                                          "docId": 2, "pdf": True}])
    documents: Optional[List[Optional[str]]] = Field(None, description="The documents to associate with the embeddings",
                                                     example=["This is a document", "This is another document"])
    ids: List[str] = Field(None, description="The ids of the embeddings you wish to add",
                           example=["ID1", "ID-2"])


class UpdateEmbedding(BaseModel):  # type: ignore
    embeddings: Optional[List[Any]] = Field(None, description="The embeddings to add. ",
                                            example=[[0.1, 0.2, 0.3], [-0.1, -0.2, -0.3]])
    metadatas: Optional[List[Optional[Dict[Any, Any]]]] = Field(None,
                                                                description="The metadata to associate with the "
                                                                            "embeddings.",
                                                                example=[{"type": "page", "docId": 1, "pdf": False},
                                                                         {"type": "page",
                                                                          "docId": 2, "pdf": True}])

    documents: Optional[List[Optional[str]]] = Field(None, description="The documents to associate with the embeddings",
                                                     example=["This is a document", "This is another document"])
    ids: List[str] = Field(..., description="The ids of the embeddings to update",
                           example=["ID1", "ID-2"])


class QueryEmbedding(BaseModel):  # type: ignore
    # TODO: Pydantic doesn't bode well with recursive types so we use generic Dicts
    # for Where and WhereDocument. This is not ideal, but it works for now since
    # there is a lot of downstream validation.
    where: Optional[Dict[Any, Any]] = Field(None, description="A Where type dict used to filter the deletion by",
                                            example={"color": "red", "price": 4.20})
    where_document: Optional[Dict[Any, Any]] = Field(None,
                                                     description="A WhereDocument type dict used to "
                                                                 "filter the deletion by the document content",
                                                     example={"$contains": {"text": "hello"}})
    query_embeddings: List[Any] = Field(..., description="The embeddings to get the closes neighbors of. "
                                                         "These should be the representations of the texts "
                                                         "you want to find the closest neighbors of.",
                                        example=[[0.1, 0.2, 0.3], [-0.1, -0.2, -0.3]])
    n_results: int = Field(10, description="The number of results to return. Note: If you plan to fetch lots "
                                           "of results you may need to adjust your hyper params:", example=5)
    include: Include = Field(["metadatas", "documents", "distances"],
                             description="A list of what to include in the results.",
                             example=["metadatas", "documents", "distances", "embeddings"])


class GetEmbedding(BaseModel):  # type: ignore
    ids: Optional[List[str]] = Field(None, description="The ids of the embeddings to delete",
                                     example=["ID1", "ID-2"])
    where: Optional[Dict[Any, Any]] = Field(None, description="A Where type dict used to filter the deletion by",
                                            example={"color": "red", "price": 4.20})
    where_document: Optional[Dict[Any, Any]] = Field(None,
                                                     description="A WhereDocument type dict used to "
                                                                 "filter the deletion by the document content",
                                                     example={"$contains": {"text": "hello"}})
    # TODO this is not in https://docs.trychroma.com/reference/Collection#get
    sort: Optional[str] = Field(None, description="The metadata field to sort by?", example="price")
    limit: Optional[int] = Field(None, description="The number of documents to return", example=10)
    offset: Optional[int] = Field(None, description="The offset to start returning results from. "
                                                    "Useful for paging results with limit",
                                  example=50)
    include: Include = Field(["metadatas", "documents", "distances"],
                             description="A list of what to include in the results.",
                             example=["metadatas", "documents", "distances", "embeddings"])


class DeleteEmbedding(BaseModel):  # type: ignore
    ids: Optional[List[str]] = Field(None, description="The ids of the embeddings to delete",
                                     example=["ID1", "ID-2"])
    where: Optional[Dict[Any, Any]] = Field(None, description="A Where type dict used to filter the deletion by",
                                            example={"color": "red", "price": 4.20})
    where_document: Optional[Dict[Any, Any]] = Field(None,
                                                     description="A WhereDocument type dict used to "
                                                                 "filter the deletion by the document content",
                                                     example={"$contains": {"text": "hello"}})


class CreateCollection(BaseModel):  # type: ignore
    name: str = Field(..., description="The name of the collection to create", example="my-collection")
    metadata: Optional[CollectionMetadata] = Field(None, description="The metadata to associate with the collection",
                                                   example={"type": "pdf-docs"})
    get_or_create: bool = Field(False, description="If True, will return the collection if it already exists")


class UpdateCollection(BaseModel):  # type: ignore
    new_name: Optional[str] = Field(None, description="The new name of the collection", example="new-collection-name")
    new_metadata: Optional[CollectionMetadata] = Field(None,
                                                       description="The new metadata to associate with the collection",
                                                       example={"type": "page", "docId": 1, "pdf": False})
