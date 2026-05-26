"""Generic OpenAI-compatible provider"""
from typing import Optional, List
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
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider_name: str = "OpenAI",
    ):
        super().__init__(provider_name, model, api_key)
        if not OPENAI_AVAILABLE:
            raise ImportError("openai not installed. Install with: pip install openai")
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def _build_content(self, image_data_list: List[dict]) -> List[dict]:
        content = []
        for img_data in image_data_list:
            base64_image = base64.b64encode(img_data['image_bytes']).decode('utf-8')
            mime_type = f"image/{img_data['format']}"
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}
            })
        return content

    def _extract_tokens(self, response) -> dict:
        usage = response.usage if hasattr(response, 'usage') else None
        return {
            'input': usage.prompt_tokens if usage else 0,
            'output': usage.completion_tokens if usage else 0,
            'total': usage.total_tokens if usage else 0
        }

    def extract_structured_data(
        self,
        schema: type[BaseModel],
        system_prompt: Optional[str] = None,
        image_data_list: Optional[List[dict]] = None,
    ) -> tuple[BaseModel, dict]:
        if system_prompt is None:
            system_prompt = schema.__doc__ or "Extract structured data from the provided content."

        content = self._build_content(image_data_list or [])

        response = self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            response_format=schema,
            temperature=0.0,
            top_p=0.95,
            max_tokens=8192,
            frequency_penalty=0.0,
            presence_penalty=0.0,
        )

        return response.choices[0].message.parsed, self._extract_tokens(response)
