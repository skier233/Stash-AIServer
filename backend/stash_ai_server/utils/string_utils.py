from typing import Any, Mapping, Sequence

def normalize_null_strings(obj: Any) -> Any:
    """Recursively convert string 'null' (case-insensitive) to None inside dict/list structures.
    
    Args:
        obj: The object to process. Can be a string, dictionary, list, or other type.
        
    Returns:
        The processed object with all 'null' strings converted to None.
        Other types are returned as-is.
    """
    if isinstance(obj, str):
        return None if obj.lower() == "null" else obj
    if isinstance(obj, Mapping):
        return {k: normalize_null_strings(v) for k, v in obj.items()}
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes)):
        return [normalize_null_strings(v) for v in obj]
    return obj