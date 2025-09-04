"""
Predefined API interfaces for common use cases.

This module provides ready-to-use interfaces for popular APIs:
- CrossRef API for academic literature
- OpenAI API for language models  
- PDF processing for document extraction

Each interface handles optional dependencies gracefully using try-except ImportError.
"""

# Import interfaces with graceful error handling
_available_apis = []

# CrossRef API interface
try:
    from .crossref_api import CrossRefExecutor, crossref_batch_query
    _available_apis.append('crossref')
    __all__ = getattr(__all__ if '__all__' in locals() else [], []) + [
        'CrossRefExecutor', 'crossref_batch_query'
    ]
except ImportError as e:
    CrossRefExecutor = None
    crossref_batch_query = None
    _crossref_import_error = str(e)

# OpenAI API interface  
try:
    from .openai_api import OpenAIExecutor, openai_batch_query, openai_price_calculator
    _available_apis.append('openai')
    __all__ = getattr(__all__ if '__all__' in locals() else [], []) + [
        'OpenAIExecutor', 'openai_batch_query', 'openai_price_calculator'
    ]
except ImportError as e:
    OpenAIExecutor = None
    openai_batch_query = None
    openai_price_calculator = None
    _openai_import_error = str(e)

# PDF processing interface
try:
    from .pdf_api import PDFExecutor, pdf_batch_extract
    _available_apis.append('pdf')
    __all__ = getattr(__all__ if '__all__' in locals() else [], []) + [
        'PDFExecutor', 'pdf_batch_extract'
    ]
except ImportError as e:
    PDFExecutor = None
    pdf_batch_extract = None
    _pdf_import_error = str(e)

# Ensure __all__ exists
if '__all__' not in locals():
    __all__ = []

# Add utility functions
__all__ += ['get_available_apis', 'check_api_availability']


def get_available_apis():
    """Get list of available API interfaces."""
    return _available_apis.copy()


def check_api_availability(api_name: str) -> tuple[bool, str]:
    """
    Check if an API interface is available.
    
    Args:
        api_name: Name of the API ('crossref', 'openai', 'pdf')
        
    Returns:
        Tuple of (is_available, error_message)
    """
    if api_name == 'crossref':
        if 'crossref' in _available_apis:
            return True, ""
        return False, globals().get('_crossref_import_error', 'Unknown import error')
    
    elif api_name == 'openai':
        if 'openai' in _available_apis:
            return True, ""
        return False, globals().get('_openai_import_error', 'Unknown import error')
    
    elif api_name == 'pdf':
        if 'pdf' in _available_apis:
            return True, ""
        return False, globals().get('_pdf_import_error', 'Unknown import error')
    
    else:
        return False, f"Unknown API: {api_name}"


# Print availability info when imported
if _available_apis:
    print(f"Multask APIs available: {', '.join(_available_apis)}")
else:
    print("No Multask APIs available - missing dependencies")
