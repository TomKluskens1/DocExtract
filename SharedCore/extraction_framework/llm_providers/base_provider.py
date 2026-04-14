"""Base class for LLM providers"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
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
        text: str, 
        schema: type[BaseModel],
        system_prompt: Optional[str] = None
    ) -> tuple[BaseModel, Dict[str, int]]:
        """Extract structured data using the LLM
        
        Args:
            text: Input text to extract from
            schema: Pydantic model defining output structure
            system_prompt: Optional system prompt override
            
        Returns:
            Tuple of (extracted data instance, token usage dict with 'input', 'output', 'total' keys)
        """
        pass
    
    def extract_text(self, prompt: str) -> tuple[str, Dict[str, int]]:
        """Extract/filter text using the LLM (for preselection)
        
        Args:
            prompt: Prompt including the text to filter
            
        Returns:
            Tuple of (filtered text, token usage dict with 'input', 'output', 'total' keys)
        """
        # Default implementation - subclasses should override
        raise NotImplementedError(f"{self.name} does not support text extraction")
    
    @abstractmethod
    def supports_inline_files(self) -> bool:
        """Whether this provider supports inline file uploads
        
        Returns:
            True if files can be uploaded directly
        """
        pass
    
    def __str__(self):
        return f"{self.name} ({self.model})"
