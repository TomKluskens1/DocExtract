"""Dynamic model loader for flexible schema testing"""
import sys
import importlib.util
from pathlib import Path
from typing import Type, List, Dict, Any, Optional, Tuple, Set
from pydantic import BaseModel


class ModelLoader:
    """Load Pydantic models dynamically from Python files"""
    
    def __init__(self, test_dir: Path):
        """
        Args:
            test_dir: Directory containing test files and model definitions
        """
        self.test_dir = Path(test_dir)
    
    def load_model_from_file(self, model_file: Path, model_class_name: str = None) -> Type[BaseModel]:
        """Load a Pydantic model from a Python file
        
        Args:
            model_file: Path to Python file containing the model
            model_class_name: Name of the model class (if None, tries to find it)
            
        Returns:
            The model class
            
        Raises:
            ValueError: If model not found or invalid
        """
        if not model_file.exists():
            raise ValueError(f"Model file not found: {model_file}")
        
        # Load module dynamically
        spec = importlib.util.spec_from_file_location("dynamic_model", model_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules["dynamic_model"] = module
        spec.loader.exec_module(module)
        
        # Find the model class
        if model_class_name:
            if not hasattr(module, model_class_name):
                raise ValueError(f"Model class '{model_class_name}' not found in {model_file}")
            model_class = getattr(module, model_class_name)
        else:
            # Try to find a BaseModel subclass
            # Priority: classes starting with "Dati", then any BaseModel
            model_class = None
            fallback_class = None
            
            for name in dir(module):
                obj = getattr(module, name)
                if (isinstance(obj, type) and 
                    issubclass(obj, BaseModel) and 
                    obj != BaseModel and
                    not name.startswith('_')):
                    
                    # Prioritize classes starting with "Dati"
                    if name.startswith('Dati'):
                        model_class = obj
                        break
                    
                    # Store first BaseModel as fallback
                    if fallback_class is None:
                        fallback_class = obj
            
            # Use fallback if no "Dati*" class found
            if model_class is None:
                model_class = fallback_class
            
            if model_class is None:
                raise ValueError(f"No Pydantic BaseModel found in {model_file}")
        
        if not issubclass(model_class, BaseModel):
            raise ValueError(f"Class '{model_class.__name__}' is not a Pydantic BaseModel")
        
        return model_class
    
    def discover_models(self) -> List[Dict[str, Any]]:
        """Discover all model files in test directory
        
        Returns:
            List of dicts with model information
        """
        models = []
        
        for model_file in self.test_dir.glob("**/modello*.py"):
            # Skip __pycache__ and similar
            if any(part.startswith('.') or part.startswith('__') for part in model_file.parts):
                continue
            
            try:
                # Try to load and introspect
                model_class = self.load_model_from_file(model_file)
                
                models.append({
                    "file": str(model_file),
                    "relative_path": str(model_file.relative_to(self.test_dir)),
                    "class_name": model_class.__name__,
                    "description": model_class.__doc__ or "",
                    "test_folder": model_file.parent.name
                })
            except Exception as e:
                print(f"Warning: Could not load model from {model_file}: {e}")
        
        return models
    
    def get_model_for_test(self, test_name: str, model_file_name: str = "modello.py") -> Type[BaseModel]:
        """Get model for a specific test
        
        Args:
            test_name: Name of the test folder (e.g., 'bolletta_ee_cenpi')
            model_file_name: Name of the model file (default: 'modello.py')
            
        Returns:
            The model class
        """
        model_path = self.test_dir / test_name / model_file_name
        return self.load_model_from_file(model_path)
    
    def get_module_for_test(self, test_name: str, model_file_name: str = "modello.py"):
        """Get the module for a specific test (for accessing PAGE_VALIDATION_RULES, etc.)
        
        Args:
            test_name: Name of the test folder (e.g., 'bolletta_ee_cenpi')
            model_file_name: Name of the model file (default: 'modello.py')
            
        Returns:
            The loaded module
        """
        model_path = self.test_dir / test_name / model_file_name
        
        # Import the module
        spec = importlib.util.spec_from_file_location(f"{test_name}.{model_path.stem}", model_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        return module
    
    def list_test_folders(self) -> List[Dict[str, Any]]:
        """List all test folders with their models
        
        Returns:
            List of test folder information
        """
        test_folders = []
        
        for folder in self.test_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith('.'):
                continue
            
            # Look for model files
            model_files = list(folder.glob("modello*.py"))
            if not model_files:
                continue
            
            # Look for PDF files
            pdf_files = list(folder.glob("*.pdf"))
            
            # Look for validated JSON files
            json_files = list(folder.glob("*.json"))
            json_files = [j for j in json_files if not j.name.startswith('modello')]
            
            test_folders.append({
                "name": folder.name,
                "path": str(folder),
                "model_files": [m.name for m in model_files],
                "pdf_count": len(pdf_files),
                "validated_count": len(json_files)
            })
        
        return test_folders
    
    def load_scoring_rules(self, test_name: str) -> Tuple[List[str], Set[str]]:
        """Load scoring rules from regole.py in test folder
        
        Args:
            test_name: Name of the test folder
            
        Returns:
            Tuple of (unique_identifiers: List[str], ignored_fields: Set[str])
            Returns defaults if regole.py doesn't exist
        """
        rules_path = self.test_dir / test_name / "regole.py"
        
        # Default values if no rules file exists
        default_unique_ids = ['codice', 'giorno_inizio', 'giorno_fine']
        default_ignored = {'timestamp', 'indirizzo'}
        
        if not rules_path.exists():
            print(f"[ModelLoader] No regole.py found in {test_name}, using defaults")
            return default_unique_ids, default_ignored
        
        try:
            # Load the rules module
            spec = importlib.util.spec_from_file_location(f"{test_name}.regole", rules_path)
            if spec is None or spec.loader is None:
                raise ValueError(f"Cannot load regole.py from {rules_path}")
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Extract rules
            unique_identifiers = getattr(module, 'UNIQUE_IDENTIFIERS', default_unique_ids)
            ignored_fields = getattr(module, 'IGNORED_FIELDS', default_ignored)
            
            print(f"[ModelLoader] Loaded scoring rules from {test_name}/regole.py")
            print(f"  - Unique identifiers: {unique_identifiers}")
            print(f"  - Ignored fields: {ignored_fields}")
            
            return unique_identifiers, ignored_fields
            
        except Exception as e:
            print(f"[ModelLoader] Error loading regole.py from {test_name}: {e}")
            print(f"[ModelLoader] Using default rules")
            return default_unique_ids, default_ignored
