"""Page validation and filtering system using regex patterns"""
from pathlib import Path
from typing import List, Dict, Any, Tuple
import re


class PageValidator:
    """Validates pages using regex patterns and filters unnecessary pages"""
    
    def __init__(self, validation_rules: List[Dict[str, Any]]):
        """
        Initialize validator with regex rules.
        
        Args:
            validation_rules: List of dicts with 'patterns' (list of regex strings).
                             All patterns in a dict must match for validation to pass.
                             Only one dict needs to pass per page.
        
        Example:
            [
                {"patterns": ["BOLLETTA", "DATA.*EMISSIONE"]},  # Both must match
                {"patterns": ["FATTURA", "CODICE.*CLIENTE"]}    # Or both these
            ]
        """
        self.validation_rules = []
        
        for rule in validation_rules:
            compiled_patterns = []
            for pattern in rule.get('patterns', []):
                try:
                    compiled_patterns.append(re.compile(pattern, re.IGNORECASE | re.DOTALL))
                except re.error as e:
                    print(f"[PageValidator] ⚠️  Invalid regex pattern '{pattern}': {e}")
            
            if compiled_patterns:
                self.validation_rules.append({
                    'patterns': compiled_patterns,
                    'description': rule.get('description', 'Unnamed rule')
                })
    
    def validate_page(self, page_text: str) -> bool:
        """
        Check if page passes at least one validation rule.
        
        Args:
            page_text: Text content of the page
            
        Returns:
            True if page passes at least one rule, False otherwise
        """
        if not self.validation_rules:
            # No rules = all pages valid
            return True
        
        for rule in self.validation_rules:
            # All patterns in this rule must match
            all_match = all(
                pattern.search(page_text) is not None
                for pattern in rule['patterns']
            )
            
            if all_match:
                return True
        
        return False
    
    def filter_pages(
        self, 
        pages: List[str], 
        verbose: bool = True
    ) -> Tuple[List[str], Dict[str, Any]]:
        """
        Filter pages by removing consecutive invalid pages from head and tail.
        Keeps intermediate invalid pages for context.
        
        Args:
            pages: List of page contents
            verbose: Print filtering statistics
            
        Returns:
            Tuple of (filtered_pages, statistics)
        """
        if not pages:
            return pages, self._empty_stats()
        
        # Validate each page
        validations = [self.validate_page(page) for page in pages]
        
        # Find first and last valid page
        try:
            first_valid = validations.index(True)
        except ValueError:
            # No valid pages found - keep all pages as fallback
            if verbose:
                print(f"[PageValidator] ⚠️  No pages passed validation - keeping all pages")
            return pages, self._empty_stats()
        
        try:
            last_valid = len(validations) - 1 - validations[::-1].index(True)
        except ValueError:
            last_valid = first_valid
        
        # Keep pages from first valid to last valid (inclusive)
        filtered_pages = pages[first_valid:last_valid + 1]
        
        # Calculate statistics
        stats = {
            'total_pages': len(pages),
            'validated_pages': sum(validations),
            'filtered_pages': len(filtered_pages),
            'removed_from_head': first_valid,
            'removed_from_tail': len(pages) - last_valid - 1,
            'total_removed': len(pages) - len(filtered_pages),
            'kept_invalid_intermediate': len(filtered_pages) - sum(validations[first_valid:last_valid + 1]),
            'original_text_length': sum(len(p) for p in pages),
            'filtered_text_length': sum(len(p) for p in filtered_pages)
        }
        
        stats['text_reduction_percent'] = (
            (1 - stats['filtered_text_length'] / stats['original_text_length']) * 100
            if stats['original_text_length'] > 0 else 0
        )
        
        if verbose:
            self._print_stats(stats)
        
        return filtered_pages, stats
    
    def filter_page_indices(
        self,
        pages: List[str],
        verbose: bool = True
    ) -> Tuple[List[int], Dict[str, Any]]:
        """
        Filter pages and return 0-based indices of kept pages.
        Uses same head/tail trimming logic as filter_pages().

        Args:
            pages: List of page text contents
            verbose: Print filtering statistics

        Returns:
            Tuple of (list of 0-based page indices, statistics)
        """
        if not pages:
            return list(range(len(pages))), self._empty_stats()

        validations = [self.validate_page(page) for page in pages]

        try:
            first_valid = validations.index(True)
        except ValueError:
            if verbose:
                print(f"[PageValidator] No pages passed validation - keeping all pages")
            return list(range(len(pages))), self._empty_stats()

        try:
            last_valid = len(validations) - 1 - validations[::-1].index(True)
        except ValueError:
            last_valid = first_valid

        filtered_indices = list(range(first_valid, last_valid + 1))

        stats = {
            'total_pages': len(pages),
            'validated_pages': sum(validations),
            'filtered_pages': len(filtered_indices),
            'removed_from_head': first_valid,
            'removed_from_tail': len(pages) - last_valid - 1,
            'total_removed': len(pages) - len(filtered_indices),
            'page_indices': filtered_indices,
        }

        if verbose:
            print(f"\n[PageValidator] Page Filtering: {stats['total_pages']} total, "
                  f"{stats['filtered_pages']} kept (pages {[i+1 for i in filtered_indices]}), "
                  f"{stats['total_removed']} removed")

        return filtered_indices, stats

    def _empty_stats(self) -> Dict[str, Any]:
        """Return empty statistics"""
        return {
            'total_pages': 0,
            'validated_pages': 0,
            'filtered_pages': 0,
            'removed_from_head': 0,
            'removed_from_tail': 0,
            'total_removed': 0,
            'kept_invalid_intermediate': 0,
            'original_text_length': 0,
            'filtered_text_length': 0,
            'text_reduction_percent': 0
        }
    
    def _print_stats(self, stats: Dict[str, Any]):
        """Print filtering statistics"""
        print(f"\n[PageValidator] 📄 Page Filtering Results:")
        print(f"  Total pages: {stats['total_pages']}")
        print(f"  Pages with validation: {stats['validated_pages']}")
        print(f"  Filtered pages (kept): {stats['filtered_pages']}")
        print(f"  Removed from head: {stats['removed_from_head']}")
        print(f"  Removed from tail: {stats['removed_from_tail']}")
        print(f"  Total removed: {stats['total_removed']}")
        
        if stats['kept_invalid_intermediate'] > 0:
            print(f"  Invalid pages kept (intermediate): {stats['kept_invalid_intermediate']}")
        
        print(f"  Text length before: {stats['original_text_length']:,} chars")
        print(f"  Text length after: {stats['filtered_text_length']:,} chars")
        print(f"  Text reduction: {stats['text_reduction_percent']:.1f}%")


def load_validation_rules_from_model(model_module) -> List[Dict[str, Any]]:
    """
    Load validation rules from a model module or class.
    
    Looks for PAGE_VALIDATION_RULES in:
    1. Model class as ClassVar (new approach)
    2. Module level variable (legacy approach)
    
    Args:
        model_module: Python module or class containing PAGE_VALIDATION_RULES
        
    Returns:
        List of validation rules or empty list if not found
    """
    # Try to get from module-level variable (legacy)
    rules = getattr(model_module, 'PAGE_VALIDATION_RULES', None)
    
    # If not found at module level, try to find in classes
    if not rules:
        # Look for a Pydantic model class with PAGE_VALIDATION_RULES
        for attr_name in dir(model_module):
            attr = getattr(model_module, attr_name)
            if isinstance(attr, type) and hasattr(attr, 'PAGE_VALIDATION_RULES'):
                rules = getattr(attr, 'PAGE_VALIDATION_RULES', None)
                if rules:
                    print(f"[PageValidator] Found PAGE_VALIDATION_RULES in class {attr_name}")
                    break
    
    if not rules:
        print(f"[PageValidator] No PAGE_VALIDATION_RULES found in model - no filtering will be applied")
    else:
        print(f"[PageValidator] Loaded {len(rules)} validation rule(s) from model")
    
    return rules or []
