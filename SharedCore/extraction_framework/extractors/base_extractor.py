"""Base class for PDF text extractors"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from pathlib import Path


class BaseExtractor(ABC):
    """Abstract base class for PDF extractors"""
    
    def __init__(self, name: str):
        self.name = name
    
    @abstractmethod
    def extract_text(self, pdf_path: Path) -> str:
        """Extract full text from PDF
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Extracted text as string
        """
        pass
    
    @abstractmethod
    def extract_pages(self, pdf_path: Path) -> List[str]:
        """Extract text page by page
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            List of text strings, one per page
        """
        pass
    

    
    def extract_metadata(self, pdf_path: Path) -> Dict:
        """Extract PDF metadata (optional)
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Dictionary with metadata
        """
        return {
            "extractor": self.name,
            "file": str(pdf_path)
        }
    
    def __str__(self):
        return self.name
