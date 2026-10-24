from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import logging
import uuid
import os
from contextlib import asynccontextmanager # Import asynccontextmanager

# Import custom exceptions (Keep all as they are used by handlers)
from .core.exceptions import (
    DeepResearchError, ConfigurationError, ValidationError, LLMError,
    ExternalServiceError, SearchAPIError, RankingAPIError, ScrapingError,
    AgentExecutionError, LLMCommunicationError, LLMRateLimitError, LLMOutputValidationError
)
from fastapi.middleware.cors import CORSMiddleware

# --- Import Agency Routers ---
from app.agencies.deep_research.routes import router as deep_research_router
from app.agencies.cpe.routes import router as cpe_router # Import CPE router

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Lifespan Context Manager ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Actions to perform on startup
    logger.info("Application startup: Initializing services...")
    if os.getenv("OPENROUTER_DEBUG", "").strip().lower() in ("1", "true", "yes"):
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)
        logger.info("OPENROUTER_DEBUG enabled: httpx/httpcore logging set to DEBUG.")
    try:
        from .core.dependencies import get_settings

        _s = get_settings()
        _has_or = bool(_s.openrouter_api_key and _s.openrouter_api_key.get_secret_value())
        logger.info(
            "OpenRouter: base_url=%s api_key_loaded=%s",
            _s.openrouter_api_base,
            _has_or,
        )
        if not _has_or:
            logger.warning(
                "OPENROUTER_API_KEY is not set after loading env files. "
                "LLM calls will fail until it is set (ODR-api.env or environment)."
            )
    except Exception as ex:
        logger.warning("Could not log OpenRouter startup diagnostics: %s", ex)
    logger.info("Application startup: Service initialization complete.")
    yield
    # Actions to perform on shutdown (optional)
    logger.info("Application shutdown.")


# Create FastAPI app instance with lifespan manager
app = FastAPI(
    title="Deep Research API",
    description="API service providing access to various research agencies.",
    version="0.1.0",
    lifespan=lifespan # Add the lifespan manager here
)

# --- Import Core Dependencies ---
from .core.dependencies import get_settings

# --- Global Exception Handlers (Apply to all routes) --- #

@app.exception_handler(ConfigurationError)
async def configuration_error_handler(request: Request, exc: ConfigurationError):
    error_id = uuid.uuid4()
    error_type = type(exc).__name__
    logger.error(f"Configuration Error (ID: {error_id}): {error_type} - {exc}", exc_info=False)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Server configuration error. Please contact administrator. Error ID: {error_id}"},
    )

@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    error_id = uuid.uuid4()
    error_type = type(exc).__name__
    logger.warning(f"Validation Error (ID: {error_id}): {exc}", exc_info=False)
    return JSONResponse(
        status_code=422,
        content={"detail": f"Validation Error: {str(exc)}. Error ID: {error_id}"},
    )

@app.exception_handler(LLMRateLimitError)
async def llm_rate_limit_error_handler(request: Request, exc: LLMRateLimitError):
    error_id = uuid.uuid4()
    error_type = type(exc).__name__
    logger.warning(f"LLM Rate Limit Error (ID: {error_id}): {error_type}", exc_info=False)
    return JSONResponse(
        status_code=429,
        content={"detail": f"LLM service rate limit exceeded. Please try again later. Error ID: {error_id}"},
    )

@app.exception_handler(LLMCommunicationError)
async def llm_communication_error_handler(request: Request, exc: LLMCommunicationError):
    error_id = uuid.uuid4()
    error_type = type(exc).__name__
    logger.error(f"LLM Communication Error (ID: {error_id}): {error_type}", exc_info=False)
    return JSONResponse(
        status_code=503,
        content={"detail": f"Error communicating with LLM service. Please try again later. Error ID: {error_id}"},
    )

@app.exception_handler(ExternalServiceError)
async def external_service_error_handler(request: Request, exc: ExternalServiceError):
    error_id = uuid.uuid4()
    error_type = type(exc).__name__
    logger.error(f"External Service Error (ID: {error_id}) - Type: {error_type}: {exc}", exc_info=False)
    return JSONResponse(
        status_code=503,
        content={"detail": f"Error communicating with an external service ({error_type}). Please try again later. Error ID: {error_id}"},
    )

@app.exception_handler(AgentExecutionError)
async def agent_error_handler(request: Request, exc: AgentExecutionError):
    error_id = uuid.uuid4()
    error_type = type(exc).__name__
    logger.error(f"Agent Execution Error (ID: {error_id}): {error_type} - {exc}", exc_info=False)
    return JSONResponse(
        status_code=500,
        content={"detail": f"An error occurred during the agent execution process. Error ID: {error_id}"},
    )

@app.exception_handler(DeepResearchError)
async def deep_research_error_handler(request: Request, exc: DeepResearchError):
    error_id = uuid.uuid4()
    error_type = type(exc).__name__
    logger.error(f"Deep Research Error (ID: {error_id}) - Type: {error_type}", exc_info=False)
    return JSONResponse(
        status_code=500,
        content={"detail": f"An error occurred during deep research. Error ID: {error_id}"},
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    error_id = uuid.uuid4()
    error_type = type(exc).__name__
    logger.critical(f"Unhandled Exception (ID: {error_id}): {error_type}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": f"An internal server error occurred. Error ID: {error_id}"},
    )

# --- Root Endpoint --- #
@app.get("/")
async def root():
    _ = get_settings()  # Fails fast if required settings are missing
    return {"message": "ODR Multi-Agency API is running."}

# --- Include Agency Routers ---
# Add routers from different agencies here
app.include_router(
    deep_research_router, 
    prefix="/deep_research",
    tags=["Deep Research Agency"]
)
app.include_router(
    cpe_router, 
    prefix="/cpe",
    tags=["Company Profile Extractor"]
)

# --- CORS Middleware --- #
# Comma-separated list, e.g. CORS_ALLOWED_ORIGINS="https://app.example.com,http://localhost:3000".
# Defaults to "*" for local development.
allowed_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
) 
