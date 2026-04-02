import logging
from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from typing import Any

from .config import CPEConfig
from .schemas import ExtractedCompanyData, CompanyProfile, CPEPlannerOutput
from .prompts import EXTRACTOR_SYSTEM_PROMPT, CPE_PLANNER_SYSTEM_PROMPT, CPE_PLANNER_USER_TEMPLATE
from app.core.config import AppSettings

logger = logging.getLogger(__name__)


def create_llm_model(model_id: str, settings: AppSettings) -> OpenAIModel:
    """
    Helper to instantiate an OpenAIModel via OpenRouter.
    """
    provider = OpenAIProvider(
        api_key=settings.get_openrouter_api_key(),
        base_url=str(settings.openrouter_api_base),
    )
    return OpenAIModel(model_id, provider=provider)


def create_extractor_agent(config: CPEConfig, settings: AppSettings) -> Agent[ExtractedCompanyData]:
    """Creates the ExtractedCompanyData extractor Agent."""
    model = create_llm_model(config.extractor_model_id, settings)
    return Agent[ExtractedCompanyData](
        model=model,
        system_prompt=EXTRACTOR_SYSTEM_PROMPT,
        output_type=ExtractedCompanyData,
        retries=3
    )


def create_planner_agent(config: CPEConfig, settings: AppSettings) -> Agent[CPEPlannerOutput]:
    """Creates the CPE Planner Agent instance."""
    model = create_llm_model(config.planner_model_id, settings)
    return Agent[CPEPlannerOutput](
        model=model,
        system_prompt=CPE_PLANNER_SYSTEM_PROMPT,
        output_type=CPEPlannerOutput,
        retries=2
    )


class AgencyAgents(BaseModel):
    extractor: Any
    planner: Any
    model_config = ConfigDict(arbitrary_types_allowed=True)


def get_cpe_agents(config: CPEConfig, settings: AppSettings) -> AgencyAgents:
    """Initializes and returns the agents for CPE."""
    extractor = create_extractor_agent(config=config, settings=settings)
    logger.info(f"Initialized extractor agent using model: {config.extractor_model_id}")
    
    planner = create_planner_agent(config=config, settings=settings)
    logger.info(f"Initialized planner agent using model: {config.planner_model_id}")

    return AgencyAgents(
        extractor=extractor,
        planner=planner
    ) 