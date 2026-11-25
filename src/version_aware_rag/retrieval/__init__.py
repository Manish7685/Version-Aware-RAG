"""Retrieval (RAG-M5/M6): one interface, ``retrieve(query, version | None, k)``, for every method.

Only permitted chunks (MIT documentation pages and migration guides) are indexed. Release-post and
release-note chunks are restricted text: they are never indexed, retrieved, shown or sent to a
model.
"""
