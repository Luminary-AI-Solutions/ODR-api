from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, ValidationError, AliasChoices
from typing import Dict, Any, Literal, Optional

class ChunkSettings(BaseSettings):
    chunk_size: int = 2048
    chunk_overlap: int = 100
    min_chunk_size: int = 256

class DeepResearchConfig(BaseSettings):
    """Configuration specific to the Deep Research Agency."""
    
    # Reranking provider and role-specific models stay agency-local.
    rerank_provider: Literal["cohere", "together"] = Field(
        default="cohere",
        description="Which rerank API to use (see DEEP_RESEARCH_RERANK_PROVIDER).",
    )

    # --- LLM Model IDs --- 
    # Loadable from env vars like DEEP_RESEARCH_PLANNER_MODEL_ID
    planner_model_id: str = Field(default="openai/gpt-4.1-mini")
    summarizer_model_id: str = Field(default="google/gemini-2.0-flash-001")
    writer_model_id: str = Field(default="openai/gpt-4.1")
    refiner_model_id: str = Field(default="google/gemini-2.0-flash-001")
    
    # --- Reranker --- 
    # Cohere: e.g. rerank-english-v3.0; Together: e.g. Salesforce/Llama-Rank-v1 (if available)
    reranker_model: str = Field(
        default="rerank-english-v3.0",
        validation_alias=AliasChoices("DEEP_RESEARCH_RERANKER_MODEL", "RERANKER_MODEL"),
    )
    
    # Relevance threshold for initial search result reranking
    rerank_relevance_threshold: float = Field(default=0.25, description="Threshold for initial search result reranking.")

    # Relevance threshold specifically for chunk reranking
    chunk_rerank_relevance_threshold: float = Field(default=0.5, description="Threshold for reranking content chunks.")
    
    # Orchestration parameters with defaults, loadable from env vars
    max_refinement_loops: int = Field(default=2)
    max_total_chunks: int = Field(default=1000) # Ensure this is uncommented
    top_n_chunks_per_source: int = Field(default=5, description="Max chunks to keep per source after reranking.") # Configurable top N per source
    
    # Chunking settings (as a nested model or dict)
    # Option 1: Nested Model (cleaner)
    default_chunk_settings: ChunkSettings = Field(default_factory=ChunkSettings)

    # Model configuration for BaseSettings (DEEP_RESEARCH_* from os.environ; see app.core.config _load_repo_env_files)
    model_config = SettingsConfigDict(
        env_prefix="DEEP_RESEARCH_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

# Example usage (for testing if run directly)
if __name__ == "__main__":
    try:
        config = DeepResearchConfig()
        print("Deep Research Config loaded successfully:")
        print(f"  Planner Model: {config.planner_model_id}")
        print(f"  Summarizer Model: {config.summarizer_model_id}")
        print(f"  Writer Model: {config.writer_model_id}")
        print(f"  Refiner Model: {config.refiner_model_id}")
        print(f"  Rerank provider: {config.rerank_provider}")
        print(f"  Reranker Model: {config.reranker_model}")
        print(f"  Max Loops: {config.max_refinement_loops}")
        print(f"  Threshold: {config.rerank_relevance_threshold}")
        print(f"  Chunk Threshold: {config.chunk_rerank_relevance_threshold}")
        print(f"  Top N Chunks per Source: {config.top_n_chunks_per_source}")
        print(f"  Chunk Settings: {config.default_chunk_settings}")
    except ValidationError as e:
        print(f"Error loading DeepResearchConfig: {e}")
