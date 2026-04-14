"""Generic OpenAI-compatible provider"""
from typing import Optional, List, Any
from pydantic import BaseModel
from .base_provider import BaseLLMProvider
import base64

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class OpenAIProvider(BaseLLMProvider):
    """Generic OpenAI-compatible provider (OpenAI, Azure, custom endpoints)"""
    
    def __init__(
        self, 
        model: str = "gpt-4o", 
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider_name: str = "OpenAI"
    ):
        super().__init__(provider_name, model, api_key)
        if not OPENAI_AVAILABLE:
            raise ImportError("openai not installed. Install with: pip install openai")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
    
    def _build_content(
        self,
        text: Optional[str] = None,
        image_data_list: Optional[List[dict]] = None,
        pdf_bytes: Optional[bytes] = None,
        prompt: Optional[str] = None
    ) -> List[dict]:
        """Build content list dynamically based on inputs (all inline base64)
        
        Args:
            text: Text content (XML, JSON, plain text)
            image_data_list: List of dicts with 'image_bytes' and 'format'
            pdf_bytes: PDF file bytes
            prompt: User prompt/instruction
            
        Returns:
            List of content parts for OpenAI API
        """
        content = []
        
        # Add prompt first
        if prompt:
            content.append({"type": "text", "text": prompt})
        
        # Add text if provided
        if text:
            content.append({"type": "text", "text": text})
        
        # Add images inline (base64)
        if image_data_list:
            for img_data in image_data_list:
                base64_image = base64.b64encode(img_data['image_bytes']).decode('utf-8')
                mime_type = f"image/{img_data['format']}"
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_image}"
                    }
                })
        
        # Add PDF inline (base64)
        if pdf_bytes:
            base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
            content.append({
                "type": "input_file",
                "filename": "document.pdf",
                "file_data": f"data:application/pdf;base64,{base64_pdf}"
            })
        
        return content
    
    def _extract_tokens(self, response) -> dict:
        """Extract token usage from response"""
        usage = response.usage if hasattr(response, 'usage') else None
        return {
            'input': usage.prompt_tokens if usage else 0,
            'output': usage.completion_tokens if usage else 0,
            'total': usage.total_tokens if usage else 0
        }
    
    def extract_structured_data(
        self, 
        text: Optional[str] = None,
        schema: type[BaseModel] = None,
        system_prompt: Optional[str] = None,
        image_data_list: Optional[List[dict]] = None,
        pdf_bytes: Optional[bytes] = None
    ) -> tuple[BaseModel, dict]:
        """Extract structured data using OpenAI API with dynamic content construction
        
        Args:
            text: Text content (XML, JSON, plain text) - optional
            schema: Pydantic model for structured output
            system_prompt: Optional system prompt (uses schema docstring if None)
            image_data_list: Optional list of images (inline base64)
            pdf_bytes: Optional PDF bytes (inline base64)
            
        Returns:
            Tuple of (parsed_data, token_usage)
        """
        if system_prompt is None:
            system_prompt = schema.__doc__ or "Extract structured data from the provided content."
        
        # Build content dynamically based on inputs (without prompt — sent as system message)
        content = self._build_content(
            text=text,
            image_data_list=image_data_list,
            pdf_bytes=pdf_bytes
        )

        # Use structured outputs with optimized parameters
        response = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            response_format=schema,
            # Optimized parameters for structured/tabular extraction
            temperature=0.0,              # Deterministic output, no hallucination
            top_p=0.95,                   # Focus on high-probability tokens
            max_tokens=8192,              # High limit to avoid truncation
            frequency_penalty=0.0,        # No penalty - allow repetitive table structures
            presence_penalty=0.0,         # No penalty - allow similar content (months, values)
            extra_body={"keep_alive": 0}  # Force Ollama to unload model from VRAM immediately
        )
        
        return response.choices[0].message.parsed, self._extract_tokens(response)
    
    def extract_text(self, text: str, prompt: str) -> tuple[str, dict]:
        """Extract/filter text using OpenAI (for preselection)
        
        Args:
            text: Input text to process
            prompt: Instruction prompt
            
        Returns:
            Tuple of (filtered_text, token_usage)
        """
        content = self._build_content(text=text, prompt=prompt)
        
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": content}],
            extra_body={"keep_alive": 0}
        )
        
        return response.choices[0].message.content, self._extract_tokens(response)
    
    def supports_inline_files(self) -> bool:
        """OpenAI supports inline files (images, PDFs) via base64"""
        return True
