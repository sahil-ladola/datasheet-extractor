"""Datasheet Extractor: turn PDF component datasheets into queryable records.

Pipeline: DocumentLoader -> select_pages -> Extractor -> Validator -> Store.
The dashboard reads from Store only.
"""

__version__ = "0.1.0"
