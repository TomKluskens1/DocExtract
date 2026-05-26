"""Base class for LLM providers"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from pydantic import BaseModel


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers"""

    def __init__(self, name: str, model: str, api_key: Optional[str] = None):
        self.name = name
        self.model = model
        self.api_key = api_key

    @abstractmethod
    def extract_structured_data(
        self,
        schema: type[BaseModel],
        system_prompt: Optional[str] = None,
        image_data_list: Optional[List[dict]] = None,
    ) -> tuple[BaseModel, Dict[str, int]]:
        """Extract structured data using the LLM.

        Returns:
            Tuple of (extracted data instance, token usage dict with 'input', 'output', 'total' keys)
        """
        pass

    def __str__(self):
        return f"{self.name} ({self.model})"
