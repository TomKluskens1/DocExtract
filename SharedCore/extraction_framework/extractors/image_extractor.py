"""Image-based extractor - converts PDF pages to images"""
from pathlib import Path
from typing import List, Optional
from .base_extractor import BaseExtractor
import base64
import io

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False


class ImageExtractor(BaseExtractor):
    """Convert PDF pages to images for inline processing"""
    
    def __init__(self, dpi: int = 150):
        super().__init__("PDF-Images")
        if not PYMUPDF_AVAILABLE:
            raise ImportError("PyMuPDF not installed. Install with: pip install pymupdf")
        self.dpi = dpi
        self.zoom = dpi / 72  # PyMuPDF uses 72 DPI as base
    
    def extract_text(self, pdf_path: Path) -> str:
        """Extract all pages as base64-encoded images in a structured format"""
        doc = fitz.open(pdf_path)
        
        result_parts = [f"DOCUMENT: {pdf_path.name}"]
        result_parts.append(f"TOTAL_PAGES: {len(doc)}")
        result_parts.append("")
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Convert page to image
            mat = fitz.Matrix(self.zoom, self.zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to PNG bytes
            img_bytes = pix.tobytes("png")
            
            # Encode as base64
            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            
            result_parts.append(f"=== PAGE {page_num + 1} ===")
            result_parts.append(f"IMAGE_BASE64: {img_base64}")
            result_parts.append("")
        
        doc.close()
        return "\n".join(result_parts)
    
    def extract_pages(self, pdf_path: Path) -> List[str]:
        """Extract pages as individual base64-encoded images"""
        doc = fitz.open(pdf_path)
        pages = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Convert page to image
            mat = fitz.Matrix(self.zoom, self.zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to PNG bytes
            img_bytes = pix.tobytes("png")
            
            # Encode as base64
            img_base64 = base64.b64encode(img_bytes).decode('utf-8')
            
            page_data = f"PAGE {page_num + 1}\nIMAGE_BASE64: {img_base64}"
            pages.append(page_data)
        
        doc.close()
        return pages
    
    def get_page_images_for_llm(self, pdf_path: Path) -> List[dict]:
        """
        Extract pages as image data suitable for LLM vision APIs.
        Returns list of dicts with page number and image data.
        """
        doc = fitz.open(pdf_path)
        page_images = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            
            # Convert page to image
            mat = fitz.Matrix(self.zoom, self.zoom)
            pix = page.get_pixmap(matrix=mat)
            
            # Convert to PNG bytes
            img_bytes = pix.tobytes("png")
            
            page_images.append({
                "page_number": page_num + 1,
                "image_bytes": img_bytes,
                "format": "png",
                "width": pix.width,
                "height": pix.height
            })
        
        doc.close()
        return page_images
    
    def get_page_texts(self, pdf_path: Path) -> List[str]:
        """Extract text content per page (for page filtering before LLM)."""
        doc = fitz.open(pdf_path)
        texts = [page.get_text() for page in doc]
        doc.close()
        return texts

    def get_filtered_page_images_for_llm(self, pdf_path: Path, page_indices: List[int]) -> List[dict]:
        """
        Extract only specified pages as image data for LLM vision APIs.

        Args:
            pdf_path: Path to the PDF file
            page_indices: List of 0-based page indices to render
        """
        doc = fitz.open(pdf_path)
        page_images = []

        for page_num in page_indices:
            if page_num < 0 or page_num >= len(doc):
                continue
            page = doc[page_num]
            mat = fitz.Matrix(self.zoom, self.zoom)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")

            page_images.append({
                "page_number": page_num + 1,
                "image_bytes": img_bytes,
                "format": "png",
                "width": pix.width,
                "height": pix.height
            })

        doc.close()
        return page_images

    def get_metadata(self):
        """Return extractor metadata"""
        return {
            "name": self.name,
            "format": "Images",
            "dpi": self.dpi,
            "library": "pymupdf",
            "features": ["vision_api_ready", "high_quality", "inline_processing"]
        }
