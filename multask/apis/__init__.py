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
_import_errors = {}
__all__ = []

# API interfaces to import
_API_INTERFACES = [
    ('crossref', 'crossref_api', ['CrossRefExecutor', 'crossref_batch_query']),
    ('openai', 'openai_api', ['OpenAIExecutor', 'openai_batch_query', 'openai_price_calculator']),
    ('pdf', 'pdf_api', ['PDFExecutor', 'pdf_batch_extract'])
]

# Import each API interface
for api_name, module_name, exports in _API_INTERFACES:
    try:
        module = __import__(f'.{module_name}', package=__name__, level=1)
        for export in exports:
            globals()[export] = getattr(module, export)
            __all__.append(export)
        _available_apis.append(api_name)
    except ImportError as e:
        _import_errors[api_name] = str(e)
        for export in exports:
            globals()[export] = None

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
    if api_name in _available_apis:
        return True, ""
    elif api_name in _import_errors:
        return False, _import_errors[api_name]
    else:
        return False, f"Unknown API: {api_name}"


# Print availability info when imported
if _available_apis:
    print(f"Multask APIs available: {', '.join(_available_apis)}")
else:
    print("No Multask APIs available - missing dependencies")
